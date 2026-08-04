"""Headless smoke test for the built MulticastTool.exe.

Launches the exe in the background, waits a couple of seconds, checks
the process is still alive, then terminates it. Exits 0 on success.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve().parent
    exe = here / "dist" / "MulticastTool" / "MulticastTool.exe"
    if not exe.exists():
        print(f"FAIL: {exe} not found. Run: pyinstaller --noconfirm multicast_tool.spec")
        return 1

    print(f"Launching: {exe}")
    env = os.environ.copy()
    # Force the Qt platform to "offscreen" so the GUI does not need a
    # display / desktop session. This is what we want for headless CI.
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.Popen(
        [str(exe)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Give it a moment to either crash or settle
    time.sleep(3.0)
    if proc.poll() is not None:
        out, err = proc.communicate(timeout=2)
        print("FAIL: process exited prematurely")
        print("--- stdout ---")
        print(out.decode("utf-8", errors="replace"))
        print("--- stderr ---")
        print(err.decode("utf-8", errors="replace"))
        return 1

    print(f"OK: process {proc.pid} alive after 3s")
    # Try graceful termination first
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    print("Process terminated cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
