from __future__ import annotations
import numpy as np

DR = (-1, -1, 0, 1, 1, 1, 0, -1)
DC = (0, 1, 1, 1, 0, -1, -1, -1)
DIST = (1.0, 1.4142135623730951, 1.0, 1.4142135623730951, 1.0, 1.4142135623730951, 1.0, 1.4142135623730951)


def downstream_cell(r: int, c: int, direction: np.ndarray) -> tuple[int, int] | None:
    if r < 0 or r >= direction.shape[0] or c < 0 or c >= direction.shape[1]:
        return None
    d = direction[r, c]
    if d < 0 or d >= 8:
        return None
    return r + DR[d], c + DC[d]


def calculate_flow_direction(
    dem: np.ndarray,
    valid_mask: np.ndarray,
    resolution_m: float,
) -> np.ndarray:
    rows, cols = dem.shape
    direction = np.full((rows, cols), -1, dtype=np.int8)

    for r in range(rows):
        for c in range(cols):
            if not valid_mask[r, c] or np.isnan(dem[r, c]):
                continue
            elev = dem[r, c]
            max_slope = 0.0
            best_d = -1
            for d in range(8):
                nr, nc = r + DR[d], c + DC[d]
                if 0 <= nr < rows and 0 <= nc < cols and valid_mask[nr, nc] and not np.isnan(dem[nr, nc]):
                    drop = elev - dem[nr, nc]
                    if drop > 0:
                        dist = resolution_m * DIST[d]
                        slope = drop / dist
                        if slope > max_slope:
                            max_slope = slope
                            best_d = d
            direction[r, c] = best_d

    return direction
