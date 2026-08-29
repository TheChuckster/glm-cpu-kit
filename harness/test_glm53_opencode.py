#!/usr/bin/env python3
"""Regression tests for the GLM-5.3 launcher's resident-model gate."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import textwrap
import unittest


LAUNCHER = Path(__file__).with_name("glm53-opencode.sh")


class Glm53OpenCodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        config = self.tmp / ".glm-opencode-config" / "opencode" / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text("{}\n", encoding="utf-8")
        (self.tmp / ".glm-api-key").write_text("test-key\n", encoding="utf-8")

        self._write_executable(
            "opencode",
            r"""
            #!/usr/bin/env bash
            printf 'opencode-argv:'
            printf ' <%s>' "$@"
            printf '\ninline=%s\ndirect-base=%s\n' \
              "$OPENCODE_CONFIG_CONTENT" "$GLM53_DIRECT_BASE_URL"
            """,
        )
        self._write_executable("sleep", "#!/usr/bin/env bash\nexit 0\n")
        self._write_executable(
            "curl",
            r"""
            #!/usr/bin/env bash
            if [[ "${MOCK_CURL_FAIL:-0}" == 1 ]]; then exit 7; fi
            printf '{"data":[{"id":"%s"}]}\n' "${MOCK_DIRECT_ALIAS:-glm-5.3}"
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
            selected variant : glm53-q4xl  (glm-5.3)
            model directory  : /models/glm53
            engine           : /engine/llama-server
            service          : activating
            health           : not responding (still loading, or stopped)
            EOF
                else
                  cat <<'EOF'
            selected variant : glm53-q4xl  (glm-5.3)
            model directory  : /models/glm53
            engine           : /engine/llama-server
            service          : active
            health           : {"status":"ok"}
            serving alias    : glm-5.3
            EOF
                fi
                ;;
              healthy-no-alias)
                cat <<'EOF'
            selected variant : glm53-q4xl  (glm-5.3)
            model directory  : /models/glm53
            engine           : /engine/llama-server
            service          : active
            health           : {"status":"ok"}
            EOF
                ;;
              wrong-variant)
                cat <<'EOF'
            selected variant : kimi-k3-q5attn-abl-v26  (kimi-k3)
            model directory  : /models/kimi
            engine           : /engine/llama-server
            service          : active
            health           : {"status":"ok"}
            serving alias    : kimi-k3
            EOF
                ;;
              *)
                cat <<'EOF'
            selected variant : glm53-q4xl  (glm-5.3)
            model directory  : /models/glm53
            engine           : /engine/llama-server
            service          : active
            health           : {"status":"ok"}
            serving alias    : glm-5.3
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

    def _run(
        self,
        status: str = "ready",
        direct_alias: str = "glm-5.3",
        curl_fail: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin}:{env['PATH']}",
                "HOME": str(self.tmp),
                "OPENCODE_BIN": str(self.bin / "opencode"),
                "MOCK_SSH_COUNT": str(self.tmp / "ssh-count"),
                "MOCK_SSH_STATUS": status,
                "MOCK_DIRECT_ALIAS": direct_alias,
                "MOCK_CURL_FAIL": "1" if curl_fail else "0",
                "GLM53_READY_TIMEOUT": "3",
                "GLM53_READY_POLL": "1",
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
            "opencode-argv: <--model> <local/glm-5.3> <-s> <ses_test>",
            result.stdout,
        )
        inline_line = next(
            line for line in result.stdout.splitlines() if line.startswith("inline=")
        )
        provider = json.loads(inline_line.removeprefix("inline="))["provider"]["local"]
        self.assertEqual(provider["models"]["glm-5.3"]["limit"]["context"], 60_000)
        self.assertEqual(provider["options"]["baseURL"], "{env:GLM53_DIRECT_BASE_URL}")
        self.assertEqual(provider["options"]["apiKey"], "{env:GLM_API_KEY}")
        self.assertIn("direct-base=http://chuckdancer:8080/v1", result.stdout)

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
        self.assertIn("selected 'kimi-k3-q5attn-abl-v26'", result.stderr)
        self.assertNotIn("opencode-argv:", result.stdout)

    def test_wrong_direct_endpoint_alias_fails_closed(self) -> None:
        result = self._run(direct_alias="kimi-k3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("direct endpoint serves 'kimi-k3'", result.stderr)
        self.assertNotIn("opencode-argv:", result.stdout)

    def test_unreachable_direct_endpoint_fails_before_opencode(self) -> None:
        result = self._run(curl_fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("direct GLM endpoint is unreachable", result.stderr)
        self.assertNotIn("opencode-argv:", result.stdout)


if __name__ == "__main__":
    unittest.main()
