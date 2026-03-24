#!/bin/bash
set -e

# ─── WebLeadScraper — one-shot deploy script ───────────────────────────────
# Działa na czystym Ubuntu 22.04 / 24.04 VPS.
# Użycie: bash deploy.sh

REPO_URL="${1:-https://github.com/soft-synergy/scraper.git}"
APP_DIR="/opt/scraper"

echo "==> [1/5] Instalacja Dockera"
if ! command -v docker &>/dev/null; then
    apt-get update -q
    apt-get install -y -q ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    echo "    Docker zainstalowany: $(docker --version)"
else
    echo "    Docker już zainstalowany: $(docker --version)"
fi

echo "==> [2/5] Klonowanie / aktualizacja repo"
if [ -d "$APP_DIR/.git" ]; then
    echo "    Repo już istnieje — git pull"
    git -C "$APP_DIR" pull
else
    if [ -z "$REPO_URL" ]; then
        echo "BŁĄD: Podaj URL repo jako argument: bash deploy.sh https://github.com/user/repo.git"
        exit 1
    fi
    git clone "$REPO_URL" "$APP_DIR"
fi

echo "==> [3/5] Konfiguracja .env"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ""
    echo "  ┌─────────────────────────────────────────────────────────────┐"
    echo "  │  UZUPEŁNIJ .env przed kontynuacją!                          │"
    echo "  │  nano $APP_DIR/.env                                         │"
    echo "  │                                                              │"
    echo "  │  Wymagane:                                                   │"
    echo "  │    JWT_SECRET_KEY  — wygeneruj: openssl rand -hex 32        │"
    echo "  │    OPENROUTER_API_KEY — klucz do generowania maili          │"
    echo "  └─────────────────────────────────────────────────────────────┘"
    echo ""
    read -p "  Naciśnij Enter żeby otworzyć nano, lub Ctrl+C żeby wyjść..."
    nano "$APP_DIR/.env"
else
    echo "    .env już istnieje — pomijam"
fi

echo "==> [4/5] Tworzenie katalogu na bazę danych"
mkdir -p "$APP_DIR/data"

echo "==> [5/5] Build i start kontenera"
cd "$APP_DIR"
docker compose down 2>/dev/null || true
docker compose build --no-cache
docker compose up -d

echo ""
echo "  ✓ Aplikacja działa na http://$(curl -s ifconfig.me 2>/dev/null || echo 'IP_SERWERA'):8000"
echo ""
echo "  Przydatne komendy:"
echo "    Logi:     docker compose -f $APP_DIR/docker-compose.yml logs -f"
echo "    Restart:  docker compose -f $APP_DIR/docker-compose.yml restart"
echo "    Update:   git -C $APP_DIR pull && docker compose -f $APP_DIR/docker-compose.yml up -d --build"
echo ""
