"""One-time deployment smoke test for Chief's Discord SWING picture card.

Python imports sitecustomize automatically at interpreter startup when this repo is on
sys.path. The VPS marker makes the test one-shot so normal bot restarts do not spam Discord.
"""
from pathlib import Path


def _send_once():
    marker = Path('/root/.chief_discord_swing_card_test_v1')
    if marker.exists():
        return
    try:
        import json
        import os
        import requests
        from dotenv import load_dotenv
        from chief_discord_card import render_signal_card

        repo = Path(__file__).resolve().parent
        load_dotenv(repo / '.env')
        webhook = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
        if not webhook:
            return

        msg = (
            '✅ CHIEF CONFIRMED CALL | TEST-SWING | SWING\n'
            'Score: 9.1/10 | JR Score 91/100 A+\n'
            'Price: 100.00\n'
            'Style: SWING | DAILY execution / Weekly + 4H alignment / RS + volume + structure\n'
            'Momentum: TEST — Daily structure engine\n'
            'Spread: 0.10%\n'
            'Pattern: Bull Flag + Daily Breakout (Daily)\n'
            'Trigger: TEST — Daily breakout confirmed with volume\n'
            'Invalidation: 95.00\n'
            'Confirmation: TEST ONLY — JR DAILY SWING ENGINE CARD\n'
            'News: TEST ONLY — no live trade\n'
            'Why: Weekly/Daily/4H aligned, RS leader, RVOL 1.80x, accumulation, Daily breakout\n\n'
            '🎯 RECOMMENDED OPTIONS\n'
            'TEST CONTRACT — 35 DTE | Delta 0.60 | SWING PROFILE\n\n'
            'Status: ✅ TEST ONLY — NOT A LIVE TRADE'
        )
        card = render_signal_card(msg)
        if card is None:
            return
        response = requests.post(
            webhook,
            data={'payload_json': json.dumps({'content': '🧪 CHIEF SWING ENGINE TEST — NOT A LIVE TRADE'})},
            files={'files[0]': ('chief-swing-test.png', card, 'image/png')},
            timeout=15,
        )
        response.raise_for_status()
        marker.write_text('sent\n', encoding='utf-8')
        print('Chief Discord SWING picture-card test sent successfully.', flush=True)
    except Exception as exc:
        print(f'Chief Discord SWING test warning: {exc}', flush=True)


_send_once()
