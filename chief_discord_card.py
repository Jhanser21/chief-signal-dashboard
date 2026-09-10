import io
import json
import re
import textwrap
from PIL import Image, ImageDraw, ImageFont


def _font(size, bold=False):
    candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf',
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _parse_signal(msg):
    lines = [x.strip() for x in str(msg).splitlines()]
    if not lines or 'CHIEF ' not in lines[0] or (' WATCH ' not in lines[0] and ' CONFIRMED ' not in lines[0]):
        return None

    first = lines[0]
    status = 'CONFIRMED' if ' CONFIRMED ' in first else 'WATCH'
    m = re.search(r'CHIEF\s+(?:WATCH|CONFIRMED)\s+(CALL|PUT)\s*\|\s*([^\s]+)', first)
    if not m:
        return None
    side, ticker = m.group(1), m.group(2)

    fields = {}
    for line in lines[1:]:
        if ':' in line:
            k, v = line.split(':', 1)
            key = k.strip().lower()
            if key in {'score','price','momentum','spread','pattern','trigger','invalidation','confirmation','news','why','status'}:
                fields[key] = v.strip()

    # Preserve the option recommendation block if present.
    option_lines = []
    in_options = False
    for line in lines:
        if 'RECOMMENDED OPTIONS' in line:
            in_options = True
        if in_options:
            if line.lower().startswith('status:'):
                break
            if line:
                option_lines.append(line)

    return {
        'status': status,
        'side': side,
        'ticker': ticker,
        'fields': fields,
        'options': option_lines,
    }


def _fit_text(draw, text, font, max_width, max_lines=2):
    if not text:
        return ['—']
    words = str(text).split()
    out, cur = [], ''
    for word in words:
        test = (cur + ' ' + word).strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            cur = test
        else:
            if cur:
                out.append(cur)
            cur = word
            if len(out) >= max_lines:
                break
    if cur and len(out) < max_lines:
        out.append(cur)
    if len(out) == max_lines and len(' '.join(out)) < len(str(text)):
        last = out[-1]
        while last and draw.textbbox((0, 0), last + '…', font=font)[2] > max_width:
            last = last[:-1]
        out[-1] = last.rstrip() + '…'
    return out or ['—']


def render_signal_card(msg):
    data = _parse_signal(msg)
    if not data:
        return None

    W, H = 1800, 1040
    img = Image.new('RGB', (W, H), (3, 17, 31))
    draw = ImageDraw.Draw(img)

    # Futuristic dark-blue panel and cyan outline.
    draw.rounded_rectangle((14, 14, W - 14, H - 14), radius=28, fill=(4, 23, 42), outline=(42, 220, 255), width=3)
    draw.rounded_rectangle((26, 26, W - 26, H - 26), radius=23, outline=(11, 83, 112), width=1)
    draw.line((45, 145, W - 45, 145), fill=(16, 88, 117), width=2)

    cyan = (83, 226, 255)
    white = (241, 246, 252)
    muted = (145, 180, 212)
    green = (104, 244, 162)
    red = (255, 92, 106)
    amber = (255, 202, 73)

    title_font = _font(47, True)
    ticker_font = _font(45, True)
    label_font = _font(23, True)
    body_font = _font(27, False)
    body_bold = _font(27, True)
    small_font = _font(22, False)

    status = data['status']
    icon = '✓' if status == 'CONFIRMED' else '⌛'
    accent = green if status == 'CONFIRMED' else amber
    side_color = green if data['side'] == 'CALL' else red

    # Header icon block.
    draw.rounded_rectangle((48, 44, 105, 101), radius=10, fill=(20, 167, 79) if status == 'CONFIRMED' else (184, 125, 18))
    draw.text((63, 48), icon, font=_font(38, True), fill=white)
    draw.text((130, 44), 'CHIEF', font=title_font, fill=cyan)
    draw.text((340, 44), f"{status} {data['side']}", font=title_font, fill=white)
    draw.text((865, 44), '|', font=title_font, fill=muted)
    draw.text((905, 44), data['ticker'], font=ticker_font, fill=(171, 211, 248))
    draw.text((1490, 51), 'SIGNAL ALERT', font=_font(23, True), fill=muted)
    draw.text((1425, 90), 'DISCIPLINE  ·  DATA  ·  EXECUTION', font=_font(16, False), fill=(89, 153, 201))

    f = data['fields']
    left_x = 55
    value_x = 280
    y = 175
    row_h = 52

    rows = [
        ('SCORE', f.get('score', '—')),
        ('PRICE', f.get('price', '—')),
        ('MOMENTUM', f.get('momentum', '—')),
        ('SPREAD', f.get('spread', '—')),
        ('PATTERN', f.get('pattern', '—')),
        ('TRIGGER', f.get('trigger', '—')),
        ('INVALIDATION', f.get('invalidation', '—')),
    ]

    for label, value in rows:
        draw.text((left_x, y), label, font=label_font, fill=muted)
        draw.line((235, y - 2, 235, y + 31), fill=(28, 116, 153), width=2)
        fill = white
        if label == 'MOMENTUM':
            low = value.lower()
            if 'bear' in low:
                fill = red
            elif 'bull' in low:
                fill = green
        font = body_bold if label in ('SCORE', 'MOMENTUM') else body_font
        for j, txt in enumerate(_fit_text(draw, value, font, W - value_x - 80, 1)):
            draw.text((value_x, y - 2 + j * 30), txt, font=font, fill=fill)
        y += row_h

    sep_y = y + 4
    draw.line((45, sep_y, W - 45, sep_y), fill=(21, 92, 119), width=2)
    y = sep_y + 30

    detail_rows = [
        ('CONFIRMATION', f.get('confirmation', '—')),
        ('NEWS', f.get('news', '—')),
        ('WHY', f.get('why', '—')),
    ]
    for label, value in detail_rows:
        draw.text((left_x, y), label, font=label_font, fill=muted)
        draw.line((235, y - 2, 235, y + 31), fill=(28, 116, 153), width=2)
        fill = accent if label == 'CONFIRMATION' else white
        max_lines = 2 if label != 'WHY' else 2
        wrapped = _fit_text(draw, value, small_font if label != 'CONFIRMATION' else body_bold, W - value_x - 65, max_lines)
        for j, txt in enumerate(wrapped):
            draw.text((value_x, y - 1 + j * 29), txt, font=small_font if label != 'CONFIRMATION' else body_bold, fill=fill)
        y += 72 if len(wrapped) > 1 else 55

    # Option block, only when Chief actually returned recommendations.
    option_lines = data.get('options') or []
    if option_lines and y < H - 170:
        draw.line((45, y + 4, W - 45, y + 4), fill=(21, 92, 119), width=1)
        y += 22
        draw.text((left_x, y), 'RECOMMENDED OPTIONS', font=label_font, fill=cyan)
        opt_text = '  '.join(x.replace('🎯', '').strip() for x in option_lines[1:4])
        for j, txt in enumerate(_fit_text(draw, opt_text, small_font, W - value_x - 65, 2)):
            draw.text((value_x, y - 1 + j * 28), txt, font=small_font, fill=white)
        y += 62

    # Status footer.
    footer_top = H - 112
    footer_fill = (4, 48, 54) if status == 'CONFIRMED' else (54, 42, 5)
    draw.rounded_rectangle((28, footer_top, W - 28, H - 28), radius=18, fill=footer_fill, outline=accent, width=2)
    draw.text((58, footer_top + 25), 'STATUS', font=label_font, fill=cyan)
    draw.line((185, footer_top + 16, 185, footer_top + 62), fill=(28, 116, 153), width=2)
    footer = '✓  CONFIRMED — PRICE ACTION VALIDATED' if status == 'CONFIRMED' else '⌛  WATCH — WAITING FOR PRICE-ACTION CONFIRMATION'
    draw.text((225, footer_top + 19), footer, font=_font(29, True), fill=accent)

    out = io.BytesIO()
    img.save(out, format='PNG', optimize=True)
    out.seek(0)
    return out


def install_discord_card_hook(requests_module):
    """Upgrade Chief Discord webhook signal text into an image card.

    Chief's existing Telegram delivery remains untouched. Only Discord webhook
    posts containing a WATCH/CONFIRMED Chief signal are converted to PNG.
    """
    if getattr(requests_module, '_chief_card_hook_installed', False):
        return

    original_post = requests_module.post

    def hooked_post(url, *args, **kwargs):
        try:
            payload = kwargs.get('json')
            msg = payload.get('content', '') if isinstance(payload, dict) else ''
            is_discord = 'discord.com/api/webhooks/' in str(url) or 'discordapp.com/api/webhooks/' in str(url)
            if is_discord and msg and 'CHIEF ' in msg and (' WATCH ' in msg or ' CONFIRMED ' in msg):
                card = render_signal_card(msg)
                if card is not None:
                    kwargs.pop('json', None)
                    # Keep a tiny searchable caption; the full signal is on the card.
                    parsed = _parse_signal(msg)
                    caption = f"Chief {parsed['status']} {parsed['side']} | {parsed['ticker']}" if parsed else 'Chief Signal'
                    kwargs['data'] = {'payload_json': json.dumps({'content': caption})}
                    kwargs['files'] = {'files[0]': ('chief-signal.png', card, 'image/png')}
                    return original_post(url, *args, **kwargs)
        except Exception as exc:
            print(f'Discord card render warning: {exc}', flush=True)
        return original_post(url, *args, **kwargs)

    requests_module.post = hooked_post
    requests_module._chief_card_hook_installed = True
