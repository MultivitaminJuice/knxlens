#!/usr/bin/env bash
# Tailscale-Subnetz-Router auf dem Odroid einrichten (läuft als root auf dem Gerät).
# Idempotent: kann beliebig oft laufen.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Als root ausführen" >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
AUTHKEY_FILE=${AUTHKEY_FILE:-/run/ts-authkey}
step() { printf '\n== %s\n' "$*"; }

# shellcheck disable=SC1091
. /etc/os-release

step "1/7 Pakete: Tailscale aus dem offiziellen Repo, networkd-dispatcher für den Netzwerk-Up-Hook"
install -d -m 0755 /usr/share/keyrings
curl -fsSL "https://pkgs.tailscale.com/stable/debian/${VERSION_CODENAME}.noarmor.gpg" \
  -o /usr/share/keyrings/tailscale-archive-keyring.gpg
curl -fsSL "https://pkgs.tailscale.com/stable/debian/${VERSION_CODENAME}.tailscale-keyring.list" \
  -o /etc/apt/sources.list.d/tailscale.list
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tailscale networkd-dispatcher jq ethtool

step "2/7 IP-Forwarding (sonst verwirft der Kernel Pakete zwischen tailscale0 und dem LAN)"
install -m 0644 "$HERE/etc/sysctl.d/99-tailscale.conf" /etc/sysctl.d/99-tailscale.conf
sysctl -q -p /etc/sysctl.d/99-tailscale.conf

step "3/7 Skripte, Konfiguration und systemd-Units"
install -m 0755 "$HERE/bin/ts-advertise-routes" /usr/local/sbin/ts-advertise-routes
install -m 0755 "$HERE/bin/ts-watchdog" /usr/local/sbin/ts-watchdog
install -m 0755 "$HERE/bin/ts-knx-relay" /usr/local/sbin/ts-knx-relay
[ -e /etc/default/ts-router ] || install -m 0644 "$HERE/etc/default/ts-router" /etc/default/ts-router
for u in ts-advertise-routes.service ts-advertise-routes.timer ts-watchdog.service ts-watchdog.timer ts-knx-relay.service; do
  install -m 0644 "$HERE/systemd/$u" "/etc/systemd/system/$u"
done
install -d /etc/systemd/system/tailscaled.service.d
install -m 0644 "$HERE/systemd/tailscaled-restart.conf" /etc/systemd/system/tailscaled.service.d/10-restart.conf
install -d /etc/networkd-dispatcher/routable.d
install -m 0755 "$HERE/etc/networkd-dispatcher/50-ts-router" /etc/networkd-dispatcher/routable.d/50-ts-router

step "4/7 Hardware-Watchdog (Neustart, falls das System selbst hängt)"
if [ -e /dev/watchdog0 ] || [ -e /dev/watchdog ]; then
  install -d /etc/systemd/system.conf.d
  install -m 0644 "$HERE/systemd/watchdog.conf" /etc/systemd/system.conf.d/10-watchdog.conf
  echo "aktiv nach dem nächsten Neustart (oder: systemctl daemon-reexec)"
else
  echo "kein /dev/watchdog gefunden, übersprungen"
fi

step "5/7 Dienste beim Boot aktivieren"
systemctl daemon-reload
systemctl enable --now tailscaled networkd-dispatcher
systemctl enable --now ts-advertise-routes.timer ts-watchdog.timer
# Restart picks up a new relay version on redeploy
systemctl enable ts-knx-relay.service
systemctl restart ts-knx-relay.service
systemctl restart networkd-dispatcher

step "6/7 Bei Tailscale anmelden"
state=$(tailscale status --json 2>/dev/null | jq -r '.BackendState // ""' || true)
if [ "$state" = Running ]; then
  echo "schon angemeldet, setze nur die Optionen"
  tailscale set --ssh --accept-dns=false
elif [ -s "$AUTHKEY_FILE" ]; then
  # "file:" liest den Key aus der Datei, damit er nicht in der Prozessliste steht
  if tailscale up --help 2>&1 | grep -q 'file:'; then
    tailscale up --auth-key="file:$AUTHKEY_FILE" --ssh --accept-dns=false
  else
    tailscale up --auth-key="$(cat "$AUTHKEY_FILE")" --ssh --accept-dns=false
  fi
else
  echo "Kein Auth-Key in $AUTHKEY_FILE und nicht angemeldet. Mit TS_AUTHKEY über deploy.sh erneut starten." >&2
  exit 1
fi
rm -f "$AUTHKEY_FILE"

step "7/7 Subnetz ermitteln und anbieten"
/usr/local/sbin/ts-advertise-routes
echo
tailscale status
echo
echo "Fertig. Route in der Admin-Konsole freigeben (Machines → $(hostname) → Edit route settings)."
