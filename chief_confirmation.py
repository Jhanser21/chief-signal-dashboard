import math
import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import requests as _requests
    from chief_discord_card import install_discord_card_hook
    install_discord_card_hook(_requests)
except Exception as _card_hook_error:
    print(f'Discord card hook warning: {_card_hook_error}', flush=True)

try:
    import chief_resend_lulu
except Exception as _lulu_resend_error:
    print(f'LULU resend import warning: {_lulu_resend_error}', flush=True)


def _is_power_hour():
    now = datetime.now(ZoneInfo('America/New_York'))
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (14 * 60 + 55) <= minutes < (16 * 60)


def _power_hour_mover(side, d):
    if not _is_power_hour() or d is None or len(d) < 20:
        return False, ''
    close = d['close'].astype(float); high = d['high'].astype(float); low = d['low'].astype(float); volume = d['volume'].astype(float)
    last = float(close.iloc[-1]); prev5 = float(close.iloc[-6]); move5 = ((last / prev5) - 1.0) * 100.0 if prev5 else 0.0
    avg_vol = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else float(volume.iloc[:-1].mean())
    vol_ratio = float(volume.iloc[-1] / max(avg_vol, 1.0))
    recent_high = float(high.iloc[-11:-1].max()); recent_low = float(low.iloc[-11:-1].min())
    range_now = float(high.iloc[-1] - low.iloc[-1]); avg_range = float((high.iloc[-11:-1] - low.iloc[-11:-1]).mean())
    range_expand = range_now >= max(avg_range * 1.20, 1e-9)
    if side == 'CALL': directional = move5 >= 0.30; structure = last >= recent_high
    else: directional = move5 <= -0.30; structure = last <= recent_low
    mover = bool(directional and vol_ratio >= 1.20 and (structure or range_expand))
    if not mover: return False, ''
    direction = 'UPSIDE' if side == 'CALL' else 'DOWNSIDE'
    return True, f'POWER HOUR {direction} MOVER | 5-bar move {move5:+.2f}% | Volume {vol_ratio:.2f}x'


def _caller_trade_type():
    frame = inspect.currentframe()
    try:
        caller = frame.f_back
        for _ in range(5):
            if not caller:
                break
            trade_type = str(caller.f_locals.get('trade_type', '')).upper()
            if trade_type in ('DAY TRADE', 'SWING'):
                return trade_type
            caller = caller.f_back
        return ''
    finally:
        del frame


def _swing_context_from_caller(side):
    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back else None
        if not caller:
            return None
        loc = caller.f_locals
        if str(loc.get('trade_type', '')).upper() != 'SWING':
            return None
        ctx = loc.get('ctx'); ticker = loc.get('ticker'); reasons = loc.get('reasons')
        if ctx is None or not ticker:
            return None
        from chief_options import swing_flow_snapshot
        from chief_sector import swing_sector_confluence
        flow = swing_flow_snapshot(ctx, ticker, side)
        sector = swing_sector_confluence(ctx, ticker)
        sector_bonus = float(sector.get('score', 0.0)) if side == 'CALL' and sector.get('state') in ('LEADING', 'IMPROVING') else 0.0
        flow_bonus = float(flow.get('score', 0.0))
        if isinstance(reasons, list):
            if flow.get('available'):
                reasons.append(f"Swing options flow {flow.get('label', 'NEUTRAL')}")
            if sector.get('available'):
                state = sector.get('state', 'UNKNOWN'); name = sector.get('sector', 'Sector'); etf = sector.get('etf', '')
                if side == 'CALL' and state in ('LEADING', 'IMPROVING'):
                    reasons.append(f"{name} {state} sector ({etf})")
                elif side == 'PUT' and state in ('LEADING', 'IMPROVING'):
                    reasons.append(f"Caution: {name} sector is {state}")
        return {'flow': flow, 'sector': sector, 'flow_bonus': flow_bonus, 'sector_bonus': sector_bonus, 'bonus': flow_bonus + sector_bonus}
    except Exception as exc:
        print(f'Swing confluence warning: {exc}', flush=True)
        return None
    finally:
        del frame


def evaluate_confirmation(side, df, score, pattern=None, min_score=8.0):
    if df is None or len(df) < 25:
        return {'confirmed': False, 'text': '⏳ Not enough candles for price-action confirmation', 'checks': []}
    d = df.tail(30).copy(); close=d['close'].astype(float); high=d['high'].astype(float); low=d['low'].astype(float); open_=d['open'].astype(float); volume=d['volume'].astype(float)
    last_close=float(close.iloc[-1]); prev_close=float(close.iloc[-2]); last_high=float(high.iloc[-1]); last_low=float(low.iloc[-1]); last_open=float(open_.iloc[-1])
    avg_vol=float(volume.iloc[-21:-1].mean()) if len(volume)>=21 else float(volume.iloc[:-1].mean()); vol_ratio=float(volume.iloc[-1]/max(avg_vol,1.0))
    prior_high=float(high.iloc[-11:-1].max()); prior_low=float(low.iloc[-11:-1].min()); short_high=float(high.iloc[-6:-1].max()); short_low=float(low.iloc[-6:-1].min())
    pattern_trigger=None
    if pattern is not None:
        try:
            if pattern.trigger is not None and math.isfinite(float(pattern.trigger)): pattern_trigger=float(pattern.trigger)
        except Exception: pattern_trigger=None

    trade_type = _caller_trade_type()
    swing_ctx = _swing_context_from_caller(side)
    confluence_bonus = float(swing_ctx.get('bonus', 0.0)) if swing_ctx else 0.0
    effective_score = min(10.0, float(score) + confluence_bonus)

    bullish=side=='CALL'; checks=[]; score_ok=effective_score>=float(min_score); checks.append(('Score ≥ %.1f'%min_score,score_ok))
    directional_candle=(last_close>last_open and last_close>prev_close) if bullish else (last_close<last_open and last_close<prev_close); checks.append(('Directional candle',directional_candle))
    structure_break=last_close>prior_high if bullish else last_close<prior_low; micro_structure_break=last_close>short_high if bullish else last_close<short_low
    pattern_break=False
    if pattern_trigger is not None: pattern_break=last_close>=pattern_trigger if bullish else last_close<=pattern_trigger
    trigger_ok=pattern_break or structure_break or micro_structure_break; checks.append(('Trigger/BOS',trigger_ok))
    if pattern_trigger is not None and pattern_break: hold_ok=last_low>=pattern_trigger*0.998 if bullish else last_high<=pattern_trigger*1.002
    elif structure_break: hold_ok=last_low>=prior_high*0.998 if bullish else last_high<=prior_low*1.002
    elif micro_structure_break: hold_ok=last_close>short_high if bullish else last_close<short_low
    else: hold_ok=False
    checks.append(('Breakout hold',hold_ok))
    candle_range=max(last_high-last_low,1e-9); lower_wick=min(last_open,last_close)-last_low; upper_wick=last_high-max(last_open,last_close)
    rejection=(lower_wick/candle_range>=0.35 and last_close>last_open) if bullish else (upper_wick/candle_range>=0.35 and last_close<last_open); checks.append(('Rejection',rejection))
    volume_ok=vol_ratio>=1.15; checks.append((f'Volume {vol_ratio:.2f}x',volume_ok))
    power_mover,power_text=_power_hour_mover(side,d)
    if _is_power_hour(): checks.append(('Power-hour mover',power_mover))

    # Fast day-trade breakout path: do not wait for the slower 8/10 composite
    # after a pattern/structure has already broken. A fresh directional break
    # with >=1.25x volume and a solid 7+ setup can confirm immediately on the
    # active 1m/5m trigger candle. This is intentionally DAY TRADE only.
    fast_day_breakout = bool(
        trade_type == 'DAY TRADE'
        and effective_score >= 7.0
        and directional_candle
        and vol_ratio >= 1.25
        and trigger_ok
        and (pattern_break or structure_break or micro_structure_break)
    )
    if trade_type == 'DAY TRADE':
        checks.append(('Fast breakout + volume', fast_day_breakout))

    if swing_ctx:
        flow = swing_ctx['flow']; sector = swing_ctx['sector']
        if flow.get('score', 0) > 0: checks.append((f"Options flow {flow.get('label', '')}", True))
        if side == 'CALL' and sector.get('state') in ('LEADING', 'IMPROVING'): checks.append((f"Sector {sector.get('state')}", True))

    supporting=sum(bool(x) for x in (directional_candle,hold_ok,rejection,volume_ok,power_mover))
    normal_confirmed=bool(score_ok and trigger_ok and supporting>=2)
    confirmed=bool(normal_confirmed or fast_day_breakout)
    passed=[name for name,ok in checks if ok]; missing=[name for name,ok in checks if not ok]
    if confirmed:
        if fast_day_breakout and not normal_confirmed:
            text='⚡ FAST BREAKOUT CONFIRMED | Pattern/structure break + directional candle + volume expansion | '+', '.join(passed)
        else:
            text='✅ PRICE ACTION CONFIRMED | '+', '.join(passed)
        if power_mover: text+=' | ⚡ '+power_text
    else:
        next_need=', '.join(missing[:3]) if missing else 'additional confirmation'; text='⏳ WAITING | Need: '+next_need
        if _is_power_hour() and power_mover: text+=' | ⚡ '+power_text

    if swing_ctx:
        text += f" | {swing_ctx['flow'].get('text', '🐋 Swing Options Flow: unavailable')}"
        text += f" | {swing_ctx['sector'].get('text', '🧭 Sector Rotation: unavailable')}"
        if confluence_bonus > 0: text += f" | Swing confluence +{confluence_bonus:.1f} → {effective_score:.1f}/10"

    return {'confirmed':confirmed,'text':text,'checks':checks,'volume_ratio':vol_ratio,'trigger_level':pattern_trigger,'power_hour':_is_power_hour(),'power_hour_mover':power_mover,'power_hour_text':power_text,'effective_score':effective_score,'fast_day_breakout':fast_day_breakout,'swing_confluence':swing_ctx}
