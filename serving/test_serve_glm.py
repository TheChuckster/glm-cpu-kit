#!/usr/bin/env python3
"""Regression tests for serve-glm's hash-bound variant prefill expansion."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("serve-glm.sh")


class ServeGlmPrefillTests(unittest.TestCase):
    def fixture(self, directory: Path, *, expected_hash: str | None = None):
        model_dir = directory / "model"
        model_dir.mkdir()
        (model_dir / "Model.gguf").write_bytes(b"fixture")

        capture = directory / "argv.json"
        engine = directory / "fake-llama-server"
        engine.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['CAPTURE'], 'x', encoding='utf-8') as f:\n"
            "    json.dump(sys.argv[1:], f)\n"
        )
        engine.chmod(0o755)

        api_key = directory / "api-key"
        api_key.write_text("test-key\n")
        prefill = directory / "prefill.txt"
        prefill_text = "Exact user's prefill with punctuation."
        prefill.write_text(prefill_text + "\n")
        digest = expected_hash or hashlib.sha256(prefill.read_bytes()).hexdigest()

        opts = (
            "--reasoning-format deepseek "
            "--chat-template-kwargs '{\"thinking_effort\": \"low\"}' "
            "--dry-sequence-breaker '\"*' "
            f"--kit-reasoning-prefill-file {prefill} "
            f"--kit-reasoning-prefill-sha256 {digest}"
        )
        registry = directory / "variants.conf"
        registry.write_text(
            f"test|local:test||Model|1|test-alias|{model_dir}|{engine}|{opts}\n"
        )
        env = os.environ.copy()
        env.update(
            {
                "API_KEY_FILE": str(api_key),
                "CAPTURE": str(capture),
                "CTX": "1024",
                "GLM_VARIANT": "test",
                "MLOCK_MIN_HEADROOM_GB": "0",
                "NUMA_POLICY": "test",
                "THREADS": "1",
                "VARIANTS": str(registry),
            }
        )
        return env, capture, prefill, prefill_text, registry, opts

    def test_expands_exact_prefill_and_consumes_pseudo_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            env, capture, _, prefill_text, _, _ = self.fixture(Path(temporary))
            completed = subprocess.run(
                [SCRIPT], env=env, text=True, capture_output=True, check=True
            )
            argv = json.loads(capture.read_text())

        self.assertIn("serving variant 'test' as 'test-alias'", completed.stdout)
        self.assertNotIn("--kit-reasoning-prefill-file", argv)
        self.assertNotIn("--kit-reasoning-prefill-sha256", argv)
        self.assertEqual(argv[argv.index("--reasoning-prefill") + 1], prefill_text)
        self.assertEqual(argv[argv.index("--dry-sequence-breaker") + 1], '\"*')
        self.assertEqual(
            argv[argv.index("--chat-template-kwargs") + 1],
            '{"thinking_effort": "low"}',
        )
        self.assertEqual(argv[-2:], ["--reasoning-prefill", prefill_text])

    def test_rejects_prefill_hash_mismatch_before_engine_exec(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env, capture, _, _, _, _ = self.fixture(root, expected_hash="0" * 64)
            completed = subprocess.run(
                [SCRIPT], env=env, text=True, capture_output=True, check=False
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("reasoning-prefill SHA-256 mismatch", completed.stderr)
            self.assertFalse(capture.exists())

    def test_rejects_unpaired_prefill_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env, capture, _, _, registry, opts = self.fixture(root)
            registry.write_text(
                registry.read_text().replace(
                    " --kit-reasoning-prefill-sha256 " + opts.rsplit(" ", 1)[-1], ""
                )
            )
            completed = subprocess.run(
                [SCRIPT], env=env, text=True, capture_output=True, check=False
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("file and SHA-256 pseudo-options must be paired", completed.stderr)
            self.assertFalse(capture.exists())


if __name__ == "__main__":
    unittest.main()
