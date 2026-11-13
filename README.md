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
Liste unvollständig sein; eine bereits vorhandene Datei dieses Tages wird
auch nach einer Schemaänderung nicht überschrieben und enthält deshalb
gegebenenfalls noch das alte Format. Ältere, nicht abgedeckte Tage werden weder neu angelegt
noch verändert. Auch innerhalb des Zeitraums ist die Vollständigkeit von der
Sitemap abhängig. Dateien außerhalb des Zeitraums bleiben erhalten.

## Lokal ausführen

Python 3.11 oder neuer ist erforderlich; externe Pakete werden nicht benötigt.

```powershell
python -m unittest discover -s tests -v
python export_clickfix.py
```

Optional kann mit `--output` ein anderes Ausgabeziel und mit `--delay` die
Pause zwischen Reportabrufen in Sekunden angegeben werden (Standard: eine
Sekunde).
