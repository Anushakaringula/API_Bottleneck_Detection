"""Default data paths resolve under <repo>/data and can be overridden by environment variables."""
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import telemetry_analysis.analyzer as analyzer

REPO_ROOT = Path(__file__).resolve().parents[3]
KEYS = ("ABR_DATA_DIR", "ABR_EXPERIMENTS_DIR", "ABR_SPAN_FILE", "ABR_ANALYSIS_DIR")


class PathDefaultTests(unittest.TestCase):
    def parsed(self, env):
        captured = {}

        def fake_analyze(experiments_dir, span_file, output_root):
            captured.update(experiments_dir=experiments_dir, span_file=span_file, output_root=output_root)
            return output_root

        with mock.patch.dict(os.environ, env, clear=False):
            for key in KEYS:
                if key not in env:
                    os.environ.pop(key, None)
            module = importlib.reload(analyzer)
            with mock.patch.object(module, "analyze", fake_analyze), mock.patch.object(sys, "argv", ["analyzer"]), \
                    mock.patch("builtins.print"):
                module.main()
        importlib.reload(analyzer)
        return captured

    def test_defaults_are_anchored_to_repo_root(self):
        got = self.parsed({})
        self.assertEqual(got["experiments_dir"], REPO_ROOT / "data" / "raw" / "experiments")
        self.assertEqual(got["span_file"], REPO_ROOT / "data" / "raw" / "spans" / "otel-spans.jsonl")
        self.assertEqual(got["output_root"], REPO_ROOT / "data" / "processed")

    def test_data_dir_override(self):
        got = self.parsed({"ABR_DATA_DIR": "/tmp/abr-test-data"})
        self.assertEqual(got["experiments_dir"], Path("/tmp/abr-test-data/raw/experiments"))
        self.assertEqual(got["output_root"], Path("/tmp/abr-test-data/processed"))

    def test_specific_overrides_win(self):
        got = self.parsed({"ABR_DATA_DIR": "/tmp/a", "ABR_EXPERIMENTS_DIR": "/tmp/b", "ABR_SPAN_FILE": "/tmp/c.jsonl", "ABR_ANALYSIS_DIR": "/tmp/d"})
        self.assertEqual(got, {"experiments_dir": Path("/tmp/b"), "span_file": Path("/tmp/c.jsonl"), "output_root": Path("/tmp/d")})


if __name__ == "__main__":
    unittest.main()
