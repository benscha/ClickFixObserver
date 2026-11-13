# ClickFix infrastructure export

The GitHub Actions workflow is scheduled every 30 minutes (at minutes 00 and
30 of each hour, UTC) and can also be started
manually from **Actions**. It downloads the public sitemap of
[clickfix.observer](https://clickfix.observer/) and the report pages listed
there. Generated JSON files are committed to the `data` directory on the
workflow branch. GitHub Actions requires permission to write repository contents.
Scheduled workflows run from the default branch. GitHub may delay scheduled
runs, so this schedule does not guarantee exact execution times.

## Daily archive

The exporter processes available reports from the last 30 days, including
the boundary calendar day needed for an exact 720-hour window. Reports are
grouped by their UTC scan date in `data/YYYYMMDD.json`, with one object per report:

```json
[
  {
    "ReportId": "example123",
    "ScannedAt": "2026-11-13T06:21:36.065000+00:00",
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

Existing daily files are extended with new reports; existing report data is
not replaced or removed. `ReportId` identifies reports and avoids repeated
downloads. `ScannedAt` records the exact UTC timestamp from the sitemap's
`lastmod` field. Existing reports still listed in the sitemap are enriched
with a missing timestamp without downloading their pages again.

Legacy entries without `ReportId` are matched against the fields already
stored. Each legacy entry can match at most one report per run. A matched
entry receives `ReportId` and `ScannedAt`; its existing infrastructure and
payload fields remain unchanged. This matching is not unambiguous: identical
report data or changed reports can cause incorrect matches or duplicate entries.
Invalid existing JSON files cause the exporter to fail instead of overwriting them.

## Rolling sets

Each successful export rebuilds the following JSON arrays from the daily archive:

| File under `data/sets` | Time window |
|---|---|
| `1D.json` through `10D.json` | Last 24, 48, 72, ... 240 hours |
| `14D.json` | Last 336 hours |
| `30D.json` | Last 720 hours |

The windows use exact timestamps, not whole calendar days. An entry is
included when `export time - window <= ScannedAt <= export time`.
All windows share the same UTC reference time captured at the start of the
export. Entries newer than that reference time are not included.
Each report ID appears at most once per set.

Legacy entries without an exact timestamp are excluded from the sets with
an explicit warning; the exporter does not invent timestamps from daily filenames.
Missing archive files also produce warnings. Sets may therefore be incomplete
until sufficient history has accumulated. Invalid timestamps, conflicting report
IDs, or malformed archive files cause failure before daily files or sets are
written. Daily files outside the selected period remain untouched.

The workflow already commits everything under `data`, including the sets.
The sets reflect the last successful export, not the time a consumer reads
them. A failed or delayed workflow leaves the previously committed sets in place.
GitHub Raw responses may also be cached.

## Exported fields

`UrlDomain`, `DeliveryUrl`, and `PayloadUrl` contain the values shown in the
Lure, Delivery, and Payload infrastructure columns. Individual IP addresses
from those columns and Related are deduplicated into the `IP` array.
If Lure is an IP address, `UrlDomain` is `null`. Related items retain their
displayed type; an individual IP uses `IP` instead of `Value`.

`Payload` contains the full command from **Extracted payload**, as displayed,
or `null` if the section is absent. `ResolvedDownloadUrl` contains the
download URLs shown there; these can differ from the infrastructure column's
`PayloadUrl`. Missing lists are represented as empty arrays.

Square brackets are removed from indicators. The payload command is kept
unchanged so that PowerShell type expressions such as `[guid]` are preserved.
Exports contain **active, potentially malicious indicators and commands**:
do not open the URLs or execute the commands.

## Data source limitations and failures

The sitemap currently contains only the newest 500 reports and may cover much
less than 30 days. The exporter intentionally does not use the dashboard API,
which is excluded for crawlers by `robots.txt`. Older reports are available
only if previously archived. Even within the covered period, completeness
depends on the sitemap. Longer sets accumulate over successful runs and do
not provide a complete historical backfill on the first run.

Every report download is logged immediately. Missing or incomplete
infrastructure sections and temporary download errors are retried up to
three times, with waits of five and ten seconds. A longer `Retry-After`
header is respected for HTTP 429 and temporary server errors. Permanent HTTP
errors and other schema errors fail immediately. A final download or parser
failure prevents daily files and sets from being written; reports are not
silently skipped.

Legacy entries without report IDs need their pages downloaded for matching.
For 500 reports, the default one-second pause alone takes more than eight
minutes, before network response times.

## Run locally

Python 3.11 or later is required. No third-party packages are needed.

```powershell
python -m unittest discover -s tests -v
python export_clickfix.py
```

Use `--output` to change the destination directory and `--delay` to change
the pause between report requests in seconds (default: one second).

## KQL

For a public repository, use a fixed URL for the desired set, for example
the last 72 hours:

```kusto
let clifixobserver = externaldata(Entry:dynamic)
[
    "https://raw.githubusercontent.com/benscha/ClickFixObserver/refs/heads/main/data/sets/3D.json"
]
with (
    format = "multijson",
    ingestionMapping = '[{"Column":"Entry","Properties":{"Path":"$"}}]'
)
| project
    ReportId = tostring(Entry.ReportId),
    ScannedAt = todatetime(Entry.ScannedAt),
    UrlDomain = tostring(Entry.UrlDomain),
    DeliveryUrl = Entry.DeliveryUrl,
    PayloadUrl = Entry.PayloadUrl,
    IP = Entry.IP,
    Related = Entry.Related,
    Payload = tostring(Entry.Payload),
    ResolvedDownloadUrl = Entry.ResolvedDownloadUrl;
clifixobserver
```
