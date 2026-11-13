#!/usr/bin/env bash
# Router-Setup per SSH auf den Odroid bringen und ausführen (läuft auf dem Mac).
#
#   TS_AUTHKEY=tskey-auth-... deploy.sh <ip-oder-hostname>
#
# Der Auth-Key geht über stdin der SSH-Verbindung in eine Datei auf tmpfs (/run),
# taucht also weder in Prozesslisten noch auf der eMMC noch im Repo auf.
# setup.sh löscht ihn nach "tailscale up".
set -euo pipefail

HOST=${1:?IP oder Hostname des Odroid angeben}
HERE=$(cd "$(dirname "$0")/.." && pwd)
SSH=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@$HOST")

"${SSH[@]}" 'rm -rf /root/ts-router && mkdir -p /root/ts-router'
tar -C "$HERE/device" -cf - . | "${SSH[@]}" 'tar -C /root/ts-router -xf -'

if [ -n "${TS_AUTHKEY:-}" ]; then
  printf '%s\n' "$TS_AUTHKEY" | "${SSH[@]}" 'umask 077; cat > /run/ts-authkey'
else
  echo "Hinweis: TS_AUTHKEY ist nicht gesetzt. Ohne Key läuft das Setup nur, wenn das Gerät schon angemeldet ist."
fi

"${SSH[@]}" 'bash /root/ts-router/setup.sh'
