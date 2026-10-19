#!/usr/bin/env python3
"""
NVIDIA Job Watcher
------------------
Polls NVIDIA's public Workday careers API (the same JSON endpoint the careers
site calls from the browser) and sends an alert when new postings appear.

No Selenium/Playwright needed: the Workday "cxs" endpoint returns JSON directly.

Setup:
    pip install requests

Configure with environment variables (all optional except at least one alert channel
if you want notifications instead of console output):

    SEARCH_TERMS        Comma-separated keywords, e.g. "CUDA,deep learning,compiler"
                        (empty = watch all postings)
    LOCATION_KEYWORDS   Comma-separated substrings to match in the location,
                        e.g. "Santa Clara,Remote,US, CA" (empty = any location)
    POLL_MINUTES        Minutes between checks in loop mode (default 30)
    MAX_PAGES           Pages of 20 results scanned per search term (default 5)
    STATE_FILE          Where seen job IDs are stored (default seen_jobs.json)
    DISCORD_WEBHOOK_URL Discord channel webhook URL
    TELEGRAM_BOT_TOKEN  Telegram bot token
    TELEGRAM_CHAT_ID    Telegram chat ID to send to

Usage:
    python nvidia_job_watcher.py          # run forever, checking every POLL_MINUTES
    python nvidia_job_watcher.py --once   # single check (good for cron / GitHub Actions)

The first run only records existing postings (no alerts), so you aren't flooded
with hundreds of notifications. Alerts start on the next run.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

API_URL = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
JOB_BASE_URL = "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
PAGE_SIZE = 20  # Workday caps this at 20

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (personal job watcher)",
}


def env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


SEARCH_TERMS = env_list("SEARCH_TERMS") or [""]
LOCATION_KEYWORDS = [k.lower() for k in env_list("LOCATION_KEYWORDS")]
POLL_MINUTES = float(os.getenv("POLL_MINUTES", "30"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "5"))
STATE_FILE = Path(os.getenv("STATE_FILE", "seen_jobs.json"))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("nvidia-watcher")


# ---------------------------------------------------------------- state

def load_seen() -> set[str] | None:
    """Return the set of seen job IDs, or None if this is the first run."""
    if not STATE_FILE.exists():
        return None
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except (json.JSONDecodeError, OSError):
        log.warning("State file unreadable; starting fresh.")
        return None


def save_seen(seen: set[str]) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(seen)))
    tmp.replace(STATE_FILE)


# ---------------------------------------------------------------- fetching

def fetch_jobs(search_text: str) -> list[dict]:
    """Fetch up to MAX_PAGES pages of postings for one search term."""
    jobs: list[dict] = []
    session = requests.Session()
    for page in range(MAX_PAGES):
        payload = {
            "appliedFacets": {},
            "limit": PAGE_SIZE,
            "offset": page * PAGE_SIZE,
            "searchText": search_text,
        }
        for attempt in range(3):
            try:
                resp = session.post(API_URL, json=payload, headers=HEADERS, timeout=20)
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                wait = 5 * (attempt + 1)
                log.warning("Request failed (%s); retrying in %ss", exc, wait)
                time.sleep(wait)
        else:
            log.error("Giving up on '%s' page %d", search_text or "<all>", page)
            break

        data = resp.json()
        postings = data.get("jobPostings", [])
        jobs.extend(postings)
        total = data.get("total", 0)
        if not postings or (page + 1) * PAGE_SIZE >= total:
            break
        time.sleep(1)  # be polite
    return jobs


def job_id(job: dict) -> str:
    # externalPath is unique and stable, e.g. /job/US-CA-Santa-Clara/Senior-Engineer_JR1990000
    return job.get("externalPath") or "|".join(job.get("bulletFields", [])) or job.get("title", "")


def matches_location(job: dict) -> bool:
    if not LOCATION_KEYWORDS:
        return True
    loc = (job.get("locationsText") or "").lower()
    return any(k in loc for k in LOCATION_KEYWORDS)


def format_job(job: dict) -> dict:
    return {
        "title": job.get("title", "Untitled"),
        "location": job.get("locationsText", "Unknown location"),
        "posted": job.get("postedOn", ""),
        "req": (job.get("bulletFields") or [""])[0],
        "url": JOB_BASE_URL + job.get("externalPath", ""),
    }


# ---------------------------------------------------------------- alerts

def send_discord(jobs: list[dict]) -> None:
    # Discord allows up to 10 embeds per message
    for i in range(0, len(jobs), 10):
        embeds = [
            {
                "title": j["title"][:256],
                "url": j["url"],
                "description": f"📍 {j['location']}\n🕒 {j['posted']}\n🆔 {j['req']}",
                "color": 0x76B900,  # green
            }
            for j in jobs[i : i + 10]
        ]
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": f"🟢 {len(jobs)} new NVIDIA posting(s)" if i == 0 else None, "embeds": embeds},
            timeout=15,
        )
        if r.status_code >= 300:
            log.error("Discord error %s: %s", r.status_code, r.text[:200])
        time.sleep(1)


def send_telegram(jobs: list[dict]) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for j in jobs:
        text = f"🟢 <b>{j['title']}</b>\n📍 {j['location']}\n🕒 {j['posted']}\n<a href=\"{j['url']}\">View posting</a>"
        r = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
        if r.status_code >= 300:
            log.error("Telegram error %s: %s", r.status_code, r.text[:200])
        time.sleep(0.5)


def notify(jobs: list[dict]) -> None:
    for j in jobs:
        log.info("NEW: %s | %s | %s", j["title"], j["location"], j["url"])
    if DISCORD_WEBHOOK_URL:
        send_discord(jobs)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        send_telegram(jobs)


# ---------------------------------------------------------------- main loop

def check_once() -> None:
    seen = load_seen()
    first_run = seen is None
    seen = seen or set()

    current: dict[str, dict] = {}
    for term in SEARCH_TERMS:
        for job in fetch_jobs(term):
            if matches_location(job):
                current[job_id(job)] = job
    log.info("Fetched %d matching postings across %d search term(s)", len(current), len(SEARCH_TERMS))

    new_ids = [jid for jid in current if jid not in seen]
    if first_run:
        log.info("First run: recorded %d existing postings, no alerts sent.", len(new_ids))
    elif new_ids:
        notify([format_job(current[jid]) for jid in new_ids])
    else:
        log.info("No new postings.")

    seen.update(new_ids)
    save_seen(seen)


def main() -> None:
    if "--once" in sys.argv:
        check_once()
        return
    log.info("Watching NVIDIA careers every %s min. Terms: %s", POLL_MINUTES, SEARCH_TERMS)
    while True:
        try:
            check_once()
        except Exception:  # keep the watcher alive on unexpected errors
            log.exception("Check failed")
        time.sleep(POLL_MINUTES * 60)


if __name__ == "__main__":
    main()
