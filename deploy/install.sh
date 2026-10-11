#!/usr/bin/env bash
# Установка на чистый сервер Ubuntu/Debian одной командой:
#   curl -fsSL https://raw.githubusercontent.com/tupamango007/telegrambott/main/deploy/install.sh | bash
set -euo pipefail

REPO="${REPO:-https://github.com/tupamango007/telegrambott.git}"
BRANCH="${BRANCH:-main}"
DIR="${DIR:-$HOME/telegrambott}"

if ! command -v docker >/dev/null 2>&1; then
    echo "==> Устанавливаю Docker"
    curl -fsSL https://get.docker.com | sudo sh
fi
command -v git >/dev/null 2>&1 || { sudo apt-get update -y && sudo apt-get install -y git; }

if [ -d "$DIR/.git" ]; then
    echo "==> Обновляю $DIR"
    git -C "$DIR" fetch origin "$BRANCH" && git -C "$DIR" checkout "$BRANCH" && git -C "$DIR" pull --ff-only origin "$BRANCH"
else
    echo "==> Клонирую в $DIR"
    git clone --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"

mkdir -p config data
[ -f config/replies.toml ] || cp replies.example.toml config/replies.toml

if [ ! -f .env ]; then
    cp .env.example .env
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
        token="$TELEGRAM_BOT_TOKEN"
    else
        read -rp "Токен бота от @BotFather: " token < /dev/tty
    fi
    sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$token|" .env
fi

echo "==> Собираю и запускаю"
sudo docker compose up -d --build
sudo docker compose ps
echo
echo "Готово. Логи: cd $DIR && sudo docker compose logs -f"
echo "Шаблоны ответов: $DIR/config/replies.toml (правки применяются без перезапуска)"
