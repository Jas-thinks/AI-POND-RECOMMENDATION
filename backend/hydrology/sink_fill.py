from __future__ import annotations
import heapq
import numpy as np

NEIGHBORS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def fill_sinks_priority_flood(
    dem: np.ndarray,
    valid_mask: np.ndarray,
) -> np.ndarray:
    """Simple Priority-Flood depression filling for a masked DEM."""
    rows, cols = dem.shape
    out = dem.copy()
    visited = np.zeros_like(valid_mask, dtype=bool)
    heap = []

    def is_boundary_cell(r: int, c: int) -> bool:
        if r == 0 or r == rows - 1 or c == 0 or c == cols - 1:
            return True
        for dr, dc in NEIGHBORS:
            rr, cc = r + dr, c + dc
            if not valid_mask[rr, cc]:
                return True
        return False

    for r in range(rows):
        for c in range(cols):
            if valid_mask[r, c] and not np.isnan(dem[r, c]):
                if is_boundary_cell(r, c):
                    visited[r, c] = True
                    heapq.heappush(heap, (float(dem[r, c]), r, c))

    if not heap:
        raise ValueError("No valid terrain boundary cells were found.")

    eps = 1e-6
    while heap:
        elev, r, c = heapq.heappop(heap)
        for dr, dc in NEIGHBORS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows and 0 <= cc < cols and valid_mask[rr, cc] and not visited[rr, cc] and not np.isnan(dem[rr, cc]):
                visited[rr, cc] = True
                next_elev = max(float(dem[rr, cc]), elev + eps)
                out[rr, cc] = next_elev
                heapq.heappush(heap, (next_elev, rr, cc))

    out[~valid_mask] = np.nan
    return out
