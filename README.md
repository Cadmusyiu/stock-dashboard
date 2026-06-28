# CadAI Stock Dashboard

A multi-panel equity research dashboard that combines fundamentals, fund-manager positioning, sector rotation, and event-driven social-signal impact analysis.

## Features

- **Market Overview** — broad market snapshot at a glance
- **Fund Manager 13F** — holdings & performance tracking for tracked managers
- **Top Movers** — daily price leaders and laggards
- **Sector Rotation** — capital flow across sectors
- **Trump Impact Score** — a social-signal pipeline (`scripts/trump_social_monitor.py` / `trump_social_scraper.py`) that ingests posts, dedupes them, and scores their market impact

## Stack

- **Frontend:** HTML / CSS / vanilla JavaScript (no build step)
- **Data:** JSON snapshots in `data/` (`screener_data.json`, `fund_manager_*`, `sector_rotation.json`, `trump_impact_scores.json`)
- **Social-signal pipeline:** Python (secrets managed via 1Password, never hardcoded)

## Run

Open `index.html` directly, or serve locally:

```bash
python3 -m http.server 8000
# then visit http://localhost:8000
```

## Data refresh

The Trump social-signal pipeline can be run via `scripts/trump_social_monitor_wrapper.sh`; outputs land in `data/trump_*.json` and feed the Impact Score panel.
