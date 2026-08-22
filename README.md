# AI-Based Village Pond Planning System

An interactive geographical decision support tool developed for **CS559 — Computer Systems Design** to assist planners, hydrologists, and students in identifying, evaluating, and sizing suitable locations for constructing village rainwater harvesting ponds.

---

## 1. Project Overview
Rainwater harvesting ponds are essential in semi-arid and agricultural regions to capture monsoon runoff, recharge groundwater aquifers, and provide backup irrigation. This project implements a lightweight spatial planning pipeline to geocode locations, analyze local digital elevation model (DEM) terrain, route water using the D8 flow direction model, outline catchment drainage basins, calculate runoff potentials using historic weather precipitation statistics, and recommend optimal pond construction coordinates based on a Multi-Criteria Decision Analysis (MCDA) framework.

---

## 2. System Architecture
The application runs locally with a clean, low-complexity, three-tier architecture:

```
[Leaflet Frontend] <--- JSON REST ---> [Flask Backend Router]
       |                                      |
       v                                      v
 [Map Layers / Charts]                [GIS Algorithms & APIs]
                                     - location.py (OSM Nominatim)
                                     - terrain.py (Open-Meteo DEM)
                                     - slope.py (Horn's Method)
                                     - catchment.py (D8 Accumulation)
                                     - rainfall.py (Open-Meteo Archive)
                                     - runoff.py (Rational Equation)
                                     - suitability.py (Weighted MCDA)
```

- **Frontend:** Single-page dashboard built using HTML5, Vanilla CSS, Leaflet.js (for map overlays), and a responsive CSS bar chart for meteorological averages.
- **Backend:** Flask web server executing mathematical algorithms in pure Python/NumPy/SciPy.
- **Data/APIs:** OpenStreetMap Nominatim for geocoding, Open-Meteo APIs for elevations and rainfall climatology.

---

## 3. Features
1. **Interactive Geocoder:** Resolves villages and landmark names to latitude/longitude.
2. **Dynamic DEM Extraction:** Generates an elevation grid (up to 15x15) around target coordinates via Open-Meteo or loads a pre-loaded synthetic profile.
3. **Slope Gradient Analysis:** Computes steepness to ensure structural embankment safety.
4. **D8 Drainage Flow Routing:** Maps flow vector paths to neighbor cells of steepest descent.
5. **Upstream Catchment Boundaries:** Traces flow directions backward using BFS to delineate total drainage area.
6. **Rainfall & Runoff Modeling:** Connects to archive weather data to sum precipitation and calculate runoff volume.
7. **Pond Dimension Sizing:** Sizes rectangular excavation dimensions ($L \times W \times D$) based on soil coefficients and target volume limits.
8. **MCDA Scoring Engine:** Evaluates candidate grid cells using adjustable weights for elevation, slope, and flow accumulation.
9. **Interactive Map Overlays:** Allows toggling between Suitability, Elevation, Slope, and Flow accumulation rasters instantly on Leaflet.
10. **Demo Mode:** Built-in synthetic valley basin configuration to demonstrate functionality immediately without internet or external requests.

---

## 4. Technology Stack
- **Web Server:** Python 3 + Flask
- **Math & Science:** NumPy, SciPy, Pandas
- **APIs:** Requests, Python-dotenv
- **Frontend Map:** Leaflet.js
- **Icons & Fonts:** FontAwesome 6, Google Fonts (Inter, Space Grotesk)

---

## 5. Mathematical Formulas & GIS Algorithms

### A. Slope Calculation (Central Differences)
To find the slope at cell $(i, j)$ with spacing $S$ (meters):
- **Change in X (West-East):**  
  $$\frac{dz}{dx} = \frac{elevation_{i, j+1} - elevation_{i, j-1}}{2 \cdot S}$$
- **Change in Y (North-South):**  
  $$\frac{dz}{dy} = \frac{elevation_{i+1, j} - elevation_{i-1, j}}{2 \cdot S}$$
- **Slope Magnitude (degrees):**  
  $$Slope(^{\circ}) = \arctan\left(\sqrt{\left(\frac{dz}{dx}\right)^2 + \left(\frac{dz}{dy}\right)^2}\right) \times \frac{180}{\pi}$$
*(Boundary cells utilize first-order one-sided differences).*

### B. D8 Flow Routing
For each cell, slope gradient is evaluated for all 8 surrounding neighbors:
- **Slope to neighbor $k$:**  
  $$Slope_k = \frac{elevation_{cell} - elevation_{neighbor\_k}}{S \cdot f_k}$$
  where $f_k = 1.0$ for orthogonal neighbors (N, S, E, W) and $f_k = \sqrt{2}$ for diagonal neighbors (NE, NW, SE, SW).
- The cell drains to the neighbor cell with the maximum positive slope. Sinks (depressions/pits) are cells with no lower neighbors (direction code: `-1`).

### C. Flow Accumulation & Catchment BFS Tracing
- **Flow Accumulation:** Cells are sorted descending by elevation. Each cell passes its accumulated count (starting at 1.0) to its designated D8 downstream neighbor. Sinks accumulate flow but do not pass it on.
- **Catchment Delineation:** Starting from a target candidate cell, we run a Breadth-First Search (BFS) on the reverse flow network. Any neighboring cell whose flow vector points to the current cell is added to the catchment set.
  $$\text{Catchment Area } (m^2) = \text{Count of catchment cells} \times S^2$$

### D. Simplified Runoff Volume (Rational Equation)
Estimated annual runoff volume ($Q$) draining to the pond site:
$$Q (m^3) = P (m) \times A (m^2) \times C$$
- $P$ is annual precipitation in meters ($P = \text{Rainfall in mm} / 1000$)
- $A$ is the catchment area in square meters.
- $C$ is the dimensionless Runoff Coefficient based on soil infiltration rate:
  - Sandy Soil ($C = 0.15$)
  - Loam Soil ($C = 0.35$)
  - Clay Soil ($C = 0.55$)
  - Forested ($C = 0.12$)
  - Urban/Impervious ($C = 0.80$)

### E. Pond Capacity & Excavation Dimensions
The pond is sized to store a fraction of the annual runoff volume ($V_{target} = 0.15 \times Q$), capped between $200\text{ m}^3$ (minimum practical size) and $5000\text{ m}^3$ (village limit).
- Assuming standard depth ($D = 2.5\text{ m}$) and a length-to-width ratio of $1.5 : 1$:
  $$\text{Pond Area } (m^2) = \frac{V_{target}}{D}$$
  $$Width (m) = \sqrt{\frac{\text{Pond Area}}{1.5}}$$
  $$Length (m) = 1.5 \times Width$$

### F. Multi-Criteria Suitability Scoring
Every cell is ranked on a scale of $0 - 100$:
$$\text{Score} = w_{elev} \cdot S_{elev} + w_{slope} \cdot S_{slope} + w_{accum} \cdot S_{accum}$$
- $S_{elev} = \frac{elev_{max} - elev}{elev_{max} - elev_{min}} \times 100$ (Low elevation preferred)
- $S_{slope} = \max\left(0, \left(1 - \frac{\text{slope}}{15}\right) \times 100\right)$ (Flat slope preferred, slopes $> 15^{\circ}$ get 0 suitability)
- $S_{accum} = \frac{\ln(flow\_accum)}{\ln(max\_accum)} \times 100$ (Log-scaled accumulation preferred)

---

## 6. Installation & Environment Setup

### Prerequisites
Make sure Python 3.8+ and `pip` are installed on your machine.

### Setup Instructions
1. **Clone or download the project files** to your local directory.
2. **Open a terminal** inside the root project directory:
   ```bash
   cd AI-POND
   ```
3. **Create a virtual environment** (recommended):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
4. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
5. **Configure Environment Variables:**
   Rename the `.env.example` file to `.env` or create it manually:
   ```bash
   cp .env.example .env
   ```

---

## 7. How to Run & Test
Start the local Flask development server:
```bash
python3 app.py
```
By default, the server launches on **http://localhost:5000**. Open this address in any modern web browser to access the planner interface.

---

## 8. CONFIGURATION REQUIRED FROM USER
This application is fully functional out of the box. Because it connects to public APIs, **no commercial API keys (like Google Maps or ArcGIS) are required**.

However, to comply with OpenStreetMap's Nominatim usage policies and avoid request throttling, please verify the following:
1. **Custom User-Agent:**
   Open the `.env` file in the root directory and ensure the `GEOCONTROLLER_USER_AGENT` variable is defined:
   ```env
   GEOCONTROLLER_USER_AGENT=AI-VillagePondPlanner/1.0 (contact: college-project@domain.com)
   ```
   *Nominatim requires a descriptive user agent to identify your request origin and prevent blocking.*

---

## 9. How to Use the Dashboard
1. **Run a Quick Demo (Offline Friendly):**
   Ensure the **DEMO MODE** checkbox is checked in the sidebar. Click **"Run AI Pond Analysis"**. The system instantly renders a synthetic valley basin showing contour gradients, flow paths, catchment areas, and candidate markers.
2. **Analyze a Real Location:**
   - Uncheck the **DEMO MODE** checkbox.
   - Enter a village or location in the search bar (e.g. `"Malpura, Rajasthan"` or `"Palampur, Himachal Pradesh"`). Click search or hit Enter.
   - The geocoder will update the Latitude and Longitude coordinates.
   - Adjust target pond depth, runoff soil type, or MCDA criteria weights (moving a weight slider dynamically adjusts others to sum to 100%).
   - Click **"Run AI Pond Analysis"**. The server fetches real elevation grids from the Open-Meteo elevation model and weather archives to generate real field planning parameters!
3. **Toggle Map Layers:**
   Move your mouse over the Legend control panel on the map to switch raster views between Suitability, Elevation, Slope, and Flow accumulation.
4. **Inspect Candidates:**
   Click any candidate marker (numbered 1-4) on the map. The map will display that site's specific drainage catchment outline and update the metrics card with exact sizing specs.

---

## 10. API Documentation

### `GET /api/geocode`
Resolves address search queries to coordinates.
- **Query Parameter:** `query` (string)
- **Response (200 OK):**
  ```json
  {
    "lat": 26.2974,
    "lon": 75.3804,
    "display_name": "Malpura, Tonk District, Rajasthan, India"
  }
  ```

### `POST /api/analyze`
Executes terrain routing, catchment, runoff, and suitability scoring.
- **Request Body (JSON):**
  ```json
  {
    "latitude": 26.2974,
    "longitude": 75.3804,
    "cell_spacing": 50,
    "grid_size": 11,
    "soil_type": "loam_soil",
    "pond_depth": 2.5,
    "target_fraction": 0.15,
    "demo_mode": false,
    "weights": {
      "elevation": 25,
      "slope": 35,
      "accumulation": 40
    }
  }
  ```
- **Response (200 OK):** Returns metadata, raw 2D grid matrix lists (for map heatmaps), candidate site indices, catchment coordinates, and recommended dimensions.

---

## 11. Limitations & Future Improvements
- **Simplified Hydraulics:** Calculations use a single runoff coefficient. A production system would use the NRCS Curve Number method with daily storm logs.
- **First-Order Pond Shapes:** Assumes rectangular boxes. Real pond construction requires trapezoidal cuts with stable side slopes (e.g. 1.5:1 or 2:1 side slope ratio).
- **Static Resolution:** Grid points are spacing-based approximations. Integrations with high-resolution satellite DEMs (ALOS/SRTM/LiDAR) via GeoTIFF file uploads would yield finer output.
- **Land Ownership:** Does not check land tenure. Official cadastral surveys must be performed to verify community vs. private land holdings.
