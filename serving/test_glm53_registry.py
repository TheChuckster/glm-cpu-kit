#!/usr/bin/env python3
"""GLM-5.3 registry, revision-pin, and shard-manifest regression tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import textwrap
import unittest


HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "glm-variants.conf"
MODEL_CLI = HERE / "glm-model"
MANIFEST = HERE / "manifests" / "glm53-q4xl.sha256"
REVISION = "346b3591c7f28d1a23716f97a065ecf12ec14771"
SIZES = [
    9428677,
    49433942336,
    48566415136,
    48566415136,
    48566415136,
    48566415136,
    48566415136,
    48566415136,
    48566415136,
    48566415136,
    29314424736,
]


class Glm53RegistryTest(unittest.TestCase):
    def test_registry_row_is_exact_and_quality_first(self) -> None:
        row = next(
            line
            for line in REGISTRY.read_text(encoding="utf-8").splitlines()
            if line.split("|", 1)[0].strip() == "glm53-q4xl"
        )
        fields = [field.strip() for field in row.split("|", 8)]
        self.assertEqual(len(fields), 9)
        name, repo, subdir, prefix, shards, alias, directory, engine, opts = fields
        self.assertEqual(name, "glm53-q4xl")
        self.assertEqual(repo, f"unsloth/GLM-5.3-GGUF@{REVISION}")
        self.assertEqual(subdir, "UD-Q4_K_XL")
        self.assertEqual(prefix, "GLM-5.3-UD-Q4_K_XL")
        self.assertEqual(shards, "11")
        self.assertEqual(alias, "glm-5.3")
        self.assertEqual(
            directory, "/home/chuck/models/GLM-5.3-UD-Q4_K_XL/UD-Q4_K_XL"
        )
        self.assertIn("246e671e", engine)

        argv = shlex.split(opts)
        self.assertEqual(argv[argv.index("--reasoning-format") + 1], "deepseek")
        self.assertEqual(argv[argv.index("--repeat-penalty") + 1], "1.0")
        self.assertNotIn("--reasoning off", opts)
        kwargs = json.loads(argv[argv.index("--chat-template-kwargs") + 1])
        self.assertEqual(kwargs, {"reasoning_effort": "max", "clear_thinking": True})

    def test_release_manifest_has_all_exact_shards(self) -> None:
        entries: dict[str, str] = {}
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            digest, name = line.split(None, 1)
            entries[name.strip()] = digest
        self.assertEqual(len(entries), 11)
        self.assertEqual(sum(SIZES), 467_289_116_837)
        for index in range(1, 12):
            name = f"GLM-5.3-UD-Q4_K_XL-{index:05d}-of-00011.gguf"
            self.assertIn(name, entries)
            self.assertRegex(entries[name], r"^[0-9a-f]{64}$")


class ModelCliPinAndManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.model_dir = self.tmp / "model"
        self.model_dir.mkdir()
        self.manifest_dir = self.tmp / "manifests"
        self.manifest_dir.mkdir()
        self.curl_log = self.tmp / "curl.log"
        self.conf = self.tmp / "variants.conf"
        self.conf.write_text(
            f"tiny|owner/repo@deadbeef|Q|tiny|1|tiny|{self.model_dir}||\n",
            encoding="utf-8",
        )
        self._write_executable(
            "curl",
            r"""
            #!/usr/bin/env bash
            printf '%s\n' "$*" >> "$MOCK_CURL_LOG"
            printf '%s\n' '[{"path":"Q/tiny.gguf","lfs":{"size":3}}]'
            """,
        )
        (self.model_dir / "tiny.gguf").write_bytes(b"abc")
        digest = hashlib.sha256(b"abc").hexdigest()
        (self.manifest_dir / "tiny.sha256").write_text(
            f"{digest}  tiny.gguf\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_executable(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _run_verify(self) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin}:{env['PATH']}",
                "GLM_VARIANTS_CONF": str(self.conf),
                "GLM_MANIFEST_DIR": str(self.manifest_dir),
                "MOCK_CURL_LOG": str(self.curl_log),
            }
        )
        return subprocess.run(
            [str(MODEL_CLI), "verify", "tiny"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_verify_uses_pinned_revision_and_checks_manifest(self) -> None:
        result = self._run_verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tiny.gguf: OK", result.stdout)
        self.assertEqual((self.model_dir / ".complete").read_text().strip(), "3")
        self.assertIn("/tree/deadbeef/Q", self.curl_log.read_text())

    def test_manifest_mismatch_fails_closed(self) -> None:
        (self.model_dir / "tiny.gguf").write_bytes(b"abd")
        result = self._run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest verification failed", result.stderr)
        self.assertFalse((self.model_dir / ".complete").exists())

    def test_pinned_revision_without_manifest_fails_closed(self) -> None:
        (self.manifest_dir / "tiny.sha256").unlink()
        (self.model_dir / ".complete").write_text("3\n", encoding="utf-8")
        result = self._run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pinned repository requires readable manifest", result.stderr)
        self.assertFalse((self.model_dir / ".complete").exists())


if __name__ == "__main__":
    unittest.main()
