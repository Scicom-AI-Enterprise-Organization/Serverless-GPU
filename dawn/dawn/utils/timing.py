"""Timing helpers."""

import time
from contextlib import contextmanager


class Timer:
    """Context manager that measures wall-clock time in milliseconds.

    Usage:
        with Timer() as t:
            do_work()
        print(t.ms)
    """

    def __init__(self) -> None:
        self.start_ns: int = 0
        self.end_ns: int = 0

    def __enter__(self) -> "Timer":
        self.start_ns = time.monotonic_ns()
        return self

    def __exit__(self, *exc) -> None:
        self.end_ns = time.monotonic_ns()

    @property
    def ms(self) -> int:
        end = self.end_ns or time.monotonic_ns()
        return (end - self.start_ns) // 1_000_000

    @property
    def s(self) -> float:
        return self.ms / 1000.0
