#!/usr/bin/env python3
"""
Greg Value Investing Screener — Global 5 FA Criteria
====================================================
Fetches S&P 500 + HSI constituents, applies FA filters,
outputs screened results as JSON for Stock Dashboard.

Run: python3 screener.py
Output: screener_data.json
"""

import json, os, sys, time, warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf

warnings.filterwarnings("ignore")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ═══════════════════════════════════════════════
# Global 5 FA Criteria (from FA1, FA2, M1 notes)
# ═══════════════════════════════════════════════
CRITERIA = {
    "roe_min": 12.0,           # FA1: ROE ≥ 12% — 優質企業底線
    "gross_margin_min": 30.0,  # FA1: Gross Margin (護城河 indicator, 40% ideal, 30% min)
    "eps_growth_min": 5.0,     # FA1/FA2: EPS 穩定增長 (>5%)
    "rev_growth_min": 5.0,     # FA1: Revenue Growth
    "fcf_yield_min": 0.0,      # FA3: FCF positive
    "debt_equity_max": 1.5,    # FA2: moderate leverage
    "peg_max": 2.5,            # FA2/M1: reasonable valuation
    "beta_min": 0.3,           # optional reference
}

# ═══════════════════════════════════════════════
# Stock Universes
# ═══════════════════════════════════════════════
SP500_TICKERS = [
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","BRK-B","AVGO","JPM",
    "LLY","V","UNH","XOM","WMT","MA","JNJ","PG","HD","ORCL",
    "COST","ABBV","BAC","CRM","CVX","NFLX","MRK","AMD","KO","PEP",
    "ADBE","TMO","CSCO","LIN","ACN","MCD","ABT","DIS","WFC","CAT",
    "TXN","QCOM","IBM","VZ","DHR","PM","GE","INTU","NOW","AMGN",
    "UBER","PFE","RTX","ISRG","GS","SPGI","AXP","MS","SYK","PLTR",
    "BLK","NEE","LOW","T","HON","CMCSA","BSX","ETN","BKNG","SCHW",
    "PGR","UNP","DE","TJX","MU","ELV","FI","ADP","LMT","ANET",
    "AMT","COP","PANW","C","ADI","MMC","KLAC","SO","VRTX","BA",
    "CB","MDT","GILD","LRCX","TMUS","CI","REGN","NKE","UPS","INTC",
    "DUK","ICE","EQIX","WM","CME","TT","ZTS","MO","PH","AON",
    "AMAT","WELL","SHW","SNPS","MCK","MMM","TDG","CDNS","MCO","APH",
    "MSI","BDX","NOC","ITW","HCA","PNC","USB","ORLY","CVS","CSX",
    "EMR","APD","AFL","GD","CEG","ROP","FDX","MAR","CL","ECL",
    "FCX","AJG","AIG","CRWD","GM","D","CTAS","TFC","TRV","WMB",
    "OXY","CARR","AZO","OKE","PCAR","NSC","SRE","LNG","AEP","SPG",
    "HLT","BK","MPC","PSX","ADSK","RSG","FTNT","TGT","KMI","DLR",
    "DHI","ALL","CMG","JCI","HES","MCHP","FICO","NUE","GWW","URI",
    "BKR","HWM","AME","EW","DFS","VLO","PSA","AXON","PAYX","ROST",
    "LEN","PRU","FIS","AMP","MSCI","KDP","VST","MET","PWR","IQV",
    "F","MNST","VRSK","LHX","FAST","HUM","CPRT","CCI","ODFL","SYY",
    "CTSH","DDOG","IDXX","YUM","PCG","ACGL","KR","A","PEG","GIS",
    "TTD","OTIS","EXC","GEHC","COR","IT","HIG","CNC","NDAQ","XEL",
    "CTVA","AME","VMC","BIIB","STZ","EA","EFX","DD","GRMN","ED",
    "IR","XYL","WAB","DVN","MLM","DELL","KEYS","HSY","CSGP","EBAY",
    "WTW","ANSS","CHTR","RCL","HPE","CAH","EIX","ETR","LYB","WEC",
    "TSCO","FITB","AWK","VICI","HPQ","DOW","RJF","WBD","FE","WY",
    "STE","MTD","HAL","AEE","STT","PPG","ES","CDW","BRO","DOV",
    "VLTO","HUBB","BR","TROW","DTE","FSLR","PPL","HPE","SBAC","ZBH",
    "ERIE","ATO","CHD","FCNCA","EL","TYL","WST","GPN","BALL","CNP",
    "LUV","MKC","K","CLX","AVB","CMS","MAA","EXPD","CPT","SYF",
    "BAX","ESS","NVR","WDC","OMC","INVH","ZBRA","KEY","TER","MOH",
    "PFG","APTV","J","TDY","EQT","SNA","DGX","LNT","DG","EXPE",
    "NI","PKG","IP","UAL","ARE","BBY","CTRA","COO","GEN","ULTA",
    "NRG","CCL","JBL","WRB","CE","GPC","BLDR","SJM","AMCR","DAL",
    "UDR","LDOS","MAS","TXT","FDS","STLD","MGM","ENPH","VTR","EQR",
    "CF","TRMB","HOLX","CAG","KMX","TSN","EG","IEX","SWK","KIM",
    "VRSN","LVS","DOC","DPZ","PNR","RVTY","PODD","NDSN","TFX","FFIV",
    "AKAM","SEDG","ALGN","EMN","JKHY","ALLE","BXP","RHI","SWKS",
    "HST","NCLH","AOS","QRVO","CHRW","CPB","NWSA","HAS","WYNN",
    "IVZ","LW","BIO","FMC","MHK","PARA","CZR","GNRC","BEN","TPR",
    "AAL","LNC","ETSY","MTCH","APA","RL","REG","PNW","FRT","AIZ",
    "HRL","FOXA","DVA","BBWI","NWS","GL",
]

HSI_TICKERS = [
    "0005.HK","0011.HK","0016.HK","0027.HK","0066.HK","0083.HK","0101.HK",
    "0175.HK","0267.HK","0288.HK","0386.HK","0688.HK","0700.HK","0762.HK",
    "0823.HK","0857.HK","0883.HK","0939.HK","0941.HK","0960.HK","0968.HK",
    "0981.HK","0992.HK","1038.HK","1044.HK","1093.HK","1109.HK","1113.HK",
    "1177.HK","1211.HK","1299.HK","1378.HK","1398.HK","1755.HK","1810.HK",
    "1876.HK","1928.HK","1997.HK","2007.HK","2015.HK","2020.HK","2269.HK",
    "2313.HK","2318.HK","2319.HK","2331.HK","2382.HK","2388.HK","2688.HK",
    "2828.HK","2899.HK","3690.HK","3968.HK","3988.HK","6098.HK","6618.HK",
    "6690.HK","6862.HK","9618.HK","9633.HK","9888.HK","9922.HK","9988.HK",
    "9999.HK",
]

# ═══════════════════════════════════════════════
# Data Fetching
# ═══════════════════════════════════════════════

def fetch_stock(ticker):
    """Fetch FA data for one stock via yfinance."""
    try:
        s = yf.Ticker(ticker)
        info = s.info
        if not info or info.get("regularMarketPrice") is None:
            return None

        roe = info.get("returnOnEquity")
        if roe is not None:
            if roe < 10:  # decimal (0.34 → 34%)
                roe = roe * 100
            # else: already percentage (e.g., 141 for 141%)

        gross_margin = info.get("grossMargins")
        if gross_margin is not None and gross_margin < 1:
            gross_margin = gross_margin * 100

        # Growth metrics
        rev_growth = info.get("revenueGrowth")
        if rev_growth is not None and abs(rev_growth) < 1:
            rev_growth = rev_growth * 100

        eps_growth = info.get("earningsGrowth")
        if eps_growth is not None and abs(eps_growth) < 1:
            eps_growth = eps_growth * 100

        debt_equity_raw = info.get("debtToEquity")
        debt_equity = debt_equity_raw / 100.0 if debt_equity_raw else None  # yfinance returns %, convert to ratio

        market_cap = info.get("marketCap", 0)
        fcf = info.get("freeCashflow", 0)
        fcf_yield = (fcf / market_cap * 100) if market_cap and fcf else None

        return {
            "ticker": ticker,
            "name": info.get("shortName") or info.get("longName", ""),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "market": "HK" if ".HK" in ticker else "US",
            "price": info.get("regularMarketPrice"),
            "market_cap": market_cap,
            "roe": roe,
            "gross_margin": gross_margin,
            "rev_growth": rev_growth,
            "eps_growth": eps_growth,
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg": info.get("pegRatio"),
            "debt_equity": debt_equity,
            "beta": info.get("beta"),
            "dividend_yield": info.get("dividendYield"),
        }

    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def apply_criteria(stock):
    """Apply Global 5 FA criteria. Returns (passed, rank_score, fail_reasons)."""
    fail = []
    score = 0
    total_checks = 0

    # 1. ROE ≥ 12%
    if stock.get("roe") is not None:
        total_checks += 1
        if stock["roe"] >= CRITERIA["roe_min"]:
            score += min(stock["roe"] / CRITERIA["roe_min"], 3)  # cap at 3x
        else:
            fail.append(f"ROE {stock['roe']:.1f}% < {CRITERIA['roe_min']}%")

    # 2. Gross Margin > 30%
    if stock.get("gross_margin") is not None:
        total_checks += 1
        if stock["gross_margin"] >= CRITERIA["gross_margin_min"]:
            score += min(stock["gross_margin"] / CRITERIA["gross_margin_min"], 3)
        else:
            fail.append(f"GP% {stock['gross_margin']:.1f}% < {CRITERIA['gross_margin_min']}%")

    # 3. Revenue Growth > 5%
    if stock.get("rev_growth") is not None:
        total_checks += 1
        if stock["rev_growth"] >= CRITERIA["rev_growth_min"]:
            score += min(stock["rev_growth"] / CRITERIA["rev_growth_min"], 4)
        else:
            fail.append(f"RevG {stock['rev_growth']:.1f}%")

    # 4. EPS Growth > 5%
    if stock.get("eps_growth") is not None:
        total_checks += 1
        if stock["eps_growth"] >= CRITERIA["eps_growth_min"]:
            score += min(stock["eps_growth"] / CRITERIA["eps_growth_min"], 4)
        else:
            fail.append(f"EPSG {stock['eps_growth']:.1f}%")

    # 5. FCF Yield > 0
    if stock.get("fcf_yield") is not None:
        total_checks += 1
        if stock["fcf_yield"] > 0:
            score += 1
        else:
            fail.append(f"FCF Yield {stock['fcf_yield']:.1f}%")

    # 6. Debt/Equity < 1.5
    if stock.get("debt_equity") is not None:
        total_checks += 1
        de = stock["debt_equity"]
        if de <= CRITERIA["debt_equity_max"]:
            score += max(0, 2 - de)  # lower D/E = higher score
        else:
            fail.append(f"D/E {de:.1f} > {CRITERIA['debt_equity_max']}")

    # 7. PEG < 2.5
    if stock.get("peg") is not None and stock["peg"] > 0:
        total_checks += 1
        if stock["peg"] <= CRITERIA["peg_max"]:
            score += max(0, CRITERIA["peg_max"] / max(stock["peg"], 0.1))
        else:
            fail.append(f"PEG {stock['peg']:.1f}")

    # Determine if passed: need at least 4 passing checks AND no critical fails (ROE + GP%)
    critical_fail = any("ROE" in f or "GP%" in f for f in fail)
    passed = len(fail) <= 2 and not critical_fail and total_checks >= 5

    return {
        "passed": passed,
        "score": round(score, 1),
        "fails": fail,
        "checks": total_checks,
    }


def format_value(val, decimals=1):
    """Format numeric values for display."""
    if val is None:
        return "N/A"
    if isinstance(val, float):
        if abs(val) >= 1e9:
            return f"${val/1e9:.1f}B"
        if abs(val) >= 1e6:
            return f"${val/1e6:.1f}M"
        return f"{val:.{decimals}f}"
    return str(val)


def main():
    print("🔍 Greg Value Investing Screener")
    print("=" * 50)

    all_tickers = SP500_TICKERS + HSI_TICKERS
    print(f"\n📊 Scanning {len(SP500_TICKERS)} US + {len(HSI_TICKERS)} HK stocks...")

    results = {"us": [], "hk": [], "failed": [], "empty": []}
    stocks = []
    processed = 0

    start = time.time()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_stock, t): t for t in all_tickers}

        for future in as_completed(futures):
            ticker = futures[future]
            processed += 1
            try:
                data = future.result(timeout=15)
                if data is None:
                    results["empty"].append(ticker)
                elif "error" in data:
                    results["failed"].append(ticker)
                else:
                    # Apply criteria
                    verdict = apply_criteria(data)
                    data.update(verdict)
                    stocks.append(data)

                    # Progress
                    if processed % 50 == 0:
                        elapsed = time.time() - start
                        print(f"  ... {processed}/{len(all_tickers)} stocks "
                              f"({processed/elapsed:.1f}/s) | {len(stocks)} candidates")

            except Exception:
                results["failed"].append(ticker)

    # Sort by score descending
    stocks.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Split by market
    for s in stocks:
        if s["market"] == "HK":
            results["hk"].append(s)
        else:
            results["us"].append(s)

    elapsed = time.time() - start

    # Print summary
    us_passed = sum(1 for s in results["us"] if s["passed"])
    hk_passed = sum(1 for s in results["hk"] if s["passed"])

    print(f"\n{'='*50}")
    print(f"✅ DONE in {elapsed:.1f}s")
    print(f"   US: {us_passed}/{len(results['us'])} passed | "
          f"HK: {hk_passed}/{len(results['hk'])} passed")
    print(f"   Empty: {len(results['empty'])} | Failed: {len(results['failed'])}")

    # ── Top US ──
    top_us = [s for s in results["us"] if s["passed"]][:20]
    if top_us:
        print(f"\n🏆 TOP US PASSED:")
        for s in top_us:
            fails_str = ", ".join(s.get("fails", [])) or "ALL PASS ✅"
            print(f"  {s['ticker']:8s} Score={s['score']:.1f} | "
                  f"ROE={s['roe']:.1f}% GP={s['gross_margin']:.1f}% "
                  f"PEG={s['peg']:.1f} | Fails: {fails_str}")

    # ── Top HK ──
    top_hk = [s for s in results["hk"] if s["passed"]][:10]
    if top_hk:
        print(f"\n🏆 TOP HK PASSED:")
        for s in top_hk:
            fails_str = ", ".join(s.get("fails", [])) or "ALL PASS ✅"
            print(f"  {s['ticker']:8s} Score={s['score']:.1f} | "
                  f"ROE={s['roe']:.1f}% GP={s['gross_margin']:.1f}% "
                  f"PEG={s['peg']:.1f} | Fails: {fails_str}")

    # Save to JSON
    output_path = os.path.join(SCRIPT_DIR, "screener_data.json")
    output = {
        "generated": time.strftime("%Y-%m-%d %H:%M HKT"),
        "criteria": CRITERIA,
        "summary": {
            "total_scanned": len(all_tickers),
            "us_passed": us_passed,
            "hk_passed": hk_passed,
            "total_stocks_with_data": len(stocks),
        },
        "us": results["us"],
        "hk": results["hk"],
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n💾 Saved to: {output_path}")
    print(f"   File size: {os.path.getsize(output_path)/1024:.1f} KB")


if __name__ == "__main__":
    main()
