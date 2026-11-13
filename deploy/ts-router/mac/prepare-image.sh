#!/usr/bin/env bash
# Armbian-Image für den Odroid-C4 vor dem ersten Boot vorkonfigurieren (läuft auf dem Mac).
#
#   prepare-image.sh <image.img> <pubkey.pub> [hostname]
#
# Arbeitet nur auf der Image-Datei, nie auf einem Laufwerk. Braucht e2fsprogs
# (brew install e2fsprogs), weil macOS ext4 nicht schreiben kann: Die Root-Partition
# wird aus dem Image kopiert, mit debugfs bearbeitet, geprüft und zurückgeschrieben.
set -euo pipefail

IMG=${1:?Image-Datei (.img, entpackt) angeben}
PUB=${2:?Öffentlichen SSH-Schlüssel (.pub) angeben}
HOST=${3:-ts-router}

E2=/opt/homebrew/opt/e2fsprogs/sbin
DF=$E2/debugfs
FSCK=$E2/e2fsck
[ -x "$DF" ] || { echo "debugfs fehlt: brew install e2fsprogs" >&2; exit 1; }
[ -f "$IMG" ] || { echo "Image nicht gefunden: $IMG" >&2; exit 1; }
grep -qE '^(ssh-(ed25519|rsa)|ecdsa-sha2-)' "$PUB" || { echo "Kein SSH-Public-Key: $PUB" >&2; exit 1; }
[[ $HOST =~ ^[a-z0-9-]{1,63}$ ]] || { echo "Ungültiger Hostname: $HOST" >&2; exit 1; }

# Partition 1 aus der MBR-Tabelle lesen (Start-LBA und Länge, je 4 Byte little endian)
read -r START COUNT < <(od -An -tu4 -j454 -N8 "$IMG")
[ "$START" -gt 0 ] && [ "$COUNT" -gt 0 ] || { echo "Partitionstabelle unlesbar" >&2; exit 1; }
echo "Root-Partition: Sektor $START, $COUNT Sektoren"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
FS=$WORK/root.ext4
dd if="$IMG" of="$FS" bs=512 skip="$START" count="$COUNT" 2>/dev/null
"$FSCK" -fn "$FS" >/dev/null

# Dateien, die ins Image kommen
printf '%s\n' "$HOST" > "$WORK/hostname"
"$DF" -R "cat /etc/hosts" "$FS" 2>/dev/null | sed "s/odroidc4/$HOST/g" > "$WORK/hosts"
grep -q "$HOST" "$WORK/hosts" || printf '127.0.1.1   %s\n' "$HOST" >> "$WORK/hosts"
cp "$PUB" "$WORK/authorized_keys"
cat > "$WORK/sshd.conf" <<'EOF'
# ts-router: nur Schlüssel-Logins. Steht in sshd_config.d und gewinnt damit
# gegen "PermitRootLogin yes" in der Armbian-sshd_config (erster Wert zählt).
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
X11Forwarding no
EOF
# Root-Passwort ("1234" bei Armbian) unbrauchbar machen: "*" sperrt Passwort-Logins,
# Schlüssel-Logins bleiben möglich (anders als bei "!").
"$DF" -R "cat /etc/shadow" "$FS" 2>/dev/null | awk -F: 'BEGIN{OFS=":"} $1=="root"{$2="*"} {print}' > "$WORK/shadow"
grep -q '^root:\*:' "$WORK/shadow" || { echo "shadow nicht anpassbar" >&2; exit 1; }
date -u +"vorbereitet %Y-%m-%dT%H:%MZ mit prepare-image.sh" > "$WORK/marker"

# debugfs "mkdir" auf ein vorhandenes Verzeichnis belegt trotzdem einen Block
# (Doppelbelegung). Deshalb nur anlegen, wenn es fehlt; Dateien vor "write" löschen.
# So kann das Skript mehrfach auf dasselbe Image laufen.
MKDIR_SSH="mkdir /root/.ssh"
"$DF" -R "stat /root/.ssh" "$FS" 2>/dev/null | grep -q 'Inode:' && MKDIR_SSH=""

cat > "$WORK/cmds" <<EOF
rm /etc/hostname
write $WORK/hostname /etc/hostname
sif /etc/hostname mode 0100644
sif /etc/hostname uid 0
sif /etc/hostname gid 0
rm /etc/hosts
write $WORK/hosts /etc/hosts
sif /etc/hosts mode 0100644
sif /etc/hosts uid 0
sif /etc/hosts gid 0
$MKDIR_SSH
sif /root/.ssh mode 040700
sif /root/.ssh uid 0
sif /root/.ssh gid 0
rm /root/.ssh/authorized_keys
write $WORK/authorized_keys /root/.ssh/authorized_keys
sif /root/.ssh/authorized_keys mode 0100600
sif /root/.ssh/authorized_keys uid 0
sif /root/.ssh/authorized_keys gid 0
rm /etc/ssh/sshd_config.d/10-ts-router.conf
write $WORK/sshd.conf /etc/ssh/sshd_config.d/10-ts-router.conf
sif /etc/ssh/sshd_config.d/10-ts-router.conf mode 0100644
sif /etc/ssh/sshd_config.d/10-ts-router.conf uid 0
sif /etc/ssh/sshd_config.d/10-ts-router.conf gid 0
rm /etc/shadow
write $WORK/shadow /etc/shadow
sif /etc/shadow mode 0100640
sif /etc/shadow uid 0
sif /etc/shadow gid 42
rm /root/.not_logged_in_yet
rm /etc/systemd/system/getty@.service.d/override.conf
rmdir /etc/systemd/system/getty@.service.d
rm /etc/systemd/system/serial-getty@.service.d/override.conf
rmdir /etc/systemd/system/serial-getty@.service.d
rm /etc/ts-router-prepared
write $WORK/marker /etc/ts-router-prepared
sif /etc/ts-router-prepared mode 0100644
sif /etc/ts-router-prepared uid 0
sif /etc/ts-router-prepared gid 0
EOF
# Fehlermeldungen für bereits gelöschte/fehlende Dateien sind bei Wiederholung normal
"$DF" -w -f "$WORK/cmds" "$FS" >/dev/null 2>&1 || true
"$FSCK" -fn "$FS" >/dev/null || { echo "Dateisystem nach Bearbeitung fehlerhaft, Image unverändert" >&2; exit 1; }

# Kontrolle vor dem Zurückschreiben
for f in /etc/hostname /etc/ts-router-prepared /root/.ssh/authorized_keys /etc/ssh/sshd_config.d/10-ts-router.conf; do
  "$DF" -R "stat $f" "$FS" 2>/dev/null | grep -q 'Inode:' || { echo "fehlt: $f" >&2; exit 1; }
done
if "$DF" -R "stat /root/.not_logged_in_yet" "$FS" 2>/dev/null | grep -q 'Inode:'; then
  echo "Erst-Login-Marker noch vorhanden" >&2; exit 1
fi

dd if="$FS" of="$IMG" bs=512 seek="$START" conv=notrunc 2>/dev/null
echo "Fertig: $IMG (Hostname $HOST, Schlüssel $(ssh-keygen -lf "$PUB" | awk '{print $2}'))"
