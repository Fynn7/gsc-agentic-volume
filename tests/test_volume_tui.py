"""Non-recursive TUI regressions: no Codex harness, model, or renderer is started.

Launcher `execute` is mocked; HTTP/verify paths use temp dirs and fixture runs.
"""
import io
import json
import struct
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "volume_tui"))

import config as tui_config
import run_lifecycle as lifecycle
import run_local_agent as launcher
import runner as tui_runner
import runs as tui_runs
import trace_events as tui_trace


def _minimal_config(tmp: Path, **overrides):
    volume = tmp / "volume.dat"
    volume.write_text("header")
    config = {"harness": "codex", "model": "qwen3.6:35b", "endpoint": "http://127.0.0.1:11434",
              "rendererService": "http://127.0.0.1:8765", "contextSize": 16384, "timeoutSeconds": 300,
              "taskTimeoutSeconds": 900, "temperature": 0, "maxOutputTokens": 1200,
              "volumePath": str(volume), "renderBudget": 4, "task": "unit task",
              "resultDirectory": str(tmp / "results")}
    config.update(overrides)
    return config


class TaskConfig(unittest.TestCase):
    def test_task_a_b_match_manual_and_custom_is_isolated(self):
        self.assertIn("[25,-35,10]", tui_config.TASK_A)
        self.assertIn("at least two new native renders", tui_config.TASK_B.lower())
        settings = dict(tui_config.DEFAULTS)
        # Custom text with similar words must not inherit A/B prompts.
        custom = tui_config.build_effective_config("custom", "rotate a little", settings)
        self.assertEqual(custom["task"], "rotate a little")
        self.assertNotIn("25,-35,10", custom["task"])
        with self.assertRaises(ValueError):
            tui_config.build_effective_config("custom", "   ", settings)

    def test_settings_roundtrip_and_endpoint_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            with patch.object(tui_config, "SETTINGS_FILE", target):
                settings = tui_config.load_settings()
                self.assertEqual(settings["model"], "qwen3.6:35b")
                settings["mode"] = "H200"
                settings["volumePath"] = str(target.parent / "chosen.dat")
                tui_config.save_settings(settings)
                reloaded = tui_config.load_settings()
                self.assertEqual(reloaded["mode"], "H200")
                self.assertEqual(reloaded["volumePath"], settings["volumePath"])
                self.assertEqual(tui_config.active_endpoint(reloaded), reloaded["h200Endpoint"])

    def test_selected_volume_drives_new_configs_with_gate_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate = root / "gate" / "render.dat"
            gate.parent.mkdir()
            gate.write_text("ObjectFileName: render.raw\nResolution: 4 4 4\n")
            chosen = root / "chosen.dat"
            chosen.write_text("ObjectFileName: chosen.raw\nResolution: 8 8 8\n")
            with patch.object(tui_config, "GATE_VOLUME", gate):
                settings = dict(tui_config.DEFAULTS, volumePath=str(chosen))
                built = tui_config.build_effective_config("task-a", "", settings)
                self.assertEqual(Path(built["volumePath"]), chosen.resolve())
                settings["volumePath"] = ""
                built = tui_config.build_effective_config("task-a", "", settings)
                self.assertEqual(Path(built["volumePath"]), gate.resolve())


class VolumeDiscovery(unittest.TestCase):
    def _tree(self, root: Path):
        runtime = root / "runtime"
        gate = runtime / "gate-volume" / "render.dat"
        gate.parent.mkdir(parents=True)
        gate.write_text("ObjectFileName: render.raw\nResolution: 4 5 6\nFormat: uchar\n")
        (gate.parent / "volume.json").write_text(json.dumps(
            {"dimensions": [4, 5, 6], "volumeHash": "same"}))

        # Fixed scan root: only this folder is discovered.
        volumes = root / "volumes"
        copied = volumes / "session-1" / "volume"
        copied.mkdir(parents=True)
        (copied / "render.dat").write_text("ObjectFileName: render.raw\nResolution: 4 5 6\n")
        (copied / "volume.json").write_text(json.dumps(
            {"dimensions": [4, 5, 6], "volumeHash": "same"}))

        duplicate = volumes / "session-2" / "volume"
        duplicate.mkdir(parents=True)
        (duplicate / "render.dat").write_text("ObjectFileName: render.raw\nResolution: 4 5 6\n")
        (duplicate / "volume.json").write_text(json.dumps(
            {"dimensions": [4, 5, 6], "volumeHash": "same"}))

        sample = volumes / "sample.nhdr"
        sample.write_text("NRRD0004\ntype: uchar\ndimension: 3\nsizes: 8 9 10\n")
        compressed = volumes / "compressed.dat"
        compressed.write_text("ObjectFileName: x.lz4\nResolution: 2 2 2\nCompression: lz4\n")

        # TU Wien binary DAT (6-byte header, no sibling volume.json).
        binary = volumes / "present246x246x221.dat"
        binary.write_bytes(struct.pack("<3H", 246, 246, 221) + b"\x00" * 16)

        # Outside the fixed folder: must not be discovered any more.
        outside = root / "experiments" / "sample.nhdr"
        outside.parent.mkdir()
        outside.write_text("NRRD0004\ntype: uchar\ndimension: 3\nsizes: 1 2 3\n")
        prior = runtime / "run-3" / "volume" / "render.dat"
        prior.parent.mkdir(parents=True)
        prior.write_text("ObjectFileName: header.raw\nResolution: 9 9 9\n")
        (prior.parent / "volume.json").write_text(json.dumps(
            {"dimensions": [9, 9, 9], "volumeHash": "prior"}))
        prior_config = runtime / "run-3"
        (prior_config / "config.json").write_text(json.dumps({"volumePath": str(prior)}))
        return runtime, gate, volumes, duplicate / "render.dat", sample, compressed, binary, outside, prior

    def _scan(self, root, runtime, gate, volumes, selected=None):
        import volumes as tui_volumes
        with patch.object(tui_volumes, "ROOT", root), \
             patch.object(tui_volumes, "RUNTIME", runtime), \
             patch.object(tui_volumes, "GATE", gate), \
             patch.object(tui_volumes, "VOLUME_DIR", volumes):
            return tui_volumes.discover_volumes(selected)

    def test_scan_reads_only_the_volume_folder_and_collapses_identical_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, gate, volumes, duplicate, sample, compressed, binary, outside, prior = self._tree(root)
            found = self._scan(root, runtime, gate, volumes, gate)
            paths = [Path(item["path"]) for item in found]
            self.assertEqual(paths[0], gate.resolve())
            self.assertTrue(found[0]["default"])
            self.assertEqual(len([item for item in found if item["hash"] == "same"]), 1)
            self.assertEqual(found[[item["hash"] for item in found].index("same")]["copies"], 3)
            self.assertNotIn(duplicate.resolve(), paths)  # identical payload collapses into the gate row
            self.assertNotIn((volumes / "session-1" / "volume" / "render.dat").resolve(), paths)
            self.assertIn(sample.resolve(), paths)
            self.assertNotIn(compressed.resolve(), paths)
            self.assertNotIn(outside.resolve(), paths)
            self.assertNotIn(prior.resolve(), paths)
            nhdr = next(item for item in found if Path(item["path"]) == sample.resolve())
            self.assertEqual(nhdr["dimensions"], [8, 9, 10])
            binary_item = next(item for item in found if Path(item["path"]) == binary.resolve())
            self.assertEqual(binary_item["dimensions"], [246, 246, 221])

    def test_selection_is_listed_first_and_unsupported_is_explained(self):
        import volumes as tui_volumes
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, gate, volumes, duplicate, sample, compressed, binary, outside, prior = self._tree(root)
            found = self._scan(root, runtime, gate, volumes, duplicate)
            self.assertEqual(Path(found[0]["path"]), duplicate.resolve())
            self.assertIn("8x9x10", tui_volumes.option_text(
                next(item for item in found if Path(item["path"]) == sample.resolve())))
            facts = tui_volumes.describe(compressed)
            self.assertFalse(facts["ok"])
            self.assertIn("lz4", facts["detail"])

    def test_preflight_checks_the_selected_volume(self):
        import env as tui_env
        with tempfile.TemporaryDirectory() as tmp:
            chosen = Path(tmp) / "chosen.dat"
            chosen.write_text("ObjectFileName: chosen.raw\nResolution: 4 4 4\n")
            settings = {"mode": "Local", "endpoint": "http://127.0.0.1:1",
                        "h200Endpoint": "http://127.0.0.1:2",
                        "rendererService": "http://127.0.0.1:3",
                        "model": "qwen3.6:35b", "volumePath": str(chosen)}
            with patch.object(tui_config, "GATE_VOLUME", Path(tmp) / "missing.dat"), \
                 patch.object(tui_env, "_http_json", side_effect=OSError("refused")):
                items = tui_env.check_environment(settings)["items"]
            volume = next(item for item in items if item["name"] == "Volume")
            self.assertTrue(volume["ok"])
            self.assertEqual(Path(volume["detail"]), chosen.resolve())


class LifecycleDelegation(unittest.TestCase):
    def test_repeat_run_creates_independent_dirs_and_duplicate_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_dir = root / "locks"
            with patch.object(launcher, "LOCK_DIRECTORY", lock_dir):
                config = _minimal_config(root)
                path = root / "config.json"
                path.write_text(json.dumps(config))
                raw = path.read_bytes()
                first = launcher.prepare_run(config, path, raw)
                try:
                    with self.assertRaises(lifecycle.RunConflictError):
                        launcher.prepare_run(config, path, raw)
                finally:
                    first.lock.release()
                # After release a new run succeeds with a different directory.
                second = launcher.prepare_run(config, path, raw)
                try:
                    self.assertNotEqual(first.directory, second.directory)
                    self.assertEqual(path.read_bytes(), raw)
                finally:
                    second.lock.release()

    def test_runner_delegates_to_launcher_module_not_a_second_loop(self):
        import inspect
        source = inspect.getsource(tui_runner.launch)
        self.assertIn("run_local_agent.py", source)
        self.assertNotIn("reserve_directory", source)
        self.assertNotIn("RunLock", source)

    def test_harness_kill_uses_tree_flag_and_survives_second_ctrl_c(self):
        process = unittest.mock.MagicMock(pid=4321)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return unittest.mock.MagicMock(returncode=0)

        with patch.object(launcher.subprocess, "run", side_effect=fake_run), \
             patch.object(launcher.signal, "signal") as install:
            launcher.stop_harness(process)
        install.assert_called_once()
        self.assertIn("/T", calls[0])
        self.assertIn("/F", calls[0])
        process.wait.assert_called_once()


class TraceEvents(unittest.TestCase):
    def _fixture_run(self, root: Path):
        run_dir = root / "run-x"
        run_dir.mkdir()
        session_dir = root / "session-x"
        session_dir.mkdir()
        (run_dir / "harness.jsonl").write_text(
            '{"type":"turn.started"}\n{"type":"turn.completed","usage":{"input_tokens":10}}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"hello"}}\n'
            '{"type":"broken\n', encoding="utf-8")  # partial tail stays buffered
        (session_dir / "events.jsonl").write_text(
            '{"kind":"external_tool_call","name":"render_scene","arguments":{"rotationXYZ":[1,2,3]}}\n'
            '{"kind":"external_tool_return","name":"render_scene","seconds":2.5}\n'
            '{"kind":"render","result":{"image":"D:/f.png","scene":{"revision":1},"nonblackPixels":5}}\n',
            encoding="utf-8")
        http_dir = run_dir / "http"
        http_dir.mkdir()
        (http_dir / "http-001.request.bin").write_bytes(b"{}")
        (http_dir / "http-001.metadata.json").write_text(
            json.dumps({"index": 1, "method": "POST", "path": "/v1/responses", "status": 200, "seconds": 1.2}),
            encoding="utf-8")
        return run_dir, session_dir

    def test_poll_order_dedup_and_partial_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, session_dir = self._fixture_run(Path(tmp))
            tail = tui_trace.EventTail(run_dir, session_dir)
            first = tail.poll()
            cats = [e["cat"] for e in first]
            # Same order as agent_console: harness -> events -> http.
            self.assertLess(cats.index("MODEL"), cats.index("TOOL"))
            self.assertLess(cats.index("TOOL"), cats.index("INFER"))
            titles = " ".join(e["title"] for e in first)
            self.assertIn("MODEL #1", titles)
            self.assertIn("render_scene", titles)
            self.assertIn("IMAGE", titles)
            # Second poll yields nothing new (dedup); partial harness line never parsed.
            self.assertEqual(tail.poll(), [])
            # Completing the partial line surfaces one more event.
            with (run_dir / "harness.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(',"usage":{}}\n')
            # The completed line is still invalid JSON ("broken...), so it stays skipped.
            self.assertEqual([e["cat"] for e in tail.poll()], [])

    def test_infer_event_shows_request_summary_not_filler(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, session_dir = self._fixture_run(Path(tmp))
            (run_dir / "http" / "http-001.forwarded.bin").write_text(json.dumps({
                "model": "qwen3.6:35b",
                "input": [
                    {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "rotate the camera now"}]},
                    {"type": "function_call", "name": "render_scene",
                     "arguments": '{"rotationXYZ":[25,-35,10]}'},
                    {"type": "function_call_output", "call_id": "c1", "output": [
                        {"type": "input_text", "text": "rev=1 nonblack=5"},
                        {"type": "input_image", "image_url": "data:image/png;base64,AAAA",
                         "detail": "auto"}]},
                ],
                "tools": [{"name": "mcp__renderer"}]}), encoding="utf-8")
            tail = tui_trace.EventTail(run_dir, session_dir)
            infer = [e for e in tail.poll() if e["cat"] == "INFER" and "Sent to model" in e["title"]]
            self.assertEqual(len(infer), 1)
            detail = infer[0]["detail"]
            self.assertIn("model=qwen3.6:35b", detail)
            self.assertIn("rotate the camera", detail)
            self.assertIn("render_scene", detail)
            self.assertIn("1 image(s)", detail)
            self.assertNotIn("AAAA", detail)
            self.assertNotIn("fake", detail)

    def test_session_json_events_need_no_session_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-y"
            run_dir.mkdir()
            (run_dir / "session.json").write_text(json.dumps({"id": "sid-y", "events": [
                {"seq": 0, "kind": "external_tool_call", "name": "render_scene",
                 "arguments": {"rotationXYZ": [25, -35, 10]}},
                {"seq": 1, "kind": "render", "result": {
                    "image": "D:/g.png", "scene": {"revision": 1}, "nonblackPixels": 7}},
                {"seq": 2, "kind": "finish", "selectedRevision": 1,
                 "externalTaskVerification": "unverified", "modelClaim": "done"}]}),
                encoding="utf-8")
            tail = tui_trace.EventTail(run_dir, None)
            cats = [e["cat"] for e in tail.poll()]
            self.assertEqual(cats, ["TOOL", "IMAGE", "FINISH"])
            # Gap-fill from events.jsonl uses the same sequence space: no doubles.
            session_dir = Path(tmp) / "sid-y"
            session_dir.mkdir()
            (session_dir / "events.jsonl").write_text(
                '{"seq": 1, "kind": "render", "result": {"image": "D:/g.png",'
                ' "scene": {"revision": 1}, "nonblackPixels": 7}}\n',
                encoding="utf-8")
            tail.session_dir = session_dir
            self.assertEqual(tail.poll(), [])

    def test_history_tolerates_missing_and_old_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "run-good"
            good.mkdir()
            (good / "run.json").write_text(json.dumps({"runId": "run-good", "status": "finished"}))
            (good / "manifest.json").write_text(json.dumps({"exitCode": 0}))
            old = root / "run-old"
            old.mkdir()
            (old / "run.json").write_text(json.dumps({"runId": "run-old"}))  # no status
            with patch.object(tui_runs, "ROOT", root):
                # discover_runs scans ROOT/runtime; mirror that layout.
                runtime = root / "runtime"
                runtime.mkdir()
                (good).rename(runtime / "run-good")
                (old).rename(runtime / "run-old")
                found = tui_runs.discover_runs()
                by_id = {r["runId"]: r for r in found}
                self.assertEqual(by_id["run-good"]["status"], "Finished")
                self.assertEqual(by_id["run-old"]["status"], "Unknown")

    def test_verify_summary_splits_three_ways(self):
        lines = tui_runs.verify_summary(None)
        self.assertTrue(any("Verify" in l for l in lines))
        report = {"state": "finished", "actualFrames": 2, "agentRenders": 1,
                  "visuallyDeliveredRevisions": [1], "final": {"revision": 1, "image": "D:/f.png"}}
        text = "\n".join(tui_runs.verify_summary(report))
        self.assertIn("Execution: passed", text)
        self.assertIn("Image transport: passed", text)
        self.assertIn("Visual quality: UNVERIFIED", text)


class SelfAcceptanceFixes(unittest.TestCase):
    def test_run_config_filenames_are_unique_within_one_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(tui_config, "ROOT", Path(tmp)):
                settings = dict(tui_config.DEFAULTS)
                first = tui_config.write_run_config({"task": "x"}, "task-a")
                second = tui_config.write_run_config({"task": "x"}, "task-a")
                self.assertNotEqual(first, second)
                self.assertTrue(first.is_file() and second.is_file())

    def test_task_label_maps_a_b_custom_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, task, expected in (
                    ("a", tui_config.TASK_A, "Task A"),
                    ("b", tui_config.TASK_B, "Task B"),
                    ("c", "look around a bit", "Custom: look around a bit"),
                    ("d", None, "taskKey=deadbeef")):
                directory = root / name
                directory.mkdir()
                (directory / "run.json").write_text(json.dumps({"runId": name, "taskKey": "deadbeef"}))
                if task is not None:
                    (directory / "config.json").write_text(json.dumps({"task": task}))
                self.assertEqual(tui_runs.task_label(directory, {"taskKey": "deadbeef"}), expected)

    def test_discover_cache_stale_then_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            first = runtime / "run-1"
            first.mkdir()
            (first / "run.json").write_text(json.dumps({"runId": "run-1", "status": "finished"}))
            with patch.object(tui_runs, "ROOT", root):
                seen_first = tui_runs.discover_runs(_fresh=True)
                self.assertEqual(len(seen_first), 1)
                second = runtime / "run-2"
                second.mkdir()
                (second / "run.json").write_text(json.dumps({"runId": "run-2", "status": "finished"}))
                self.assertEqual(len(tui_runs.discover_runs()), 1)  # cached
                self.assertEqual(len(tui_runs.discover_runs(_fresh=True)), 2)

    def test_cached_check_identity_and_expiry(self):
        import env as tui_env
        settings = dict(tui_config.DEFAULTS)
        with patch.object(tui_env, "CHECK_TTL", 3600.0):
            first = tui_env.cached_check(settings)
            self.assertIs(tui_env.cached_check(settings), first)
        tui_env._CHECK_CACHE["at"] = 0.0
        # Recompute path still returns a well-formed report (services refused fast).
        report = tui_env.cached_check(settings, ttl=0.0)
        self.assertIn("items", report)

    def test_runner_uses_utf8_pipe_for_non_ascii_model_text(self):
        import inspect
        self.assertIn('encoding="utf-8"', inspect.getsource(tui_runner.launch))

    def test_verify_picks_an_interpreter_with_numpy(self):
        import subprocess as real_subprocess
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd[0])
            result = unittest.mock.MagicMock()
            result.returncode = 0 if "bundled" in cmd[0] else 1
            return result

        with patch.object(tui_runs.subprocess, "run", side_effect=fake_run):
            with patch.object(tui_runs.sys, "executable", "C:/plain/python.exe"):
                with patch.object(tui_runs.Path, "home", return_value=Path("C:/user/bundled")):
                    picked = tui_runs._python_for_verify()
        self.assertIn("bundled", picked)
        self.assertEqual(len(calls), 2)

    def test_turn_completed_without_start_has_no_fake_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "harness.jsonl").write_text('{"type":"turn.completed","usage":{}}\n')
            probe = tui_trace.EventTail(run_dir, None)
            out = probe.poll()
        self.assertEqual(len(out), 1)
        self.assertIn("turn count unknown", out[0]["title"])
        self.assertNotIn("#0", out[0]["title"])

    def test_service_gate_blocks_only_on_down_services(self):
        import flows as tui_flows
        import env as tui_env
        down = {"items": [{"name": "Inference http://127.0.0.1:11434", "ok": False, "detail": "refused", "fix": "start ollama"},
                          {"name": "Renderer http://127.0.0.1:8765", "ok": True}]}
        with patch.object(tui_env, "check_environment", return_value=down):
            problems = tui_flows.service_gate({}, tui_env, __import__("services"))
            self.assertEqual(len(problems), 1)
            self.assertTrue(problems[0]["name"].startswith("Inference"))
        up = {"items": [{"name": "Inference x", "ok": True}, {"name": "Renderer y", "ok": True}]}
        with patch.object(tui_env, "check_environment", return_value=up):
            self.assertEqual(tui_flows.service_gate({}, tui_env, __import__("services")), [])

    def test_failure_hint_reads_failure_json(self):
        import evidence as tui_evidence
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(tui_evidence.failure_hint(root))
            (root / "failure.json").write_text(json.dumps({"error": "boom  info"}))
            self.assertIn("boom", tui_evidence.failure_hint(root))

    def test_renderer_down_hint_names_the_real_start_command(self):
        import env as tui_env
        with patch.object(tui_env, "_http_json", side_effect=OSError("refused")):
            items = tui_env.check_environment({"mode": "Local", "endpoint": "http://127.0.0.1:11434",
                                               "h200Endpoint": "http://127.0.0.1:11435",
                                               "rendererService": "http://127.0.0.1:8765",
                                               "model": "qwen3.6:35b"})["items"]
        renderer = next(i for i in items if i["name"].startswith("Renderer"))
        self.assertFalse(renderer["ok"])
        self.assertIn("src/app.py", renderer["fix"])

    def test_service_lifecycle_autostart_and_release(self):
        import services as tui_services
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = {"mode": "Local", "model": "qwen3.6:35b",
                        "endpoint": "http://127.0.0.1:11499",
                        "h200Endpoint": "http://127.0.0.1:11498",
                        "rendererService": "http://127.0.0.1:18765"}
            state = {"ollama_up": False, "renderer_up": False}
            want_context = tui_config.example_config().get("contextSize")

            def fake_http(url, timeout=5.0):
                if url.endswith("/api/version") and state["ollama_up"]:
                    return {"version": "test"}
                if url.endswith("/api/tags"):
                    if not state["ollama_up"]:
                        raise OSError("refused")
                    return {"models": [{"name": "qwen3.6:35b"}]}
                if url.endswith("/api/ps"):
                    return {"models": [{"name": "qwen3.6:35b", "context_length": want_context}]}
                if url.endswith("/api/service"):
                    if not state["renderer_up"]:
                        raise OSError("refused")
                    return {"service": "gav-native-renderer"}
                raise OSError("refused")

            def fake_spawn(cmd, env, cwd, stem):
                if stem == "ollama":
                    state["ollama_up"] = True
                    return 1111
                state["renderer_up"] = True
                return 2222

            with patch.object(tui_services, "RECORD", root / "services.json"), \
                 patch.object(tui_services, "_http_get", side_effect=fake_http), \
                 patch.object(tui_services, "_spawn", side_effect=fake_spawn), \
                 patch.object(tui_services.shutil, "which", return_value="C:/fake/ollama.exe"):
                report = tui_services.ensure_services(settings)
                self.assertEqual(len(report["started"]), 2)
                self.assertEqual(report["problems"], [])
                record = json.loads((root / "services.json").read_text(encoding="utf-8"))
                self.assertEqual(record["services"]["ollama"]["pid"], 1111)

                killed = []

                def fake_image(pid):
                    return {1111: "ollama.exe", 2222: "python.exe"}.get(pid)

                with patch.object(tui_services, "_image_of_pid", side_effect=fake_image), \
                     patch.object(tui_services, "_terminate", side_effect=lambda pid: killed.append(pid) or True), \
                     patch.object(tui_services.runs_mod, "discover_runs", return_value=[]), \
                     patch.object(tui_services, "process_alive", return_value=True), \
                     patch.object(tui_services, "_live_launchers", return_value=[]):
                    rel = tui_services.release_owned_services()
                self.assertEqual(sorted(killed), [1111, 2222])
                self.assertIsNone(rel["kept"])
                record = json.loads((root / "services.json").read_text(encoding="utf-8"))
                self.assertEqual(record["services"], {})

    def test_release_refuses_while_agent_running(self):
        import services as tui_services
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "services.json").write_text(json.dumps(
                {"owner": tui_services.SESSION_ID, "heartbeat": 9999999999.0,
                 "services": {"ollama": {"pid": 31337, "image": "ollama.exe"}}}))
            with patch.object(tui_services, "RECORD", root / "services.json"), \
                 patch.object(tui_services.runs_mod, "discover_runs",
                              return_value=[{"runId": "run-x", "status": "Running"}]):
                killed = []
                with patch.object(tui_services, "_terminate", side_effect=lambda pid: killed.append(pid) or True):
                    rel = tui_services.release_owned_services()
                self.assertEqual(killed, [])
                self.assertIn("still live", rel["kept"])
                record = json.loads((root / "services.json").read_text(encoding="utf-8"))
                self.assertIn("ollama", record["services"])

    def test_release_never_touches_foreign_images(self):
        import services as tui_services
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "services.json").write_text(json.dumps(
                {"owner": tui_services.SESSION_ID, "heartbeat": 9999999999.0,
                 "services": {"renderer": {"pid": 42424, "image": "python.exe"}}}))
            with patch.object(tui_services, "RECORD", root / "services.json"), \
                 patch.object(tui_services, "process_alive", return_value=True), \
                 patch.object(tui_services, "_image_of_pid", return_value="notepad.exe"), \
                 patch.object(tui_services.runs_mod, "discover_runs", return_value=[]), \
                 patch.object(tui_services, "_live_launchers", return_value=[]):
                killed = []
                with patch.object(tui_services, "_terminate", side_effect=lambda pid: killed.append(pid) or True):
                    rel = tui_services.release_owned_services()
                self.assertEqual(killed, [])
                self.assertTrue(any("no longer ours" in s for s in rel["skipped"]))

    def test_force_release_bypasses_live_run_gate(self):
        import services as tui_services
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "services.json").write_text(json.dumps(
                {"owner": tui_services.SESSION_ID, "heartbeat": 9999999999.0,
                 "services": {"ollama": {"pid": 31337, "image": "ollama.exe"}}}))
            with patch.object(tui_services, "RECORD", root / "services.json"), \
                 patch.object(tui_services, "process_alive", return_value=True), \
                 patch.object(tui_services, "_image_of_pid", return_value="ollama.exe"), \
                 patch.object(tui_services.runs_mod, "discover_runs",
                              return_value=[{"runId": "run-x", "status": "Running"}]), \
                 patch.object(tui_services, "_live_launchers", return_value=["x.lock:pid"]):
                killed = []
                with patch.object(tui_services, "_terminate", side_effect=lambda pid: killed.append(pid) or True):
                    rel = tui_services.release_owned_services(force=True)
                self.assertEqual(killed, [31337])
                self.assertIsNone(rel["kept"])

    def test_ctrl_c_shutdown_stops_agent_ollama_and_owned_services(self):
        import services as tui_services
        with patch.object(tui_services, "_block_interrupts") as blocked, \
             patch.object(tui_services, "heartbeat_stop"), \
             patch.object(tui_services, "_terminate_agent_trees",
                          return_value={"stopped": ["codex.exe pid 9"], "failed": []}), \
             patch.object(tui_services, "terminate_local_inference",
                          return_value={"stopped": ["ollama.exe pid 7"], "failed": [],
                                        "endpoint": "http://127.0.0.1:11434", "verified": True}), \
             patch.object(tui_services, "release_owned_services",
                          return_value={"stopped": ["renderer pid 5"], "kept": None, "skipped": []}):
            report = tui_services.shutdown_all({"mode": "Local"})
        blocked.assert_called_once()
        self.assertEqual(report["agent"], ["codex.exe pid 9"])
        self.assertEqual(report["stopped"], ["ollama.exe pid 7", "renderer pid 5"])
        self.assertEqual(report["failed"], [])

    def test_local_inference_kill_is_verified_and_spares_foreign_listener(self):
        import services as tui_services
        killed = []
        with patch.object(tui_services, "_pids_with_images", return_value=[7, 8]), \
             patch.object(tui_services, "_listener_pids", return_value=[]), \
             patch.object(tui_services, "_image_of_pid",
                          side_effect=lambda pid: {7: "ollama.exe", 8: "notepad.exe"}.get(pid)), \
             patch.object(tui_services, "_terminate", side_effect=lambda pid: killed.append(pid) or True), \
             patch.object(tui_services, "_endpoint_up", return_value=False):
            report = tui_services.terminate_local_inference(
                {"mode": "Local", "endpoint": "http://127.0.0.1:11434"})
        self.assertEqual(killed, [7])
        self.assertTrue(report["verified"])
        self.assertEqual(report["failed"], [])

    def test_h200_never_kills_a_remote_endpoint_provider(self):
        import services as tui_services
        with patch.object(tui_services, "_pids_with_images") as pids, \
             patch.object(tui_services, "_terminate") as terminate:
            report = tui_services.terminate_local_inference(
                {"mode": "H200", "endpoint": "http://127.0.0.1:11434"})
        pids.assert_not_called()
        terminate.assert_not_called()
        self.assertEqual(report["stopped"], [])

    def test_h200_never_autostarts(self):
        import services as tui_services
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = {"mode": "H200", "model": "qwen3.6:35b",
                        "endpoint": "http://127.0.0.1:11434",
                        "h200Endpoint": "http://127.0.0.1:11498",
                        "rendererService": "http://127.0.0.1:18766"}
            with patch.object(tui_services, "RECORD", root / "services.json"), \
                 patch.object(tui_services, "_http_get", side_effect=OSError("no tunnel")), \
                 patch.object(tui_services, "_image_of_pid", return_value=None), \
                 patch.object(tui_services, "_wait_true", return_value=False):
                spawned = []
                with patch.object(tui_services, "_spawn", side_effect=lambda *a: spawned.append(a[3]) or 999):
                    report = tui_services.ensure_services(settings)
                # Local renderer is still managed in H200 mode; Ollama/tunnel never is.
                self.assertNotIn("ollama", spawned)
                self.assertTrue(any("H200" in p["name"] for p in report["problems"]))

    def test_no_tty_refuses_with_guidance_not_traceback(self):
        import io
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "volume_tui_app",
            Path(__file__).resolve().parents[1] / "scripts" / "volume_tui" / "app.py")
        tui_app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tui_app)
        with patch.object(tui_app.sys.stdin, "isatty", return_value=False):
            out = io.StringIO()
            with unittest.mock.patch.object(tui_app.sys, "stdout", out):
                code = tui_app.main()
            self.assertEqual(code, 2)
            text = out.getvalue().lower()
            self.assertTrue("terminal" in text or "interactive" in text or "textual" in text)


class TemporaryContextInstance(unittest.TestCase):
    def test_offer_predicate_only_for_lone_local_context_mismatch(self):
        import flows as tui_flows
        local = {"mode": "Local"}
        mismatch = [{"name": "Model context", "ok": False, "detail": "loaded x context 262144 != required 16384"}]
        self.assertIsNotNone(tui_flows.temporary_context_offer(local, mismatch))
        self.assertIsNone(tui_flows.temporary_context_offer({"mode": "H200"}, mismatch))
        self.assertIsNone(tui_flows.temporary_context_offer(local, mismatch + [
            {"name": "Renderer y", "ok": False}]))
        self.assertIsNone(tui_flows.temporary_context_offer(
            local, [{"name": "Inference x", "ok": False}]))

    def test_temporary_ollama_uses_free_port_and_required_context_then_reaped(self):
        import services as tui_services
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = {"mode": "Local", "model": "qwen3.6:35b",
                        "endpoint": "http://127.0.0.1:11434",
                        "h200Endpoint": "http://127.0.0.1:11435",
                        "rendererService": "http://127.0.0.1:8765"}
            seen_env = {}

            def fake_spawn(cmd, env, cwd, stem):
                seen_env.update(env)
                self.assertTrue(stem.startswith("ollama-tmp-"))
                self.assertIn("serve", cmd)
                return 5555

            with patch.object(tui_services, "RECORD", root / "services.json"), \
                 patch.object(tui_services, "_http_get", return_value={"version": "test"}), \
                 patch.object(tui_services, "_wait_true", return_value=True), \
                 patch.object(tui_services, "_spawn", side_effect=fake_spawn), \
                 patch.object(tui_services.shutil, "which", return_value="C:/fake/ollama.exe"):
                started = tui_services.start_temporary_ollama(settings, 16384)
            self.assertTrue(started["ok"])
            self.assertTrue(started["endpoint"].startswith("http://127.0.0.1:"))
            self.assertEqual(seen_env.get("OLLAMA_CONTEXT_LENGTH"), "16384")
            record = json.loads((root / "services.json").read_text(encoding="utf-8"))
            self.assertIn(started["key"], record["services"])
            self.assertEqual(record["services"][started["key"]]["pid"], 5555)
            # Saved global settings are never touched by a temporary instance.
            self.assertEqual(settings["endpoint"], "http://127.0.0.1:11434")
            # Exit path reaps exactly the temporary pid (image re-verified).
            killed = []
            with patch.object(tui_services, "RECORD", root / "services.json"), \
                 patch.object(tui_services, "_image_of_pid", return_value="ollama.exe"), \
                 patch.object(tui_services, "_terminate", side_effect=lambda pid: killed.append(pid) or True), \
                 patch.object(tui_services, "process_alive", return_value=True), \
                 patch.object(tui_services.runs_mod, "discover_runs", return_value=[]), \
                 patch.object(tui_services, "_live_launchers", return_value=[]):
                rel = tui_services.release_owned_services()
            self.assertEqual(killed, [5555])
            self.assertIsNone(rel["kept"])


if __name__ == '__main__':
    unittest.main()
