import math
from datetime import datetime
from zoneinfo import ZoneInfo

# Chief Discord card hook: chief_bot imports requests before this module, so
# installing the hook here upgrades only Discord signal webhook posts while
# leaving Telegram and all market-data HTTP behavior untouched.
try:
    import requests as _requests
    from chief_discord_card import install_discord_card_hook
    install_discord_card_hook(_requests)
except Exception as _card_hook_error:
    print(f'Discord card hook warning: {_card_hook_error}', flush=True)

# One-time resend requested by the user. The marker is local to the VPS so this
# historical AVGO signal is not re-posted on later service restarts.
def _resend_avgo_once():
    try:
        import os
        import time
        import threading
        from dotenv import load_dotenv

        marker = os.path.join(os.path.dirname(__file__), '.avgo_resend_daytrade_v1.sent')
        if os.path.exists(marker):
            return

        def _send():
            time.sleep(4)
            load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
            url = os.getenv('DISCORD_WEBHOOK_URL', '')
            if not url or os.path.exists(marker):
                return
            msg = (
                '✅ CHIEF CONFIRMED PUT | AVGO | DAY TRADE\n'
                'Score: 9.4/10\n'
                'Price: 362.30\n'
                'Style: DAY TRADE | 1m + 5m patterns / 3m EMA8-VWAP timing / 15m execution / 1H + Daily bias\n'
                'Momentum: BEARISH | RSI 43.6 | 5-bar ROC -0.53% | MACD hist -0.154\n'
                '3m Timing: 3m EMA8/VWAP fresh bearish cross\n'
                'Spread: 0.02%\n'
                'Pattern: Double Top (5m)\n'
                'Trigger: 362.30\n'
                'Invalidation: 364.62\n'
                'Confirmation: ✅ PRICE ACTION CONFIRMED | Score ≥ 8.0, Directional candle, Trigger/BOS, Breakout hold\n'
                'News: POSITIVE headline tone | Moomoo News 9/10 | Piper Sandler Initiates Broadcom(AVGO.US) With Buy Rating, Announces Target Price $460\n'
                'Why: HTF trend aligned, Double Top, 20/50 EMA structure, bearish momentum, 3m EMA8/VWAP fresh cross, spread 0.02%, 5m pattern confirmation\n'
                'Status: ✅ CONFIRMED — PRICE ACTION VALIDATED'
            )
            r = _requests.post(url, json={'content': msg}, timeout=15)
            r.raise_for_status()
            with open(marker, 'w', encoding='utf-8') as fh:
                fh.write('sent\n')
            print('CHIEF: resent AVGO DAY TRADE card once', flush=True)

        threading.Thread(target=_send, daemon=True, name='chief-avgo-resend').start()
    except Exception as exc:
        print(f'AVGO resend warning: {exc}', flush=True)

_resend_avgo_once()


def _is_power_hour():
    """US equity power hour: 3:00 PM through 4:00 PM New York time on weekdays."""
    now = datetime.now(ZoneInfo('America/New_York'))
    return now.weekday() < 5 and now.hour == 15


def _power_hour_mover(side, d):
    """Detect late-day acceleration using price expansion + volume + structure.

    Works best when confirmation receives a 1m or 5m dataframe, which is how
    Chief confirms day-trade patterns. This does not create an unconfirmed alert;
    it is extra evidence toward an already high-quality confirmed setup.
    """
    if not _is_power_hour() or d is None or len(d) < 20:
        return False, ''

    close = d['close'].astype(float)
    high = d['high'].astype(float)
    low = d['low'].astype(float)
    volume = d['volume'].astype(float)

    last = float(close.iloc[-1])
    prev5 = float(close.iloc[-6])
    move5 = ((last / prev5) - 1.0) * 100.0 if prev5 else 0.0

    avg_vol = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else float(volume.iloc[:-1].mean())
    vol_ratio = float(volume.iloc[-1] / max(avg_vol, 1.0))

    recent_high = float(high.iloc[-11:-1].max())
    recent_low = float(low.iloc[-11:-1].min())
    range_now = float(high.iloc[-1] - low.iloc[-1])
    avg_range = float((high.iloc[-11:-1] - low.iloc[-11:-1]).mean())
    range_expand = range_now >= max(avg_range * 1.20, 1e-9)

    if side == 'CALL':
        directional = move5 >= 0.30
        structure = last >= recent_high
    else:
        directional = move5 <= -0.30
        structure = last <= recent_low

    mover = bool(directional and vol_ratio >= 1.20 and (structure or range_expand))
    if not mover:
        return False, ''

    direction = 'UPSIDE' if side == 'CALL' else 'DOWNSIDE'
    text = f'POWER HOUR {direction} MOVER | 5-bar move {move5:+.2f}% | Volume {vol_ratio:.2f}x'
    return True, text


def evaluate_confirmation(side, df, score, pattern=None, min_score=8.0):
    """Confirm a Chief setup with price action, not score alone.

    Confirmation requires score >= min_score plus a real directional trigger.
    During 3:00-4:00 PM ET, Chief also looks for power-hour acceleration:
    strong short-term direction, volume expansion, and a structure/range break.
    """
    if df is None or len(df) < 25:
        return {
            'confirmed': False,
            'text': '⏳ Not enough candles for price-action confirmation',
            'checks': [],
        }

    d = df.tail(30).copy()
    close = d['close'].astype(float)
    high = d['high'].astype(float)
    low = d['low'].astype(float)
    open_ = d['open'].astype(float)
    volume = d['volume'].astype(float)

    last_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    last_high = float(high.iloc[-1])
    last_low = float(low.iloc[-1])
    last_open = float(open_.iloc[-1])

    avg_vol = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else float(volume.iloc[:-1].mean())
    vol_ratio = float(volume.iloc[-1] / max(avg_vol, 1.0))

    prior_high = float(high.iloc[-11:-1].max())
    prior_low = float(low.iloc[-11:-1].min())
    short_high = float(high.iloc[-6:-1].max())
    short_low = float(low.iloc[-6:-1].min())

    pattern_trigger = None
    if pattern is not None:
        try:
            if pattern.trigger is not None and math.isfinite(float(pattern.trigger)):
                pattern_trigger = float(pattern.trigger)
        except Exception:
            pattern_trigger = None

    bullish = side == 'CALL'
    checks = []
    score_ok = float(score) >= float(min_score)
    checks.append(('Score ≥ %.1f' % min_score, score_ok))

    directional_candle = (
        last_close > last_open and last_close > prev_close
        if bullish else
        last_close < last_open and last_close < prev_close
    )
    checks.append(('Directional candle', directional_candle))

    structure_break = last_close > prior_high if bullish else last_close < prior_low
    micro_structure_break = last_close > short_high if bullish else last_close < short_low

    pattern_break = False
    if pattern_trigger is not None:
        pattern_break = last_close >= pattern_trigger if bullish else last_close <= pattern_trigger

    trigger_ok = pattern_break or structure_break or micro_structure_break
    checks.append(('Trigger/BOS', trigger_ok))

    if pattern_trigger is not None and pattern_break:
        hold_ok = last_low >= pattern_trigger * 0.998 if bullish else last_high <= pattern_trigger * 1.002
    elif structure_break:
        hold_ok = last_low >= prior_high * 0.998 if bullish else last_high <= prior_low * 1.002
    elif micro_structure_break:
        hold_ok = last_close > short_high if bullish else last_close < short_low
    else:
        hold_ok = False
    checks.append(('Breakout hold', hold_ok))

    candle_range = max(last_high - last_low, 1e-9)
    lower_wick = min(last_open, last_close) - last_low
    upper_wick = last_high - max(last_open, last_close)
    rejection = (
        lower_wick / candle_range >= 0.35 and last_close > last_open
        if bullish else
        upper_wick / candle_range >= 0.35 and last_close < last_open
    )
    checks.append(('Rejection', rejection))

    volume_ok = vol_ratio >= 1.15
    checks.append((f'Volume {vol_ratio:.2f}x', volume_ok))

    power_mover, power_text = _power_hour_mover(side, d)
    if _is_power_hour():
        checks.append(('Power-hour mover', power_mover))

    # Require score + trigger, then at least two supporting confirmations.
    # A genuine power-hour acceleration counts as an additional supporting check.
    supporting = sum(bool(x) for x in (directional_candle, hold_ok, rejection, volume_ok, power_mover))
    confirmed = bool(score_ok and trigger_ok and supporting >= 2)

    passed = [name for name, ok in checks if ok]
    missing = [name for name, ok in checks if not ok]

    if confirmed:
        text = '✅ PRICE ACTION CONFIRMED | ' + ', '.join(passed)
        if power_mover:
            text += ' | ⚡ ' + power_text
    else:
        next_need = ', '.join(missing[:3]) if missing else 'additional confirmation'
        text = '⏳ WAITING | Need: ' + next_need
        if _is_power_hour() and power_mover:
            text += ' | ⚡ ' + power_text

    return {
        'confirmed': confirmed,
        'text': text,
        'checks': checks,
        'volume_ratio': vol_ratio,
        'trigger_level': pattern_trigger,
        'power_hour': _is_power_hour(),
        'power_hour_mover': power_mover,
        'power_hour_text': power_text,
    }
