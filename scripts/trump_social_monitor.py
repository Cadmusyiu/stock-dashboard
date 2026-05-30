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

# ── Multi-Category Market Lexicon ───────────────────────────────────
# Each entry: (regex, {asset_class: impact}, category, description)
# asset_class: us_equities | fx | crypto | geo_risk
# impact: -1.0 (max bearish) to +1.0 (max bullish) per asset class
# 0.0 = neutral / no opinion on that asset
MARKET_LEXICON = [
    # ════════════════════════════════════════════════════════════════
    # TRADE / TARIFF
    # ════════════════════════════════════════════════════════════════
    (r"tariff(s)?", {"us_equities": -0.40, "fx": -0.10, "crypto": 0.0, "geo_risk": 0.35}, "trade", "Tariffs = trade war risk, bearish equities"),
    (r"tariff (man|wom)", {"us_equities": -0.50, "fx": -0.15, "crypto": 0.0, "geo_risk": 0.40}, "trade", "Tariff Man persona = aggressive stance"),
    (r"trade (war|dispute|tension)", {"us_equities": -0.50, "fx": -0.15, "crypto": -0.10, "geo_risk": 0.50}, "trade", "Trade war = bearish all risk assets"),
    (r"trade (deal|agreement|negotiation)", {"us_equities": 0.30, "fx": 0.10, "crypto": 0.10, "geo_risk": -0.30}, "trade", "Trade deal = bullish risk assets"),
    (r"reciprocal", {"us_equities": -0.25, "fx": -0.05, "crypto": 0.0, "geo_risk": 0.25}, "trade", "Reciprocal tariffs = retaliation risk"),
    (r"(unfair|unjust) trade", {"us_equities": -0.15, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.20}, "trade", "Claims unfair trade = escalation risk"),

    # ════════════════════════════════════════════════════════════════
    # FED / INTEREST RATES
    # ════════════════════════════════════════════════════════════════
    (r"(rate cut|lower(ing)? (interest )?rate|cut (interest )?rates)", {"us_equities": 0.40, "fx": -0.25, "crypto": 0.25, "geo_risk": -0.10}, "monetary", "Rate cuts = bullish equities/crypto, bearish USD"),
    (r"(rate hike|raise (interest )?rate)", {"us_equities": -0.45, "fx": 0.30, "crypto": -0.30, "geo_risk": 0.10}, "monetary", "Rate hikes = bearish risk, bullish USD"),
    (r"(fed|federal reserve)", {"us_equities": 0.0, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "monetary", "Fed mention — neutral by itself"),
    (r"interest rate(s)?", {"us_equities": 0.0, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.05}, "monetary", "Interest rate mention — context needed"),
    (r"quantitative easing", {"us_equities": 0.30, "fx": -0.20, "crypto": 0.30, "geo_risk": -0.10}, "monetary", "QE = bullish liquidity across risk assets"),
    (r"tighten(ing)?", {"us_equities": -0.30, "fx": 0.20, "crypto": -0.20, "geo_risk": 0.05}, "monetary", "Tightening = bearish risk, bullish USD"),

    # ════════════════════════════════════════════════════════════════
    # INFLATION / MACRO
    # ════════════════════════════════════════════════════════════════
    (r"inflation", {"us_equities": -0.30, "fx": -0.10, "crypto": 0.0, "geo_risk": 0.15}, "macro", "Inflation = stagflation risk, bearish equities"),
    (r"(disinflation|deflation)", {"us_equities": 0.15, "fx": 0.0, "crypto": 0.0, "geo_risk": -0.05}, "macro", "Disinflation = mildly positive"),
    (r"(hyperinflation|runaway inflation)", {"us_equities": -0.60, "fx": -0.40, "crypto": 0.15, "geo_risk": 0.40}, "macro", "Hyperinflation = very bearish trad-fi, crypto hedge"),
    (r"(cpi|consumer price)", {"us_equities": -0.15, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.05}, "macro", "CPI data mention — mildly bearish if high"),
    (r"recession", {"us_equities": -0.60, "fx": -0.20, "crypto": -0.25, "geo_risk": 0.35}, "macro", "Recession mention = bearish all risk assets"),
    (r"(economic (slowdown|downturn|weakness))", {"us_equities": -0.45, "fx": -0.15, "crypto": -0.15, "geo_risk": 0.25}, "macro", "Economic weakness = bearish"),
    (r"(depression|great depression)", {"us_equities": -0.80, "fx": -0.30, "crypto": -0.30, "geo_risk": 0.50}, "macro", "Depression comparison = extreme bearish"),
    (r"(soft landing|economic resilience|robust)", {"us_equities": 0.35, "fx": 0.10, "crypto": 0.20, "geo_risk": -0.15}, "macro", "Soft landing = bullish risk assets"),

    # ════════════════════════════════════════════════════════════════
    # DOLLAR / FX
    # ════════════════════════════════════════════════════════════════
    (r"(strong|powerful) dollar", {"us_equities": -0.15, "fx": 0.35, "crypto": -0.15, "geo_risk": 0.0}, "fx", "Strong USD = mixed (good for imports, but hurts exports)"),
    (r"(weak|weakening) dollar", {"us_equities": 0.15, "fx": -0.30, "crypto": 0.20, "geo_risk": 0.0}, "fx", "Weak USD = inflationary, bullish gold/crypto"),
    (r"(devalue|devaluation)", {"us_equities": -0.30, "fx": -0.40, "crypto": 0.10, "geo_risk": 0.30}, "fx", "Devaluation = bearish, capital outflow risk"),
    (r"(reserve currency|petrodollar)", {"us_equities": 0.15, "fx": 0.40, "crypto": 0.0, "geo_risk": -0.20}, "fx", "Reserve currency status = bullish USD"),
    (r"de-dollarization", {"us_equities": -0.20, "fx": -0.50, "crypto": 0.25, "geo_risk": 0.35}, "fx", "De-dollarization = bearish USD, bullish crypto"),

    # ════════════════════════════════════════════════════════════════
    # OIL / ENERGY
    # ════════════════════════════════════════════════════════════════
    (r"(oil (price|supply|production))", {"us_equities": -0.05, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.05}, "energy", "Oil mention — mildly bearish if higher prices"),
    (r"(drill|fracking|energy independence|energy dominance)", {"us_equities": 0.10, "fx": 0.15, "crypto": 0.0, "geo_risk": -0.05}, "energy", "Drill = positive energy sector, USD"),
    (r"(opec|oil cut|supply cut|production cut)", {"us_equities": -0.10, "fx": -0.05, "crypto": 0.0, "geo_risk": 0.15}, "energy", "OPEC cuts = higher oil, mixed for economy"),
    (r"(gas price|petrol)", {"us_equities": -0.10, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "energy", "Gas prices = consumer headwind"),

    # ════════════════════════════════════════════════════════════════
    # DEFENSE / GEOPOLITICS
    # ════════════════════════════════════════════════════════════════
    (r"(defense|military spending|pentagon)", {"us_equities": 0.15, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.05}, "defense", "Defense spending = bullish defense/industrial stocks"),
    (r"(war|military action|strike|bomb)", {"us_equities": -0.30, "fx": 0.10, "crypto": -0.15, "geo_risk": 0.70}, "conflict", "Military action = geopolitical risk spike, sell risk"),
    (r"(nuclear (weapon|program|threat|capability))", {"us_equities": -0.20, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.65}, "conflict", "Nuclear threat = high geopolitical risk"),
    (r"(sanction|embargo)", {"us_equities": -0.10, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.30}, "conflict", "Sanctions = trade disruption, geopolitical tension"),
    (r"(peace (deal|agreement|negotiation|process))", {"us_equities": 0.25, "fx": 0.10, "crypto": 0.10, "geo_risk": -0.50}, "conflict", "Peace progress = de-escalation, bullish"),
    (r"ceasefire", {"us_equities": 0.20, "fx": 0.05, "crypto": 0.10, "geo_risk": -0.45}, "conflict", "Ceasefire = de-escalation, mildly bullish"),
    (r"(troop|deploy|escalat)", {"us_equities": -0.25, "fx": 0.0, "crypto": -0.10, "geo_risk": 0.60}, "conflict", "Troop deployment = escalation risk"),

    # ════════════════════════════════════════════════════════════════
    # IRAN
    # ════════════════════════════════════════════════════════════════
    (r"iran", {"us_equities": -0.10, "fx": 0.05, "crypto": 0.0, "geo_risk": 0.35}, "geopolitics", "Iran mention = geopolitical risk"),
    (r"(iran deal|jcpoa)", {"us_equities": 0.25, "fx": 0.10, "crypto": 0.10, "geo_risk": -0.40}, "geopolitics", "Iran deal = de-escalation, bullish"),
    (r"iran.*(nuclear|weapon|missile|threat)", {"us_equities": -0.25, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.65}, "geopolitics", "Iran nuclear = serious geopolitical risk"),
    (r"(maximum pressure|snapback|iran sanction)", {"us_equities": -0.10, "fx": 0.05, "crypto": 0.0, "geo_risk": 0.30}, "geopolitics", "Iran pressure = tension"),

    # ════════════════════════════════════════════════════════════════
    # STOCK MARKET
    # ════════════════════════════════════════════════════════════════
    (r"(stock market|dow|s&amp;p|nasdaq|s&p)", {"us_equities": 0.15, "fx": 0.0, "crypto": 0.05, "geo_risk": 0.0}, "markets", "Stock market mention — bullish if claiming success"),
    (r"(new high|record high|all.?time high)", {"us_equities": 0.60, "fx": 0.10, "crypto": 0.20, "geo_risk": -0.15}, "markets", "Record highs = strong bullish signal"),
    (r"(market (crash|plunge|tumble|sell.?off|bloodbath))", {"us_equities": -0.70, "fx": 0.15, "crypto": -0.40, "geo_risk": 0.30}, "markets", "Market crash = very bearish risk assets"),
    (r"(rally|surge|soar|boom|roaring)", {"us_equities": 0.45, "fx": 0.0, "crypto": 0.20, "geo_risk": 0.0}, "markets", "Market rally = bullish"),
    (r"(volatility|uncertainty|turbulence|jitters)", {"us_equities": -0.25, "fx": 0.05, "crypto": -0.15, "geo_risk": 0.20}, "markets", "Volatility/unexpected = mildly bearish"),

    # ════════════════════════════════════════════════════════════════
    # CRYPTO
    # ════════════════════════════════════════════════════════════════
    (r"(bitcoin|crypto|blockchain)", {"us_equities": 0.05, "fx": 0.0, "crypto": 0.40, "geo_risk": 0.0}, "crypto", "Crypto mention = positive (Trump pro-crypto stance)"),
    (r"(digital asset|web3|defi)", {"us_equities": 0.0, "fx": 0.0, "crypto": 0.35, "geo_risk": 0.0}, "crypto", "Digital asset mention = pro-crypto signal"),
    (r"(cbdc|central bank digital currency)", {"us_equities": 0.0, "fx": 0.0, "crypto": -0.15, "geo_risk": 0.0}, "crypto", "CBDC = bearish for decentralized crypto"),
    (r"(crypto (reserve|regulation|bill|policy|framework))", {"us_equities": 0.10, "fx": 0.0, "crypto": 0.50, "geo_risk": 0.0}, "crypto", "Crypto policy progress = very bullish crypto"),

    # ════════════════════════════════════════════════════════════════
    # CHINA
    # ════════════════════════════════════════════════════════════════
    (r"china", {"us_equities": -0.15, "fx": -0.05, "crypto": 0.0, "geo_risk": 0.25}, "geopolitics", "China mention = trade tension, mildly bearish"),
    (r"(china.*(tariff|trade|sanction|barrier))", {"us_equities": -0.40, "fx": -0.10, "crypto": -0.10, "geo_risk": 0.40}, "geopolitics", "China tariff/sanction = trade war escalation"),
    (r"(china.*(deal|agreement|cooperation|partner))", {"us_equities": 0.25, "fx": 0.10, "crypto": 0.10, "geo_risk": -0.30}, "geopolitics", "China deal = de-escalation, mildly bullish"),
    (r"(taiwan|south china sea)", {"us_equities": -0.20, "fx": 0.0, "crypto": -0.10, "geo_risk": 0.55}, "geopolitics", "Taiwan/SCS = geopolitical flashpoint"),

    (r"tax cut(s)?", {"us_equities": 0.50, "fx": 0.10, "crypto": 0.15, "geo_risk": 0.0}, "fiscal", "Tax cuts = bullish equities"),
    (r"tax (increase|hike|raise)", {"us_equities": -0.50, "fx": -0.10, "crypto": -0.10, "geo_risk": 0.05}, "fiscal", "Tax hikes = bearish equities"),
    (r"(corporate|business) tax", {"us_equities": 0.30, "fx": 0.05, "crypto": 0.10, "geo_risk": 0.0}, "fiscal", "Corporate tax cuts = bullish"),
    (r"(deficit|national debt|fiscal)", {"us_equities": -0.25, "fx": -0.10, "crypto": 0.0, "geo_risk": 0.20}, "fiscal", "Deficit/debt = fiscal risk, bearish"),
    (r"(government shutdown|debt ceiling)", {"us_equities": -0.45, "fx": -0.10, "crypto": -0.15, "geo_risk": 0.35}, "fiscal", "Govt shutdown = uncertainty, bearish"),
    (r"infrastructure", {"us_equities": 0.20, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "fiscal", "Infrastructure spending = mildly bullish"),

    # ════════════════════════════════════════════════════════════════
    # COMPANY-SPECIFIC
    # ════════════════════════════════════════════════════════════════
    (r"(nvda|nvidia)", {"us_equities": 0.15, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "stocks", "NVIDIA = AI leader, mildly positive"),
    (r"(apple|aapl)", {"us_equities": 0.10, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "stocks", "Apple = market bellwether"),
    (r"(googl|google)", {"us_equities": 0.10, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "stocks", "Google = tech bellwether"),
    (r"(meta|facebook)", {"us_equities": 0.10, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "stocks", "Meta = tech bellwether"),
    (r"(amzn|amazon)", {"us_equities": 0.10, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "stocks", "Amazon = bellwether"),
    (r"(msft|microsoft)", {"us_equities": 0.15, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "stocks", "Microsoft = AI leader"),
    (r"(pltr|palantir)", {"us_equities": 0.20, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "stocks", "Palantir = defense/AI"),
    (r"(ba|boeing)", {"us_equities": -0.10, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "stocks", "Boeing = mixed"),
    (r"(tsla|tesla)", {"us_equities": 0.15, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "stocks", "Tesla = Trump/Musk alignment"),
    (r"(djia|trump media|djt|truth social)", {"us_equities": 0.20, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}, "stocks", "Trump Media = correlation"),

    # ════════════════════════════════════════════════════════════════
    # IMMIGRATION (minimal market impact)
    # ════════════════════════════════════════════════════════════════
    (r"(border|immigration|deport)", {"us_equities": -0.05, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.10}, "policy", "Immigration = political uncertainty"),

    # ════════════════════════════════════════════════════════════════
    # ELECTION / POLITICS (low/no market signal — excluded)
    # NOTHING — all political rhetoric (win, victory, witch hunt,
    # fake news, great, horrible, etc.) is excluded by design.
    # Pure market signal only.
    # ════════════════════════════════════════════════════════════════
]

def analyze_sentiment(text: str) -> dict:
    """
    Multi-category market sentiment analysis.
    Scores each post against 4 dimensions:
      - us_equities: impact on US stock market
      - fx: impact on USD
      - crypto: impact on crypto markets
      - geo_risk: geopolitical risk level (>0 = higher risk, no bullish/bearish opposite)
    Falls back to TextBlob only when no market terms matched.
    """
    if not text or len(text) < 10:
        return {
            "polarity": 0.0, "label": "neutral",
            "method": "none", "matches": [], "confidence": 0,
            "us_equities": 0.0, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0,
        }

    txt_lower = text.lower()
    matches = []
    scores = {"us_equities": 0.0, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}
    impact_counts = {"us_equities": 0, "fx": 0, "crypto": 0, "geo_risk": 0}

    for pattern, impacts, category, explanation in MARKET_LEXICON:
        if re.search(pattern, txt_lower):
            # Build clean term name
            raw = pattern.strip("()").replace("\\","").split("|")[0].rstrip("?s")
            term = raw[:25]
            matches.append({
                "term": term, "category": category,
                "impacts": impacts, "explanation": explanation
            })
            for asset_class, impact in impacts.items():
                scores[asset_class] += impact
                if impact != 0:
                    impact_counts[asset_class] += 1

    # Average scores across matched categories
    for k in scores:
        if impact_counts[k] > 0:
            scores[k] = round(scores[k] / max(1, impact_counts[k] * 0.5), 3)
            # 0.5 dampening: stronger when multiple matches confirm direction
            scores[k] = max(-1.0, min(1.0, scores[k] * (1 - 0.3 / max(1, impact_counts[k]))))
            scores[k] = round(max(-1.0, min(1.0, scores[k])), 3)

    if matches:
        # Overall polarity = weighted avg of us_equities + crypto
        pol = round((scores["us_equities"] * 2 + scores["crypto"] * 1) / 3, 3)
        confidence = min(1.0, len(matches) * 0.12)
        method = "lexicon"
    else:
        # No market terms — fallback to TextBlob. Label only, no multi-category.
        blob = TextBlob(text[:2000])
        pol = blob.sentiment.polarity
        confidence = min(1.0, len(text) / 500)
        method = "textblob"
        scores = {"us_equities": 0.0, "fx": 0.0, "crypto": 0.0, "geo_risk": 0.0}

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
        "label": label,
        "method": method,
        "matches": matches[:8],
        "confidence": round(confidence, 2),
        "us_equities": scores["us_equities"],
        "fx": scores["fx"],
        "crypto": scores["crypto"],
        "geo_risk": scores["geo_risk"],
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
            sent = post.get("sentiment", {})
            sent_str = sentiment_emoji(label) + f" {polarity:+.2f}"
            # Build market signal line if lexicon method
            sig = ""
            if sent and sent.get("method") == "lexicon":
                parts = []
                eq = sent.get("us_equities", 0)
                fx = sent.get("fx", 0)
                cr = sent.get("crypto", 0)
                gr = sent.get("geo_risk", 0)
                if eq: parts.append(f"🇺🇸{eq:+.2f}")
                if fx: parts.append(f"💵{fx:+.2f}")
                if cr: parts.append(f"₿{cr:+.2f}")
                if gr: parts.append(f"⚠️{gr:.2f}")
                if parts:
                    sig = " | ".join(parts) + "\n"
            msg = (
                f"🔴 <b>Trump Alert</b> | {src} {sent_str}\n"
                f"📌 {', '.join(alert_reason[:4])}\n"
                f"{sig}"
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
