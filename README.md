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

## Payload patterns

Every successful export rebuilds `data/patterns.json` and `data/patterns.txt`
from the seven-day set and adds newly discovered regex to
`data/patterns.regex.json`, without additional downloads.
The existing workflow commits all three files under `data`; unchanged contents
are not rewritten. All nonempty payloads are included, including singletons,
long scripts, and payloads without recognizable URLs.

Normalization replaces recognizable HTTP(S) URLs and schemaless domain URLs
directly following `irm`, `iwr`, `Invoke-RestMethod`, or `Invoke-WebRequest`
with `<URL>`. Defanged `[.]` dots are refanged, line endings are normalized,
and outer whitespace is trimmed. Specific families receive additional
normalization:

- For `curl -d` telemetry followed by a shell pipeline, UUID-valued `id`,
  percent-encoded `siteUrl`, and ISO UTC `lastVisitAt`/`lastShellAt` fields
  inside the quoted POST argument become `<UUID>`, `<ENCODED_URL>`, and
  `<TIMESTAMP>`. Field names, location, OS, downloader flags, and shell
  execution structure remain intact. Curl and wget variants stay separate.
- For the PHP inventory prefix `$tok=...;$id=...;$interval=...;` with
  `function _ea_inv`, token and hex ID values become `<TOKEN>` and `<HEX_ID>`.
  Whitespace outside quoted strings is collapsed; the interval and code
  remain intact. These are server-side content templates, not Windows
  process detections.
- WUA user-agent and `X-WUA` header hex values (8-32 characters) become
  `<WUA_ID>`, preserving the header names and command structure. The regex
  does not enforce equality between the two IDs.
- Clipboard/encoded PowerShell with `Get-Clipboard`, `FromBase64String`,
  `Unicode.GetString`, and `Invoke-Expression $clean` groups the ASCII banner,
  base64 assignment to `$updateloadUrl`, and millisecond sleep values.
  Placeholders are constrained by character sets and lengths; other code
  and quoted messages are retained.

Other case, quotes, internal whitespace, variable names, filenames, IDs, and
encoded content are preserved. This is
more conservative than the KQL template example below, so its pattern count
can differ. It is lexical grouping, not a parser or executable detection rule.
Split/obfuscated URLs, some URL punctuation, and schemaless URLs in other
contexts can be missed. Never execute the payloads or templates.

`patterns.json` is an array of objects containing `Pattern`,
`DistinctPayloadCount`, `ReportCount`, `FirstSeen`, `LastSeen`, `ReportIds`,
and all distinct original `PayloadExamples`. Patterns are sorted by variant
count, report count, then text. Counts refer to commands and reports, not
victims or independent campaigns; scan timestamps cover only the loaded set.
The files inherit the rolling set's coverage and freshness limitations.

`patterns.txt` contains one template per physical line in the same order.
Each template uses JSON string escaping, without the surrounding quotes:
newlines appear as `\n`, backslashes as `\\`, and quotes as `\"`.
Use the JSON file for programmatic consumption and multiline scripts.

`patterns.regex.json` contains only a JSON array of regex strings, without
objects, metadata, report IDs, or original payload examples. Metadata and
examples for the current seven-day set remain in `patterns.json`.
The regex array is cumulative: existing entries and their order are preserved,
and new expressions are appended only if the exact string is not already
present. Regexes are not removed when reports leave the rolling set.
Without additions, the file is not rewritten, even if its formatting differs.
Malformed JSON, non-string entries, or invalid existing Python regex cause
failure before output files are written. Historical regex may outlive their
metadata and require manual review or removal when obsolete.
Regex literals are
escaped; only the defined placeholders become constrained expressions.
Whitespace matching is flexible (PHP outside quoted strings), but case is
preserved. Regexes use dot-all mode and substring matching, without lookarounds
or backreferences. Consumers must refang `[.]` to `.` before matching.
Unlike the escaped TXT templates, each JSON string is the actual regex
after JSON decoding.

Every generated regex is compiled and checked against all of its original
payload examples before files are written. Failure stops the export instead
of silently omitting a pattern. This checks Python regex behavior, not live
KQL execution. These expressions are analysis candidates, not automatically
high-fidelity rules: quoted code can match, long scripts can be expensive,
and partial source scripts are not proof of complete executable payloads.
Keep server-side content separate from endpoint command-line detections.
The TXT file remains a template list, so existing consumers are not silently
switched to regex. Consumers using exact normalized-template comparisons must
implement the new field placeholders to match those newly grouped families.

Regex generation also combines simple `irm URL | iex` forms, with or without
parentheses, into one expression with scoped case-insensitive command names
and flexible quote/whitespace syntax. This affects new generation too: future
variants covered by these rules produce an existing regex and are not appended.
Unknown structures remain separate rather than being generalized blindly.

All three generated pattern filenames are lowercase. GitHub Raw URLs are
case-sensitive: update existing consumers from `Patterns.*` to `patterns.*`.
Contents, JSON keys, and regex escapes are not lowercased.

An explicit one-time consolidation is available:

```powershell
python export_clickfix.py --patterns-only --consolidate-regex-history
```

This uses existing `patterns.json` provenance to replace known historical
expressions with newly generated, verified equivalents and removes exact
duplicates. Unknown historical expressions are retained. It must run before
overwriting that provenance with a normal refresh. Subsequent normal runs
remain append-only; consolidation is not enabled in the scheduled workflow.

To rebuild patterns locally from the existing set without accessing the network:

```powershell
python export_clickfix.py --patterns-only
```

This mode uses the existing `data/sets/7D.json` as-is; it does not refresh its
time window. Missing or malformed input fails explicitly. Pattern input is
validated before normal exports start writing daily files and sets. A failed
workflow leaves the previously committed files in place.

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
followed by suspicious command execution under `explorer.exe` on the same
device. The telemetry lookback is one day. Beyond `powershell.exe`/`pwsh.exe`,
the suspicious-process check also covers `cmd.exe`, `mshta.exe`,
`wscript.exe`, `cscript.exe`, `msiexec.exe`, `rundll32.exe`, and
`regsvr32.exe`, matching the binary list already used in the delivery-domain
correlation below. PowerShell is held to the stricter `irm|iwr ... | iex`
pattern; the other binaries are flagged when their command line carries any
URL.

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
| where InitiatingProcessFileName =~ "explorer.exe"
| where FileName in~ (
    "powershell.exe", "pwsh.exe", "cmd.exe", "mshta.exe",
    "wscript.exe", "cscript.exe", "msiexec.exe",
    "rundll32.exe", "regsvr32.exe"
)
| where
    (FileName in~ ("powershell.exe", "pwsh.exe")
        and ProcessCommandLine matches regex
            @"(?i)\b(irm|iwr|invoke-restmethod|invoke-webrequest)\b"
        and (ProcessCommandLine matches regex @"(?i)\b(iex|invoke-expression)\b"
            or ProcessCommandLine matches regex @"(?i)-outfile\b.*\.msi\b"))
    or (FileName !in~ ("powershell.exe", "pwsh.exe")
        and (ProcessCommandLine contains "http://" or ProcessCommandLine contains "https://"))
| where isnotempty(AccountUpn) and isnotempty(DeviceId)
| extend UserKey = tolower(AccountUpn)
| project
    Timestamp,
    ReportId,
    DeviceId,
    DeviceName,
    UserKey,
    AccountUpn,
    FileName,
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
    FileName,
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
other command patterns can prevent matches. The non-PowerShell binaries are
matched on "any URL in the command line" rather than a specific execution
verb, so legitimate installers or downloads triggered through `explorer.exe`
can still produce false positives; review and tighten this per binary before
alerting. Multiple matching events or reports can produce multiple rows. Test
and tune the query in your tenant before creating a scheduled custom
detection. It has been statically validated for Defender XDR but not executed
in a tenant.

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

### Find payload URLs in process command lines

This Defender XDR query searches one day of selected process creation events
for concrete URLs from `PayloadUrl` and `ResolvedDownloadUrl` in the seven-day
report set. It matches host, explicit port, path, and query string rather than
just the domain, and groups matching reports per process event.

```kusto
let PayloadKey = (Value:string) {
    let Parsed = parse_url(Value);
    let Host = tolower(trim_end(@"\.+", tostring(Parsed.Host)));
    let Path = tostring(Parsed.Path);
    let Port = tostring(Parsed.Port);
    let Query = extract(@"\?([^#]*)", 1, Value);
    iff(
        isempty(Host)
            or ((isempty(Path) or Path == "/") and isempty(Query)),
        "",
        strcat(
            Host,
            iff(isempty(Port), "", strcat(":", Port)),
            Path,
            iff(isempty(Query), "", strcat("?", Query))
        )
    )
};
let PayloadIndicators = materialize(
    externaldata(Entry:dynamic)
    [
        "https://raw.githubusercontent.com/benscha/ClickFixObserver/refs/heads/main/data/sets/7D.json"
    ]
    with(
        format="multijson",
        ingestionMapping='[{"Column":"Entry","Properties":{"Path":"$"}}]'
    )
    | mv-expand PayloadUrl = array_concat(
        Entry.PayloadUrl,
        Entry.ResolvedDownloadUrl
    ) to typeof(string)
    | extend PayloadUrl = trim(@"\s+", PayloadUrl)
    | where PayloadUrl startswith "https://"
        or PayloadUrl startswith "http://"
    | extend MatchKey = PayloadKey(PayloadUrl)
    | where isnotempty(MatchKey)
    | project
        MatchKey,
        PayloadUrl,
        ReportJson = tostring(Entry)
    | distinct MatchKey, PayloadUrl, ReportJson
    | extend Entry = parse_json(ReportJson)
);
DeviceProcessEvents
| where Timestamp >= ago(1d)
| where ActionType == "ProcessCreated"
| where FileName in~ (
    "powershell.exe", "pwsh.exe", "cmd.exe",
    "curl.exe", "wget.exe", "mshta.exe", "msiexec.exe",
    "rundll32.exe", "regsvr32.exe",
    "wscript.exe", "cscript.exe",
    "certutil.exe", "bitsadmin.exe",
    "bash", "sh", "zsh", "curl", "wget", "pwsh"
)
| where ProcessCommandLine contains "http://"
    or ProcessCommandLine contains "https://"
| project
    Timestamp,
    ReportId,
    DeviceId,
    DeviceName,
    AccountUpn,
    AccountSid,
    FileName,
    ProcessCommandLine,
    InitiatingProcessFileName,
    InitiatingProcessCommandLine
| extend CommandUrls = extract_all(
    @"(?i)(https?://[^\s""'<>|;`()]+)",
    ProcessCommandLine
)
| mv-expand CommandUrl = CommandUrls to typeof(string)
| extend MatchKey = PayloadKey(CommandUrl)
| where isnotempty(MatchKey)
| join kind=inner (PayloadIndicators) on MatchKey
| summarize
    MatchedCommandUrls = make_set(CommandUrl, 1000),
    MatchedPayloadUrls = make_set(PayloadUrl, 1000),
    ObserverReportIds = make_set(tostring(Entry.ReportId), 1000),
    ObserverPayloadCommands = make_set(tostring(Entry.Payload), 1000),
    OriginalReports = make_set(Entry, 1000)
    by
    Timestamp,
    ReportId,
    DeviceId,
    DeviceName,
    AccountUpn,
    AccountSid,
    FileName,
    ProcessCommandLine,
    InitiatingProcessFileName,
    InitiatingProcessCommandLine
| extend ObserverReportCount = array_length(ObserverReportIds)
```

Hosts are case-insensitive; paths and query strings remain case-sensitive.
Scheme and fragments are ignored, but query parameter order is preserved.
Root URLs without a query are skipped. Shared services remain eligible when
their concrete URLs match. The arrays are capped at 1,000 values each.

A match proves only that a selected process was started with a known payload
URL in its command line, not that the download or payload execution succeeded.
Obfuscated, encoded, dynamically assembled, or schemaless URLs can be missed,
as can changed query parameters and URLs the extraction regex cannot capture.
Test and tune in your tenant before creating a scheduled custom detection.
The query has been statically validated for Defender XDR but has not been
executed in a tenant.

### Generate recurring payload templates and hunt for new URLs

This hunting query derives recurring PowerShell download-and-execute templates
from the seven-day report set and searches one day of process creation events.
Both payloads and command lines use the same URL placeholder, allowing matches
on new infrastructure. It normalizes case, whitespace, and trailing semicolons.
Only templates with at least two distinct payload commands are used.

```kusto
let MinDistinctPayloads = 2;
let NormalizePayload = (Text:string) {
    let Refanged = replace_string(
        replace_string(Text, "[.]", "."),
        "&amp;",
        "&"
    );
    let DownloadSlot = replace_regex(
        Refanged,
        @"(?i)\b(irm|iwr|invoke-restmethod|invoke-webrequest)\s+[""']?(?:https?://)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d+)?(?:/[^\s""'<>|;`()]+)?[""']?",
        @"\1 __url__"
    );
    let UrlsReplaced = replace_regex(
        DownloadSlot,
        @"(?i)https?://[^\s""'<>|;`()]+",
        "__url__"
    );
    trim_end(
        @";+",
        tolower(replace_regex(UrlsReplaced, @"\s+", ""))
    )
};
let Patterns = materialize(
    externaldata(Entry:dynamic)
    [
        "https://raw.githubusercontent.com/benscha/ClickFixObserver/refs/heads/main/data/sets/7D.json"
    ]
    with(
        format="multijson",
        ingestionMapping='[{"Column":"Entry","Properties":{"Path":"$"}}]'
    )
    | project
        Payload = tostring(Entry.Payload),
        ObserverReportId = tostring(Entry.ReportId)
    | where isnotempty(Payload) and strlen(Payload) <= 2000
    | where Payload matches regex
        @"(?i)^\s*\(?\s*(irm|iwr|invoke-restmethod|invoke-webrequest)\b"
    | where Payload matches regex
        @"(?i)\b(iex|invoke-expression)\b"
    | extend
        Downloader = tolower(
            extract(
                @"(?i)\b(irm|iwr|invoke-restmethod|invoke-webrequest)\b",
                1,
                Payload
            )
        ),
        Pattern = NormalizePayload(Payload)
    | where Pattern contains "__url__"
    | summarize
        PayloadExamples = make_set(Payload, 1000),
        ObserverReportIds = make_set(ObserverReportId, 1000)
        by Downloader, Pattern
    | extend DistinctPayloadCount = array_length(PayloadExamples)
    | where DistinctPayloadCount >= MinDistinctPayloads
);
DeviceProcessEvents
| where Timestamp >= ago(1d)
| where ActionType == "ProcessCreated"
| where FileName in~ ("powershell.exe", "pwsh.exe")
| where ProcessCommandLine has_any (
    "irm", "iwr", "Invoke-RestMethod", "Invoke-WebRequest"
)
| where ProcessCommandLine has_any ("iex", "Invoke-Expression")
| extend
    Downloader = tolower(
        extract(
            @"(?i)\b(irm|iwr|invoke-restmethod|invoke-webrequest)\b",
            1,
            ProcessCommandLine
        )
    ),
    NormalizedCommand = NormalizePayload(ProcessCommandLine)
| join kind=inner (Patterns) on Downloader
| where NormalizedCommand contains_cs Pattern
| summarize
    MatchedPatterns = make_set(Pattern, 1000),
    PatternEvidence = make_set(
        pack(
            "Pattern", Pattern,
            "DistinctPayloadCount", DistinctPayloadCount,
            "ObserverReportIds", ObserverReportIds
        ),
        1000
    )
    by
    Timestamp,
    ReportId,
    DeviceId,
    DeviceName,
    AccountUpn,
    FileName,
    ProcessCommandLine,
    InitiatingProcessFileName
| extend ExplorerStarted = InitiatingProcessFileName =~ "explorer.exe"
```

To inspect only the generated templates, replace the entire section starting
with `DeviceProcessEvents` with:

```kusto
Patterns
| project Pattern, DistinctPayloadCount, PayloadExamples, ObserverReportIds
| order by DistinctPayloadCount desc
```

This is lexical normalization, not a PowerShell parser. Legitimate
administration and quoted code can match; `ExplorerStarted` is context, not
proof of ClickFix. Obfuscation, variables, alternative spellings, and changed
parameters can prevent matches. MSI, curl/shell, and encoded payloads need
separate template families. Counts describe distinct commands, not victims
or independent campaigns; arrays are capped at 1,000 values each. The coarse
join on downloader can be expensive if many templates qualify. Review the
generated templates and baseline results before using them for alerting.
The query has been statically validated for Defender XDR, and equivalent
normalization was tested locally, but it has not been executed in a tenant.

### Hunt for regex matches in Explorer-started processes

This query loads the cumulative regex array as text and JSON-decodes each
line. This import format was successfully tested in Defender Advanced Hunting;
the direct `multijson` scalar mapping previously failed at execution time.
All nonempty regex are combined into a single constant expression.

It searches one hour of processes started directly by Explorer or through
`explorer.exe -> cmd.exe -> process`. Each distinct command line is evaluated
once, then all matching process events are returned.

```kusto
let RegexPatterns = materialize(
    externaldata(Line:string)
    [
        "https://raw.githubusercontent.com/benscha/ClickFixObserver/refs/heads/main/data/patterns.regex.json"
    ]
    with(format="txt")
    | extend JsonString = trim(@"\s+", Line)
    | where JsonString != "[" and JsonString != "]"
    | where isnotempty(JsonString)
    | extend JsonString = trim_end(@",", JsonString)
    | extend Regex = tostring(parse_json(JsonString))
    | where isnotempty(Regex)
    | distinct Regex
    | project Alternative = strcat(
        "(?:",
        replace_regex(Regex, @"^\(\?s\)", ""),
        ")"
    )
);
let PatternCount = toscalar(RegexPatterns | count);
let CombinedRegex = toscalar(
    RegexPatterns
    | summarize Alternatives = make_list(Alternative, 1000000)
    | project Expression = strcat(
        "(?s)(?:",
        strcat_array(Alternatives, "|"),
        ")"
    )
);
let Processes = materialize(
    DeviceProcessEvents
    | where Timestamp >= ago(1h)
    | where ActionType == "ProcessCreated"
    | where isnotempty(ProcessCommandLine)
    | where InitiatingProcessFileName =~ "explorer.exe"
        or (
            InitiatingProcessFileName =~ "cmd.exe"
            and InitiatingProcessParentFileName =~ "explorer.exe"
        )
    | project
        Timestamp,
        ReportId,
        DeviceId,
        DeviceName,
        AccountUpn,
        FileName,
        ProcessCommandLine,
        InitiatingProcessFileName,
        InitiatingProcessCommandLine
);
let MatchingCommands = Processes
| distinct ProcessCommandLine
| where PatternCount > 0
| extend MatchInput = replace_string(ProcessCommandLine, "[.]", ".")
| where MatchInput matches regex CombinedRegex
| project ProcessCommandLine;
Processes
| join kind=leftsemi (MatchingCommands) on ProcessCommandLine
```

The text import requires the exporter's format: one JSON string per line,
with array brackets on separate lines. JSON escapes are decoded before regex
matching. `matches regex` requires a constant expression, not a regex column
from a join; `toscalar` builds that expression. The empty-pattern guard
prevents an empty alternation from matching every command.

The parent filter is compatible with Run-dialog launches, but also includes
other Explorer launches and excludes deeper process chains. It does not prove
Win+R or malicious execution. Matching is case-sensitive except where a regex
explicitly scopes case-insensitive command names. Obfuscated
commands or scripts absent from command-line telemetry may be missed. Results
do not identify which individual regex matched. A growing cumulative regex
array and larger time windows can increase compilation cost and materialize
memory use. This query was statically validated and successfully executed by
the user in Defender Advanced Hunting; detection accuracy and scheduled-rule
behavior still require tenant-specific testing.

### Correlate RunMRU paste-and-run with Explorer-launched processes

This Defender XDR query targets the "Win+R, paste, Enter" step itself,
independent of the IOC feed: a write to the Explorer `RunMRU` registry key
followed shortly by a matching process launched directly by `explorer.exe`.
The telemetry lookback is one day.

```kusto
let LookbackWindow = 1d;
let SuspiciousBinaries = dynamic([
    "powershell.exe", "pwsh.exe", "cmd.exe", "mshta.exe",
    "wscript.exe", "cscript.exe", "msiexec.exe",
    "rundll32.exe", "regsvr32.exe", "curl.exe"
]);
let RunMRUWrites = DeviceRegistryEvents
| where Timestamp > ago(LookbackWindow)
| where ActionType == "RegistryValueSet"
| where RegistryKey contains @"\Software\Microsoft\Windows\CurrentVersion\Explorer\RunMRU"
| where RegistryValueName !in~ ("MRUList", "MRUListEx")
| extend RunCommand = trim(@"\\\d+$", tostring(RegistryValueData))
| where strlen(RunCommand) > 15
    or RunCommand contains "http://" or RunCommand contains "https://"
    or RunCommand matches regex @"(?i)\b(irm|iwr|iex|invoke-expression|invoke-restmethod|invoke-webrequest)\b"
| project
    DeviceId,
    DeviceName,
    RunMRUTime = Timestamp,
    RunMRUAccount = InitiatingProcessAccountUpn,
    RunCommand;
let SuspiciousExplorerChildren = DeviceProcessEvents
| where Timestamp > ago(LookbackWindow)
| where ActionType == "ProcessCreated"
| where InitiatingProcessFileName =~ "explorer.exe"
| where FileName in~ (SuspiciousBinaries)
| project
    ProcessTime = Timestamp,
    ReportId,
    DeviceId,
    DeviceName,
    AccountUpn,
    FileName,
    ProcessCommandLine,
    InitiatingProcessFileName;
RunMRUWrites
| join kind=inner (SuspiciousExplorerChildren) on DeviceId
| where ProcessTime >= RunMRUTime - 15s and ProcessTime <= RunMRUTime + 2m
| project
    RunMRUTime,
    ProcessTime,
    DeviceId,
    DeviceName,
    RunMRUAccount,
    AccountUpn,
    RunCommand,
    FileName,
    ProcessCommandLine,
    InitiatingProcessFileName
| order by ProcessTime desc
```

`RunMRU` legacy and extended value names (`MRUList`, `MRUListEx`) hold only
the entries' display order, not a command, and are excluded. Each value's
trailing `\<slot number>` marker is stripped before evaluation. The
`strlen(RunCommand) > 15` clause, together with the URL/PowerShell-verb
checks, exists specifically to drop short, everyday Run-dialog commands
(`cmd`, `powershell`, `regedit`) that would otherwise dominate the results;
tune the threshold and binary list against your own baseline. This query does
not use the ClickFix infrastructure feed, so it has no concept of report
freshness or known infrastructure, it is a generic paste-and-run heuristic and
will also fire on legitimate but unusual Run-dialog usage. It has not been
executed in a tenant; validate and tune before using it for alerting.

## Data Source & Attribution

All Indicators of Compromise (IOCs) processed and formatted by this repository are sourced from **[clickfix.observer](https://clickfix.observer)**.

This project acts purely as an automated parser and formatter to make these community-curated threat intelligence feeds easily consumable in Microsoft Sentinel and Microsoft Defender XDR via KQL (`externaldata`). All credit for the discovery, tracking, and curation of the underlying IOCs belongs to the maintainers of `clickfix.observer`.

## Disclaimer

* **No Warranty:** The scripts and formatted indicator lists in this repository are provided *"as is"*, without warranty of any kind, express or implied.

* **False Positives:** Threat intelligence feeds can occasionally contain legitimate domains, shared hosting infrastructure, or outdated indicators. Always validate and test these feeds in **hunting or audit mode** before using them for automated blocking or containment actions (e.g., via Defender Indicator API).
* **Takedown / Removal:** If you are the maintainer of `clickfix.observer` and prefer that these formatted outputs are not hosted in this repository, please open an issue or reach out directly, and the hosted data files will be removed immediately.
