# AI Pond Intelligence Platform
**Geospatial Decision Support System for Village Pond Planning**

The AI Pond Intelligence Platform is a geospatial decision support platform designed for preliminary planning and ranking of village pond locations. It integrates terrain processing, hydrological modeling, rainfall estimation, runoff calculation, land-use filtering, and multi-factor suitability scoring into an interactive dark command-center web application.

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Problem Statement](#2-problem-statement)
3. [Features](#3-features)
4. [Architecture](#4-architecture)
5. [Technology Stack](#5-technology-stack)
6. [Analysis Workflow](#6-analysis-workflow)
7. [Input Methods](#7-input-methods)
8. [Location Analysis](#8-location-analysis)
9. [Contour / KML Analysis](#9-contour--kml-analysis)
10. [DEM Generation](#10-dem-generation)
11. [Hydrology Analysis](#11-hydrology-analysis)
12. [Candidate Ranking](#12-candidate-ranking)
13. [Output Files](#13-output-files)
14. [Installation](#14-installation)
15. [Running Backend](#15-running-backend)
16. [Running Frontend](#16-running-frontend)
17. [Project Structure](#17-project-structure)
18. [Limitations](#18-limitations)
19. [Future Work](#19-future-work)

---

## 1. Project Overview
The platform addresses rural water scarcity by automating candidate pond site selection using geospatial calculations. It processes topographic and hydrological parameters to calculate surface runoff, delineate catchment areas, estimate storage capacities, and output ranked candidate sites on a Leaflet map workspace.

## 2. Problem Statement
Manual identification of optimal pond locations in rural regions requires evaluating complex topographical features, slope gradients, drainage networks, land encumbrances, and rainfall volume. Without automated decision support systems, site selection can lead to ineffective water storage or high construction costs. This platform standardizes terrain and hydrological analysis to support decision-making.

## 3. Features
- **Dual Input Modes**: Direct location lookup via geocoding or manual upload of KML/KMZ contour files.
- **Automated DEM Handling**: OpenTopography DEM fetch for location mode; elevation interpolation (Barycentric / IDW) for uploaded contours.
- **Hydrological Engine**: Sink filling via priority-flood algorithm, D8 flow direction, flow accumulation via Kahn topological sorting, and reverse-BFS catchment delineation.
- **Land Clearance & Exclusions**: OpenStreetMap integration to filter out existing water bodies, rivers, buildings, and transportation networks.
- **Multi-Factor Ranking**: Combined evaluation of terrain slope, flow accumulation, storage potential, and environmental constraints.
- **Interactive Command-Center UI**: Leaflet map workspace with candidate strip, layer toggle controls, candidate inspector panel, and detailed modal metrics.
- **Data Export**: Generated DEM GeoTIFF, contour KML, and GeoJSON layer downloads.

## 4. Architecture
```
┌─────────────────────────────────┐         ┌─────────────────────────────────┐
│         FRONTEND (Vite)         │         │          BACKEND (FastAPI)       │
│      http://localhost:5173      │  HTTP   │       http://localhost:8000      │
│                                 │ ──────► │                                 │
│  - index.html                   │  /api/* │  - FastAPI app                  │
│  - css/command-center.css       │         │  - /api/analyzeContour (KML)     │
│  - js/{app,command-center,      │         │  - /api/analyzeLocation         │
│       location}.js              │         │  - /api/health                  │
│  - Leaflet map workspace        │         │  - Static asset mounts          │
└─────────────────────────────────┘         └─────────────────────────────────┘
                                                        │
                                                        ▼
                                              ┌─────────────────────┐
                                              │  External Services  │
                                              │  - OpenTopography   │
                                              │  - OpenStreetMap    │
                                              │  - Nominatim        │
                                              │  - Open-Meteo       │
                                              └─────────────────────┘
```

## 5. Technology Stack
### Backend
- **Python 3.10+**
- **FastAPI** + **Uvicorn**
- **NumPy** & **SciPy** (Matrix computations, grid interpolation, spatial algorithms)
- **Rasterio** (GeoTIFF generation and spatial raster handling)
- **Shapely** & **PyProj** (Vector geometry and coordinate transformations)
- **HTTPX** (Async requests to external APIs)

### Frontend
- **Vite 5** (Asset bundler and development proxy)
- **Vanilla HTML5 / CSS3 / JavaScript (ES6+)**
- **Leaflet 1.9.4** (Interactive mapping library)
- **Google Fonts** (Inter & JetBrains Mono)

## 6. Analysis Workflow
1. **Define Analysis Area**: Specify a place name or upload a KML contour file.
2. **Terrain Engine**: Generate grid DEM and derive slope matrix.
3. **Hydrology Engine**: Compute sink-filled DEM, D8 flow directions, flow accumulation grid, and catchments.
4. **Pond Intelligence**: Extract local flow accumulation peaks, filter excluded zones, calculate storage volume, compute suitability scores, and display candidate sites.

## 7. Input Methods
- **Select Location**: Input village or district name (e.g., "IIT Bhilai, Kutelabhata, Chhattisgarh, India") and select search radius (0.5 km to 10 km).
- **Upload Contours**: Drop or select a valid `.kml` or `.kmz` file containing contour LineString vectors.

## 8. Location Analysis
When analyzing by location:
1. Nominatim / Open-Meteo geocodes the input string to latitude and longitude.
2. OpenTopography API downloads DEM raster tiles (Copernicus 30m / GLO-30).
3. System extracts elevation grid and derives contours and hydrology models.

## 9. Contour / KML Analysis
When uploading a KML file:
1. Parsed coordinates and elevation attributes are converted into 2D points.
2. Grid bounds are constructed at user-defined spatial resolution (default 10m).
3. Grid values are interpolated to construct a synthetic Digital Elevation Model.

## 10. DEM Generation
- Interpolation uses scipy griddata (Clough-Tocher / Linear / Nearest fallback).
- Elevation range and grid resolution are dynamically computed and displayed in the Terrain Analysis overlay.

## 11. Hydrology Analysis
- **Sink Filling**: Priority-queue flood algorithm eliminates artificial depressions.
- **Flow Direction**: D8 algorithm computes steepest descent path (1, 2, 4, 8, 16, 32, 64, 128 encoding).
- **Flow Accumulation**: Iterative Kahn topological sort accumulates upstream contributing cell counts.
- **Catchment Delineation**: Reverse breadth-first search (BFS) traces all cells draining into each candidate sink.

## 12. Candidate Ranking
Pond site candidates are selected using local accumulation maxima and scored (0 to 100):
- **Flow Accumulation Weight**: Higher upstream contributing area increases score.
- **Terrain Slope Weight**: Moderate slopes (1-5%) preferred; steep or flat terrain penalization.
- **Storage Potential**: Volumetric capacity calculated from runoff formula $Q = P \times A \times C$.
- **Land Status**: Unencumbered land receives higher priority.

## 13. Output Files
Generated outputs are stored in `data/generated/` and accessible via download links:
- **GeoTIFF DEM**: Standard GIS raster file.
- **Contour KML**: Vector contour lines formatted for Google Earth / GIS viewing.
- **Contour GeoJSON**: Vector contour features for web mapping.

## 14. Installation
```bash
# Clone repository
cd AI-based-Village-Pond-Planning-System

# Set up Python virtual environment
python3 -m venv backend/venv
source backend/venv/bin/activate
pip install -r requirements.txt

# Set up frontend dependencies
cd frontend
npm install
cd ..
```

## 15. Running Backend
```bash
source backend/venv/bin/activate
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
Backend will be available on `http://localhost:8000`.

## 16. Running Frontend
```bash
cd frontend
npm run dev
```
Frontend will be available on `http://localhost:5173`. API requests `/api/*` are automatically proxied to `http://localhost:8000`.

## 17. Project Structure
```
AI-based-Village-Pond-Planning-System/
├── backend/
│   ├── main.py                 # FastAPI application entry point
│   ├── api/                    # Route handlers (contour_routes, location_routes)
│   ├── hydrology/              # Flow direction, accumulation, sink fill, catchment
│   ├── pond/                   # Candidate selection & water balance metrics
│   ├── services/               # DEM fetch, land filter, geocoding, rainfall
│   ├── terrain/                # DEM generation & slope computation
│   └── schemas/                # Pydantic response models
├── frontend/
│   ├── index.html              # Main HTML page shell
│   ├── css/                    # Stylesheets (command-center.css, style.css)
│   ├── js/                     # Scripts (app.js, command-center.js, location.js)
│   ├── package.json            # Node dependencies
│   └── vite.config.js          # Vite configuration
├── data/                       # Cache & generated output files
├── contours_1m.kml             # Sample contour test file
├── requirements.txt            # Python dependencies
└── README.md                   # Academic project documentation
```

## 18. Limitations
- **Resolution**: Global DEM datasets (Copernicus 30m) provide general terrain guidance but lack high-precision micro-topography.
- **Ground Truth Verification**: OpenStreetMap land exclusion filters depend on mapped spatial features. Field survey and soil testing are required prior to engineering execution.

## 19. Future Work
- Integration of high-resolution LiDAR / drone survey elevation rasters.
- Detailed geotechnical soil permeability profiles.
- Soil conservation and siltation estimation models.

---
*Developed for academic evaluation and geospatial decision support research.*
