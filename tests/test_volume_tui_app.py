"""Textual TUI regressions: no Codex harness, model, or renderer is started."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "volume_tui"))

import config as tui_config
import flows as tui_flows
import imaging as tui_imaging
import runner as tui_runner
import runs as tui_runs
import telemetry as tui_telemetry
import trace_events as tui_trace


def _load_tui_app():
    """Load scripts/volume_tui/app.py by path (avoids collision with src/app.py)."""
    import importlib.util
    path = ROOT / "scripts" / "volume_tui" / "app.py"
    spec = importlib.util.spec_from_file_location("volume_tui_app", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load volume_tui app")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    tui_app = _load_tui_app()
    HAS_TEXTUAL = bool(getattr(tui_app, "_TEXTUAL", False))
except Exception:
    tui_app = None
    HAS_TEXTUAL = False

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except Exception:
    HAS_NUMPY = False


class ConfigExtension(unittest.TestCase):
    def test_new_defaults_present_and_valid(self):
        settings = dict(tui_config.DEFAULTS)
        for key in ("contextSize", "renderBudget", "temperature", "think",
                    "timeoutSeconds", "taskTimeoutSeconds", "maxOutputTokens"):
            self.assertIn(key, settings)
        self.assertEqual(tui_config.validate_settings(settings), [])

    def test_invalid_budget_and_endpoint_flagged(self):
        settings = dict(tui_config.DEFAULTS, renderBudget=99, endpoint="http://example.com:11434")
        problems = tui_config.validate_settings(settings)
        self.assertTrue(any("renderBudget" in p for p in problems))
        self.assertTrue(any("endpoint" in p for p in problems))

    def test_effective_config_carries_new_fields(self):
        import volumes as tui_volumes
        with tempfile.TemporaryDirectory() as tmp:
            vol = Path(tmp) / "v.dat"
            vol.write_text("ObjectFileName: v.raw\nResolution: 2 2 2\n")
            settings = dict(tui_config.DEFAULTS, volumePath=str(vol),
                            renderBudget=7, contextSize=8192, temperature=0.5)
            with patch.object(tui_config, "GATE_VOLUME", Path(tmp) / "missing.dat"):
                effective = tui_config.build_effective_config("task-a", "", settings)
            self.assertEqual(effective["renderBudget"], 7)
            self.assertEqual(effective["contextSize"], 8192)
            self.assertEqual(effective["temperature"], 0.5)
            self.assertIn("[25,-35,10]", effective["task"])


class CooperativeStop(unittest.TestCase):
    def test_request_stop_writes_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(tui_runner, "CONTROL_DIR", Path(tmp)):
                marker = tui_runner.request_stop("run-abc")
                self.assertTrue(marker.is_file())
                self.assertEqual(marker.name, "run-abc.stop")

    def test_stop_requested_predicate(self):
        import run_local_agent as launcher
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(launcher, "CONTROL_DIRECTORY", Path(tmp)):
                self.assertFalse(launcher.stop_requested("run-x"))
                (Path(tmp) / "run-x.stop").write_text("stop")
                self.assertTrue(launcher.stop_requested("run-x"))


class TraceTimestamps(unittest.TestCase):
    def test_events_carry_ts_and_mtime_gate_skips_reread(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "session.json").write_text(json.dumps({"id": "s", "events": [
                {"seq": 0, "kind": "external_tool_call", "name": "render_scene", "arguments": {}},
                {"seq": 1, "kind": "render", "result": {"image": "D:/f.png", "scene": {"revision": 1}}},
            ]}))
            tail = tui_trace.EventTail(run_dir, None)
            first = tail.poll()
            self.assertEqual(len(first), 2)
            self.assertTrue(all("ts" in e for e in first))
            # Second poll with unchanged mtime/size yields nothing new.
            self.assertEqual(tail.poll(), [])


class RunHelpers(unittest.TestCase):
    def test_config_diff_and_frame_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = Path(tmp) / "left"
            right = Path(tmp) / "right"
            left.mkdir()
            right.mkdir()
            (left / "config.json").write_text(json.dumps({"task": "a", "model": "m1"}))
            (right / "config.json").write_text(json.dumps({"task": "b", "model": "m1"}))
            diff = tui_runs.config_diff(left, right)
            self.assertTrue(any(line.startswith(("+", "-")) for line in diff))
            (left / "session.json").write_text(json.dumps({"id": "s", "events": [
                {"kind": "render", "result": {"image": "D:/a.png", "scene": {"revision": 2}, "nonblackPixels": 3}}]}))
            frames = tui_runs.frame_images(left)
            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0]["revision"], 2)

    def test_summary_numbers_and_final_image(self):
        entry = {"runId": "r1", "status": "Finished", "model": "m", "frames": 2,
                 "directory": "/tmp/none", "run": {}, "manifest": {"exitCode": 0, "seconds": 5},
                 "verification": {"actualFrames": 2, "agentRenders": 1,
                                  "final": {"image": "D:/final.png", "revision": 2}}}
        numbers = tui_runs.summary_numbers(entry)
        self.assertEqual(numbers["status"], "Finished")
        self.assertEqual(tui_runs.final_image_of(entry), "D:/final.png")

    def test_run_json_log_holds_every_json_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "run.json").write_text(json.dumps({"runId": "r1", "status": "finished"}))
            (run_dir / "session.json").write_text(json.dumps({"id": "s", "events": []}))
            (run_dir / "harness.jsonl").write_text(
                json.dumps({"type": "turn.started"}) + "\n" + json.dumps({"type": "turn.completed"}) + "\n")
            (run_dir / "broken.jsonl").write_text("not json\n")
            http = run_dir / "http"
            http.mkdir()
            (http / "http-001.metadata.json").write_text(json.dumps({"index": 1, "status": 200}))
            events = [{"cat": "MODEL", "title": "MODEL #1  Running", "ts": 1.0}]
            document = tui_runs.run_json_log(run_dir, events=events)
            self.assertEqual(document["runId"], "r1")
            self.assertEqual(document["events"], events)
            self.assertIn("run.json", document["files"])
            self.assertIn("session.json", document["files"])
            self.assertEqual(len(document["files"]["harness.jsonl"]), 2)
            self.assertIn("http/http-001.metadata.json", document["files"])
            # A broken line is reported, never hidden.
            self.assertTrue(any(item["name"] == "broken.jsonl"
                                for item in document["skipped"]))

    def test_run_json_log_omits_base64_image_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "harness.jsonl").write_text(json.dumps({
                "type": "item.completed",
                "item": {"type": "mcp_tool_call",
                         "result": {"content": [{"type": "image", "data": "A" * 4096}]}}}) + "\n")
            document = tui_runs.run_json_log(run_dir)
            blob = document["files"]["harness.jsonl"][0]["item"]["result"]["content"][0]["data"]
            self.assertNotIn("AAAA", blob)
            self.assertIn("4096", blob)


class ClipboardHelper(unittest.TestCase):
    def test_copy_to_clipboard_never_raises(self):
        import evidence as tui_evidence
        message = tui_evidence.copy_to_clipboard("")
        self.assertIn("Nothing to copy", message)

    def test_copy_to_clipboard_reports_failure_without_raising(self):
        import evidence as tui_evidence
        with patch.object(tui_evidence.subprocess, "run",
                          side_effect=OSError("no clipboard")):
            message = tui_evidence.copy_to_clipboard("payload")
        self.assertIn("Clipboard copy failed", message)


class SlicePreview(unittest.TestCase):
    def test_slice_needs_prepared_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            header = Path(tmp) / "plain.dat"
            header.write_text("ObjectFileName: x.raw\nResolution: 2 2 2\n")
            result = __import__("volumes").slice_preview_path(header, "z", 0)
            self.assertFalse(result["ok"])

    def test_slice_renders_small_prepared_volume(self):
        if not HAS_NUMPY:
            self.skipTest("numpy not installed")
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "render.dat").write_text("ObjectFileName: render.raw\nResolution: 4 4 4\n")
            (root / "volume.json").write_text(json.dumps({"dimensions": [4, 4, 4], "volumeHash": "h"}))
            np.arange(64, dtype="uint8").tofile(root / "render.raw")
            result = __import__("volumes").slice_preview_path(root / "render.dat", "z", 1)
            self.assertTrue(result["ok"])
            self.assertTrue(Path(result["image"]).is_file())


class ImagingTelemetry(unittest.TestCase):
    def test_rich_preview_has_cells_not_ansi(self):
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image
            path = Path(tmp) / "frame.png"
            Image.new("RGB", (8, 8), (10, 20, 30)).save(path)
            rendered = tui_imaging.preview_or_message(path, width=8, height=4)
            text = rendered.plain if hasattr(rendered, "plain") else rendered
            self.assertIn("\u2580", text)
            self.assertNotIn("[38;", text)
            self.assertNotIn("\x1b", text)
            spans = getattr(rendered, "spans", None) or []
            self.assertTrue(len(spans) > 0)
            self.assertIn("(no frame", tui_imaging.preview_or_message(None))

    def test_resource_line_missing_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("not yet", tui_telemetry.resource_line(Path(tmp)))
            (Path(tmp) / "telemetry.jsonl").write_text(json.dumps({"gpu": "10 / 100 MiB", "ollama": {"models": []}}) + "\n")
            self.assertIn("GPU", tui_telemetry.resource_line(Path(tmp)))


class FrameFitting(unittest.TestCase):
    """The preview must show the whole frame: fitted, never cropped."""

    def test_fit_box_keeps_aspect_ratio(self):
        self.assertEqual(tui_imaging.fit_box(512, 512, 100, 40), (40, 40))
        self.assertEqual(tui_imaging.fit_box(512, 256, 100, 40), (80, 40))
        self.assertEqual(tui_imaging.fit_box(256, 512, 100, 40), (20, 40))

    def test_render_frame_uses_the_whole_cell_box(self):
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image
            path = Path(tmp) / "frame.png"
            Image.new("RGB", (512, 512), (10, 20, 30)).save(path)
            rendered = tui_imaging.render_frame(path, width=40, height=20)
            lines = rendered.plain.splitlines()
            self.assertEqual(len(lines), 20)
            self.assertEqual(len(lines[0]), 40)

    def test_render_frame_keeps_shape_of_a_wide_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image
            path = Path(tmp) / "frame.png"
            Image.new("RGB", (400, 100), (10, 20, 30)).save(path)
            rendered = tui_imaging.render_frame(path, width=40, height=20)
            lines = rendered.plain.splitlines()
            self.assertEqual(len(lines), 20)
            self.assertEqual(len(lines[0]), 40)
            # A 4:1 frame in a 40x20 cell box keeps its ratio: the picture is
            # 40 columns wide and 5 rows tall, the rest is black padding.
            painted = sum(1 for line in lines if line.strip("\u2580 "))
            self.assertLessEqual(painted, 6)


class NewRunFlow(unittest.TestCase):
    def test_build_new_run_blocks_bad_settings(self):
        settings = dict(tui_config.DEFAULTS, renderBudget=99)
        _, problems = tui_flows.build_new_run("task-a", "", settings, tui_config)
        self.assertTrue(problems)

    def test_custom_never_inherits_ab(self):
        settings = dict(tui_config.DEFAULTS)
        effective, problems = tui_flows.build_new_run("custom", "rotate a little", settings, tui_config)
        self.assertEqual(problems, [])
        self.assertEqual(effective["task"], "rotate a little")


def _conversation_text(app) -> str:
    """Everything the conversation shows, as plain text."""
    parts = []
    for widget in app.query(".block"):
        if isinstance(widget, tui_app.ImageBlock):
            for caption in widget.query(".image-caption"):
                parts.append(str(caption.render()))
            continue
        parts.append(str(widget.render()))
    return "\n".join(parts)


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class CodingAgentScreen(unittest.TestCase):
    """The TUI is one screen, like a coding agent app - no workspaces."""

    def test_the_workspaces_are_gone(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                self.assertEqual(len(app.query("TabbedContent")), 0)
                self.assertEqual(len(app.query("TabPane")), 0)
                self.assertEqual(len(app.query("TabbedContent")), 0)
        asyncio.run(_run())

    def test_the_screen_is_a_status_line_a_conversation_and_a_composer(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                for widget_id in ("#status-bar", "#transcript", "#blocks",
                                  "#composer", "#btn-send"):
                    self.assertTrue(app.query(widget_id), widget_id)
                self.assertTrue(app.query("Footer"))
        asyncio.run(_run())

    def test_every_widget_stays_inside_the_window(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                width = app.screen.size.width
                for widget_id in ("#status-bar", "#transcript", "#composer", "#btn-send"):
                    region = app.query_one(widget_id).region
                    self.assertGreater(region.width, 0, widget_id)
                    self.assertLessEqual(region.x + region.width, width, widget_id)
        asyncio.run(_run())

    def test_ctrl_up_and_down_scroll_the_conversation(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                for index in range(60):
                    app.transcript.add(tui_app.transcript_mod.NOTICE,
                                       title="line %d" % index)
                app._sync_transcript()
                await pilot.pause()
                scroller = app.query_one("#transcript", tui_app.VerticalScroll)
                app.query_one("#composer", tui_app.Composer).focus()
                await pilot.pause()
                # The user reads the history: the view is not at the tail.
                scroller.scroll_to(y=0, animate=False)
                await pilot.pause()
                before = scroller.scroll_offset.y
                await pilot.press("ctrl+down")
                await pilot.pause()
                self.assertGreater(scroller.scroll_offset.y, before)
                await pilot.press("ctrl+up")
                await pilot.pause()
                self.assertLess(scroller.scroll_offset.y, before + 8)
        asyncio.run(_run())

    def test_the_status_line_names_the_model_and_the_run(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            app.settings["model"] = "probe-model"
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.live_run_id = "run-demo"
                app._render_status()
                await pilot.pause()
                status = str(app.query_one("#status-bar").render())
                self.assertIn("probe-model", status)
                self.assertIn("run-demo", status)
                # The model works in the conversation, not in the status line.
                app._ingest_events([{"kind": "thinking_start"}])
                app._render_status()
                await pilot.pause()
                status = str(app.query_one("#status-bar").render())
                self.assertNotIn("thinking", status.lower())
        asyncio.run(_run())

    def test_the_status_line_explains_the_observation_budget(self):
        import asyncio
        import types

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.live_run_id = "run-demo"
                app.live_tail = types.SimpleNamespace(meta={"used": 2, "budget": 4})
                app._render_status()
                await pilot.pause()
                status = str(app.query_one("#status-bar").render())
                self.assertIn("2 of 4 observations used", status)
                self.assertNotIn("obs", status.replace("observations", ""))
        asyncio.run(_run())

    def test_the_volume_is_named_in_voxels(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                volume = Path(tmp) / "present246x246x221.dat"
                volume.write_text("volume")
                app = tui_app.VolumeAgentApp()
                app.settings["volumePath"] = str(volume)
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    app._render_status()
                    await pilot.pause()
                    status = str(app.query_one("#status-bar").render())
                    self.assertIn("246x246x221 voxels", status)
        asyncio.run(_run())

    def test_the_transport_rows_stay_hidden_until_ctrl_h(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._ingest_events([
                    {"kind": "meta", "text": "model call #1"},
                    {"kind": "tool_call", "name": "render_scene", "source": "service",
                     "arguments": {"parameters": {"rotationXYZ": [25, -35, 10]}}},
                ])
                await pilot.pause()
                text = _conversation_text(app)
                # The step is the story; the transport is behind a key press.
                self.assertIn("Render a picture", text)
                self.assertIn("render_scene", text)
                rows = [widget for widget in app.query(".block")
                        if "model call #1" in str(widget.render())]
                self.assertTrue(rows)
                self.assertFalse(rows[0].display)
                app.action_toggle_plumbing()
                await pilot.pause()
                self.assertTrue(rows[0].display)
        asyncio.run(_run())

    def test_f2_opens_the_glossary(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("f2")
                await pilot.pause()
                self.assertIsInstance(app.screen, tui_app.GlossaryScreen)
                text = str(app.screen.query_one("#help-text").render())
                self.assertIn("observation", text)
                await pilot.press("escape")
                await pilot.pause()
        asyncio.run(_run())

    def test_ctrl_t_switches_the_theme(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                before = app.theme
                app.action_toggle_dark()
                await pilot.pause()
                self.assertNotEqual(app.theme, before)
                app.action_toggle_dark()
                await pilot.pause()
                self.assertEqual(app.theme, before)
        asyncio.run(_run())


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class Conversation(unittest.TestCase):
    """The conversation: the task, the thinking, the tool calls, the answer."""

    def test_opening_a_run_starts_with_its_task(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                (run_dir / "config.json").write_text(json.dumps(
                    {"task": "Rotate the camera to 25,-35,10"}))
                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    app.start_live(run_dir, "run-demo")
                    await pilot.pause()
                    first = app.transcript.blocks[0]
                    self.assertEqual(first["kind"], tui_app.transcript_mod.USER)
                    self.assertIn("Rotate the camera", first["title"])
                    self.assertEqual(app.live_run_id, "run-demo")
                    self.assertFalse(app.live_frozen)
        asyncio.run(_run())

    def test_the_conversation_shows_the_tool_call_with_its_numbers(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.transcript.add(tui_app.transcript_mod.USER, title="Rotate it")
                app._ingest_events([
                    {"kind": "thinking_start"},
                    {"kind": "thinking", "reason": "The task wants a rotation.",
                     "tool": "render_scene"},
                    {"kind": "tool_call", "name": "render_scene", "source": "service",
                     "arguments": {"parameters": {"rotationXYZ": [25, -35, 10]}}},
                    {"kind": "tool_result", "name": "render_scene", "source": "service",
                     "seconds": 1.25},
                    {"kind": "assistant", "text": "The camera is rotated now."},
                ])
                await pilot.pause()
                text = _conversation_text(app)
                # The turn carried the agent's reason, not reasoning.
                self.assertIn("Working", text)
                self.assertIn("render_scene", text)
                self.assertIn("Render a picture", text)
                self.assertIn("camera rotation 25,-35,10", text)
                self.assertIn("a new PNG of the volume", text)
                # The model's own words are marked, the screen's are not.
                self.assertIn("\u00bb The camera is rotated now.", text)
                self.assertIn("The camera is rotated now.", text)
                # The tool call is done, so it is marked as such.
                self.assertIn("✓", text)
        asyncio.run(_run())

    def test_a_tool_call_is_shown_once_even_when_two_sources_see_it(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._ingest_events([
                    {"kind": "tool_call", "name": "render_scene", "source": "harness",
                     "arguments": {}},
                    {"kind": "tool_result", "name": "render_scene", "source": "harness"},
                    {"kind": "tool_call", "name": "render_scene", "source": "service",
                     "arguments": {}},
                    {"kind": "tool_result", "name": "render_scene", "source": "service",
                     "seconds": 0.5},
                ])
                await pilot.pause()
                tools = [block for block in app.transcript.blocks
                         if block["kind"] == tui_app.transcript_mod.TOOL]
                self.assertEqual(len(tools), 1)
                self.assertEqual(tools[0]["status"], "done")
        asyncio.run(_run())

    def test_the_conversation_follows_the_tail_only_while_at_the_bottom(self):
        import asyncio
        import tempfile

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                with tempfile.TemporaryDirectory() as tmp:
                    app.start_live(Path(tmp), "run-demo")
                    for index in range(40):
                        app.transcript.add(tui_app.transcript_mod.NOTICE,
                                           title="row %d" % index)
                    app._sync_transcript()
                    await pilot.pause()
                    scroller = app.query_one("#transcript", tui_app.VerticalScroll)
                    scroller.scroll_to(y=6, animate=False)
                    await pilot.pause()
                    before = scroller.scroll_offset.y
                    self.assertGreater(before, 0)
                    # A new entry arrives (the normal poll path).
                    app.transcript.add(tui_app.transcript_mod.NOTICE, title="new")
                    app._sync_transcript()
                    await pilot.pause()
                    # Reading the history: the view is not yanked to the tail.
                    self.assertEqual(scroller.scroll_offset.y, before)
                    # Scrolling back to the tail resumes the follow.
                    scroller.scroll_end(animate=False)
                    await pilot.pause()
                    app.transcript.add(tui_app.transcript_mod.NOTICE, title="newest")
                    app._sync_transcript()
                    for _ in range(20):
                        await pilot.pause()
                        if scroller.scroll_offset.y >= scroller.max_scroll_y - 1:
                            break
                    self.assertGreaterEqual(scroller.scroll_offset.y,
                                            scroller.max_scroll_y - 1)
        asyncio.run(_run())

    def test_the_thinking_block_animates_while_the_model_works(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._ingest_events([{"kind": "thinking_start"}])
                await pilot.pause()
                thinking = app.transcript.find(tui_app.transcript_mod.THINKING)
                self.assertEqual(thinking["status"], "running")
                app._animate_thinking()
                await pilot.pause()
                self.assertIn("s", str(thinking.get("subtitle")))
                # No reasoning yet: the line does not claim thinking.
                self.assertIn("Working", _conversation_text(app))
        asyncio.run(_run())

    def test_the_thinking_block_stops_spinning_when_the_run_is_over(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                (run_dir / "run.json").write_text(json.dumps({"status": "finished"}))
                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    app.start_live(run_dir, "run-demo")
                    app._ingest_events([{"kind": "thinking_start"}])
                    await pilot.pause()
                    thinking = app.transcript.find(tui_app.transcript_mod.THINKING)
                    self.assertEqual(thinking["status"], "running")
                    app._poll_live()
                    await pilot.pause()
                    self.assertTrue(app.live_frozen)
                    self.assertEqual(thinking["status"], "done")
        asyncio.run(_run())

    def test_the_thinking_block_opens_the_reasoning_chain_on_a_click(self):
        import asyncio

        chain = ("Let me inspect the scene to see what volume we are working "
                 "with, then I will render a frame of it.")

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._ingest_events([
                    {"kind": "thinking_start"},
                    {"kind": "reasoning_delta", "delta": chain},
                ])
                await pilot.pause()
                blocks = list(app.query(tui_app.ThinkingBlock))
                self.assertEqual(len(blocks), 1)
                widget = blocks[0]
                # Collapsed: the chain is hidden, a hint points at it.
                self.assertFalse(widget.expanded)
                collapsed = str(widget.render())
                self.assertIn("Thinking", collapsed)
                self.assertIn("▸", collapsed)
                self.assertNotIn(chain, collapsed)
                # A click opens the chain.
                widget.on_click()
                await pilot.pause()
                self.assertTrue(widget.expanded)
                opened = str(widget.render())
                self.assertIn(chain, opened)
                # Open, the arrow points down: the line still says it can close.
                self.assertIn("▾", opened)
                # A second click closes it again.
                widget.on_click()
                await pilot.pause()
                self.assertFalse(widget.expanded)
                self.assertIn("▸", str(widget.render()))
        asyncio.run(_run())

    def test_the_thinking_line_says_thought_when_the_model_is_done(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._ingest_events([
                    {"kind": "thinking_start"},
                    {"kind": "reasoning_done", "text": "I'll inspect the scene."},
                    {"kind": "thinking_end",
                     "usage": {"input_tokens": 12211, "output_tokens": 449}},
                ])
                await pilot.pause()
                widget = list(app.query(tui_app.ThinkingBlock))[0]
                line = str(widget.render())
                self.assertIn("Thought", line)
                self.assertNotIn("Thinking", line)
                # Closed: no preview of the words, only the arrow.
                self.assertIn("▸", line)
                self.assertNotIn("I'll inspect the scene", line)
        asyncio.run(_run())

    def test_a_turn_without_reasoning_is_not_called_thinking(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                # The Think setting is off: the model answers without reasoning.
                app._ingest_events([
                    {"kind": "thinking_start"},
                    {"kind": "thinking_end",
                     "usage": {"input_tokens": 12211, "output_tokens": 449}},
                ])
                await pilot.pause()
                widget = list(app.query(tui_app.ThinkingBlock))[0]
                line = str(widget.render())
                self.assertIn("Model answered", line)
                self.assertNotIn("Thinking", line)
                self.assertNotIn("Thought", line)
                # Nothing to open: no arrow.
                self.assertNotIn("▸", line)
        asyncio.run(_run())

    def test_a_turn_with_reasoning_is_called_thinking(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._ingest_events([
                    {"kind": "thinking_start"},
                    {"kind": "reasoning_delta", "delta": "I look at the scene."},
                    {"kind": "thinking_end",
                     "usage": {"input_tokens": 12211, "output_tokens": 449}},
                ])
                await pilot.pause()
                widget = list(app.query(tui_app.ThinkingBlock))[0]
                line = str(widget.render())
                self.assertIn("Thought", line)
                self.assertNotIn("Model answered", line)
                self.assertIn("▸", line)
        asyncio.run(_run())

    def test_the_screen_ticks_faster_than_the_backend(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                # The screen has its own fast clock; the backend keeps its.
                self.assertLess(tui_app.UI_TICK_SECONDS, tui_app.BACKEND_TICK_SECONDS)
                app._ingest_events([{"kind": "thinking_start"}])
                await pilot.pause()
                app._animate_thinking()
                await pilot.pause()
                block = app.transcript.find(tui_app.transcript_mod.THINKING)
                subtitle = str(block.get("subtitle"))
                # Whole seconds, no decimals.
                self.assertTrue(subtitle.endswith("s"), subtitle)
                self.assertNotIn(".", subtitle)
        asyncio.run(_run())

    def test_the_model_stream_reaches_the_conversation(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                http = run_dir / "http"
                http.mkdir()
                (http / "http-001.response.bin").write_text(
                    'event: x\ndata: {"type": "response.output_text.delta", '
                    '"delta": "It is a frog."}\n\n', encoding="utf-8")
                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    app.start_live(run_dir, "run-demo")
                    await pilot.pause()
                    app._poll_live()
                    await pilot.pause()
                    answers = [block for block in app.transcript.blocks
                               if block["kind"] == tui_app.transcript_mod.ASSISTANT]
                    self.assertEqual(len(answers), 1)
                    self.assertEqual(answers[0]["title"], "It is a frog.")
        asyncio.run(_run())

    def test_the_frame_preview_shows_the_whole_frame(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                frame_dir = root / "session" / "frame-1"
                frame_dir.mkdir(parents=True)
                png = frame_dir / "image.png"
                from PIL import Image
                Image.new("RGB", (256, 256), (200, 200, 200)).save(png)
                run_dir = root / "run"
                run_dir.mkdir()
                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(120, 40)) as pilot:
                    await pilot.pause()
                    app.start_live(run_dir, "run-demo")
                    app._ingest_events([
                        {"kind": "image", "source": "render", "revision": 1,
                         "lit": "148491", "size": "512x512", "image": str(png)},
                    ])
                    await pilot.pause()
                    await pilot.pause()
                    preview = app.query_one(".image-preview")
                    lines = preview.content.plain.splitlines()
                    # Every row of the picture is on screen (nothing clipped) ...
                    self.assertEqual(preview.size.height, len(lines))
                    # ... and no row is folded in half (nothing cropped): the
                    # picture is drawn at its own width, never wider.
                    self.assertLessEqual(len(lines[0]), preview.size.width)
                    # The PNG path is on screen, so the file can be found.
                    self.assertIn(str(png), _conversation_text(app))
        asyncio.run(_run())

    def test_open_folder_targets_the_folder_that_holds_the_png(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                frame_dir = root / "session" / "frame-1"
                frame_dir.mkdir(parents=True)
                png = frame_dir / "image.png"
                png.write_bytes(b"not-a-real-png")
                run_dir = root / "run"
                run_dir.mkdir()
                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    app.start_live(run_dir, "run-demo")
                    app.live_image_path = str(png)
                    # The frames live beside the run, not inside the run folder.
                    self.assertEqual(app._live_frames_folder(), root / "session")
                    self.assertNotEqual(app._live_frames_folder(), run_dir)
        asyncio.run(_run())


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class ComposerFlow(unittest.TestCase):
    """Enter in the composer starts a run with that task."""

    def _patched_launch(self, tmp, run_dir):
        target = Path(tmp) / "c.json"

        def fake_launch(config_path, on_line=None):
            if on_line:
                on_line('{"event": "run_started", "runId": "r1",'
                        ' "directory": "%s"}' % run_dir)
            result = tui_runner.LaunchResult()
            result.run_id = "r1"
            result.directory = run_dir
            result.exit_code = 0
            result.output_tail = ""
            return result

        return patch.multiple(
            tui_app.flows_mod, build_new_run=patch.DEFAULT,
            service_gate=patch.DEFAULT), target, fake_launch

    def test_enter_in_the_composer_starts_a_run(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run-r1"
                run_dir.mkdir()
                target = Path(tmp) / "c.json"

                def fake_launch(config_path, on_line=None):
                    if on_line:
                        on_line('{"event": "run_started", "runId": "r1",'
                                ' "directory": "%s"}' % run_dir)
                    result = tui_runner.LaunchResult()
                    result.run_id = "r1"
                    result.directory = run_dir
                    result.exit_code = 0
                    result.output_tail = ""
                    return result

                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(100, 32)) as pilot:
                    await pilot.pause()
                    with patch.object(tui_app.flows_mod, "build_new_run",
                                      return_value=({"task": "x"}, [])), \
                         patch.object(tui_app.flows_mod, "service_gate",
                                      return_value=[]), \
                         patch.object(tui_app.cfg, "write_run_config",
                                      return_value=target), \
                         patch.object(tui_app.runner_mod, "launch",
                                      side_effect=fake_launch):
                        composer = app.query_one("#composer", tui_app.Composer)
                        composer.text = "Rotate the camera"
                        composer.focus()
                        await pilot.pause()
                        await pilot.press("enter")
                        for _ in range(200):
                            await pilot.pause()
                            if app._launcher_result.get("settled"):
                                break
                        for _ in range(200):
                            await pilot.pause()
                            if app._busy_count == 0:
                                break
                    self.assertEqual(app.live_run_id, "r1")
                    self.assertEqual(app._busy_count, 0)
                    # The composer is empty again - the task is in the run.
                    self.assertEqual(app.query_one("#composer").text, "")
        asyncio.run(_run())

    def test_a_launch_failure_settles_and_says_why(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "c.json"
                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(100, 32)) as pilot:
                    await pilot.pause()
                    with patch.object(tui_app.flows_mod, "build_new_run",
                                      return_value=({"task": "x"}, [])), \
                         patch.object(tui_app.flows_mod, "service_gate",
                                      return_value=[]), \
                         patch.object(tui_app.cfg, "write_run_config",
                                      return_value=target), \
                         patch.object(tui_app.runner_mod, "launch",
                                      side_effect=RuntimeError("boom")):
                        app.run_worker(lambda: app._start_run_worker("custom", "x"),
                                       thread=True)
                        for _ in range(200):
                            await pilot.pause()
                            if app._busy_count == 0 and app._launcher_result.get("settled"):
                                break
                    self.assertEqual(app._busy_count, 0)
                    self.assertFalse(app._start_locked)
                    self.assertIn("boom", _conversation_text(app))
        asyncio.run(_run())

    def test_a_second_task_does_not_start_a_second_run(self):
        import asyncio
        import threading

        async def _run():
            app = tui_app.VolumeAgentApp()
            gate = threading.Event()
            started = []

            def _slow_gate(settings, env_mod, services_mod):
                started.append("gate")
                gate.wait(2.0)
                return []

            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                with patch.object(tui_app.flows_mod, "build_new_run",
                                  return_value=({"task": "x"}, [])), \
                     patch.object(tui_app.flows_mod, "service_gate", side_effect=_slow_gate), \
                     patch.object(tui_app.cfg, "write_run_config",
                                  return_value=Path("c.json")), \
                     patch.object(tui_app.runner_mod, "launch",
                                  return_value=tui_runner.LaunchResult()):
                    composer = app.query_one("#composer", tui_app.Composer)
                    composer.text = "first task"
                    app.action_submit_task()
                    await pilot.pause()
                    composer.text = "second task"
                    app.action_submit_task()
                    await pilot.pause()
                    # The gate is still running: the second task was refused.
                    self.assertEqual(len(started), 1)
                    self.assertTrue(app._start_locked)
                    gate.set()
                    for _ in range(200):
                        await pilot.pause()
                        if not app._start_locked:
                            break
                    self.assertFalse(app._start_locked)
        asyncio.run(_run())

    def test_copying_a_selection_never_crashes(self):
        """A selection past the last line must not break the copy.

        While the answer grows, a block can be one layout behind its content;
        Textual's own extraction then reads past the last line.
        """
        import asyncio
        from textual.geometry import Offset
        from textual.selection import Selection

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._ingest_events([
                    {"kind": "thinking_start"},
                    {"kind": "reasoning_delta",
                     "delta": "I'll start by inspecting the current scene."},
                ])
                for _ in range(3):
                    await pilot.pause()
                widget = list(app.query(tui_app.ThinkingBlock))[0]
                # The selection points one line past the content.
                selection = Selection(start=Offset(2, 1), end=Offset(24, 1))
                selected = widget.get_selection(selection)
                self.assertIsNotNone(selected)
                self.assertIn("Thinking", selected[0])
                # The screen copy action works as well.
                await pilot.press("ctrl+c")
                await pilot.pause()
                self.assertTrue(app.is_running)
        asyncio.run(_run())

    def test_esc_asks_the_agent_to_stop(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                app.live_run_id = "run-demo"
                with patch.object(tui_app.runner_mod, "request_stop") as stop:
                    await pilot.press("escape")
                    await pilot.pause()
                    # A second Esc does not repeat the request or the line.
                    await pilot.press("escape")
                    await pilot.pause()
                self.assertEqual(stop.call_count, 1)
                self.assertEqual(stop.call_args.args[0], "run-demo")
                self.assertEqual(_conversation_text(app).count("Stop requested"), 1)
        asyncio.run(_run())

    def test_esc_stops_the_agent_with_the_composer_focused(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                app.live_run_id = "run-demo"
                app.query_one("#composer", tui_app.Composer).focus()
                await pilot.pause()
                with patch.object(tui_app.runner_mod, "request_stop") as stop:
                    await pilot.press("escape")
                    await pilot.pause()
                self.assertTrue(stop.called, "Esc must stop the agent")
        asyncio.run(_run())

    def test_the_send_button_becomes_a_red_stop_while_the_agent_works(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                (run_dir / "run.json").write_text(json.dumps({"status": "running"}))
                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(100, 32)) as pilot:
                    await pilot.pause()
                    button = app.query_one("#btn-send", tui_app.Button)
                    self.assertEqual(str(button.label), "Send")
                    app.start_live(run_dir, "run-demo")
                    await pilot.pause()
                    # The agent works: the button stops it instead of sending.
                    self.assertEqual(str(button.label), "Stop")
                    self.assertEqual(button.variant, "error")
                    with patch.object(tui_app.runner_mod, "request_stop") as stop:
                        button.focus()
                        await pilot.pause()
                        await pilot.press("enter")
                        await pilot.pause()
                    self.assertTrue(stop.called, "the Stop button must stop the agent")
                    # And the line says that the agent is stopping.
                    self.assertIn("Stopping", str(button.label))
        asyncio.run(_run())

    def test_the_status_line_says_that_the_agent_is_stopping(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                (run_dir / "run.json").write_text(json.dumps({"status": "running"}))
                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(100, 32)) as pilot:
                    await pilot.pause()
                    app.start_live(run_dir, "run-demo")
                    await pilot.pause()
                    with patch.object(tui_app.runner_mod, "request_stop"):
                        await pilot.press("escape")
                        await pilot.pause()
                    status = str(app.query_one("#status-bar").render())
                    self.assertIn("stopping", status)
                    # The run ended as interrupted: the line says so.
                    (run_dir / "run.json").write_text(json.dumps(
                        {"status": "failed", "interrupted": True}))
                    app._poll_live()
                    await pilot.pause()
                    status = str(app.query_one("#status-bar").render())
                    self.assertIn("stopped", status)
                    self.assertIn("Run stopped", _conversation_text(app))
        asyncio.run(_run())


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class Dialogs(unittest.TestCase):
    """The pages that were deleted are now dialogs behind the palette."""

    def test_the_settings_dialog_saves_the_form(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "settings.json"
                with patch.object(tui_app.cfg, "SETTINGS_FILE", target):
                    app = tui_app.VolumeAgentApp()
                    async with app.run_test(size=(100, 32)) as pilot:
                        await pilot.pause()
                        app._open_settings()
                        await pilot.pause()
                        self.assertIsInstance(app.screen, tui_app.SettingsScreen)
                        app.screen.query_one("#set-model", tui_app.Input).value = "probe-model"
                        app.screen.query_one("#btn-settings-save", tui_app.Button).focus()
                        await pilot.pause()
                        await pilot.press("enter")
                        await pilot.pause()
                        self.assertEqual(tui_app.cfg.load_settings()["model"], "probe-model")
        asyncio.run(_run())

    def test_reset_to_defaults_fills_the_form_and_writes_nothing(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "settings.json"
                with patch.object(tui_app.cfg, "SETTINGS_FILE", target):
                    app = tui_app.VolumeAgentApp()
                    async with app.run_test(size=(100, 32)) as pilot:
                        await pilot.pause()
                        app._open_settings()
                        await pilot.pause()
                        app.screen.query_one("#set-model", tui_app.Input).value = "changed-model"
                        app.screen._reset()
                        await pilot.pause()
                        # The form is filled with the defaults ...
                        self.assertEqual(app.screen.query_one("#set-model", tui_app.Input).value,
                                         str(tui_app.cfg.DEFAULTS["model"]))
                        self.assertEqual(app.screen.query_one("#set-budget", tui_app.Input).value,
                                         str(tui_app.cfg.DEFAULTS["renderBudget"]))
                        # ... and nothing was written (the button says so).
                        self.assertFalse(target.exists())
        asyncio.run(_run())

    def test_the_settings_dialog_offers_the_volume_and_the_model(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                volume = Path(tmp) / "render.dat"
                volume.write_text("ObjectFileName: x.raw\nResolution: 2 2 2\n")
                target = Path(tmp) / "settings.json"
                with patch.object(tui_app.cfg, "SETTINGS_FILE", target):
                    app = tui_app.VolumeAgentApp()
                    async with app.run_test(size=(100, 32)) as pilot:
                        await pilot.pause()
                        # The volume in use is offered in the dialog.
                        app.settings["volumePath"] = str(volume)
                        app._open_settings()
                        await pilot.pause()
                        screen = app.screen
                        # The model is still configurable ...
                        screen.query_one("#set-model", tui_app.Input).value = "probe-model"
                        # ... and so is the volume the agent works on.
                        screen.query_one("#set-volume", tui_app.Select).value = str(volume)
                        screen.query_one("#btn-settings-save", tui_app.Button).focus()
                        await pilot.pause()
                        await pilot.press("enter")
                        await pilot.pause()
                        saved = tui_app.cfg.load_settings()
                        self.assertEqual(saved["model"], "probe-model")
                        self.assertEqual(saved["volumePath"], str(volume))
                        # The app uses the new settings for the next run.
                        self.assertEqual(app.settings["model"], "probe-model")
        asyncio.run(_run())

    def test_the_runs_dialog_opens_a_run(self):
        import asyncio
        import tempfile

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                (run_dir / "config.json").write_text(json.dumps({"task": "the old task"}))
                runs = [{"runId": "run-old", "status": "Finished", "model": "m",
                         "frames": 1, "task": "the old task", "directory": str(run_dir)}]
                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(100, 32)) as pilot:
                    await pilot.pause()
                    with patch.object(app, "discover_runs", return_value=runs):
                        app._open_runs()
                    await pilot.pause()
                    self.assertIsInstance(app.screen, tui_app.RunsScreen)
                    app.screen.query_one("#runs-modal-table", tui_app.DataTable).focus()
                    await pilot.pause()
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertEqual(app.live_run_id, "run-old")
                    self.assertIn("the old task", _conversation_text(app))
        asyncio.run(_run())


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class BusyFeedback(unittest.TestCase):
    """Every waiting operation shows feedback and always settles."""

    def test_guarded_worker_shows_busy_and_resets(self):
        import asyncio
        import threading

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                gate = threading.Event()
                app._run_guarded("Probe", gate.wait)
                await pilot.pause()
                self.assertGreater(app._busy_count, 0)
                spinner = app.query_one("#busy-spinner")
                self.assertIn("busy", spinner.classes)
                gate.set()
                for _ in range(100):
                    await pilot.pause()
                    if app._busy_count == 0:
                        break
                self.assertEqual(app._busy_count, 0)
                self.assertNotIn("busy", app.query_one("#busy-spinner").classes)
        asyncio.run(_run())

    def test_guarded_worker_error_resets_busy(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()

                def boom():
                    raise RuntimeError("probe-boom")

                app._run_guarded("Probe", boom)
                for _ in range(100):
                    await pilot.pause()
                    if app._busy_count == 0:
                        break
                self.assertEqual(app._busy_count, 0)
        asyncio.run(_run())

    def test_copy_json_log_copies_the_whole_run(self):
        import asyncio
        import tempfile

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                with tempfile.TemporaryDirectory() as tmp:
                    run_dir = Path(tmp)
                    (run_dir / "run.json").write_text(json.dumps({"runId": "run-demo"}))
                    app.start_live(run_dir, "run-demo")
                    app.live_events = [{"cat": "MODEL", "title": "MODEL #1  Running", "detail": ""}]
                    copied = {}

                    def _fake_copy(text):
                        copied["text"] = text
                        return "Copied %d characters to the clipboard." % len(text)

                    with patch.object(tui_app.evidence_mod, "copy_to_clipboard", _fake_copy):
                        app._copy_run_json_log()
                        for _ in range(200):
                            await pilot.pause()
                            if "text" in copied and app._busy_count == 0:
                                break
                    self.assertIn("text", copied)
                    document = json.loads(copied["text"])
                    self.assertEqual(document["runId"], "run-demo")
                    self.assertEqual(len(document["events"]), 1)
                    self.assertIn("run.json", document["files"])
                    self.assertEqual(app._busy_count, 0)
        asyncio.run(_run())


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class CtrlPOpensTheTuiPalette(unittest.TestCase):
    """Ctrl+P must open the TUI palette, not Textual's system palette."""

    def test_ctrl_p_is_not_taken_over_by_textual(self):
        app = tui_app.VolumeAgentApp()
        bindings = app._bindings.key_to_bindings.get("ctrl+p", ())
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].action, "app.command_palette")

    def test_ctrl_p_opens_the_tui_palette(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                await pilot.press("ctrl+p")
                await pilot.pause()
                self.assertIsInstance(app.screen, tui_app.CommandScreen)
                # The palette runs its action when a command is picked.
                app.screen.query_one("#cmd-settings", tui_app.Button).focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, tui_app.SettingsScreen)
        asyncio.run(_run())

    def test_every_command_palette_button_carries_a_tooltip(self):
        for _label, _action, tooltip in tui_app.CommandScreen.COMMANDS:
            self.assertTrue(str(tooltip).strip())

    def test_the_palette_knows_the_run_commands(self):
        actions = [action for _label, action, _tooltip in tui_app.CommandScreen.COMMANDS]
        for action in ("run-task-a", "run-task-b", "live-stop", "live-verify",
                       "live-copy", "live-open-png", "live-open", "runs-open",
                       "settings", "theme", "help", "hard-stop", "quit"):
            self.assertIn(action, actions)

    def test_the_hard_stop_kills_everything_and_leaves(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            with patch.object(tui_app.services_mod, "shutdown_all") as shutdown:
                async with app.run_test(size=(100, 32)) as pilot:
                    await pilot.pause()
                    app._command_chosen("cmd-hard-stop")
                    await pilot.pause()
                    await asyncio.sleep(0.2)
                    await pilot.pause()
            self.assertTrue(shutdown.called, "the hard stop must stop everything")
            self.assertTrue(shutdown.call_args.kwargs.get("hard"))
            self.assertFalse(app.is_running)
        asyncio.run(_run())


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class ServiceLifecycle(unittest.TestCase):
    """The services the TUI owns are still started and stopped correctly."""

    def test_entry_starts_the_ownership_heartbeat(self):
        with patch.object(tui_app.services_mod, "ensure_services",
                          return_value={"started": [], "reused": [], "adopted": [],
                                        "problems": [], "contested": False,
                                        "owns": False}), \
             patch.object(tui_app.services_mod, "heartbeat_start") as heartbeat:
            tui_app.start_owned_services({"mode": "Local"})
        # Without the heartbeat the record goes stale and another TUI session
        # adopts (and stops) the services this session still owns.
        self.assertTrue(heartbeat.called)

    def test_shutdown_cache_is_not_poisoned_by_the_dialogs(self):
        import services as tui_services
        with patch.object(tui_services, "_SHUTDOWN_REPORT", None), \
             patch.object(tui_services, "_block_interrupts"), \
             patch.object(tui_services, "heartbeat_stop"), \
             patch.object(tui_services, "request_run_stops", return_value=[]), \
             patch.object(tui_services, "_wait_for_launchers", return_value=[]), \
             patch.object(tui_services, "_terminate_agent_trees",
                          return_value={"stopped": [], "failed": []}), \
             patch.object(tui_services, "_inference_state",
                          return_value={"endpoint": None, "verified": None, "owned": False}), \
             patch.object(tui_services, "release_owned_services",
                          return_value={"stopped": [], "kept": None, "skipped": []}), \
             patch.object(tui_services, "_owns_ollama", return_value=False):
            tui_services.shutdown_all({"mode": "Local"}, hard=False, cache=False)
            # The quit path must still run its own shutdown afterwards.
            self.assertIsNone(tui_services._SHUTDOWN_REPORT)


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class ModalEscape(unittest.TestCase):
    """Esc must close every dialog the TUI opens."""

    def test_escape_closes_the_help_screen(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                await pilot.press("f1")
                await pilot.pause()
                self.assertIsInstance(app.screen, tui_app.HelpScreen)
                await pilot.press("escape")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, tui_app.DismissibleModal)
                # Esc closes the overlay, it never quits the TUI.
                self.assertTrue(app.is_running)
        asyncio.run(_run())

    def test_escape_closes_the_command_palette(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                app.action_commands()
                await pilot.pause()
                self.assertIsInstance(app.screen, tui_app.CommandScreen)
                await pilot.press("escape")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, tui_app.DismissibleModal)
                self.assertTrue(app.is_running)
        asyncio.run(_run())

    def test_escape_cancels_the_confirm_dialog(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            ran = []
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                app.push_screen(tui_app.ConfirmScreen(
                    "Stop the run?", on_yes=lambda: ran.append("yes")))
                await pilot.pause()
                self.assertIsInstance(app.screen, tui_app.ConfirmScreen)
                await pilot.press("escape")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, tui_app.DismissibleModal)
                # Esc is a cancel, never the Yes action.
                self.assertEqual(ran, [])
        asyncio.run(_run())

    def test_every_modal_has_the_escape_binding(self):
        for screen in (tui_app.HelpScreen, tui_app.CommandScreen, tui_app.ConfirmScreen,
                       tui_app.RunsScreen, tui_app.SettingsScreen):
            keys = [binding.key for binding in screen.BINDINGS]
            self.assertIn("escape", keys, screen.__name__)


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class QuitLeavesNothingBehind(unittest.TestCase):
    """Quitting the TUI must stop the agent run and the owned services."""

    def test_quit_stops_everything_before_exiting(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            with patch.object(tui_app.services_mod, "shutdown_all") as shutdown:
                async with app.run_test() as pilot:
                    await pilot.pause()
                    app.runs = [{"runId": "run-x", "status": "Running"}]
                    app.live_run_id = "run-x"
                    app._quit_and_stop()
                    await pilot.pause()
                    await asyncio.sleep(0.2)
                    await pilot.pause()
            self.assertTrue(shutdown.called, "the quit must run the shutdown")
            self.assertFalse(app.is_running)
        asyncio.run(_run())

    def test_quit_dialog_names_the_run_and_promises_no_leftovers(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                app.runs = [{"runId": "run-x", "status": "Running"}]
                app.live_run_id = "run-x"
                # Ctrl+Q is the quit key.
                await pilot.press("ctrl+q")
                await pilot.pause()
                self.assertIsInstance(app.screen, tui_app.ConfirmScreen)
                text = str(app.screen.query_one("#confirm-message").render())
                self.assertIn("run-x", text)
                self.assertIn("Nothing is left running", text)
                self.assertIn("Ollama", text)
        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
