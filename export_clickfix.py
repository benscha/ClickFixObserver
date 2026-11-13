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


def export(output, now, delay):
    reports = parse_sitemap(fetch(SITEMAP_URL))
    today = now.astimezone(timezone.utc).date()
    start = today - timedelta(days=6)
    oldest = reports[-1][1].date()
    if oldest >= start:
        print(
            f"WARNING: Sitemap coverage begins on {oldest}; that day may be "
            f"incomplete, and days before it are not available (requested from {start}).",
            file=sys.stderr,
        )

    grouped = {}
    for offset in range(7):
        day = today - timedelta(days=offset)
        if day < oldest:
            continue
        destination = output / f"{day:%Y%m%d}.json"
        exists = destination.exists()
        entries, ids, legacy = load_existing_entries(destination) if exists else ([], set(), [])
        grouped[day] = {
            "entries": entries, "ids": ids, "legacy": legacy,
            "exists": exists, "added": 0,
        }
    pending = [
        (path, timestamp) for path, timestamp in reports
        if start <= timestamp.date() <= today
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
            group["legacy"].pop(match)
            continue
        entry["ReportId"] = report_id
        group["ids"].add(report_id)
        group["added"] += 1
        group["entries"].append(entry)

    output.mkdir(parents=True, exist_ok=True)
    for day in sorted(today - timedelta(days=offset) for offset in range(7)):
        destination = output / f"{day:%Y%m%d}.json"
        if day < oldest:
            print(f"Skipping {day}: not covered by sitemap")
            continue
        group = grouped[day]
        if group["exists"] and not group["added"]:
            print(f"Skipping {day}: no new entries")
            continue
        destination.write_text(
            json.dumps(group["entries"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {destination}: {len(group['entries'])} entries ({group['added']} new)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between report requests")
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay must not be negative")
    export(args.output, datetime.now(timezone.utc), args.delay)


if __name__ == "__main__":
    main()
