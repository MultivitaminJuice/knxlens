# KNX-Rekonstruktion – Konzept

Ziel: Aus einer laufenden KNX-Anlage ohne ETS-Projekt ein vollständiges Inventar
(Geräte, Gruppenadressen, Verknüpfungen, möglichst Parameter) rekonstruieren und
am Ende ein importierbares `.knxproj` erzeugen. **Ausschließlich lesend.**

## Anlagen

| | Testanlage | Zielanlage |
|---|---|---|
| Interface | Gira X1, (LAN-IP), PA 1.1.1 | Eibmarkt IP-Interface, IP offen |
| Tunnel | UDP+TCP, Slots 1.1.4/1.1.5 frei, 1.1.3 = HA | unbekannt |
| HA | ja, KNX-Integration (Tunnel) | nein |
| Geräte | MDT, Steinel, Jung, B.E.G. … (System 7/B) | v. a. Gira Tastsensoren mit RTR (ca. 2000), Eibmarkt-Aktoren, vermutl. Gira BWM + Gira Wetterstation → **BCU1/BCU2** |
| Referenz | Original-`.knxproj` vorhanden | keins (das ist der Anlass) |

Die Testanlage ist der Prüfstand: Jede Phase wird gegen das Original-Projekt
verglichen (xknxproject parst es), bevor wir an die Zielanlage gehen.

## Architektur

```
knxrecon/            Python-Paket (eine Codebasis für Mac und später Raspberry Pi)
  bus/               Verbindung + ReadOnlyGuard   ← einzige Stelle, die sendet
  scan/              Linien- und Gerätescan
  device/            Masken-Strategien (BCU1, BCU2, System 7, System B)
  catalog/           .knxprod / Produktdaten aus .knxproj lesen
  decode/            Tabellen + Parameterbytes dekodieren
  monitor/           passiver Telegrammmitschnitt
  export/            CSV, Markdown, ETS-GA-Import-XML, .knxproj
  compare/           Abgleich gegen Referenz-.knxproj
  web/               FastAPI + htmx-Oberfläche
data/recon.sqlite    alle Rohdaten + Nutzer-Ergänzungen
```

**Leitprinzipien**

1. **Rohdaten zuerst, Deutung später.** Jede Antwort vom Bus wird roh in SQLite
   abgelegt (Speicherdumps, Property-Werte, Telegramme). Dekodierung läuft offline
   und beliebig oft – der Bus wird nur einmal belastet.
2. **ReadOnlyGuard.** Alles, was gesendet wird, läuft durch eine Funktion mit
   Positivliste erlaubter Dienste. Alles andere wirft eine Exception, bevor es
   das Netz erreicht. Unit-Tests stellen sicher, dass kein Schreibdienst durchkommt.
   - erlaubt: T_Connect/T_Disconnect, A_DeviceDescriptor_Read, A_PropertyValue_Read,
     A_PropertyDescription_Read, A_Memory_Read, A_ADC_Read
   - nur mit Einzelfreigabe: A_Authorize_Request (manche Geräte geben Speicher erst
     nach Authorize mit Standardschlüssel frei – schreibt nichts, ändert aber die
     Zugriffsstufe der Verbindung), A_GroupValue_Read (harmlos, aber Bus-sichtbar)
   - verboten: alles mit *Write*, Restart, IndividualAddress_*, Memory/Property-Write
3. **Ein Tunnel, gedrosselt.** Rate-Limit (z. B. max. 10 Telegramme/s), damit
   Buslast und andere Nutzer (HA) nicht leiden.
4. **CLI und Web-UI teilen denselben Kern.** CLI für Skripte/Automatisierung,
   Web-UI für Überblick und Nutzer-Ergänzungen.

**Warum Web-App statt Mac-App:** Läuft unverändert auf Mac und Raspberry Pi
(Vor-Ort-Tool), gleiche Sprache wie der KNX-Kern (Python), kein Xcode/Swift-Zweitcode.
Auf dem Mac später optional als „App“ per pywebview-Fenster oder launchd-Dienst.

**Bibliotheken:** xknx (Verbindung, Telegramme), xknxproject (Referenz parsen),
FastAPI + htmx (UI, kein Node-Build), SQLite. Calimero-tools (Java ≥ 17) nur als
Gegenprobe, falls unsere Ergebnisse für ein Gerät unplausibel sind.

## Phasen

### 0 – Interface prüfen ✅
Description-Request: Name, PA, Tunnel-Slots, Secure. Erledigt für den X1.

### 1 – Topologie und Linienscan
- Linienkoppler finden: `x.y.0` für alle Bereiche/Linien proben (240 Anfragen).
- Pro gefundener Linie alle 255 Adressen mit T_Connect + DeviceDescriptor_Read.
- Virtuelle Tunneladressen des Interfaces ausblenden.
- Ergebnis: Liste antwortender PAs. Dauer grob 5–15 min pro Linie.

### 2 – Geräteinventar
DeviceDescriptor (Maskenversion) bestimmt, wie das Gerät intern aufgebaut ist:

| Maske | Typ | Zugriff |
|---|---|---|
| 0x0010–0x0013 | BCU1 / System 1 | feste Speicheradressen (0x0100 ff.) |
| 0x0020–0x0025 | BCU2 / System 2 | Speicher + einige Properties |
| 0x0700–0x0705 | System 7 | Interface-Objekte / Properties |
| 0x07B0 | System B | Properties, große Tabellen |

Gelesen werden: Hersteller-ID, Applikations-ID + Version, Seriennummer,
Bestellnummer/Hardware-Typ (falls vorhanden), Programmierstatus.

### 3 – Tabellen
Adresstabelle, Assoziationstabelle, Gruppenobjekttabelle je Gerät lesen.
Ergebnis: Welche GA hängt an welchem Kommunikationsobjekt welches Geräts,
inkl. Flags (K/L/S/Ü/A). **Damit ist die Garagenfrage beantwortbar.**

### 4 – Passiver Mitschnitt
Gruppentelegramme loggen (Quelle, Ziel-GA, Dienst, Nutzdaten). Liefert reale
Nutzung, Sender je GA und Hinweise auf den DPT. Erst Mac, später Raspberry Pi
im Dauerbetrieb.

### 5 – Produktdaten und Parameter
- Testanlage: Produktdaten stecken im Referenz-`.knxproj` (M-xxxx-Ordner).
- Zielanlage: `.knxprod` aus KNX-Online-Katalog / Herstellerseiten.
- Zuordnung über Hersteller + Applikationsnummer + Version.
- Erst Objektnamen und DPTs (einfach), dann Parameter über Offset/Bit (schwer:
  Sichtbarkeit hängt von Bedingungen im Dynamic-Baum ab).

### 6 – Export
1. CSV/Markdown (immer).
2. **ETS-Gruppenadressen-Import** (CSV/XML) – von der ETS offiziell unterstützt,
   sicherer Zwischenschritt.
3. `.knxproj` erzeugen (experimentell, Format nach Vorbild xknxproject).

### Web-UI
Geräteliste, GA-Tabelle mit Benennung und Raumzuordnung (die nicht
rekonstruierbaren Teile), Live-Monitor, Scan-Fortschritt, Abgleich-Ansicht.

## Meilensteine

1. **M1 Testanlage lesen:** Phasen 1–3 + Abgleich gegen Referenz. Abnahme:
   alle Geräte gefunden, ≥ 95 % der GA-Verknüpfungen deckungsgleich.
2. **M2 Zielanlage Inventar:** Phasen 1–4 an der Eibmarkt-Anlage. Garage klären.
3. **M3 UI:** Ergebnisse ansehen, GAs benennen, Räume zuordnen.
4. **M4 Parameter:** Produktdaten-Dekodierung, gegen Referenz geprüft.
5. **M5 Export:** GA-Import, dann `.knxproj`-Versuch.
6. **M6 Raspberry Pi:** Dauer-Mitschnitt vor Ort.

## Konsequenz Zielanlage = BCU1/BCU2

Die Testanlage prüft nur den System-7/B-Pfad. Für BCU1/BCU2 (feste Speicherkarte,
keine Properties) eigener Lesepfad nach Spezifikation:
0x0104 Hersteller, 0x0105–0x0106 Gerätetyp, 0x0107 Version, 0x0111 Zeiger
Assoziationstabelle, 0x0112 Zeiger Gruppenobjekttabelle, ab 0x0116 Adresstabelle
(1 Byte Länge, eigene PA, dann GAs). Gegenprobe über Gira-Produktdaten aus dem
Katalog und Telegrammmitschnitt; vorab im Speicher gezielt ein bekanntes Gerät
prüfen (z. B. einen Taster mit bekannter Funktion).

## Risiken

- **Speicherschutz:** Manche Geräte verweigern Memory-Read ohne Authorize →
  Einzelfreigabe, notfalls nur Properties.
- **Herstellereigene Speicher-Layouts** bei System-B-Geräten → Gegenprobe Calimero.
- **Verschlüsselte/signierte `.knxprod`:** unklar, ob alle Hersteller offen lesbar sind.
- **`.knxproj`-Import:** Ob die ETS ein selbst erzeugtes Projekt ohne Beanstandung
  öffnet, ist ungeklärt. Fallback: GA-Import + Geräte manuell einfügen.
- **Alte BCU1-Geräte** antworten langsam → großzügige Timeouts.

## Offen

- Pfad zum Referenz-`.knxproj` der Testanlage; passwortgeschützt? aktuell?
- IP des Eibmarkt-Interfaces (für M2).
