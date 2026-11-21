#!/usr/bin/env bash
# Günlük SQLite yedeği (crontab: 30 3 * * * /opt/ucuzgez/deploy/backup.sh)
set -euo pipefail
cd /opt/ucuzgez
mkdir -p backups
sqlite3 ucuzgez.db ".backup backups/ucuzgez-$(date +%F).db" 2>/dev/null \
  || .venv/bin/python -c "import sqlite3,sys;s=sqlite3.connect('ucuzgez.db');d=sqlite3.connect(sys.argv[1]);s.backup(d)" "backups/ucuzgez-$(date +%F).db"
ls -1t backups/*.db | tail -n +15 | xargs -r rm --
