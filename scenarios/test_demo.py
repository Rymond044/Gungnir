"""Offline checks for the manual demonstration's safety and evidence boundaries."""

import argparse
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "demo", Path(__file__).with_name("demo.py")
)
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


class DemoTests(unittest.TestCase):
    def test_expected_story_requires_fresh_engine_annotations_for_host_or_build(self):
        onset, end = 100.0, 130.0
        host = {
            "id": 1,
            "entity": "host",
            "workload_class": "build",
            "started_at": "1970-01-01T00:01:45+00:00",
            "kind": "anomaly",
        }
        for case, records, host_records, explained, passed in (
            ("host", [], [host], ["1"], True),
            ("not explained", [], [host], [], False),
            (
                "old host",
                [],
                [{**host, "started_at": "1970-01-01T00:00:30+00:00"}],
                ["1"],
                False,
            ),
            ("wrong class", [], [{**host, "workload_class": "backup"}], ["1"], False),
            ("impact", [], [{**host, "kind": "impact"}], ["1"], False),
            ("unrelated group", [{**host, "entity": "/other.scope"}], [], ["1"], False),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                runner = demo.Runner(
                    argparse.Namespace(host="laptop", story="expected", check=False)
                )
                runner.out = Path(folder)
                runner.state = {
                    "at": "1970-01-01T00:03:20+00:00",
                    "lag": 0,
                    "records": records,
                    "host_records": host_records,
                    "facts": [{"value": "build"}],
                    "incidents": [],
                    "actions": [],
                }
                runner.samples = [
                    {"time": onset, "cpu": 0, "io": 0, "memory": 0},
                    {"time": end, "cpu": 1000, "io": 0, "memory": 0},
                ]
                build = Mock(returncode=0)
                build.poll.return_value = 0
                with (
                    patch.object(runner, "launch", return_value=(build, Path(folder))),
                    patch.object(runner, "wait"),
                    patch.object(runner, "tick"),
                    patch.object(demo.time, "time", side_effect=[onset, end]),
                    patch.object(
                        demo,
                        "command",
                        return_value=json.dumps([{"expected": explained}]),
                    ),
                ):
                    result = runner.story(Path(folder))
                self.assertEqual(result["passed"], passed)
                self.assertEqual(
                    result["host_expected_record_ids"], [1] if passed else []
                )
                self.assertEqual(result["scope_expected_record_ids"], [])

    def test_missing_pressure_is_not_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                demo.pressure(Path(folder) / "missing")
            path = Path(folder) / "cpu.pressure"
            path.write_text("full avg10=0.00 total=0\n")
            with self.assertRaises(ValueError):
                demo.pressure(path)
            path.write_text("some avg10=1.00 total=123456\n")
            self.assertEqual(demo.pressure(path), 123456)

    def test_share_uses_measured_window_and_rejects_missing_data(self):
        rows = [
            {"time": 0, "cpu": 100},
            {"time": 10, "cpu": 3000100},
            {"time": 20, "cpu": 3100100},
        ]
        self.assertAlmostEqual(demo.stall_share(rows, 0, 10), 0.3)
        self.assertAlmostEqual(demo.stall_share(rows, 10, 20), 0.01)
        self.assertIsNone(demo.stall_share(rows, 4, 9))
        self.assertIsNone(demo.stall_share([{"time": 0, "cpu": None}], 0, 30))

    def test_restoration_requires_existing_file_and_original_value(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(demo, "CGROUP", Path(folder)),
        ):
            group = Path(folder) / "a.scope"
            group.mkdir()
            path = group / "cpu.weight"
            action = {
                "files": [
                    {
                        "group": "/a.scope",
                        "file": "cpu.weight",
                        "before": "100",
                        "after": "25",
                    }
                ]
            }
            path.write_text("25\n")
            self.assertFalse(demo.restored(action))
            path.write_text("100\n")
            self.assertTrue(demo.restored(action))
            self.assertFalse(demo.restored({"files": []}))
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                demo.restored(action)

    def test_sql_connection_is_read_only(self):
        with patch.object(
            demo, "command", return_value='BEGIN\n{"ok":true}\nCOMMIT'
        ) as command:
            self.assertEqual(demo.sql("SELECT '{}'::json"), {"ok": True})
            self.assertIn("BEGIN READ ONLY;", command.call_args.args[0][-1])

    def test_abort_stops_only_owned_scopes_and_reports_cleanup(self):
        runner = demo.Runner(
            argparse.Namespace(host="laptop", story="cpu", check=False)
        )
        with tempfile.TemporaryDirectory() as folder:
            runner.out = Path(folder)
            runner.units = ["owned-victim.scope", "owned-culprit.scope"]
            with patch.object(
                demo.subprocess, "run", return_value=Mock(stdout="inactive\n")
            ) as run:
                runner.cleanup()
            stops = [c.args[0] for c in run.call_args_list if "stop" in c.args[0]]
            self.assertEqual(
                stops,
                [
                    ["systemctl", "--user", "stop", "owned-culprit.scope"],
                    ["systemctl", "--user", "stop", "owned-victim.scope"],
                ],
            )
            self.assertEqual(
                json.loads((runner.out / "cleanup.json").read_text())[
                    "remaining_scopes"
                ],
                [],
            )

    def test_cleanup_timeout_is_explicit_failure(self):
        runner = demo.Runner(
            argparse.Namespace(host="laptop", story="cpu", check=False)
        )
        with tempfile.TemporaryDirectory() as folder:
            runner.out = Path(folder)
            runner.units = ["owned.scope"]
            with (
                patch.object(
                    demo.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired("systemctl", 15),
                ),
                self.assertRaisesRegex(RuntimeError, "still running"),
            ):
                runner.cleanup()
            self.assertEqual(
                json.loads((runner.out / "cleanup.json").read_text())[
                    "remaining_scopes"
                ],
                ["owned.scope"],
            )

    def test_confirmation_timeout_keeps_cleanup_and_failure_evidence(self):
        runner = demo.Runner(
            argparse.Namespace(host="laptop", story="cpu", check=False)
        )
        with tempfile.TemporaryDirectory() as folder:
            runner.out = Path(folder) / "evidence"
            with (
                patch.object(runner, "preflight", return_value={}),
                patch.object(demo.subprocess, "Popen", return_value=Mock()),
                patch.object(
                    runner,
                    "story",
                    side_effect=RuntimeError("no browser-confirmed action"),
                ),
                patch.object(runner, "cleanup") as cleanup,
                self.assertRaisesRegex(RuntimeError, "no browser-confirmed"),
            ):
                runner.run()
            cleanup.assert_called_once()
            result = json.loads((runner.out / "result.json").read_text())
            self.assertFalse(result["passed"])
            self.assertIn("no browser-confirmed", result["error"])

    def test_check_does_not_launch_or_create_evidence(self):
        runner = demo.Runner(argparse.Namespace(host="laptop", story="cpu", check=True))
        with tempfile.TemporaryDirectory() as folder:
            runner.out = Path(folder) / "evidence"
            with (
                patch.object(runner, "preflight", return_value={}),
                patch.object(demo.subprocess, "Popen") as launch,
            ):
                runner.run()
            launch.assert_not_called()
            self.assertFalse(runner.out.exists())

    def test_real_confirmation_deadline_does_not_apply_any_action(self):
        runner = demo.Runner(
            argparse.Namespace(host="laptop", story="cpu", check=False)
        )
        runner.state = {"actions": [], "proposals": []}
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(
                runner, "launch", return_value=(Mock(), Path("/unused"))
            ) as launch,
            patch.object(runner, "wait"),
            patch.object(runner, "tick"),
            patch.object(demo.time, "monotonic", side_effect=[0, 181]),
            self.assertRaisesRegex(RuntimeError, "within 180 seconds"),
        ):
            runner.out = Path(folder)
            runner.story(Path(folder))
        self.assertEqual(launch.call_count, 2)

    def test_verification_deadline_cannot_turn_into_a_success(self):
        runner = demo.Runner(
            argparse.Namespace(host="laptop", story="cpu", check=False)
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runner.out = root
            (root / "cpu.weight").write_text("25")
            action = {
                "id": 1,
                "action": "cpu_weight",
                "state": "applied",
                "outcome": None,
                "target": "/" + runner.unit + "-culprit.scope",
                "applied_at": "2026-11-25T13:00:00+00:00",
                "files": [
                    {"group": "/", "file": "cpu.weight", "before": "100", "after": "25"}
                ],
            }
            runner.state = {"actions": [action], "proposals": []}
            with (
                patch.object(runner, "launch", return_value=(Mock(), root)),
                patch.object(runner, "wait"),
                patch.object(runner, "tick"),
                patch.object(demo, "CGROUP", root),
                patch.object(demo.time, "monotonic", side_effect=[0, 1, 1000, 1421]),
                self.assertRaisesRegex(RuntimeError, "within 420 seconds"),
            ):
                runner.story(root)

    def test_runner_has_no_confirmation_or_keep_command(self):
        # No path may impersonate an operator via CLI/API or request retention.
        source = Path(demo.__file__).read_text()
        self.assertNotIn("'confirm'", source)
        self.assertNotIn("proposals confirm", source)
        self.assertNotIn("/confirm", source)
        self.assertNotIn("'keep'", source)


if __name__ == "__main__":
    unittest.main()
