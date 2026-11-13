import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import export_clickfix as exporter


def report(lure="lure[.]example", delivery=(), payload=()):
    def column(label, values):
        nodes = "".join(
            f'<div class="obs-infra-node"><p class="obs-data">{value}</p></div>'
            for value in values
        )
        return (
            '<div class="obs-infra-column">'
            f'<p class="obs-label">{label}</p>{nodes}</div>'
        )

    return (
        '<details id="infrastructure"><div class="obs-infra">'
        + column("Lure", [lure])
        + column("Delivery", delivery)
        + column("Payload", payload)
        + '</div><div class="obs-data">unrelated.example</div></details>'
    )


def sitemap(entries):
    urls = "".join(
        f"<url><loc>clickfix-portal.vercel.app/report/{name}</loc>"
        f"<lastmod>{date}</lastmod></url>"
        for name, date in entries
    )
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'.encode()


class InfrastructureTests(unittest.TestCase):
    def test_extracts_only_columns_and_refangs_values(self):
        html = report(
            delivery=["delivery[.]example", "192[.]0[.]2[.]4", "2001:db8::1"],
            payload=["https://payload[.]example/a?x=1&amp;y=2"],
        )
        self.assertEqual(
            exporter.parse_infrastructure(html),
            {
                "UrlDomain": "lure.example",
                "DeliveryUrl": ["delivery.example"],
                "PayloadUrl": ["https://payload.example/a?x=1&y=2"],
            },
        )

    def test_empty_columns_are_arrays(self):
        self.assertEqual(exporter.parse_infrastructure(report())["PayloadUrl"], [])
        self.assertEqual(exporter.parse_infrastructure(report())["DeliveryUrl"], [])

    def test_missing_or_changed_structure_fails(self):
        for html in ("<html></html>", report().replace("Payload", "Other"), report()[:-10]):
            with self.subTest(html=html), self.assertRaises(ValueError):
                exporter.parse_infrastructure(html)


class SitemapTests(unittest.TestCase):
    def test_parse_sitemap_uses_utc(self):
        entries = [("one", "2026-11-13T01:00:00+02:00")]
        self.assertEqual(
            exporter.parse_sitemap(sitemap(entries))[0][1].date().isoformat(),
            "2026-11-12",
        )

    def test_rejects_bad_order_and_duplicates(self):
        for entries in (
            [("a", "2026-11-12T00:00:00Z"), ("b", "2026-11-13T00:00:00Z")],
            [("a", "2026-11-13T00:00:00Z"), ("a", "2026-11-12T00:00:00Z")],
        ):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                exporter.parse_sitemap(sitemap(entries))


class ExportTests(unittest.TestCase):
    def test_seven_days_preserves_partial_day_and_existing_older_files(self):
        entries = [
            ("today", "2026-11-13T10:00:00Z"),
            ("yesterday", "2026-11-12T10:00:00Z"),
            ("partial", "2026-11-10T10:00:00Z"),
        ]
        page = report(delivery=["delivery[.]example"]).encode()

        def fake_fetch(url):
            return sitemap(entries) if url == exporter.SITEMAP_URL else page

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "20261110.json").write_text("existing", encoding="utf-8")
            (output / "20261109.json").write_text("older", encoding="utf-8")
            with patch.object(exporter, "fetch", side_effect=fake_fetch):
                exporter.export(output, datetime(2026, 11, 13, tzinfo=timezone.utc), 0)
            self.assertEqual((output / "20261110.json").read_text(), "existing")
            self.assertEqual((output / "20261109.json").read_text(), "older")
            self.assertFalse((output / "20261108.json").exists())
            self.assertEqual(json.loads((output / "20261111.json").read_text()), [])
            result = json.loads((output / "20261113.json").read_text())
            self.assertEqual(result[0]["DeliveryUrl"], ["delivery.example"])

    def test_partial_day_created_when_missing(self):
        entries = [("partial", "2026-11-10T10:00:00Z")]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(
                exporter, "fetch", side_effect=[sitemap(entries), report().encode()]
            ):
                exporter.export(output, datetime(2026, 11, 13, tzinfo=timezone.utc), 0)
            self.assertEqual(len(json.loads((output / "20261110.json").read_text())), 1)

    def test_failed_report_does_not_change_existing_output(self):
        entries = [("report", "2026-11-13T10:00:00Z"), ("old", "2026-11-12T10:00:00Z")]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            existing = output / "20261113.json"
            existing.write_text("existing", encoding="utf-8")
            with patch.object(
                exporter, "fetch", side_effect=[sitemap(entries), b"<html>missing</html>"]
            ):
                with self.assertRaisesRegex(RuntimeError, "/report/report"):
                    exporter.export(output, datetime(2026, 11, 13, tzinfo=timezone.utc), 0)
            self.assertEqual(existing.read_text(), "existing")


if __name__ == "__main__":
    unittest.main()
