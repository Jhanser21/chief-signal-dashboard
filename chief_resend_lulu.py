"""One-time resend of the latest LULU swing signal after contract-card update."""
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv('.env')
MARKER = Path(__file__).with_name('.chief_lulu_resend_contract_v1.sent')


def resend_once():
    if MARKER.exists():
        return
    webhook = os.getenv('DISCORD_WEBHOOK_URL', '')
    if not webhook:
        return

    try:
        from moomoo import OpenQuoteContext
        from chief_options import recommend_options

        ctx = OpenQuoteContext(
            host=os.getenv('MOOMOO_HOST', '127.0.0.1'),
            port=int(os.getenv('MOOMOO_PORT', '11111')),
        )
        try:
            options = recommend_options(ctx, 'LULU', 'PUT', trade_type='SWING')
        finally:
            ctx.close()

        # Do not resend a confirmed signal without a real contract.
        if not options.get('ok'):
            print(f"LULU resend waiting for contract: {options.get('text', 'option scan unavailable')}", flush=True)
            return

        msg = (
            "✅ CHIEF CONFIRMED PUT | LULU | SWING\n"
            "Score: 8.0/10\n"
            "Price: 96.59\n"
            "Style: SWING | 1H execution / Daily swing bias / 3m timing context\n"
            "Momentum: BEARISH | RSI 25.1 | 5-bar ROC -2.27% | MACD hist +0.218\n"
            "3m Timing: 3m EMA8/VWAP aligned\n"
            "Spread: 0.02%\n"
            "Pattern: Double Top (1H)\n"
            "Trigger: 96.59\n"
            "Invalidation: 100.90\n"
            "Confirmation: PRICE ACTION CONFIRMED | Score ≥ 8.0, Directional candle, Trigger/BOS, Breakout hold, Rejection\n"
            "News: MIXED/NEUTRAL headline tone | Moomoo News 9/10 | Morgan Stanley Maintains Lululemon Athletica With Sell Rating, Maintains Target Price $83\n"
            "Why: HTF trend aligned, Double Top, 20/50 EMA structure, bearish momentum, 3m EMA8/VWAP aligned, spread 0.02%, Swing trend structure\n\n"
            f"{options['text']}\n\n"
            "Status: ✅ CONFIRMED — PRICE ACTION VALIDATED"
        )
        r = requests.post(webhook, json={'content': msg}, timeout=20)
        r.raise_for_status()
        MARKER.write_text('sent\n', encoding='utf-8')
        print('One-time LULU swing signal resent with recommended contract.', flush=True)
    except Exception as exc:
        print(f'LULU resend warning: {exc}', flush=True)


resend_once()
