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
    W,H=1800,1040; img=Image.new('RGB',(W,H),(3,17,31)); d=ImageDraw.Draw(img)
    cyan=(83,226,255); white=(241,246,252); muted=(145,180,212); green=(104,244,162); red=(255,92,106); amber=(255,202,73)
    early=data['early']; status=data['status']; accent=amber if early or status=='WATCH' else green
    d.rounded_rectangle((14,14,W-14,H-14),radius=28,fill=(4,23,42),outline=accent if early else cyan,width=3)
    d.rounded_rectangle((26,26,W-26,H-26),radius=23,outline=(11,83,112),width=1); d.line((45,145,W-45,145),fill=(16,88,117),width=2)
    tf=_font(47,True); lf=_font(23,True); bf=_font(27); bb=_font(27,True); sf=_font(22)
    if early:icon='!'; title='EARLY SETUP'; fill=(184,125,18)
    elif status=='CONFIRMED':icon='✓'; title=f"CONFIRMED {data['side']}"; fill=(20,167,79)
    else:icon='⌛'; title=f"WATCH {data['side']}"; fill=(184,125,18)
    d.rounded_rectangle((48,44,105,101),radius=10,fill=fill); d.text((65,49),icon,font=_font(34,True),fill=white)
    d.text((130,44),'CHIEF',font=tf,fill=cyan); d.text((340,44),title,font=tf,fill=amber if early else white); d.text((865,44),'|',font=tf,fill=muted); d.text((905,44),data['ticker'],font=_font(45,True),fill=(171,211,248))
    badge=data['trade_type']; bw=250 if badge=='DAY TRADE' else 180; d.rounded_rectangle((1180,48,1180+bw,98),radius=14,fill=(9,60,83),outline=accent if early else cyan,width=2); d.text((1202,58),badge,font=_font(24,True),fill=accent if early else cyan)
    d.text((1490,51),'SETUP ALERT' if early else 'SIGNAL ALERT',font=_font(23,True),fill=muted); d.text((1425,90),'DISCIPLINE  ·  DATA  ·  EXECUTION',font=_font(16),fill=(89,153,201))
    f=data['fields']; y=175; lx=55; vx=280; rh=52
    rows=[('STYLE',badge)]
    if early:rows += [('DIRECTION',data['side']),('SCORE',f.get('score','—')),('PRICE',f.get('price','—')),('3M TIMING',f.get('3m timing','—')),('MOMENTUM',f.get('momentum','—')),('SPREAD',f.get('spread','—')),('PATTERN',f.get('pattern','—')),('TRIGGER',f.get('trigger','—'))]
    else:rows += [('SCORE',f.get('score','—')),('PRICE',f.get('price','—')),('3M TIMING',f.get('3m timing','—')),('MOMENTUM',f.get('momentum','—')),('SPREAD',f.get('spread','—')),('PATTERN',f.get('pattern','—')),('TRIGGER',f.get('trigger','—')),('INVALIDATION',f.get('invalidation','—'))]
    for label,value in rows:
        d.text((lx,y),label,font=lf,fill=muted); d.line((235,y-2,235,y+31),fill=(28,116,153),width=2); low=str(value).lower(); color=cyan if label=='STYLE' else white
        if label in ('DIRECTION','MOMENTUM','3M TIMING'):color=green if ('call' in low or 'bull' in low) else red if ('put' in low or 'bear' in low) else white
        if early and label=='SCORE':color=green
        if early and label=='TRIGGER':color=amber
        font=bb if label in ('STYLE','DIRECTION','SCORE','MOMENTUM') else bf
        for j,t in enumerate(_fit(d,value,font,W-vx-80,1)):d.text((vx,y-2+j*30),t,font=font,fill=color)
        y+=rh
    sy=y+4; d.line((45,sy,W-45,sy),fill=(21,92,119),width=2); y=sy+30
    details=[('CONFIRMATION',f.get('confirmation','—')),('WHY ON WATCH',f.get('why','—'))] if early else [('CONFIRMATION',f.get('confirmation','—')),('NEWS',f.get('news','—')),('WHY',f.get('why','—'))]
    for label,value in details:
        d.text((lx,y),label,font=lf,fill=muted); d.line((235,y-2,235,y+31),fill=(28,116,153),width=2); font=bb if label=='CONFIRMATION' else sf; color=accent if label=='CONFIRMATION' else white; wrapped=_fit(d,value,font,W-vx-65,2)
        for j,t in enumerate(wrapped):d.text((vx,y-1+j*29),t,font=font,fill=color)
        y+=72 if len(wrapped)>1 else 55
    opts=data.get('options') or []
    if opts and y<H-170:
        d.line((45,y+4,W-45,y+4),fill=(21,92,119),width=1); y+=22; d.text((lx,y),'RECOMMENDED OPTIONS',font=lf,fill=cyan); ot='  '.join(x.replace('🎯','').strip() for x in opts[1:4])
        for j,t in enumerate(_fit(d,ot,sf,W-vx-65,2)):d.text((vx,y-1+j*28),t,font=sf,fill=white)
    ft=H-112; ff=(54,42,5) if early or status=='WATCH' else (4,48,54); d.rounded_rectangle((28,ft,W-28,H-28),radius=18,fill=ff,outline=accent,width=2); d.text((58,ft+25),'STATUS',font=lf,fill=cyan); d.line((185,ft+16,185,ft+62),fill=(28,116,153),width=2)
    footer='⚠  EARLY SETUP — NOT A CONFIRMED ENTRY' if early else '✓  CONFIRMED — PRICE ACTION VALIDATED' if status=='CONFIRMED' else '⌛  WATCH — WAITING FOR PRICE-ACTION CONFIRMATION'; d.text((225,ft+19),footer,font=_font(29,True),fill=accent)
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
