#!/usr/bin/env python3
"""
Trump Social Media Monitor
Fetches latest posts from:
  - Truth Social via trumpstruth.org RSS feed
  - X/Twitter via Nitter RSS
Outputs JSON to ../data/trump_social_feed.json
Sends Telegram alerts for keyword-matched posts
"""

import json
import os
import hashlib
import re
import xml.etree.ElementTree as ET
import html
from datetime import datetime, timezone, timedelta

import requests

HKT = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
FEED_FILE = os.path.join(DATA_DIR, "trump_social_feed.json")
DEDUP_FILE = os.path.join(DATA_DIR, "trump_dedup.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ── Telegram ─────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = None

def load_telegram_creds():
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    # Try env vars first (injected by cron)
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    env_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if env_token and env_chat:
        TELEGRAM_BOT_TOKEN = env_token
        TELEGRAM_CHAT_ID = env_chat
        return
    # Fallback: 1Password CLI with fast timeout
    try:
        import subprocess
        result = subprocess.run(
            ["op", "read", "op://Cadai API Keys/Telegram Bot - CadAI Openclaw/bot_token"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            TELEGRAM_BOT_TOKEN = result.stdout.strip()
        result2 = subprocess.run(
            ["op", "read", "op://Cadai API Keys/Telegram Bot - NLP Sentiment/chat_id"],
            capture_output=True, text=True, timeout=5
        )
        if result2.returncode == 0:
            TELEGRAM_CHAT_ID = result2.stdout.strip()
    except subprocess.TimeoutExpired:
        print("⚠️  1Password op timed out, skipping Telegram creds")
    except Exception as e:
        print(f"⚠️  Telegram creds load: {e}")

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured, skipping alert")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10)
        if not resp.ok:
            print(f"Telegram error {resp.status_code}")
    except Exception as e:
        print(f"Telegram exception: {e}")

# ── Dedup ────────────────────────────────────────────────────────────
def _pid(text: str, source: str, ts: str) -> str:
    raw = f"{source}:{ts}:{(text or '')[:100]}"
    return hashlib.md5(raw.encode()).hexdigest()

def load_dedup() -> set:
    if os.path.exists(DEDUP_FILE):
        try:
            with open(DEDUP_FILE) as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_dedup(ids: set):
    with open(DEDUP_FILE, "w") as f:
        json.dump(list(ids), f)

# ── Keyword alert config ────────────────────────────────────────────
KEYWORDS_ALERT = [
    "bitcoin", "crypto", "tariff", "china", "stock market", "dow",
    "fed", "interest rate", "inflation", "recession",
    "dollar", "oil", "energy", "defense", "iran",
    "nvda", "apple", "google", "meta", "amazon", "microsoft",
    "pltr", "ba", "tesla", "trump media", "tariffs",
    "trade", "nuclear",
]

def check_keywords(text: str) -> list:
    tl = text.lower()
    return [kw for kw in KEYWORDS_ALERT if kw in tl]

# ── Truth Social (via trumpstruth.org RSS) ───────────────────────────
TRUTH_RSS = "https://www.trumpstruth.org/feed"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

def strip_html(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t).strip()

def fetch_truth_social(dedup: set) -> list:
    new_posts = []
    try:
        resp = requests.get(TRUTH_RSS, timeout=20, headers={"User-Agent": USER_AGENT})
        if not resp.ok:
            print(f"Truth RSS: HTTP {resp.status_code}")
            return new_posts
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            desc = item.findtext("description", "")
            link = item.findtext("link", "")
            pub = item.findtext("pubDate", "")
            orig_url_el = item.find("{https://truthsocial.com/ns}originalUrl")
            orig_url = orig_url_el.text if orig_url_el is not None else link

            text = strip_html(desc or title)
            if not text or len(text) < 5:
                continue

            pid = _pid(text, "Truth Social", pub)
            if pid in dedup:
                continue
            dedup.add(pid)

            # Clean title
            clean_title = title.replace("[No Title] - ", "").strip()
            if not clean_title or clean_title.startswith("Post from"):
                clean_title = ""

            new_posts.append({
                "id": pid,
                "source": "Truth Social",
                "title": clean_title,
                "text": text[:1000],
                "url": orig_url,
                "timestamp": pub,
                "detected_at": datetime.now(HKT).isoformat(),
            })
        print(f"  Truth Social: {len(new_posts)} new")
    except Exception as e:
        print(f"Truth Social error: {e}")
    return new_posts

# ── X/Twitter (via Nitter RSS) ──────────────────────────────────────
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.kavin.rocks",
]

def fetch_x_twitter(dedup: set) -> list:
    new_posts = []
    for inst in NITTER_INSTANCES:
        url = f"{inst}/realDonaldTrump/with_replies/rss"
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT})
            if not resp.ok:
                continue
            raw = resp.text
            if not raw or len(raw) < 100:
                continue
            root = ET.fromstring(raw.encode("utf-8"))
            count = 0
            for item in root.findall(".//item"):
                title = item.findtext("title", "")
                desc = item.findtext("description", "")
                link = item.findtext("link", "")
                pub = item.findtext("pubDate", "")

                text = strip_html(desc or title)
                if not text or len(text) < 10:
                    continue
                # Skip nitter nav text
                if text.startswith("@") or "nitter" in text[:20].lower():
                    continue

                pid = _pid(text, "X", pub)
                if pid in dedup:
                    continue
                dedup.add(pid)
                count += 1

                # Extract tweet from title if it has "realDonaldTrump: " prefix
                clean_title = ""
                if "realDonaldTrump:" in title:
                    clean_title = title.split("realDonaldTrump:", 1)[1].strip()
                else:
                    clean_title = title.strip()

                new_posts.append({
                    "id": pid,
                    "source": "X (Twitter)",
                    "title": clean_title,
                    "text": text[:1000],
                    "url": f"https://x.com/realDonaldTrump/status/{hashlib.md5(text.encode()).hexdigest()[:12]}",
                    "timestamp": pub,
                    "detected_at": datetime.now(HKT).isoformat(),
                })
            print(f"  X/Twitter ({inst}): {count} new")
            if new_posts:
                break  # success
        except Exception as e:
            print(f"  X ({inst}) error: {e}")
            continue
    return new_posts

# ── Main ─────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now(HKT).isoformat()}] 🔴 Trump Social Monitor starting...\n")
    load_telegram_creds()
    dedup = load_dedup()

    all_new = []
    all_new.extend(fetch_truth_social(dedup))
    all_new.extend(fetch_x_twitter(dedup))

    save_dedup(dedup)

    # Merge with existing feed
    existing = []
    if os.path.exists(FEED_FILE):
        try:
            with open(FEED_FILE) as f:
                data = json.load(f)
            # Handle both list and dict formats
            if isinstance(data, dict) and 'posts' in data:
                existing = data['posts']
            elif isinstance(data, list):
                existing = data
        except:
            pass

    merged = all_new + existing
    merged = merged[:200]  # cap

    with open(FEED_FILE, "w") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"\nFeed: {len(merged)} posts total ({len(all_new)} new)")

    # Telegram alerts for keyword matches
    for post in all_new:
        kw = check_keywords(post.get("text", "") or post.get("title", ""))
        if kw:
            src = post["source"]
            txt = (post.get("text", "") or post.get("title", "") or "")[:300]
            ts = post["timestamp"][:19] if post["timestamp"] else ""
            msg = (
                f"🔴 <b>Trump Alert</b> | {src}\n"
                f"📌 {', '.join(kw[:5])}\n"
                f"🕐 {ts} HKT\n\n"
                f"{txt}"
            )
            if post.get("url"):
                msg += f"\n\n<a href='{post['url']}'>🔗 View Post</a>"
            send_telegram(msg)
            print(f"  🔔 Telegram alert: {kw}")

    print(f"\n[{datetime.now(HKT).isoformat()}] ✅ Done.")

if __name__ == "__main__":
    main()
