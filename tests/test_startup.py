"""Exercise startup ordering without Docker or an external proxy."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        (self.workspace / "scripts").mkdir()
        (self.workspace / "gateway").mkdir()
        for name in ("scripts/start.sh", "proxy.example.json", "gateway/config.py"):
            shutil.copy2(REPOSITORY / name, self.workspace / name)
        self.bin_directory = self.workspace / "bin"
        self.bin_directory.mkdir()
        self.docker_log = self.workspace / "docker-calls.log"
        self.write_executable(
            "docker",
            """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
if [[ "$1" == compose && "$2" == up ]]; then
  exit "${FAKE_DOCKER_UP_RESULT:-0}"
fi
exit 0
""",
        )
        self.write_executable("curl", "#!/usr/bin/env bash\nexit 0\n")
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "PATH": str(self.bin_directory) + os.pathsep + os.environ["PATH"],
                "FAKE_DOCKER_LOG": str(self.docker_log),
                "CODESPACE_NAME": "fixture-codespace",
                "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
            }
        )

    def write_executable(self, name, source):
        executable = self.bin_directory / name
        executable.write_text(source)
        executable.chmod(0o755)

    def write_config(self, *, valid=True):
        configuration = {
            "server": "192.0.2.10" if valid else "",
            "port": 1080,
            "username": "fixture-user",
            "password": "never-log-secret",
            "connect_timeout_seconds": 15,
        }
        (self.workspace / "proxy.json").write_text(json.dumps(configuration))

    def run_start(self):
        return subprocess.run(
            ["bash", str(self.workspace / "scripts/start.sh")],
            cwd=self.workspace,
            env=self.environment,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def calls(self):
        return self.docker_log.read_text().splitlines()

    def assert_no_secret(self, result):
        self.assertNotIn("never-log-secret", result.stdout + result.stderr)
        self.assertNotIn("never-log-secret", self.docker_log.read_text())

    def test_missing_config_stops_old_stack_and_waits_for_configuration(self):
        result = self.run_start()
        self.assertEqual(result.returncode, 0, result.stderr)
        configuration = self.workspace / "proxy.json"
        self.assertTrue(configuration.is_file())
        self.assertEqual(stat.S_IMODE(configuration.stat().st_mode), 0o600)
        self.assertEqual(json.loads(configuration.read_text())["server"], "")
        self.assertIn("compose down --remove-orphans", self.calls())
        self.assertFalse(any(call.startswith("compose up") for call in self.calls()))

    def test_valid_configuration_starts_after_stopping_old_stack(self):
        self.write_config()
        result = self.run_start()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        stop_index = calls.index("compose down --remove-orphans")
        start_index = calls.index("compose up -d --build --wait --wait-timeout 180")
        self.assertLess(stop_index, start_index)
        self.assertIn("https://fixture-codespace-3000.app.github.dev/", result.stdout)
        self.assert_no_secret(result)

    def test_invalid_configuration_stops_old_stack_without_starting(self):
        self.write_config(valid=False)
        result = self.run_start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compose down --remove-orphans", self.calls())
        self.assertFalse(any(call.startswith("compose up") for call in self.calls()))
        self.assert_no_secret(result)

    def test_failed_start_stops_partial_stack_and_preserves_profile(self):
        self.write_config()
        profile = self.workspace / "browser-data"
        profile.mkdir()
        marker = profile / "keep-me"
        marker.write_text("profile data")
        self.environment["FAKE_DOCKER_UP_RESULT"] = "1"
        result = self.run_start()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls().count("compose down --remove-orphans"), 2)
        self.assertEqual(marker.read_text(), "profile data")
        self.assert_no_secret(result)

    def test_config_permission_failure_happens_after_stopping_old_stack(self):
        self.write_config()
        self.write_executable("chmod", "#!/usr/bin/env bash\nexit 1\n")
        result = self.run_start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compose down --remove-orphans", self.calls())
        self.assertFalse(any(call.startswith("compose up") for call in self.calls()))
        self.assert_no_secret(result)


if __name__ == "__main__":
    unittest.main()
