import numpy as np
import requests
import logging
import math

logger = logging.getLogger(__name__)

# Constants
EARTH_RADIUS = 6378137.0  # in meters

def generate_coordinate_grid(center_lat, center_lon, grid_size=11, cell_spacing=50):
    """
    Generate an N x N grid of latitude and longitude coordinates centered around a location.
    Row 0 is North (highest lat), Row N-1 is South (lowest lat).
    Col 0 is West (lowest lon), Col N-1 is East (highest lon).
    
    Returns:
        tuple: (lats_grid, lons_grid) 2D numpy arrays
    """
    n = (grid_size - 1) // 2
    
    # Degrees offset conversion
    # 1 degree latitude = 111,320 meters
    lat_deg_per_meter = 1.0 / 111320.0
    # 1 degree longitude = 111,320 * cos(lat) meters
    rad_lat = math.radians(center_lat)
    lon_deg_per_meter = 1.0 / (111320.0 * math.cos(rad_lat))
    
    lats_grid = np.zeros((grid_size, grid_size))
    lons_grid = np.zeros((grid_size, grid_size))
    
    # We want row 0 to be North (+n) and row N-1 to be South (-n)
    # col 0 to be West (-n) and col N-1 to be East (+n)
    for r in range(grid_size):
        i = n - r  # ranges from +n down to -n
        for c in range(grid_size):
            j = c - n  # ranges from -n up to +n
            
            lats_grid[r, c] = center_lat + (i * cell_spacing * lat_deg_per_meter)
            lons_grid[r, c] = center_lon + (j * cell_spacing * lon_deg_per_meter)
            
    return lats_grid, lons_grid

def fetch_elevation_grid(lats_grid, lons_grid):
    """
    Fetch elevations from the Open-Meteo Elevation API for the grid.
    
    Returns:
        np.ndarray: 2D array of elevations, or None if request fails.
    """
    shape = lats_grid.shape
    flat_lats = lats_grid.flatten()
    flat_lons = lons_grid.flatten()
    
    # Format query parameters (max 1000 locations supported by Open-Meteo)
    lat_str = ",".join([f"{lat:.6f}" for lat in flat_lats])
    lon_str = ",".join([f"{lon:.6f}" for lon in flat_lons])
    
    url = "https://api.open-meteo.com/v1/elevation"
    params = {
        "latitude": lat_str,
        "longitude": lon_str
    }
    
    try:
        logger.info(f"Fetching elevations from Open-Meteo API for {len(flat_lats)} grid cells...")
        response = requests.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            if "elevation" in data:
                elevations = np.array(data["elevation"], dtype=float)
                # Reshape back to the original grid shape
                return elevations.reshape(shape)
            else:
                logger.error("Open-Meteo response does not contain 'elevation' key.")
                return None
        else:
            logger.error(f"Open-Meteo Elevation API error: HTTP status {response.status_code}")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Elevation grid fetch request failed: {e}")
        return None

def generate_synthetic_dem(grid_size=11, base_elevation=150.0):
    """
    Generate a synthetic elevation grid (DEM) representing a realistic valley basin.
    This basin slopes down toward the bottom-middle of the grid, creating a natural sink
    at (row=8, col=5) which acts as a perfect candidate location for pond construction.
    
    Returns:
        np.ndarray: 2D array of elevations
    """
    n = (grid_size - 1) // 2
    elev_grid = np.zeros((grid_size, grid_size))
    
    # Target low point / sink at row=8, col=5
    target_r = 8
    target_c = 5
    
    # Define a clean quadratic valley basin
    for r in range(grid_size):
        for c in range(grid_size):
            # Distance terms to target
            dist_y = r - target_r
            dist_x = c - target_c
            
            # Mathematical basin shape: z = base_elev + A*dy^2 + B*dx^2
            # Added a slight ridge on the left and right to guide water flow
            z = base_elevation + 1.2 * (dist_y ** 2) + 2.0 * (dist_x ** 2) - 0.5 * dist_x * dist_y
            
            # Add micro-terrain variation (sine waves) for visual interest
            z += 1.5 * math.sin(r * 0.8) * math.cos(c * 0.8)
            
            elev_grid[r, c] = round(z, 2)
            
    return elev_grid
