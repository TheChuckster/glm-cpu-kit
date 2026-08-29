#!/usr/bin/env python3
"""Regression tests for the Together GLM-5.3 OpenCode wrapper."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import textwrap
import unittest


HERE = Path(__file__).resolve().parent
LAUNCHER = HERE / "glm53-opencode-together.sh"
CONFIG = HERE / "together-opencode.json"


class TogetherGlm53Test(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "opencode"
        self.bin.write_text(
            textwrap.dedent(
                r"""
                #!/usr/bin/env bash
                printf 'argv:'
                printf ' <%s>' "$@"
                printf '\ninline=%s\ncap=%s\n' \
                  "$OPENCODE_CONFIG_CONTENT" \
                  "$OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"
                """
            ).lstrip(),
            encoding="utf-8",
        )
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IXUSR)
        (self.tmp / ".together-key").write_text("test-key\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, effort: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.tmp),
                "OPENCODE_BIN": str(self.bin),
                "TOGETHER_OPENCODE_XDG": str(self.tmp / "config"),
            }
        )
        env.pop("TOGETHER_API_KEY", None)
        env.pop("TOGETHER_VARIANT", None)
        env.pop("GLM53_REASONING_EFFORT", None)
        if effort is not None:
            env["GLM53_REASONING_EFFORT"] = effort
        return subprocess.run(
            [str(LAUNCHER), "run", "hello"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_catalog_entry_matches_live_flagship(self) -> None:
        entry = json.loads(CONFIG.read_text(encoding="utf-8"))["provider"][
            "togetherai"
        ]["models"]["zai-org/GLM-5.3"]
        self.assertEqual(entry["limit"]["context"], 1_048_575)
        self.assertEqual(entry["cost"], {"input": 1.4, "output": 4.4, "cache_read": 0.26})
        self.assertEqual(set(entry["variants"]), {"low", "high", "max"})

    def test_defaults_to_glm53_max_and_large_output_cap(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<--model> <togetherai/zai-org/GLM-5.3>", result.stdout)
        self.assertIn('"reasoningEffort":"max"', result.stdout)
        self.assertIn("cap=131072", result.stdout)

    def test_low_effort_override_reaches_inline_config(self) -> None:
        result = self._run("low")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"reasoningEffort":"low"', result.stdout)

    def test_unknown_effort_fails_before_opencode(self) -> None:
        result = self._run("xhigh")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unsupported Together reasoning variant", result.stderr)
        self.assertNotIn("argv:", result.stdout)


if __name__ == "__main__":
    unittest.main()
