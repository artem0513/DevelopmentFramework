"""Pinned TZif behavior independent of execution-host timezone databases."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ase.timezones import resolve_timezone, TimezoneDataUnavailable


class TimezoneTests(unittest.TestCase):
    def test_bucharest_midnight_and_dst(self):
        zone = resolve_timezone("Europe/Bucharest")
        cases = {"2026-11-11T22:30:00+00:00": "2026-11-12T00:30:00+02:00",
                 "2026-03-29T00:30:00+00:00": "2026-03-29T02:30:00+02:00",
                 "2026-03-29T01:30:00+00:00": "2026-03-29T04:30:00+03:00",
                 "2026-10-25T00:30:00+00:00": "2026-10-25T03:30:00+03:00",
                 "2026-10-25T01:30:00+00:00": "2026-10-25T03:30:00+02:00"}
        for value, expected in cases.items():
            self.assertEqual(datetime.fromisoformat(value).astimezone(zone).isoformat(), expected)

    def test_asset_matches_provenance_and_does_not_need_installed_tzdata(self):
        import ase.timezones as module
        root = Path(module.__file__).parent / "data" / "timezones"
        provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256((root / "Europe" / "Bucharest").read_bytes()).hexdigest(), provenance["zones"]["Europe/Bucharest"]["sha256"])
        with patch("ase.timezones.ZoneInfo", side_effect=AssertionError("system lookup disallowed")):
            self.assertEqual(resolve_timezone("Europe/Bucharest").key, "Europe/Bucharest")
        self.assertTrue(provenance["iana_version"])
        self.assertTrue((root / "LICENSE").is_file())

    def test_missing_invalid_and_tampered_rules_are_typed_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing"
            with self.assertRaisesRegex(TimezoneDataUnavailable, "TZ_DATA_UNAVAILABLE"):
                resolve_timezone("Europe/Bucharest", tzif_path=path)
            path.write_bytes(b"TZif-not-real")
            with self.assertRaises(TimezoneDataUnavailable):
                resolve_timezone("Europe/Bucharest", tzif_path=path)
        with patch("ase.timezones.ZoneInfo", side_effect=KeyError("missing")):
            with self.assertRaises(TimezoneDataUnavailable):
                resolve_timezone("Unknown/Zone")
        self.assertEqual(datetime(2026, 1, 1, tzinfo=resolve_timezone("UTC")).utcoffset().total_seconds(), 0)
