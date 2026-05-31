#!/bin/bash
# trump_social_monitor_wrapper.sh
# Loads Telegram tokens from 1Password and runs the monitor script
# Requires 1Password desktop app to be running and signed in

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOKEN_FILE="$SCRIPT_DIR/../data/.telegram_env.sh"

# Check if token file exists and is fresh (< 1 hour from now)
if [ -f "$TOKEN_FILE" ]; then
    # Use stat to check age; mmin in find returns file path regardless
    TOKEN_AGE=$(($(date +%s) - $(stat -f %m "$TOKEN_FILE")))
    if [ "$TOKEN_AGE" -lt 3600 ]; then
        source "$TOKEN_FILE"
    fi
fi

# If tokens not sourced, try 1Password (with timeout to avoid hanging)
if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    # Read from 1Password CLI (requires desktop app integration)
    TELEGRAM_BOT_TOKEN=$(timeout 10 op read "op://Cadai API Keys/Telegram Bot - CadAI Openclaw/bot_token" 2>/dev/null)
    TELEGRAM_CHAT_ID=$(timeout 10 op read "op://Cadai API Keys/Telegram Bot - NLP Sentiment/chat_id" 2>/dev/null)
    
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
        cat > "$TOKEN_FILE" << EOF
export TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN"
export TELEGRAM_CHAT_ID="$TELEGRAM_CHAT_ID"
EOF
    fi
fi

export TELEGRAM_BOT_TOKEN
export TELEGRAM_CHAT_ID

cd "$SCRIPT_DIR/.."
python3 scripts/trump_social_monitor.py 2>&1
