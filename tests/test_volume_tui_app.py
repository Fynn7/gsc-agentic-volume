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


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class TextualLayout(unittest.TestCase):
    """Regression: every field stays inside the viewport, rows never overlap."""

    def test_settings_rows_fit_and_stack(self):
        import asyncio
        from textual.widgets import TabbedContent

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                app.query_one("#workspaces", TabbedContent).active = "tab-settings"
                await pilot.pause()
                self.assertTrue(app.query("VerticalScroll"))
                inputs = list(app.query_one("#tab-settings").query("Input"))
                self.assertEqual(len(inputs), 11)
                width = app.screen.size.width
                last_y = -1
                for widget in inputs:
                    region = widget.region
                    self.assertGreater(region.width, 0)
                    self.assertLessEqual(region.x + region.width, width)
                    self.assertGreater(region.y, last_y)
                    last_y = region.y
                labels = list(app.query_one("#tab-settings").query("Label"))
                self.assertGreaterEqual(len(labels), 11)
        asyncio.run(_run())

    def test_live_and_volumes_rows_fit(self):
        import asyncio
        from textual.widgets import TabbedContent

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                width = app.screen.size.width
                app.query_one("#workspaces", TabbedContent).active = "tab-live"
                await pilot.pause()
                for wid in ("#live-search", "#live-category", "#btn-live-copy"):
                    region = app.query_one(wid).region
                    self.assertLessEqual(region.x + region.width, width)
                app.query_one("#workspaces", TabbedContent).active = "tab-volumes"
                await pilot.pause()
                for wid in ("#slice-axis", "#slice-index", "#btn-slice", "#btn-volume-use"):
                    region = app.query_one(wid).region
                    self.assertLessEqual(region.x + region.width, width)
        asyncio.run(_run())

    def test_scroll_keys_from_textarea(self):
        import asyncio
        from textual.containers import VerticalScroll
        from textual.widgets import TabbedContent

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                app.query_one("#workspaces", TabbedContent).active = "tab-newrun"
                await pilot.pause()
                scroller = app.query_one("#tab-newrun").query_one(VerticalScroll)
                app.query_one("#newrun-prompt", tui_app.TextArea).focus()
                await pilot.pause()
                before = scroller.scroll_offset.y
                await pilot.press("ctrl+down")
                await pilot.pause()
                self.assertGreater(scroller.scroll_offset.y, before)
                await pilot.press("ctrl+up")
                await pilot.pause()
                self.assertLess(scroller.scroll_offset.y, before + 8)
        asyncio.run(_run())

    def test_scroll_hint_present(self):
        import asyncio
        from textual.widgets import TabbedContent

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                for tab in ("tab-newrun", "tab-runs", "tab-live", "tab-volumes", "tab-services", "tab-settings"):
                    app.query_one("#workspaces", TabbedContent).active = tab
                    await pilot.pause()
                    hints = app.query_one("#" + tab).query("Static.scroll-hint")
                    self.assertEqual(len(hints), 1, tab)
        asyncio.run(_run())

    def test_dashboard_goto_buttons_stick(self):
        import asyncio
        from textual.widgets import TabbedContent

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                tabs = app.query_one("#workspaces", TabbedContent)
                self.assertEqual(tabs.active, "tab-dashboard")
                await pilot.click("#btn-goto-newrun")
                await pilot.pause()
                await pilot.pause()
                self.assertEqual(tabs.active, "tab-newrun")
                tabs.active = "tab-dashboard"
                await pilot.pause()
                await pilot.click("#btn-goto-live")
                await pilot.pause()
                await pilot.pause()
                self.assertEqual(tabs.active, "tab-live")
        asyncio.run(_run())

    def test_settings_save_roundtrip(self):
        import asyncio
        import tempfile
        from textual.widgets import TabbedContent

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "settings.json"
                with patch.object(tui_app.cfg, "SETTINGS_FILE", target):
                    app = tui_app.VolumeAgentApp()
                    async with app.run_test(size=(100, 30)) as pilot:
                        await pilot.pause()
                        app.query_one("#workspaces", TabbedContent).active = "tab-settings"
                        await pilot.pause()
                        app.query_one("#set-model", tui_app.Input).value = "probe-model"
                        from textual.widgets import Button as _Button
                        app.query_one("#btn-settings-save", _Button).focus()
                        await pilot.pause()
                        await pilot.press("enter")
                        await pilot.pause()
                        self.assertEqual(tui_app.cfg.load_settings()["model"], "probe-model")
        asyncio.run(_run())


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class TextualBusy(unittest.TestCase):
    """Every waiting operation shows feedback and always settles."""

    def test_guarded_worker_shows_busy_and_resets(self):
        import asyncio
        import threading

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                gate = threading.Event()
                app._run_guarded("Probe", gate.wait,
                                 button=app.query_one("#btn-refresh"))
                await pilot.pause()
                self.assertTrue(app.query_one("#btn-refresh").loading)
                self.assertGreater(app._busy_count, 0)
                spinner = app.query_one("#busy-spinner")
                self.assertIn("busy", spinner.classes)
                gate.set()
                for _ in range(100):
                    await pilot.pause()
                    if app._busy_count == 0:
                        break
                self.assertEqual(app._busy_count, 0)
                self.assertFalse(app.query_one("#btn-refresh").loading)
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

    def test_start_launch_failure_settles(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                with tempfile.TemporaryDirectory() as tmp:
                    target = Path(tmp) / "c.json"
                    with patch.object(tui_app.flows_mod, "build_new_run",
                                       return_value=({}, [])), \
                         patch.object(tui_app.flows_mod, "service_gate",
                                       return_value=[]), \
                         patch.object(tui_app.cfg, "write_run_config",
                                       return_value=target), \
                         patch.object(tui_app.runner_mod, "launch",
                                       side_effect=RuntimeError("boom")):
                        app.run_worker(app._start_run, thread=True)
                        for _ in range(100):
                            await pilot.pause()
                            if app._busy_count == 0 and app._launcher_result.get("settled"):
                                break
                self.assertEqual(app._busy_count, 0)
                self.assertFalse(app.query_one("#btn-start-run").loading)
                preflight = app.query_one("#newrun-preflight")
                self.assertIn("boom", str(preflight.render()))
        asyncio.run(_run())

    def test_start_success_enters_live_and_frees_button(self):
        import asyncio
        from textual.widgets import TabbedContent

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
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

                    with patch.object(tui_app.flows_mod, "build_new_run",
                                       return_value=({"task": "x"}, [])), \
                         patch.object(tui_app.flows_mod, "service_gate",
                                       return_value=[]), \
                         patch.object(tui_app.cfg, "write_run_config",
                                       return_value=target), \
                         patch.object(tui_app.runner_mod, "launch",
                                       side_effect=fake_launch):
                        app.run_worker(app._start_run, thread=True)
                        for _ in range(200):
                            await pilot.pause()
                            if (app.query_one("#workspaces", TabbedContent).active == "tab-live"
                                    and app._launcher_result.get("settled")):
                                break
                self.assertEqual(
                    app.query_one("#workspaces", TabbedContent).active, "tab-live")
                self.assertEqual(app._busy_count, 0)
                self.assertFalse(app.query_one("#btn-start-run").loading)
        asyncio.run(_run())

    def test_live_table_keeps_scroll_when_appending(self):
        import asyncio
        import tempfile
        from textual.widgets import TabbedContent

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                app.query_one("#workspaces", TabbedContent).active = "tab-live"
                await pilot.pause()
                with tempfile.TemporaryDirectory() as tmp:
                    app.start_live(Path(tmp), "run-demo")
                    app.live_events = [
                        {"cat": "INFER", "title": "INFER call #%d" % i, "detail": ""}
                        for i in range(40)
                    ]
                    app._render_live()
                    await pilot.pause()
                    table = app.query_one("#events-table", tui_app.DataTable)
                    self.assertEqual(table.row_count, 40)
                    table.scroll_to(y=6, animate=False)
                    await pilot.pause()
                    before = table.scroll_offset.y
                    self.assertGreater(before, 0)
                    # A new event arrives (the normal poll path).
                    app.live_events.append({"cat": "TOOL", "title": "TOOL render_scene", "detail": ""})
                    app._render_live()
                    await pilot.pause()
                    self.assertEqual(table.row_count, 41)
                    # Reading the history: the view is not yanked to the tail.
                    self.assertEqual(table.scroll_offset.y, before)
        asyncio.run(_run())

    def test_live_follows_only_while_at_the_bottom(self):
        import asyncio
        import tempfile
        from textual.widgets import TabbedContent

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                app.query_one("#workspaces", TabbedContent).active = "tab-live"
                await pilot.pause()
                with tempfile.TemporaryDirectory() as tmp:
                    app.start_live(Path(tmp), "run-demo")
                    app.live_events = [
                        {"cat": "INFER", "title": "row %d" % i, "detail": ""} for i in range(40)
                    ]
                    app._render_live()
                    await pilot.pause()
                    table = app.query_one("#events-table", tui_app.DataTable)
                    # The first render ends at the newest event.
                    app.live_events.append({"cat": "TOOL", "title": "new", "detail": ""})
                    app._render_live()
                    await pilot.pause()
                    self.assertEqual(table.row_count, 41)
                    self.assertTrue(app._at_bottom(table))
                    # Scrolling up to read the history stops the follow.
                    table.scroll_to(y=4, animate=False)
                    await pilot.pause()
                    app.live_events.append({"cat": "TOOL", "title": "newer", "detail": ""})
                    app._render_live()
                    await pilot.pause()
                    self.assertEqual(table.scroll_offset.y, 4)
                    self.assertFalse(app._at_bottom(table))
                    # Scrolling back to the tail resumes the follow.
                    app._jump_to_latest()
                    await pilot.pause()
                    self.assertTrue(app._at_bottom(table))
                    app.live_events.append({"cat": "TOOL", "title": "newest", "detail": ""})
                    app._render_live()
                    await pilot.pause()
                    self.assertEqual(table.row_count, 43)
                    self.assertTrue(app._at_bottom(table))
        asyncio.run(_run())

    def test_copy_json_log_button_copies_the_whole_run(self):
        import asyncio
        import tempfile
        from textual.widgets import TabbedContent

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                app.query_one("#workspaces", TabbedContent).active = "tab-live"
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
                        app.query_one("#btn-live-copy", tui_app.Button).focus()
                        await pilot.pause()
                        await pilot.press("enter")
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

    def test_live_head_shows_spinner_when_running(self):
        import asyncio
        import tempfile

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                with tempfile.TemporaryDirectory() as tmp:
                    app.start_live(Path(tmp), "run-demo")
                    app._render_live()
                    await pilot.pause()
                    head = str(app.query_one("#live-head").render())
                    self.assertIn("[live", head)
                    app.live_frozen = True
                    app._render_live()
                    await pilot.pause()
                    frozen = str(app.query_one("#live-head").render())
                    self.assertIn("[FROZEN]", frozen)
                    # Letter-led [...] is markup to Static: must render literally.
                    app._set_text("runs-detail", "Custom: rotate [fast] now [25,-35,10]")
                    await pilot.pause()
                    detail = str(app.query_one("#runs-detail").render())
                    self.assertIn("[fast]", detail)
                    self.assertIn("[25,-35,10]", detail)
        asyncio.run(_run())


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class TextualAppSmoke(unittest.TestCase):
    def test_app_mounts_all_workspaces(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertEqual(len(app.query("TabPane")), 7)
                for widget_id in ("#status-bar", "#runs-table", "#events-table",
                                  "#volumes-table", "#services-table", "#newrun-prompt",
                                  "#live-image", "#slice-preview"):
                    self.assertTrue(app.query(widget_id), widget_id)
                self.assertGreaterEqual(len(app.query("Button")), 20)
        asyncio.run(_run())

    def test_live_filter_and_search(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test() as pilot:
                await pilot.pause()
                app.live_events = [
                    {"cat": "TOOL", "title": "TOOL render_scene Started", "detail": "rotationXYZ"},
                    {"cat": "IMAGE", "title": "IMAGE Frame 1 Native PNG saved", "detail": "rev=1"},
                ]
                app.live_filter = "TOOL"
                self.assertEqual(len(app._filtered_events()), 1)
                app.live_filter = "ALL"
                app.live_search = "rev=1"
                self.assertEqual(len(app._filtered_events()), 1)
                app.live_search = "nope"
                self.assertEqual(app._filtered_events(), [])
        asyncio.run(_run())

    def test_start_live_sets_state(self):
        import asyncio

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                (run_dir / "run.json").write_text(json.dumps({"status": "running"}))
                app = tui_app.VolumeAgentApp()
                async with app.run_test() as pilot:
                    await pilot.pause()
                    app.start_live(run_dir, "run-demo")
                    self.assertEqual(app.live_run_id, "run-demo")
                    self.assertFalse(app.live_frozen)
        asyncio.run(_run())


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class LiveFrameAndLabels(unittest.TestCase):
    """The Live view must show the whole frame and say what every button does."""

    def test_preview_shows_the_whole_frame(self):
        import asyncio
        from PIL import Image
        from textual.widgets import TabbedContent

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                frame_dir = root / "session" / "frame-1"
                frame_dir.mkdir(parents=True)
                png = frame_dir / "image.png"
                Image.new("RGB", (256, 256), (200, 200, 200)).save(png)
                run_dir = root / "run"
                run_dir.mkdir()
                app = tui_app.VolumeAgentApp()
                async with app.run_test(size=(120, 40)) as pilot:
                    await pilot.pause()
                    app.query_one("#workspaces", TabbedContent).active = "tab-live"
                    await pilot.pause()
                    app.start_live(run_dir, "run-demo")
                    app.live_events = [{"cat": "IMAGE", "title": "IMAGE Frame 1",
                                        "detail": "rev=1", "image": str(png), "revision": 1}]
                    app._render_live()
                    await pilot.pause()
                    await pilot.pause()
                    widget = app.query_one("#live-image")
                    lines = widget.content.plain.splitlines()
                    # Every row of the picture is on screen (nothing clipped) ...
                    self.assertEqual(widget.size.height, len(lines))
                    # ... and no row is folded in half (nothing cropped).
                    self.assertEqual(widget.size.width, len(lines[0]))
                    # The PNG path is on screen, so the file can be found.
                    shown = str(app.query_one("#live-image-path").render())
                    self.assertIn(str(png), shown)
        asyncio.run(_run())

    def test_open_folder_targets_the_folder_that_holds_the_png(self):
        import asyncio

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
                async with app.run_test() as pilot:
                    await pilot.pause()
                    app.start_live(run_dir, "run-demo")
                    app.live_image_path = str(png)
                    # The frames live beside the run, not inside the run folder.
                    self.assertEqual(app._live_frames_folder(), root / "session")
                    self.assertNotEqual(app._live_frames_folder(), run_dir)
        asyncio.run(_run())

    def test_every_live_button_says_what_it_does(self):
        import asyncio

        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test() as pilot:
                await pilot.pause()
                expected = {
                    "btn-live-stop": "Stop run",
                    "btn-live-verify": "Verify evidence",
                    "btn-live-open-png": "View PNG",
                    "btn-live-open": "Open folder",
                    "btn-live-detach": "Stop watching",
                    "btn-live-copy": "Copy JSON log",
                }
                for widget_id, label in expected.items():
                    button = app.query_one("#" + widget_id)
                    self.assertEqual(str(button.label), label, widget_id)
                    self.assertTrue(str(button.tooltip), widget_id)
                # The explanations live in the tooltips (and F1), not twice.
                self.assertFalse(app.query("#live-legend"))
        asyncio.run(_run())

    def test_command_palette_actions_have_stable_ids(self):
        actions = [action for _label, action in tui_app.CommandScreen.COMMANDS]
        for action in ("live-verify", "live-detach", "live-open-png", "live-open"):
            self.assertIn(action, actions)
        for action in actions:
            self.assertTrue(action.startswith("go-") or action.startswith("live-")
                            or action.startswith("runs-") or action.startswith("services-")
                            or action in ("help", "quit"))


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
                app.screen.query_one("#cmd-go-settings", tui_app.Button).focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                from textual.widgets import TabbedContent
                self.assertEqual(
                    app.query_one("#workspaces", TabbedContent).active, "tab-settings")
        asyncio.run(_run())


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class ModalEscape(unittest.TestCase):
    """Esc must close every modal the TUI opens (help, palette, confirm)."""

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
        for screen in (tui_app.HelpScreen, tui_app.CommandScreen, tui_app.ConfirmScreen):
            keys = [binding.key for binding in screen.BINDINGS]
            self.assertIn("escape", keys, screen.__name__)


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class QuitLeavesNothingBehind(unittest.TestCase):
    """Quitting the TUI must stop the agent run and the owned services."""

    def test_quit_stops_everything_before_exiting(self):
        import asyncio
        from unittest.mock import patch

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
            async with app.run_test() as pilot:
                await pilot.pause()
                app.runs = [{"runId": "run-x", "status": "Running"}]
                app.live_run_id = "run-x"
                app.action_quit_confirm()
                await pilot.pause()
                text = str(app.screen.query_one("#confirm-message").render())
                self.assertIn("run-x", text)
                self.assertIn("Nothing is left running", text)
                self.assertIn("Ollama", text)
        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
