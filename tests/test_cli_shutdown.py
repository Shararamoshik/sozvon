import json
import os
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(os.name != "posix", reason="SIGINT test on POSIX")
def test_cli_sigint_cleans_up_without_traceback(tmp_path):
    process = subprocess.Popen([sys.executable, "-m", "sozvon", "--no-browser", "--port", "0",
                                "--data-dir", str(tmp_path)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        runtime = tmp_path / "runtime.json"
        while not runtime.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert runtime.exists()
        assert json.loads(runtime.read_text(encoding="utf-8"))["pid"] == process.pid
        process.send_signal(signal.SIGINT)
        _stdout, stderr = process.communicate(timeout=15)
        assert not runtime.exists()
        assert b"Traceback" not in stderr
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
