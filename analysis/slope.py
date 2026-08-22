import numpy as np
import logging

logger = logging.getLogger(__name__)

def calculate_slope(elevation_grid, cell_spacing=50):
    """
    Calculate the terrain slope for each cell in the elevation grid.
    Uses numerical gradient (central differences in the interior,
    first-order one-sided differences at the boundaries).
    
    Args:
        elevation_grid (np.ndarray): 2D array of elevations.
        cell_spacing (float): Spacing of the cells in meters (resolution).
        
    Returns:
        dict: A dictionary containing:
            - 'slope_deg': 2D numpy array of slope in degrees.
            - 'slope_percent': 2D numpy array of slope in percentage.
            - 'grad_x': 2D array of gradient in x-direction.
            - 'grad_y': 2D array of gradient in y-direction.
    """
    try:
        # np.gradient returns gradients along each axis.
        # axis 0 (rows) corresponds to Y gradient.
        # axis 1 (cols) corresponds to X gradient.
        grad_y, grad_x = np.gradient(elevation_grid, cell_spacing)
        
        # Calculate slope magnitude (rise over run)
        rise_run = np.sqrt(grad_x**2 + grad_y**2)
        
        # Slope in radians
        slope_rad = np.arctan(rise_run)
        
        # Slope in degrees
        slope_deg = np.degrees(slope_rad)
        
        # Slope in percent
        slope_percent = rise_run * 100
        
        logger.info(f"Calculated slope: mean={np.mean(slope_deg):.2f}°, max={np.max(slope_deg):.2f}°")
        
        return {
            "slope_deg": np.round(slope_deg, 2),
            "slope_percent": np.round(slope_percent, 2),
            "grad_x": grad_x,
            "grad_y": grad_y
        }
    except Exception as e:
        logger.error(f"Failed to calculate slope: {e}")
        raise e
