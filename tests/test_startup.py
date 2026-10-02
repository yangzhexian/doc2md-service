"""Exercise startup scripts with isolated interpreters and service commands."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if os.name == "nt":
    BASH = next(
        (str(path) for path in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/bin/bash.exe",
        ) if path.is_file()),
        None,
    )
else:
    BASH = shutil.which("bash")


def bash_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if os.name == "nt":
        return "/" + value[0].lower() + value[2:]
    return value


@unittest.skipUnless(BASH, "Bash is required to exercise Unix startup scripts")
class StartupScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="docs2md_startup_test_")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        (self.project / "src").mkdir()
        (self.project / "src/converter_service.py").write_text("", encoding="utf-8")
        (self.project / "requirements.txt").write_text("fixture", encoding="utf-8")
        self.trace = self.project / "trace.txt"
        self.args = self.project / "server_args.txt"
        self.mockbin = self.project / "mockbin"
        self.mockbin.mkdir()
        self.venv = self.project / "venv"
        self.env = os.environ.copy()
        self.env.update({
            "DOCS2MD_TEST_PROJECT": bash_path(self.project),
            "DOCS2MD_TEST_TRACE": bash_path(self.trace),
            "DOCS2MD_TEST_ARGS": bash_path(self.args),
            "DOCS2MD_TEST_PIP_EXIT": "0",
            "DOCS2MD_TEST_UVICORN_EXIT": "0",
            "DOCS2MD_TEST_ENTRY_EXIT": "0",
            "DOCS2MD_TEST_SETUP_DELAY": "0",
        })
        self.write_script(self.project / "start.sh", (PROJECT_ROOT / "start.sh").read_text())
        self.write_script(self.project / "stub_python", """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == */scripts/requirements_hash.py ]]; then
    printf fixturehash
elif [ "$1" = -m ] && [ "$2" = pip ]; then
    printf 'pip:%s\n' "$*" >> "$DOCS2MD_TEST_TRACE"
    sleep "$DOCS2MD_TEST_SETUP_DELAY"
    exit "$DOCS2MD_TEST_PIP_EXIT"
elif [ "$1" = -m ] && [ "$2" = uvicorn ]; then
    if [ "${3:-}" = --version ]; then
        echo python-uvicorn-check >> "$DOCS2MD_TEST_TRACE"
        exit "$DOCS2MD_TEST_UVICORN_EXIT"
    fi
    echo server-start >> "$DOCS2MD_TEST_TRACE"
    printf '%s\n' "$@" > "$DOCS2MD_TEST_ARGS"
else
    echo "Unexpected interpreter command: $*" >&2
    exit 99
fi
""")
        self.write_script(self.project / "stub_uvicorn", """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" != --version ]; then exit 99; fi
echo uvicorn-entry-check >> "$DOCS2MD_TEST_TRACE"
exit "$DOCS2MD_TEST_ENTRY_EXIT"
""")
        self.write_script(self.mockbin / "python3", """#!/usr/bin/env bash
set -euo pipefail
if [ "$1" != -m ] || [ "$2" != venv ]; then exit 99; fi
mkdir -p "$3/bin"
cp "$DOCS2MD_TEST_PROJECT/stub_python" "$3/bin/python"
cp "$DOCS2MD_TEST_PROJECT/stub_uvicorn" "$3/bin/uvicorn"
touch "$3/bin/activate"
chmod +x "$3/bin/python" "$3/bin/uvicorn"
""")
        self.write_script(self.mockbin / "systemctl", """#!/usr/bin/env bash
set -euo pipefail
printf 'systemctl:%s\n' "$*" >> "$DOCS2MD_TEST_TRACE"
if [ "$2" = start ] && [ ! -f "$DOCS2MD_TEST_PROJECT/venv/.deps_installed" ]; then
    echo 'Dependency installation was not complete.' >&2
    exit 17
fi
""")
        scripts = self.project / "scripts"
        scripts.mkdir()
        installer = (PROJECT_ROOT / "scripts/install-autostart.sh").read_text()
        # Redirect only the copied script's output paths; keep the user's HOME intact.
        self.assertIn('SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"', installer)
        self.assertIn('mkdir -p "$HOME/.config/systemd/user"', installer)
        installer = installer.replace(
            'SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"',
            'SERVICE_FILE="$PROJECT_DIR/.test-systemd/${SERVICE_NAME}.service"',
        ).replace('mkdir -p "$HOME/.config/systemd/user"', 'mkdir -p "$PROJECT_DIR/.test-systemd"')
        self.write_script(scripts / "install-autostart.sh", installer)
        shutil.copyfile(PROJECT_ROOT / "scripts/docs2md.service", scripts / "docs2md.service")

    def write_script(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def make_venv(self, *, installed_hash: str = "fixturehash", entrypoint: bool = True) -> None:
        (self.venv / "bin").mkdir(parents=True)
        shutil.copyfile(self.project / "stub_python", self.venv / "bin/python")
        (self.venv / "bin/python").chmod(0o755)
        (self.venv / "bin/activate").touch()
        if entrypoint:
            shutil.copyfile(self.project / "stub_uvicorn", self.venv / "bin/uvicorn")
            (self.venv / "bin/uvicorn").chmod(0o755)
        (self.venv / ".deps_installed").write_text(installed_hash, encoding="utf-8")

    def run_script(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        command = 'export PATH=' + shlex.quote(bash_path(self.mockbin)) + ':"$PATH"; exec bash '
        command += shlex.join([bash_path(self.project / script), *args])
        return subprocess.run(
            [BASH, "-c", command], capture_output=True, text=True,
            env=self.env, cwd=self.project, timeout=20,
        )

    def trace_lines(self) -> list[str]:
        return self.trace.read_text().splitlines() if self.trace.exists() else []

    def test_host_option_preserves_default_port_and_positional_compatibility(self) -> None:
        self.make_venv()
        for args, port, host in (
            ((), "8000", "127.0.0.1"),
            (("--host", "0.0.0.0"), "8000", "0.0.0.0"),
            (("9090", "0.0.0.0"), "9090", "0.0.0.0"),
            (("--port", "9090", "--host", "0.0.0.0"), "9090", "0.0.0.0"),
            (("--port=9090", "--host=0.0.0.0"), "9090", "0.0.0.0"),
        ):
            with self.subTest(args=args):
                result = self.run_script("start.sh", *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = self.args.read_text().splitlines()
                self.assertEqual(actual[actual.index("--port") + 1], port)
                self.assertEqual(actual[actual.index("--host") + 1], host)
                self.assertNotIn("pip:", "\n".join(self.trace_lines()))

    def test_invalid_options_fail_before_setup(self) -> None:
        for args in (("--port",), ("--host",), ("--port", "oops"),
                     ("0",), ("65536",), ("--unknown",), ("--host=",)):
            with self.subTest(args=args):
                result = self.run_script("start.sh", *args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(self.venv.exists())
                self.assertFalse(self.trace.exists())

    def test_setup_only_installs_and_checks_uvicorn_without_starting_server(self) -> None:
        result = self.run_script("start.sh", "--setup-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.venv / ".deps_installed").read_text(), "fixturehash")
        self.assertEqual(sum(line.startswith("pip:") for line in self.trace_lines()), 2)
        self.assertTrue(any("install --require-hashes -r" in line for line in self.trace_lines()))
        self.assertIn("python-uvicorn-check", self.trace_lines())
        self.assertNotIn("server-start", self.trace_lines())

    def test_first_autostart_waits_for_setup_longer_than_five_seconds(self) -> None:
        self.env["DOCS2MD_TEST_SETUP_DELAY"] = "3"
        result = self.run_script("scripts/install-autostart.sh", "9090")
        self.assertEqual(result.returncode, 0, result.stderr)
        trace = self.trace_lines()
        self.assertEqual(sum(line.startswith("pip:") for line in trace), 2)
        self.assertLess(trace.index("uvicorn-entry-check"), trace.index("systemctl:--user daemon-reload"))
        self.assertIn("systemctl:--user start docs2md.service", trace)
        self.assertNotIn("server-start", trace)
        self.assertIn("--port 9090", (self.project / ".test-systemd/docs2md.service").read_text())

    def test_autostart_propagates_setup_failures_without_service_changes(self) -> None:
        for variable, code in (("DOCS2MD_TEST_PIP_EXIT", "19"),
                               ("DOCS2MD_TEST_UVICORN_EXIT", "23")):
            with self.subTest(variable=variable):
                self.env[variable] = code
                result = self.run_script("scripts/install-autostart.sh")
                self.assertEqual(result.returncode, int(code), result.stderr)
                self.assertFalse((self.project / ".test-systemd").exists())
                self.assertFalse((self.venv / ".deps_installed").exists())
                self.assertFalse(any(line.startswith("systemctl:") for line in self.trace_lines()))
                self.env[variable] = "0"

    def test_autostart_rejects_unusable_uvicorn_entry_point(self) -> None:
        self.make_venv()
        self.env["DOCS2MD_TEST_ENTRY_EXIT"] = "31"
        result = self.run_script("scripts/install-autostart.sh")
        self.assertEqual(result.returncode, 31, result.stderr)
        self.assertFalse((self.project / ".test-systemd").exists())
        self.assertFalse(any(line.startswith("systemctl:") for line in self.trace_lines()))

    def test_autostart_rejects_missing_uvicorn_entry_point(self) -> None:
        self.make_venv(entrypoint=False)
        result = self.run_script("scripts/install-autostart.sh")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("uvicorn is not installed", result.stderr)
        self.assertFalse((self.project / ".test-systemd").exists())
        self.assertFalse(any(line.startswith("systemctl:") for line in self.trace_lines()))

    def test_autostart_upgrades_existing_venv_when_requirements_change(self) -> None:
        self.make_venv(installed_hash="outdated")
        result = self.run_script("scripts/install-autostart.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(line.startswith("pip:") for line in self.trace_lines()), 2)
        self.assertEqual((self.venv / ".deps_installed").read_text(), "fixturehash")


if __name__ == "__main__":
    unittest.main()
