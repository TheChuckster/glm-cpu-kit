#!/usr/bin/env python3
"""Regression tests for kimi-opencode's fail-closed readiness check."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import textwrap
import unittest


LAUNCHER = Path(__file__).with_name("kimi-opencode.sh")


class KimiOpenCodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

        self._write_executable(
            "curl",
            r"""
            #!/usr/bin/env bash
            printf '%s\n' '{"data":[{"id":"kimi-k3"}]}'
            """,
        )
        self._write_executable(
            "opencode",
            r"""
            #!/usr/bin/env bash
            printf 'opencode-argv:'
            printf ' <%s>' "$@"
            printf '\n'
            """,
        )
        self._write_executable(
            "sleep",
            r"""
            #!/usr/bin/env bash
            exit 0
            """,
        )
        self._write_executable(
            "ssh",
            r"""
            #!/usr/bin/env bash
            count=0
            if [[ -f "$MOCK_SSH_COUNT" ]]; then
              read -r count < "$MOCK_SSH_COUNT"
            fi
            count=$((count + 1))
            printf '%s\n' "$count" > "$MOCK_SSH_COUNT"

            case "$MOCK_SSH_STATUS" in
              loading-ready)
                if (( count == 1 )); then
                  cat <<'EOF'
            selected variant : kimi-k3-q5attn-abl-v26  (kimi-k3)
            model directory  : /models/kimi
            engine           : /engine/llama-server
            service          : active
            health           : not responding (still loading, or stopped)
            EOF
                else
                  cat <<'EOF'
            selected variant : kimi-k3-q5attn-abl-v26  (kimi-k3)
            model directory  : /models/kimi
            engine           : /engine/llama-server
            service          : active
            health           : {"status":"ok","slots_idle":1,"slots_processing":0}
            serving alias    : kimi-k3
            EOF
                fi
                ;;
              healthy-no-alias)
                cat <<'EOF'
            selected variant : kimi-k3-q5attn-abl-v26  (kimi-k3)
            model directory  : /models/kimi
            engine           : /engine/llama-server
            service          : active
            health           : {"status":"ok","slots_idle":1,"slots_processing":0}
            EOF
                ;;
              wrong-variant)
                cat <<'EOF'
            selected variant : glm52-q4km  (glm-5.2)
            model directory  : /models/glm
            engine           : /engine/llama-server
            service          : active
            health           : {"status":"ok","slots_idle":1,"slots_processing":0}
            serving alias    : glm-5.2
            EOF
                ;;
              *)
                cat <<'EOF'
            selected variant : kimi-k3-q5attn-abl-v26  (kimi-k3)
            model directory  : /models/kimi
            engine           : /engine/llama-server
            service          : active
            health           : {"status":"ok","slots_idle":1,"slots_processing":0}
            serving alias    : kimi-k3
            EOF
                ;;
            esac
            """,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_executable(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _run(self, status: str = "ready") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin}:{env['PATH']}",
                "HOME": str(self.tmp),
                "OPENCODE_BIN": str(self.bin / "opencode"),
                "MOCK_SSH_COUNT": str(self.tmp / "ssh-count"),
                "MOCK_SSH_STATUS": status,
                "KIMI_READY_TIMEOUT": "3",
                "KIMI_READY_POLL": "1",
            }
        )
        return subprocess.run(
            [str(LAUNCHER), "-s", "ses_test"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_ready_server_launches_with_session_unchanged(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "opencode-argv: <--model> <local/kimi-k3> <-s> <ses_test>",
            result.stdout,
        )

    def test_loading_server_is_retried_then_launched(self) -> None:
        result = self._run("loading-ready")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("is still loading; waiting up to 3s", result.stderr)
        self.assertEqual((self.tmp / "ssh-count").read_text().strip(), "2")

    def test_healthy_server_without_alias_fails_closed(self) -> None:
        result = self._run("healthy-no-alias")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("healthy but its serving alias could not be verified", result.stderr)
        self.assertNotIn("opencode-argv:", result.stdout)

    def test_wrong_variant_fails_closed(self) -> None:
        result = self._run("wrong-variant")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("selected 'glm52-q4km'", result.stderr)
        self.assertNotIn("opencode-argv:", result.stdout)


if __name__ == "__main__":
    unittest.main()
