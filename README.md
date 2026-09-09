# Chief Signal Dashboard

Cloud-ready Chief trading signal dashboard.

## Current build
- Runs as a Flask/Gunicorn web service
- Automatic 60-second scanner loop
- WATCH and CONFIRMED signal feed
- Telegram and Discord notification hooks
- Render Blueprint configuration included

## Important
The current signal generator is DEMO data. Do not use its generated entries/targets for live trading. Connect a real market-data adapter before live use.

## Render
Deploy using `render.yaml`. Keep Telegram bot tokens and Discord webhooks in Render environment variables; never commit them to GitHub.
