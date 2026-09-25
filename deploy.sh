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

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    read -r -s -p "BOT_TOKEN from @BotFather: " token < /dev/tty; echo
    read -r -p "Allowed Telegram user IDs, comma-separated (empty = everyone): " users < /dev/tty
    sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${token}|; s|^ALLOWED_USERS=.*|ALLOWED_USERS=${users}|" .env
fi

$SUDO docker compose up -d --build --remove-orphans
$SUDO docker compose ps
echo
echo "Logs: cd $APP_DIR && $SUDO docker compose logs -f bot"
