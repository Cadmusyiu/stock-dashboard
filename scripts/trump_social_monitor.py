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
from bs4 import BeautifulSoup
from textblob import TextBlob
from collections import Counter

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
    # Fallback: 1Password CLI (may fail if session expired)
    try:
        import subprocess
        result = subprocess.run(
            ["op", "read", "op://Cadai API Keys/Telegram Bot - CadAI Openclaw/bot_token"],
            capture_output=True, text=True, timeout=8
        )
        if result.returncode == 0:
            TELEGRAM_BOT_TOKEN = result.stdout.strip()
        result2 = subprocess.run(
            ["op", "read", "op://Cadai API Keys/Telegram Bot - NLP Sentiment/chat_id"],
            capture_output=True, text=True, timeout=8
        )
        if result2.returncode == 0:
            TELEGRAM_CHAT_ID = result2.stdout.strip()
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

# ── Financial/Political Keyword Lexicon ─────────────────────────────
FINANCIAL_LEXICON = [
    (r"tariff(s)?", -0.40, "trade", "Tariffs = trade war risk, bearish for equities"),
    (r"tariff (man|wom)", -0.60, "trade", "Tariff Man persona = aggressive trade stance"),
    (r"trade (war|dispute|tension)", -0.50, "trade", "Trade war escalation = bearish"),
    (r"trade (deal|agreement|negotiation)", 0.25, "trade", "Trade deal progress = mildly bullish"),
    (r"reciprocal", -0.20, "trade", "Reciprocal tariffs = retaliation risk"),
    (r"(unfair|unjust) trade", -0.30, "trade", "Claims unfair trade = escalation risk"),
    (r"america first", 0.15, "policy", "America First = protectionist, mixed"),
    (r"(fed|federal reserve)", -0.05, "monetary", "Fed mention — neutral by itself"),
    (r"(rate cut|lower(ing)? (interest )?rate)", 0.35, "monetary", "Rate cuts = bullish equities"),
    (r"(rate hike|raise (interest )?rate)", -0.40, "monetary", "Rate hikes = bearish equities"),
    (r"interest rate(s)?", -0.05, "monetary", "Interest rate mention — context needed"),
    (r"quantitative easing", 0.25, "monetary", "QE = bullish liquidity"),
    (r"tighten(ing)?", -0.30, "monetary", "Tightening = bearish liquidity"),
    (r"inflation", -0.35, "macro", "Inflation = stagflation risk, bearish"),
    (r"(disinflation|deflation)", 0.10, "macro", "Disinflation = mildly positive"),
    (r"(hyperinflation|runaway inflation)", -0.70, "macro", "Hyperinflation threat = very bearish"),
    (r"(cpi|consumer price)", -0.20, "macro", "CPI high = rate pressure"),
    (r"recession", -0.60, "macro", "Recession mention = very bearish"),
    (r"(economic (slowdown|downturn|weakness))", -0.45, "macro", "Economic weakness = bearish"),
    (r"(depression|great depression)", -0.80, "macro", "Depression comparison = extremely bearish"),
    (r"(soft landing|economic resilience)", 0.30, "macro", "Soft landing = bullish"),
    (r"(strong|powerful) dollar", 0.20, "fx", "Strong dollar = mixed"),
    (r"(weak|weakening) dollar", -0.25, "fx", "Weak dollar = inflationary"),
    (r"(devalue|devaluation)", -0.40, "fx", "Currency devaluation = bearish"),
    (r"(reserve currency|petrodollar)", 0.30, "fx", "Reserve currency status = bullish USD"),
    (r"de-dollarization", -0.50, "fx", "De-dollarization threat = very bearish USD"),
    (r"(oil (price|supply|production))", -0.10, "energy", "Oil mention — context dependent"),
    (r"(drill|fracking|energy independence)", 0.20, "energy", "Drill baby drill = bullish energy stocks"),
    (r"(opec|oil cut|supply cut)", -0.25, "energy", "OPEC cuts = higher oil = mixed"),
    (r"(gas price|petrol)", -0.15, "energy", "Gas prices rising = consumer pain"),
    (r"(green energy|renewable|climate)", 0.10, "energy", "Green energy = positive for renewables"),
    (r"(defense|military spending|pentagon)", 0.10, "defense", "Defense spending = bullish defense stocks"),
    (r"(war|military action|strike|bomb)", -0.50, "conflict", "Military action = geopolitical risk, bearish"),
    (r"(nuclear (weapon|program|threat))", -0.45, "conflict", "Nuclear threat = high geopolitical risk"),
    (r"(sanction|embargo)", -0.20, "conflict", "Sanctions = trade disruption risk"),
    (r"(peace (deal|agreement|negotiation))", 0.40, "conflict", "Peace progress = risk down, bullish"),
    (r"ceasefire", 0.35, "conflict", "Ceasefire = de-escalation, mildly bullish"),
    (r"(troop|deploy|escalat)", -0.35, "conflict", "Troop deployment = escalation risk"),
    (r"iran", -0.25, "geopolitics", "Iran mention = geopolitical risk"),
    (r"(iran deal|jcpoa)", 0.30, "geopolitics", "Iran deal diplomacy = de-escalation"),
    (r"iran.*(nuclear|weapon|missile)", -0.50, "geopolitics", "Iran nuclear threat = high risk"),
    (r"(maximum pressure|snapback|iran sanction)", -0.25, "geopolitics", "Iran pressure = tension"),
    (r"(stock market|dow|s&p|nasdaq)", 0.10, "markets", "Stock market mention — bullish claims"),
    (r"(new high|record high|all.?time high)", 0.50, "markets", "Record highs = bullish confidence"),
    (r"(market (crash|plunge|tumble|sell.?off))", -0.70, "markets", "Market crash = very bearish"),
    (r"(rally|surge|soar|boom)", 0.40, "markets", "Market rally = bullish"),
    (r"(volatility|uncertainty|turbulence)", -0.25, "markets", "Volatility/uncertainty = mildly bearish"),
    (r"(bitcoin|crypto|blockchain)", 0.20, "crypto", "Crypto mention = positive (Trump pro-crypto)"),
    (r"(digital asset|web3|defi)", 0.25, "crypto", "Digital asset mention = positive signal"),
    (r"(cbdc|central bank digital currency)", -0.10, "crypto", "CBDC = mixed for decentralized crypto"),
    (r"(crypto (reserve|regulation|bill|policy))", 0.30, "crypto", "Crypto policy progress = bullish"),
    (r"china", -0.20, "geopolitics", "China mention = trade tension, mildly bearish"),
    (r"(china.*(tariff|trade|sanction))", -0.40, "geopolitics", "China tariff/sanction = trade war escalation"),
    (r"(china.*(deal|agreement|cooperation))", 0.20, "geopolitics", "China deal = de-escalation, mildly bullish"),
    (r"(taiwan|south china sea)", -0.35, "geopolitics", "Taiwan/SCS = geopolitical flashpoint"),
    (r"tax cut(s)?", 0.40, "fiscal", "Tax cuts = bullish for equities"),
    (r"tax (increase|hike|raise)", -0.45, "fiscal", "Tax hikes = bearish for equities"),
    (r"(corporate|business) tax", 0.30, "fiscal", "Corporate tax = positive if cuts"),
    (r"(deficit|national debt|fiscal)", -0.30, "fiscal", "Deficit/debt = fiscal risk, bearish"),
    (r"(government shutdown|debt ceiling)", -0.40, "fiscal", "Govt shutdown = uncertainty, bearish"),
    (r"infrastructure", 0.20, "fiscal", "Infrastructure spending = mildly bullish"),
    (r"(election|vote|ballot)", -0.10, "politics", "Election mention = political uncertainty"),
    (r"(landslide|win|victory)", 0.25, "politics", "Electoral win = stability, mildly bullish"),
    (r"(impeach|indict|investigat|prosecute)", -0.35, "politics", "Legal trouble = political risk, bearish"),
    (r"(fake news|witch hunt|hoax)", -0.20, "politics", "Attack rhetoric = combative"),
    (r"(rigged|fraud|corrupt)", -0.35, "politics", "Corruption claims = instability, bearish"),
    (r"(nvda|nvidia)", 0.15, "stocks", "NVIDIA = AI leader, mildly positive"),
    (r"(apple|aapl)", 0.10, "stocks", "Apple = market bellwether"),
    (r"(googl|google)", 0.10, "stocks", "Google = tech bellwether"),
    (r"(meta|facebook)", 0.10, "stocks", "Meta = tech bellwether"),
    (r"(amzn|amazon)", 0.10, "stocks", "Amazon = consumer/cloud bellwether"),
    (r"(msft|microsoft)", 0.15, "stocks", "Microsoft = AI leader"),
    (r"(pltr|palantir)", 0.20, "stocks", "Palantir = defense/AI correlation"),
    (r"(ba|boeing)", -0.10, "stocks", "Boeing = mixed"),
    (r"(tsla|tesla)", 0.15, "stocks", "Tesla = Trump/Musk alignment"),
    (r"(djia|trump media|djt)", 0.20, "stocks", "Trump Media = correlation"),
    (r"(border|immigration|deport)", -0.15, "policy", "Immigration policy = political uncertainty"),
    (r"(wall|border security)", 0.10, "policy", "Border wall = base signal"),
    (r"(great (again|job|economy|day|honor))" , 0.30, "general", "Positive rhetoric"),
    (r"(disaster|catastrophe|terrible|worst|horrible)", -0.45, "general", "Negative rhetoric = bearish"),
    (r"(wonderful|beautiful|fantastic|incredible|unprecedented)", 0.20, "general", "Trump positive adjectives"),
    (r"(sad|pathetic|disgrace|weak|dumb|stupid)", -0.30, "general", "Trump negative adjectives"),
    (r"(we will win|we are winning|we won)", 0.25, "general", "Confidence signal"),
    (r"(they are killing us|they are destroying)", -0.40, "general", "Victim rhetoric = negative outlook"),
]

def analyze_sentiment(text: str) -> dict:
    """
    Hybrid financial/political sentiment analysis:
    1. Check keyword lexicon first (domain-specific financial/political terms)
    2. Fallback to TextBlob for general English sentiment
    
    Returns polarity, subjectivity, label, matched terms, method, and confidence.
    """
    if not text or len(text) < 10:
        return {
            "polarity": 0.0, "subjectivity": 0.0, "label": "neutral",
            "method": "none", "matches": [], "confidence": 0
        }
    txt_lower = text.lower()
    matched_terms = []
    total_impact = 0.0
    for pattern, impact, category, explanation in FINANCIAL_LEXICON:
        if re.search(pattern, txt_lower):
            matched_terms.append({
                "term": pattern.strip("()").replace("\\","").split("|")[0].rstrip("?s"),
                "impact": impact,
                "category": category,
                "explanation": explanation,
            })
            total_impact += impact
    if matched_terms:
        n = len(matched_terms)
        avg_impact = total_impact / n
        confidence = min(1.0, (n * abs(avg_impact)) / 0.5)
        pol = max(-1.0, min(1.0, avg_impact))
        subj = min(1.0, 0.3 + confidence * 0.4)
    else:
        blob = TextBlob(text[:2000])
        pol = blob.sentiment.polarity
        subj = blob.sentiment.subjectivity
        confidence = min(1.0, len(text) / 500)
        matched_terms = []
    if pol <= -0.5:
        label = "very_negative"
    elif pol <= -0.1:
        label = "negative"
    elif pol >= 0.5:
        label = "very_positive"
    elif pol >= 0.1:
        label = "positive"
    else:
        label = "neutral"
    return {
        "polarity": round(pol, 3),
        "subjectivity": round(subj, 3),
        "label": label,
        "method": "lexicon" if matched_terms else "textblob",
        "matches": matched_terms[:10],
        "confidence": round(confidence, 2),
    }

def sentiment_emoji(label: str) -> str:
    return {
        "very_negative": "🔴📉",
        "negative": "🔴",
        "neutral": "⚪",
        "positive": "🟢",
        "very_positive": "🟢📈",
    }.get(label, "⚪")

def check_keywords(text: str) -> list:
    tl = text.lower()
    return [kw for kw in KEYWORDS_ALERT if kw in tl]

# ── Truth Social (via trumpstruth.org RSS) ───────────────────────────
TRUTH_RSS = "https://www.trumpstruth.org/feed"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

def strip_html(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t).strip()

def fetch_truth_social(dedup: set) -> list:
    """Fetch Trump's Truth Social posts via trumpstruth.org RSS."""
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

            # Extract actual text from description HTML
            if desc:
                soup = BeautifulSoup(desc, "html.parser")
                text = ' '.join(soup.stripped_strings)
            else:
                text = ""

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

            sentiment = analyze_sentiment(text)
            new_posts.append({
                "id": pid,
                "source": "Truth Social",
                "title": clean_title,
                "text": text[:1000],
                "url": orig_url,
                "timestamp": pub,
                "detected_at": datetime.now(HKT).isoformat(),
                "sentiment": sentiment,
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

                sentiment = analyze_sentiment(text)
                new_posts.append({
                    "id": pid,
                    "source": "X (Twitter)",
                    "title": clean_title,
                    "text": text[:1000],
                    "url": f"https://x.com/realDonaldTrump/status/{hashlib.md5(text.encode()).hexdigest()[:12]}",
                    "timestamp": pub,
                    "detected_at": datetime.now(HKT).isoformat(),
                    "sentiment": sentiment,
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
                raw = json.load(f)
                # Support both old (list) and new (dict with posts key) formats
                if isinstance(raw, dict):
                    existing = raw.get("posts", [])
                else:
                    existing = raw
        except:
            pass

    merged = all_new + existing
    merged = merged[:200]  # cap

    # ── Sentiment metrics (last 24h) ──────────────────────────
    from datetime import timedelta
    now_hkt = datetime.now(HKT)
    recent = [p for p in merged if "detected_at" in p and (now_hkt - datetime.fromisoformat(p["detected_at"])).total_seconds() < 86400]
    if recent:
        polarities = [p.get("sentiment", {}).get("polarity", 0) for p in recent if p.get("sentiment")]
        labels = [p.get("sentiment", {}).get("label", "neutral") for p in recent if p.get("sentiment")]
        metrics = {
            "24h_post_count": len(recent),
            "24h_avg_polarity": round(sum(polarities)/len(polarities), 3) if polarities else 0,
            "24h_sentiment_breakdown": {
                "very_negative": labels.count("very_negative"),
                "negative": labels.count("negative"),
                "neutral": labels.count("neutral"),
                "positive": labels.count("positive"),
                "very_positive": labels.count("very_positive"),
            },
        }
        # Most extreme posts
        mn = min(recent, key=lambda x: x.get("sentiment", {}).get("polarity", 5))
        mx = max(recent, key=lambda x: x.get("sentiment", {}).get("polarity", -5))
        if mn.get("sentiment",{}).get("polarity",0) < 0:
            metrics["most_negative"] = {
                "text": (mn.get("text","") or "")[:200],
                "polarity": mn.get("sentiment",{}).get("polarity",0),
                "source": mn.get("source",""),
            }
        if mx.get("sentiment",{}).get("polarity",0) > 0:
            metrics["most_positive"] = {
                "text": (mx.get("text","") or "")[:200],
                "polarity": mx.get("sentiment",{}).get("polarity",0),
                "source": mx.get("source",""),
            }
    else:
        metrics = {"24h_post_count": 0, "24h_avg_polarity": 0, "24h_sentiment_breakdown": {}}

    output = {
        "posts": merged,
        "metrics": metrics,
        "updated_at": datetime.now(HKT).isoformat(),
    }

    with open(FEED_FILE, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Feed: {len(merged)} posts total ({len(all_new)} new)")
    print(f"  📊 24h sentiment avg: {metrics.get('24h_avg_polarity',0)} | breakdown: {metrics.get('24h_sentiment_breakdown',{})}")

    # Telegram alerts: only for strong sentiment + keywords, or very strong sentiment alone
    sent_alerts = []
    for post in all_new:
        txt = post.get("text", "") or post.get("title", "") or ""
        kw = check_keywords(txt)
        sent = post.get("sentiment", {})
        label = sent.get("label", "neutral") if sent else "neutral"
        polarity = sent.get("polarity", 0) if sent else 0
        
        should_alert = False
        alert_reason = []
        
        # Strong sentiment alone → alert
        if label in ("very_negative", "very_positive"):
            should_alert = True
            alert_reason.append(f"{label} ({polarity:+.2f})")
        
        # Keyword match with non-neutral sentiment → alert
        if kw and label in ("negative", "positive", "very_negative", "very_positive"):
            should_alert = True
            alert_reason.extend(kw[:3])
        
        if should_alert:
            src = post["source"]
            ts = post["timestamp"][:19] if post["timestamp"] else ""
            sent_str = sentiment_emoji(label) + f" {polarity:+.2f}"
            msg = (
                f"🔴 <b>Trump Alert</b> | {src} {sent_str}\n"
                f"📌 {', '.join(alert_reason[:4])}\n"
                f"🕐 {ts} HKT\n\n"
                f"{txt[:400]}"
            )
            if post.get("url"):
                msg += f"\n\n<a href='{post['url']}'>🔗 View Post</a>"
            sent_alerts.append(msg)
            print(f"  🔔 Alert: {alert_reason}")
    
    # Send alerts (max 5 per run to avoid spam)
    for msg in sent_alerts[:5]:
        send_telegram(msg)
    if len(sent_alerts) > 5:
        send_telegram(f"🔴 <b>Trump Social Summary</b> — {len(sent_alerts)} notable posts in this check.\nCheck dashboard for details: https://cadmusyiu.github.io/stock-dashboard/")

    print(f"\n[{datetime.now(HKT).isoformat()}] ✅ Done.")

if __name__ == "__main__":
    main()
