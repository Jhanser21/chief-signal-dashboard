import os
import time
import math
import re
import requests
import pandas as pd
from dotenv import load_dotenv
from chief_patterns import detect_patterns

load_dotenv('.env')

WATCH_SCORE = float(os.getenv('WATCH_SCORE', '7.0'))
CONFIRMED_SCORE = float(os.getenv('CONFIRMED_SCORE', '8.5'))
MAX_SPREAD_PCT = float(os.getenv('MAX_SPREAD_PCT', '0.40'))
MIN_RVOL = float(os.getenv('MIN_RVOL', '1.25'))
SCAN_SECONDS = int(os.getenv('SCAN_SECONDS', '60'))

WHOLE_MARKET = os.getenv('WHOLE_MARKET', 'true').lower() in ('1', 'true', 'yes', 'on')
DEEP_CANDIDATES = int(os.getenv('DEEP_CANDIDATES', '24'))
UNIVERSE_REFRESH_SECONDS = int(os.getenv('UNIVERSE_REFRESH_SECONDS', '900'))
MIN_PRICE = float(os.getenv('MIN_PRICE', '5'))
MAX_PRICE = float(os.getenv('MAX_PRICE', '1000'))
MIN_TURNOVER = float(os.getenv('MIN_TURNOVER', '5000000'))
MIN_VOLUME = float(os.getenv('MIN_VOLUME', '300000'))
SNAPSHOT_BATCH = min(int(os.getenv('SNAPSHOT_BATCH', '400')), 400)

CORE_WATCHLIST = [x.strip().upper() for x in os.getenv(
    'WATCHLIST', 'QQQ,SPY,NVDA,TSLA,AMD,AMZN,META,GOOGL,AAPL,MSFT,AVGO,ARM,COIN,HIMS'
).split(',') if x.strip()]


def telegram(msg):
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat = os.getenv('TELEGRAM_CHAT_ID', '')
    if token and chat:
        r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json={'chat_id': chat, 'text': msg}, timeout=15)
        r.raise_for_status()


def discord(msg):
    url = os.getenv('DISCORD_WEBHOOK_URL', '')
    if url:
        r = requests.post(url, json={'content': msg}, timeout=15)
        r.raise_for_status()


def notify(msg):
    telegram(msg)
    discord(msg)


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def normalize(df):
    df = df.rename(columns={c: c.lower() for c in df.columns})
    for target in ('open', 'high', 'low', 'close', 'volume'):
        if target not in df.columns:
            for c in df.columns:
                if c.lower().endswith(target):
                    df[target] = df[c]
                    break
    return df[['open', 'high', 'low', 'close', 'volume']].astype(float).dropna()


def metrics(df):
    if len(df) < 55:
        raise RuntimeError('not enough candles')
    c = df.close
    e20 = ema(c, 20)
    e50 = ema(c, 50)
    prior_vol = df.volume.tail(21).iloc[:-1].mean()
    rvol = float(df.volume.iloc[-1] / max(prior_vol, 1))
    bull = e20.iloc[-1] > e50.iloc[-1] and e20.iloc[-1] > e20.iloc[-5] and e50.iloc[-1] > e50.iloc[-5]
    bear = e20.iloc[-1] < e50.iloc[-1] and e20.iloc[-1] < e20.iloc[-5] and e50.iloc[-1] < e50.iloc[-5]
    return {'bull': bool(bull), 'bear': bool(bear), 'rvol': rvol, 'e20': float(e20.iloc[-1]), 'e50': float(e50.iloc[-1])}


def score_setup(side, m15, m60, daily, patterns, spread_pct=999.0):
    score = 0.0
    reasons = []
    aligned = ((side == 'CALL' and m60['bull'] and daily['bull']) or (side == 'PUT' and m60['bear'] and daily['bear']))
    if aligned:
        score += 2.5
        reasons.append('HTF trend aligned')
    elif (side == 'CALL' and m60['bull']) or (side == 'PUT' and m60['bear']):
        score += 1.2
        reasons.append('1H trend aligned')
    if m15['rvol'] >= MIN_RVOL:
        score += 1.5
        reasons.append(f"RVOL {m15['rvol']:.2f}x")
    matching = [p for p in patterns if p.side == side]
    if matching:
        p = matching[0]
        score += min(2.2, 1.0 + p.confidence * 1.4)
        reasons.append(p.name)
    ema_ok = ((side == 'CALL' and m15['e20'] > m15['e50']) or (side == 'PUT' and m15['e20'] < m15['e50']))
    if ema_ok:
        score += 1.3
        reasons.append('20/50 EMA structure')
    if spread_pct <= MAX_SPREAD_PCT:
        score += 1.0
        reasons.append(f'spread {spread_pct:.2f}%')
    return min(round(score, 1), 10.0), reasons, (matching[0] if matching else None)


def format_alert(ticker, side, score, status, price, pat, reasons, spread_pct):
    atr_note = 'Pattern-based invalidation' if pat and pat.invalidation else 'Use confirmed structure invalidation'
    trigger = f"{pat.trigger:.2f}" if pat and pat.trigger else f"{price:.2f} confirmation"
    invalid = f"{pat.invalidation:.2f}" if pat and pat.invalidation else atr_note
    if status == 'CONFIRMED':
        icon = '✅'
        status_text = 'CONFIRMED — BREAK → HOLD → EXPAND'
    else:
        icon = '⏳'
        status_text = 'WAITING FOR CONFIRMATION...'
    return (f"{icon} CHIEF {status} {side} | {ticker}\n"
            f"Score: {score}/10\nPrice: {price:.2f}\n"
            f"Spread: {spread_pct:.2f}%\nPattern: {pat.name if pat else 'No A+ pattern yet'}\n"
            f"Trigger: {trigger}\nInvalidation: {invalid}\nWhy: {', '.join(reasons)}\n"
            f"Status: {icon} {status_text}")


def moomoo_context():
    from moomoo import OpenQuoteContext
    return OpenQuoteContext(host=os.getenv('MOOMOO_HOST', '127.0.0.1'), port=int(os.getenv('MOOMOO_PORT', '11111')))


def _truthy_series(series):
    """Normalize Moomoo boolean-like fields; strings such as 'False' must stay false."""
    return series.fillna(False).map(lambda v: str(v).strip().lower() in ('true', '1', 'yes', 'y'))


def get_us_stock_universe(ctx):
    from moomoo import RET_OK, Market, SecurityType
    ret, data = ctx.get_stock_basicinfo(Market.US, SecurityType.STOCK)
    if ret != RET_OK:
        raise RuntimeError(f'get_stock_basicinfo failed: {data}')
    if data is None or data.empty or 'code' not in data.columns:
        raise RuntimeError('Moomoo returned an empty US stock universe')
    raw_count = len(data)
    if 'delisting' in data.columns:
        data = data[~_truthy_series(data['delisting'])].copy()
    if 'suspension' in data.columns:
        data = data[~_truthy_series(data['suspension'])].copy()
    codes = data['code'].dropna().astype(str).drop_duplicates().tolist()
    print(f'CHIEF universe: {len(codes)} active US symbols from {raw_count} Moomoo records', flush=True)
    return codes


def _unsupported_code_from_error(err, batch):
    text = str(err)
    m = re.search(r'not available for\s+([A-Z0-9.\-]+)', text, re.I)
    if not m:
        return None
    raw = m.group(1).rstrip('.,;:').upper()
    candidates = {raw, f'US.{raw}' if not raw.startswith('US.') else raw}
    for code in batch:
        if code.upper() in candidates or code.upper().replace('US.', '') == raw.replace('US.', ''):
            return code
    return None


def _snapshot_resilient(ctx, batch, depth=0):
    from moomoo import RET_OK
    if not batch:
        return [], []
    working = list(batch)
    skipped = []
    for _ in range(min(30, len(working))):
        ret, data = ctx.get_market_snapshot(working)
        if ret == RET_OK and data is not None and not data.empty:
            return [data], skipped
        bad = _unsupported_code_from_error(data, working)
        if bad:
            working.remove(bad)
            skipped.append(bad)
            if not working:
                return [], skipped
            time.sleep(0.08)
            continue
        break
    if len(working) == 1:
        return [], skipped + working
    mid = len(working) // 2
    left_frames, left_skipped = _snapshot_resilient(ctx, working[:mid], depth + 1)
    right_frames, right_skipped = _snapshot_resilient(ctx, working[mid:], depth + 1)
    return left_frames + right_frames, skipped + left_skipped + right_skipped


def get_snapshots(ctx, codes):
    frames = []
    skipped = []
    batches = (len(codes) + SNAPSHOT_BATCH - 1) // SNAPSHOT_BATCH
    for i in range(0, len(codes), SNAPSHOT_BATCH):
        batch_no = i // SNAPSHOT_BATCH + 1
        batch = codes[i:i + SNAPSHOT_BATCH]
        got, bad = _snapshot_resilient(ctx, batch)
        frames.extend(got)
        skipped.extend(bad)
        if batch_no == 1 or batch_no % 5 == 0 or batch_no == batches:
            rows = sum(len(x) for x in frames)
            print(f'CHIEF stage 1 progress: batch {batch_no}/{batches}, {rows} quoted, {len(skipped)} unsupported skipped', flush=True)
        time.sleep(0.35)
    if skipped:
        print(f'CHIEF stage 1: skipped {len(set(skipped))} unsupported/unquotable US symbols', flush=True)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def rank_market_candidates(snapshot):
    if snapshot.empty:
        return []
    d = snapshot.copy()
    numeric = ['last_price', 'prev_close_price', 'volume', 'turnover', 'volume_ratio', 'ask_price', 'bid_price']
    for c in numeric:
        if c not in d.columns:
            d[c] = 0.0
        d[c] = pd.to_numeric(d[c], errors='coerce').fillna(0.0)
    d = d[(d.last_price >= MIN_PRICE) & (d.last_price <= MAX_PRICE) & (d.volume >= MIN_VOLUME) &
          (d.turnover >= MIN_TURNOVER) & (d.prev_close_price > 0)].copy()
    if d.empty:
        return []
    d['move_pct'] = ((d.last_price / d.prev_close_price) - 1.0).abs() * 100.0
    mid = (d.ask_price + d.bid_price) / 2.0
    d['spread_pct'] = ((d.ask_price - d.bid_price) / mid.replace(0, pd.NA) * 100.0).fillna(999.0)
    d['liquidity_score'] = d.turnover.clip(lower=1).map(lambda x: math.log10(x))
    d['volume_ratio_score'] = d.volume_ratio.clip(lower=0, upper=5)
    d['market_rank'] = (d.move_pct.clip(upper=15) * 1.8 + d.volume_ratio_score * 1.6 +
                        d.liquidity_score * 0.9 - d.spread_pct.clip(upper=5) * 2.0)
    d = d.sort_values(['market_rank', 'turnover'], ascending=[False, False])
    return d.head(DEEP_CANDIDATES)['code'].astype(str).tolist()


def build_deep_scan_list(ctx):
    if not WHOLE_MARKET:
        return [f'US.{x}' for x in CORE_WATCHLIST]
    universe = get_us_stock_universe(ctx)
    print(f'CHIEF stage 1: scanning {len(universe)} US symbols...', flush=True)
    snap = get_snapshots(ctx, universe)
    print(f'CHIEF stage 1: received usable snapshots for {len(snap)} symbols', flush=True)
    ranked = rank_market_candidates(snap)
    combined = []
    for code in ranked + [f'US.{x}' for x in CORE_WATCHLIST]:
        if code not in combined:
            combined.append(code)
        if len(combined) >= DEEP_CANDIDATES:
            break
    names = ', '.join(c.replace('US.', '') for c in combined)
    print(f'CHIEF stage 2: {len(combined)} deep candidates -> {names}', flush=True)
    return combined


def snapshot_for_code(ctx, code):
    from moomoo import RET_OK
    ret, data = ctx.get_market_snapshot([code])
    if ret != RET_OK or data is None or data.empty:
        raise RuntimeError(f'snapshot failed: {data}')
    row = data.iloc[0]
    price = float(row.get('last_price', 0) or 0)
    ask = float(row.get('ask_price', 0) or 0)
    bid = float(row.get('bid_price', 0) or 0)
    mid = (ask + bid) / 2.0
    spread_pct = ((ask - bid) / mid * 100.0) if ask > 0 and bid > 0 and mid > 0 else 999.0
    return price, spread_pct


def get_bars(ctx, code, ktype, count=300):
    from moomoo import RET_OK, SubType, KLType
    subtype = {KLType.K_15M: SubType.K_15M, KLType.K_60M: SubType.K_60M, KLType.K_DAY: SubType.K_DAY}[ktype]
    ret, err = ctx.subscribe([code], [subtype], subscribe_push=False)
    if ret != RET_OK:
        raise RuntimeError(f'subscribe failed {code} {ktype}: {err}')
    ret, data = ctx.get_cur_kline(code, count, ktype)
    if ret != RET_OK:
        raise RuntimeError(str(data))
    return normalize(data)


def release_removed_candidates(ctx, removed):
    if not removed:
        return
    from moomoo import RET_OK, SubType
    subtypes = [SubType.K_15M, SubType.K_60M, SubType.K_DAY]
    for code in removed:
        ret, err = ctx.unsubscribe([code], subtypes)
        if ret != RET_OK:
            print(f'unsubscribe warning {code}: {err}', flush=True)


def run():
    from moomoo import KLType
    ctx = moomoo_context()
    last_alert = {}
    candidates = []
    last_universe_refresh = 0.0
    notify('Chief Bot started. Whole-market scanner connected to live Moomoo data.')
    try:
        while True:
            now = time.time()
            if not candidates or now - last_universe_refresh >= UNIVERSE_REFRESH_SECONDS:
                old = set(candidates)
                try:
                    new_candidates = build_deep_scan_list(ctx)
                    if new_candidates:
                        candidates = new_candidates
                        last_universe_refresh = now
                        release_removed_candidates(ctx, old - set(candidates))
                    else:
                        print('Stage-1 returned no candidates; keeping previous list.', flush=True)
                except Exception as e:
                    print(f'whole-market refresh error: {e}', flush=True)
                    if not candidates:
                        candidates = [f'US.{x}' for x in CORE_WATCHLIST[:DEEP_CANDIDATES]]
            for code in candidates:
                ticker = code.replace('US.', '')
                try:
                    d15 = get_bars(ctx, code, KLType.K_15M)
                    d60 = get_bars(ctx, code, KLType.K_60M)
                    dd = get_bars(ctx, code, KLType.K_DAY)
                    price, spread_pct = snapshot_for_code(ctx, code)
                    if spread_pct > MAX_SPREAD_PCT:
                        continue
                    patterns = detect_patterns(d15)
                    m15, m60, daily = metrics(d15), metrics(d60), metrics(dd)
                    for side in ('CALL', 'PUT'):
                        score, reasons, pat = score_setup(side, m15, m60, daily, patterns, spread_pct)
                        status = 'CONFIRMED' if score >= CONFIRMED_SCORE else ('WATCH' if score >= WATCH_SCORE else None)
                        key = (ticker, side, status, pat.name if pat else '')
                        if status and time.time() - last_alert.get(key, 0) > 1800:
                            notify(format_alert(ticker, side, score, status, price, pat, reasons, spread_pct))
                            last_alert[key] = time.time()
                except Exception as e:
                    print(f'{ticker}: {e}', flush=True)
            time.sleep(SCAN_SECONDS)
    finally:
        ctx.close()


if __name__ == '__main__':
    run()
