import time

# Swing-only sector/industry rotation confluence. Uses Moomoo owner plates to
# identify the stock's industry and a liquid US sector/industry ETF as a proxy.
# RRG-style states are derived from ETF relative strength and relative momentum
# versus SPY. This is a confluence layer, not a standalone trade trigger.

_CACHE = {}
CACHE_SECONDS = 900

ETF_RULES = [
    (('software','application software','systems software','cloud'), 'IGV', 'Software'),
    (('semiconductor','chip'), 'SOXX', 'Semiconductors'),
    (('biotech','biotechnology'), 'XBI', 'Biotechnology'),
    (('bank','banking'), 'KBE', 'Banks'),
    (('retail','retailer'), 'XRT', 'Retail'),
    (('aerospace','defense'), 'ITA', 'Aerospace & Defense'),
    (('homebuilding','homebuilder'), 'XHB', 'Homebuilders'),
    (('transport','airline','railroad','trucking'), 'IYT', 'Transportation'),
    (('technology','information technology','computer','electronic'), 'XLK', 'Technology'),
    (('financial','insurance','capital markets','broker'), 'XLF', 'Financials'),
    (('energy','oil','gas','petroleum'), 'XLE', 'Energy'),
    (('health','medical','pharma','pharmaceutical'), 'XLV', 'Health Care'),
    (('consumer discretionary','automobile','auto','leisure','restaurant','hotel'), 'XLY', 'Consumer Discretionary'),
    (('consumer staples','food','beverage','household products','tobacco'), 'XLP', 'Consumer Staples'),
    (('industrial','machinery','construction','electrical equipment'), 'XLI', 'Industrials'),
    (('material','chemical','metals','mining','paper'), 'XLB', 'Materials'),
    (('utility','utilities','electric utility','water utility'), 'XLU', 'Utilities'),
    (('real estate','reit'), 'XLRE', 'Real Estate'),
    (('communication','media','entertainment','telecom','interactive media'), 'XLC', 'Communication Services'),
]


def _pick_etf(plate_names):
    text = ' | '.join(str(x).lower() for x in plate_names if x)
    for keywords, etf, sector in ETF_RULES:
        if any(k in text for k in keywords):
            return etf, sector
    return None, None


def _daily_history(ctx, code, count=70):
    from moomoo import RET_OK, KLType, AuType
    ret, data, _ = ctx.request_history_kline(code, ktype=KLType.K_DAY, autype=AuType.QFQ, max_count=count)
    if ret != RET_OK or data is None or getattr(data, 'empty', True):
        raise RuntimeError(str(data))
    closes = data['close'].astype(float).dropna()
    if len(closes) < 30:
        raise RuntimeError('not enough daily bars')
    return closes


def _relative_metrics(sector_close, spy_close):
    n = min(len(sector_close), len(spy_close))
    s = sector_close.iloc[-n:].reset_index(drop=True)
    b = spy_close.iloc[-n:].reset_index(drop=True)
    rs = s / b.replace(0, float('nan'))
    rs20 = (rs.iloc[-1] / rs.iloc[-21] - 1.0) * 100.0
    rs5 = (rs.iloc[-1] / rs.iloc[-6] - 1.0) * 100.0
    prev5 = (rs.iloc[-6] / rs.iloc[-11] - 1.0) * 100.0
    momentum = rs5 - prev5
    return rs20, rs5, momentum


def swing_sector_confluence(ctx, ticker):
    """Return RRG-style sector state for SWING confluence only."""
    code = ticker if str(ticker).startswith('US.') else f'US.{ticker}'
    now = time.time()
    cached = _CACHE.get(code)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    result = {'available': False, 'score': 0.0, 'state': 'UNKNOWN', 'text': '🧭 Sector Rotation: unavailable'}
    try:
        from moomoo import RET_OK
        ret, plates = ctx.get_owner_plate([code])
        if ret != RET_OK or plates is None or getattr(plates, 'empty', True):
            raise RuntimeError(str(plates))
        names = plates.get('plate_name', []).tolist() if 'plate_name' in plates.columns else []
        etf, sector = _pick_etf(names)
        if not etf:
            result = {'available': False, 'score': 0.0, 'state': 'UNKNOWN', 'text': '🧭 Sector Rotation: industry found, ETF mapping unavailable'}
        else:
            etf_close = _daily_history(ctx, f'US.{etf}')
            spy_close = _daily_history(ctx, 'US.SPY')
            rs20, rs5, mom = _relative_metrics(etf_close, spy_close)

            # RRG-style quadrants from relative trend + relative momentum.
            # LEADING: positive medium-term RS and positive short-term momentum.
            # IMPROVING: RS trend not yet strong, but relative momentum is turning up.
            if rs20 > 0.75 and rs5 > 0 and mom >= 0:
                state, bonus = 'LEADING', 1.0
            elif mom > 0.35 and rs5 > 0:
                state, bonus = 'IMPROVING', 0.6
            elif rs20 > 0 and mom < 0:
                state, bonus = 'FADING', 0.0
            else:
                state, bonus = 'LAGGING', 0.0

            result = {
                'available': True,
                'score': bonus,
                'state': state,
                'sector': sector,
                'etf': etf,
                'rs20': rs20,
                'rs5': rs5,
                'momentum': mom,
                'text': f'🧭 Sector Rotation: {sector} ({etf}) — {state} | RS20 {rs20:+.2f}% | RS5 {rs5:+.2f}% | Momentum {mom:+.2f}',
            }
    except Exception as exc:
        result = {'available': False, 'score': 0.0, 'state': 'UNKNOWN', 'text': f'🧭 Sector Rotation: unavailable ({exc})'}

    _CACHE[code] = (now, result)
    return result
