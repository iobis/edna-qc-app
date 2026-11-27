"""Dev server that reloads on code changes and respawns a dead worker.

uvicorn --reload keeps the parent process alive when the worker segfaults.
The listen socket stays open, so later requests (including POST /api/jobs)
hang until the proxy times out.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

from watchfiles import watch

CMD = [
    sys.executable,
    "-m",
    "uvicorn",
    "main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
]


def _start() -> subprocess.Popen:
    return subprocess.Popen(CMD)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def main() -> None:
    stop = False

    def _request_stop(signum, _frame):
        nonlocal stop
        stop = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    proc = _start()
    signal_deaths = 0
    window_started = time.monotonic()
    try:
        for changes in watch(
            "/app",
            yield_on_timeout=True,
            rust_timeout=500,
            raise_interrupt=False,
        ):
            if stop:
                break

            code = proc.poll()
            if code is not None:
                if code < 0:
                    now = time.monotonic()
                    if now - window_started > 30:
                        signal_deaths = 0
                        window_started = now
                    signal_deaths += 1
                    print(
                        f"API worker died (signal {-code}), restarting",
                        flush=True,
                    )
                    if signal_deaths >= 5:
                        print(
                            "API worker crashed 5 times in 30s; waiting for a code change",
                            flush=True,
                        )
                        signal_deaths = 0
                        continue
                    time.sleep(0.3)
                    proc = _start()
                elif changes:
                    print("Reloading API after worker exit", flush=True)
                    proc = _start()
                continue

            if changes:
                print("Reloading API", flush=True)
                _stop(proc)
                proc = _start()
    except KeyboardInterrupt:
        pass
    finally:
        _stop(proc)


if __name__ == "__main__":
    main()
