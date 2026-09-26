import json
import subprocess
import sys


def test_cli_version_and_isolated_ping():
    version = subprocess.run([sys.executable, "-m", "sozvon", "--version"], capture_output=True,
                             text=True, timeout=10, check=False)
    assert version.returncode == 0, version.stderr
    assert "0.1.0" in version.stdout
    probe = subprocess.run([sys.executable, "-m", "sozvon", "--probe", "ping"],
                           capture_output=True, text=True, timeout=15, check=False)
    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout)["pid"] != __import__("os").getpid()
