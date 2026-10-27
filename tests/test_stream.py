"""The recorded model stream: SSE parsing and the live tail."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "volume_tui"))

import stream as tui_stream


def _sse(*events) -> str:
    """A recorded stream: one JSON event per SSE block."""
    blocks = []
    for event in events:
        blocks.append("event: %s\ndata: %s\n\n" % (event.get("type", "?"),
                                                  json.dumps(event)))
    return "".join(blocks)


class ParseSse(unittest.TestCase):
    def test_the_text_deltas_are_the_answer(self):
        events = tui_stream.parse_sse(_sse(
            {"type": "response.output_text.delta", "delta": "Hello"},
            {"type": "response.output_text.delta", "delta": " world"}))
        self.assertEqual([e["delta"] for e in events], ["Hello", " world"])
        self.assertTrue(all(e["kind"] == "text_delta" for e in events))

    def test_the_reasoning_deltas_are_the_thinking(self):
        events = tui_stream.parse_sse(_sse(
            {"type": "response.reasoning_summary_text.delta", "delta": "hmm"},
            {"type": "response.reasoning_text.delta", "delta": " indeed"}))
        self.assertEqual([e["kind"] for e in events],
                         ["reasoning_delta", "reasoning_delta"])

    def test_a_finished_tool_call_names_the_tool_and_its_arguments(self):
        events = tui_stream.parse_sse(_sse(
            {"type": "response.output_item.done",
             "item": {"type": "function_call", "name": "render_scene",
                      "arguments": "{\"parameters\": {\"rotationXYZ\": [1, 2, 3]}}"}}))
        call = events[0]
        self.assertEqual(call["kind"], "tool_call")
        self.assertEqual(call["name"], "render_scene")
        self.assertEqual(call["arguments"]["parameters"]["rotationXYZ"], [1, 2, 3])
        self.assertEqual(call["source"], "stream")

    def test_broken_arguments_do_not_raise(self):
        events = tui_stream.parse_sse(_sse(
            {"type": "response.output_item.done",
             "item": {"type": "function_call", "name": "render_scene",
                      "arguments": "{not json"}}))
        self.assertEqual(events[0]["kind"], "tool_call")

    def test_the_finished_answer_and_the_end_are_seen(self):
        events = tui_stream.parse_sse(_sse(
            {"type": "response.output_text.done", "text": "the answer"},
            {"type": "response.completed"}))
        self.assertEqual(events[0]["kind"], "message_done")
        self.assertEqual(events[0]["text"], "the answer")
        self.assertEqual(events[1]["kind"], "done")

    def test_a_half_written_event_is_ignored(self):
        events = tui_stream.parse_sse(
            'event: x\ndata: {"type": "response.output_text.delta", "delta": "ok"}\n\n'
            'event: x\ndata: {"type": "response.output_te')
        self.assertEqual([e["kind"] for e in events], ["text_delta"])

    def test_the_parser_keeps_the_turn_state_between_chunks(self):
        """The words after a tool call are the answer, not the thinking."""
        parser = tui_stream.SseParser()
        first = parser.feed(_sse(
            {"type": "response.output_item.done",
             "item": {"type": "function_call", "name": "render_scene",
                      "arguments": "{}"}}))
        self.assertEqual(first[0]["kind"], "tool_call")
        second = parser.feed(_sse(
            {"type": "response.output_text.delta", "delta": "done"}))
        self.assertEqual(second[0]["kind"], "text_delta")


class StreamTail(unittest.TestCase):
    def test_the_tail_reads_only_what_is_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            http = run_dir / "http"
            http.mkdir()
            path = http / "http-001.response.bin"
            path.write_text(_sse({"type": "response.output_text.delta",
                                   "delta": "one"}), encoding="utf-8")
            tail = tui_stream.StreamTail(run_dir)
            events = tail.poll()
            self.assertEqual([e["delta"] for e in events], ["one"])
            # Nothing new: nothing to show.
            self.assertEqual(tail.poll(), [])
            # The model keeps writing: the new chunk arrives.
            with path.open("a", encoding="utf-8") as handle:
                handle.write(_sse({"type": "response.output_text.delta",
                                   "delta": "two"}))
            events = tail.poll()
            self.assertEqual([e["delta"] for e in events], ["two"])

    def test_a_missing_http_folder_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tail = tui_stream.StreamTail(Path(tmp))
            self.assertEqual(tail.poll(), [])

    def test_the_state_survives_between_the_polls(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            http = run_dir / "http"
            http.mkdir()
            path = http / "http-001.response.bin"
            path.write_text(_sse({"type": "response.output_text.delta",
                                   "delta": "before"}), encoding="utf-8")
            tail = tui_stream.StreamTail(run_dir)
            tail.poll()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(_sse(
                    {"type": "response.output_item.done",
                     "item": {"type": "function_call", "name": "render_scene",
                              "arguments": "{}"}},
                    {"type": "response.output_text.delta", "delta": "after"}))
            kinds = [e["kind"] for e in tail.poll()]
            self.assertIn("tool_call", kinds)
            self.assertIn("text_delta", kinds)


class StreamCallTags(unittest.TestCase):
    """Every streamed event knows which model call it came from."""

    def test_the_tail_tags_each_event_with_its_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            http = run_dir / "http"
            http.mkdir()
            (http / "http-001.response.bin").write_text(
                _sse({"type": "response.output_text.delta", "delta": "one"}),
                encoding="utf-8")
            (http / "http-002.response.bin").write_text(
                _sse({"type": "response.output_text.delta", "delta": "two"}),
                encoding="utf-8")
            tail = tui_stream.StreamTail(run_dir)
            events = tail.poll()
            self.assertEqual([(e["call"], e["delta"]) for e in events],
                             [(1, "one"), (2, "two")])
            self.assertTrue(all("ts" in e for e in events))
            self.assertTrue(all("compaction" not in e for e in events))

    def test_streamed_chunks_carry_run_clock_times_inside_their_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            http = run_dir / "http"
            http.mkdir()
            (http / "http-001.response.bin").write_text(
                _sse({"type": "response.output_text.delta", "delta": "one"}),
                encoding="utf-8")
            (http / "http-001.metadata.json").write_text(json.dumps(
                {"index": 1, "started": 1010.0, "ended": 1084.0, "status": 200}))
            tail = tui_stream.StreamTail(run_dir)
            events = tail.poll()
            self.assertEqual(len(events), 1)
            self.assertGreaterEqual(events[0]["time"], 1010.0)
            self.assertLessEqual(events[0]["time"], 1084.0)
            self.assertEqual(events[0]["call"], 1)

    def test_a_compaction_call_is_flagged_from_the_request_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            http = run_dir / "http"
            http.mkdir()
            (http / "http-009.forwarded.bin").write_text(json.dumps({
                "model": "qwen3.6:35b",
                "input": [{"type": "message", "role": "user", "content": [
                    {"type": "input_text",
                     "text": "You are performing a CONTEXT CHECKPOINT COMPACTION."}]}]}),
                encoding="utf-8")
            (http / "http-009.response.bin").write_text(
                _sse({"type": "response.output_text.delta", "delta": "# Context"}),
                encoding="utf-8")
            tail = tui_stream.StreamTail(run_dir)
            events = tail.poll()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["call"], 9)
            self.assertTrue(events[0]["compaction"])


if __name__ == "__main__":
    unittest.main()
