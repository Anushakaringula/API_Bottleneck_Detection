"""Default data paths resolve under <repo>/data and can be overridden by environment variables."""
import importlib
import os
import unittest
from pathlib import Path
from unittest import mock

import experiment_runner.runner as runner

REPO_ROOT = Path(__file__).resolve().parents[3]


class PathDefaultTests(unittest.TestCase):
    def reload(self, env):
        with mock.patch.dict(os.environ, env, clear=False):
            for key in ("ABR_DATA_DIR", "ABR_EXPERIMENTS_DIR", "ABR_SPAN_FILE"):
                if key not in env:
                    os.environ.pop(key, None)
            module = importlib.reload(runner)
            args = module.build_parser().parse_args(["--config", "unused.json"])
        importlib.reload(runner)
        return module, args

    def test_repo_root_and_defaults(self):
        module, args = self.reload({})
        self.assertEqual(module.ROOT, REPO_ROOT)
        self.assertEqual(Path(args.output_dir), REPO_ROOT / "data" / "raw" / "experiments")
        self.assertEqual(Path(args.span_file), REPO_ROOT / "data" / "raw" / "spans" / "otel-spans.jsonl")

    def test_data_dir_override(self):
        _, args = self.reload({"ABR_DATA_DIR": "/tmp/abr-test-data"})
        self.assertEqual(Path(args.output_dir), Path("/tmp/abr-test-data/raw/experiments"))
        self.assertEqual(Path(args.span_file), Path("/tmp/abr-test-data/raw/spans/otel-spans.jsonl"))

    def test_specific_overrides_win(self):
        _, args = self.reload({"ABR_DATA_DIR": "/tmp/a", "ABR_EXPERIMENTS_DIR": "/tmp/b", "ABR_SPAN_FILE": "/tmp/c.jsonl"})
        self.assertEqual(args.output_dir, "/tmp/b")
        self.assertEqual(args.span_file, "/tmp/c.jsonl")


if __name__ == "__main__":
    unittest.main()
