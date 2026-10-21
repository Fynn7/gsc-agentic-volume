"""The chat record as a log: the conversation of a run in plain text."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "volume_tui"))

import chat_log as tui_chat_log
import transcript as tui_transcript


def _conversation():
    """The blocks of a short run: task, thinking, a call, a frame, the answer."""
    transcript = tui_transcript.Transcript()
    transcript.add(tui_transcript.USER,
                   title="Set the camera rotationXYZ to [25,-35,10] degrees.")
    transcript.add(tui_transcript.THINKING, title="Thinking", status="done",
                   subtitle="in 120, out 40 tokens",
                   body="Rotate the camera as the task asks.",
                   data={"reasoning": True})
    transcript.add(tui_transcript.TOOL, title="Render a picture",
                   subtitle="camera rotation 25,-35,10 · a new PNG of the volume",
                   status="done", body="» to see the top")
    transcript.add(tui_transcript.IMAGE, title="frame revision 1",
                   subtitle="512x512")
    transcript.add(tui_transcript.ASSISTANT,
                   title="The camera is now rotated to [25,-35,10].")
    return transcript.blocks


class ChatLogText(unittest.TestCase):
    def test_the_log_carries_the_task_the_call_and_the_answer(self):
        text = tui_chat_log.chat_log_text(_conversation())
        self.assertIn("Set the camera rotationXYZ to [25,-35,10] degrees.", text)
        self.assertIn("Render a picture", text)
        self.assertIn("camera rotation 25,-35,10", text)
        self.assertIn("The camera is now rotated to [25,-35,10].", text)

    def test_every_row_carries_its_time(self):
        text = tui_chat_log.chat_log_text(_conversation())
        rows = [line for line in text.splitlines() if line.startswith("[")]
        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertRegex(row, r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d] ")
    def test_the_marks_are_the_marks_of_the_screen(self):
        text = tui_chat_log.chat_log_text(_conversation())
        self.assertIn("▌ Set the camera", text)      # the user's task
        self.assertIn("» The camera is now rotated", text)  # the model's words
        self.assertIn("● Render a picture", text)    # a step of the agent
        self.assertIn("▣ frame revision 1", text)    # a picture
        self.assertIn("✓", text)                      # a done turn

    def test_the_reasoning_is_open_in_the_log(self):
        # The screen folds the chain away; the log keeps the words.
        text = tui_chat_log.chat_log_text(_conversation())
        self.assertIn("✓ Thought", text)
        self.assertIn("» Rotate the camera as the task asks.", text)
        # A turn that is still running says so.
        transcript = tui_transcript.Transcript()
        transcript.add(tui_transcript.THINKING, title="Thinking", status="running",
                       data={"reasoning": True})
        running = tui_chat_log.chat_log_text(transcript.blocks)
        self.assertIn("✳ Thinking", running)

    def test_a_turn_without_reasoning_says_working(self):
        transcript = tui_transcript.Transcript()
        transcript.add(tui_transcript.THINKING, title="Working", status="done")
        text = tui_chat_log.chat_log_text(transcript.blocks)
        self.assertIn("✓ Model answered", text)

    def test_the_technical_rows_stay_out_of_the_log(self):
        transcript = tui_transcript.Transcript()
        transcript.add(tui_transcript.META, title="MODEL #1  Running")
        transcript.add(tui_transcript.ASSISTANT, title="done")
        text = tui_chat_log.chat_log_text(transcript.blocks)
        self.assertNotIn("MODEL #1", text)
        # The log says where the transport is.
        self.assertIn("JSON log", text)

    def test_the_log_names_the_run_the_model_and_the_evidence(self):
        text = tui_chat_log.chat_log_text(
            _conversation(), run_id="run-demo", model="qwen3.6:35b",
            run_dir="D:/runs/run-demo")
        self.assertIn("# run: run-demo", text)
        self.assertIn("# model: qwen3.6:35b", text)
        self.assertIn("# evidence: D:/runs/run-demo", text)
        self.assertIn("# exported:", text)

    def test_an_empty_conversation_is_still_a_readable_log(self):
        text = tui_chat_log.chat_log_text([])
        self.assertIn("# Volume Agent chat log", text)
        self.assertTrue(text.endswith("\n"))

    def test_a_row_without_a_time_does_not_break_the_log(self):
        text = tui_chat_log.chat_log_text(
            [{"kind": tui_transcript.ASSISTANT, "title": "hello"}])
        self.assertIn("» hello", text)

    def test_a_multi_line_answer_stays_one_row(self):
        transcript = tui_transcript.Transcript()
        transcript.add(tui_transcript.ASSISTANT,
                       title="Done. Here is the summary:\n\n- one\n- two")
        text = tui_chat_log.chat_log_text(transcript.blocks)
        rows = [line for line in text.splitlines() if line.startswith("[")]
        self.assertEqual(len(rows), 1)
        # The lines under the row belong to it.
        self.assertIn("  - one", text)
        self.assertIn("  - two", text)

    def test_the_row_carries_the_time_the_step_happened(self):
        import time
        transcript = tui_transcript.Transcript()
        transcript.ingest({"kind": "tool_call", "name": "render_scene",
                           "ts": 0, "time": 1700000000.0})
        text = tui_chat_log.chat_log_text(transcript.blocks)
        self.assertIn(time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(1700000000.0)), text)


class ChatLogExport(unittest.TestCase):
    def test_the_export_writes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tui_chat_log.export_chat_log(
                _conversation(), tmp, run_id="run-demo", model="qwen3.6:35b")
            self.assertTrue(path.exists())
            self.assertEqual(path.suffix, ".log")
            self.assertIn("run-demo", path.name)
            written = path.read_text(encoding="utf-8")
            self.assertIn("Set the camera rotationXYZ", written)

    def test_the_export_writes_only_into_the_log_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "chat-logs"
            run_dir = Path(tmp) / "run-demo"
            run_dir.mkdir()
            (run_dir / "run.json").write_text("{}", encoding="utf-8")
            path = tui_chat_log.export_chat_log(_conversation(), log_dir,
                                                run_id="run-demo")
            self.assertEqual(path.parent, log_dir)
            # The run folder keeps its evidence: nothing is written into it.
            self.assertEqual([p.name for p in run_dir.iterdir()], ["run.json"])

    def test_the_default_log_folder_is_the_tui_folder(self):
        self.assertEqual(tui_chat_log.LOG_DIR,
                         ROOT / "runtime" / "tui" / "chat-logs")

    def test_two_exports_keep_two_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = tui_chat_log.export_chat_log(_conversation(), tmp,
                                                 run_id="run-demo")
            second = tui_chat_log.export_chat_log(_conversation(), tmp,
                                                  run_id="run-demo")
            self.assertNotEqual(first, second)
            self.assertEqual(len(list(Path(tmp).glob("*.log"))), 2)

    def test_the_file_name_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tui_chat_log.export_chat_log(_conversation(), tmp,
                                                 run_id="run/with:bad*chars")
            self.assertEqual(path.parent, Path(tmp))
            self.assertTrue(path.name.endswith(".log"))
            for char in '/\\:*?"<>|':
                self.assertNotIn(char, path.name)


if __name__ == "__main__":
    unittest.main()
