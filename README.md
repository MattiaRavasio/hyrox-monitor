# hyrox-monitor

Tracks ticket availability for HYROX races. Hourly cron via GitHub Actions scrapes
[hyrox.com/find-my-race](https://hyrox.com/find-my-race/) plus each favorite race's
Vivenu shop page and notifies via Telegram on:

- Tickets dropped (race flipped from "Find out more" to "Buy Tickets")
- Vivenu shop opened (`saleStatus = "onSale"`)
- HYROX MEN (Men's Open Singles) became available / unavailable

A static dashboard at `index.html` (served via GitHub Pages) shows favorites with
race dates, countdown, and Men's Open status.

## Files

| Path | Purpose |
|---|---|
| `scrape.py` | Scraper + diff + Telegram notify |
| `config.json` | Favorite races (slug + label). Edit this list to add/remove. |
| `data.json` | Latest scrape output, consumed by `index.html`. |
| `state.json` | Previous run's snapshot, used for change detection. |
| `index.html` | Mobile-friendly dashboard. |
| `.github/workflows/monitor.yml` | Hourly cron. |

## Setup

### 1. Telegram bot

1. Open Telegram, message `@BotFather`, send `/newbot`, follow prompts.
   Save the **bot token** (e.g. `8123456789:AAH...`).
2. Search for your new bot, hit **Start**, send any message ("hi").
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser.
   Find `"chat":{"id": <NUMBER>}`. Save the **chat ID**.

### 2. GitHub Actions secrets

Repo → Settings → Secrets and variables → Actions → **New repository secret**.
Add two:

- `TELEGRAM_BOT_TOKEN` — the bot token from step 1
- `TELEGRAM_CHAT_ID` — the chat ID from step 1

### 3. GitHub Pages

Repo → Settings → Pages → **Source: Deploy from a branch** → Branch: `main` / `(root)` → Save.
The dashboard will be live at `https://mattiaravasio.github.io/hyrox-monitor/`.

### 4. First run

Either wait for the next hour, or trigger manually:

- Repo → Actions → **Hyrox Monitor** → **Run workflow**

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Optional: load secrets from a local .env (gitignored)
export TELEGRAM_BOT_TOKEN=...   # or omit to skip notifications
export TELEGRAM_CHAT_ID=...

python scrape.py
```

Output is written to `data.json` and `state.json`. Open `index.html` in a browser
to view the dashboard locally.

## Editing favorites

Add or remove entries in `config.json`. Each favorite needs:

- `slug` — last segment of the race URL (e.g. `hyrox-paris-s26-27` for `hyrox.com/event/hyrox-paris-s26-27/`)
- `label` — short display name

The script will report `error: "not_found_in_list"` if a slug doesn't match any race.
