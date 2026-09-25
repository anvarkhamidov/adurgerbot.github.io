#!/usr/bin/env bash
# Deploys or updates the bot on a fresh Ubuntu/Debian/Oracle Linux VPS.
#   curl -fsSL https://raw.githubusercontent.com/anvarkhamidov/adurgerbot.github.io/claude/telegram-video-downloader-bot-00mq5d/deploy.sh | bash
# The script does not touch the firewall or SSH: the bot uses outbound
# connections only (long polling) and needs no open ports.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/anvarkhamidov/adurgerbot.github.io.git}"
BRANCH="${BRANCH:-claude/telegram-video-downloader-bot-00mq5d}"
APP_DIR="${APP_DIR:-$HOME/video-bot}"

SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed. It will be installed from get.docker.com."
    read -r -p "Continue? [y/N] " answer < /dev/tty
    [[ "$answer" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
    curl -fsSL https://get.docker.com | $SUDO sh
    $SUDO systemctl enable --now docker
fi
if ! $SUDO docker compose version >/dev/null 2>&1; then
    echo "The docker compose plugin is missing: install docker-compose-plugin." >&2
    exit 1
fi
command -v git >/dev/null 2>&1 || { $SUDO apt-get update && $SUDO apt-get install -y git; } \
    || $SUDO dnf install -y git

if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$APP_DIR" reset --hard FETCH_HEAD
else
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# Sets KEY=VALUE in .env, replacing an existing (or commented-out) line.
set_env() {
    local key="$1" value="$2"
    if grep -qE "^#?${key}=" .env; then
        sed -i "s|^#\?${key}=.*|${key}=${value}|" .env
    else
        printf '%s=%s\n' "$key" "$value" >> .env
    fi
}
get_env() { sed -n "s/^$1=//p" .env | tail -n 1; }

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    read -r -s -p "BOT_TOKEN from @BotFather: " token < /dev/tty; echo
    read -r -p "Allowed Telegram user IDs, comma-separated (empty = everyone): " users < /dev/tty
    set_env BOT_TOKEN "$token"
    set_env ALLOWED_USERS "$users"
fi

# Asked on every run until enabled, so an existing install can switch too.
if [ -z "$(get_env BOT_API_URL)" ] && [ "${SKIP_LOCAL_API:-0}" != "1" ]; then
    echo
    echo "The cloud Bot API accepts files up to 50 MB only (a 10-minute video fits at ~480p)."
    echo "A local Bot API server lifts the limit to 2000 MB. It needs api_id and api_hash"
    echo "from https://my.telegram.org -> API development tools."
    read -r -p "TELEGRAM_API_ID (empty = keep the 50 MB limit): " api_id < /dev/tty
    if [ -n "$api_id" ]; then
        if ! [[ "$api_id" =~ ^[0-9]+$ ]]; then
            echo "api_id must be a number" >&2
            exit 1
        fi
        read -r -s -p "TELEGRAM_API_HASH: " api_hash < /dev/tty; echo
        set_env TELEGRAM_API_ID "$api_id"
        set_env TELEGRAM_API_HASH "$api_hash"
        set_env COMPOSE_PROFILES local-api
        set_env BOT_API_URL http://telegram-bot-api:8081
        # A bot must log out of the cloud API before a local server can serve it
        # (afterwards the cloud API refuses it for ~10 minutes).
        if curl -fsS "https://api.telegram.org/bot$(get_env BOT_TOKEN)/logOut" >/dev/null; then
            echo "Bot logged out of the cloud Bot API."
        else
            echo "logOut failed (already logged out?) - continuing."
        fi
    fi
fi

$SUDO docker compose up -d --build --remove-orphans
$SUDO docker compose ps
echo
echo "Logs: cd $APP_DIR && $SUDO docker compose logs -f bot"
