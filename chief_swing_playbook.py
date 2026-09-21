import math
import pandas as pd


def _ema(s,n): return s.ewm(span=n,adjust=False).mean()
def _sma(s,n): return s.rolling(n).mean()
def _std(s,n): return s.rolling(n).std()


def qualify_swing_playbook(dd,d60,side):
    """Extra qualification layer from the user's FiFi TQE swing playbook.
    Adds VCP/tightness, Stage analysis, post-earnings-style gap follow-through,
    30m pivot proxy from 1H data, undercut/reclaim, anticipation, and mean-reversion context.
    It enriches JR Swing PRO; it does not replace its confirmation gate.
    """
    if dd is None or len(dd)<160:
        return {'score_bonus':0,'models':[],'flags':[],'stage':'Unknown','vcp':False,'rubber_band':'Normal'}
    d=dd.copy().reset_index(drop=True)
    c,h,l,o,v=d.close,d.high,d.low,d.open,d.volume
    e8,e21,e50=_ema(c,8),_ema(c,21),_ema(c,50)
    vol20=_sma(v,20)
    price=float(c.iloc[-1]); models=[]; flags=[]; bonus=0

    # VCP / tight-price-action: progressively smaller ranges, higher lows, volume dry-up.
    r5=float(h.tail(5).max()-l.tail(5).min())
    r10=float(h.tail(10).max()-l.tail(10).min())
    r20=float(h.tail(20).max()-l.tail(20).min())
    higher_lows=bool(l.tail(5).min()>l.iloc[-10:-5].min()>l.iloc[-20:-10].min())
    volume_dry=bool(float(v.tail(5).mean()) < float(v.iloc[-20:-5].mean())*.75)
    tightening=bool(r5<r10*.70 and r10<r20*.80)
    holds_ma=bool(price>float(e50.iloc[-1]) and price>float(e21.iloc[-1]))
    vcp=bool(tightening and higher_lows and volume_dry and holds_ma)
    if vcp: bonus+=8; models.append('VCP / Tight Coil')

    # Stage analysis: approximate 30-week MA from daily bars (~150 sessions).
    ma30w=_sma(c,150)
    ma30_now=float(ma30w.iloc[-1]); ma30_prev=float(ma30w.iloc[-11])
    ma30_rising=ma30_now>ma30_prev
    ma30_falling=ma30_now<ma30_prev
    near_ma=abs(price-ma30_now)/max(price,1e-9)<.08
    if price>ma30_now and ma30_rising: stage='Stage 2 Uptrend'; bonus+=5
    elif price<ma30_now and ma30_falling: stage='Stage 4 Downtrend'
    elif near_ma and not ma30_rising and not ma30_falling: stage='Stage 1 Base'
    else: stage='Stage 3 / Transition'
    if side=='CALL' and stage=='Stage 4 Downtrend': bonus-=12; flags.append('Stage 4 long penalty')
    if side=='PUT' and stage=='Stage 2 Uptrend': bonus-=8; flags.append('Stage 2 short penalty')

    # Gap reaction / 3-day rule proxy. We cannot know earnings from OHLC alone, so this
    # identifies qualifying large-volume gaps and waits for follow-through.
    prev_close=c.shift(1)
    gap=(o-prev_close)/prev_close.replace(0,pd.NA)
    gap_vol=v/vol20.replace(0,pd.NA)
    recent_gap_up=[i for i in range(max(1,len(d)-8),len(d)) if pd.notna(gap.iloc[i]) and gap.iloc[i]>=.04 and gap_vol.iloc[i]>=1.5]
    recent_gap_dn=[i for i in range(max(1,len(d)-8),len(d)) if pd.notna(gap.iloc[i]) and gap.iloc[i]<=-.04 and gap_vol.iloc[i]>=1.5]
    gap_state='None'
    if recent_gap_up:
        gi=recent_gap_up[-1]; age=len(d)-1-gi; gap_low=float(l.iloc[gi]); held=bool(l.iloc[gi:].min()>=gap_low*.985)
        if age>=3 and held and price>float(h.iloc[gi]): gap_state='Gap Up + 3-Day Follow-Through'; bonus+=6; models.append('Post-Gap Follow-Through')
        elif age<3: gap_state=f'Gap Up — Day {age+1}/3'; flags.append('3-day gap observation')
    elif recent_gap_dn:
        gi=recent_gap_dn[-1]; age=len(d)-1-gi
        gap_state=f'Gap Down — Day {age+1}' if age<3 else 'Gap Down / Distribution Watch'
        if side=='CALL' and age<3: bonus-=8; flags.append('Avoid fresh gap-down long')

    # Entry models: breakout, undercut & rally, anticipation. 30m pivot is approximated
    # conservatively from the available 1H swing data rather than inventing 30m candles.
    base_high=float(h.iloc[-21:-1].max()); base_low=float(l.iloc[-21:-1].min())
    rvol=float(v.iloc[-1]/max(float(vol20.iloc[-1]),1))
    breakout=bool(price>base_high and rvol>=1.5)
    if breakout: bonus+=6; models.append('Breakout')
    prior_low=float(l.iloc[-11:-1].min())
    undercut=bool(float(l.iloc[-1])<prior_low and price>prior_low)
    if undercut: bonus+=5; models.append('Undercut & Rally')
    base_width=(base_high-base_low)/max(base_low,1e-9)
    anticipation=bool(price<base_high and price>=base_high*.97 and base_width<=.12 and volume_dry and price>float(e21.iloc[-1]))
    if anticipation: bonus+=4; models.append('Anticipation / Tight Right Side')

    pivot_pullback=False
    if d60 is not None and len(d60)>=30:
        x=d60.copy().reset_index(drop=True); xc=x.close; xe20=_ema(xc,20)
        pivot_pullback=bool(float(x.low.iloc[-2])<=float(xe20.iloc[-2])*1.01 and float(xc.iloc[-1])>float(x.high.iloc[-2]) and float(xc.iloc[-1])>float(xe20.iloc[-1]))
        if pivot_pullback: bonus+=4; models.append('Pivot Pullback (1H proxy)')

    # Rubber-band / mean-reversion context using 20-day z-score.
    mean20=_sma(c,20); sd20=_std(c,20)
    z=float((c.iloc[-1]-mean20.iloc[-1])/max(float(sd20.iloc[-1]),1e-9))
    rubber='Normal'
    if z>=2: rubber='Overextended +2SD'; bonus-=5 if side=='CALL' else 2; flags.append('Rubber-band overextension')
    elif z<=-2: rubber='Oversold -2SD'; bonus-=5 if side=='PUT' else 2; flags.append('Rubber-band oversold')

    return {'score_bonus':int(bonus),'models':models,'flags':flags,'stage':stage,'vcp':vcp,'gap_state':gap_state,
            'rubber_band':rubber,'zscore':round(z,2),'tightening':tightening,'higher_lows':higher_lows,
            'volume_dry':volume_dry,'base_high':base_high,'base_low':base_low,'pivot_pullback':pivot_pullback}
