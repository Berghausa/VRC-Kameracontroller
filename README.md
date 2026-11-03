# VRC Kameracontroller

Windows-Studio für gespeicherte Fahrten der nativen VRChat-Dolly-Kamera. Mit Weltzuordnung, automatischer OSCQuery-Erkennung und Bitfocus Companion.

## Installieren

1. Unter [Releases](https://github.com/Berghausa/VRC-Kameracontroller/releases/latest) **VRC-Kameracontroller-Setup.exe** herunterladen und öffnen.
2. Installieren und über das Startmenü starten. Optional eine Desktop-Verknüpfung anlegen.
3. Im Programm **Anleitung** öffnen. Dort steht der kurze Ablauf mit VRChat und Companion.

Python ist nicht erforderlich. Alternativ funktioniert die eigenständige `VRC-Kameracontroller.exe` ohne Installation.

## Erste Fahrt

In VRChat VRC+ und OSC aktivieren. Eine Dolly-Fahrt erstellen und als JSON exportieren. Im Studio **Fahrt hinzufügen**, Datei auswählen, Namen und Welt zuordnen. Danach die Fahrt in der passenden Welt starten. Das Studio bleibt ohne VRChat offen und verbindet sich automatisch. Ein bestehender OSC-Router kann parallel laufen.

Der Controller stoppt vor einem Wechsel die alte Fahrt und lädt die neue über einen Dateipfad. Es entstehen **keine automatischen Kontroll-Exporte** im VRChat-Ordner. Eine feste interne Importdatei pro Fahrt wird wiederverwendet. VRChat bestätigt einen Import nicht direkt; vor Play wird eine Sekunde gewartet.

Relative Fahrten benötigen denselben Ausgangspunkt und dieselbe Ausrichtung. Absolute Fahrten sind an die Weltgeometrie gebunden. Weiche Übergänge zwischen Fahrten sind derzeit nicht verfügbar.

## Updates

Beim Start und alle sechs Stunden wird die neueste stabile GitHub-Veröffentlichung geprüft. Neue Versionen erscheinen im Fenster. **Update installieren** lädt den Installer herunter, kontrolliert SHA-256 und startet ihn erst nach Bestätigung. Danach wird das Studio geschlossen; der Installer aktualisiert die Installation. Ohne Internet bleibt die Kamera-Steuerung nutzbar. Es werden keine GitHub-Zugangsdaten im Programm gespeichert.

Die EXE und der Installer sind derzeit nicht digital signiert.

## Daten behalten und übernehmen

Fahrten, Weltzuordnungen und Einstellungen liegen unter `%LOCALAPPDATA%\VRC-Kameracontroller\club_analytics.sqlite3`, außerhalb des Installationsordners. Updates und Deinstallation löschen diese Daten nicht.

Bestehende Daten: **Anleitung → Daten übernehmen**, bisherige `club_analytics.sqlite3` auswählen. Die neue Bibliothek muss leer sein. Es werden nur Kamera-Tabellen und Weltnamen übernommen, keine Besucherdaten oder DJ-Planungen. Quellen werden ausschließlich lesend geöffnet. Companion-IDs bleiben erhalten. **Datenordner öffnen** zeigt den Ordner für Sicherungen.

## Bitfocus Companion

Ab Version 1.1 kann Companion auf einem lokalen Server laufen: **Anleitung → Companion-Server**, lokale IP des Kamera-PCs und IP des Servers eingeben und das Studio neu starten. Die angezeigte Base URL statt `127.0.0.1` verwenden. In jeder HTTP-Aktion den angezeigten `Authorization`-Header eintragen. Der Zugriff erfordert den Schlüssel und ist auf die konfigurierte Server-IP beschränkt. Das ist für das eigene lokale Netzwerk vorgesehen, nicht für eine öffentliche Internetfreigabe. Bei geänderter PC-IP muss die Zuordnung angepasst werden. Standardmäßig bleibt die API nur lokal erreichbar.

Generic HTTP Requests, Base URL `http://127.0.0.1:8766`:

| Aktion | Methode | URI |
|---|---|---|
| Fahrt 1 starten | POST | `/api/paths/1/play` |
| Stoppen | POST | `/api/stop` |
| Status | GET | `/api/status` |

Antwort enthält `playing`, `world_id`, `active_path_id`, `osc_connected`, `load_state`, `error` und `paths`. `active_path_id` ist die zuletzt gestartete Auswahl, kein unabhängiger Nachweis des in VRChat geladenen Inhalts. Status regelmäßig über einen Companion-Trigger abrufen und Antwortfelder als Variablen speichern. Ohne Server-Einrichtung ist die API nur auf demselben Rechner erreichbar.

## Entwicklung und Releases

Python 3.12 und Inno Setup 6 installieren:

```powershell
python -m pip install -r requirements-build.txt
python -m unittest discover -s camera_controller -p 'test_*.py'
python camera_controller/controller.py
./build.ps1
```

`release/` enthält Setup, eigenständige EXE und `SHA256SUMS.txt`. `build.ps1 -Python ... -Iscc ...` erlaubt abweichende Werkzeugpfade. Nutzerdaten werden nie in die EXE eingebunden.

Neue Veröffentlichung: `camera_controller/version.py` erhöhen, committen und passenden Tag `vX.Y.Z` pushen. GitHub Actions testet und erstellt das vollständige Windows-Release. Die Update-Prüfung berücksichtigt nur stabile Releases mit Installer und Prüfsummendatei. Ein manuell gestarteter Workflow baut nur ein herunterladbares Artefakt.

Optionen: `--headless`, `--db PFAD`, `--logs ORDNER`, `--api-port 8766`, `--receive-port 9001`. Ohne freien Standard-Empfangsport wird automatisch ein eigener Port verwendet.

Quellen: [VRChat Camera Dolly](https://wiki.vrchat.com/wiki/Camera_Dolly), [VRChat OSCQuery](https://docs.vrchat.com/docs/oscquery), [Companion HTTP](https://github.com/bitfocus/companion-module-generic-http/blob/master/companion/HELP.md).
