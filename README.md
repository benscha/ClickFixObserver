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
    "PayloadUrl": ["https://delivery.example/payload"]
  }
]
```

Die Werte stammen ausschließlich aus den Spalten Lure, Delivery und Payload
der Infrastrukturansicht. IP-Adressen in Delivery werden ausgeschlossen;
eckige Klammern werden entfernt. Nicht vorhandene Delivery- oder Payload-Werte
ergeben leere Arrays. Die Ergebnisse enthalten damit **aktive, nicht mehr
defangte potenziell gefährliche Indikatoren**: nicht im Browser öffnen.

## Einschränkung der Datenquelle

Die Sitemap enthält derzeit nur die neuesten 500 Reports und kann daher weniger
als sieben Tage abdecken. Der Export verwendet bewusst nicht die durch
`robots.txt` für Crawler ausgeschlossene Dashboard-API. Ein Warnhinweis im
Workflow-Log nennt den ältesten verfügbaren Tag. Für diesen Tag kann die
Liste unvollständig sein; eine bereits vorhandene Datei dieses Tages wird
nicht überschrieben. Ältere, nicht abgedeckte Tage werden weder neu angelegt
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
