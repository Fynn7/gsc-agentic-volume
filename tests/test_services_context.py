"""The service gate must require the context the settings actually ask for."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "volume_tui"))

import services


class ModelContextGate(unittest.TestCase):
    def _problems(self, loaded: int, required: int):
        def fake_get(url, timeout):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "qwen3.6:35b"}]}
            if url.endswith("/api/ps"):
                return {"models": [{"name": "qwen3.6:35b", "context_length": loaded}]}
            return {}

        settings = {
            "mode": "Local",
            "endpoint": "http://127.0.0.1:11434",
            "model": "qwen3.6:35b",
            "contextSize": required,
        }
        with mock.patch.object(services, "_loopback", lambda _endpoint: ("127.0.0.1", 11434)), \
                mock.patch.object(services, "_http_get", fake_get):
            return services.model_problems(settings)

    def test_the_configured_context_is_the_requirement(self):
        self.assertEqual(self._problems(loaded=32768, required=32768), [])

    def test_a_loaded_context_that_differs_is_a_problem(self):
        problems = self._problems(loaded=262144, required=16384)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["name"], "Model context")
        self.assertIn("262144", problems[0]["detail"])
        self.assertIn("16384", problems[0]["detail"])


if __name__ == "__main__":
    unittest.main()
