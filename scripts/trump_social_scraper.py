#!/usr/bin/env python3
"""
Trump Social Media Monitor
Fetches latest posts from Trump's Truth Social and X/Twitter accounts.
Outputs JSON to ../data/trump_social_feed.json
"""

import json
import os
import time
import hashlib
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

HKT = timezone(timedelta(hours=8))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
FEED_FILE = os.path.join(DATA_DIR, "trump_social_feed.json")
DEDUP_FILE = os.path.join(DATA_DIR, "trump_dedup.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ── Telegram config ──────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = None  # loaded from 1password
TELEGRAM_CHAT_ID = None

def load_telegram_creds():
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    try:
        import subprocess
        result = subprocess.run(
            ["op", "read", "op://Cadai API Keys/Telegram Bot - CadAI Openclaw/bot_token"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            TELEGRAM_BOT_TOKEN = result.stdout.strip()
        result2 = subprocess.run(
            ["op", "read", "op://Cadai API Keys/Telegram Bot - NLP Sentiment/chat_id"],
            capture_output=True, text=True, timeout=10
        )
        if result2.returncode == 0:
            TELEGRAM_CHAT_ID = result2.stdout.strip()
    except Exception as e:
        print(f"Telegram creds load failed: {e}")

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured, skipping notify")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            print(f"Telegram send error: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Telegram send exception: {e}")

# ── Dedup helpers ────────────────────────────────────────────────────
def load_dedup() -> set:
    if os.path.exists(DEDUP_FILE):
        try:
            with open(DEDUP_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_dedup(ids: set):
    with open(DEDUP_FILE, "w") as f:
        json.dump(list(ids), f)

def post_id(text: str, source: str, ts: str) -> str:
    raw = f"{source}:{ts}:{text.strip()[:100]}"
    return hashlib.md5(raw.encode()).hexdigest()

# ── Feeds ────────────────────────────────────────────────────────────

def fetch_rss_feed(url: str, source_name: str, dedup: set) -> list:
    """Generic RSS/Atom feed parser."""
    import xml.etree.ElementTree as ET
    new_posts = []
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        })
        if not resp.ok:
            print(f"{source_name} RSS fetch failed: {resp.status_code}")
            return new_posts
        root = ET.fromstring(resp.content)
        # Handle both RSS and Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = []
        # RSS
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            desc = item.findtext("description", "")
            pubdate = item.findtext("pubDate", "")
            entries.append({"title": title, "link": link, "text": desc, "ts": pubdate, "source": source_name})
        # Atom
        for entry in root.findall(".//atom:entry", ns):
            title = entry.findtext("atom:title", "", ns)
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
            content = entry.findtext("atom:content", "", ns)
            published = entry.findtext("atom:published", "", ns)
            entries.append({"title": title, "link": link, "text": content, "ts": published, "source": source_name})
        for e in entries:
            pid = post_id(e["text"] or e["title"], e["source"], e["ts"])
            if pid not in dedup:
                dedup.add(pid)
                news_post = {
                    "id": pid,
                    "source": e["source"],
                    "title": e["title"],
                    "text": (e["text"] or "")[:500],
                    "url": e["link"],
                    "timestamp": e["ts"],
                    "detected_at": datetime.now(HKT).isoformat(),
                }
                new_posts.append(news_post)
    except Exception as ex:
        print(f"{source_name} fetch error: {ex}")
    return new_posts


def search_rss_bridge(source: str) -> Optional[str]:
    """Try to find an RSS bridge for a site. Returns URL or None."""
    # RSS-Bridge instances that may have Truth Social bridges
    bridges = [
        "https://rss-bridge.org/bridge01/?action=display&bridge=TruthSocial&username=realDonaldTrump&format=Atom",
        "https://rss-bridge.org/bridge01/?action=display&bridge=Twitter&username=realDonaldTrump&format=Atom",
    ]
    return None  # fallback to nitter/nitter


def fetch_nitter(username: str, dedup: set) -> list:
    """
    Fetch tweets via Nitter (privacy-friendly Twitter front-end).
    Multiple Nitter instances for redundancy.
    """
    instances = [
        "https://nitter.net",
        "https://nitter.1d4.us",
        "https://nitter.kavin.rocks",
        "https://nitter.unixfox.eu",
    ]
    new_posts = []
    import html
    from bs4 import BeautifulSoup

    for instance in instances:
        try:
            url = f"{instance}/{username}/with_replies/rss"
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
            })
            if not resp.ok:
                continue
            # Nitter RSS feed
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item"):
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                desc = item.findtext("description", "")
                pubdate = item.findtext("pubDate", "")
                # Clean HTML from description
                if desc:
                    soup = BeautifulSoup(desc, "html.parser")
                    desc = soup.get_text(separator=" ", strip=True)
                # Clean common X prefix like "realDonaldTrump: "
                if ":" in title:
                    title = title.split(":", 1)[1].strip()
                text = desc or title
                if not text or text.startswith("@") or len(text) < 10:
                    continue
                pid = post_id(text, "X", pubdate)
                if pid not in dedup:
                    dedup.add(pid)
                    new_posts.append({
                        "id": pid,
                        "source": "X (Twitter)",
                        "title": title,
                        "text": text[:500],
                        "url": link,
                        "timestamp": pubdate,
                        "detected_at": datetime.now(HKT).isoformat(),
                    })
            if new_posts:
                break  # success on this instance
        except Exception as e:
            print(f"Nitter {instance} error: {e}")
            continue
    return new_posts


def fetch_truth_social_via_rss(dedup: set) -> list:
    """
    Fetch Truth Social posts. Truth Social doesn't have public RSS/API,
    but some RSS-Bridge instances may work. Fallback to nitter-style
    by checking specific RSS services.
    """
    new_posts = []
    # Try known bridge for Truth Social
    bridges = [
        "https://rss-bridge.org/bridge01/?action=display&bridge=TruthSocial&username=realDonaldTrump&format=Atom",
        # Alternative: use a service that mirrors Truth Social
        "https://truthsocial.railsapi.com/users/realDonaldTrump/feed.atom",
        "https://api.truthsocial.com/api/v1/accounts/107780447626640395/statuses?limit=5",
    ]
    for url in bridges:
        try:
            if "api." in url and "truthsocial.com" in url:
                # Direct API
                resp = requests.get(url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
                })
                if not resp.ok:
                    continue
                posts = resp.json()
                for p in posts[:10]:
                    text = p.get("content", "")
                    if not text:
                        continue
                    # Strip HTML from content
                    import re
                    text = re.sub(r"<[^>]+>", "", text).strip()
                    ts = p.get("created_at", "")
                    pid = post_id(text, "Truth Social", ts)
                    if pid not in dedup:
                        dedup.add(pid)
                        new_posts.append({
                            "id": pid,
                            "source": "Truth Social",
                            "title": "",
                            "text": text[:500],
                            "url": p.get("url", f"https://truthsocial.com/@realDonaldTrump/{p.get('id', '')}"),
                            "timestamp": ts,
                            "detected_at": datetime.now(HKT).isoformat(),
                        })
            else:
                # RSS bridge
                import xml.etree.ElementTree as ET
                resp = requests.get(url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
                })
                if not resp.ok:
                    continue
                root = ET.fromstring(resp.content)
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall(".//atom:entry", ns):
                    title = entry.findtext("atom:title", "", ns)
                    content = entry.findtext("atom:content", "", ns)
                    published = entry.findtext("atom:published", "", ns)
                    link_el = entry.find("atom:link", ns)
                    link = link_el.get("href", "") if link_el is not None else ""
                    text = content or title
                    import re
                    text = re.sub(r"<[^>]+>", "", text).strip()
                    if not text or len(text) < 10:
                        continue
                    pid = post_id(text, "Truth Social", published)
                    if pid not in dedup:
                        dedup.add(pid)
                        new_posts.append({
                            "id": pid,
                            "source": "Truth Social",
                            "title": title,
                            "text": text[:500],
                            "url": link,
                            "timestamp": published,
                            "detected_at": datetime.now(HKT).isoformat(),
                        })
            if new_posts:
                break  # first working source
        except Exception as e:
            print(f"TruthSocial bridge {url} error: {e}")
            continue
    return new_posts


KEYWORDS_ALERT = [
    "bitcoin", "crypto", "tariff", "china", "stock market",
    "fed", "interest rate", "inflation", "recession",
    "dollar", "oil", "energy", "defense",
    "nvda", "apple", "google", "meta", "amazon", "microsoft",
    "pltr", "ba", "tesla", "trump media",
]

def check_keywords(text: str) -> list:
    """Check if text contains any monitored keywords."""
    text_lower = text.lower()
    hits = []
    for kw in KEYWORDS_ALERT:
        if kw in text_lower:
            hits.append(kw)
    return hits

def main():
    print(f"[{datetime.now(HKT).isoformat()}] Trump Social Monitor starting...")
    
    load_telegram_creds()
    dedup = load_dedup()
    all_new_posts = []
    
    # Fetch from X (via Nitter RSS)
    print("Fetching X/Twitter...")
    x_posts = fetch_nitter("realDonaldTrump", dedup)
    all_new_posts.extend(x_posts)
    print(f"  → {len(x_posts)} new posts from X")
    
    # Fetch from Truth Social
    print("Fetching Truth Social...")
    ts_posts = fetch_truth_social_via_rss(dedup)
    all_new_posts.extend(ts_posts)
    print(f"  → {len(ts_posts)} new posts from Truth Social")
    
    # Save dedup
    save_dedup(dedup)
    
    # Load existing feed and merge new posts
    existing_feed = []
    if os.path.exists(FEED_FILE):
        try:
            with open(FEED_FILE, "r") as f:
                existing_feed = json.load(f)
        except:
            existing_feed = []
    
    # Prepend new posts
    all_posts = all_new_posts + existing_feed
    # Keep max 200 posts
    all_posts = all_posts[:200]
    
    with open(FEED_FILE, "w") as f:
        json.dump(all_posts, f, ensure_ascii=False, indent=2)
    
    print(f"Total feed size: {len(all_posts)} posts")
    
    # Telegram alerts
    for post in all_new_posts:
        kw_hits = check_keywords(post.get("text", "") or post.get("title", ""))
        if kw_hits:
            source = post.get("source", "Unknown")
            text = (post.get("text", "") or post.get("title", "") or "")[:300]
            ts = post.get("timestamp", "")[:19]
            msg = (
                f"🔴 <b>Trump Alert</b> | {source}\n"
                f"📌 Keywords: {', '.join(kw_hits[:5])}\n"
                f"🕐 {ts} HKT\n"
                f"\n{text}"
            )
            if post.get("url"):
                msg += f"\n\n<a href='{post['url']}'>🔗 View Post</a>"
            send_telegram(msg)
            print(f"  🔔 Telegram alert sent for keyword: {kw_hits}")
    
    print(f"[{datetime.now(HKT).isoformat()}] Done. {len(all_new_posts)} new, {len(all_posts)} total.")

if __name__ == "__main__":
    main()
