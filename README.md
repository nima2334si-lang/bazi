# SECRET SOCIETY Telegram Bot

## Requirements
- Python 3.10+
- Dependencies are listed in `requirements.txt`.

## Environment variables
Set these in your hosting provider's environment settings:
- `BOT_TOKEN`: Telegram bot token
- `ADMIN_ID`: your numeric Telegram user ID

## Start command
`python secret_society_bot.py`

## Storage
On FadeHost, the SQLite database and generated mission images are stored under `/data` so they survive redeploys and restarts. Locally, they are stored beside the script.

Do not commit bot tokens, `.env` files, or database files to GitHub.
