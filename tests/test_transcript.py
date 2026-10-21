"""The coding-agent transcript: run evidence -> the blocks the screen shows."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "volume_tui"))

import transcript as tui_transcript


class ToolSummary(unittest.TestCase):
    def test_the_summary_names_the_numbers_the_call_changed(self):
        summary = tui_transcript.tool_summary(
            "render_scene", {"parameters": {"rotationXYZ": [25, -35, 10]}})
        self.assertIn("camera rotation 25,-35,10", summary)
        # What comes out, and what changes.
        self.assertIn("a new PNG of the volume", summary)
        self.assertIn("the scene changes", summary)

    def test_the_summary_of_a_slice_names_the_axis_and_the_index(self):
        summary = tui_transcript.tool_summary("inspect_slice", {"axis": "z", "index": 3})
        self.assertIn("axis z", summary)
        self.assertIn("index 3", summary)
        self.assertIn("nothing changes", summary)

    def test_the_summary_of_the_finish_call_names_the_revision(self):
        summary = tui_transcript.tool_summary("finish_task", {"frameRevision": 3})
        self.assertIn("3", summary)

    def test_an_unknown_call_is_summarized_compactly(self):
        summary = tui_transcript.tool_summary("do_something", {"a": 1, "b": "x"})
        self.assertIn("a", summary)
        self.assertLessEqual(len(summary), 130)

    def test_the_detail_carries_the_reason_of_the_agent(self):
        detail = tui_transcript.tool_detail(
            "render_scene",
            {"parameters": {"rotationXYZ": [1, 2, 3]}, "reason": "to see the top"})
        # The model's own words carry the marker, the facts do not.
        self.assertIn("» to see the top", detail)
        self.assertIn("the camera turns 1° around X", detail)
        self.assertNotIn("why:", detail)
        self.assertNotIn("what that does", detail)
        self.assertNotIn("changed:", detail)

    def test_a_refused_call_says_what_was_wrong(self):
        detail = tui_transcript.tool_error_detail(
            "Stale scene revision", ["camera rotation 1,2,3"])
        self.assertIn("refused change", detail)
        self.assertIn("the scene had already changed", detail)
        self.assertIn("Stale scene revision", detail)


class TranscriptBlocks(unittest.TestCase):
    def setUp(self):
        self.transcript = tui_transcript.Transcript()

    def test_the_task_is_the_first_block(self):
        self.transcript.add(tui_transcript.USER, title="Rotate the camera")
        self.assertEqual(self.transcript.blocks[0]["kind"], tui_transcript.USER)

    def test_the_thinking_block_opens_and_closes(self):
        self.transcript.ingest({"kind": "thinking_start"})
        block = self.transcript.find(tui_transcript.THINKING)
        self.assertEqual(block["status"], "running")
        self.transcript.ingest({"kind": "thinking_end",
                                "usage": {"input_tokens": 10, "output_tokens": 2}})
        self.assertEqual(block["status"], "done")
        self.assertIn("10", block["subtitle"])

    def test_the_reason_of_the_agent_is_a_line_of_the_models_own(self):
        self.transcript.ingest({"kind": "thinking_start"})
        self.transcript.ingest({"kind": "thinking", "reason": "rotate it",
                                "tool": "render_scene"})
        # The reason is not reasoning: it does not fill the foldable block.
        thinking = self.transcript.find(tui_transcript.THINKING)
        self.assertNotIn("rotate it", thinking["body"])
        self.assertEqual(thinking["data"]["next"], "render_scene")
        answers = [block for block in self.transcript.blocks
                   if block["kind"] == tui_transcript.ASSISTANT]
        self.assertEqual(len(answers), 1)
        self.assertIn("rotate it", answers[0]["title"])

    def test_a_tool_call_becomes_a_block_with_its_numbers(self):
        self.transcript.ingest({
            "kind": "tool_call", "name": "render_scene", "source": "service",
            "arguments": {"parameters": {"rotationXYZ": [25, -35, 10]}}})
        block = self.transcript.find(tui_transcript.TOOL)
        self.assertEqual(block["status"], "running")
        self.assertIn("camera rotation 25,-35,10", block["subtitle"])

    def test_the_tool_call_is_closed_by_its_result(self):
        self.transcript.ingest({
            "kind": "tool_call", "name": "render_scene", "source": "service",
            "arguments": {}})
        self.transcript.ingest({"kind": "tool_result", "name": "render_scene",
                                "source": "service", "seconds": 1.5})
        block = self.transcript.find(tui_transcript.TOOL)
        self.assertEqual(block["status"], "done")
        self.assertEqual(block["data"]["seconds"], 1.5)

    def test_the_two_sources_report_one_call_only_once(self):
        self.transcript.ingest({"kind": "tool_call", "name": "render_scene",
                                "source": "harness", "arguments": {}})
        self.transcript.ingest({"kind": "tool_result", "name": "render_scene",
                                "source": "harness"})
        self.transcript.ingest({"kind": "tool_call", "name": "render_scene",
                                "source": "service", "arguments": {}})
        self.transcript.ingest({"kind": "tool_result", "name": "render_scene",
                                "source": "service", "seconds": 0.5})
        tools = [block for block in self.transcript.blocks
                 if block["kind"] == tui_transcript.TOOL]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["status"], "done")

    def test_the_service_log_wins_over_the_harness(self):
        self.transcript.ingest({"kind": "tool_call", "name": "render_scene",
                                "source": "service", "arguments": {}})
        self.transcript.ingest({"kind": "tool_call", "name": "render_scene",
                                "source": "harness", "arguments": {}})
        tools = [block for block in self.transcript.blocks
                 if block["kind"] == tui_transcript.TOOL]
        self.assertEqual(len(tools), 1)

    def test_two_calls_of_the_same_tool_are_two_blocks(self):
        for _ in range(2):
            self.transcript.ingest({"kind": "tool_call", "name": "render_scene",
                                    "source": "service", "arguments": {}})
            self.transcript.ingest({"kind": "tool_result", "name": "render_scene",
                                    "source": "service", "seconds": 0.2})
        tools = [block for block in self.transcript.blocks
                 if block["kind"] == tui_transcript.TOOL]
        self.assertEqual(len(tools), 2)

    def test_a_refused_call_is_marked_as_failed(self):
        self.transcript.ingest({"kind": "tool_call", "name": "render_scene",
                                "source": "service", "arguments": {}})
        self.transcript.ingest({"kind": "tool_error", "name": "render_scene",
                                "source": "service", "message": "Stale scene revision"})
        block = self.transcript.find(tui_transcript.TOOL)
        self.assertEqual(block["status"], "failed")
        self.assertIn("Stale scene revision", block["body"])

    def test_the_answer_is_its_own_block(self):
        self.transcript.ingest({"kind": "assistant", "text": "The camera is rotated."})
        block = self.transcript.find(tui_transcript.ASSISTANT)
        self.assertEqual(block["title"], "The camera is rotated.")

    def test_a_streamed_answer_grows_in_one_block(self):
        self.transcript.ingest({"kind": "assistant_update", "text": "The camera "})
        self.transcript.ingest({"kind": "assistant_update", "text": "The camera is "})
        self.transcript.ingest({"kind": "assistant", "text": "The camera is rotated."})
        answers = [block for block in self.transcript.blocks
                   if block["kind"] == tui_transcript.ASSISTANT]
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]["title"], "The camera is rotated.")

    def test_the_streamed_answer_grows_with_every_chunk(self):
        self.transcript.ingest({"kind": "text_delta", "delta": "The "})
        self.transcript.ingest({"kind": "text_delta", "delta": "camera "})
        self.transcript.ingest({"kind": "text_delta", "delta": "is rotated."})
        answers = [block for block in self.transcript.blocks
                   if block["kind"] == tui_transcript.ASSISTANT]
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]["title"], "The camera is rotated.")

    def test_the_streamed_reasoning_grows_in_the_thinking_block(self):
        self.transcript.ingest({"kind": "thinking_start"})
        self.transcript.ingest({"kind": "reasoning_delta", "delta": "let me "})
        self.transcript.ingest({"kind": "reasoning_delta", "delta": "look"})
        thinking = self.transcript.find(tui_transcript.THINKING)
        self.assertEqual(thinking["body"], "let me look")
        self.assertTrue(thinking["data"]["reasoning"])

    def test_the_words_before_a_tool_call_stay_a_line_without_reasoning(self):
        """Without reasoning there is no foldable block to move them into."""
        self.transcript.ingest({"kind": "text_delta", "delta": "Let me inspect the scene."})
        self.transcript.ingest({"kind": "tool_call", "name": "inspect_scene",
                                "source": "stream", "arguments": {}})
        answers = [block for block in self.transcript.blocks
                   if block["kind"] == tui_transcript.ASSISTANT]
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]["title"], "Let me inspect the scene.")
        # No foldable block was needed at all.
        self.assertIsNone(self.transcript.find(tui_transcript.THINKING))

    def test_the_words_before_a_tool_call_join_the_reasoning(self):
        """With reasoning they belong in the foldable block."""
        self.transcript.ingest({"kind": "thinking_start"})
        self.transcript.ingest({"kind": "reasoning_delta", "delta": "I look around."})
        self.transcript.ingest({"kind": "text_delta", "delta": "Let me inspect the scene."})
        self.transcript.ingest({"kind": "tool_call", "name": "inspect_scene",
                                "source": "stream", "arguments": {}})
        answers = [block for block in self.transcript.blocks
                   if block["kind"] == tui_transcript.ASSISTANT]
        self.assertEqual(answers, [])
        thinking = self.transcript.find(tui_transcript.THINKING)
        self.assertIn("Let me inspect the scene.", thinking["body"])

    def test_the_answer_after_the_tool_call_is_its_own_block(self):
        self.transcript.ingest({"kind": "text_delta", "delta": "Let me inspect."})
        self.transcript.ingest({"kind": "tool_call", "name": "inspect_scene",
                                "source": "stream", "arguments": {}})
        self.transcript.ingest({"kind": "text_delta", "delta": "It is a frog."})
        answers = [block for block in self.transcript.blocks
                   if block["kind"] == tui_transcript.ASSISTANT]
        # The words before the call stay their own line, the answer is another.
        self.assertEqual(len(answers), 2)
        self.assertEqual(answers[0]["title"], "Let me inspect.")
        self.assertEqual(answers[1]["title"], "It is a frog.")

    def test_the_harness_answer_and_the_streamed_answer_are_one_block(self):
        """Both report the same answer: it is written once."""
        self.transcript.ingest({"kind": "assistant", "text": "It is a frog."})
        self.transcript.ingest({"kind": "text_delta", "delta": "It is a frog."})
        answers = [block for block in self.transcript.blocks
                   if block["kind"] == tui_transcript.ASSISTANT]
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]["title"], "It is a frog.")

    def test_the_streamed_answer_wins_over_the_harness_text(self):
        """The stream arrives first: the harness closes the same block."""
        self.transcript.ingest({"kind": "text_delta", "delta": "It is a "})
        self.transcript.ingest({"kind": "assistant", "text": "It is a frog."})
        answers = [block for block in self.transcript.blocks
                   if block["kind"] == tui_transcript.ASSISTANT]
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]["title"], "It is a frog.")

    def test_a_frame_carries_its_path_and_its_numbers(self):
        self.transcript.ingest({"kind": "image", "source": "render", "revision": 2,
                                "lit": "148491", "size": "512x512",
                                "image": "D:/frame-2/image.png"})
        block = self.transcript.find(tui_transcript.IMAGE)
        self.assertIn("frame revision 2", block["title"])
        self.assertIn("148491 lit px", block["title"])
        self.assertIn("512x512", block["title"])
        self.assertEqual(block["data"]["image"], "D:/frame-2/image.png")

    def test_the_frame_says_how_much_of_the_picture_is_visible(self):
        self.transcript.ingest({"kind": "image", "source": "render", "revision": 2,
                                "lit": "148491", "size": "512x512",
                                "image": "D:/frame-2/image.png"})
        block = self.transcript.find(tui_transcript.IMAGE)
        self.assertIn("56.6%", block["title"])

    def test_the_tool_row_leads_with_the_step_in_words(self):
        self.transcript.ingest({
            "kind": "tool_call", "name": "render_scene", "source": "service",
            "arguments": {"parameters": {"rotationXYZ": [25, -35, 10]}}})
        block = self.transcript.find(tui_transcript.TOOL)
        self.assertEqual(block["title"], "Render a picture")
        self.assertNotIn("render_scene", block["title"])
        # The tool name stays available: the screen shows it next to the words.
        self.assertEqual(block["data"]["tool"], "render_scene")
        self.assertIn("camera rotation 25,-35,10", block["subtitle"])

    def test_a_read_only_call_says_that_it_changes_nothing(self):
        self.transcript.ingest({
            "kind": "tool_call", "name": "inspect_scene", "source": "service",
            "arguments": {}})
        block = self.transcript.find(tui_transcript.TOOL)
        self.assertEqual(block["title"], "Read the scene")
        # What comes out, and that nothing changes.
        self.assertIn("camera, opacity window, crop box, current picture",
                      block["subtitle"])
        self.assertIn("nothing changes", block["subtitle"])

    def test_the_harness_notices_are_one_row(self):
        self.transcript.ingest({"kind": "warn", "message": "Under-development features enabled"})
        self.transcript.ingest({"kind": "warn", "message": "Model metadata not found"})
        notices = [block for block in self.transcript.blocks
                   if block["kind"] == tui_transcript.NOTICES]
        self.assertEqual(len(notices), 1)
        self.assertIn("2 harness notices", notices[0]["title"])
        self.assertIn("Model metadata not found", notices[0]["body"])

    def test_the_transport_rows_are_marked_as_plumbing(self):
        self.transcript.ingest({"kind": "meta", "text": "model call #1"})
        block = self.transcript.find(tui_transcript.META)
        self.assertTrue(block["data"]["plumbing"])

    def test_the_end_of_the_run_names_the_frame_and_the_claim(self):
        self.transcript.ingest({"kind": "finish", "revision": 3,
                                "verification": "unverified",
                                "claim": "The rotation is applied."})
        block = self.transcript.find(tui_transcript.FINISH)
        self.assertIn("3", block["title"])
        self.assertIn("unverified", block["title"])
        self.assertIn("The rotation is applied.", block["body"])

    def test_an_error_is_its_own_block(self):
        self.transcript.ingest({"kind": "error", "message": "the renderer crashed"})
        block = self.transcript.find(tui_transcript.ERROR)
        self.assertIn("the renderer crashed", block["title"])

    def test_an_unknown_event_changes_nothing(self):
        self.assertEqual(self.transcript.ingest({"kind": "nonsense"}), [])
        self.assertEqual(self.transcript.blocks, [])
    def test_clearing_empties_the_conversation(self):
        self.transcript.ingest({"kind": "assistant", "text": "hi"})
        self.transcript.clear()
        self.assertEqual(self.transcript.blocks, [])


if __name__ == "__main__":
    unittest.main()
