#!/usr/bin/env python3
"""
13F Updater — Fetches latest SEC 13F filings and updates stock-dashboard index.html.
"""
import requests
import re
import os
import sys
import json
import urllib.request
import urllib.error
from collections import OrderedDict, defaultdict

UA = 'CadmusYiu/1.0 (cadmusyiu@gmail.com)'
HEADERS = {'User-Agent': UA}

# ─── Known manager → CIK + latest accession mapping ───
MANAGERS = OrderedDict([
    ('buffett', {
        'label': 'Buffett (Berkshire Hathaway)',
        'meta': 'Value · Insurance · Consumer',
        'tags': ['Value', 'Insurance', 'Consumer'],
        'cik': '0001067983',
        'acc': '0001193125-26-226661',
    }),
    ('ackman', {
        'label': 'Ackman (Pershing Square)',
        'meta': 'Activist · Concentrated',
        'tags': ['Activist', 'Financials', 'Tech'],
        'cik': '0001336528',
        'acc': '0001172661-26-002336',
    }),
    ('drucken', {
        'label': 'Druckenmiller (Duquesne)',
        'meta': 'Macro · Biotech · Cyclicals',
        'tags': ['Macro', 'Biotech', 'Cyclical'],
        'cik': '0001536411',
        'acc': '0001536411-26-000004',
    }),
    ('salp', {
        'label': 'SALP (Situational Awareness LP)',
        'meta': 'AI Infra · Power · Crypto',
        'tags': ['AI Infra', 'Power', 'Semicon'],
        'cik': '0002045724',
        'acc': '0002045724-26-000008',
    }),
    ('burry', {
        'label': 'Burry (Scion Asset Mgmt)',
        'meta': 'Contrarian · Value',
        'tags': ['Value', 'Healthcare', 'Contrarian'],
        'cik': '0001649339',
        'acc': '0001649339-25-000007',  # latest available
    }),
    ('cathie', {
        'label': 'Cathie Wood (ARK Invest)',
        'meta': 'Innovation · Disruptive Tech',
        'tags': ['Disruptive', 'Genomics', 'Fintech'],
        'cik': '0001575705',
        'acc': None,
    }),
])

# ⚠️ Trump has NO 13F — keep as speculative watchlist

TRUMP_HOLDINGS = {
    'tk': ['DJT', 'MSTR', 'TSLA'],
    'wt': {'DJT': 40, 'MSTR': 10, 'TSLA': 5},
}

# ─── Cathie — ARK funds have no single 13F CIK (uses trusts)
# Instead, pull from ARK's daily disclosure API
CATHIE_HOLDINGS = {
    'label': 'Cathie Wood (ARK Innovation)',
    'meta': 'ARKK Daily Holdings',
    'tags': ['Innovation', 'Genomics', 'Fintech'],
    # Pulled from arkfunds.io API, updated fresh each run
}

# ─── Company name → ticker mapping (known from 13F filings) ───
# These need to cover the actual names found in the filings
NAME_TO_TICKER = {
    'APPLE INC': 'AAPL',
    'AMERICAN EXPRESS CO': 'AXP',
    'COCA COLA CO': 'KO',
    'BANK AMERICA CORP': 'BAC',
    'CHEVRON CORPORATION': 'CVX',
    'OCCIDENTAL PETE CORP': 'OXY',
    'ALPHABET INC': 'GOOGL',
    'CHUBB LTD SWITZ': 'CB',
    'MOODYS CORP': 'MCO',
    'KRAFT HEINZ CO': 'KHC',
    'DAVITA INC': 'DVA',
    'KROGER CO': 'KR',
    'SIRIUSXM HOLDINGS INC': 'SIRI',
    'DELTA AIR LINES INC': 'DAL',
    'VERISIGN INC': 'VRSN',
    'NVIDIA CORPORATION': 'NVDA',
    'BROADCOM INC': 'AVGO',
    'ORACLE CORP': 'ORCL',
    'ADVANCED MICRO DEVICES INC': 'AMD',
    'MICRON TECHNOLOGY INC': 'MU',
    'INTEL CORP': 'INTC',
    'ASML HLDG NV N Y REGISTRY': 'ASML',
    'TAIWAN SEMICONDUCTOR MANUFAC': 'TSM',
    'SANDISK CORP': 'SNDK',
    'BLOOM ENERGY CORP': 'BE',
    'COREWEAVE INC': 'CRWV',
    'IREN LIMITED': 'IREN',
    'CORE SCIENTIFIC INC NEW': 'CORZ',
    'APPLIED DIGITAL CORP': 'APLD',
    'RIOT PLATFORMS INC': 'RIOT',
    'CLEANSPARK INC': 'CLSK',
    'SOLARIS ENERGY INFRAS INC': 'SEI',
    'T1 ENERGY INC': 'T1',
    'BITFARMS LTD': 'BITF',
    'BITDEER TECHNOLOGIES GROUP': 'BTDR',
    'POWER SOLUTIONS INTL INC': 'PSIX',
    'WHITEFIBER INC': 'WHT',
    'BABCOCK &amp; WILCOX ENTERPRISES': 'BW',
    'SHARONAI HOLDINGS INC': 'SHAI',
    'PROPETRO HLDG CORP': 'PUMP',
    'HIVE DIGITAL TECHNOLOGIES LT': 'HIVE',
    'CORNING INC': 'GLW',
    'VANECK ETF TRUST': 'SMH',
    'BROOKFIELD CORP': 'BN',
    'AMAZON COM INC': 'AMZN',
    'UBER TECHNOLOGIES INC': 'UBER',
    'MICROSOFT CORP': 'MSFT',
    'RESTAURANT BRANDS INTL INC': 'QSR',
    'META PLATFORMS INC': 'META',
    'HOWARD HUGHES HOLDINGS INC': 'HHC',
    'SEAPORT ENTMT GROUP INC': 'SPG',
    'HERTZ GLOBAL HLDGS INC': 'HTZ',
    'PFIZER INC': 'PFE',
    'HALLIBURTON CO': 'HAL',
    'MOLINA HEALTHCARE INC': 'MOH',
    'LULULEMON ATHLETICA INC': 'LULU',
    'SLM CORP': 'SLM',
    'BRUKER CORP': 'BRKR',
    'NATERA INC': 'NTRA',
    'IHSIARES INC': 'IWM',
    'ISHARES INC': 'IWM',
    'ISHARES TR': 'IWM',
    'INSMED INC': 'INSM',
    'YPF SOCIEDAD ANONIMA': 'YPF',
    'BBB FOODS INC': 'TBBB',
    'ALCOA CORP': 'AA',
    'NEWAMSTERDAM PHARMA COMPANY': 'NAMS',
    'SEA LTD': 'SE',
    'STMICROELECTRONICS N V': 'STM',
    'WOODWARD INC': 'WWD',
    'TEVA PHARMACEUTICAL INDS LTD': 'TEVA',
    'ROKU INC': 'ROKU',
    'STATE STR SPDR S&amp;P 500 ETF T': 'SPY',
    'INVESCO EXCHANGE TRADED FD T': 'QQQ',
    'INVESCO EXCH TRADED FD TR': 'QQQ',
    'INVESCO QQQ TRUST': 'QQQ',
    'INVESCO ETF TRUST': 'QQQ',
    'COUPANG INC': 'CPNG',
    'OPTION CARE HEALTH INC': 'OPCH',
    'CRH PLC': 'CRH',
    'FIGURE TECHNOLOGY SOLUTIO': 'FIG',
    'GLOBAL X FDS': 'ARKQ',
    'CARIS LIFE SCIENCES INC': 'CRIS',
    'REVOLUTION MEDICINES INC': 'RVMD',
    'PALANTIR TECHNOLOGIES INC': 'PLTR',
}

# Additional tickers for the exch map
EXTRA_TICKERS = [
    'CB', 'KHC', 'DVA', 'KR', 'SIRI', 'DAL', 'AVGO', 'ORCL', 'AMD', 'MU', 'INTC', 'ASML',
    'BE', 'CRWV', 'IREN', 'CORZ', 'APLD', 'RIOT', 'CLSK', 'SEI', 'BITF', 'BTDR', 'BW',
    'PUMP', 'HIVE', 'GLW', 'SMH', 'BN', 'UBER', 'QSR', 'HHC', 'HTZ', 'HAL', 'MOH',
    'LULU', 'SLM', 'BRKR', 'NTRA', 'IWM', 'INSM', 'YPF', 'TBBB', 'AA', 'NAMS', 'SE',
    'STM', 'WWD', 'TEVA', 'CPNG', 'OPCH', 'CRH', 'RVMD', 'MSTR',
    'TSLA', 'COIN', 'ROKU', 'RBLX', 'HOOD', 'SHOP', 'PLTR', 'CRSP', 'TEM',
]

# NASDAQ-traded tickers (heuristic)
NASDAQ_TICKERS = {'AAPL', 'NVDA', 'MRVL', 'SNDK', 'WDC', 'AMZN', 'META', 'GOOGL',
    'GOOG', 'TSLA', 'ROKU', 'COIN', 'ZM', 'CRSP', 'RBLX', 'MSFT', 'PLTR', 'MSTR', 'HOOD', 'SHOP', 'TEM',
    'DXCM', 'AVGO', 'AMD', 'MU', 'INTC', 'ASML', 'AMGN', 'GILD', 'REGN', 'VRTX',
    'NTRA', 'INSM', 'NAMS', 'OPCH', 'FIG', 'RVMD', 'CRIS', 'SHAI', 'WWD',
    'LULU', 'BRKR', 'SLM', 'TEVA', 'AA', 'CPNG', 'SMH', 'SNDK',
    'IREN', 'CORZ', 'APLD', 'RIOT', 'CLSK',
}
NYSE_TICKERS = {'BAC', 'AXP', 'KO', 'OXY', 'CVX', 'MCO', 'VRSN', 'VRT', 'CEG', 'TSM',
    'CMG', 'HLT', 'LOW', 'PFE', 'GEO', 'SIG', 'MRK', 'DVA', 'KR', 'SIRI', 'DAL',
    'CB', 'KHC', 'SPG', 'HHC', 'HTZ', 'HAL', 'MOH', 'QSR', 'BN', 'UBER', 'CRH',
    'TBBB', 'SE', 'STM', 'YPF', 'BE', 'GLW', 'M', 'CRCL',
}
AMEX_TICKERS = {'SPY', 'IWM'}


# ─── Helper functions ───

def get_13f(cik, acc):
    """Fetch & parse 13F infotable from SEC EDGAR."""
    if not acc:
        return []
    clean = acc.replace('-', '')
    idx_url = f'https://www.sec.gov/Archives/edgar/data/{cik}/{clean}/index.json'
    try:
        r = requests.get(idx_url, headers=HEADERS, timeout=10)
        items = r.json().get('directory', {}).get('item', [])
    except:
        return []

    info_file = None
    for item in items:
        n = item.get('name', '')
        if n.endswith('.xml') and n != 'primary_doc.xml':
            info_file = n
            break
    if not info_file:
        return []

    info_url = f'https://www.sec.gov/Archives/edgar/data/{cik}/{clean}/{info_file}'
    try:
        r = requests.get(info_url, headers=HEADERS, timeout=15)
        text = r.text
    except:
        return []

    tables = re.findall(r'<ns1:infoTable>(.*?)</ns1:infoTable>', text, re.DOTALL)
    if not tables:
        tables = re.findall(r'<infoTable>(.*?)</infoTable>', text, re.DOTALL)
    if not tables:
        return []

    holdings = []
    for tb in tables:
        def x(tag):
            m = re.search(f'<{tag}>(.*?)</{tag}>', tb)
            if not m:
                m = re.search(f'<ns1:{tag}>(.*?)</ns1:{tag}>', tb)
            return m.group(1).strip() if m else ''
        name = x('nameOfIssuer')
        value = int(x('value')) if x('value') else 0
        shares = int(x('sshPrnamt')) if x('sshPrnamt') else 0
        ticker_raw = x('ticker')
        is_put = x('putCall') == 'Put'
        holdings.append({
            'name': name.upper().strip(),
            'ticker': ticker_raw.upper().strip() if ticker_raw else '',
            'value': value,
            'shares': shares,
            'is_put': is_put,
        })
    return holdings


def aggregate_long_only(holdings):
    """Sum long positions by issuer, ignoring puts/calls."""
    agg = defaultdict(lambda: {'val': 0, 'ticker': ''})
    for h in holdings:
        if h['is_put']:
            continue
        key = h['name']
        agg[key]['val'] += h['value']
        if h['ticker'] and not agg[key]['ticker']:
            agg[key]['ticker'] = h['ticker']
    return agg


def resolve_ticker(name, ticker_from_filing, known_map):
    """Resolve a company name/raw ticker to a clean ticker symbol."""
    # If there's a ticker from the filing and it's 1-5 letters, use it
    if ticker_from_filing and 1 <= len(ticker_from_filing) <= 5 and ticker_from_filing.isalpha():
        return ticker_from_filing
    # Try the hardcoded name→ticker map
    if name in known_map:
        return known_map[name]
    # Try partial match
    for key, val in known_map.items():
        if key.startswith(name[:6]) or name.startswith(key[:6]):
            return val
    # Unknown — use abbreviated name
    return name[:5]


def get_cathie_holdings():
    """Fetch ARKK daily holdings from arkfunds.io API."""
    try:
        req = urllib.request.Request('https://arkfunds.io/api/v1/etf/holdings?symbol=ARKK',
                                      headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        holdings = data.get('holdings', [])
        # Filter to top 10 with valid tickers, exclude unknowns
        valid = [h for h in holdings if h.get('ticker') and len(h['ticker']) <= 5]
        total_top = sum(h.get('weight') or 0 for h in valid[:10])
        tickers = []
        weights = {}
        for h in valid[:10]:
            t = h['ticker']
            wt = (h.get('weight') or 0) / total_top * 100 if total_top > 0 else 0
            tickers.append(t)
            weights[t] = wt
        return tickers, weights
    except Exception as e:
        print(f"  ⚠️ Failed to fetch ARKK: {e}")
        return None, None


def get_exchange(ticker):
    """Determine exchange for a ticker."""
    if ticker in NASDAQ_TICKERS:
        return 'NASDAQ'
    if ticker in NYSE_TICKERS:
        return 'NYSE'
    if ticker in AMEX_TICKERS:
        return 'AMEX'
    return 'NASDAQ'  # default


def generate_cmg_entry(manager_key, label, meta, tags, tickers, weights):
    """Generate the CMG entry JavaScript."""
    tk_str = ','.join(f"'{t}'" for t in tickers)
    wt_str = ','.join(f"{t}:{weights[t]}" for t in tickers if t in weights)
    return (f"  {manager_key}:{{name:'{label}',meta:'{meta}',"
            f"tags:{tags},tk:[{tk_str}],wt:{{{wt_str}}}}},")


def generate_exch_map(tickers):
    """Generate the exch mapping JavaScript."""
    parts = []
    for t in sorted(set(tickers)):
        ex = get_exchange(t)
        parts.append(f"  {t}:'{ex}'")
    return ',\n'.join(parts)


def generate_colors_map(tickers):
    """Generate color mapping."""
    colors = [
        '#76b900', '#4fc3f7', '#7cff00', '#00b894', '#ff6b6b', '#e17055', '#0984e3',
        '#ff9900', '#1877f2', '#34a853', '#555555', '#e31b23', '#002663', '#f40000',
        '#0093c4', '#e31937', '#003a70', '#7ac043', '#e82127', '#6a1b9a', '#0052ff',
        '#0e71eb', '#000000', '#00a2ff', '#00a4ef', '#00843d', '#00b2a9', '#d50032',
        '#0089cf', '#211915', '#00a4e4', '#004990', '#0093d0',
    ]
    parts = []
    for i, t in enumerate(sorted(set(tickers))):
        c = colors[i % len(colors)]
        parts.append(f"  {t}:'{c}'")
    return ',\n'.join(parts)


# ─── Main ───

def main():
    print("📡 Fetching 13F data from SEC EDGAR...")

    all_tickers = set()
    cmg_entries = []

    for key, mgr in MANAGERS.items():
        name = mgr['label']
        cik = mgr['cik']
        acc = mgr['acc']
        tags = mgr['tags']
        meta = mgr['meta']

        holdings = get_13f(cik, acc)
        if not holdings:
            print(f"  ⚠️ {name}: No 13F data (acc={acc})")
            continue

        agg = aggregate_long_only(holdings)
        total_val = sum(d['val'] for d in agg.values())
        if total_val == 0:
            print(f"  ⚠️ {name}: Total value is 0, skipping")
            continue

        # Sort by value descending
        sorted_items = sorted(agg.items(), key=lambda x: x[1]['val'], reverse=True)

        # Build ticker + weight map (top 10 positions, excluding unresolved)
        tickers = []
        weights = {}
        for name_filing, data in sorted_items:
            ticker = resolve_ticker(name_filing, data['ticker'], NAME_TO_TICKER)
            if len(ticker) > 5:
                continue  # skip unresolved
            pct = round(data['val'] / total_val * 100, 1)
            if pct < 1.0:
                continue  # skip <1% positions to keep dashboard clean
            tickers.append(ticker)
            weights[ticker] = pct
            all_tickers.add(ticker)
            if len(tickers) >= 10:
                break

        # Normalize weights to sum to 100%
        total_wt = sum(weights.values())
        if total_wt > 0:
            weights = {t: round(w / total_wt * 100, 1) for t, w in weights.items()}

        # total_val is in DOLLARS (raw XML value)
        # Convert to billions/millions
        val_b = total_val / 1e9
        if val_b >= 1.0:
            aum_str = f'${val_b:.2f}B'
        else:
            val_m = total_val / 1e6
            aum_str = f'${val_m:.0f}M'
        meta_str = f'AUM: {aum_str}'

        entry = generate_cmg_entry(key, name, meta_str, str(tags), tickers, weights)
        cmg_entries.append(entry)
        print(f"  ✅ {name}: {len(tickers)} holdings ({aum_str})")

    # Add Cathie (ARK Innovation — from daily API)
    cathie_tickers, cathie_weights = get_cathie_holdings()
    if cathie_tickers:
        cmg_entries.append(
            f"  cathie:{{name:'Cathie Wood (ARK Innovation)',meta:'ARKK Daily',"
            f"tags:['Innovation','Genomics','Fintech'],tk:{cathie_tickers},"
            f"wt:{{{','.join(f'{t}:{round(w,1)}' for t,w in cathie_weights.items())}}}}}",
        )
        for t in cathie_tickers:
            all_tickers.add(t)
        print(f"  ✅ Cathie Wood (ARKK): {len(cathie_tickers)} holdings")
    else:
        print(f"  ⚠️ Cathie Wood (ARKK): Failed to fetch")

    # Add Trump (manual speculative watchlist)
    trump_tickers = TRUMP_HOLDINGS['tk']
    trump_weights = TRUMP_HOLDINGS['wt']
    cmg_entries.append(
        f"  trump:{{name:'Trump (Media & Deals)',meta:'Speculative',"
        f"tags:['Media','Crypto'],tk:{trump_tickers},wt:{{{','.join(f'{t}:{w}' for t,w in trump_weights.items())}}}}}"
    )
    for t in trump_tickers:
        all_tickers.add(t)

    print(f"\n📝 Generating updated index.html...")

    # Read existing template
    template_path = os.path.expanduser('~/.openclaw/workspace/skills/stock-dashboard/static/index.html')
    with open(template_path) as f:
        html = f.read()

    # Build new exch map
    exch_lines = generate_exch_map(all_tickers)
    colors_lines = generate_colors_map(all_tickers)

    # Build new CMG block
    cmg_block = "var CMG = {\n" + "\n".join(cmg_entries) + "\n};"

    # Replace exch map
    html = re.sub(
        r'var exch = \{[\s\S]*?\};',
        f'var exch = {{\n{exch_lines}\n}};',
        html
    )

    # Replace COLORS map
    html = re.sub(
        r'var COLORS = \{[\s\S]*?\};',
        f'var COLORS = {{\n{colors_lines}\n}};',
        html
    )

    # Replace CMG object
    html = re.sub(
        r'var CMG = \{[\s\S]*?\};',
        cmg_block,
        html
    )

    with open(template_path, 'w') as f:
        f.write(html)

    print(f"  ✅ Written to {template_path}")
    print(f"\n📊 Summary:")
    print(f"  Total unique tickers in exch map: {len(all_tickers)}")
    for entry in cmg_entries:
        tickers_in = re.findall(r"'([A-Z]+)'", entry)
        print(f"  • {len(tickers_in)} holdings per entry")


if __name__ == '__main__':
    main()
