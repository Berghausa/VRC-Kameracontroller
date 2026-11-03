HELP = '''SCHNELLSTART

1  VRChat starten, VRC+ verwenden und OSC aktivieren.
   Das Studio erkennt VRChat automatisch. Es darf auch ohne VRChat offen bleiben.

2  Kamerafahrt in VRChat unter Kamera → Dolly erstellen und exportieren.
   Die JSON-Datei liegt normalerweise unter Dokumente → VRChat → CameraPaths.

3  Im Studio „Fahrt hinzufügen“ wählen, JSON öffnen und einen Namen vergeben.
   Einmal die passende Welt zuordnen und speichern. Die Zuordnung bleibt erhalten.

4  Fahrt in der Liste auswählen und „Fahrt starten“ drücken.
   Nur Fahrten für die aktuell erkannte Welt können gestartet werden.
   „Stopp“ beendet die Wiedergabe. Beim Wechsel entstehen keine Kontroll-Exporte.

VERBINDUNG

„OSC verbunden“ bestätigt aktuelle Rückmeldungen oder eine erfolgreiche
OSCQuery-Abfrage. Bei „Warte auf OSC“ OSC in VRChat prüfen. Ein bestehender
OSC-Router darf geöffnet bleiben; das Studio verwendet einen eigenen freien Port.
„Welt wird geladen“: warten, bis VRChat die Welt vollständig betreten hat.

FAHRTEN UND DATEN

Gespeicherte Fahrten werden intern kopiert. Die Quelldatei wird nicht verändert.
Relative Fahrten benötigen denselben Standort und dieselbe Ausrichtung.
Für feste Club-Aufnahmen Fahrten mit Weltkoordinaten exportieren.
Ein weicher Übergang zwischen zwei Fahrten ist derzeit nicht enthalten.

Installation und Updates behalten deine Daten. Mit „Daten übernehmen“ kannst du
eine vorhandene club_analytics.sqlite3 auswählen. Es werden nur Kamera-Fahrten,
Weltzuordnungen und OSC-Einstellungen übernommen; die Quelle bleibt unverändert.
Der Datenordner lässt sich über die Schaltfläche unten öffnen und sichern.

BITFOCUS COMPANION

Verbindung: Generic HTTP Requests
Base URL: http://127.0.0.1:8766
Fahrt starten: POST /api/paths/ID/play  (ID steht in der Liste)
Stoppen: POST /api/stop
Status: GET /api/status

Statusfelder: playing, world_id, active_path_id, osc_connected, load_state, error.
Für Anzeigen den Status regelmäßig abrufen und Felder in Companion-Variablen
übernehmen. Companion und Studio müssen auf demselben PC laufen.

UPDATES

Das Studio prüft beim Öffnen und alle sechs Stunden GitHub auf stabile Releases.
„Updates“ erlaubt eine manuelle Prüfung. Bei einer neuen Version kannst du den
Installer herunterladen und nach Bestätigung starten. Die Prüfsumme wird vor dem
Start kontrolliert. Ohne Internet bleibt das Studio benutzbar.
'''
