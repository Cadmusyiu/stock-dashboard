#!/bin/bash
# GREG Screener Auto-Refresh
# Runs screener.py, commits updated data, pushes to GitHub
# Scheduled via LaunchAgent: com.cadmus.screener-refresh

set -e
cd /Users/cadmusyiu/.openclaw/workspace/skills/stock-dashboard

echo "=== $(date) ==="
echo "Running screener.py..."
/usr/bin/python3 screener.py

echo "Checking for changes..."
if git diff --quiet screener_data.json; then
    echo "No changes — screener data unchanged."
    exit 0
fi

echo "Changes detected — committing and pushing..."
git add screener_data.json
git commit -m "auto-refresh: screener data update $(date +%Y-%m-%d)"
git push origin main:main
git push origin main:gh-pages

echo "Done! Deployed to GitHub Pages."
