# Job Posting Watcher

Checks NVIDIA's careers site twice a day (around 8 AM and 8 PM Pacific) with GitHub Actions and sends an alert to Discord or Telegram when new postings appear.

## Setup

1. **Add an alert channel** — Settings → Secrets and variables → Actions → **Secrets**:
   - `DISCORD_WEBHOOK_URL` (Discord: channel settings → Integrations → Webhooks → New Webhook → Copy URL), or
   - `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
2. **Optional filters** — same page, **Variables** tab:
   - `SEARCH_TERMS` — comma-separated keywords, e.g. `CUDA,deep learning,intern`
   - `LOCATION_KEYWORDS` — comma-separated, e.g. `Santa Clara,Remote`
   - `MAX_PAGES` — pages of 20 results scanned per term (default 5)
3. **Run it once** — Actions tab → *NVIDIA job watcher* → **Run workflow**.

The first run records current postings without alerting. After that, only new postings trigger alerts. Seen jobs are stored in `seen_jobs.json`, which the workflow commits back to the repo.

Delete `seen_jobs.json` after changing filters if you want to re-seed quietly.

## Run locally

```bash
pip install -r requirements.txt
SEARCH_TERMS="CUDA" python nvidia_job_watcher.py --once
```

Without `--once`, it loops every `POLL_MINUTES` (default 30).
