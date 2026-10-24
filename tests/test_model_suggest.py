"""Model-name completion in Settings: local Ollama tags -> Tab suggestion."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "volume_tui"))

import services as tui_services


def _load_tui_app():
    import importlib.util
    path = ROOT / "scripts" / "volume_tui" / "app.py"
    spec = importlib.util.spec_from_file_location("volume_tui_app_suggest", path)
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


class ListModels(unittest.TestCase):
    def test_sorted_deduped_names(self):
        payload = {"models": [{"name": "b:7b"}, {"name": "a:8b"}, {"name": "b:7b"},
                              {"nope": 1}, "junk"]}
        with patch.object(tui_services, "_http_get", return_value=payload):
            self.assertEqual(tui_services.list_models("http://127.0.0.1:11434"),
                             ["a:8b", "b:7b"])

    def test_non_loopback_never_probed(self):
        with patch.object(tui_services, "_http_get",
                          side_effect=AssertionError("must not dial out")):
            self.assertEqual(tui_services.list_models("http://example.com:11434"), [])

    def test_unreachable_or_junk_means_empty(self):
        with patch.object(tui_services, "_http_get", side_effect=OSError("down")):
            self.assertEqual(tui_services.list_models("http://127.0.0.1:11434"), [])
        with patch.object(tui_services, "_http_get", return_value={"nope": []}):
            self.assertEqual(tui_services.list_models("http://127.0.0.1:11434"), [])
        with patch.object(tui_services, "_http_get", return_value=["junk"]):
            self.assertEqual(tui_services.list_models("http://127.0.0.1:11434"), [])


class ModelSuggester(unittest.TestCase):
    def test_prefix_then_substring_case_insensitive(self):
        suggester = tui_app._ModelSuggester(["qwen3.6:35b", "llama3.1:8b"])
        self.assertEqual(asyncio.run(suggester.get_suggestion("qwen")), "qwen3.6:35b")
        self.assertEqual(asyncio.run(suggester.get_suggestion("QWEN")), "qwen3.6:35b")
        self.assertEqual(asyncio.run(suggester.get_suggestion("35b")), "qwen3.6:35b")
        self.assertIsNone(asyncio.run(suggester.get_suggestion("")))
        self.assertIsNone(asyncio.run(suggester.get_suggestion("zzz")))

    def test_update_replaces_and_dedupes(self):
        suggester = tui_app._ModelSuggester(["b", "a", "b", " ", ""])
        self.assertEqual(suggester.suggestions, ["a", "b"])
        suggester.update(["z:1"])
        self.assertEqual(asyncio.run(suggester.get_suggestion("q")), None)
        self.assertEqual(asyncio.run(suggester.get_suggestion("z")), "z:1")


@unittest.skipUnless(HAS_TEXTUAL, "Textual not installed")
class SettingsCompletion(unittest.TestCase):
    def test_refresh_lists_local_models_for_tab(self):
        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                with patch.object(tui_services, "list_models",
                                  return_value=["qwen3.6:35b", "llama3.1:8b"]):
                    app._open_settings()
                    await pilot.pause()
                    screen = app.screen
                    self.assertIsInstance(screen, tui_app.SettingsScreen)
                    # Auto-read on open (worker): the hint lists this machine's models.
                    import time
                    deadline = time.time() + 20
                    hint = ""
                    while time.time() < deadline:
                        await pilot.pause()
                        await asyncio.sleep(0.05)
                        hint = str(screen.query_one("#model-hint").render())
                        if "qwen3.6:35b" in hint:
                            break
                    self.assertIn("qwen3.6:35b", hint)
                    self.assertIn("llama3.1:8b", hint)
                    # Typing completes from the list (Textual shows it greyed).
                    model = screen.query_one("#set-model", tui_app.Input)
                    model.value = "qwen"
                    await pilot.pause()
                    self.assertEqual(
                        await screen._model_suggester.get_suggestion(model.value),
                        "qwen3.6:35b")
        asyncio.run(_run())

    def test_refresh_button_re_reads_after_endpoint_edit(self):
        async def _run():
            app = tui_app.VolumeAgentApp()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                with patch.object(tui_services, "list_models", return_value=[]):
                    app._open_settings()
                    await pilot.pause()
                    screen = app.screen
                    screen.query_one("#set-endpoint", tui_app.Input).value = \
                        "http://127.0.0.1:1"
                    await pilot.pause()
                    self.assertIn("Refresh models",
                                  str(screen.query_one("#model-hint").render()))
                with patch.object(tui_services, "list_models",
                                  return_value=["edited:1b"]):
                    screen.query_one("#btn-models-refresh", tui_app.Button).press()
                    import time
                    deadline = time.time() + 20
                    hint = ""
                    while time.time() < deadline:
                        await pilot.pause()
                        await asyncio.sleep(0.05)
                        hint = str(screen.query_one("#model-hint").render())
                        if "edited:1b" in hint:
                            break
                    self.assertIn("edited:1b", hint)
        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
