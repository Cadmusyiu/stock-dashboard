#!/usr/bin/env python3
"""
Trump Impact Score
==================
Quantifies the impact of Trump's Truth Social posts on specific stocks and sectors.
Uses keyword matching to map posts → affected tickers and assigns an impact score
(-5 to +5). Tracks cumulative impact over 24h and 7d windows.

Reads:  data/trump_social_feed.json
Writes: data/trump_impact_scores.json

Usage:
  python3 trump_impact_scorer.py
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
FEED_FILE = os.path.join(DATA_DIR, "trump_social_feed.json")
OUTPUT_FILE = os.path.join(DATA_DIR, "trump_impact_scores.json")


# ═══════════════════════════════════════════════════════════════
# Keyword → Ticker/Sector Mapping
# ═══════════════════════════════════════════════════════════════
# Each entry: (keyword_pattern, [list of affected tickers], sector)
# Impact direction: + = bullish for that ticker, - = bearish

KEYWORD_MAP = [
    # ── Crypto ─────────────────────────────────────────────────
    (r"\b(bitcoin|btc)\b", ["BTC", "COIN", "MSTR"], "Crypto", 0.8),
    (r"\b(crypto|cryptocurrency|digital asset)\b", ["COIN", "MSTR", "BTC"], "Crypto", 0.6),
    (r"\b(blockchain|defi|web3)\b", ["COIN", "MSTR"], "Crypto", 0.4),

    # ── Tariff / Trade ─────────────────────────────────────────
    (r"\b(tariff|tariffs)\b", ["CAT", "DE", "XOM", "BA"], "Trade/Tariff", -0.7),
    (r"\b(trade war|trade dispute)\b", ["CAT", "DE", "BA", "XOM"], "Trade/Tariff", -0.8),
    (r"\b(reciprocal)\b", ["CAT", "DE"], "Trade/Tariff", -0.5),
    (r"\b(unfair trade)\b", ["CAT", "DE"], "Trade/Tariff", -0.4),

    # ── China ──────────────────────────────────────────────────
    (r"\bchina\b", ["CAT", "DE", "AAPL", "NVDA", "AMD", "TSM"], "China", -0.5),
    (r"\b(china.*(tariff|sanction))\b", ["CAT", "DE", "AAPL", "AMD"], "China", -0.7),
    (r"\b(china.*(deal|agreement))\b", ["CAT", "DE", "AAPL", "NVDA"], "China", 0.5),

    # ── Trump Media ────────────────────────────────────────────
    (r"\b(djt|trump media|truth social)\b", ["DJT"], "Trump Media", 0.7),

    # ── Oil / Energy ───────────────────────────────────────────
    (r"\b(oil|crude|petroleum)\b", ["XLE", "XOM", "CVX", "OXY", "COP"], "Energy", 0.3),
    (r"\b(energy|drill|fracking)\b", ["XLE", "XOM", "CVX", "OXY"], "Energy", 0.4),
    (r"\b(gas price|petrol|gasoline)\b", ["XLE", "XOM", "CVX"], "Energy", -0.3),
    (r"\b(opec|energy independence)\b", ["XLE", "XOM", "OXY"], "Energy", 0.5),
    (r"\b(strategic petroleum reserve|spr)\b", ["XLE", "XOM"], "Energy", 0.3),

    # ── AI / Tech ──────────────────────────────────────────────
    (r"\b(ai|artificial intelligence|artificialintelligence)\b", ["QQQ", "NVDA", "MSFT", "GOOGL", "META"], "AI/Tech", 0.6),
    (r"\b(tech|technology)\b", ["QQQ", "NVDA", "MSFT", "AAPL"], "AI/Tech", 0.4),
    (r"\b(data center|data center|datacenter)\b", ["NVDA", "AMD", "MSTR", "CORZ", "IREN"], "AI/Tech", 0.5),
    (r"\b(semiconductor|chip|chips|microchip)\b", ["NVDA", "AMD", "TSM", "INTC", "SMH"], "AI/Tech", 0.4),
    (r"\b(nvda|nvidia)\b", ["NVDA"], "AI/Tech", 0.5),
    (r"\b(msft|microsoft)\b", ["MSFT"], "AI/Tech", 0.3),
    (r"\b(googl|google|alphabet)\b", ["GOOGL"], "AI/Tech", 0.2),
    (r"\b(meta|facebook)\b", ["META"], "AI/Tech", 0.2),
    (r"\b(amzn|amazon)\b", ["AMZN"], "AI/Tech", 0.3),
    (r"\b(aapl|apple)\b", ["AAPL"], "AI/Tech", 0.2),
    (r"\b(tsla|tesla)\b", ["TSLA"], "AI/Tech", 0.4),

    # ── Defense ────────────────────────────────────────────────
    (r"\b(defense|military|pentagon)\b", ["BA", "LMT", "NOC", "GD", "RTX", "PLTR"], "Defense", 0.5),
    (r"\b(war|military action|strike|bomb)\b", ["BA", "LMT", "NOC", "GD", "PLTR"], "Defense", 0.3),
    (r"\b(nuclear)\b", ["BA", "LMT", "NOC"], "Defense", 0.3),
    (r"\b(pltr|palantir)\b", ["PLTR"], "Defense", 0.6),

    # ── Fed / Rates ────────────────────────────────────────────
    (r"\b(rate cut|lower rates|cut rates)\b", ["SPY", "QQQ", "IWM"], "Monetary Policy", 0.6),
    (r"\b(rate hike|raise rates)\b", ["SPY", "QQQ", "IWM"], "Monetary Policy", -0.6),
    (r"\b(fed|federal reserve|powell)\b", ["SPY", "TLT"], "Monetary Policy", 0.0),  # Context-dependent
    (r"\b(inflation)\b", ["SPY", "GLD", "BTC"], "Monetary Policy", -0.4),
    (r"\b(recession)\b", ["SPY", "IWM", "QQQ"], "Monetary Policy", -0.7),

    # ── Dollar / FX ────────────────────────────────────────────
    (r"\b(dollar|usd|greenback)\b", ["DXY", "GLD"], "FX/Macro", 0.2),
    (r"\b(strong dollar)\b", ["SPY"], "FX/Macro", -0.2),
    (r"\b(weak dollar)\b", ["GLD", "BTC"], "FX/Macro", 0.3),
    (r"\b(reserve currency|petrodollar)\b", ["DXY"], "FX/Macro", 0.3),
    (r"\b(de-dollarization)\b", ["BTC", "GLD"], "FX/Macro", 0.4),

    # ── Stock Market ───────────────────────────────────────────
    (r"\b(stock market|dow|s&p|snp|nasdaq)\b", ["SPY", "QQQ", "IWM", "DIA"], "Stock Market", 0.3),
    (r"\b(record high|all.?time high|new high)\b", ["SPY", "QQQ"], "Stock Market", 0.8),
    (r"\b(market crash|plunge|tumble|sell.?off|bloodbath)\b", ["SPY", "QQQ", "IWM"], "Stock Market", -0.8),
    (r"\b(rally|surge|soar|boom)\b", ["SPY", "QQQ"], "Stock Market", 0.6),

    # ── Sector-Specific ───────────────────────────────────────
    (r"\b(auto|automotive|car)\b", ["TSLA", "GM", "F"], "Auto", 0.2),
    (r"\b(healthcare|health|medical)\b", ["XLV", "UNH", "LLY"], "Healthcare", 0.2),
    (r"\b(pharma|pharmaceutical|drug)\b", ["XLV", "PFE", "MRK", "LLY"], "Healthcare", 0.2),
    (r"\b(bank|banking|financial)\b", ["XLF", "JPM", "BAC", "GS"], "Financial", 0.3),
    (r"\b(housing|real estate|home)\b", ["XLRE", "XHB"], "Housing", 0.2),
    (r"\b(consumer|retail|spending)\b", ["XLY", "XLP", "AMZN", "WMT"], "Consumer", 0.2),
    (r"\b(infrastructure|roads|bridges|highway)\b", ["CAT", "DE", "PWR", "XLI"], "Infrastructure", 0.5),

    # ── Tax / Fiscal ────────────────────────────────────────────
    (r"\b(tax cut|cut taxes)\b", ["SPY", "IWM"], "Fiscal Policy", 0.7),
    (r"\b(tax hike|tax increase|raise taxes)\b", ["SPY", "IWM"], "Fiscal Policy", -0.7),
    (r"\b(corporate tax)\b", ["SPY"], "Fiscal Policy", 0.5),

    # ── Geopolitics ─────────────────────────────────────────────
    (r"\b(iran)\b", ["XLE", "XOM", "OXY"], "Geopolitics", -0.3),
    (r"\b(russia|ukraine)\b", ["XLE", "BA", "LMT", "GD"], "Geopolitics", -0.3),
    (r"\b(taiwan)\b", ["TSM", "NVDA", "AMD"], "Geopolitics", -0.5),
    (r"\b(sanction|embargo)\b", ["XOM", "CAT", "BA"], "Geopolitics", -0.3),
    (r"\b(ceasefire|peace deal|peace agreement)\b", ["SPY", "XLE", "BA"], "Geopolitics", 0.4),
    (r"\b(border|immigration)\b", [], "Policy", 0.0),
]


# ═══════════════════════════════════════════════════════════════
# Sentiment → Impact Modifier
# ═══════════════════════════════════════════════════════════════

def sentiment_modifier(sentiment_label: str, polarity: float) -> float:
    """
    Amplify or dampen the base impact score based on post sentiment.
    If sentiment is positive and topic is bullish → stronger positive.
    If sentiment is negative and topic is bullish → dampened or reversed.
    """
    if sentiment_label == "very_positive":
        return 1.5 * (1 + abs(polarity))
    elif sentiment_label == "positive":
        return 1.2 * (1 + abs(polarity) * 0.5)
    elif sentiment_label == "neutral":
        return 1.0
    elif sentiment_label == "negative":
        return 0.6 * (1 - abs(polarity) * 0.3)
    elif sentiment_label == "very_negative":
        return 0.4 * (1 - abs(polarity) * 0.4)
    return 1.0


def compute_impact(post: dict) -> list:
    """
    Compute impact scores for a single post.
    Returns list of dicts: {ticker, sector, score, confidence}
    """
    text = (post.get("text", "") or "") + " " + (post.get("title", "") or "")
    text_lower = text.lower()

    results = []
    matches_found = set()

    for pattern, tickers, sector, base_impact in KEYWORD_MAP:
        if re.search(pattern, text_lower):
            for ticker in tickers:
                if ticker not in matches_found:
                    # Apply sentiment modifier
                    sentiment = post.get("sentiment", {})
                    label = sentiment.get("label", "neutral") if sentiment else "neutral"
                    polarity = sentiment.get("polarity", 0) if sentiment else 0

                    modifier = sentiment_modifier(label, polarity)
                    score = base_impact * modifier

                    # Clamp to [-5, +5]
                    score = max(-5.0, min(5.0, score))

                    # Confidence based on match directness and keyword overlap
                    # Direct ticker mentions get higher confidence
                    confidence = 0.5
                    if ticker.lower() in text_lower:
                        confidence = 0.9
                    elif sector == "Crypto" and ("bitcoin" in text_lower or "crypto" in text_lower):
                        confidence = 0.7
                    elif len(tickers) <= 2 and base_impact != 0:
                        confidence = 0.6

                    matches_found.add(ticker)
                    results.append({
                        "ticker": ticker,
                        "sector": sector,
                        "score": round(score, 3),
                        "base_impact": base_impact,
                        "confidence": round(confidence, 2),
                    })

    return results


# ═══════════════════════════════════════════════════════════════
# Cumulative Impact Calculation
# ═══════════════════════════════════════════════════════════════

def compute_cumulative_impact(all_scores: list, now: datetime) -> dict:
    """
    Compute cumulative impact scores over 24h and 7d windows.
    Returns per-ticker and per-sector aggregates.
    """
    ticker_impact = defaultdict(lambda: {"24h": 0.0, "7d": 0.0, "count_24h": 0, "count_7d": 0, "posts_24h": [], "posts_7d": []})
    sector_impact = defaultdict(lambda: {"24h": 0.0, "7d": 0.0, "count_24h": 0, "count_7d": 0})

    for item in all_scores:
        # Try detected_at first, then timestamp
        timestamp_str = item.get("detected_at", "") or item.get("timestamp", "")
        if not timestamp_str:
            continue

        # Parse timestamp (handle various formats with timezone offsets)
        try:
            ts_str = timestamp_str.replace("T", " ")
            # Detect timezone offset (e.g., +08:00, Z, or none)
            tz_match = re.search(r'([+-]\d{2}:\d{2}|Z)$', ts_str)
            ts_clean = re.sub(r'[+-]\d{2}:\d{2}|Z$', '', ts_str).strip()[:19]
            ts = datetime.strptime(ts_clean, "%Y-%m-%d %H:%M:%S")
            if tz_match:
                tz_str = tz_match.group(1)
                if tz_str == 'Z':
                    ts = ts.replace(tzinfo=timezone.utc)
                else:
                    sign = 1 if tz_str[0] == '+' else -1
                    hours = int(tz_str[1:3])
                    mins = int(tz_str[4:6])
                    offset = timedelta(hours=sign * hours, minutes=sign * mins)
                    ts = ts.replace(tzinfo=timezone(offset))
            else:
                # Assume UTC if no timezone
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, IndexError) as e:
            continue

        age_hours = abs((now - ts).total_seconds()) / 3600

        for impact in item.get("impacts", []):
            ticker = impact["ticker"]
            score = impact["score"]
            sector = impact["sector"]

            if age_hours <= 24:
                ticker_impact[ticker]["24h"] += score
                ticker_impact[ticker]["count_24h"] += 1
                sector_impact[sector]["24h"] += score
                sector_impact[sector]["count_24h"] += 1
                ticker_impact[ticker]["posts_24h"].append({
                    "id": item.get("id", ""),
                    "text": (item.get("text", "") or "")[:150],
                    "score": score,
                })

            if age_hours <= 168:  # 7 days
                ticker_impact[ticker]["7d"] += score
                ticker_impact[ticker]["count_7d"] += 1
                sector_impact[sector]["7d"] += score
                sector_impact[sector]["count_7d"] += 1
                ticker_impact[ticker]["posts_7d"].append({
                    "id": item.get("id", ""),
                    "text": (item.get("text", "") or "")[:100],
                    "score": score,
                })

    # Sort by cumulative impact
    sorted_tickers = sorted(ticker_impact.items(), key=lambda x: x[1]["24h"], reverse=True)
    sorted_sectors = sorted(sector_impact.items(), key=lambda x: x[1]["24h"], reverse=True)

    return {
        "tickers": {
            ticker: data
            for ticker, data in sorted_tickers
            if data["count_24h"] > 0 or data["count_7d"] > 0
        },
        "sectors": {
            sector: {
                "24h": round(data["24h"], 3),
                "7d": round(data["7d"], 3),
                "count_24h": data["count_24h"],
                "count_7d": data["count_7d"],
            }
            for sector, data in sorted_sectors
            if data["count_24h"] > 0 or data["count_7d"] > 0
        },
        "most_impacted_tickers": [
            {"ticker": t, "score_24h": round(d["24h"], 3), "score_7d": round(d["7d"], 3), "count": d["count_24h"]}
            for t, d in sorted_tickers[:10]
        ],
        "most_impacted_sectors": [
            {"sector": s, "score_24h": round(d["24h"], 3), "score_7d": round(d["7d"], 3), "count": d["count_24h"]}
            for s, d in sorted_sectors[:10]
        ],
    }


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("🔴 Trump Impact Score Calculator")
    print(f"   {datetime.now(HKT).strftime('%Y-%m-%d %H:%M')} HKT")
    print("=" * 60)

    # Check for feed file
    if not os.path.exists(FEED_FILE):
        print(f"❌ Feed file not found: {FEED_FILE}")
        print("   Run trump_social_monitor.py first to generate the feed.")
        return

    # Load the feed
    with open(FEED_FILE) as f:
        feed = json.load(f)

    posts = feed.get("posts", [])
    if not posts:
        posts = feed if isinstance(feed, list) else []
    print(f"📰 Loaded {len(posts)} posts from feed")

    now = datetime.now(timezone.utc)
    scored_posts = 0
    all_impact_entries = []

    # Process each post
    for post in posts:
        impacts = compute_impact(post)
        if impacts:
            post["impacts"] = impacts
            scored_posts += 1
            all_impact_entries.append({
                "id": post.get("id", ""),
                "source": post.get("source", ""),
                "text": (post.get("text", "") or "")[:300],
                "timestamp": post.get("timestamp", ""),
                "detected_at": post.get("detected_at", ""),
                "sentiment": post.get("sentiment", {}),
                "impacts": impacts,
            })

    # Compute cumulative impact
    cumulative = compute_cumulative_impact(all_impact_entries, now)

    # Build output
    output = {
        "generated": datetime.now(HKT).isoformat(),
        "feed_timestamp": feed.get("updated_at", ""),
        "total_posts_analyzed": len(all_impact_entries),
        "total_posts_scored": scored_posts,
        "cumulative": cumulative,
    }

    # Write output
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Print summary
    print(f"\n📊 Analysis Summary")
    print(f"   Posts with market impact: {scored_posts}/{len(posts)}")

    print(f"\n🏆 Most Impacted Tickers (24h):")
    for t in cumulative.get("most_impacted_tickers", []):
        direction = "🟢" if t["score_24h"] > 0 else "🔴"
        print(f"   {direction} {t['ticker']:8s} 24h: {t['score_24h']:+6.2f}  7d: {t['score_7d']:+6.2f}  ({t['count']} posts)")

    print(f"\n🌐 Most Impacted Sectors (24h):")
    for s in cumulative.get("most_impacted_sectors", []):
        direction = "🟢" if s["score_24h"] > 0 else "🔴"
        print(f"   {direction} {s['sector']:20s} 24h: {s['score_24h']:+6.2f}  7d: {s['score_7d']:+6.2f}  ({s['count']} posts)")

    # Top bullish/bearish
    sorted_tickers = sorted(cumulative.get("tickers", {}).items(), key=lambda x: x[1]["24h"])
    if sorted_tickers:
        most_bearish = sorted_tickers[:3]
        most_bullish = sorted_tickers[-3:]
        print(f"\n🔴 Most Bearish (24h):")
        for t, d in most_bearish:
            if d["24h"] < 0:
                print(f"   {t}: {d['24h']:+.3f} ({d['count_24h']} posts)")
        print(f"\n🟢 Most Bullish (24h):")
        for t, d in reversed(most_bullish):
            if d["24h"] > 0:
                print(f"   {t}: {d['24h']:+.3f} ({d['count_24h']} posts)")

    print(f"\n💾 Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
