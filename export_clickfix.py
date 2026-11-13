#!/usr/bin/env python3
"""Export infrastructure from publicly listed clickfix.observer reports."""

import argparse
import ipaddress
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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

    def text(self):
        return "".join(
            child.text() if isinstance(child, Node) else child
            for child in self.children
        ).strip()

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()


class InfrastructureParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = None
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self.stack:
            if tag != "details" or attrs.get("id") != "infrastructure":
                return
            if self.root is not None:
                raise ValueError("Multiple infrastructure sections found")
            self.root = Node(tag, attrs)
            self.stack.append(self.root)
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


def parse_infrastructure(html):
    parser = InfrastructureParser()
    parser.feed(html)
    if parser.root is None or parser.stack:
        raise ValueError("Missing or incomplete infrastructure section")

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
    return {
        "UrlDomain": columns["Lure"][0],
        "DeliveryUrl": [value for value in columns["Delivery"] if not is_ip(value)],
        "PayloadUrl": columns["Payload"],
    }


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


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

    grouped = {today - timedelta(days=offset): [] for offset in range(7)}
    for path, timestamp in reports:
        day = timestamp.date()
        if day < start or day > today:
            continue
        destination = output / f"{day:%Y%m%d}.json"
        if day == oldest and destination.exists():
            continue
        time.sleep(delay)
        try:
            entry = parse_infrastructure(fetch(BASE_URL + path).decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Failed to process {BASE_URL + path}: {exc}") from exc
        grouped[day].append(entry)

    output.mkdir(parents=True, exist_ok=True)
    for day, entries in sorted(grouped.items()):
        destination = output / f"{day:%Y%m%d}.json"
        if day < oldest:
            print(f"Skipping {day}: not covered by sitemap")
            continue
        if day == oldest and destination.exists():
            print(f"Skipping {day}: preserving existing file for partially covered day")
            continue
        destination.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {destination}: {len(entries)} entries")


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
