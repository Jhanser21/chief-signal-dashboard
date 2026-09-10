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

    # Recent structure excludes the current candle so a fresh break can be seen.
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

    # Directional impulse: candle closes in direction and above/below prior close.
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

    # Hold means price did not immediately lose the breakout area.
    if pattern_trigger is not None and pattern_break:
        hold_ok = last_low >= pattern_trigger * 0.998 if bullish else last_high <= pattern_trigger * 1.002
    elif structure_break:
        hold_ok = last_low >= prior_high * 0.998 if bullish else last_high <= prior_low * 1.002
    elif micro_structure_break:
        hold_ok = last_close > short_high if bullish else last_close < short_low
    else:
        hold_ok = False
    checks.append(('Breakout hold', hold_ok))

    # Rejection gives an alternate confirmation path after a sweep/retest.
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

    # Require score + trigger, then at least two supporting confirmations.
    supporting = sum(bool(x) for x in (directional_candle, hold_ok, rejection, volume_ok))
    confirmed = bool(score_ok and trigger_ok and supporting >= 2)

    passed = [name for name, ok in checks if ok]
    missing = [name for name, ok in checks if not ok]

    if confirmed:
        text = '✅ PRICE ACTION CONFIRMED | ' + ', '.join(passed)
    else:
        next_need = ', '.join(missing[:3]) if missing else 'additional confirmation'
        text = '⏳ WAITING | Need: ' + next_need

    return {
        'confirmed': confirmed,
        'text': text,
        'checks': checks,
        'volume_ratio': vol_ratio,
        'trigger_level': pattern_trigger,
    }
