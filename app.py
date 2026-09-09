from flask import Flask, render_template, jsonify, request
import os, time, random, threading, requests

app = Flask(__name__)
lock = threading.Lock()
WATCHLIST=['QQQ','SPY','NVDA','TSLA','AMD','AMZN','META','GOOGL','AAPL','MSFT','AVGO','ARM','COIN','HIMS']
state={'running':True,'last_scan':None,'signals':[]}

def notify(message):
    if os.getenv('SEND_TELEGRAM','false').lower()=='true':
        token=os.getenv('TELEGRAM_BOT_TOKEN',''); chat=os.getenv('TELEGRAM_CHAT_ID','')
        if token and chat:
            requests.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat,'text':message},timeout=10).raise_for_status()
    if os.getenv('SEND_DISCORD','false').lower()=='true':
        url=os.getenv('DISCORD_WEBHOOK_URL','')
        if url: requests.post(url,json={'content':message},timeout=10).raise_for_status()

def scan():
    ticker=random.choice(WATCHLIST); side=random.choice(['CALL','PUT']); score=round(random.uniform(7,9.8),1); p=round(random.uniform(50,700),2)
    s={'ticker':ticker,'side':side,'score':score,'status':'CONFIRMED' if score>=8.5 else 'WATCH','entry':f'{p-.35:.2f} - {p+.35:.2f}','stop':round(p-1.75 if side=='CALL' else p+1.75,2),'tp1':round(p+2 if side=='CALL' else p-2,2),'tp2':round(p+4 if side=='CALL' else p-4,2),'tp3':round(p+6 if side=='CALL' else p-6,2),'pattern':random.choice(['Breakout + Retest','Liquidity Sweep','Double Bottom','Double Top','EMA Retest']),'trend':'Bullish' if side=='CALL' else 'Bearish','rvol':round(random.uniform(1.1,3.2),2),'spread':round(random.uniform(.03,.35),2),'news_risk':random.choice(['Low','Low','Moderate'])}
    with lock:
        state['last_scan']=time.strftime('%H:%M:%S UTC',time.gmtime()); state['signals'].insert(0,s); state['signals']=state['signals'][:50]
    if s['status']=='CONFIRMED':
        try: notify(f"CHIEF CONFIRMED {side} | {ticker}\nScore {score}/10\n{s['pattern']}\nEntry {s['entry']} | Stop {s['stop']}\nTP1 {s['tp1']} | TP2 {s['tp2']} | TP3 {s['tp3']}")
        except Exception as e: print(e,flush=True)
    return s

def loop():
    while True:
        if state['running']: scan()
        time.sleep(60)
threading.Thread(target=loop,daemon=True).start()

@app.get('/')
def home(): return render_template('index.html')
@app.get('/health')
def health(): return {'ok':True}
@app.get('/api/status')
def status():
    with lock: return jsonify({'running':state['running'],'last_scan':state['last_scan'],'signals':state['signals'],'watchlist':WATCHLIST})
@app.post('/api/toggle')
def toggle():
    state['running']=not state['running']; return {'ok':True,'running':state['running']}
@app.post('/api/scan')
def scan_now(): return {'ok':True,'signal':scan()}
@app.post('/api/test')
def test():
    notify('Chief Signal Dashboard cloud test OK'); return {'ok':True}
