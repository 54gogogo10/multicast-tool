"""Smoke test for the bundled onefile exes.

Launches each in the background, waits a few seconds, then terminates.
Exits 0 if the process is still running (i.e. didn't crash on startup).
"""

import os
import subprocess
import sys
import time


def smoke(exe: str) -> int:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.Popen([exe], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(5.0)
    if proc.poll() is not None:
        out, err = proc.communicate(timeout=2)
        print(f"FAIL [{exe}]: exit={proc.returncode}")
        print("stdout:", out.decode("utf-8", errors="replace")[:500])
        print("stderr:", err.decode("utf-8", errors="replace")[:500])
        return 1
    print(f"OK [{exe}]: pid {proc.pid} alive after 5s")
    proc.terminate()
    try:
        proc.wait(timeout=5)
        print("OK: terminated cleanly")
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


def main() -> int:
    targets = sys.argv[1:] or [
        "dist/MulticastTool.exe",
        "dist/MulticastTool-Win7.exe",
    ]
    rc = 0
    for t in targets:
        if not os.path.exists(t):
            print(f"SKIP: {t} not found")
            continue
        rc |= smoke(t)
    return rc


if __name__ == "__main__":
    sys.exit(main())
