from __future__ import annotations
from collections import deque
import numpy as np
from backend.hydrology.flow_direction import downstream_cell


def delineate_catchment(
    direction: np.ndarray,
    valid_mask: np.ndarray,
    outlet_row: int,
    outlet_col: int,
) -> np.ndarray:
    rows, cols = direction.shape
    catchment = np.zeros((rows, cols), dtype=bool)

    if not (0 <= outlet_row < rows and 0 <= outlet_col < cols) or not valid_mask[outlet_row, outlet_col]:
        return catchment

    catchment[outlet_row, outlet_col] = True
    queue = deque([(outlet_row, outlet_col)])

    while queue:
        r, c = queue.popleft()
        r0, r1 = max(0, r - 1), min(rows, r + 2)
        c0, c1 = max(0, c - 1), min(cols, c + 2)

        for nr in range(r0, r1):
            for nc in range(c0, c1):
                if not catchment[nr, nc] and valid_mask[nr, nc]:
                    down = downstream_cell(nr, nc, direction)
                    if down == (r, c):
                        catchment[nr, nc] = True
                        queue.append((nr, nc))

    return catchment
