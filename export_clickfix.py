#!/usr/bin/env python3
"""Export infrastructure from publicly listed clickfix.observer reports."""

import argparse
import ipaddress
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import IncompleteRead
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


BASE_URL = "https://clickfix.observer"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
USER_AGENT = "ClickFixInfrastructureExport/1.0"
REPORT_PATH = re.compile(r"^/report/[a-zA-Z0-9]+$")
SET_DAYS = (*range(1, 11), 14, 30)
PAYLOAD_URL = re.compile(r"""https?://[^\s"'<>|;`()]+""", re.IGNORECASE)
DOWNLOAD_URL = re.compile(
    r"""(\b(?:irm|iwr|invoke-restmethod|invoke-webrequest)\s+["']?)"""
    r"""(?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*(?::\d+)?"""
    r"""(?:/[^\s"'<>|;`()]+)?""",
    re.IGNORECASE,
)
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list = field(default_factory=list)

    def descendants(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.descendants()

    def raw_text(self):
        return "".join(
            child.raw_text() if isinstance(child, Node) else child
            for child in self.children
        )

    def text(self):
        return self.raw_text().strip()

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()


class InfrastructureParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = None
        self.stack = []
        self.payload_sections = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self.stack:
            if tag == "details" and attrs.get("id") == "infrastructure":
                if self.root is not None:
                    raise ValueError("Multiple infrastructure sections found")
                self.root = Node(tag, attrs)
                self.stack.append(self.root)
            elif tag == "section" and "obs-alert-danger" in attrs.get("class", "").split():
                section = Node(tag, attrs)
                self.payload_sections.append(section)
                self.stack.append(section)
            return
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and self.stack:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.stack:
            if self.stack[-1].tag != tag:
                raise ValueError(f"Unexpected closing tag in infrastructure: {tag}")
            self.stack.pop()

    def handle_data(self, data):
        if self.stack:
            self.stack[-1].children.append(data)


def defang_to_plain(value):
    return value.strip().replace("[", "").replace("]", "")


def is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


class IncompleteReportError(ValueError):
    pass


def parse_infrastructure(html):
    parser = InfrastructureParser()
    parser.feed(html)
    parser.close()
    if parser.root is None or parser.stack:
        raise IncompleteReportError(
            "Missing or incomplete infrastructure section "
            f"(HTML characters: {len(html)}, section found: {parser.root is not None}, "
            f"unclosed tags: {[node.tag for node in parser.stack]})"
        )

    columns = {}
    for column in parser.root.descendants():
        if not column.has_class("obs-infra-column"):
            continue
        labels = [node.text() for node in column.descendants() if node.has_class("obs-label")]
        if len(labels) != 1 or labels[0] in columns:
            raise ValueError("Unexpected infrastructure column labels")
        values = []
        for node in column.descendants():
            if not node.has_class("obs-infra-node"):
                continue
            values_nodes = [item for item in node.descendants() if item.has_class("obs-data")]
            if len(values_nodes) != 1:
                raise ValueError("Unexpected infrastructure node")
            value = defang_to_plain(values_nodes[0].text())
            if not value:
                raise ValueError("Empty infrastructure value")
            values.append(value)
        columns[labels[0]] = values

    if set(columns) != {"Lure", "Delivery", "Payload"} or len(columns["Lure"]) != 1:
        raise ValueError("Unexpected infrastructure columns or lure count")

    ips = []

    def collect_ips(values):
        remaining = []
        for value in values:
            if is_ip(value):
                if value not in ips:
                    ips.append(value)
            else:
                remaining.append(value)
        return remaining

    lure = collect_ips(columns["Lure"])
    delivery = collect_ips(columns["Delivery"])
    payload_urls = collect_ips(columns["Payload"])

    related = []
    for node in parser.root.descendants():
        if not node.has_class("obs-infra-related"):
            continue
        values = [item.text() for item in node.descendants() if item.has_class("obs-data")]
        tags = [item.text() for item in node.descendants() if item.has_class("obs-badge")]
        if len(values) != 1 or len(tags) != 1 or not values[0] or not tags[0]:
            raise ValueError("Unexpected related infrastructure item")
        value = defang_to_plain(values[0])
        if collect_ips([value]):
            related.append({"Value": value, "Type": tags[0]})
        else:
            related.append({"IP": value, "Type": tags[0]})

    payload_sections = [
        section for section in parser.payload_sections
        if any(node.tag == "h2" and node.text() == "Extracted payload"
               for node in section.descendants())
    ]
    if len(payload_sections) > 1:
        raise ValueError("Multiple extracted payload sections found")
    command = None
    resolved_urls = []
    if payload_sections:
        section = payload_sections[0]
        commands = [node.raw_text() for node in section.descendants() if node.tag == "pre"]
        if len(commands) != 1 or not commands[0].strip():
            raise ValueError("Unexpected extracted payload command")
        command = commands[0]
        for node in section.descendants():
            if node.tag != "div":
                continue
            labels = [child for child in node.children
                      if isinstance(child, Node) and child.has_class("obs-label")]
            if not any(label.text() in {"Resolved download URL", "Resolved download URLs"}
                       for label in labels):
                continue
            lists = [child for child in node.children
                     if isinstance(child, Node) and child.tag == "ul"]
            if len(lists) != 1:
                raise ValueError("Unexpected resolved download URL list")
            for item in lists[0].children:
                if isinstance(item, Node) and item.tag == "li":
                    value = defang_to_plain(item.text())
                    if not value:
                        raise ValueError("Empty resolved download URL")
                    resolved_urls.append(value)
    resolved_urls = collect_ips(resolved_urls)

    return {
        "UrlDomain": lure[0] if lure else None,
        "DeliveryUrl": delivery,
        "PayloadUrl": payload_urls,
        "IP": ips,
        "Related": related,
        "Payload": command,
        "ResolvedDownloadUrl": resolved_urls,
    }


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def retry_delay(error, attempt):
    delay = 5 * attempt
    if not isinstance(error, urllib.error.HTTPError):
        return delay
    retry_after = error.headers.get("Retry-After")
    if retry_after is None:
        return delay
    try:
        if retry_after.strip().isdigit():
            return max(delay, int(retry_after))
        date = parsedate_to_datetime(retry_after)
        if date.tzinfo is None:
            raise ValueError("Missing timezone")
        return max(delay, (date - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError) as exc:
        print(
            f"WARNING: Invalid Retry-After header {retry_after!r}: {exc}; "
            f"waiting {delay}s instead.",
            file=sys.stderr, flush=True,
        )
        return delay


def fetch_report(url):
    for attempt in range(1, 4):
        try:
            return parse_infrastructure(fetch(url).decode("utf-8"))
        except (IncompleteReportError, urllib.error.URLError, TimeoutError, IncompleteRead) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in {
                429, 500, 502, 503, 504,
            }:
                raise RuntimeError(f"Failed to process {url}: {exc}") from exc
            if attempt == 3:
                raise RuntimeError(
                    f"Failed to process {url} after {attempt} attempts: {exc}"
                ) from exc
            delay = retry_delay(exc, attempt)
            print(
                f"WARNING: Attempt {attempt}/3 failed for {url}: {exc}; "
                f"retrying in {delay:g}s.",
                file=sys.stderr, flush=True,
            )
            time.sleep(delay)
        except ValueError as exc:
            raise RuntimeError(f"Failed to process {url}: {exc}") from exc


def parse_sitemap(data):
    root = ET.fromstring(data)
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    if root.tag != f"{{{namespace['s']}}}urlset":
        raise ValueError("Expected a sitemap urlset")
    reports = []
    seen = set()
    for item in root.findall("s:url", namespace):
        loc = item.findtext("s:loc", default="", namespaces=namespace)
        path = urlsplit(loc if "://" in loc else f"https://{loc}").path
        if not path.startswith("/report/"):
            continue
        if not REPORT_PATH.fullmatch(path) or path in seen:
            raise ValueError(f"Invalid or duplicate report path: {path}")
        stamp = item.findtext("s:lastmod", default="", namespaces=namespace)
        date = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError(f"Missing timezone for {path}")
        reports.append((path, date.astimezone(timezone.utc)))
        seen.add(path)
    if not reports:
        raise ValueError("Sitemap contains no reports")
    if any(a[1] < b[1] for a, b in zip(reports, reports[1:])):
        raise ValueError("Sitemap reports are not ordered newest first")
    return reports


def load_existing_entries(path):
    with path.open(encoding="utf-8") as file:
        entries = json.load(file)
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError(f"Expected an array of objects in {path}")
    report_ids = set()
    legacy = []
    for entry in entries:
        if "ReportId" not in entry:
            if not {"UrlDomain", "DeliveryUrl", "PayloadUrl"}.issubset(entry) or not set(entry).issubset({
                "UrlDomain", "DeliveryUrl", "PayloadUrl", "IP", "Related",
                "Payload", "ResolvedDownloadUrl",
            }):
                raise ValueError(f"Cannot identify legacy entry in {path}")
            legacy.append(entry)
        else:
            report_id = entry["ReportId"]
            if (not isinstance(report_id, str) or not REPORT_PATH.fullmatch("/report/" + report_id)
                    or report_id in report_ids):
                raise ValueError(f"Invalid or duplicate ReportId in {path}")
            report_ids.add(report_id)
    return entries, report_ids, legacy


def build_sets(output, now, replacements):
    now = now.astimezone(timezone.utc)
    sets = {days: [] for days in SET_DAYS}
    seen = {}
    for offset in range(31):
        day = now.date() - timedelta(days=offset)
        path = output / f"{day:%Y%m%d}.json"
        if day in replacements:
            entries = replacements[day]
        elif path.exists():
            entries, _, _ = load_existing_entries(path)
        else:
            print(f"WARNING: No archive file for {day}; sets may be incomplete.",
                  file=sys.stderr, flush=True)
            continue
        excluded = 0
        for entry in entries:
            if "ScannedAt" not in entry:
                excluded += 1
                continue
            stamp = entry["ScannedAt"]
            if not isinstance(stamp, str):
                raise ValueError(f"Invalid ScannedAt in {path}")
            timestamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp.astimezone(timezone.utc).date() != day:
                raise ValueError(f"Invalid ScannedAt timezone or archive date in {path}")
            timestamp = timestamp.astimezone(timezone.utc)
            report_id = entry.get("ReportId")
            if not report_id:
                raise ValueError(f"ScannedAt without ReportId in {path}")
            if report_id in seen:
                if seen[report_id] != entry:
                    raise ValueError(f"Conflicting archived ReportId: {report_id}")
                continue
            seen[report_id] = entry
            for days in SET_DAYS:
                if now - timedelta(days=days) <= timestamp <= now:
                    sets[days].append(entry)
        if excluded:
            print(
                f"WARNING: Excluding {excluded} entries from {path} without ScannedAt.",
                file=sys.stderr, flush=True,
            )
    for entries in sets.values():
        entries.sort(
            key=lambda entry: (
                datetime.fromisoformat(entry["ScannedAt"].replace("Z", "+00:00")),
                entry["ReportId"],
            ),
            reverse=True,
        )
    return sets


def write_sets(output, sets):
    directory = output / "sets"
    directory.mkdir(parents=True, exist_ok=True)
    for days, entries in sets.items():
        path = directory / f"{days}D.json"
        content = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") == content:
            print(f"Skipping {path}: unchanged", flush=True)
            continue
        path.write_text(content, encoding="utf-8")
        print(f"Wrote {path}: {len(entries)} entries (last {days * 24} hours)", flush=True)


def normalize_payload(payload):
    text = payload.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = text.replace("[.]", ".")
    text = PAYLOAD_URL.sub("<URL>", text)
    return DOWNLOAD_URL.sub(lambda match: match.group(1) + "<URL>", text)


def build_patterns(entries):
    if not isinstance(entries, list):
        raise ValueError("Expected a report array for patterns")
    groups = {}
    seen = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Expected report objects for patterns")
        report_id = entry.get("ReportId")
        stamp = entry.get("ScannedAt")
        payload = entry.get("Payload")
        if (not isinstance(report_id, str)
                or not REPORT_PATH.fullmatch("/report/" + report_id)
                or not isinstance(stamp, str)):
            raise ValueError("Pattern reports require valid ReportId and ScannedAt")
        timestamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError(f"Pattern report {report_id} has no timestamp timezone")
        if payload is not None and not isinstance(payload, str):
            raise ValueError(f"Invalid Payload in pattern report {report_id}")
        if report_id in seen:
            if seen[report_id] != entry:
                raise ValueError(f"Conflicting pattern ReportId: {report_id}")
            continue
        seen[report_id] = entry
        if payload is None or not payload.strip():
            continue
        pattern = normalize_payload(payload)
        group = groups.setdefault(pattern, {"payloads": set(), "reports": {}})
        group["payloads"].add(payload)
        group["reports"][report_id] = timestamp.astimezone(timezone.utc)
    patterns = [
        {
            "Pattern": pattern,
            "DistinctPayloadCount": len(group["payloads"]),
            "ReportCount": len(group["reports"]),
            "FirstSeen": min(group["reports"].values()).isoformat(),
            "LastSeen": max(group["reports"].values()).isoformat(),
            "ReportIds": sorted(group["reports"]),
            "PayloadExamples": sorted(group["payloads"]),
        }
        for pattern, group in groups.items()
    ]
    return sorted(
        patterns,
        key=lambda item: (-item["DistinctPayloadCount"], -item["ReportCount"], item["Pattern"]),
    )


def write_patterns(output, patterns):
    contents = {
        "Patterns.json": json.dumps(patterns, indent=2, ensure_ascii=False) + "\n",
        "Patterns.txt": "".join(
            json.dumps(item["Pattern"], ensure_ascii=False)[1:-1] + "\n"
            for item in patterns
        ),
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        path = output / name
        if path.exists() and path.read_text(encoding="utf-8") == content:
            print(f"Skipping {path}: unchanged", flush=True)
            continue
        path.write_text(content, encoding="utf-8")
        print(f"Wrote {path}: {len(patterns)} patterns", flush=True)


def export(output, now, delay):
    now = now.astimezone(timezone.utc)
    reports = parse_sitemap(fetch(SITEMAP_URL))
    today = now.date()
    start = (now - timedelta(days=30)).date()
    oldest = reports[-1][1].date()
    if oldest >= start:
        print(
            f"WARNING: Sitemap coverage begins on {oldest}; that day may be "
            f"incomplete, and days before it are not available (requested from {start}).",
            file=sys.stderr,
        )

    grouped = {}
    for offset in range(31):
        day = today - timedelta(days=offset)
        if day < oldest:
            continue
        destination = output / f"{day:%Y%m%d}.json"
        exists = destination.exists()
        entries, ids, legacy = load_existing_entries(destination) if exists else ([], set(), [])
        grouped[day] = {
            "entries": entries, "ids": ids, "legacy": legacy,
            "exists": exists, "added": 0, "enriched": 0,
        }
    for path, timestamp in reports:
        day = timestamp.date()
        if day not in grouped or timestamp > now:
            continue
        group = grouped[day]
        report_id = path.rsplit("/", 1)[-1]
        for entry in group["entries"]:
            if entry.get("ReportId") == report_id and "ScannedAt" not in entry:
                entry["ScannedAt"] = timestamp.isoformat()
                group["enriched"] += 1
    pending = [
        (path, timestamp) for path, timestamp in reports
        if start <= timestamp.date() <= today and timestamp <= now
        and path.rsplit("/", 1)[-1] not in grouped[timestamp.date()]["ids"]
    ]
    print(
        f"Checking {len(pending)} reports; "
        f"{sum(len(group['legacy']) for group in grouped.values())} existing entries lack ReportId.",
        flush=True,
    )
    for index, (path, timestamp) in enumerate(pending, start=1):
        day = timestamp.date()
        group = grouped[day]
        report_id = path.rsplit("/", 1)[-1]
        time.sleep(delay)
        print(f"[{index}/{len(pending)}] Fetching {BASE_URL + path}", flush=True)
        entry = fetch_report(BASE_URL + path)
        match = next(
            (index for index, old in enumerate(group["legacy"])
             if all(key in entry and entry[key] == value for key, value in old.items())),
            None,
        )
        if match is not None:
            old = group["legacy"].pop(match)
            old["ReportId"] = report_id
            old["ScannedAt"] = timestamp.isoformat()
            group["ids"].add(report_id)
            group["enriched"] += 1
            continue
        entry["ReportId"] = report_id
        entry["ScannedAt"] = timestamp.isoformat()
        group["ids"].add(report_id)
        group["added"] += 1
        group["entries"].append(entry)

    sets = build_sets(output, now, {day: group["entries"] for day, group in grouped.items()})
    patterns = build_patterns(sets[7])
    output.mkdir(parents=True, exist_ok=True)
    for day in sorted(grouped):
        destination = output / f"{day:%Y%m%d}.json"
        group = grouped[day]
        if group["exists"] and not group["added"] and not group["enriched"]:
            print(f"Skipping {day}: no new entries")
            continue
        destination.write_text(
            json.dumps(group["entries"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(
            f"Wrote {destination}: {len(group['entries'])} entries "
            f"({group['added']} new, {group['enriched']} enriched)"
        )
    write_sets(output, sets)
    write_patterns(output, patterns)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between report requests")
    parser.add_argument(
        "--patterns-only", action="store_true",
        help="Rebuild patterns from the existing sets/7D.json without network requests",
    )
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay must not be negative")
    if args.patterns_only:
        source = args.output / "sets" / "7D.json"
        with source.open(encoding="utf-8") as file:
            patterns = build_patterns(json.load(file))
        write_patterns(args.output, patterns)
    else:
        export(args.output, datetime.now(timezone.utc), args.delay)


if __name__ == "__main__":
    main()
