#!/usr/bin/env python3
"""
Sector Rotation Tracker
=======================
Tracks capital flow between sectors by analyzing relative performance
and volume-weighted price action across sector ETFs.

Calculations:
  - 1w / 1m / 3m relative performance vs SPY
  - Capital flow proxy: volume × price change (accumulation/distribution)
  - Sector ranking by momentum

Reads:  (live data via yfinance)
Writes: data/sector_rotation.json

Usage:
  python3 sector_rotation.py
"""

import json
import os
import math
import warnings
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import yfinance as yf

warnings.filterwarnings("ignore")

HKT = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "sector_rotation.json")

# ═══════════════════════════════════════════════════════════════
# Sector ETF Configuration
# ═══════════════════════════════════════════════════════════════

SECTOR_ETFS = {
    "XLK": {"name": "Technology", "color": "#4fc3f7"},
    "XLF": {"name": "Financials", "color": "#ffc312"},
    "XLE": {"name": "Energy", "color": "#ff6b6b"},
    "XLV": {"name": "Healthcare", "color": "#fd79a8"},
    "XLY": {"name": "Consumer Discretionary", "color": "#a29bfe"},
    "XLP": {"name": "Consumer Staples", "color": "#00b894"},
    "XLI": {"name": "Industrials", "color": "#e17055"},
    "XLRE": {"name": "Real Estate", "color": "#0984e3"},
    "XLU": {"name": "Utilities", "color": "#636e72"},
    "XLB": {"name": "Materials", "color": "#76b900"},
}

BENCHMARK = "SPY"

# Additional broad-market ETFs for context
MACRO_ETFS = {
    "QQQ": "NASDAQ 100 (Tech-heavy)",
    "IWM": "Russell 2000 (Small Cap)",
    "DIA": "Dow 30 (Blue Chips)",
    "TLT": "20+ Year Treasury (Rates)",
    "GLD": "Gold (Safe Haven)",
}


# ═══════════════════════════════════════════════════════════════
# Data Fetching
# ═══════════════════════════════════════════════════════════════

def fetch_sector_data() -> dict:
    """
    Fetch price and volume data for all sector ETFs + benchmark.
    Returns {ticker: {prices, volumes, ...}}
    """
    tickers = list(SECTOR_ETFS.keys()) + [BENCHMARK] + list(MACRO_ETFS.keys())
    all_data = {}

    try:
        # Download 6 months of daily data
        data = yf.download(
            tickers,
            period="6mo",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )

        if data.empty:
            print("  ⚠️ No data received from yfinance")
            return all_data

        for ticker in tickers:
            try:
                if ticker in data.columns.levels[0]:
                    ticker_data = data[ticker]
                else:
                    # Single-level index fallback
                    if "Close" in data.columns:
                        # Single ticker download
                        ticker_data = data
                    else:
                        continue

                all_data[ticker] = {
                    "close": ticker_data["Close"].dropna(),
                    "volume": ticker_data["Volume"].dropna(),
                    "high": ticker_data["High"].dropna(),
                    "low": ticker_data["Low"].dropna(),
                    "open": ticker_data["Open"].dropna(),
                }
            except (KeyError, Exception) as e:
                print(f"  ⚠️ Could not process data for {ticker}: {e}")
                continue

    except Exception as e:
        print(f"  ❌ yfinance error: {e}")

    return all_data


# ═══════════════════════════════════════════════════════════════
# Performance Calculations
# ═══════════════════════════════════════════════════════════════

def compute_returns(prices, periods={"1w": 5, "1m": 21, "3m": 63}):
    """Compute returns over specified periods (in trading days)."""
    results = {}
    for label, days in periods.items():
        if len(prices) > days:
            current = prices.iloc[-1]
            past = prices.iloc[-days - 1]
            results[label] = (current / past - 1) * 100
        else:
            results[label] = None
    return results


def compute_capital_flow(prices, volumes, window=21):
    """
    Capital flow proxy: volume × price change.
    Positive = accumulation (money flowing in)
    Negative = distribution (money flowing out)
    Returns recent 21d cumulative and 63d cumulative.
    """
    if len(prices) < 2 or len(volumes) < 2:
        return {"21d": 0, "63d": 0}

    # Align series
    aligned = prices.align(volumes, join="inner")
    prices_a, volumes_a = aligned

    if len(prices_a) < 2:
        return {"21d": 0, "63d": 0}

    # Daily price change
    pct_changes = prices_a.pct_change().dropna()
    # Volume-weighted money flow
    daily_flow = pct_changes * volumes_a.loc[pct_changes.index]

    # Cumulative over windows
    cum_21d = daily_flow.iloc[-21:].sum() if len(daily_flow) >= 21 else daily_flow.sum()
    cum_63d = daily_flow.iloc[-63:].sum() if len(daily_flow) >= 63 else daily_flow.sum()

    return {
        "21d": round(float(cum_21d), 2),
        "63d": round(float(cum_63d), 2),
    }


def compute_relative_strength(sector_returns, spy_returns):
    """
    Relative strength vs SPY: sector_return - SPY_return for each period.
    Positive = outperforming.
    """
    rel = {}
    for period in ["1w", "1m", "3m"]:
        s = sector_returns.get(period)
        b = spy_returns.get(period)
        if s is not None and b is not None:
            rel[period] = round(s - b, 2)
        else:
            rel[period] = None
    return rel


def compute_momentum_score(returns_dict, relative_dict):
    """
    Composite momentum score: weighted combination of relative returns.
    Higher weight on shorter timeframes for trading signal.
    Weights: 1w=0.5, 1m=0.3, 3m=0.2
    """
    score = 0.0
    weights = {"1w": 0.5, "1m": 0.3, "3m": 0.2}
    total_weight = 0.0

    for period, weight in weights.items():
        r = relative_dict.get(period)
        if r is not None:
            # Normalize: score scales with relative performance
            score += r * weight
            total_weight += weight

    if total_weight > 0:
        score = score / total_weight

    return round(score, 2)


def compute_sector_rotation(all_data: dict) -> dict:
    """
    Main computation: for each sector ETF, compute performance,
    capital flow, and relative strength vs SPY.
    """
    spy_data = all_data.get(BENCHMARK)
    if not spy_data:
        return {"sectors": {}, "error": "No SPY benchmark data"}

    spy_returns = compute_returns(spy_data["close"])
    spy_flow = compute_capital_flow(spy_data["close"], spy_data["volume"])

    sectors = {}
    for ticker, info in SECTOR_ETFS.items():
        etf_data = all_data.get(ticker)
        if not etf_data:
            continue

        returns = compute_returns(etf_data["close"])
        flow = compute_capital_flow(etf_data["close"], etf_data["volume"])
        relative = compute_relative_strength(returns, spy_returns)
        momentum = compute_momentum_score(returns, relative)

        # Current price and YTD (approximate from 6mo data)
        price = float(etf_data["close"].iloc[-1]) if len(etf_data["close"]) > 0 else 0
        price_3m_ago = float(etf_data["close"].iloc[0]) if len(etf_data["close"]) > 0 else 0
        ytd = round((price / price_3m_ago - 1) * 100, 2) if price_3m_ago > 0 else None

        sectors[ticker] = {
            "name": info["name"],
            "color": info["color"],
            "price": round(price, 2),
            "returns": returns,
            "relative_vs_spy": relative,
            "capital_flow": flow,
            "momentum_score": momentum,
            "ytd_return": ytd,
        }

    # Also analyze macro ETFs
    macro = {}
    for ticker, name in MACRO_ETFS.items():
        data = all_data.get(ticker)
        if not data:
            continue

        returns = compute_returns(data["close"])
        flow = compute_capital_flow(data["close"], data["volume"])

        # Relative against SPY for comparison
        relative = compute_relative_strength(returns, spy_returns)

        price = float(data["close"].iloc[-1]) if len(data["close"]) > 0 else 0
        macro[ticker] = {
            "name": name,
            "price": round(price, 2),
            "returns": returns,
            "relative_vs_spy": relative,
            "capital_flow": flow,
        }

    # Rank sectors by momentum
    ranked = sorted(sectors.items(), key=lambda x: x[1]["momentum_score"], reverse=True)

    # Rotation signal: which sectors are leaders / laggards
    top_3 = ranked[:3] if len(ranked) >= 3 else ranked
    bottom_3 = ranked[-3:] if len(ranked) >= 3 else ranked
    mid = ranked[3:-3] if len(ranked) > 6 else []

    rotation_signal = {
        "top_sectors": [
            {
                "ticker": t,
                "name": d["name"],
                "momentum": d["momentum_score"],
                "return_1w": d["returns"].get("1w"),
                "return_1m": d["returns"].get("1m"),
                "return_3m": d["returns"].get("3m"),
                "flow_21d": d["capital_flow"]["21d"],
            }
            for t, d in top_3
        ],
        "bottom_sectors": [
            {
                "ticker": t,
                "name": d["name"],
                "momentum": d["momentum_score"],
                "return_1w": d["returns"].get("1w"),
                "return_1m": d["returns"].get("1m"),
                "return_3m": d["returns"].get("3m"),
                "flow_21d": d["capital_flow"]["21d"],
            }
            for t, d in reversed(bottom_3)
        ],
        "spread_top_bottom": round(top_3[0][1]["momentum_score"] - bottom_3[0][1]["momentum_score"], 2)
        if top_3 and bottom_3 else None,
    }

    # Rotation direction assessment
    rotation_direction = assess_rotation(sectors, macro)

    return {
        "sectors": sectors,
        "macro": macro,
        "ranked": [{"ticker": t, "name": d["name"], "momentum": d["momentum_score"]} for t, d in ranked],
        "benchmark": {
            "ticker": BENCHMARK,
            "returns": spy_returns,
            "capital_flow": spy_flow,
        },
        "rotation_signal": rotation_signal,
        "rotation_direction": rotation_direction,
    }


def assess_rotation(sectors: dict, macro: dict) -> dict:
    """
    Assess broad rotation direction based on sector performance patterns.

    Risk-on = Tech, Consumer Disc, Financials outperform
    Risk-off = Utilities, Consumer Staples, Healthcare outperform
    Cyclical = Energy, Industrials, Materials lead
    """
    risk_on_tickers = ["XLK", "XLY", "XLF"]
    risk_off_tickers = ["XLU", "XLP", "XLV"]
    cyclical_tickers = ["XLE", "XLI", "XLB"]

    def avg_momentum(tickers_list):
        scores = []
        for t in tickers_list:
            if t in sectors:
                s = sectors[t].get("momentum_score")
                if s is not None:
                    scores.append(s)
        return sum(scores) / len(scores) if scores else 0

    risk_on = avg_momentum(risk_on_tickers)
    risk_off = avg_momentum(risk_off_tickers)
    cyclical = avg_momentum(cyclical_tickers)

    # Determine regime
    if risk_on > risk_off and risk_on > cyclical:
        regime = "Risk-On (Growth)"
        description = "Growth/tech leading → bullish risk appetite"
    elif cyclical > risk_off and cyclical > risk_on:
        regime = "Cyclical (Recovery)"
        description = "Cyclicals leading → economic expansion/reflation"
    elif risk_off > risk_on and risk_off > cyclical:
        regime = "Risk-Off (Defensive)"
        description = "Defensives leading → caution / market hedging"
    else:
        regime = "Mixed"
        description = "No clear rotation signal"

    return {
        "regime": regime,
        "description": description,
        "risk_on_momentum": round(risk_on, 2),
        "risk_off_momentum": round(risk_off, 2),
        "cyclical_momentum": round(cyclical, 2),
    }


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("🔄 Sector Rotation Tracker")
    print(f"   {datetime.now(HKT).strftime('%Y-%m-%d %H:%M')} HKT")
    print(f"   Tracking {len(SECTOR_ETFS)} sector ETFs + {len(MACRO_ETFS)} macro ETFs")
    print("=" * 60)

    print("\n📡 Fetching market data...")
    all_data = fetch_sector_data()

    if not all_data:
        print("❌ No data fetched. Check internet connection.")
        return

    print(f"   Data received for {len(all_data)} tickers")

    # Compute rotation
    print("\n📊 Computing sector rotation...")
    result = compute_sector_rotation(all_data)

    # Save
    output = {
        "generated": datetime.now(HKT).isoformat(),
        "data_timestamp": datetime.now(HKT).strftime("%Y-%m-%d %H:%M"),
        **result,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str, ensure_ascii=False)

    # Print summary
    rd = result.get("rotation_direction", {})
    print(f"\n🔄 Rotation Regime: {rd.get('regime', 'N/A')}")
    print(f"   {rd.get('description', '')}")
    print(f"   Risk-On: {rd.get('risk_on_momentum', 'N/A')} | "
          f"Risk-Off: {rd.get('risk_off_momentum', 'N/A')} | "
          f"Cyclical: {rd.get('cyclical_momentum', 'N/A')}")

    rs = result.get("rotation_signal", {})
    print(f"\n🏆 Top 3 Sectors:")
    for s in rs.get("top_sectors", []):
        direction = "🟢" if s["momentum"] > 0 else ""
        print(f"   {direction} {s['name']:22s} Momentum: {s['momentum']:+6.2f} | "
              f"1w: {s.get('return_1w', 'N/A')}% | Flow: {s.get('flow_21d', 'N/A')}")

    print(f"\n🔻 Bottom 3 Sectors:")
    for s in rs.get("bottom_sectors", []):
        direction = "🔴" if s["momentum"] < 0 else ""
        print(f"   {direction} {s['name']:22s} Momentum: {s['momentum']:+6.2f} | "
              f"1w: {s.get('return_1w', 'N/A')}% | Flow: {s.get('flow_21d', 'N/A')}")

    # Sector performance table
    print(f"\n📈 Sector Performance vs SPY:")
    ranked = result.get("ranked", [])
    for r in ranked:
        ticker = r["ticker"]
        sec = result.get("sectors", {}).get(ticker, {})
        perf_1w = sec.get("returns", {}).get("1w", "N/A")
        perf_1m = sec.get("returns", {}).get("1m", "N/A")
        rel = sec.get("relative_vs_spy", {}).get("1m", "N/A")
        print(f"   {r['name']:25s} Mom: {r['momentum']:+6.2f} | "
              f"1w: {perf_1w if perf_1w else 'N/A'} | "
              f"1m: {perf_1m if perf_1m else 'N/A'} | "
              f"Rel: {rel if rel else 'N/A'}")

    # Benchmark
    bench = result.get("benchmark", {})
    spy_ret = bench.get("returns", {})
    print(f"\n📊 SPY Benchmark: 1w: {spy_ret.get('1w', 'N/A')}% | "
          f"1m: {spy_ret.get('1m', 'N/A')}% | 3m: {spy_ret.get('3m', 'N/A')}%")

    # Macro context
    macro = result.get("macro", {})
    if macro:
        print(f"\n🌐 Macro Context:")
        for ticker, data in macro.items():
            ret_1m = data.get("returns", {}).get("1m", "N/A")
            print(f"   {ticker:6s} ({data['name']:30s}) 1m: {ret_1m if ret_1m else 'N/A'}%")

    print(f"\n💾 Saved to: {OUTPUT_FILE}")
    print(f"   Size: {os.path.getsize(OUTPUT_FILE) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
