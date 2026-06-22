#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/drebolbot"
SERVICE_NAME="drebolbot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
REPO_URL="https://github.com/pratokwau/drebolbot.git"
APT_PACKAGES=(git python3 python3-venv python3-pip)

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer as root."
  exit 1
fi

mkdir -p "$ROOT"

ensure_apt_packages() {
  local missing=()
  for pkg in "${APT_PACKAGES[@]}"; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
      missing+=("$pkg")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    echo "Installing system packages: ${missing[*]}"
    apt-get update
    apt-get install -y "${missing[@]}"
  fi
}

if [[ ! -d "$ROOT/.git" ]]; then
  if [[ -n "$(ls -A "$ROOT" 2>/dev/null || true)" ]]; then
    echo "$ROOT exists and is not a git repo."
    echo "Remove it or clone the repository there manually, then rerun this script."
    exit 1
  fi
  git clone "$REPO_URL" "$ROOT"
fi

python3 "$ROOT/install/install.py"

ensure_apt_packages

if [[ ! -d "$ROOT/.venv" ]]; then
  python3 -m venv "$ROOT/.venv"
fi

"$ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Drebolbot Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python $ROOT/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/usr/bin/test -x $ROOT/.venv/bin/python

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "Installed and started: $SERVICE_NAME"
