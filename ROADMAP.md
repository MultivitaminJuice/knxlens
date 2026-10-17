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
- **Oberfläche:** Für Editor-Funktionen (Bäume, Drag & Drop beim Verknüpfen, Parameterseiten)
  reicht serverseitiges HTML auf Dauer nicht. Entscheidung bei E2: kleines Frontend-Framework
  mit Build-Schritt (Ergebnis wird als statische Dateien mitgeliefert, zur Laufzeit kein Node).
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
| **E1** Projektmodell | Projektdatei, Import aus Rekonstruktion und `.knxproj`, Export | Testanlage verlustfrei hin und zurück | nein |
| **E2** Editor offline | Topologie, GAs, Geräte aus Katalog einfügen, Parameter bearbeiten, Verknüpfen, Räume | Bedienbar; Parameter-Kodierung = Umkehr der Dekodierung | nein |
| **E3** Download-Abbild | Aus dem Projekt Adress-/Assoziations-/Objekttabelle und Parameterspeicher erzeugen, Ladeablauf aus den Produktdaten ableiten | **Erzeugte Bytes = von den 40 Geräten gelesene Bytes** | nein |
| **E4** Erster Download | Ladeablauf ausführen (Entladen, Segmente, Laden, Prüfen) an einem unkritischen Gerät | Gerät funktioniert, Rücklesen identisch | ja, 1 Gerät |
| **E5** PA & Teil-Downloads | Physikalische Adresse programmieren, nur Parameter / nur Tabellen laden, mehrere Geräte | an Testgeräten | ja |
| **E6** Desktop-App | Mac `.app`, Linux/Windows-Pakete, Feinschliff | installierbar, startet ohne Terminal | nein |
| **E7** Extras | Werte senden/lesen (Test schalten), Filtertabellen Koppler, BCU1-Laden, Busmonitor | je Funktion | teils |

E3 ist der Kern der Absicherung: Stimmen die erzeugten Bytes für alle 40 Geräte mit dem
überein, was sie tatsächlich im Speicher haben, ist der Download-Inhalt bewiesen, bevor
irgendetwas geschrieben wird.

## Offene Punkte

- Testgerät für E4 festlegen (unkritisches Gerät der Testanlage).
- Lizenzbedingungen der Hersteller-Produktdaten für die Nutzung außerhalb der ETS prüfen
  (für den Eigengebrauch unkritisch, für die öffentliche Weitergabe klären).
- Name: knxlens bleibt vorerst – „Linse“ passt auch auf ein Werkzeug, das Anlagen sichtbar
  und bearbeitbar macht.
