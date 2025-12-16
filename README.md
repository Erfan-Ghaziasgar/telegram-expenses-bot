# Telegram Expenses Bot

Logs simple expenses/debts from chat messages (Persian-friendly) into SQLite and can return weekly/monthly summaries.

## Setup

1. Create a bot with `@BotFather` and copy the token.
2. Install dependencies:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
   - `pip install -r requirements.txt`
3. Create `.env` from the template and set:
   - `cp .env.example .env`
   - `TELEGRAM_BOT_TOKEN="123:abc..."`
   - Optional: `TELEGRAM_ALLOWED_USER_IDS="123456789"` (comma-separated)
   - Optional: `DB_PATH="./data/expenses.db"`
   - Optional: `LOG_LEVEL="INFO"`

Run the bot:
- `python3 run.py`

## Usage

Send a message like:
- `100 تومن پول نون`
- `220 تومن به ممد باید بدم`
- `۱۵۰ تومن ممد باید بهم بده`

Commands:
- `/id` (show your Telegram user id)
- `/week` (weekly summary)
- `/month` (monthly summary)
- `/help`
