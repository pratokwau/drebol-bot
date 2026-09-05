#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/drebol-bot"
BOT_SERVICE="drebol-bot"
WEB_SERVICE="drebol-web"
WEB_PORT=8090
REPO_URL="https://github.com/pratokwau/drebol-bot.git"
APT_PACKAGES=(git python3 python3-pip nginx certbot python3-certbot-nginx curl openssl)

RESET="\033[0m"; BOLD="\033[1m"; DIM="\033[2m"
CYAN="\033[36m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"

step()  { echo -e "\n${BOLD}${CYAN}▸ $1${RESET}"; }
ok()    { echo -e "  ${GREEN}✓${RESET} $1"; }
warn()  { echo -e "  ${YELLOW}!${RESET} $1"; }
die()   { echo -e "  ${RED}✗ $1${RESET}"; exit 1; }
ask()   {
  local __resultvar=$1 __prompt=$2 __default=${3:-} __required=${4:-}
  local __value
  while true; do
    if [[ -n "$__default" ]]; then
      read -r -p "  $(echo -e "${CYAN}›${RESET}") $__prompt [$__default]: " __value
      __value="${__value:-$__default}"
    else
      read -r -p "  $(echo -e "${CYAN}›${RESET}") $__prompt: " __value
    fi
    if [[ -n "$__value" || -z "$__required" ]]; then
      printf -v "$__resultvar" '%s' "$__value"
      return
    fi
    echo -e "  ${YELLOW}Это поле обязательно.${RESET}"
  done
}
ask_secret() {
  local __resultvar=$1 __prompt=$2
  local __value
  read -r -s -p "  $(echo -e "${CYAN}›${RESET}") $__prompt: " __value
  echo
  printf -v "$__resultvar" '%s' "$__value"
}

if [[ $EUID -ne 0 ]]; then
  die "Запусти установщик от root."
fi

echo -e "\n${BOLD}  ⚡ Drebolbot — установка на сервер${RESET}"
echo -e "  ${DIM}Telegram-бот + веб-панель + Nginx + SSL — всё в одном скрипте.${RESET}"
echo -e "  ${DIM}────────────────────────────────────────────────────────────${RESET}"

step "Домен и доступ к веб-панели"
ask DOMAIN "Домен сайта (A-запись должна уже указывать на этот сервер)" "" required
ask WEB_USERNAME "Логин веб-панели" "admin"
ask_secret WEB_PASSWORD "Пароль веб-панели (Enter — сгенерировать случайный)"
if [[ -z "$WEB_PASSWORD" ]]; then
  WEB_PASSWORD="$(openssl rand -base64 12 | tr -d '=+/' | cut -c1-16)"
  ok "Сгенерирован пароль: ${BOLD}${WEB_PASSWORD}${RESET} ${DIM}(сохрани его!)${RESET}"
fi
ask EMAIL "Email для SSL-сертификата (Let's Encrypt)" "admin@${DOMAIN}"

step "Telegram-бот"
ask TOKEN "Токен бота (от @BotFather)" "" required
ask ADMIN_ID "Твой Telegram ID (админ)" "" required

step "Системные пакеты"
echo "Устанавливаю часовой пояс Europe/Moscow..."
timedatectl set-timezone Europe/Moscow

missing=()
for pkg in "${APT_PACKAGES[@]}"; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if (( ${#missing[@]} > 0 )); then
  echo "Ставлю: ${missing[*]}"
  apt-get update -qq
  apt-get install -y "${missing[@]}" >/dev/null
fi

py_ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
py_pkg="python${py_ver}-venv"
if ! dpkg -s "$py_pkg" >/dev/null 2>&1; then
  apt-get install -y "$py_pkg" >/dev/null
fi
ok "Пакеты установлены"

step "Проверка DNS"
SERVER_IP="$(curl -fsSL -4 ifconfig.me 2>/dev/null || echo "")"
DOMAIN_IP="$(python3 -c "import socket,sys
try:
    print(socket.gethostbyname(sys.argv[1]))
except Exception:
    print('')" "$DOMAIN")"
if [[ -z "$SERVER_IP" || -z "$DOMAIN_IP" ]]; then
  warn "Не удалось проверить DNS автоматически — продолжаю без проверки."
elif [[ "$SERVER_IP" != "$DOMAIN_IP" ]]; then
  warn "Домен $DOMAIN указывает на $DOMAIN_IP, а сервер — $SERVER_IP."
  warn "Если A-запись ещё не обновилась, получение SSL-сертификата ниже может не сработать."
  ask CONTINUE_ANYWAY "Продолжить всё равно? (y/n)" "y"
  if [[ "$CONTINUE_ANYWAY" != "y" ]]; then
    die "Установка прервана — обнови DNS и запусти скрипт заново."
  fi
else
  ok "DNS настроен верно ($DOMAIN → $SERVER_IP)"
fi

step "Код проекта"
for svc in "$BOT_SERVICE" "$WEB_SERVICE"; do
  if systemctl list-unit-files | grep -q "^${svc}\.service"; then
    systemctl stop "$svc" >/dev/null 2>&1 || true
    systemctl disable "$svc" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/${svc}.service"
  fi
done
systemctl daemon-reload || true
rm -rf "$ROOT"
git clone -q "$REPO_URL" "$ROOT"
ok "Репозиторий склонирован в $ROOT"

step "Конфигурация"
python3 "$ROOT/install.py" \
  --domain "$DOMAIN" \
  --web-username "$WEB_USERNAME" \
  --web-password "$WEB_PASSWORD" \
  --token "$TOKEN" \
  --admin-id "$ADMIN_ID"

step "Python-окружение и зависимости"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel -q
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt" -q
ok "Зависимости установлены"

step "Systemd-сервисы"
cat > "/etc/systemd/system/${BOT_SERVICE}.service" <<EOF
[Unit]
Description=Drebolbot Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python $ROOT/main.py
Restart=always
RestartSec=1
TimeoutStartSec=20
TimeoutStopSec=5
KillMode=mixed
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

cat > "/etc/systemd/system/${WEB_SERVICE}.service" <<EOF
[Unit]
Description=Drebolbot Web Panel
After=network.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port ${WEB_PORT}
Restart=always
RestartSec=1
TimeoutStartSec=20
TimeoutStopSec=5
KillMode=mixed
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

mkdir -p "$ROOT/data"
systemctl daemon-reload
systemctl enable "$BOT_SERVICE" "$WEB_SERVICE" >/dev/null
systemctl restart "$BOT_SERVICE"
systemctl restart "$WEB_SERVICE"
ok "Сервисы $BOT_SERVICE и $WEB_SERVICE запущены"

step "Nginx"
cat > "/etc/nginx/sites-available/${WEB_SERVICE}" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:${WEB_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf "/etc/nginx/sites-available/${WEB_SERVICE}" "/etc/nginx/sites-enabled/${WEB_SERVICE}"
rm -f /etc/nginx/sites-enabled/default
nginx -t >/dev/null 2>&1 && systemctl reload nginx
ok "Nginx настроен для $DOMAIN"

step "SSL-сертификат (Let's Encrypt)"
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect -m "$EMAIL" >/tmp/certbot.log 2>&1; then
  ok "Сертификат получен, HTTPS включён"
else
  warn "Не удалось выпустить сертификат автоматически (см. /tmp/certbot.log)."
  warn "Сайт всё равно доступен по http://${DOMAIN} — можно повторить позже:"
  warn "certbot --nginx -d ${DOMAIN} --agree-tos -m ${EMAIL}"
fi

echo
echo -e "  ${DIM}────────────────────────────────────────────────────────────${RESET}"
echo -e "${BOLD}${GREEN}  ✓ Установка завершена${RESET}"
echo -e "  ${DIM}────────────────────────────────────────────────────────────${RESET}"
echo -e "  Сайт:        ${BOLD}https://${DOMAIN}${RESET}"
echo -e "  Логин:       ${BOLD}${WEB_USERNAME}${RESET}"
echo -e "  Пароль:      ${BOLD}${WEB_PASSWORD}${RESET}"
echo -e "  Бот:         ${GREEN}●${RESET} $(systemctl is-active "$BOT_SERVICE" 2>/dev/null || echo unknown)"
echo -e "  Веб-панель:  ${GREEN}●${RESET} $(systemctl is-active "$WEB_SERVICE" 2>/dev/null || echo unknown)"
echo -e "  Логи бота:   ${DIM}journalctl -u ${BOT_SERVICE} -f${RESET}"
echo -e "  Логи сайта:  ${DIM}journalctl -u ${WEB_SERVICE} -f${RESET}"
echo
