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
let clickfixobserver = externaldata(Entry:dynamic)
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
clickfixobserver
```

### Find lure and delivery domains in email URLs

The following Defender XDR query searches seven days of email URLs and clicks
against the seven-day report set. It includes lure, delivery, payload, and
resolved download indicators. Dedicated hosts use domain matching; the listed
shared services require an exact host and non-root URL path. Matching reports
and email metadata are grouped per click event instead of multiplying rows.
The set must have been published by a successful workflow run.

```kusto
let SharedHosts = dynamic([
    "script.google.com", "script.googleusercontent.com",
    "fastly.jsdelivr.net", "cdn.jsdelivr.net",
    "raw.githubusercontent.com", "github.com", "gitlab.com",
    "gist.github.com", "gist.githubusercontent.com",
    "pastebin.com", "drive.google.com", "docs.google.com",
    "dropbox.com", "www.dropbox.com", "dl.dropboxusercontent.com",
    "storage.googleapis.com", "vercel.app", "pages.dev"
]);
let GetMatchKey = (Value:string) {
    let Parsed = parse_url(
        iff(Value contains "://", Value, strcat("https://", Value))
    );
    let Host = tolower(trim_end(@"\.+", tostring(Parsed.Host)));
    let Path = tostring(Parsed.Path);
    iff(
        isempty(Host),
        "",
        iff(
            Host in (SharedHosts),
            iff(
                isempty(Path) or Path == "/",
                "",
                strcat("url:", Host, Path)
            ),
            strcat("domain:", Host)
        )
    )
};
let clickfixobserver = materialize(
    externaldata(Entry:dynamic)
    [
        "https://raw.githubusercontent.com/benscha/ClickFixObserver/refs/heads/main/data/sets/7D.json"
    ]
    with(
        format="multijson",
        ingestionMapping='[{"Column":"Entry","Properties":{"Path":"$"}}]'
    )
    | mv-expand Indicator = array_concat(
        pack_array(tostring(Entry.UrlDomain)),
        Entry.DeliveryUrl,
        Entry.PayloadUrl,
        Entry.ResolvedDownloadUrl
    ) to typeof(string)
    | extend Indicator = trim(@"\s+", Indicator)
    | where isnotempty(Indicator)
    | extend MatchKey = GetMatchKey(Indicator)
    | where isnotempty(MatchKey)
    | project MatchKey, ReportJson = tostring(Entry)
    | distinct MatchKey, ReportJson
    | extend Entry = parse_json(ReportJson)
    | project MatchKey, Entry
);
let MatchingEmailUrls = EmailUrlInfo
| where Timestamp >= ago(7d)
| where isnotempty(NetworkMessageId) and isnotempty(Url)
| extend MatchKey = GetMatchKey(Url)
| where isnotempty(MatchKey)
| project
    EmailTimestamp = Timestamp,
    NetworkMessageId,
    Url,
    UrlDomain,
    UrlLocation,
    MatchKey;
let EmailClicks = UrlClickEvents
| where Timestamp >= ago(7d)
| where Workload == "Email"
| where isnotempty(NetworkMessageId) and isnotempty(Url)
| project
    NetworkMessageId,
    Url,
    ClickTimestamp = Timestamp,
    AccountUpn,
    ActionType,
    IsClickedThrough,
    ClickIPAddress = IPAddress,
    ClickReportId = ReportId;
MatchingEmailUrls
| join kind=inner (clickfixobserver) on MatchKey
| join kind=inner (EmailClicks) on NetworkMessageId, Url
| summarize
    EmailTimestamps = make_set(EmailTimestamp, 1000),
    UrlLocations = make_set(UrlLocation, 1000),
    ObserverReportIds = make_set(tostring(Entry.ReportId), 1000),
    LureDomains = make_set(tostring(Entry.UrlDomain), 1000),
    OriginalReports = make_set(Entry, 1000)
    by
    NetworkMessageId,
    Url,
    ClickTimestamp,
    MatchKey,
    AccountUpn,
    ActionType,
    IsClickedThrough,
    ClickIPAddress,
    ClickReportId
| extend
    MatchedDomain = tolower(
        trim_end(@"\.+", tostring(parse_url(Url).Host))
    ),
    MatchType = iff(MatchKey startswith "url:", "ExactHostAndPath", "Domain"),
    ReportCount = array_length(ObserverReportIds)
| order by ClickTimestamp desc
```

Hosts are case-insensitive and ignore trailing dots; URL paths remain
case-sensitive. Scheme and query parameters are ignored for indicator matching,
so providers that identify content through query parameters need provider-specific
matching. Shared-host entries without a concrete path are skipped rather than
matching every URL on that service. The shared-host list is not exhaustive and
must be maintained; tenant-specific hosts such as `example.vercel.app` still
use exact domain matching. Subdomains and redirect chains are not expanded.

Clicks are joined on both `NetworkMessageId` and the exact, case-sensitive
full `Url`. Each result groups matching reports for a click ID, timestamp, URL,
account, action, and IP; distinct click events remain distinct. The report and
metadata arrays are capped at 1,000 values each. `ReportCount` counts matching
reports, not attacks or victims. `OriginalReports` preserves the full report
objects, including payloads, URLs, IPs, and scan timestamps.

A match does not prove malicious execution or establish that an indicator was
malicious at the click time. Blocked clicks are included; review `ActionType`
and `IsClickedThrough`. Missing matches do not prove no click occurred.
Coverage depends on Safe Links telemetry, available message identifiers, URL
representation, and the report set. The query has been statically validated
for Defender XDR but has not been executed in a tenant.

### Correlate email clicks, browser connections, and PowerShell execution

This Defender XDR query correlates allowed or clicked-through email links to
seven-day lure domains with successful browser connections by the same user,
followed by suspicious PowerShell activity on the same device. The telemetry
lookback is one day.

```kusto
let Lures = materialize(
    externaldata(Entry:dynamic)
    [
        "https://raw.githubusercontent.com/benscha/ClickFixObserver/refs/heads/main/data/sets/7D.json"
    ]
    with (
        format="multijson",
        ingestionMapping='[{"Column":"Entry","Properties":{"Path":"$"}}]'
    )
    | extend Host = tolower(
        trim_end(@"\.+", tostring(Entry.UrlDomain))
    )
    | where isnotempty(Host)
    | project
        Host,
        ObserverReportId = tostring(Entry.ReportId),
        ScannedAt = todatetime(Entry.ScannedAt)
    | distinct Host, ObserverReportId, ScannedAt
);
let Clicks = UrlClickEvents
| where Timestamp > ago(1d)
| where Workload == "Email"
| where ActionType == "ClickAllowed" or IsClickedThrough == true
| where isnotempty(AccountUpn) and isnotempty(Url)
| extend
    Host = tolower(trim_end(@"\.+", tostring(parse_url(Url).Host))),
    UserKey = tolower(AccountUpn)
| join kind=inner (Lures) on Host
| project
    Host,
    UserKey,
    ClickTime = Timestamp,
    ClickUrl = Url,
    ClickAction = ActionType,
    IsClickedThrough,
    NetworkMessageId,
    ClickReportId = ReportId,
    ObserverReportId,
    ScannedAt;
let BrowserConnections = DeviceNetworkEvents
| where Timestamp > ago(1d)
| where ActionType == "ConnectionSuccess"
| where InitiatingProcessFileName in~ (
    "msedge.exe", "chrome.exe", "firefox.exe", "brave.exe"
)
| where isnotempty(DeviceId)
    and isnotempty(RemoteUrl)
    and isnotempty(InitiatingProcessAccountUpn)
| extend
    Host = tolower(
        trim_end(
            @"\.+",
            tostring(
                parse_url(
                    iff(
                        RemoteUrl contains "://",
                        RemoteUrl,
                        strcat("https://", RemoteUrl)
                    )
                ).Host
            )
        )
    ),
    UserKey = tolower(InitiatingProcessAccountUpn)
| project
    Host,
    UserKey,
    DeviceId,
    NetworkTime = Timestamp,
    RemoteUrl,
    RemoteIP;
let SuspiciousProcesses = DeviceProcessEvents
| where Timestamp > ago(1d)
| where ActionType == "ProcessCreated"
| where FileName in~ ("powershell.exe", "pwsh.exe")
    and InitiatingProcessFileName =~ "explorer.exe"
| where ProcessCommandLine matches regex
    @"(?i)\b(irm|iwr|invoke-restmethod|invoke-webrequest)\b"
| where ProcessCommandLine matches regex
    @"(?i)\b(iex|invoke-expression)\b|(-outfile\b.*\.msi\b)"
| where isnotempty(AccountUpn) and isnotempty(DeviceId)
| extend UserKey = tolower(AccountUpn)
| project
    Timestamp,
    ReportId,
    DeviceId,
    DeviceName,
    UserKey,
    AccountUpn,
    ProcessCommandLine;
Clicks
| join kind=inner (BrowserConnections) on Host, UserKey
| where NetworkTime >= ClickTime - 2m and NetworkTime <= ClickTime + 10m
| join kind=inner (
    SuspiciousProcesses
) on DeviceId, UserKey
| where Timestamp >= ClickTime and Timestamp <= ClickTime + 30m
| where NetworkTime <= Timestamp + 1m
| project
    Timestamp,
    ReportId,
    DeviceId,
    DeviceName,
    AccountUpn,
    ProcessCommandLine,
    ClickTime,
    ClickUrl,
    ClickAction,
    IsClickedThrough,
    NetworkMessageId,
    ClickReportId,
    NetworkTime,
    RemoteUrl,
    RemoteIP,
    Host,
    ObserverReportId,
    ScannedAt
```

The correlation uses exact, case-insensitive hosts and matching UPNs. It does
not prove that the click caused the process, and the click may have occurred
on another device. Redirects, missing UPNs or telemetry, other browsers, and
other command patterns can prevent matches. Multiple matching events or
reports can produce multiple rows. Test and tune the query in your tenant
before creating a scheduled custom detection. It has been statically
validated for Defender XDR but not executed in a tenant.

### Correlate processes with connections to delivery domains

This Defender XDR query correlates successful connections to seven-day delivery
domains with selected processes on the same device, using their unique process
identifiers. The telemetry lookback is one day, and the connection must occur
within one hour of process creation.

```kusto
let IocDomains = materialize(
    externaldata(Entry:dynamic)
    [
        "https://raw.githubusercontent.com/benscha/ClickFixObserver/refs/heads/main/data/sets/7D.json"
    ]
    with (
        format="multijson",
        ingestionMapping='[{"Column":"Entry","Properties":{"Path":"$"}}]'
    )
    | mv-expand Indicator = Entry.DeliveryUrl to typeof(string)
    | extend Host = tolower(
        trim_end(
            @"\.+",
            tostring(
                parse_url(
                    iff(
                        Indicator contains "://",
                        Indicator,
                        strcat("https://", Indicator)
                    )
                ).Host
            )
        )
    )
    | where isnotempty(Host)
    | where Host !in (
        "fastly.jsdelivr.net",
        "cdn.jsdelivr.net",
        "raw.githubusercontent.com",
        "github.com",
        "pastebin.com",
        "drive.google.com",
        "dropbox.com",
        "storage.googleapis.com"
    )
    | project
        Host,
        ObserverReportId = tostring(Entry.ReportId),
        ScannedAt = todatetime(Entry.ScannedAt)
    | distinct Host, ObserverReportId, ScannedAt
);
let Procs = DeviceProcessEvents
| where Timestamp > ago(1d)
| where ActionType == "ProcessCreated"
| where FileName in~ (
    "powershell.exe", "pwsh.exe", "cmd.exe", "mshta.exe",
    "wscript.exe", "cscript.exe", "curl.exe", "msiexec.exe"
)
| where isnotempty(DeviceId) and isnotempty(ProcessUniqueId)
| project
    DeviceId,
    ProcessKey = ProcessUniqueId,
    ProcessTime = Timestamp,
    ProcessCommandLine,
    FileName,
    AccountUpn;
DeviceNetworkEvents
| where Timestamp > ago(1d)
| where ActionType == "ConnectionSuccess"
| where isnotempty(DeviceId)
    and isnotempty(InitiatingProcessUniqueId)
    and isnotempty(RemoteUrl)
| extend Host = tolower(
    trim_end(
        @"\.+",
        tostring(
            parse_url(
                iff(
                    RemoteUrl contains "://",
                    RemoteUrl,
                    strcat("https://", RemoteUrl)
                )
            ).Host
        )
    )
)
| join kind=inner (IocDomains) on Host
| join kind=inner (Procs)
    on DeviceId, $left.InitiatingProcessUniqueId == $right.ProcessKey
| where Timestamp >= ProcessTime and Timestamp <= ProcessTime + 1h
| project
    Timestamp,
    ReportId,
    DeviceId,
    DeviceName,
    RemoteUrl,
    RemoteIP,
    Host,
    FileName,
    ProcessCommandLine,
    AccountUpn,
    ProcessTime,
    ObserverReportId,
    ScannedAt
```

Hosts are matched exactly and case-insensitively; subdomains are not included
automatically. The shared-service exclusions are not exhaustive, so review
delivery domains and tune the list before alerting. A successful connection
does not prove that downloaded content was executed. Missing process keys or
`RemoteUrl`, IP-only connections, excluded hosts, and long-running processes
can prevent matches; multiple reports or connections can produce multiple
rows. Test and tune the query in your tenant before creating a scheduled
custom detection. It has been statically validated for Defender XDR but not
executed in a tenant.
