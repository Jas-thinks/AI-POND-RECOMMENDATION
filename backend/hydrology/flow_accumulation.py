from __future__ import annotations
from collections import deque
import numpy as np
from backend.hydrology.flow_direction import downstream_cell


def calculate_flow_accumulation(
    direction: np.ndarray,
    valid_mask: np.ndarray,
) -> np.ndarray:
    rows, cols = direction.shape
    in_degree = np.zeros((rows, cols), dtype=np.int32)
    acc = np.zeros((rows, cols), dtype=np.float64)

    for r in range(rows):
        for c in range(cols):
            if valid_mask[r, c]:
                acc[r, c] = 1.0
                down = downstream_cell(r, c, direction)
                if down is not None:
                    nr, nc = down
                    if 0 <= nr < rows and 0 <= nc < cols and valid_mask[nr, nc]:
                        in_degree[nr, nc] += 1
            else:
                acc[r, c] = np.nan

    queue = deque()
    for r in range(rows):
        for c in range(cols):
            if valid_mask[r, c] and in_degree[r, c] == 0:
                queue.append((r, c))

    while queue:
        r, c = queue.popleft()
        down = downstream_cell(r, c, direction)
        if down is not None:
            nr, nc = down
            if 0 <= nr < rows and 0 <= nc < cols and valid_mask[nr, nc]:
                acc[nr, nc] += acc[r, c]
                in_degree[nr, nc] -= 1
                if in_degree[nr, nc] == 0:
                    queue.append((nr, nc))

    return acc
