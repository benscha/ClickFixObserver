# ClickFix-Infrastrukturexport

Der Workflow ruft täglich um 03:15 Uhr UTC die öffentliche Sitemap von
[clickfix.observer](https://clickfix.observer/) und die dort gelisteten
Reportseiten ab. Er kann auch manuell über **Actions** gestartet werden.
Die JSON-Dateien werden im Verzeichnis `data` auf dem ausgeführten Branch
committet. Das Repository muss GitHub Actions Schreibzugriff auf Inhalte
erlauben.

Der Export betrachtet sieben UTC-Kalendertage einschließlich des aktuellen
Tages. Das Datum entspricht `lastmod` in der Sitemap. Pro abgedecktem Tag
entsteht `data/YYYYMMDD.json`, ein Array mit einem Objekt pro Report:

```json
[
  {
    "ReportId": "example123",
    "UrlDomain": "example.com",
    "DeliveryUrl": ["delivery.example"],
    "PayloadUrl": ["https://delivery.example/payload"],
    "IP": ["192.0.2.1"],
    "Related": [{"Value": "related.example", "Type": "Reference"}],
    "Payload": "command as displayed in the report",
    "ResolvedDownloadUrl": ["https://delivery.example/payload"]
  }
]
```

Bestehende Tagesdateien werden für alle sieben Tage nur um neue Reports
ergänzt. Bereits enthaltene Einträge werden nicht verändert oder entfernt.
`ReportId` identifiziert neu hinzugefügte Reports eindeutig; bereits bekannte
Reports werden nicht erneut abgerufen. Bei alten Einträgen ohne `ReportId`
werden stattdessen die vorhandenen Felder mit dem Report verglichen. Jeder
solche Eintrag kann pro Lauf höchstens einen Report zuordnen und bleibt
unverändert. Dieser Abgleich ist nicht eindeutig: Wenn mehrere Reports die
gleichen Werte haben oder ein alter Report sich geändert hat, kann es zu
falschen Zuordnungen oder doppelten Einträgen kommen. Ungültige vorhandene
JSON-Dateien führen zum Abbruch, statt überschrieben zu werden.

Die Felder `UrlDomain`, `DeliveryUrl` und `PayloadUrl` enthalten die jeweiligen
Werte der drei Infrastrukturspalten. Einzelne IP-Adressen aus diesen Spalten
und dem Bereich Related werden dedupliziert im Array `IP` gesammelt. Falls
Lure eine IP-Adresse ist, ist `UrlDomain` gleich `null`. `Related` enthält alle
zugehörigen Werte mit ihrem angezeigten Typ; bei einer einzelnen IP-Adresse
heißt das Wertefeld `IP` statt `Value`.

`Payload` enthält den vollständigen Befehl aus „Extracted payload“, wie auf
der Website angezeigt, oder `null`, falls dieser Bereich fehlt.
`ResolvedDownloadUrl` enthält die dort angezeigten Download-URLs. Diese
können sich von `PayloadUrl` unterscheiden, das nur die Payload-Spalte der
Infrastrukturansicht enthält. Nicht vorhandene Listen ergeben leere Arrays.
Bei Indikatoren werden eckige Klammern entfernt; der Payload-Befehl bleibt
hingegen unverändert, damit beispielsweise PowerShell-Typen wie `[guid]`
erhalten bleiben. Die Ergebnisse enthalten **aktive, nicht mehr defangte
potenziell gefährliche Indikatoren und möglicherweise schädliche Befehle**:
nicht öffnen oder ausführen.

## Einschränkung der Datenquelle

Die Sitemap enthält derzeit nur die neuesten 500 Reports und kann daher weniger
als sieben Tage abdecken. Der Export verwendet bewusst nicht die durch
`robots.txt` für Crawler ausgeschlossene Dashboard-API. Ein Warnhinweis im
Workflow-Log nennt den ältesten verfügbaren Tag. Für diesen Tag kann die
Liste unvollständig sein. Auch diese Datei wird um neu gelistete Reports
ergänzt; bestehende Einträge bleiben unverändert und können weiterhin ein
älteres Schema enthalten. Ältere, nicht abgedeckte Tage werden weder neu
angelegt noch verändert. Auch innerhalb des Zeitraums ist die Vollständigkeit
von der Sitemap abhängig. Dateien außerhalb des Zeitraums bleiben erhalten.

## Lokal ausführen

Python 3.11 oder neuer ist erforderlich; externe Pakete werden nicht benötigt.

```powershell
python -m unittest discover -s tests -v
python export_clickfix.py
```

Optional kann mit `--output` ein anderes Ausgabeziel und mit `--delay` die
Pause zwischen Reportabrufen in Sekunden angegeben werden (Standard: eine
Sekunde).
