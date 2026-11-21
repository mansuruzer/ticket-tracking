#!/usr/bin/env bash
# Ubuntu / Oracle Linux sunucuda tek komutla kurulum (Oracle Cloud Always Free için yazıldı).
# Kullanım:  curl ... | bash   ya da   bash deploy/setup.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/mansuruzer/ticket-tracking.git}"
APP_DIR=/opt/ucuzgez

if command -v apt-get >/dev/null; then
  sudo apt-get update -y && sudo apt-get install -y python3 python3-venv git
else
  sudo dnf install -y python3 python3-pip git   # Oracle Linux
fi

sudo mkdir -p "$APP_DIR" && sudo chown "$USER" "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then git -C "$APP_DIR" pull; else git clone "$REPO_URL" "$APP_DIR"; fi
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo ">> $APP_DIR/.env dosyasını düzenle (token'lar)!"; }

# 1 GB RAM'li Micro makinede güvenlik için 1 GB swap
if [ ! -f /swapfile ] && [ "$(free -m | awk '/Mem:/{print $2}')" -lt 2000 ]; then
  sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

sed "s#__USER__#$USER#; s#__DIR__#$APP_DIR#" deploy/ucuzgez.service | sudo tee /etc/systemd/system/ucuzgez.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now ucuzgez
echo "Kuruldu. Durum: sudo systemctl status ucuzgez · Log: journalctl -u ucuzgez -f"
echo "Kullanıcı ekle: cd $APP_DIR && .venv/bin/python -m ucuzgez.cli add-user \"İsim\""
