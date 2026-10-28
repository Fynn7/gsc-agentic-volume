"""The GUI bridge folds one run once: painted rows never move or duplicate."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "volume_tui"))

import live_transcript as live_mod


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def append_lines(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


class LiveTranscriptTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temp.name) / "run-1"
        self.run_dir.mkdir(parents=True)
        write_json(self.run_dir / "config.json", {"task": "Set the camera", "model": "qwen3.6:35b"})
        write_json(self.run_dir / "run.json", {"status": "running", "startedAt": time.time() - 5})
        append_lines(self.run_dir / "harness.jsonl", [
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "First answer."}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        ])

    def tearDown(self):
        self.temp.cleanup()

    def test_first_poll_folds_the_evidence_and_later_polls_only_append(self):
        live = live_mod.LiveTranscript(self.run_dir, "Set the camera")
        first = live.poll()
        kinds = [block["kind"] for block in first]
        self.assertEqual(kinds[0], "user")
        self.assertIn("assistant", kinds)
        ids = [block["id"] for block in first]

        # Nothing new: the same rows, in the same order, with the same identity.
        again = live.poll()
        self.assertEqual([block["id"] for block in again], ids)
        self.assertEqual(len(again), len(first))

        # A second turn appends below; earlier blocks keep their ids/positions.
        append_lines(self.run_dir / "harness.jsonl", [
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Second answer."}},
            {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 2}},
        ])
        third = live.poll()
        self.assertGreater(len(third), len(again))
        self.assertEqual([block["id"] for block in third[: len(again)]], ids)
        self.assertEqual(third[-1]["title"], "Second answer.")

    def test_terminal_notice_is_appended_once_and_running_rows_close(self):
        live = live_mod.LiveTranscript(self.run_dir, "Set the camera")
        live.poll()
        live.finalize("stopped", False)
        first = live.blocks
        notices = [block for block in first if block["kind"] == "notice"]
        self.assertEqual(len(notices), 1)
        self.assertTrue(all(block["status"] != "running" for block in first if block["kind"] == "thinking"))

        live.finalize("stopped", False)
        second = live.blocks
        self.assertEqual(len([b for b in second if b["kind"] == "notice"]), 1)
        self.assertEqual(len(second), len(first))


if __name__ == "__main__":
    unittest.main()
