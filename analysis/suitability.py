import numpy as np
import math
import logging

logger = logging.getLogger(__name__)

# Default weights for the multi-criteria decision system
DEFAULT_WEIGHTS = {
    "elevation": 0.25,
    "slope": 0.35,
    "accumulation": 0.40
}

def calculate_suitability_grid(elevation_grid, slope_deg_grid, flow_accum_grid, weights=None, land_suitability=None):
    """
    Calculate a suitability score (0-100) for each cell in the terrain.
    
    Formula:
        Score = w_elev * S_elev + w_slope * S_slope + w_accum * S_accum
        
    Normalize terms:
        - S_elev: Lower elevation is better -> (max_el - el) / (max_el - min_el) * 100
        - S_slope: Lower slope is better. Slopes > 15 deg get 0.
                   Otherwise: (1 - slope / 15) * 100
        - S_accum: Higher accumulation is better (Log scale) -> ln(accum) / ln(max_accum) * 100
        
    Args:
        elevation_grid (np.ndarray): 2D array of elevations
        slope_deg_grid (np.ndarray): 2D array of slopes in degrees
        flow_accum_grid (np.ndarray): 2D array of flow accumulation values
        weights (dict): Weight overrides for elevation, slope, accumulation
        land_suitability (np.ndarray): Optional 2D array of multiplier values (1.0 or 0.0)
        
    Returns:
        np.ndarray: 2D array of suitability scores (0-100)
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS
        
    # Verify weights sum to 1.0
    w_sum = sum(weights.values())
    w_elev = weights["elevation"] / w_sum
    w_slope = weights["slope"] / w_sum
    w_accum = weights["accumulation"] / w_sum
    
    rows, cols = elevation_grid.shape
    
    # 1. Elevation normalization (Lower is better)
    elev_min = np.min(elevation_grid)
    elev_max = np.max(elevation_grid)
    elev_range = elev_max - elev_min
    if elev_range > 0:
        s_elev = ((elev_max - elevation_grid) / elev_range) * 100.0
    else:
        s_elev = np.ones((rows, cols)) * 100.0
        
    # 2. Slope normalization (Flatter is better, slope > 15 deg is unsuitable)
    max_slope_limit = 15.0  # standard engineering limit for easy excavating
    s_slope = np.zeros((rows, cols))
    suitable_slope_mask = slope_deg_grid <= max_slope_limit
    s_slope[suitable_slope_mask] = (1.0 - slope_deg_grid[suitable_slope_mask] / max_slope_limit) * 100.0
    
    # 3. Flow accumulation normalization (Log-scale, higher is better)
    max_accum = np.max(flow_accum_grid)
    s_accum = np.zeros((rows, cols))
    if max_accum > 1:
        # Use log scale because flow accumulation has highly skewed distribution
        s_accum = (np.log(flow_accum_grid) / np.log(max_accum)) * 100.0
    else:
        s_accum = np.ones((rows, cols)) * 100.0
        
    # Combine scores
    suitability = w_elev * s_elev + w_slope * s_slope + w_accum * s_accum
    
    # Apply optional land availability overrides
    if land_suitability is not None:
        suitability = suitability * land_suitability
        
    # Cells on the very edge of the DEM are set to 0 to prevent edge-effect routing issues
    suitability[0, :] = 0
    suitability[-1, :] = 0
    suitability[:, 0] = 0
    suitability[:, -1] = 0
    
    return np.round(suitability, 2)

def identify_candidates(suitability_grid, min_score=45.0, max_candidates=5, min_dist_cells=2.5):
    """
    Select candidate pond locations by picking high-suitability cells
    that are spatially separated (not adjacent in the same depression).
    
    Args:
        suitability_grid (np.ndarray): 2D suitability array
        min_score (float): Minimum score to consider
        max_candidates (int): Maximum candidates to return
        min_dist_cells (float): Minimum grid cell distance between candidates
        
    Returns:
        list: List of dicts, each with {'r', 'c', 'score'} sorted by score descending
    """
    rows, cols = suitability_grid.shape
    candidates = []
    
    # Flatten indices and sort by suitability descending
    flat_indices = np.argsort(suitability_grid.flatten())[::-1]
    
    for idx in flat_indices:
        r = idx // cols
        c = idx % cols
        score = suitability_grid[r, c]
        
        if score < min_score:
            break  # No more cells above threshold
            
        # Check spatial separation from existing candidates
        too_close = False
        for cand in candidates:
            dist = math.sqrt((r - cand["r"])**2 + (c - cand["c"])**2)
            if dist < min_dist_cells:
                too_close = True
                break
                
        if not too_close:
            candidates.append({
                "r": int(r),
                "c": int(c),
                "score": float(score)
            })
            if len(candidates) >= max_candidates:
                break
                
    logger.info(f"Identified {len(candidates)} candidate pond locations.")
    return candidates

def recommend_pond_dimensions(runoff_volume_m3, target_fraction=0.15, default_depth=2.5):
    """
    Recommend dimensions for the pond.
    Sizes the pond to store a fraction of the annual catchment runoff.
    
    Args:
        runoff_volume_m3 (float): Estimated annual runoff volume in m3
        target_fraction (float): Target fraction of annual runoff to store
        default_depth (float): Recommended average pond depth in meters
        
    Returns:
        dict: Dict containing 'length_m', 'width_m', 'depth_m', and 'capacity_m3'
    """
    # Target volume
    target_vol = runoff_volume_m3 * target_fraction
    
    # Bounds on pond storage capacity (for safety/practicality of village ponds)
    # Min 200 m3, Max 5000 m3
    capacity = max(200.0, min(5000.0, target_vol))
    
    # Area needed
    area_needed = capacity / default_depth
    
    # Width and Length assuming a 1.5:1 length-to-width ratio
    # Area = L * W = 1.5 * W * W => W = sqrt(Area / 1.5)
    width = math.sqrt(area_needed / 1.5)
    length = 1.5 * width
    
    return {
        "length_m": round(length, 1),
        "width_m": round(width, 1),
        "depth_m": round(default_depth, 1),
        "capacity_m3": round(capacity, 1),
        "target_fraction_percent": round(target_fraction * 100, 1)
    }
