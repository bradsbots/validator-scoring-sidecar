"""Loop-interval validation in the container entrypoint (entrypoint.sh)."""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "entrypoint.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None,
    reason="entrypoint.sh needs a POSIX sh",
)

INTERVAL_VARS = {
    "sync": "POSTFIAT_SIDECAR_SYNC_INTERVAL_SECONDS",
    "participate": "POSTFIAT_SIDECAR_CHAIN_POLL_INTERVAL_SECONDS",
}


@pytest.fixture
def stub(tmp_path):
    """A fake `validator-scoring-sidecar` that records every call it receives."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    script = bin_dir / "validator-scoring-sidecar"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        'if [ "$1" = "--version" ]; then echo "validator-scoring-sidecar 0.0.0"; fi\n'
        "exit 0\n"
    )
    script.chmod(0o755)
    return bin_dir, calls


def _env(bin_dir, mode, interval):
    return {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "POSTFIAT_SIDECAR_MODE": mode,
        INTERVAL_VARS[mode]: interval,
    }


@pytest.mark.parametrize("mode", ["sync", "participate"])
@pytest.mark.parametrize(
    "interval", ["0", "00", "0" * 400], ids=["0", "00", "400-zeros"]
)
def test_zero_interval_is_refused_before_the_loop(stub, mode, interval):
    bin_dir, calls = stub
    result = subprocess.run(
        ["sh", str(ENTRYPOINT)],
        env=_env(bin_dir, mode, interval),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "loop interval must be a positive integer" in result.stdout
    assert not calls.exists()


@pytest.mark.parametrize("interval", ["1", "060"])
def test_positive_interval_still_starts_the_loop(stub, interval):
    bin_dir, calls = stub
    proc = subprocess.Popen(
        ["sh", str(ENTRYPOINT)],
        env=_env(bin_dir, "sync", interval),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if calls.exists() and "sync" in calls.read_text().split("\n"):
                break
            time.sleep(0.05)
        time.sleep(0.5)
        assert proc.poll() is None
    finally:
        # Kill the whole group so the loop's background sleep cannot hold stdout.
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate(timeout=10)
    assert f"starting sync loop (interval={interval}s)" in output
    assert "sync" in calls.read_text().split("\n")
