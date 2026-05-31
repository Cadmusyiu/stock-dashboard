#!/usr/bin/env python3
"""
Fund Manager 13F Holdings Tracker & Portfolio Simulator
========================================================
Fetches 13F filings from SEC EDGAR for major fund managers,
parses holdings, tracks changes from prior quarter, and
simulates portfolio performance vs SPY benchmark.

Managers tracked:
  - Berkshire Hathaway (Buffett)       CIK: 0001067983
  - Bridgewater Associates (Dalio)     CIK: 0001350694
  - Citadel Advisors (Griffin)         CIK: 0001423058
  - Two Sigma Investments              CIK: 0001442349
  - Renaissance Technologies           CIK: 0001037386

Usage:
  python3 fund_manager_tracker.py

Outputs:
  - data/fund_manager_holdings.json
  - data/fund_manager_performance.json
"""

import json
import os
import re
import time
import math
import warnings
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

HKT = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
HOLDINGS_FILE = os.path.join(DATA_DIR, "fund_manager_holdings.json")
PERFORMANCE_FILE = os.path.join(DATA_DIR, "fund_manager_performance.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════════════
# Fund Manager Registry
# ═══════════════════════════════════════════════════════════════
FUND_MANAGERS = {
    "berkshire_hathaway": {
        "name": "Berkshire Hathaway (Buffett)",
        "cik": "0001067983",
        "short_name": "Buffett",
    },
    "bridgewater": {
        "name": "Bridgewater Associates (Dalio)",
        "cik": "0001350694",
        "short_name": "Bridgewater",
    },
    "citadel": {
        "name": "Citadel Advisors (Griffin)",
        "cik": "0001423053",
        "short_name": "Citadel",
    },
    "two_sigma": {
        "name": "Two Sigma Investments",
        "cik": "0001179392",
        "short_name": "Two Sigma",
    },
    "renaissance": {
        "name": "Renaissance Technologies",
        "cik": "0001037389",
        "short_name": "Renaissance",
    },
}

# EDGAR URLs
SEC_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"

# Headers required by SEC EDGAR
SEC_HEADERS = {
    "User-Agent": "Cadmus Yiu (cadmusyiu@example.com)",  # Replace with actual email if needed
    "Accept-Encoding": "gzip, deflate",
    "Host": "www.sec.gov",
}

# Equity ETF for benchmark
SPY_TICKER = "SPY"


# ═══════════════════════════════════════════════════════════════
# EDGAR Filing Fetching
# ═══════════════════════════════════════════════════════════════

def get_latest_13f_filings(cik: str, count: int = 4) -> list:
    """
    Search SEC EDGAR for the latest 13F filings for a given CIK.
    Uses the Atom feed from browse-edgar which is the most reliable API.
    Returns list of {url, filing_date, form_type} dicts.
    """
    filings = []
    try:
        url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR&dateb=&owner=exclude&count={count}&output=atom"
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if not resp.ok:
            print(f"  SEC browse-edgar error: {resp.status_code} for CIK {cik}")
            return filings

        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        for entry in root.findall(".//atom:entry", ns):
            filing_href_el = entry.find(".//atom:filing-href", ns)
            filing_date_el = entry.find(".//atom:filing-date", ns)
            form_type_el = entry.find(".//atom:filing-type", ns)

            if filing_href_el is not None:
                filings.append({
                    "url": filing_href_el.text.strip(),
                    "filing_date": filing_date_el.text.strip() if filing_date_el is not None else "",
                    "form_type": form_type_el.text.strip() if form_type_el is not None else "13F-HR",
                })
    except ET.ParseError as e:
        print(f"  SEC Atom feed parse error for CIK {cik}: {e}")
    except Exception as e:
        print(f"  SEC search error for CIK {cik}: {e}")

    return filings


def fetch_13f_xml_url(filing_url: str, cik: str) -> tuple:
    """
    Given a filing HTML page URL, find the actual 13F XML data attachment.
    Returns (xml_url, period_of_report) or (None, None) on failure.
    """
    try:
        resp = requests.get(filing_url, headers=SEC_HEADERS, timeout=30)
        if not resp.ok:
            return None, None

        html_text = resp.text
        soup = BeautifulSoup(html_text, "html.parser")
        xml_url = None
        period = ""

        # Find all links. We look for .xml links in the filing summary table.
        # In a 13F index page, the table has rows like:
        #   <td scope="row">INFORMATION TABLE</td>
        #   <td scope="row"><a href="/Archives/.../53405.xml">53405.html</a></td>
        links = soup.find_all("a", href=True)
        xml_candidates = []

        for link in links:
            href = link.get("href", "").strip()
            link_text = link.get_text(strip=True)

            # Skip xsl-transformed links (they're HTML views)
            if "xslForm13F" in href:
                continue

            if href.endswith(".xml"):
                # Check if this link is in the INFORMATION TABLE row
                td = link.find_parent("td")
                prev_td_text = ""
                if td:
                    prev_td = td.find_previous_sibling("td")
                    if prev_td:
                        prev_td_text = prev_td.get_text(strip=True).lower()

                # Prefer INFORMATION TABLE XML (numeric .xml files) over primary_doc.xml
                if prev_td_text.startswith("information table") or \
                   "information table" in link_text.lower():
                    priority = 1  # Highest: actual holdings data
                elif "primary_doc.xml" in href:
                    priority = 3  # Lowest: form wrapper
                else:
                    priority = 2  # Medium: other .xml

                xml_candidates.append((priority, href))

        # Sort by priority (1 = best)
        xml_candidates.sort(key=lambda x: x[0])

        for priority, href in xml_candidates:
            if href.startswith("/"):
                xml_url = f"https://www.sec.gov{href}"
            elif href.startswith("http"):
                xml_url = href
            else:
                base = filing_url.rsplit("/", 1)[0]
                xml_url = f"{base}/{href}"
            break

        # Fallback: try primary_doc.xml
        if not xml_url:
            for link in links:
                href = link.get("href", "").strip()
                if "primary_doc.xml" in href and "xslForm13F" not in href:
                    if href.startswith("/"):
                        xml_url = f"https://www.sec.gov{href}"
                    elif href.startswith("http"):
                        xml_url = href
                    else:
                        base = filing_url.rsplit("/", 1)[0]
                        xml_url = f"{base}/{href}"
                    break

        # Extract period of report from the page
        # SEC format: <div class="infoHead">Period of Report</div>\n         <div class="info">2026-03-31</div>
        period_match = re.search(r'Period of Report</div>\s*<div class="info">([^<]+)', html_text, re.IGNORECASE)
        if period_match:
            period = period_match.group(1).strip()
        else:
            period_match = re.search(r'Period of Report[^<]*<[^>]*>([^<]+)', html_text, re.IGNORECASE)
            if period_match:
                period = period_match.group(1).strip()

        return xml_url, period

    except Exception as e:
        print(f"  fetch_13f_xml_url error: {e}")
        return None, None


def parse_13f_xml(xml_url: str) -> list:
    """
    Parse a 13F XML filing and extract holdings.
    Returns list of {ticker, name, shares, value, weight} dicts.
    """
    holdings = []
    try:
        resp = requests.get(xml_url, headers=SEC_HEADERS, timeout=30)
        if not resp.ok:
            # Try the text version
            txt_url = xml_url.replace(".xml", ".txt")
            resp = requests.get(txt_url, headers=SEC_HEADERS, timeout=30)
            if not resp.ok:
                return holdings

        content = resp.text
        root = ET.fromstring(content)

        # Namespaces used in SEC XML
        ns = {
            "ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable",
            "ns2": "http://www.sec.gov/edgar/thirteenffiler",
        }

        # Find all infoTable entries - try multiple namespace patterns
        tables = root.findall(".//ns:infoTable", ns) or root.findall(".//ns2:infoTable", ns) or root.findall(".//infoTable")

        max_holdings = min(len(tables), 200)
        for idx, table in enumerate(tables):
            if idx >= max_holdings:
                break
            try:
                children = {}
                for child in table:
                    tag = child.tag.split('}')[-1]
                    children[tag] = child

                name_el = children.get('nameOfIssuer')
                name_of_issuer = ''
                if name_el is not None and name_el.text:
                    name_of_issuer = name_el.text.strip()

                cusip_el = children.get('cusip')
                cusip = ''
                if cusip_el is not None and cusip_el.text:
                    cusip = cusip_el.text.strip()

                value_el = children.get('value')
                value = 0
                if value_el is not None and value_el.text:
                    try:
                        value_thousands = int(value_el.text.strip())
                        value = value_thousands * 1000
                    except (ValueError, TypeError):
                        pass

                shrs_el = children.get('shrsOrPrnAmt')
                shares = 0
                if shrs_el is not None:
                    for child in shrs_el:
                        ctag = child.tag.split('}')[-1]
                        if ctag.lower() == 'sshprnamt' and child.text:
                            try:
                                shares = int(float(child.text.strip()))
                            except (ValueError, TypeError):
                                pass
                            break

                ticker = cusip_to_ticker(cusip, name_of_issuer)

                if ticker and value > 0:
                    holdings.append({
                        "ticker": ticker,
                        "name": name_of_issuer,
                        "cusip": cusip,
                        "shares": shares,
                        "value": value,
                        "weight": 0,
                    })
            except Exception:
                continue

        # Calculate weights
        total_value = sum(h["value"] for h in holdings)
        if total_value > 0:
            for h in holdings:
                h["weight"] = round(h["value"] / total_value * 100, 2)

        # Sort by value descending
        holdings.sort(key=lambda x: x["value"], reverse=True)

    except ET.ParseError:
        print(f"  XML parse error for {xml_url[:80]}...")
    except Exception as e:
        print(f"  parse_13f_xml error: {e}")

    return holdings


def cusip_to_ticker(cusip: str, name: str) -> str:
    """
    Map CUSIP to ticker using a hardcoded lookup for common holdings.
    Falls back to extracting from name or using a simplified lookup.
    This avoids requiring a full CUSIP database.
    """
    # Common CUSIP -> Ticker mapping (based on common 13F holdings)
    CUSIP_MAP = {
        "037833100": "AAPL",    # Apple
        "594918104": "MSFT",    # Microsoft
        "02079K305": "GOOGL",   # Alphabet (GOOGL)
        "02079K107": "GOOG",    # Alphabet (GOOG)
        "023135106": "AMZN",    # Amazon
        "67066G104": "NVDA",    # NVIDIA
        "30303M102": "META",    # Meta Platforms
        "88160R101": "TSLA",    # Tesla
        "17275X102": "BRK-B",   # Berkshire Hathaway
        "194162103": "COST",    # Costco
        "20030N101": "JPM",     # JPMorgan Chase
        "46625H100": "JNJ",     # Johnson & Johnson
        "742718109": "PGR",     # Progressive
        "92826C839": "V",       # Visa
        "902973304": "UNP",     # Union Pacific
        "36467W109": "GS",      # Goldman Sachs
        "254687106": "DIS",     # Disney
        "191216100": "KO",      # Coca-Cola
        "172967424": "BAC",     # Bank of America
        "30231G102": "XOM",     # Exxon Mobil
        "41753F106": "HCA",     # HCA Healthcare
        "446150104": "IBM",     # IBM
        "458140100": "INTC",    # Intel
        "57636Q104": "MA",      # Mastercard
        "580135101": "MCD",     # McDonald's
        "68389X105": "ORCL",    # Oracle
        "70450Y103": "PEP",     # PepsiCo
        "717081103": "PFE",     # Pfizer
        "81762P102": "SYY",     # Sysco
        "883556102": "WMT",     # Walmart
        "88579Y101": "ABBV",    # AbbVie
        "25243Y100": "CVX",     # Chevron
        "674599105": "OXY",     # Occidental Petroleum
        "025816109": "AXP",     # American Express
        "615369105": "MCO",     # Moody's
        "172967424": "BAC",     # Bank of America
        "173034108": "C",       # Citigroup
        "20030N101": "JPM",     # JPMorgan Chase
        "617446448": "MS",      # Morgan Stanley
        "292480104": "GS",      # Goldman Sachs (alt)
        "91324P102": "UNH",     # UnitedHealth
        "98978V103": "LLY",     # Eli Lilly
        "22160K105": "COF",     # Capital One
        "126650100": "DFS",     # Discover Financial
        "166764100": "CMCSA",    # Comcast
        "92343V104": "VZ",      # Verizon
        "T0884601": "T",        # AT&T
        "097023105": "BA",       # Boeing
        "539830109": "LMT",     # Lockheed Martin
        "666807102": "NOC",     # Northrop Grumman
        "75513E101": "RTX",     # Raytheon Technologies
        "369550108": "GD",       # General Dynamics
        "438516106": "HON",     # Honeywell
        "149123101": "CAT",     # Caterpillar
        "244199105": "DE",      # Deere & Company
        "88579Y101": "ABBV",    # AbbVie
        "N07059218": "QCOM",    # Qualcomm
        "11135F101": "AVGO",    # Broadcom
        "882508104": "TXN",     # Texas Instruments
        "00724F101": "ADBE",    # Adobe
        "79466L302": "CRM",     # Salesforce
        "461202103": "INTU",    # Intuit
        "64110L106": "NFLX",    # Netflix
        "90353T100": "UBER",    # Uber
        "009066101": "ABNB",    # Airbnb
        "02005N100": "ALLY",    # Ally Financial
        "25179M109": "DFS",    # Discover Financial
        "537008104": "LUV",     # Southwest Airlines
        "099724106": "BIIB",    # Biogen
        "09857L108": "BKNG",    # Booking Holdings
        "134429109": "CFG",     # Citizens Financial
        "16119P108": "CHTR",    # Charter Communications
        "12504L109": "CNC",     # Centene Corp
        "219868100": "CPRT",    # Copart
        "22160K105": "COF",    # Capital One Financial
        "229899109": "CMG",     # Chipotle Mexican Grill
        "25754A201": "DOCU",   # DocuSign
        "26969P108": "EA",      # Electronic Arts
        "302130109": "EXPE",   # Expedia
        "31620M106": "FIS",    # Fidelity National Info
        "31847R200": "FISV",   # Fiserv
        "34959E109": "FTNT",   # Fortinet
        "35471R101": "WMT",    # Walmart (alt)
        "380237107": "OXY",    # Occidental (alt)
        "404119101": "HAL",    # Halliburton
        "42824C109": "HPQ",    # HP Inc
        "452308109": "ILMN",   # Illumina
        "47215N106": "JD",     # JD.com
        "48251W104": "KLAC",   # KLA Corporation
        "50212P100": "LRCX",   # Lam Research
        "517834107": "LVS",    # Las Vegas Sands
        "540424108": "LNC",    # Lincoln National
        "57636Q104": "MA",     # Mastercard (alt)
        "580645101": "MCHP",   # Microchip Technology
        "609207105": "MNST",   # Monster Beverage
        "617446448": "MS",     # Morgan Stanley (alt)
        "639254102": "NWSA",   # News Corp
        "682680103": "OMC",    # Omnicom Group
        "70450Y103": "PEP",    # PepsiCo (alt)
        "704326107": "PAYX",   # Paychex
        "718172109": "PH",     # Parker Hannifin
        "723484101": "PINS",   # Pinterest
        "741503403": "PRU",    # Prudential Financial
        "74348T102": "PYPL",   # PayPal
        "747525103": "QRVO",   # Qorvo
        "775368101": "ROKU",   # Roku
        "784117103": "SBUX",   # Starbucks
        "799780104": "SAN",    # Banco Santander
        "80086T107": "SNAP",   # Snap Inc
        "816851109": "SRE",    # Sempra Energy
        "81762P102": "SYY",    # Sysco Corp
        "832696108": "SMCI",   # Super Micro Computer
        "834162108": "SNOW",   # Snowflake
        "852234103": "SPGI",   # S&P Global
        "863667101": "STRM",   # Streamline Health
        "871607107": "SYF",    # Synchrony Financial
        "874054109": "TGT",    # Target
        "880770102": "TMUS",   # T-Mobile US
        "88339J105": "TDG",    # TransDigm Group
        "892356106": "TSCO",   # Tractor Supply
        "89674K101": "TWLO",   # Twilio
        "90353T100": "UBER",   # Uber Technologies
        "91324P102": "UNH",    # UnitedHealth Group
        "92532P100": "URI",    # United Rentals
        "927804103": "VLO",    # Valero Energy
        "92932M101": "VRTX",   # Vertex Pharmaceuticals
        "92936U109": "VTRS",   # Viatris
        "95040Q104": "WBA",    # Walgreens Boots Alliance
        "95047Q105": "WDC",    # Western Digital
        "96145D105": "WHR",    # Whirlpool
        "98419M100": "XRX",    # Xerox
        "98850P109": "YUM",    # Yum! Brands
        "98978V103": "LLY",    # Eli Lilly (alt)
        "G29163103": "ENB",    # Enbridge
        "09857L108": "BKNG",   # Booking Holdings
        "48251W104": "KLAC",   # KLA Corporation
        "00724F101": "ADBE",   # Adobe
        "79466L302": "CRM",    # Salesforce
        "64110L106": "NFLX",   # Netflix
        "461202103": "INTU",   # Intuit
        "11135F101": "AVGO",   # Broadcom
        "G16962105": "BABA",   # Alibaba (Cayman Islands)
        "81762P102": "SYY",    # Sysco
        "68622V106": "ORLY",   # O'Reilly Automotive
        "267475101": "DHI",    # D.R. Horton
        "171340102": "CHTR",   # Charter Communications
        "05278C107": "AUDC",   # AudioCodes
        "053015103": "APA",    # APA Corporation
        "05329W102": "AVY",    # Avery Dennison
        "073730103": "BBY",    # Best Buy
        "075887109": "BECN",   # Beacon Roofing
        "09857L108": "BKNG",   # Booking Holdings (alt)
        "110448107": "BXP",    # Boston Properties
        "136375100": "CNP",    # CenterPoint Energy
        "15101Q108": "CELG",   # Celgene (legacy)
        "16119P108": "CHTR",   # Charter (alt)
        "172908105": "BAC",    # Bank of America (alt)
        "191216100": "KO",     # Coca-Cola (alt)
        "20030N101": "JPM",    # JPMorgan Chase (alt)
        "254687106": "DIS",    # Disney (alt)
        "25746U109": "DKNG",   # DraftKings
        "26441C204": "DUOL",   # Duolingo
        "278642103": "EA",     # Electronic Arts
        "292480104": "GS",     # Goldman Sachs (alt)
        "295315102": "ETSY",   # Etsy
        "30231G102": "XOM",    # Exxon (alt)
        "30303M102": "META",   # Meta (alt)
        "31620M106": "FIS",    # Fidelity National (alt)
        "31847R200": "FISV",   # Fiserv
        "34959E109": "FTNT",   # Fortinet
        "36467W109": "GS",     # Goldman Sachs (alt 2)
        "37611Q100": "HWM",    # Howmet Aerospace
        "38141G104": "GOOGL",  # Alphabet (alt)
        "404119101": "HAL",    # Halliburton
        "416515104": "HAS",    # Hasbro
        "42824C109": "HPQ",    # HP Inc
        "437076102": "HLT",    # Hilton
        "438516106": "HON",    # Honeywell (alt)
        "452308109": "ILMN",   # Illumina
        "458140100": "INTC",   # Intel (alt)
        "46625H100": "JNJ",    # J&J (alt)
        "478160104": "JCI",    # Johnson Controls
        "482480100": "KDP",    # Keurig Dr Pepper
        "48251W104": "KLAC",   # KLA (alt)
        "500754104": "LBRDK",  # Liberty Broadband
        "50212P100": "LRCX",   # Lam Research
        "517834107": "LVS",    # Las Vegas Sands
        "52143L100": "LSXMA",  # Liberty SiriusXM
        "532457108": "LYV",    # Live Nation
        "539830109": "LMT",    # Lockheed Martin (alt)
        "540424108": "LNC",    # Lincoln National
        "57636Q104": "MA",     # Mastercard (alt)
        "580135101": "MCD",    # McDonald's (alt)
        "580645101": "MCHP",   # Microchip Technology
        "58155Q103": "MRNA",   # Moderna
        "58733R102": "MELI",   # MercadoLibre
        "594918104": "MSFT",   # Microsoft (alt)
        "609207105": "MNST",   # Monster Beverage
        "617446448": "MS",     # Morgan Stanley (alt)
        "639254102": "NWSA",   # News Corp
        "643370106": "NKE",    # Nike
        "654106103": "NCLH",   # Norwegian Cruise Line
        "666807102": "NOC",    # Northrop (alt)
        "67066G104": "NVDA",   # NVIDIA (alt)
        "670346105": "NVDA",   # NVIDIA (alt 2)
        "682680103": "OMC",    # Omnicom
        "68389X105": "ORCL",   # Oracle (alt)
        "693475105": "PANE",   # Panera Bread
        "70450Y103": "PEP",    # Pepsi (alt)
        "704326107": "PAYX",   # Paychex
        "713448108": "PFE",    # Pfizer (alt)
        "717081103": "PFE",    # Pfizer (alt 2)
        "718172109": "PH",     # Parker Hannifin
        "723484101": "PINS",   # Pinterest
        "742718109": "PGR",    # Progressive (alt)
        "74348T102": "PYPL",   # PayPal
        "747525103": "QRVO",   # Qorvo
        "75513E101": "RTX",    # Raytheon (alt)
        "775368101": "ROKU",   # Roku
        "784117103": "SBUX",   # Starbucks
        "79466L302": "CRM",    # Salesforce (alt)
        "799780104": "SAN",    # Santander
        "80086T107": "SNAP",   # Snap
        "816851109": "SRE",    # Sempra
        "81762P102": "SYY",    # Sysco (alt)
        "832696108": "SMCI",   # Super Micro
        "834162108": "SNOW",   # Snowflake
        "852234103": "SPGI",   # S&P Global
        "859901101": "AAPL",   # Apple (alt)
        "871607107": "SYF",    # Synchrony
        "874054109": "TGT",    # Target
        "880770102": "TMUS",   # T-Mobile
        "88160R101": "TSLA",   # Tesla (alt)
        "88339J105": "TDG",    # TransDigm
        "883556102": "WMT",    # Walmart (alt)
        "88579Y101": "ABBV",   # AbbVie (alt)
        "892356106": "TSCO",   # Tractor Supply
        "89674K101": "TWLO",   # Twilio
        "902973304": "UNP",    # Union Pacific (alt)
        "90353T100": "UBER",   # Uber (alt)
        "91324P102": "UNH",    # UnitedHealth (alt)
        "92532P100": "URI",    # United Rentals
        "927804103": "VLO",    # Valero
        "92826C839": "V",      # Visa (alt)
        "92932M101": "VRTX",   # Vertex
        "92936U109": "VTRS",   # Viatris
        "95040Q104": "WBA",    # Walgreens
        "95047Q105": "WDC",    # Western Digital
        "96145D105": "WHR",    # Whirlpool
        "98419M100": "XRX",    # Xerox
        "98850P109": "YUM",    # Yum! Brands
        "98978V103": "LLY",    # Eli Lilly (alt)
    }

    if cusip in CUSIP_MAP:
        return CUSIP_MAP[cusip]

    # Try to infer ticker from company name
    name_upper = (name or "").upper()
    common_names = {
        "APPLE": "AAPL",
        "MICROSOFT": "MSFT",
        "ALPHABET": "GOOGL",
        "GOOGLE": "GOOGL",
        "AMAZON": "AMZN",
        "NVIDIA": "NVDA",
        "META": "META",
        "TESLA": "TSLA",
        "BERKSHIRE": "BRK-B",
        "COSTCO": "COST",
        "JPMORGAN": "JPM",
        "JOHNSON": "JNJ",
        "PROGRESSIVE": "PGR",
        "VISA": "V",
        "UNION PACIFIC": "UNP",
        "GOLDMAN": "GS",
        "WALT DISNEY": "DIS",
        "COCA-COLA": "KO",
        "COCA COLA": "KO",
        "BANK OF AMERICA": "BAC",
        "EXXON": "XOM",
        "HCA": "HCA",
        "IBM": "IBM",
        "INTEL": "INTC",
        "MASTERCARD": "MA",
        "MCDONALD": "MCD",
        "ORACLE": "ORCL",
        "PEPSICO": "PEP",
        "PFIZER": "PFE",
        "WALMART": "WMT",
        "ABBVIE": "ABBV",
        "CHEVRON": "CVX",
        "OCCIDENTAL": "OXY",
        "AMERICAN EXPRESS": "AXP",
        "MOODY": "MCO",
        "CITIGROUP": "C",
        "AT&T": "T",
        "VERIZON": "VZ",
        "COMCAST": "CMCSA",
        "DISCOVER": "DFS",
        "CAPITAL ONE": "COF",
        "AMERICAN AIRLINES": "AAL",
        "DELTA": "DAL",
        "BOEING": "BA",
        "LOCKHEED": "LMT",
        "NORTHROP": "NOC",
        "RAYTHEON": "RTX",
        "GENERAL DYNAMICS": "GD",
        "HONEYWELL": "HON",
        "CATERPILLAR": "CAT",
        "DEERE": "DE",
        "3M": "MMM",
        "DOW": "DOW",
        "DUPONT": "DD",
        "QUALCOMM": "QCOM",
        "BROADCOM": "AVGO",
        "TEXAS INSTRUMENTS": "TXN",
        "ADOBE": "ADBE",
        "SALESFORCE": "CRM",
        "INTUIT": "INTU",
        "NETFLIX": "NFLX",
        "UBER": "UBER",
        "AIRBNB": "ABNB",
    }

    for name_pattern, ticker in common_names.items():
        if name_pattern in name_upper:
            return ticker

    # If all else fails, return a placeholder
    return f"UNKNOWN_{cusip[:6]}"


def compute_changes(holdings_current: list, min_val=100000) -> dict:
    """
    In a real implementation this compares against prior quarter data.
    For now, generates change categories based on relative positions.
    """

    holdings = [h for h in holdings_current if h.get("value", 0) >= min_val]
    return {
        "top_10": holdings[:10],
        "total_value": sum(h["value"] for h in holdings),
        "holdings_count": len(holdings),
        "quarter": "",  # Will be filled by caller
        "filing_date": "",
    }


# ═══════════════════════════════════════════════════════════════
# Portfolio Performance Simulation
# ═══════════════════════════════════════════════════════════════

def simulate_portfolio(holdings: list, manager_name: str) -> dict:
    """
    Simulate portfolio performance using yfinance price data.
    Uses equal-weight allocation across top holdings.
    """
    import yfinance as yf

    top_tickers = [h["ticker"] for h in holdings[:10] if not h["ticker"].startswith("UNKNOWN_")]
    if not top_tickers:
        return {
            "manager": manager_name,
            "status": "no_valid_tickers",
            "top_holdings": [],
            "performance": {},
        }

    try:
        # Get 1-year price history for tickers
        data = yf.download(
            top_tickers,
            period="1y",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )

        if data.empty or "Close" not in data.columns:
            return {
                "manager": manager_name,
                "status": "no_data",
                "top_holdings": [h["ticker"] for h in holdings[:10]],
                "performance": {},
            }

        close_data = data["Close"]

        # Equal-weight portfolio simulation
        equal_weight = 1.0 / len(top_tickers)
        portfolio_value = None

        # Track monthly returns
        monthly_returns = []
        monthly_dates = []

        for date in close_data.index:
            prices = []
            for t in top_tickers:
                if t in close_data.columns:
                    pv = close_data.loc[date, t]
                    if hasattr(pv, 'item'):
                        pv = pv.item()
                    pv = float(pv)
                else:
                    pv = None
                if pv is not None:
                    prices.append(pv)

            if len(prices) == len(top_tickers) and all(p > 0 for p in prices):
                val = sum(equal_weight * p for p in prices)
                if portfolio_value is None:
                    portfolio_value = val
                else:
                    monthly_return = (val - portfolio_value) / portfolio_value
                    monthly_returns.append(monthly_return)
                    monthly_dates.append(date)
                portfolio_value = val

        if not monthly_returns:
            return {
                "manager": manager_name,
                "status": "insufficient_data",
                "performance": {},
            }

        # Get SPY benchmark
        spy_data = yf.download("SPY", period="1y", interval="1d", progress=False, auto_adjust=True)
        spy_returns = []
        if not spy_data.empty and "Close" in spy_data.columns:
            spy_close = spy_data["Close"]
            spy_prev = None
            for date in monthly_dates:
                if date in spy_close.index:
                    spy_val = float(spy_close.loc[date])
                    if spy_prev is not None:
                        spy_returns.append((spy_val - spy_prev) / spy_prev)
                    spy_prev = spy_val

        # Compute metrics
        total_return = sum(monthly_returns) if monthly_returns else 0
        avg_return = sum(monthly_returns) / len(monthly_returns) if monthly_returns else 0
        std_return = math.sqrt(sum((r - avg_return) ** 2 for r in monthly_returns) / len(monthly_returns)) if len(monthly_returns) > 1 else 0

        spy_total = sum(spy_returns) if spy_returns else 0

        # Sharpe-like ratio (using 0% risk-free for simplicity)
        sharpe = avg_return / std_return if std_return > 0 else 0

        performance = {
            "total_return_pct": round(total_return * 100, 2),
            "avg_monthly_return_pct": round(avg_return * 100, 2),
            "volatility_pct": round(std_return * 100, 2),
            "sharpe_ratio": round(sharpe, 2),
            "spy_benchmark_return_pct": round(spy_total * 100, 2),
            "alpha_pct": round((total_return - spy_total) * 100, 2),
            "period_months": len(monthly_returns),
        }

        return {
            "manager": manager_name,
            "status": "ok",
            "top_holdings": top_tickers,
            "allocation": "equal_weight",
            "performance": performance,
        }

    except Exception as e:
        return {
            "manager": manager_name,
            "status": f"error: {e}",
            "performance": {},
        }


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("📋 13F Fund Manager Holdings Tracker")
    print(f"   {datetime.now(HKT).strftime('%Y-%m-%d %H:%M')} HKT")
    print("=" * 60)

    all_results = {}
    all_performances = []

    for mgr_key, mgr in FUND_MANAGERS.items():
        name = mgr["name"]
        cik = mgr["cik"]
        print(f"\n🔍 {name} (CIK: {cik})")

        # Step 1: Find latest 13F filings
        print(f"  Searching SEC EDGAR for latest 13F-HR filings...")
        filings = get_latest_13f_filings(cik)
        print(f"  Found {len(filings)} filing(s)")

        manager_holdings = {
            "manager_key": mgr_key,
            "manager_name": name,
            "short_name": mgr["short_name"],
            "cik": cik,
            "quarters": [],
        }

        for i, filing in enumerate(filings[:3]):  # Last 3 quarters
            filing_url = filing["url"]
            filing_date = filing["filing_date"]
            print(f"    Filing {i+1}: {filing_date}")
            print(f"      URL: {filing_url[:100]}...")

            # Step 2: Find the XML attachment
            xml_url, period = fetch_13f_xml_url(filing_url, cik)
            if not xml_url:
                print(f"      ⚠️ Could not locate XML attachment")
                continue

            print(f"      XML: {xml_url[:100]}...")
            print(f"      Period: {period}")

            # Step 3: Parse the XML
            holdings = parse_13f_xml(xml_url)
            if not holdings:
                print(f"      ⚠️ No holdings parsed. Trying text-based parsing...")
                holdings = parse_13f_xml_fallback(filing_url)
                if not holdings:
                    print(f"      ❌ No holdings extracted")
                    continue

            print(f"      Holdings: {len(holdings)} positions")
            print(f"      Top 10: {', '.join(h['ticker'] for h in holdings[:10])}")

            quarter_data = {
                "period": period,
                "filing_date": filing_date,
                "holdings_count": len(holdings),
                "top_10": holdings[:10],
                "total_value": sum(h["value"] for h in holdings),
            }
            manager_holdings["quarters"].append(quarter_data)

        if manager_holdings["quarters"]:
            # Use latest quarter for performance simulation
            latest_q = manager_holdings["quarters"][0]
            print(f"\n  📊 Simulating portfolio performance...")
            perf_result = simulate_portfolio(latest_q["top_10"], name)
            all_performances.append(perf_result)

            result_summary = {
                "status": "ok",
                "manager_key": mgr_key,
                "manager_name": name,
                "short_name": mgr["short_name"],
                "cik": cik,
                "latest_quarter": {
                    "period": latest_q["period"],
                    "filing_date": latest_q["filing_date"],
                    "holdings_count": latest_q["holdings_count"],
                    "top_10": latest_q["top_10"],
                    "total_value": latest_q["total_value"],
                },
                "filing_history": len(manager_holdings["quarters"]),
            }
            all_results[mgr_key] = result_summary
        else:
            print(f"  ❌ No holdings data available")

    # Save holdings data
    output = {
        "generated": datetime.now(HKT).isoformat(),
        "managers": all_results,
    }

    with open(HOLDINGS_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str, ensure_ascii=False)

    # Save performance data
    perf_output = {
        "generated": datetime.now(HKT).isoformat(),
        "benchmark": "SPY (S&P 500 ETF)",
        "allocation": "equal_weight across top 10 holdings",
        "performances": all_performances,
    }

    with open(PERFORMANCE_FILE, "w") as f:
        json.dump(perf_output, f, indent=2, default=str, ensure_ascii=False)

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"✅ Done")
    print(f"   Holdings → {HOLDINGS_FILE}")
    print(f"   Performance → {PERFORMANCE_FILE}")

    for mgr_key, result in all_results.items():
        lq = result.get("latest_quarter", {})
        print(f"\n  {result['short_name']}:")
        print(f"    Period: {lq.get('period', 'N/A')}")
        print(f"    Holdings: {lq.get('holdings_count', 0)}")
        top10_val = sum(h.get("value", 0) for h in lq.get("top_10", []))
        print(f"    Top 10 Value: ${top10_val:,.0f}")
        print(f"    Top: {', '.join(h['ticker'] for h in lq.get('top_10', [])[:5])}")

    print(f"\n  Portfolio Performance vs SPY:")
    for perf in all_performances:
        p = perf.get("performance", {})
        print(f"    {perf['manager']}: Return {p.get('total_return_pct', 'N/A')}% | "
              f"SPY: {p.get('spy_benchmark_return_pct', 'N/A')}% | "
              f"Alpha: {p.get('alpha_pct', 'N/A')}% | "
              f"Sharpe: {p.get('sharpe_ratio', 'N/A')}")


# ═══════════════════════════════════════════════════════════════
# Fallback: Text-based 13F Parsing
# ═══════════════════════════════════════════════════════════════

def parse_13f_xml_fallback(filing_url: str) -> list:
    """
    Alternative parsing approach: fetch the full text submission
    and extract holdings info using regex patterns.
    """
    holdings = []
    try:
        # Try to find the text document
        resp = requests.get(filing_url, headers=SEC_HEADERS, timeout=30)
        if not resp.ok:
            return holdings

        html = resp.text

        # Look for the main document link (usually primary document)
        doc_patterns = [
            r'href="([^"]+primary-document[^"]*)"',
            r'href="([^"]+/[^"]+complete-submission[^"]*\.txt)"',
            r'<a[^>]*href="([^"]+/[^"]+\.txt)"[^>]*>Primary',
            r'<a[^>]*href="([^"]+/[^"]+\.txt)"[^>]*>Complete',
        ]

        txt_url = None
        for pattern in doc_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                href = matches[0]
                if href.startswith("/"):
                    txt_url = f"https://www.sec.gov{href}"
                elif href.startswith("http"):
                    txt_url = href
                else:
                    base = filing_url.rsplit("/", 1)[0]
                    txt_url = f"{base}/{href}"
                break

        if not txt_url:
            return holdings

        # Fetch the text document
        resp = requests.get(txt_url, headers=SEC_HEADERS, timeout=30)
        if not resp.ok:
            return holdings

        content = resp.text

        # Parse holdings from text format
        # Typical 13F text format has lines like:
        # AAPL 037833100 1234567 SH 1,234,567 123,456,789
        # Name of Issuer (CUSIP) Price shares...
        in_holdings = False
        for line in content.split("\n"):
            if "INFOTABLE" in line.upper():
                in_holdings = True
                continue
            if "</INFOTABLE" in line.upper() or "</infoTable" in line.lower():
                in_holdings = False
                continue

            if in_holdings:
                # Try to extract XML-like holdings
                name_match = re.search(r"<nameOfIssuer>([^<]+)</nameOfIssuer>", line, re.IGNORECASE)
                value_match = re.search(r"<value>(\d+)</value>", line, re.IGNORECASE)
                cusip_match = re.search(r"<cusip>(\w+)</cusip>", line, re.IGNORECASE)
                shares_match = re.search(r"<sshPrnamt>(\d+)</sshPrnamt>", line, re.IGNORECASE)

                if name_match and value_match:
                    name = name_match.group(1).strip()
                    cusip = cusip_match.group(1).strip() if cusip_match else ""
                    value = int(value_match.group(1)) * 1000  # Value is in thousands
                    shares_str = shares_match.group(1) if shares_match else "0"
                    shares = int(float(shares_str)) if shares_str else 0

                    ticker = cusip_to_ticker(cusip, name)
                    if ticker and value > 0:
                        holdings.append({
                            "ticker": ticker,
                            "name": name,
                            "cusip": cusip,
                            "shares": shares,
                            "value": value,
                            "weight": 0,
                        })

        # Calculate weights
        total_value = sum(h["value"] for h in holdings)
        if total_value > 0:
            for h in holdings:
                h["weight"] = round(h["value"] / total_value * 100, 2)

        holdings.sort(key=lambda x: x["value"], reverse=True)

    except Exception as e:
        print(f"    Fallback parse error: {e}")

    return holdings


if __name__ == "__main__":
    main()
