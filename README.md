# knxlens

**Blick in eine laufende KNX-Anlage – rein lesend.**
Für Anlagen, deren ETS-Projekt verloren ist: knxlens findet die Geräte am Bus, liest
Gruppenadressen, Verknüpfungen und Parameter aus, schneidet den Busverkehr mit und
baut daraus ein Inventar – bis hin zum ETS-Gruppenadressen-Import und einem
(experimentellen) `.knxproj`.

*English: knxlens reconstructs a KNX installation whose ETS project is lost. It scans the
bus, reads address/association/group-object tables and device parameters (decoded with the
manufacturers' product databases), records bus traffic and exports to ETS. Strictly
read-only. UI and docs are German.*

> ⚠️ **Nur lesen.** knxlens sendet ausschließlich lesende Dienste (DeviceDescriptor-,
> Memory-, PropertyValue-Read). Ein Guard prüft jedes Telegramm vor dem Senden gegen
> eine Positivliste; Schreib-, Restart- und Adressdienste werden abgewiesen
> (`tests/test_guard.py`). Ein mit knxlens erzeugtes Projekt **nie ungeprüft auf Geräte
> herunterladen** – unsichere und geschätzte Werte würden dabei ins Gerät geschrieben.

## Was es kann

| Schritt | Befehl | Ergebnis |
|---|---|---|
| Interface prüfen | `python scripts/describe.py <IP>` | Name, Tunnel-Slots, Secure |
| Linie scannen | `knxlens -g <IP> scan 1.1` | antwortende Geräte, Maskenversion |
| Geräte lesen | `knxlens -g <IP> read` | Hersteller, Applikation, Adress-/Assoziations-/Objekttabellen |
| Parameter lesen | `knxlens -g <IP> read-params --products <datei>` | Parameterspeicher der Geräte |
| Mitschnitt | `knxlens -g <IP> monitor [--duration s]` | Gruppentelegramme in SQLite |
| Oberfläche | `knxlens serve --products <datei>` | Geräte, Parameter, GAs benennen, Live-Verkehr |
| Inventar | `knxlens report -o inventar.md` | Markdown ohne Referenzprojekt |
| Projektdatei | `knxlens project import <x.knxproj> -o p.knxlens` / `project reconstruct --products … -o p.knxlens` | Projekt aus ETS bzw. aus der Rekonstruktion |
| ETS-Export | Oberfläche → *ETS-Import (XML)* | Gruppenadressen-Import für die ETS |
| `.knxproj` | `knxlens project export p.knxlens -o x.knxproj` | unsigniert, nicht ETS-importierbar |

Unterstützte Geräte: System 7 und System B (Masken 0x07xx) an echten Geräten geprüft;
BCU1/BCU2 (0x001x/0x002x, z. B. ältere Tastsensoren) nach Spezifikation implementiert,
bisher nur gegen nachgebaute Speicherabbilder getestet.

## Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Python ≥ 3.11. Auf macOS mit iCloud-synchronisiertem `~/Documents` die venv besser
außerhalb anlegen (iCloud setzt das *hidden*-Flag auf `.pth`-Dateien, Python ≥ 3.13
ignoriert sie dann).

## Produktdaten

Gerätenamen, Objekttexte, DPTs und Parameter kommen aus den Produktdatenbanken der
Hersteller (`.knxprod`, oder der Produktteil einer vorhandenen `.knxproj`). Zuordnung über
die vom Gerät gelesene Applikations-ID. Dateien in `data/produkte/` werden von `serve`
automatisch geladen. Produktdateien werden als nicht vertrauenswürdig behandelt
(`defusedxml`, Größengrenzen).

## Wie gut ist das?

Geprüft an einer Anlage mit 40 Geräten (MDT, Steinel, Jung, B.E.G., Theben, Enertex, Gira X1)
gegen deren ETS-Projekt:

- Geräte und Applikationen: 40/40, GA-Verknüpfungen: **588/588**, keine falschen
- GA-Datenpunkttypen aus Produktdaten: 310/310 Haupttyp richtig
- Parameter: 3139 von 3194 aus dem Gerät gelesenen Werten identisch mit dem Projekt;
  47 als *unsicher* markiert (Speicher noch im Auslieferungszustand), 8 unerklärt
- ETS-only-Einstellungen (ohne Speicherort) aus dem Speicher erschlossen: 400/401
- `.knxproj`-Export, mit xknxproject zurückgelesen: 40/40 Geräte, 588/588 Verknüpfungen

Nicht rekonstruierbar: Namen von Gruppenadressen und Geräten, Gebäudestruktur,
Kommentare, Parameter ohne Speicherort im Gerät. Die Oberfläche hilft beim Ergänzen
(Live-Mitschnitt: Taster drücken, GA sehen, benennen).

## Grenzen

- Der `.knxproj`-Export ist **unsigniert** (die ETS signiert Projekte mit ihrer Lizenz;
  das wird bewusst nicht nachgebildet). **Die ETS6 lehnt ihn deshalb ab** („Projektdatei
  hat keine gültige Signatur“). Er taugt für Werkzeuge, die den Inhalt lesen
  (xknxproject-Rückprüfung), nicht für die ETS. Für die ETS: GA-Import (XML) plus
  Geräte von Hand einfügen, Verknüpfungen und Parameter nach dem knxlens-Inventar.
- Welche Objektvariante aktiv ist, folgt aus den Parametern; wo das nicht eindeutig ist,
  wird geschätzt und markiert.
- KNX Secure wird nicht unterstützt.

Konzept, Messungen und Entscheidungen: [KONZEPT.md](KONZEPT.md). Weiterentwicklung zum
plattformübergreifenden Inbetriebnahmewerkzeug: [ROADMAP.md](ROADMAP.md).

## Lizenz

MIT – siehe [LICENSE](LICENSE). Baut auf [xknx](https://github.com/XKNX/xknx) und
[xknxproject](https://github.com/XKNX/xknxproject) auf.
