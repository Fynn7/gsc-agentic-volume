"""Wording rules for the Live event log.

The log must be readable by someone who never saw this pipeline: every row
names what happened and with which numbers, in one short line.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "volume_tui"))

import event_text


class Numbers(unittest.TestCase):
    def test_number_is_compact_and_never_prints_none(self):
        self.assertEqual(event_text.number(148491.0), "148491")
        self.assertEqual(event_text.number(0.1), "0.1")
        self.assertEqual(event_text.count(None), "?")
        self.assertEqual(event_text.count(0), "0")

    def test_brief_cuts_on_one_line_and_marks_the_cut(self):
        self.assertEqual(event_text.brief("a\n  b   c"), "a b c")
        self.assertEqual(event_text.brief("x" * 50, 10), "xxxxxxx...")


class ToolCalls(unittest.TestCase):
    def test_render_call_names_the_change_and_the_reason(self):
        event = event_text.tool_call("render_scene", {
            "expectedRevision": 0,
            "parameters": {"rotationXYZ": [30, 0, 0]},
            "reason": "Tilt camera down slightly"})
        self.assertIn("render_scene", event["title"])
        self.assertIn("camera rotation 30,0,0", event["title"])
        self.assertIn("Tilt camera down", event["title"])
        self.assertIn("scene revision 0", event["detail"])

    def test_read_only_tools_say_that_they_change_nothing(self):
        event = event_text.tool_call("inspect_scene", {})
        self.assertIn("changes nothing", event["title"])
        slice_event = event_text.tool_call("inspect_slice", {"axis": "z", "index": 120})
        self.assertIn("slice z=120", slice_event["title"])

    def test_crop_is_shown_as_bounds(self):
        lines = event_text.parameter_lines({"crop": [0, 246, 0, 246, 0, 221]})
        self.assertEqual(lines, ["crop x 0-246 y 0-246 z 0-221"])

    def test_return_does_not_claim_success(self):
        event = event_text.tool_return("render_scene", 2.94)
        self.assertIn("2.94s", event["title"])
        self.assertNotIn("Success", event["title"])

    def test_error_carries_the_rejection_reason(self):
        event = event_text.tool_error("render_scene", "Invalid bounded numeric vector")
        self.assertIn("FAILED", event["title"])
        self.assertIn("Invalid bounded numeric vector", event["title"])
        self.assertIn("Invalid bounded numeric vector", event["detail"])

    def test_error_names_the_rejected_call_and_explains_it(self):
        event = event_text.tool_error("render_scene", "Invalid bounded numeric vector",
                                      ["camera shift 0,-20,0"])
        self.assertIn("camera shift 0,-20,0", event["detail"])
        self.assertIn("outside the allowed range", event["detail"])


class Frames(unittest.TestCase):
    RESULT = {
        "image": "D:/runs/session/frame-1/image.png",
        "nonblackPixels": 148491,
        "scene": {"revision": 1, "output": [512, 512],
                  "camera": {"rotationXYZ": [30, 0, 0], "translation": [0, 0, 0]},
                  "tf": {"type": "smoothstep", "start": 0.1, "width": 0.12},
                  "crop": [0, 246, 0, 246, 0, 221]},
    }

    def test_frame_title_carries_revision_pixels_and_size(self):
        event = event_text.image_event("render", self.RESULT)
        self.assertIn("new frame 1", event["title"])
        self.assertIn("148491", event["title"])
        self.assertIn("512x512", event["title"])

    def test_initial_frame_is_marked_as_initial(self):
        event = event_text.image_event("initial_render", self.RESULT)
        self.assertIn("initial frame", event["title"])

    def test_detail_carries_scene_numbers_and_the_file(self):
        detail = event_text.image_event("render", self.RESULT)["detail"]
        self.assertIn("camera: rotation 30,0,0", detail)
        self.assertIn("opacity: smoothstep start 0.1 width 0.12", detail)
        self.assertIn("crop: x 0-246", detail)
        self.assertIn("image.png", detail)
        self.assertIn("56.6%", detail)

    def test_slice_is_not_called_a_render(self):
        event = event_text.image_event("slice", {"image": "D:/s.png", "axis": "z", "index": 3})
        self.assertIn("slice z=3", event["title"])
        self.assertIn("not a render", event["title"])


class ModelTurns(unittest.TestCase):
    def test_turn_done_without_a_start_does_not_invent_a_number(self):
        event = event_text.model_turn_done(0, {})
        self.assertIn("turn count unknown", event["title"])
        self.assertNotIn("#0", event["title"])

    def test_turn_done_reports_the_token_counts(self):
        event = event_text.model_turn_done(2, {"input_tokens": 63078, "cached_input_tokens": 53412,
                                              "output_tokens": 733})
        self.assertIn("MODEL #2", event["title"])
        self.assertIn("63078", event["title"])
        self.assertIn("733", event["title"])

    def test_harness_notice_is_a_warning_with_its_text(self):
        event = event_text.harness_warning("Model metadata not found")
        self.assertIn("WARN", event["title"])
        self.assertIn("Model metadata not found", event["title"])


class EndOfRun(unittest.TestCase):
    def test_finish_says_what_was_picked_and_that_it_is_unverified(self):
        event = event_text.finish_event({"selectedRevision": 2,
                                         "externalTaskVerification": "unverified",
                                         "modelClaim": "The present is visible"})
        self.assertIn("revision 2", event["title"])
        self.assertIn("unverified", event["title"])
        self.assertIn("not an independent verification", event["detail"])

    def test_infer_request_keeps_the_sent_to_model_wording(self):
        event = event_text.infer_request(1, "qwen3.6:35b", "model=qwen3.6:35b")
        self.assertIn("Sent to model", event["title"])
        self.assertIn("qwen3.6:35b", event["title"])
        self.assertIn("model=qwen3.6:35b", event["detail"])

    def test_infer_response_reports_status_and_time(self):
        event = event_text.infer_response({"index": 1, "method": "POST", "path": "/v1/responses",
                                           "status": 200, "seconds": 1.2})
        self.assertIn("200", event["title"])
        self.assertIn("1.20s", event["title"])
        failed = event_text.infer_response({"index": 2, "method": "POST", "path": "/v1/responses",
                                             "status": 500, "error": "boom"})
        self.assertIn("FAILED", failed["title"])


class TitlesStayShort(unittest.TestCase):
    def test_no_title_is_a_paragraph(self):
        titles = [
            event_text.model_turn_started(1)["title"],
            event_text.model_turn_done(1, {"input_tokens": 1, "output_tokens": 2})["title"],
            event_text.model_message("x" * 500)["title"],
            event_text.tool_call("render_scene", {"parameters": {"rotationXYZ": [1, 2, 3]},
                                                 "reason": "y" * 500})["title"],
            event_text.tool_return("render_scene", 1.0)["title"],
            event_text.tool_error("render_scene", "z" * 500)["title"],
            event_text.image_event("render", Frames.RESULT)["title"],
            event_text.finish_event({"selectedRevision": 1})["title"],
            event_text.harness_warning("w" * 500)["title"],
        ]
        for title in titles:
            self.assertLessEqual(len(title), 120, title)


if __name__ == '__main__':
    unittest.main()
