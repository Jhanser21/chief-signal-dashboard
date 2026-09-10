import time
import pandas as pd

_NEWS_CACHE = {}
_LAST_NEWS_REQUEST = 0.0

POSITIVE_WORDS = {
    'beat', 'beats', 'upgrade', 'upgraded', 'raises', 'raised', 'growth', 'record',
    'approval', 'approved', 'partnership', 'contract', 'wins', 'profit', 'surge',
    'strong', 'outperform', 'buyback', 'dividend', 'guidance raised'
}
NEGATIVE_WORDS = {
    'miss', 'misses', 'downgrade', 'downgraded', 'cuts', 'cut', 'lawsuit', 'probe',
    'investigation', 'recall', 'loss', 'weak', 'offering', 'dilution', 'bankruptcy',
    'warning', 'guidance lowered', 'fraud'
}


def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series, length=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    return (100 - (100 / (1 + rs))).fillna(50.0)


def momentum_snapshot(df):
    """Return observable price momentum from 15-minute candles."""
    if df is None or len(df) < 35:
        return {'bias': 'NEUTRAL', 'strength': 0, 'rsi': 50.0, 'macd_hist': 0.0, 'roc5': 0.0, 'text': 'Neutral / insufficient data'}

    close = df['close'].astype(float)
    rsi = float(_rsi(close, 14).iloc[-1])
    macd = _ema(close, 12) - _ema(close, 26)
    signal = _ema(macd, 9)
    hist = float((macd - signal).iloc[-1])
    roc5 = float((close.iloc[-1] / close.iloc[-6] - 1.0) * 100.0) if close.iloc[-6] else 0.0
    e20 = float(_ema(close, 20).iloc[-1])
    price = float(close.iloc[-1])

    bull_points = 0
    bear_points = 0
    if rsi >= 55:
        bull_points += 1
    elif rsi <= 45:
        bear_points += 1
    if hist > 0:
        bull_points += 1
    elif hist < 0:
        bear_points += 1
    if roc5 > 0.35:
        bull_points += 1
    elif roc5 < -0.35:
        bear_points += 1
    if price > e20:
        bull_points += 1
    elif price < e20:
        bear_points += 1

    if bull_points >= 3 and bull_points > bear_points:
        bias = 'BULLISH'
        strength = bull_points
        icon = '🚀'
    elif bear_points >= 3 and bear_points > bull_points:
        bias = 'BEARISH'
        strength = bear_points
        icon = '🔻'
    else:
        bias = 'NEUTRAL'
        strength = max(bull_points, bear_points)
        icon = '⚖️'

    return {
        'bias': bias,
        'strength': strength,
        'rsi': rsi,
        'macd_hist': hist,
        'roc5': roc5,
        'text': f'{icon} {bias} | RSI {rsi:.1f} | 5-bar ROC {roc5:+.2f}% | MACD hist {hist:+.3f}'
    }


def momentum_score(side, momentum):
    """Small scoring contribution; momentum supports a setup but never confirms it alone."""
    if side == 'CALL' and momentum.get('bias') == 'BULLISH':
        strength = momentum.get('strength', 0)
        return (1.2 if strength >= 4 else 0.8), 'bullish momentum'
    if side == 'PUT' and momentum.get('bias') == 'BEARISH':
        strength = momentum.get('strength', 0)
        return (1.2 if strength >= 4 else 0.8), 'bearish momentum'
    return 0.0, None


def _headline_tone(titles):
    pos = 0
    neg = 0
    for title in titles:
        low = title.lower()
        pos += sum(1 for word in POSITIVE_WORDS if word in low)
        neg += sum(1 for word in NEGATIVE_WORDS if word in low)
    if pos >= neg + 2:
        return 'POSITIVE', '🟢'
    if neg >= pos + 2:
        return 'NEGATIVE', '🔴'
    return 'MIXED/NEUTRAL', '🟡'


def news_context(ctx, ticker, max_count=5, cache_seconds=300):
    """Use Moomoo Search News as context only, not as a causal trading signal."""
    global _LAST_NEWS_REQUEST
    now = time.time()
    cached = _NEWS_CACHE.get(ticker)
    if cached and now - cached['time'] < cache_seconds:
        return cached['value']

    # Moomoo limits Search News requests. Keep a safe gap between calls.
    wait = 3.2 - (now - _LAST_NEWS_REQUEST)
    if wait > 0:
        time.sleep(wait)

    try:
        from moomoo import RET_OK
        ret, data = ctx.get_search_news(ticker, max_count=max_count)
        _LAST_NEWS_REQUEST = time.time()
        if ret != RET_OK or data is None or data.empty:
            value = {'tone': 'NONE', 'text': '📰 No recent Moomoo headline found', 'headlines': []}
        else:
            titles = [str(x).strip() for x in data.get('title', []).tolist() if str(x).strip()][:max_count]
            tone, icon = _headline_tone(titles)
            latest = data.iloc[0]
            source = str(latest.get('source', '') or 'Moomoo')
            when = str(latest.get('publish_time', '') or '')
            headline = str(latest.get('title', '') or '').strip()
            short_headline = headline if len(headline) <= 120 else headline[:117] + '...'
            value = {
                'tone': tone,
                'text': f'📰 {icon} {tone} headline tone | {source} {when} | {short_headline}',
                'headlines': titles,
            }
    except Exception as exc:
        value = {'tone': 'UNAVAILABLE', 'text': f'📰 News unavailable ({type(exc).__name__})', 'headlines': []}

    _NEWS_CACHE[ticker] = {'time': time.time(), 'value': value}
    return value
