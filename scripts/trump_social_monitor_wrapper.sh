#!/bin/bash
# trump_social_monitor_wrapper.sh
# Runs the Trump social monitor. Telegram alerts work only if 1Password is unlocked.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."
python3 scripts/trump_social_monitor.py 2>&1
