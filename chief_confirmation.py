import math

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
            time.sleep(4)  # allow chief_bot to load .env after imports finish
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


def _avg(series):
    vals = [float(x) for x in series if x is not None]
    return sum(vals) / len(vals) if vals else 0.0


def evaluate_confirmation(side, df, score, pattern=None, min_score=8.0):
    """Confirm a Chief setup with price action, not score alone.

    Confirmation requires score >= min_score plus directional momentum and a
    real price-action trigger. It recognizes pattern-trigger breaks, recent
    structure breaks, breakout holds/retests, rejection candles, and volume
    expansion. Returns a dict suitable for Telegram display.
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

    directional_candle = (last_close > last_open and last_close > prev_close if bullish else last_close < last_open and last_close < prev_close)
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
    rejection = (lower_wick / candle_range >= 0.35 and last_close > last_open if bullish else upper_wick / candle_range >= 0.35 and last_close < last_open)
    checks.append(('Rejection', rejection))

    volume_ok = vol_ratio >= 1.15
    checks.append((f'Volume {vol_ratio:.2f}x', volume_ok))
    supporting = sum(bool(x) for x in (directional_candle, hold_ok, rejection, volume_ok))
    confirmed = bool(score_ok and trigger_ok and supporting >= 2)

    passed = [name for name, ok in checks if ok]
    missing = [name for name, ok in checks if not ok]
    if confirmed:
        text = '✅ PRICE ACTION CONFIRMED | ' + ', '.join(passed)
    else:
        next_need = ', '.join(missing[:3]) if missing else 'additional confirmation'
        text = '⏳ WAITING | Need: ' + next_need

    return {'confirmed': confirmed, 'text': text, 'checks': checks, 'volume_ratio': vol_ratio, 'trigger_level': pattern_trigger}
