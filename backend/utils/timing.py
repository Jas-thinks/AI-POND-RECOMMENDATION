"""Lightweight pipeline timing utilities.

Uses only the Python standard library (`time.perf_counter` and `logging`)
so we can measure expensive stages in the terrain/hydrology pipeline without
introducing a third-party logging framework.

No sensitive data (secrets, API keys, file contents) is ever logged here.
The only values emitted are stage names and elapsed seconds.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Iterator

logger = logging.getLogger("analysis.pipeline")


def _emit(stage: str, seconds: float) -> None:
    """Emit a single pipeline timing line.

    Rendered as ``[ANALYSIS] <stage>: X.XXs``. Root-available so errors in a
    single stage never break the whole pipeline.
    """
    try:
        logger.info("[ANALYSIS] %s: %.2fs", stage, seconds)
    except Exception:
        # Never let logging failures interfere with the request.
        pass


@contextlib.contextmanager
def timed_stage(stage: str) -> Iterator[None]:
    """Time a block of work and log how long it took when it finishes.

    Usage::

        with timed_stage("DEM generation"):
            dem = build_dem(...)

    The elapsed time of each stage is always logged, even on failure.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        _emit(stage, time.perf_counter() - start)