"""Fresh demo labels exclude synthetic history without inventing impact."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from demo_labels import make_labels, save_and_import


class LabelsTests(unittest.TestCase):
    def fixture(self, folder, **extra):
        result = {"story": "cpu", "end": "2026-11-25T13:10:00+00:00", **extra}
        (folder / "result.json").write_text(json.dumps(result))
        (folder / "scope-starts.json").write_text(
            json.dumps(
                {
                    "victim": {"started_ms_estimate": 1795611600000},
                    "culprit": {"started_ms_estimate": 1795611690000},
                }
            )
        )
        (folder / "victim.jsonl").write_text("")

    def test_success_still_labels_original_impact_and_excludes_training(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            self.fixture(folder, victim_before_30s=0.30, victim_after_30s=0.02)
            labels = make_labels(folder)
            self.assertEqual(
                [label["phase"] for label in labels], ["victim-lead", "cpu"]
            )
            self.assertTrue(labels[-1]["harm"])
            self.assertTrue(all(label["normal"] is False for label in labels))

    def test_missing_measurement_is_unknown_not_harmless(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            self.fixture(folder, error="missing PSI")
            self.assertIsNone(make_labels(folder)[-1]["harm"])

    def test_abort_during_lead_keeps_synthetic_victim_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            self.fixture(folder)
            (folder / "scope-starts.json").write_text(
                json.dumps({"victim": {"started_ms_estimate": 1795611600000}})
            )
            labels = make_labels(folder)
            self.assertEqual(labels[0]["phase"], "victim-lead-aborted")
            self.assertFalse(labels[0]["normal"])
            self.assertIsNone(labels[0]["harm"])

    def test_no_import_without_explicit_option(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            self.fixture(folder)
            with patch("demo_labels.subprocess.run") as run:
                save_and_import(folder, "laptop")
            run.assert_not_called()
            self.assertTrue((folder / "labels.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
