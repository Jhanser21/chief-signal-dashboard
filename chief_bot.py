import os, time, requests
import pandas as pd
from dotenv import load_dotenv
from chief_patterns import detect_patterns

# Load local/private runtime settings before reading environment variables.
load_dotenv('.env')

WATCHLIST = [x.strip().upper() for x in os.getenv('WATCHLIST','QQQ,SPY,NVDA,TSLA,AMD,AMZN,META,GOOGL,AAPL,MSFT,AVGO,ARM,COIN,HIMS').split(',') if x.strip()]
WATCH_SCORE = float(os.getenv('WATCH_SCORE','7.0'))
CONFIRMED_SCORE = float(os.getenv('CONFIRMED_SCORE','8.5'))
MAX_SPREAD_PCT = float(os.getenv('MAX_SPREAD_PCT','0.40'))
MIN_RVOL = float(os.getenv('MIN_RVOL','1.25'))
SCAN_SECONDS = int(os.getenv('SCAN_SECONDS','60'))


def telegram(msg):
    token=os.getenv('TELEGRAM_BOT_TOKEN',''); chat=os.getenv('TELEGRAM_CHAT_ID','')
    if token and chat:
        r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat,'text':msg},timeout=15); r.raise_for_status()


def discord(msg):
    url=os.getenv('DISCORD_WEBHOOK_URL','')
    if url:
        r=requests.post(url,json={'content':msg},timeout=15); r.raise_for_status()


def notify(msg):
    telegram(msg); discord(msg)


def ema(s,n): return s.ewm(span=n,adjust=False).mean()


def normalize(df):
    ren={c:c.lower() for c in df.columns}; df=df.rename(columns=ren)
    aliases={'open':'open','high':'high','low':'low','close':'close','volume':'volume'}
    for target in aliases:
        if target not in df.columns:
            for c in df.columns:
                if c.lower().endswith(target): df[target]=df[c]; break
    return df[['open','high','low','close','volume']].astype(float).dropna()


def metrics(df):
    d=df.copy(); c=d.close
    e20=ema(c,20); e50=ema(c,50)
    rvol=float(d.volume.iloc[-1] / max(d.volume.tail(21).iloc[:-1].mean(),1))
    bull=e20.iloc[-1]>e50.iloc[-1] and e20.iloc[-1]>e20.iloc[-5] and e50.iloc[-1]>e50.iloc[-5]
    bear=e20.iloc[-1]<e50.iloc[-1] and e20.iloc[-1]<e20.iloc[-5] and e50.iloc[-1]<e50.iloc[-5]
    return {'bull':bull,'bear':bear,'rvol':rvol,'e20':float(e20.iloc[-1]),'e50':float(e50.iloc[-1])}


def score_setup(side, m15, m60, daily, patterns, spread_pct=0.0):
    score=0.0; reasons=[]
    aligned=(side=='CALL' and m60['bull'] and daily['bull']) or (side=='PUT' and m60['bear'] and daily['bear'])
    if aligned: score+=2.5; reasons.append('HTF trend aligned')
    elif (side=='CALL' and m60['bull']) or (side=='PUT' and m60['bear']): score+=1.2; reasons.append('1H trend aligned')
    if m15['rvol']>=MIN_RVOL: score+=1.5; reasons.append(f"RVOL {m15['rvol']:.2f}x")
    matching=[p for p in patterns if p.side==side]
    if matching:
        p=matching[0]; score+=min(2.2,1.0+p.confidence*1.4); reasons.append(p.name)
    close_above=side=='CALL' and m15['e20']>m15['e50']; close_below=side=='PUT' and m15['e20']<m15['e50']
    if close_above or close_below: score+=1.3; reasons.append('20/50 EMA structure')
    if spread_pct<=MAX_SPREAD_PCT: score+=1.0; reasons.append('spread OK')
    # Reserve 1.5 points for live breakout/retest/liquidity confirmation as engine evolves.
    return min(round(score,1),10.0), reasons, (matching[0] if matching else None)


def format_alert(ticker,side,score,status,price,pat,reasons):
    atr_note='Pattern-based invalidation' if pat and pat.invalidation else 'Use confirmed structure invalidation'
    trigger=f"{pat.trigger:.2f}" if pat and pat.trigger else f"{price:.2f} confirmation"
    invalid=f"{pat.invalidation:.2f}" if pat and pat.invalidation else atr_note
    return (f"CHIEF {status} {side} | {ticker}\nScore: {score}/10\n"
            f"Pattern: {pat.name if pat else 'No A+ pattern yet'}\nTrigger: {trigger}\nInvalidation: {invalid}\n"
            f"Why: {', '.join(reasons)}\nStatus: {'BREAK → HOLD → EXPAND' if status=='CONFIRMED' else 'WATCHING FOR CONFIRMATION'}")


def moomoo_context():
    from moomoo import OpenQuoteContext
    return OpenQuoteContext(host=os.getenv('MOOMOO_HOST','127.0.0.1'),port=int(os.getenv('MOOMOO_PORT','11111')))


def get_bars(ctx,ticker,ktype,count=300):
    from moomoo import RET_OK, SubType, KLType
    code=f'US.{ticker}'
    subtype={KLType.K_15M:SubType.K_15M,KLType.K_60M:SubType.K_60M,KLType.K_DAY:SubType.K_DAY}[ktype]
    ret,_=ctx.subscribe([code],[subtype],subscribe_push=False)
    if ret!=RET_OK: raise RuntimeError(f'subscribe failed {ticker} {ktype}')
    ret,data=ctx.get_cur_kline(code,count,ktype)
    if ret!=RET_OK: raise RuntimeError(str(data))
    return normalize(data)


def run():
    from moomoo import KLType
    ctx=moomoo_context(); last={}
    notify('Chief Bot started. Live Moomoo scanner connected.')
    try:
        while True:
            for ticker in WATCHLIST:
                try:
                    d15=get_bars(ctx,ticker,KLType.K_15M); d60=get_bars(ctx,ticker,KLType.K_60M); dd=get_bars(ctx,ticker,KLType.K_DAY)
                    p=detect_patterns(d15); a,b,c=metrics(d15),metrics(d60),metrics(dd); price=float(d15.close.iloc[-1])
                    for side in ('CALL','PUT'):
                        score,reasons,pat=score_setup(side,a,b,c,p)
                        status='CONFIRMED' if score>=CONFIRMED_SCORE else ('WATCH' if score>=WATCH_SCORE else None)
                        key=(ticker,side,status,pat.name if pat else '')
                        if status and time.time()-last.get(key,0)>1800:
                            notify(format_alert(ticker,side,score,status,price,pat,reasons)); last[key]=time.time()
                except Exception as e: print(f'{ticker}: {e}',flush=True)
            time.sleep(SCAN_SECONDS)
    finally: ctx.close()

if __name__=='__main__': run()
