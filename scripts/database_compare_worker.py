"""Run manually with python -m scripts.database_compare_worker, or via app startup."""
from __future__ import annotations

import logging
import signal
import threading

from app.services.database_compare.worker import recover_interrupted, run_next, worker_lock


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    with worker_lock() as acquired:
        if not acquired:
            return
        recover_interrupted()
        while not stopped.is_set():
            try:
                if not run_next(stopping=stopped.is_set):
                    stopped.wait(1)
            except Exception:
                logging.exception("Database comparison worker iteration failed")
                stopped.wait(3)


if __name__ == "__main__":
    main()
