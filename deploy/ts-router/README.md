# ts-router: Odroid-C4 als Tailscale-Subnetz-Router für die Fernwartung

Der Odroid kommt in ein fremdes Netz, holt sich per DHCP eine Adresse, meldet sich bei
Tailscale an und bietet das lokale IPv4-Subnetz als Route an. Vom Mac aus sind die Geräte
in diesem LAN dann über Tailscale erreichbar, insbesondere das KNXnet/IP-Interface
(UDP 3671) für knxlens.

```
Mac (knxlens) ──Tailscale──► ts-router (Odroid, eth0 per DHCP) ──LAN──► KNX-IP-Interface
     100.x.y.z                100.a.b.c / 192.168.x.y (SNAT)              192.168.x.50:3671
```

## Dateien

| Pfad | Läuft auf | Zweck |
|---|---|---|
| `mac/prepare-image.sh` | Mac | Image vor dem ersten Boot vorkonfigurieren (Hostname, SSH-Key, keine Passwörter) |
| `mac/flash.sh` | Mac | Image auf die eMMC schreiben, mit Sicherheitsprüfungen |
| `mac/deploy.sh` | Mac | `device/` per SSH übertragen, Auth-Key sicher übergeben, `setup.sh` starten |
| `device/setup.sh` | Odroid | Tailscale installieren und einrichten (idempotent) |
| `device/bin/ts-advertise-routes` | Odroid | Subnetz des Default-Interface erkennen und anbieten |
| `device/bin/ts-watchdog` | Odroid | Verbindung prüfen, gestuft reparieren |
| `device/etc/default/ts-router` | Odroid | Einstellungen (feste Routen, 4via6, Watchdog) |
| `device/systemd/*` | Odroid | Timer, Services, Restart- und Watchdog-Drop-ins |
| `tools/knx-probe.py` | Mac | KNXnet/IP-Interface per Unicast abfragen (Test für UDP 3671) |

Im Repo liegt kein Geheimnis. Der Auth-Key kommt nur aus der Umgebungsvariable `TS_AUTHKEY`.

---

## 1. Image

**Quelle:** Armbian, Board-Seite <https://www.armbian.com/odroid-c4/>, Abschnitt
„Recommended Images“ (vom Armbian-Team getestet). Verwendet:

- `Armbian_26.8.1_Odroidc4_trixie_current_6.18.43_minimal.img.xz` (Debian 13 „Trixie“, Kernel 6.18, Minimal/CLI, Build 08.08.2026)
- Download: `https://dl.armbian.com/odroidc4/Trixie_current_minimal` (leitet auf einen Mirror um), dazu `.sha` und `.asc`
- SHA-256: `3f807fa24218809d2fa1399890d278dbd178eadc2afd25be548ec886d54ac1fa`
- Signiert von Igor Pecovnik (Armbian), Fingerabdruck `DF00 FAF1 C577 104B 50BF 1D00 93D6 889F 9F0E 78D5`

Warum Armbian statt Hardkernel-Ubuntu: aktueller Mainline-Kernel, regelmäßige Updates,
signierte Images, schlankes Minimal-Image. Das Image hat **eine** ext4-Partition
(ab Sektor 8192), `/boot` liegt darin. DHCP für alle `e*`-Interfaces ist per Netplan/systemd-networkd
schon voreingestellt.

```bash
mkdir -p ~/ts-router-img && cd ~/ts-router-img
curl -fL -o armbian.img.xz     https://dl.armbian.com/odroidc4/Trixie_current_minimal
curl -fL -o armbian.img.xz.sha https://dl.armbian.com/odroidc4/Trixie_current_minimal.sha
curl -fL -o armbian.img.xz.asc https://dl.armbian.com/odroidc4/Trixie_current_minimal.asc
# Prüfsumme: beide Zeilen müssen gleich sein
cut -d' ' -f1 armbian.img.xz.sha; shasum -a 256 armbian.img.xz | cut -d' ' -f1
# Signatur in einem eigenen GnuPG-Verzeichnis prüfen
export GNUPGHOME=$PWD/gnupg; mkdir -m 700 -p "$GNUPGHOME"
curl -fsSL 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xDF00FAF1C577104B50BF1D0093D6889F9F0E78D5' | gpg --import
gpg --verify armbian.img.xz.asc armbian.img.xz     # "Korrekte Signatur von Igor Pecovnik"
xz -dk armbian.img.xz
```

### Vorkonfiguration vor dem ersten Boot

```bash
brew install e2fsprogs
deploy/ts-router/mac/prepare-image.sh ~/ts-router-img/armbian.img ~/.ssh/id_rsa.pub ts-router
```

macOS kann ext4 nicht schreiben. Das Skript kopiert deshalb die Root-Partition aus der
Image-Datei, bearbeitet sie mit `debugfs`, prüft sie mit `e2fsck` und schreibt sie zurück.
Ein Laufwerk wird dabei nicht angefasst. Änderungen und Gründe:

| Änderung | Grund |
|---|---|
| `/etc/hostname`, `/etc/hosts` → `ts-router` | Name im Tailnet und im fremden LAN (DHCP-Hostname) |
| `/root/.ssh/authorized_keys` mit deinem Key | Zugang ab dem ersten Boot ohne Passwort |
| `/etc/ssh/sshd_config.d/10-ts-router.conf` | `PasswordAuthentication no`, `PermitRootLogin prohibit-password`. Steht vor der Armbian-Zeile `PermitRootLogin yes` und gewinnt deshalb |
| Root-Passwort in `/etc/shadow` → `*` | Armbian setzt `1234`. `*` sperrt jede Passwort-Anmeldung, Schlüssel gehen weiter |
| `/root/.not_logged_in_yet` gelöscht | Sonst startet beim ersten Login der interaktive Armbian-Assistent (neues Passwort, Benutzer anlegen) |
| getty-Overrides gelöscht | Armbian meldet root an HDMI und seriell **automatisch ohne Passwort** an, bis der Assistent gelaufen ist. Bei einem Gerät in fremden Räumen nicht akzeptabel |

Folge: Es gibt **keinen** Konsolen-Login mehr. Notzugang nur per SSH/Tailscale SSH oder eMMC
ausbauen und neu flashen.

SSH-Host-Keys: Das Image enthält Keys, die für alle Armbian-Downloads gleich sind.
`armbian-firstrun` erzeugt beim ersten Boot neue (`OPENSSHD_REGENERATE_HOST_KEYS=true`).
Deshalb nach dem Einschalten ~1 Minute warten, bevor du dich zum ersten Mal verbindest.

## 2. Flashen

Vorher immer prüfen, welches Laufwerk die eMMC ist:

```bash
diskutil list external physical
```

```bash
deploy/ts-router/mac/flash.sh ~/ts-router-img/armbian.img disk4   # diskN aus der Liste
```

`flash.sh` bricht ab, wenn das Laufwerk intern, virtuell, kein ganzes Laufwerk oder nicht
14–17,2 GB groß ist. Danach zeigt es `diskutil list` für das Ziel und verlangt, den Namen
noch einmal einzutippen. `dd` läuft mit `sudo` (Passwort gibst du ein) auf `/dev/rdiskN`
(Raw-Gerät, schneller). Am Ende wirft es das Laufwerk aus.

## 3. Erster Boot

eMMC einsetzen, Netzwerkkabel, Strom. Armbian vergrößert das Dateisystem beim ersten Boot
auf die volle eMMC und startet ggf. einmal neu. IP-Adresse im DHCP des Routers nachsehen
oder:

```bash
ping -c1 ts-router.local          # falls mDNS im Netz geht
ssh root@<ip>                     # nur mit Key, Passwort wird abgelehnt
```

## 4. Tailscale einrichten

### Voraussetzungen in der Admin-Konsole

- **Auth-Key** (Settings → Keys → Generate auth key): „Pre-approved“, falls Device Approval
  aktiv ist. Einmalig reicht. Ohne Tag gehört das Gerät deinem Benutzer.
- Nach der Anmeldung beim Gerät **„Disable key expiry“** setzen. Sonst läuft der Node-Key
  nach 180 Tagen ab und das Gerät ist im fremden Netz nicht mehr erreichbar.
- Am Mac in der Tailscale-App **„Use Tailscale subnets“** einschalten (CLI: `--accept-routes`),
  sonst nutzt der Mac die angebotenen Routen nicht.

### Ausführen

```bash
export TS_AUTHKEY=tskey-auth-...    # nur in der Shell, nicht in Dateien
deploy/ts-router/mac/deploy.sh <ip>
```

`deploy.sh` überträgt `device/` nach `/root/ts-router`, schreibt den Key über stdin nach
`/run/ts-authkey` (tmpfs, Modus 600, nicht auf der eMMC, nicht in `ps`) und startet
`setup.sh`. Was `setup.sh` tut und warum:

1. **Tailscale aus dem offiziellen Paket-Repo** (`pkgs.tailscale.com/stable/debian/trixie`):
   kommt mit `apt upgrade` aktuell, Paketsignatur wird geprüft. Dazu `networkd-dispatcher`
   für den Netzwerk-Up-Hook.
2. **IP-Forwarding** in `/etc/sysctl.d/99-tailscale.conf` (`net.ipv4.ip_forward=1`,
   `net.ipv6.conf.all.forwarding=1`): Ohne das verwirft der Kernel Pakete, die von
   `tailscale0` ins LAN sollen. Als Datei unter `sysctl.d`, damit es nach jedem Boot gilt.
3. **`tailscale up --auth-key=file:/run/ts-authkey --ssh --accept-dns=false`**
   - `--auth-key=file:` liest den Key aus der Datei statt von der Kommandozeile. Danach wird die Datei gelöscht.
   - `--ssh`: Tailscale SSH als zweiter Zugang, unabhängig vom OpenSSH-Key. Die Standard-Policy
     erlaubt dir das auf eigenen Geräten im „check“-Modus (gelegentliche Neu-Anmeldung im Browser).
   - `--accept-dns=false`: Der Router soll das DNS des fremden Netzes behalten und nicht MagicDNS
     als Resolver erzwingen. Das verhindert Ärger mit lokalen Namen und Captive Portals.
4. **Routen anbieten** über `ts-advertise-routes`, siehe nächster Abschnitt.
5. **Robustheit**, siehe unten.

### Subnetz automatisch erkennen (`ts-advertise-routes`)

- Default-Interface: Default-Route mit der kleinsten Metrik (`ip -4 -j route show default`).
- Subnetz: die Kernel-Route dieses Interface (`proto kernel scope link`), z. B. `192.168.1.0/24`.
  Kein Rechnen mit Masken nötig, der Kernel hat das Netz schon.
- Angeboten werden nur private Netze (10/8, 172.16/12, 192.168/16). `100.64.0.0/10` wird nie
  angeboten, weil das der Tailscale-Adressbereich selbst ist.
- `tailscale set --advertise-routes=…` läuft nur, wenn sich die Liste wirklich ändert
  (Vergleich mit `tailscale debug prefs`). Ohne Default-Route bleiben die alten Routen stehen.
- Auslöser: `ts-advertise-routes.timer` (45 s nach Boot, dann alle 2 Min.) und
  `/etc/networkd-dispatcher/routable.d/50-ts-router`, sobald ein Interface eine Adresse hat.
  Der Hook setzt außerdem das von Tailscale empfohlene UDP-GRO-Forwarding per `ethtool`.
- Einstellungen in `/etc/default/ts-router` (feste Routen, Zusatzrouten, 4via6, Watchdog).

### Route freigeben (manuell)

Ohne Tag und ohne `autoApprovers` muss jede **neu angebotene** Route einmal in der
Admin-Konsole freigegeben werden: Machines → ts-router → „…“ → Edit route settings → Haken
setzen. Das gilt bei jedem Einsatz in einem Netz mit anderem Subnetz. Freigegebene Routen
bleiben gespeichert; kommt das Gerät später in ein Netz mit demselben Subnetz, ist nichts zu tun.
Solange die Route nicht freigegeben ist, steht sie in `tailscale status` nicht als aktiv,
und der Mac kann das ferne LAN nicht erreichen.

### Robustheit

| Maßnahme | Wirkung |
|---|---|
| `tailscaled`, `networkd-dispatcher`, beide Timer `enable`d | starten bei jedem Boot |
| Drop-in `tailscaled.service.d/10-restart.conf` | `Restart=always`, 5 s Pause, kein Startlimit |
| `ts-watchdog` (jede Minute) | gesund = Default-Route da, Tailscale `Running` und `Self.Online` (Verbindung zum Koordinationsserver). Nach 3 Min. Störung: ohne Default-Route `networkctl reconfigure` (DHCP neu), sonst `tailscaled` neu. Nach 10 Min.: beides. Nach 30 Min. (einstellbar, 0 = nie) und mindestens 30 Min. Laufzeit: Neustart |
| Hardware-Watchdog (`RuntimeWatchdogSec=30s`) | systemd füttert den SoC-Watchdog. Hängt der Kernel, startet das Gerät neu. Wird nur eingerichtet, wenn `/dev/watchdog` existiert, aktiv nach dem nächsten Neustart |

Logs: `journalctl -u ts-advertise-routes -u ts-watchdog -u tailscaled -n 100`

---

## 5. Stolpersteine

### Subnetz-Kollision mit dem Heimnetz

Nutzt das ferne Netz dasselbe Subnetz wie das Netz, in dem der Mac gerade hängt (typisch
`192.168.178.0/24` bei Fritzboxen oder `192.168.0.0/24`), sind die Ziele mehrdeutig: Der Mac
schickt Pakete für `192.168.178.50` ins eigene LAN statt durch den Tunnel, oder die
angebotene Route überdeckt das eigene LAN. Optionen:

1. **Nur das Zielgerät als /32 anbieten** (praktischste Lösung für KNX):
   in `/etc/default/ts-router` `ROUTES_OVERRIDE="192.168.178.50/32"`, dann
   `systemctl start ts-advertise-routes` und die /32-Route freigeben. Die längere Präfixlänge
   gewinnt am Mac, nur diese eine Adresse geht durch Tailscale. Ein Gerät mit derselben IP
   daheim ist währenddessen nicht erreichbar. *Noch nicht getestet:* dass macOS die /32-Route
   über Tailscale der eigenen WLAN-Route vorzieht. Prüfen mit `route -n get <IP>` – muss
   `interface: utun…` zeigen. Klappt es nicht, Option 3 (Port-Weiterleitung) nehmen.
2. **4via6**: `VIA_SITE_ID="7"` (frei wählbar, 0–65535, je Standort eindeutig). Das Subnetz wird
   als `fd7a:115c:a1e0:b1a:0:7:c0a8:b200/120` angeboten, Geräte sind kollisionsfrei per IPv6
   oder per MagicDNS-Name `192-168-178-50-via-7` erreichbar. **Für knxlens derzeit nicht
   brauchbar**: KNXnet/IP und xknx arbeiten mit IPv4. Gut für Ping, Web-Oberflächen, SSH auf
   Geräte im fernen Netz.
3. **Port-Weiterleitung auf dem Odroid**: knxlens spricht die Tailscale-IP des Odroid an, der
   leitet UDP 3671 an das Interface weiter. Keine Subnetz-Route nötig, kollisionsfrei:
   `apt install socat` und `socat UDP4-LISTEN:3671,fork,reuseaddr UDP4:192.168.178.50:3671`
   (für Dauerbetrieb als systemd-Service). Bei Route-Back (siehe unten) funktioniert das,
   weil die Antworten an die Absenderadresse gehen.
4. **Mac in ein anderes Netz** (z. B. Handy-Hotspot), solange die Fernwartung läuft.

### Source-NAT und KNXnet/IP

Tailscale übersetzt bei Subnetz-Routen die Absenderadresse (SNAT, Standard
`--snat-subnet-routes=true`): Das KNX-Interface sieht als Absender die LAN-Adresse des
Odroid, nicht die 100.x-Adresse des Mac. Für KNXnet/IP-Tunneling heißt das:

- Der Client muss im CONNECT_REQUEST **Route-Back** verwenden, also HPAI `0.0.0.0:0`. Dann
  antwortet das Interface an die Absenderadresse des UDP-Pakets (den Odroid), und Tailscale
  übersetzt zurück zum Mac. Mit einer konkreten Adresse im HPAI (z. B. der 100.x des Mac)
  schickt das Interface die Antwort an sein eigenes Default-Gateway, und sie geht verloren.
- **knxlens erkennt das selbst:** Liegt das Interface nicht im Subnetz der eigenen
  Absenderadresse (über Tailscale ist die Absenderadresse die 100.x/32 des Mac), schaltet
  knxlens Route-Back ein, im eigenen LAN nicht. Im Editor zeigt der Gruppenmonitor nach dem
  Verbinden „· Route-Back“, der Tooltip nennt den Grund. (xknx allein schaltet Route-Back
  ebenfalls ein, wenn keine eigene Schnittstelle im Subnetz des Interface liegt – nur nicht
  bei Subnetz-Kollision, siehe oben.) Erzwingen: Auswahl „Rückweg → Route-Back erzwingen“
  im Gruppenmonitor (gilt auch für „Gerät lesen“), auf der Kommandozeile `--route-back` oder
  `KNXLENS_ROUTE_BACK=1`. Bei der /32-Lösung für Subnetz-Kollisionen erkennt die Automatik
  den Weg über Tailscale selbst; erzwingen ist nur nötig, wenn das nicht greift.
- Interfaces, die Tunnel nur aus dem eigenen Subnetz annehmen, sind kein Problem: Wegen SNAT
  kommt die Anfrage aus dem eigenen Subnetz.

### Kein Multicast

KNXnet/IP-Discovery (SEARCH_REQUEST an `224.0.23.12`) und KNX-Routing gehen nicht über
Tailscale. Die App muss die Interface-IP direkt ansprechen (`knxlens … -g 192.168.x.50`).
Die IP findest du z. B. per SSH auf dem Odroid in der DHCP-Tabelle des Routers oder per
`ip neigh` nach einem Ping-Scan.

### Weitere

- **Node-Key-Ablauf**: siehe oben, „Disable key expiry“ setzen.
- **Fremde Firewalls**: Tailscale kommt über ausgehendes UDP 41641 und notfalls über DERP
  (HTTPS 443) durch. Über DERP steigt die Latenz, KNX-Tunneling verkraftet das (Timeout 1 s je
  Telegramm-Bestätigung). `tailscale netcheck` und `tailscale ping <mac>` zeigen den Weg.
- **Captive Portals / 802.1X** im fremden Netz: dagegen hilft nichts davon, dort braucht es
  einen Port mit freiem Internetzugang.

---

## 6. Tests

Auf dem Odroid:

```bash
tailscale status                       # ts-router online, eigene 100.x
tailscale debug prefs | jq .AdvertiseRoutes
systemctl list-timers 'ts-*'
journalctl -u ts-advertise-routes -n 20
```

In der Admin-Konsole: Route bei ts-router sichtbar, nach Freigabe ohne „Awaiting approval“.

Auf dem Mac:

```bash
tailscale status                                  # ts-router mit Subnetz-Route
ping -c3 <ip-eines-geräts-im-fernen-lan>          # z. B. der Router des fernen Netzes
python3 deploy/ts-router/tools/knx-probe.py <ip-des-knx-interface>
```

`knx-probe.py` schickt einen DESCRIPTION_REQUEST mit Route-Back an UDP 3671 und gibt Name,
physikalische Adresse und MAC des Interface aus. Das prüft genau den Weg, den knxlens nimmt
(Unicast-UDP durch SNAT und zurück), ohne einen Tunnel aufzubauen oder auf den Bus zu schreiben.
Kommt eine Antwort, funktioniert auch der knxlens-Tunnel (Route-Back schaltet sich dort automatisch ein).

### Ergebnis der Ersteinrichtung (12.11.2026)

Odroid im LAN 192.168.158.0/24 (DHCP 192.168.158.183, Interface `end0`), Tailscale 1.104.6,
Mac über Handy-Hotspot (172.20.10.3), also wirklich durch den Tunnel:

| Test | Ergebnis |
|---|---|
| Passwort-Login per SSH | abgelehnt (`Permission denied (publickey)`) |
| `tailscale status` auf ts-router | `Running`, online, 100.122.15.41, Key läuft nicht ab |
| Route in der Admin-Konsole | `192.168.158.0/24` angeboten, manuell freigegeben, am Mac aktiv |
| `route get 192.168.158.20` am Mac | über `utun22` (Tailscale) |
| Ping 192.168.158.1 / .20 | 0 % Verlust, ~35–41 ms |
| `knx-probe.py 192.168.158.20` (Route-Back) | Antwort: Gira X1, 1.1.1 |
| Gleiche Anfrage mit HPAI = 100.x des Mac | **keine Antwort** – bestätigt, dass Route-Back nötig ist |
| `knxlens -g 192.168.158.20 probe 1.1.1` | Route-Back automatisch an, verbunden in 45 ms, Gerät `0x07B0` |
| Gruppenmonitor im Editor | verbunden als 1.1.4 · Route-Back, Live-Telegramme aus dem fernen Netz |

## 7. Betrieb

- Updates: `apt update && apt upgrade` per SSH; Tailscale kommt aus dem eigenen Repo mit.
- Setup erneut ausführen (z. B. nach Änderungen hier): `deploy/ts-router/mac/deploy.sh <ip-oder-ts-router>`
  ohne `TS_AUTHKEY`, wenn das Gerät schon angemeldet ist.
- Routen sofort neu ermitteln: `systemctl start ts-advertise-routes`
