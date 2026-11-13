#!/usr/bin/env bash
# Image auf die eMMC schreiben (läuft auf dem Mac, fragt nach dem sudo-Passwort).
#
#   flash.sh <image.img> <diskN>
#
# Verweigert interne, virtuelle und nicht ~16-GB-Laufwerke und verlangt,
# dass der Laufwerksname zur Bestätigung noch einmal eingetippt wird.
set -euo pipefail

IMG=${1:?Image-Datei angeben}
DISK=${2:?Laufwerk angeben, z. B. disk4}
[[ $DISK =~ ^disk[0-9]+$ ]] || { echo "Erwartet diskN, nicht '$DISK'" >&2; exit 1; }
[ "$DISK" != disk0 ] || { echo "disk0 ist die interne Platte" >&2; exit 1; }
[ -f "$IMG" ] || { echo "Image nicht gefunden: $IMG" >&2; exit 1; }

info() { diskutil info -plist "/dev/$DISK" | plutil -extract "$1" raw -o - - 2>/dev/null || echo "?"; }
INTERNAL=$(info Internal)
KIND=$(info VirtualOrPhysical)
WHOLE=$(info WholeDisk)
SIZE=$(info Size)

[ "$WHOLE" = true ] || { echo "/dev/$DISK ist kein ganzes Laufwerk" >&2; exit 1; }
[ "$INTERNAL" = false ] || { echo "/dev/$DISK ist intern (Internal=$INTERNAL) – Abbruch" >&2; exit 1; }
[ "$KIND" = Physical ] || { echo "/dev/$DISK ist nicht physisch ($KIND) – Abbruch" >&2; exit 1; }
if [ "$SIZE" -lt 14000000000 ] || [ "$SIZE" -gt 17200000000 ]; then
  echo "/dev/$DISK hat $SIZE Byte, erwartet ~16 GB – Abbruch" >&2; exit 1
fi
[ "$(stat -f %z "$IMG")" -lt "$SIZE" ] || { echo "Image größer als Laufwerk" >&2; exit 1; }

diskutil list "/dev/$DISK"
echo
echo "ALLE Daten auf /dev/$DISK ($SIZE Byte, extern, physisch) werden überschrieben."
read -r -p "Zur Bestätigung '$DISK' eintippen: " CONFIRM
[ "$CONFIRM" = "$DISK" ] || { echo "Abgebrochen."; exit 1; }

diskutil unmountDisk "/dev/$DISK"
# rdisk = Raw-Gerät, deutlich schneller als /dev/diskN
sudo dd if="$IMG" of="/dev/r$DISK" bs=4m status=progress
sync
diskutil eject "/dev/$DISK"
echo "Fertig. eMMC in den Odroid stecken, Netzwerkkabel dran, Strom an."
