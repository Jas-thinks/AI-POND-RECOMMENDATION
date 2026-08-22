import numpy as np
import logging
from collections import deque

logger = logging.getLogger(__name__)

# D8 Offsets and their corresponding angles/codes
# Row, Col offsets for the 8 directions starting from East and moving counter-clockwise
NEIGHBOR_OFFSETS = [
    (0, 1),    # 0: East
    (1, 1),    # 1: South-East
    (1, 0),    # 2: South
    (1, -1),   # 3: South-West
    (0, -1),   # 4: West
    (-1, -1),  # 5: North-West
    (-1, 0),   # 6: North
    (-1, 1)    # 7: North-East
]

DIAGONAL_FACTORS = [
    1.0,               # East (orthogonal)
    np.sqrt(2),        # South-East (diagonal)
    1.0,               # South (orthogonal)
    np.sqrt(2),        # South-West (diagonal)
    1.0,               # West (orthogonal)
    np.sqrt(2),        # North-West (diagonal)
    1.0,               # North (orthogonal)
    np.sqrt(2)         # North-East (diagonal)
]

def calculate_d8_flow_direction(elevation_grid, cell_spacing=50):
    """
    Compute the D8 flow direction for each cell.
    Water flows to the neighbor with the steepest downslope gradient.
    
    Returns:
        np.ndarray: 2D array of neighbor indices (0-7), or -1 if cell is a sink (no lower neighbor).
    """
    rows, cols = elevation_grid.shape
    flow_dir = -np.ones((rows, cols), dtype=int)
    
    for r in range(rows):
        for c in range(cols):
            elev_val = elevation_grid[r, c]
            max_slope = 0.0
            best_neighbor = -1
            
            for idx, (dr, dc) in enumerate(NEIGHBOR_OFFSETS):
                nr, nc = r + dr, c + dc
                
                # Check boundaries
                if 0 <= nr < rows and 0 <= nc < cols:
                    elev_neighbor = elevation_grid[nr, nc]
                    dz = elev_val - elev_neighbor
                    
                    if dz > 0:  # Downslope
                        dist = cell_spacing * DIAGONAL_FACTORS[idx]
                        slope = dz / dist
                        if slope > max_slope:
                            max_slope = slope
                            best_neighbor = idx
            
            flow_dir[r, c] = best_neighbor
            
    return flow_dir

def calculate_flow_accumulation(elevation_grid, flow_dir):
    """
    Calculate flow accumulation grid (cell count) using the D8 flow directions.
    Cells are sorted in descending order of elevation to ensure upstream cells
    propagate their flow to downstream cells properly.
    
    Returns:
        np.ndarray: 2D array representing the flow accumulation values (count of cells draining into each cell)
    """
    rows, cols = elevation_grid.shape
    # Every cell accumulates at least its own area (starting value of 1)
    flow_accum = np.ones((rows, cols), dtype=float)
    
    # Get all indices and flat-sort them by elevation in descending order
    flat_indices = np.argsort(elevation_grid.flatten())[::-1]
    
    for idx in flat_indices:
        r = idx // cols
        c = idx % cols
        
        direction = flow_dir[r, c]
        if direction != -1:
            dr, dc = NEIGHBOR_OFFSETS[direction]
            nr, nc = r + dr, c + dc
            
            if 0 <= nr < rows and 0 <= nc < cols:
                flow_accum[nr, nc] += flow_accum[r, c]
                
    logger.info(f"Calculated flow accumulation: max={np.max(flow_accum)}")
    return flow_accum

def delineate_catchment(flow_dir, target_r, target_c, cell_spacing=50):
    """
    Trace upstream cells using reverse BFS to delineate the catchment basin
    for a specific cell (target_r, target_c).
    
    Returns:
        dict: A dictionary containing:
            - 'catchment_mask': 2D boolean numpy array indicating cell membership.
            - 'area_sqm': Total catchment area in square meters.
            - 'cells_count': Total number of cells.
            - 'boundary_coords': List of (row, col) coordinates belonging to the catchment.
    """
    rows, cols = flow_dir.shape
    catchment_mask = np.zeros((rows, cols), dtype=bool)
    
    # Initialize queue for BFS
    queue = deque([(target_r, target_c)])
    catchment_mask[target_r, target_c] = True
    cells_list = [(target_r, target_c)]
    
    while queue:
        r, c = queue.popleft()
        
        # Check all 8 neighbors
        for idx, (dr, dc) in enumerate(NEIGHBOR_OFFSETS):
            nr, nc = r + dr, c + dc
            
            if 0 <= nr < rows and 0 <= nc < cols:
                # If neighbor is not yet processed
                if not catchment_mask[nr, nc]:
                    # Find where this neighbor flows
                    neighbor_dir = flow_dir[nr, nc]
                    if neighbor_dir != -1:
                        # Downstream offsets for the neighbor
                        ndr, ndc = NEIGHBOR_OFFSETS[neighbor_dir]
                        down_r, down_c = nr + ndr, nc + ndc
                        
                        # If neighbor flows into the current cell (r, c)
                        if down_r == r and down_c == c:
                            catchment_mask[nr, nc] = True
                            cells_list.append((nr, nc))
                            queue.append((nr, nc))
                            
    cells_count = len(cells_list)
    area_sqm = cells_count * (cell_spacing ** 2)
    
    logger.info(f"Delineated catchment for ({target_r}, {target_c}): cells={cells_count}, area={area_sqm} sqm")
    
    return {
        "catchment_mask": catchment_mask,
        "area_sqm": area_sqm,
        "cells_count": cells_count,
        "boundary_coords": cells_list
    }
