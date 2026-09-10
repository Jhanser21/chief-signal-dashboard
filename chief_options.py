import math
import os
from dataclasses import dataclass


@dataclass
class OptionPick:
    code: str
    name: str
    side: str
    strike: float
    dte: int
    expiration: str
    price: float
    delta: float
    theta: float
    iv: float
    volume: int
    open_interest: int
    spread: float
    spread_pct: float
    theta_pct: float
    quality: float


MIN_DTE = int(os.getenv('OPTION_MIN_DTE', '14'))
MAX_DTE = int(os.getenv('OPTION_MAX_DTE', '45'))
MIN_VOLUME = int(os.getenv('OPTION_MIN_VOLUME', '100'))
MIN_OI = int(os.getenv('OPTION_MIN_OI', '500'))
MAX_SPREAD_PCT = float(os.getenv('OPTION_MAX_SPREAD_PCT', '12'))
MIN_PREMIUM = float(os.getenv('OPTION_MIN_PREMIUM', '0.50'))
MAX_PREMIUM = float(os.getenv('OPTION_MAX_PREMIUM', '25'))
TARGET_DELTA = float(os.getenv('OPTION_TARGET_DELTA', '0.60'))
MAX_RESULTS = int(os.getenv('OPTION_MAX_RESULTS', '3'))


def _num(row, *names, default=0.0):
    for name in names:
        if name in row.index:
            try:
                value = float(row[name])
                if math.isfinite(value):
                    return value
            except Exception:
                pass
    return default


def _text(row, *names, default=''):
    for name in names:
        if name in row.index:
            value = row[name]
            if value is not None and str(value).lower() != 'nan':
                return str(value)
    return default


def _request(ctx, underlying, side):
    """Use Moomoo's option screener so Greeks/liquidity come from the broker feed."""
    from moomoo import (
        RET_OK,
        OptionScreenRequest,
        OptMarketCategory,
        OptUnderlyingIndicator,
        OptIndicator,
    )

    req = OptionScreenRequest(market_categories=[OptMarketCategory.US_STOCK])
    req.add_underlying_filter(OptUnderlyingIndicator.STOCK_LIST, values=[underlying])
    req.add_option_filter(OptIndicator.OPTION_TYPE, values=[1 if side == 'CALL' else 2])
    req.add_option_filter(OptIndicator.LEFT_DAY, lower=MIN_DTE, upper=MAX_DTE)
    req.add_option_filter(OptIndicator.VOLUME, lower=MIN_VOLUME)
    req.add_option_filter(OptIndicator.OPEN_INTEREST, lower=MIN_OI)

    # Ask Moomoo to return the fields Chief needs to judge contract quality.
    for indicator in (
        OptIndicator.DELTA,
        OptIndicator.THETA,
        OptIndicator.IV,
        OptIndicator.VOLUME,
        OptIndicator.OPEN_INTEREST,
        OptIndicator.BID_ASK_SPREAD,
        OptIndicator.PRICE,
        OptIndicator.LEFT_DAY,
    ):
        try:
            req.add_option_retrieve(indicator)
        except Exception:
            pass

    # Volume sorting makes the first page favor contracts that can actually be traded.
    try:
        req.add_sort(OptIndicator.VOLUME, desc=True)
    except Exception:
        pass
    req.page_count = 100

    ret, data = ctx.get_option_screen(req)
    if ret != RET_OK:
        raise RuntimeError(str(data))

    # Current SDK returns (last_page, all_count, dataframe).
    if isinstance(data, tuple) and len(data) >= 3:
        return data[2]
    return data


def recommend_options(ctx, ticker, side):
    underlying = ticker if ticker.startswith('US.') else f'US.{ticker}'
    try:
        df = _request(ctx, underlying, side)
    except Exception as exc:
        return {'ok': False, 'text': f'⚠️ Option scan unavailable: {exc}', 'picks': []}

    if df is None or getattr(df, 'empty', True):
        return {'ok': False, 'text': '⚠️ No liquid contracts matched Chief filters.', 'picks': []}

    picks = []
    for _, row in df.iterrows():
        code = _text(row, 'code', 'option', default='')
        name = _text(row, 'option_name', 'name', default=code)
        price = _num(row, 'price', 'option_price', 'last_price')
        delta = _num(row, 'delta')
        theta = _num(row, 'theta')
        iv = _num(row, 'implied_volatility', 'iv')
        volume = int(_num(row, 'volume'))
        oi = int(_num(row, 'open_interest'))
        spread = _num(row, 'bid_ask_spread', 'spread')
        strike = _num(row, 'strike_price', 'strike')
        dte = int(_num(row, 'left_day', 'dte'))
        expiration = _text(row, 'strike_time', 'expiration_date', 'expiry', 'strike_date')

        if price <= 0 or price < MIN_PREMIUM or price > MAX_PREMIUM:
            continue
        spread_pct = (spread / price * 100.0) if spread > 0 else 0.0
        if spread_pct > MAX_SPREAD_PCT:
            continue

        abs_delta = abs(delta)
        # Chief favors ~0.60 delta for directional swings: responsive but not ultra-expensive.
        delta_fit = max(0.0, 1.0 - abs(abs_delta - TARGET_DELTA) / 0.35)

        # Long options have negative theta. Lower decay as % of premium is better.
        theta_pct = (abs(theta) / price * 100.0) if price > 0 else 999.0
        theta_fit = max(0.0, 1.0 - theta_pct / 8.0)

        spread_fit = max(0.0, 1.0 - spread_pct / max(MAX_SPREAD_PCT, 0.01))
        volume_fit = min(1.0, math.log10(max(volume, 1)) / 4.0)
        oi_fit = min(1.0, math.log10(max(oi, 1)) / 4.5)
        dte_fit = 1.0 - min(abs(dte - 30), 30) / 30.0 if dte else 0.5

        quality = (
            delta_fit * 2.8 +
            theta_fit * 2.3 +
            spread_fit * 2.0 +
            volume_fit * 1.2 +
            oi_fit * 1.0 +
            dte_fit * 0.7
        )

        picks.append(OptionPick(
            code=code,
            name=name,
            side=side,
            strike=strike,
            dte=dte,
            expiration=expiration,
            price=price,
            delta=delta,
            theta=theta,
            iv=iv,
            volume=volume,
            open_interest=oi,
            spread=spread,
            spread_pct=spread_pct,
            theta_pct=theta_pct,
            quality=quality,
        ))

    picks.sort(key=lambda x: x.quality, reverse=True)
    picks = picks[:MAX_RESULTS]
    if not picks:
        return {'ok': False, 'text': '⚠️ No contract passed Chief spread/premium/liquidity filters.', 'picks': []}

    lines = ['🎯 RECOMMENDED OPTIONS']
    for idx, p in enumerate(picks, 1):
        strike_text = f'${p.strike:g}' if p.strike else 'strike n/a'
        expiry_text = p.expiration if p.expiration else (f'{p.dte} DTE' if p.dte else 'expiry n/a')
        lines.append(
            f"{idx}. {p.name or p.code} | {strike_text} | {expiry_text}\n"
            f"   Premium ${p.price:.2f} | Δ {p.delta:.2f} | Θ {p.theta:.3f} ({p.theta_pct:.1f}%/day of premium)\n"
            f"   IV {p.iv:.1f}% | Vol {p.volume:,} | OI {p.open_interest:,} | Spread {p.spread_pct:.1f}%"
        )

    lines.append('Chief ranks contracts by delta fit, lower theta decay, tighter spread, volume/OI and DTE. Verify the live quote before entry.')
    return {'ok': True, 'text': '\n'.join(lines), 'picks': picks}
