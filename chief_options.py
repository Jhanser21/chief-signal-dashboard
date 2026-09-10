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


MIN_VOLUME = int(os.getenv('OPTION_MIN_VOLUME', '100'))
MIN_OI = int(os.getenv('OPTION_MIN_OI', '500'))
MAX_SPREAD_PCT = float(os.getenv('OPTION_MAX_SPREAD_PCT', '12'))
MIN_PREMIUM = float(os.getenv('OPTION_MIN_PREMIUM', '0.50'))
MAX_PREMIUM = float(os.getenv('OPTION_MAX_PREMIUM', '25'))
MAX_RESULTS = int(os.getenv('OPTION_MAX_RESULTS', '3'))

OPTION_PROFILES = {
    'DAY TRADE': {'min_dte': int(os.getenv('DAY_OPTION_MIN_DTE', '7')), 'max_dte': int(os.getenv('DAY_OPTION_MAX_DTE', '21')), 'target_delta': float(os.getenv('DAY_OPTION_TARGET_DELTA', '0.55')), 'target_dte': int(os.getenv('DAY_OPTION_TARGET_DTE', '14'))},
    'SWING': {'min_dte': int(os.getenv('SWING_OPTION_MIN_DTE', '21')), 'max_dte': int(os.getenv('SWING_OPTION_MAX_DTE', '60')), 'target_delta': float(os.getenv('SWING_OPTION_TARGET_DELTA', '0.60')), 'target_dte': int(os.getenv('SWING_OPTION_TARGET_DTE', '35'))},
}


def _num(row, *names, default=0.0):
    for name in names:
        if name in row.index:
            try:
                value = float(row[name])
                if math.isfinite(value): return value
            except Exception: pass
    return default


def _text(row, *names, default=''):
    for name in names:
        if name in row.index:
            value = row[name]
            if value is not None and str(value).lower() != 'nan': return str(value)
    return default


def _request(ctx, underlying, side, min_dte, max_dte, min_volume=None, min_oi=None):
    from moomoo import RET_OK, OptionScreenRequest, OptMarketCategory, OptUnderlyingIndicator, OptIndicator
    min_volume = MIN_VOLUME if min_volume is None else min_volume; min_oi = MIN_OI if min_oi is None else min_oi
    req = OptionScreenRequest(market_categories=[OptMarketCategory.US_STOCK]); req.add_underlying_filter(OptUnderlyingIndicator.STOCK_LIST, values=[underlying])
    req.add_option_filter(OptIndicator.OPTION_TYPE, values=[1 if side == 'CALL' else 2]); req.add_option_filter(OptIndicator.LEFT_DAY, lower=min_dte, upper=max_dte)
    req.add_option_filter(OptIndicator.VOLUME, lower=min_volume); req.add_option_filter(OptIndicator.OPEN_INTEREST, lower=min_oi)
    for indicator in (OptIndicator.DELTA, OptIndicator.THETA, OptIndicator.IV, OptIndicator.VOLUME, OptIndicator.OPEN_INTEREST, OptIndicator.BID_ASK_SPREAD, OptIndicator.PRICE, OptIndicator.LEFT_DAY):
        try: req.add_option_retrieve(indicator)
        except Exception: pass
    try: req.add_sort(OptIndicator.VOLUME, desc=True)
    except Exception: pass
    req.page_count = 100; ret, data = ctx.get_option_screen(req)
    if ret != RET_OK: raise RuntimeError(str(data))
    if isinstance(data, tuple) and len(data) >= 3: return data[2]
    return data


def _select_from_df(df, side, trade_type, profile, relaxed=False):
    picks=[]
    if df is None or getattr(df,'empty',True): return picks
    max_spread=MAX_SPREAD_PCT*(1.5 if relaxed else 1.0); min_premium=max(0.20,MIN_PREMIUM*(0.6 if relaxed else 1.0)); max_premium=MAX_PREMIUM*(1.5 if relaxed else 1.0)
    for _,row in df.iterrows():
        code=_text(row,'code','option',default=''); name=_text(row,'option_name','name',default=code); price=_num(row,'price','option_price','last_price'); delta=_num(row,'delta'); theta=_num(row,'theta'); iv=_num(row,'implied_volatility','iv'); volume=int(_num(row,'volume')); oi=int(_num(row,'open_interest')); spread=_num(row,'bid_ask_spread','spread'); strike=_num(row,'strike_price','strike'); dte=int(_num(row,'left_day','dte')); expiration=_text(row,'strike_time','expiration_date','expiry','strike_date')
        if price<=0 or price<min_premium or price>max_premium: continue
        spread_pct=(spread/price*100.0) if spread>0 else 0.0
        if spread_pct>max_spread: continue
        abs_delta=abs(delta); delta_fit=max(0.0,1.0-abs(abs_delta-profile['target_delta'])/0.35); theta_pct=(abs(theta)/price*100.0) if price>0 else 999.0; theta_limit=10.0 if trade_type=='DAY TRADE' else 6.0; theta_fit=max(0.0,1.0-theta_pct/theta_limit); spread_fit=max(0.0,1.0-spread_pct/max(max_spread,0.01)); volume_fit=min(1.0,math.log10(max(volume,1))/4.0); oi_fit=min(1.0,math.log10(max(oi,1))/4.5); span=max(profile['max_dte']-profile['min_dte'],1); dte_fit=max(0.0,1.0-abs(dte-profile['target_dte'])/span) if dte else 0.5
        quality=(delta_fit*2.6+theta_fit*1.8+spread_fit*2.4+volume_fit*1.6+oi_fit*1.0+dte_fit*0.6) if trade_type=='DAY TRADE' else (delta_fit*2.8+theta_fit*2.6+spread_fit*1.9+volume_fit*1.0+oi_fit*1.0+dte_fit*0.7)
        picks.append(OptionPick(code,name,side,strike,dte,expiration,price,delta,theta,iv,volume,oi,spread,spread_pct,theta_pct,quality))
    picks.sort(key=lambda x:x.quality,reverse=True); return picks[:MAX_RESULTS]


def swing_flow_snapshot(ctx, ticker, side):
    """Moomoo-based unusual-options/whale-flow proxy. SWING trades only; never used for day-trade scoring."""
    underlying=ticker if ticker.startswith('US.') else f'US.{ticker}'
    try: df=_request(ctx,underlying,side,14,90,min_volume=20,min_oi=50)
    except Exception as exc: return {'available':False,'score':0.0,'label':'UNAVAILABLE','text':f'🐋 Swing Options Flow: unavailable ({exc})'}
    if df is None or getattr(df,'empty',True): return {'available':False,'score':0.0,'label':'NEUTRAL','text':'🐋 Swing Options Flow: no meaningful activity detected'}
    rows=[]
    for _,r in df.iterrows():
        price=_num(r,'price','option_price','last_price'); vol=max(0,int(_num(r,'volume'))); oi=max(0,int(_num(r,'open_interest'))); delta=abs(_num(r,'delta')); iv=_num(r,'implied_volatility','iv'); spread=_num(r,'bid_ask_spread','spread'); premium=price*vol*100.0; voi=vol/max(oi,1); spread_pct=(spread/price*100.0) if price>0 and spread>0 else 0.0
        if price<=0 or vol<=0: continue
        strength=0.0
        if voi>=1.0: strength+=2.0
        elif voi>=0.5: strength+=1.0
        if premium>=1_000_000: strength+=2.0
        elif premium>=250_000: strength+=1.0
        if 0.30<=delta<=0.75: strength+=0.7
        if iv>0: strength+=0.3
        if spread_pct<=12: strength+=0.5
        rows.append((strength,premium,vol,oi,voi,delta,iv))
    if not rows: return {'available':False,'score':0.0,'label':'NEUTRAL','text':'🐋 Swing Options Flow: no meaningful activity detected'}
    rows.sort(reverse=True,key=lambda x:(x[0],x[1])); best=rows[0]; total_premium=sum(x[1] for x in rows[:10]); max_voi=max(x[4] for x in rows[:10]); max_premium=max(x[1] for x in rows[:10]); strong=best[0]>=4.0 or max_premium>=1_000_000 or max_voi>=1.0; moderate=best[0]>=2.5 or max_premium>=250_000 or max_voi>=0.5
    label=f"STRONG {'BULLISH' if side=='CALL' else 'BEARISH'}" if strong else f"{'BULLISH' if side=='CALL' else 'BEARISH'}" if moderate else 'NEUTRAL'; bonus=1.0 if strong else 0.5 if moderate else 0.0
    return {'available':True,'score':bonus,'label':label,'text':f"🐋 Swing Options Flow: {label} | Top premium ${max_premium:,.0f} | Top Vol/OI {max_voi:.2f}x | Basket premium ${total_premium:,.0f}", 'top_premium':max_premium,'vol_oi':max_voi}


def recommend_options(ctx,ticker,side,trade_type='SWING'):
    trade_type=str(trade_type or 'SWING').upper(); profile=OPTION_PROFILES.get(trade_type,OPTION_PROFILES['SWING']); underlying=ticker if ticker.startswith('US.') else f'US.{ticker}'
    attempts=[(profile['min_dte'],profile['max_dte'],MIN_VOLUME,MIN_OI,False)]
    if trade_type=='SWING': attempts += [(14,75,max(50,MIN_VOLUME//2),max(250,MIN_OI//2),False),(7,90,20,100,True)]
    else: attempts += [(3,30,max(50,MIN_VOLUME//2),max(250,MIN_OI//2),True)]
    last_error=None
    for min_dte,max_dte,min_volume,min_oi,relaxed in attempts:
        try: df=_request(ctx,underlying,side,min_dte,max_dte,min_volume=min_volume,min_oi=min_oi)
        except Exception as exc: last_error=exc; continue
        picks=_select_from_df(df,side,trade_type,profile,relaxed=relaxed)
        if picks:
            lines=[f'🎯 RECOMMENDED OPTIONS — {trade_type}']
            for idx,p in enumerate(picks,1):
                strike_text=f'${p.strike:g}' if p.strike else 'strike n/a'; expiry_text=p.expiration if p.expiration else (f'{p.dte} DTE' if p.dte else 'expiry n/a')
                lines.append(f"{idx}. {p.name or p.code} | {strike_text} | {expiry_text}\n   Premium ${p.price:.2f} | Δ {p.delta:.2f} | Θ {p.theta:.3f} ({p.theta_pct:.1f}%/day of premium)\n   IV {p.iv:.1f}% | Vol {p.volume:,} | OI {p.open_interest:,} | Spread {p.spread_pct:.1f}%")
            lines.append('Chief ranks contracts by delta, theta efficiency, spread, liquidity and DTE for this trade horizon. Verify the live quote before entry.')
            return {'ok':True,'text':'\n'.join(lines),'picks':picks}
    if last_error: return {'ok':False,'text':f'⚠️ Option scan unavailable: {last_error}','picks':[]}
    return {'ok':False,'text':f'⚠️ No acceptable {trade_type.lower()} contract found after fallback scan.','picks':[]}
