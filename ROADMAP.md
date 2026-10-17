# knxlens – Roadmap zum KNX-Inbetriebnahmewerkzeug

Ziel: Die Funktionen der ETS, die man für eine normale Anlage braucht, plattformübergreifend
nachbauen – macOS, Linux, Windows, Raspberry Pi. Schrittweise, jeder Schritt einzeln
nutzbar und gegen echte Geräte geprüft.

## Rahmen

**Drin:** Projekt anlegen und bearbeiten, Produktdaten (`.knxprod`) importieren, Topologie,
Gruppenadressen, Gebäude/Räume, Parameter bearbeiten, Objekte verknüpfen, physikalische
Adresse programmieren, Applikation/Parameter/Tabellen herunterladen, Diagnose
(Gruppen- und Busmonitor, Werte senden/lesen), Rekonstruktion bestehender Anlagen.

**Nicht drin (vorerst):** KNX Secure, ETS-Plug-ins/DCA-Apps (Geräte, die nur über ein
Windows-Plug-in parametrierbar sind), Medien RF/PL, Mehrbenutzer.

**Medien/Schnittstellen:** KNXnet/IP Tunneling (UDP/TCP) über xknx. USB später ggf. über knxd.

## Architektur

```
knxlens/
  bus.py          Verbindung, ReadOnlyGuard (wie bisher, unverändert streng)
  write/          NEU: Schreibpfad – eigener WriteGuard, nur im Programmiermodus
  catalog.py      Produktdaten
  params.py       Parameter lesen/auswerten  → NEU: kodieren (Umkehrung)
  project/        NEU: Projektmodell, Datei-Format, Import/Export
  build/          NEU: Download-Abbild erzeugen (Tabellen, Parameterspeicher, Ladeablauf)
  web/            Oberfläche (wird zur Editor-Oberfläche)
  desktop/        NEU: App-Hülle (pywebview) → Mac .app, Linux, Windows
```

- **Kern in Python**, eine Codebasis für alle Plattformen; Oberfläche im Browser bzw. in
  einem nativen Fenster (pywebview). Auf dem Pi läuft dasselbe kopflos als Server.
- **Projektdatei:** eine SQLite-Datei (`*.knxlens`) – ein File, transaktionssicher,
  versionierbar über Export. Import aus Rekonstruktion und aus `.knxproj` (unverschlüsselt).
- **Oberfläche:** Vue 3 + TypeScript + Vite, Komponenten von **Naive UI** (MIT). Das gebaute
  Frontend liegt in `knxlens/web/static/editor` und wird mit dem Python-Paket ausgeliefert –
  zur Laufzeit kein Node. (PrimeVue verworfen: ab Version 5 kommerzielle „PrimeUI License“
  mit Lizenzschlüssel, unvereinbar mit einem offenen MIT-Projekt.) Desktop-Hülle: Tauri in E6 prüfen.
- **Kaenx** (GPL, C#, Windows/UWP, Download noch unfertig) dient nur als Ideenquelle –
  kein Code übernehmen (knxlens ist MIT).

## Sicherheitskonzept für das Schreiben

Bisher gilt „nur lesen“, abgesichert im Code. Das bleibt der Standard. Schreiben wird ein
**eigener Pfad** mit eigenen Regeln:

1. Aus, solange nicht ausdrücklich ein Download für **ein bestimmtes Gerät** gestartet wird.
2. **Vorschau** vor jedem Schreiben: welche Speicherbereiche/Properties sich ändern (Diff
   gegen den aktuell gelesenen Gerätezustand); Bestätigung durch den Nutzer.
3. Ziel wird vor dem Schreiben geprüft: Maske, Hersteller, Applikation, Seriennummer wie erwartet.
4. **Rücklesen und Vergleichen** nach dem Schreiben; Protokoll jedes geschriebenen Bytes.
5. Positivliste je Phase (z. B. PA-Programmierung nur IndividualAddressWrite im Prog-Modus).
6. Kein Schreiben an Geräte, die nicht explizit ausgewählt sind; keine Broadcast-Schreibdienste
   außer der PA-Vergabe mit gedrückter Programmiertaste.

## Stufen

| Stufe | Inhalt | Abnahme (Testanlage, 40 Geräte) | Schreibt? |
|---|---|---|---|
| **E1** Projektmodell ✅ | Projektdatei, Import aus Rekonstruktion und `.knxproj`, Export | Testanlage verlustfrei hin und zurück | nein |
| **E2** Editor offline | Topologie, GAs, Geräte aus Katalog einfügen, Parameter bearbeiten, Verknüpfen, Räume | Bedienbar; Parameter-Kodierung = Umkehr der Dekodierung | nein |
| **E3** Download-Abbild | Aus dem Projekt Adress-/Assoziations-/Objekttabelle und Parameterspeicher erzeugen, Ladeablauf aus den Produktdaten ableiten | **Erzeugte Bytes = von den 40 Geräten gelesene Bytes** | nein |
| **E4** Erster Download | Ladeablauf ausführen (Entladen, Segmente, Laden, Prüfen) an einem unkritischen Gerät | Gerät funktioniert, Rücklesen identisch | ja, 1 Gerät |
| **E5** PA & Teil-Downloads | Physikalische Adresse programmieren, nur Parameter / nur Tabellen laden, mehrere Geräte | an Testgeräten | ja |
| **E6** Desktop-App | Mac `.app`, Linux/Windows-Pakete, Feinschliff | installierbar, startet ohne Terminal | nein |
| **E7** Extras | Werte senden/lesen (Test schalten), Filtertabellen Koppler, BCU1-Laden, Busmonitor | je Funktion | teils |

E3 ist der Kern der Absicherung: Stimmen die erzeugten Bytes für alle 40 Geräte mit dem
überein, was sie tatsächlich im Speicher haben, ist der Download-Inhalt bewiesen, bevor
irgendetwas geschrieben wird.

## Stand E1 (17.10.2026)

- Projektdatei `*.knxlens` (SQLite, Schema 1): Topologie, Geräte (Produkt, Parameter,
  Modulinstanzen, Verknüpfungen), Gruppenadressen mit Bereichen, Gebäude – und die
  verwendeten Produktdaten komprimiert (Testanlage: 11,6 MB statt 189 MB roh).
- `.knxproj`-Import (unverschlüsselt) und -Export. **Testanlage: Import → Datei → Export →
  Import identisch; auch auf XML-Ebene alle 3077 + 5361 Elemente gleich, in gleicher
  Reihenfolge** (nur Puids neu vergeben). Unbekannte Attribute/Elemente werden wörtlich
  mitgeführt. Die Projektsignatur der ETS wird nicht übernommen und nicht erzeugt.
- Rekonstruktion → Projekt: gegen das ETS-Projekt 40/40 Geräte, Produkt, Applikation und
  GA-Zuordnung je Gerät gleich; 456/588 Verknüpfungen mit exakt gleicher Objektvariante
  (Rest: gleiches Objekt/GA, Variante geschätzt); 447/450 Parameterwerte gleich.
- CLI: `knxlens project import|reconstruct|export|info`; Oberfläche: Download `.knxlens`/`.knxproj`.

## Stand E2 (17.10.2026)

- **E2.1 ✅** `knxlens edit projekt.knxlens`: Navigator (Topologie, Gebäude, Gruppenadressen mit
  Filter), Geräteansicht (aktive Objekte, Verknüpfungen, DPT, Flags), GA-Ansicht (Nutzer,
  sendende GA), Parameter nach ETS-Seiten – Werte aus dem Projekt, Sichtbarkeit vom Kern
  berechnet. Noch nur lesend.
- **E2.2 ✅** Parameter bearbeiten: Eingabefelder je Typ (Auswahl, Schalter, Zahl/Zeit/Gleitkomma
  mit Grenzen aus den Produktdaten, Text), Prüfung im Kern, Speichern wie die ETS (nur Abweichungen
  vom Standard), Zurücksetzen, Live-Sichtbarkeit (z. B. Kanal deaktivieren blendet dessen Seiten
  aus), Warnung bei verknüpften, aber inaktiven Objekten, Speichern mit ⌘S.
  **Kodierung = Umkehr der Dekodierung: 5298/5298 Parameter der 40 Geräte ergeben exakt die
  gelesenen Bytes** (Grundlage für E3).
- **E2.3 ✅ (Umbau nach Rückmeldung)** neue Bedienung, ETS-Begriffe mit modernen Werkzeugen:
  zwei gleichwertige Arbeitsbereiche (je Gruppenadressen/Topologie/Gebäude/Katalog), Inspector,
  großer Parameterdialog, eigener Baum (Mehrfachauswahl, Tastatur, Ziehen & Ablegen über beide
  Bereiche, Kontextmenü, F2), Befehlspalette ⌘K, Rückgängig/Wiederholen für jede Änderung.
  Kernbefehle (`project/ops.py`, getestet): Haupt-/Mittelgruppen und GAs anlegen/umbenennen/
  löschen, GA-Adresse ändern, GAs verschieben (Verknüpfungen wandern mit) und kopieren,
  verknüpfen/lösen/sendende GA, Geräte einfügen (Katalog), kopieren inkl. Parameter, PA ändern,
  auf andere Linie verschieben, löschen.
- **Fenster frei anordnen** (dockview, MIT, auf 8.4.0 festgelegt): jede Ansicht ein Fenster,
  schließen, als Reiter stapeln, an Ränder ziehen zum Teilen; Menü „Ansicht“ mit Fenstern und
  Vorlagen (eins, zwei neben-/untereinander, zwei untereinander + Eigenschaften rechts …);
  Layout wird im Browser gemerkt.
- Offen: Gebäude bearbeiten, Linien/Bereiche anlegen, neue `.knxprod` ins Projekt laden,
  Mehrfach-Umnummerieren, Geräte-Parameter mehrerer Geräte gleichzeitig.
- Offen aus E2.2: `Assign`-Regeln der Produktdaten (versteckte Folgewerte) werden noch nicht
  nachgeführt – wichtig erst für das Download-Abbild (E3).

## Offene Punkte

- Testgerät für E4 festlegen (unkritisches Gerät der Testanlage).
- Lizenzbedingungen der Hersteller-Produktdaten für die Nutzung außerhalb der ETS prüfen
  (für den Eigengebrauch unkritisch, für die öffentliche Weitergabe klären).
- Name: knxlens bleibt vorerst – „Linse“ passt auch auf ein Werkzeug, das Anlagen sichtbar
  und bearbeitbar macht.
