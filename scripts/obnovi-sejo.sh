#!/usr/bin/env bash
# Interactively renew UL SSO on the laptop; never store credentials in FE.
set -euo pipefail

NAS_USER="${FE_NAS_USER:-admin}"
NAS_HOST="${FE_NAS_HOST:-192.168.1.120}"
PROBE="${FE_PROBE_PATH:-$HOME/Downloads/fe_moodle_probe.py}"
PYTHON="${FE_PYTHON:-$HOME/Downloads/.venv/bin/python}"
COOKIE="$HOME/.local/share/fe-moodle-diagnostika/browser-profile/moodle-session-cookies.json"
SECRET="/mnt/tank/apps/fe-moodle-sync/secrets/moodle-session-cookies.json"
CONTAINER="ix-fe-moodle-sync-fe-moodle-sync-1"

if [[ ! -f "$PROBE" || ! -x "$PYTHON" ]]; then
  echo "Manjka $PROBE ali $PYTHON; obnovi FE Moodle Diagnostika v1.0.6 v ~/Downloads." >&2
  exit 1
fi

echo "1/4: Prijavi se v odprtem Chromiumu prek UL SSO in potrdi z ENTER."
"$PYTHON" "$PROBE" browser-login

echo "2/4: Preverjam prijavo prek bralne Moodle funkcije ..."
"$PYTHON" "$PROBE" browser-courses >/dev/null
[[ -f "$COOKIE" ]] || { echo "Piškotki niso bili ustvarjeni." >&2; exit 1; }

TARGET="${NAS_USER}@${NAS_HOST}"
echo "3/4: Prek SSH varno prenašam obnovljeno sejo (0600) ..."
scp -p -- "$COOKIE" "$TARGET:~/moodle-session-cookies.json"

echo "4/4: Obstoječo skrivnost posodabljam brez menjave inode Docker bind mounta ..."
ssh -tt "$TARGET" "set -eu
  sudo test -f '$SECRET'
  sudo cp \"\$HOME/moodle-session-cookies.json\" '$SECRET'
  sudo chmod 600 '$SECRET'
  rm -f \"\$HOME/moodle-session-cookies.json\"
  sudo docker restart '$CONTAINER' >/dev/null
  sudo docker exec '$CONTAINER' python -c 'from fe_sync.client import MoodleClient; c=MoodleClient(\"/run/secrets/moodle-session-cookies.json\"); print(\"Prijava uspešna:\",len(c.courses()),\"predmetov\")'
"

echo "Seja je osvežena; TrueNAS ponovno preverja spletno učilnico."
