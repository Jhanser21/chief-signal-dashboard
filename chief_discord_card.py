import io
import json
import re
from PIL import Image, ImageDraw, ImageFont


def _font(size, bold=False):
    paths=[
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf']
    for p in paths:
        try:return ImageFont.truetype(p,size=size)
        except Exception:pass
    return ImageFont.load_default()


def _parse_signal(msg):
    lines=[x.strip() for x in str(msg).splitlines() if x.strip()]
    if not lines:return None
    first=lines[0]; early='CHIEF EARLY SETUP' in first.upper()
    if early:
        m=re.search(r'CHIEF\s+EARLY\s+SETUP\s*\|\s*([^|\s]+)\s*\|\s*(DAY TRADE)',first,re.I)
        if not m:return None
        status='EARLY SETUP'; ticker=m.group(1).upper(); trade_type='DAY TRADE'; side='WATCH'
    else:
        if 'CHIEF ' not in first or (' WATCH ' not in first and ' CONFIRMED ' not in first):return None
        status='CONFIRMED' if ' CONFIRMED ' in first else 'WATCH'
        m=re.search(r'CHIEF\s+(?:WATCH|CONFIRMED)\s+(CALL|PUT)\s*\|\s*([^|\s]+)(?:\s*\|\s*(DAY TRADE|SWING))?',first,re.I)
        if not m:return None
        side,ticker=m.group(1).upper(),m.group(2).upper(); trade_type=(m.group(3) or '').upper()
    fields={}
    aliases={'observed direction':'side','confirmation still needed':'confirmation','trigger being watched':'trigger','why it is on watch':'why'}
    allowed={'score','price','style','momentum','3m timing','spread','pattern','trigger','invalidation','confirmation','news','why','status','side'}
    for line in lines[1:]:
        if ':' in line:
            k,v=line.split(':',1); key=aliases.get(k.strip().lower(),k.strip().lower())
            if key in allowed:fields[key]=v.strip()
    if early:side=fields.get('side','WATCH').upper()
    if not trade_type:
        style=fields.get('style','').upper(); trade_type='DAY TRADE' if 'DAY TRADE' in style else 'SWING' if 'SWING' in style else 'TRADE'
    options=[]; in_options=False
    if not early:
        for line in lines:
            if 'RECOMMENDED OPTIONS' in line:in_options=True
            if in_options:
                if line.lower().startswith('status:'):break
                options.append(line)
    return {'status':status,'side':side,'ticker':ticker,'trade_type':trade_type,'fields':fields,'options':options,'early':early}


def _fit(draw,text,font,max_width,max_lines=2):
    if not text:return ['—']
    out=[]; cur=''
    for word in str(text).split():
        test=(cur+' '+word).strip()
        if draw.textbbox((0,0),test,font=font)[2]<=max_width:cur=test
        else:
            if cur:out.append(cur)
            cur=word
            if len(out)>=max_lines:break
    if cur and len(out)<max_lines:out.append(cur)
    if len(out)==max_lines and len(' '.join(out))<len(str(text)):
        s=out[-1]
        while s and draw.textbbox((0,0),s+'…',font=font)[2]>max_width:s=s[:-1]
        out[-1]=s.rstrip()+'…'
    return out or ['—']


def render_signal_card(msg):
    data=_parse_signal(msg)
    if not data:return None
    # CHIEF Alert Card V2 — matches the approved compact dashboard layout.
    W,H=1536,961
    img=Image.new('RGB',(W,H),(2,15,28)); d=ImageDraw.Draw(img)
    cyan=(48,220,248); white=(242,246,251); muted=(157,190,219)
    green=(63,240,171); red=(255,91,111); amber=(255,197,70)
    navy=(3,27,47); panel=(4,31,53); line=(11,91,124)
    early=data['early']; status=data['status']; side=data['side']; f=data['fields']
    accent=amber if early or status=='WATCH' else green
    side_color=green if side=='CALL' else red if side=='PUT' else amber

    def box(x1,y1,x2,y2,outline=cyan,fill=panel,r=14,w=2):
        d.rounded_rectangle((x1,y1,x2,y2),radius=r,fill=fill,outline=outline,width=w)
    def txt(x,y,t,size=24,bold=False,color=white):
        d.text((x,y),str(t),font=_font(size,bold),fill=color)
    def one_line(text,font,maxw):
        return _fit(d,text,font,maxw,1)[0]

    # Header
    box(14,8,1522,136,outline=line,fill=navy)
    txt(38,20,'CHIEF',54,True,cyan); txt(40,78,'SIGNAL ALERT',22,True,white); txt(40,108,'DISCIPLINE · DATA · EXECUTION',14,False,muted)
    d.line((344,22,344,119),fill=cyan,width=2)
    txt(382,24,data['ticker'],54,True,white)
    title='EARLY SETUP' if early else f'CONFIRMED {side}' if status=='CONFIRMED' else f'WATCH {side}'
    txt(768,43,title,44,True,side_color if not early else amber)
    badge=data['trade_type']; box(1265,36,1505,103,outline=cyan,fill=(3,45,66)); txt(1292,53,badge,28,True,cyan)

    # Metric boxes
    metrics=[('PRICE',f.get('price','—')),('SETUP SCORE',f.get('score','—')),('TRIGGER',f.get('trigger','—')),('INVALIDATION',f.get('invalidation','—'))]
    xs=[14,398,776,1150]
    for i,(lab,val) in enumerate(metrics):
        box(xs[i],144,xs[i]+370,276,outline=line)
        txt(xs[i]+95,171,lab,20,True,muted)
        txt(xs[i]+95,205,one_line(val,_font(48,True),250),48,True,white)

    # Technical + momentum
    box(14,287,760,552,outline=line); box(775,287,1521,552,outline=line)
    txt(91,304,'TECHNICAL SETUP',29,True,cyan); txt(850,304,'MOMENTUM',29,True,cyan)
    tech=[('PATTERN',f.get('pattern','—')),('3M TIMING',f.get('3m timing','—')),('EMA8 / VWAP',f.get('3m timing','—')),('SPREAD',f.get('spread','—'))]
    mom_text=f.get('momentum','—')
    rsi=re.search(r'RSI\s*([+-]?[\d.]+)',mom_text,re.I); roc=re.search(r'(?:5-bar\s*)?ROC\s*([+-]?[\d.]+%?)',mom_text,re.I); macd=re.search(r'MACD(?:\s*hist)?\s*([+-]?[\d.]+)',mom_text,re.I); rv=re.search(r'RVOL\s*([\d.]+x?)',mom_text,re.I)
    moms=[('RSI',rsi.group(1) if rsi else '—'),('5-BAR ROC',roc.group(1) if roc else '—'),('MACD HIST',macd.group(1) if macd else '—'),('RVOL',rv.group(1) if rv else '—')]
    for j,(lab,val) in enumerate(tech):
        yy=354+j*48; txt(58,yy,lab,19,True,muted); txt(279,yy,one_line(val,_font(22,lab=='3M TIMING'),445),22,lab=='3M TIMING',side_color if lab=='3M TIMING' else white); d.line((36,yy+37,742,yy+37),fill=line,width=1)
    for j,(lab,val) in enumerate(moms):
        yy=354+j*48; txt(819,yy,lab,19,True,muted); col=side_color if lab in ('5-BAR ROC','RVOL') else white; txt(1035,yy,val,22,True if lab=='RVOL' else False,col); d.line((797,yy+37,1501,yy+37),fill=line,width=1)
    bias='BULLISH' if side=='CALL' else 'BEARISH' if side=='PUT' else 'WATCH'; box(1320,300,1501,340,outline=side_color,fill=(25,27,42)); txt(1360,309,bias,20,True,side_color)

    # Confirmation bar
    box(14,565,1521,654,outline=accent,fill=(2,52,48) if status=='CONFIRMED' else (55,42,5))
    headline='PRICE ACTION CONFIRMED' if status=='CONFIRMED' else 'EARLY SETUP — WAITING FOR CONFIRMATION'
    txt(121,585,headline,34,True,accent)
    conf=f.get('confirmation','')
    checks=['Score ≥ 8.0','Directional candle','Trigger / BOS','Breakout hold']
    for i,c in enumerate(checks): txt(620+i*180,598,'✓ '+c,15,True,green if status=='CONFIRMED' else amber)

    # Why
    box(14,664,1521,733,outline=line); txt(95,682,'WHY THIS SETUP',23,True,cyan)
    txt(375,688,one_line(f.get('why','—'),_font(18),1090),18,False,white)

    # News
    box(14,744,1521,843,outline=line); txt(95,767,'NEWS',23,True,cyan)
    news=f.get('news','—'); tone='POSITIVE HEADLINE TONE' if 'positive' in news.lower() else 'NEGATIVE HEADLINE TONE' if 'negative' in news.lower() else 'NEWS CONTEXT'
    box(224,757,468,791,outline=green if 'POSITIVE' in tone else red if 'NEGATIVE' in tone else cyan,fill=(3,49,50)); txt(244,765,tone,15,True,green if 'POSITIVE' in tone else red if 'NEGATIVE' in tone else cyan)
    txt(224,800,one_line(news,_font(19,True),1230),19,True,white)

    # Footer
    box(14,855,1521,936,outline=accent,fill=(2,55,48) if status=='CONFIRMED' else (55,42,5))
    footer='CONFIRMED — PRICE ACTION VALIDATED' if status=='CONFIRMED' else 'EARLY SETUP — NOT A CONFIRMED ENTRY'
    txt(121,878,footer,31,True,accent); txt(1204,887,'CHIEF TRADING INTELLIGENCE',14,False,muted)
    out=io.BytesIO(); img.save(out,format='PNG',optimize=True); out.seek(0); return out


class _SuppressedResponse:
    status_code=204; text=''
    def raise_for_status(self):return None


def install_discord_card_hook(requests_module):
    if getattr(requests_module,'_chief_card_hook_installed',False):return
    original_post=requests_module.post
    def hooked_post(url,*args,**kwargs):
        try:
            payload=kwargs.get('json'); msg=''
            if isinstance(payload,dict):msg=payload.get('content','') or payload.get('text','')
            if msg and 'CHIEF WATCH ' in msg:return _SuppressedResponse()
            is_discord='discord.com/api/webhooks/' in str(url) or 'discordapp.com/api/webhooks/' in str(url)
            card_msg=msg and ('CHIEF CONFIRMED ' in msg or 'CHIEF EARLY SETUP' in msg)
            if is_discord and card_msg:
                card=render_signal_card(msg)
                if card is not None:
                    kwargs.pop('json',None); p=_parse_signal(msg)
                    if p and p['early']:caption=f"Chief DAY TRADE | EARLY SETUP {p['side']} | {p['ticker']}"; fn='chief-early-setup.png'
                    else:caption=f"Chief {p['trade_type']} | CONFIRMED {p['side']} | {p['ticker']}" if p else 'Chief Confirmed Signal'; fn='chief-signal.png'
                    kwargs['data']={'payload_json':json.dumps({'content':caption})}; kwargs['files']={'files[0]':(fn,card,'image/png')}; return original_post(url,*args,**kwargs)
        except Exception as exc:print(f'Discord card render warning: {exc}',flush=True)
        return original_post(url,*args,**kwargs)
    requests_module.post=hooked_post; requests_module._chief_card_hook_installed=True
