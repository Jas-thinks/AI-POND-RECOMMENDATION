# AI Pond Intelligence Platform
**Geospatial Decision Support System for Village Pond Planning**

The AI Pond Intelligence Platform is a geospatial decision support platform designed for preliminary planning and ranking of village pond locations. It integrates terrain processing, hydrological modeling, rainfall estimation, runoff calculation, beneficiary-proximity analysis, land-use filtering, and TOPSIS-based multi-factor suitability scoring into an interactive dark command-center web application.

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
12. [Candidate Ranking (TOPSIS)](#12-candidate-ranking-topsis)
13. [Output Files](#13-output-files)
14. [Installation](#14-installation)
15. [Running Backend](#15-running-backend)
16. [Running Frontend](#16-running-frontend)
17. [Project Structure](#17-project-structure)
18. [Ground-Truth Validation](#18-ground-truth-validation)
19. [Limitations](#19-limitations)
20. [Future Work](#20-future-work)

---

## 1. Project Overview
The platform addresses rural water scarcity by automating candidate pond site selection using geospatial calculations. It processes topographic and hydrological parameters to calculate surface runoff, delineate catchment areas, estimate storage capacities, measure proximity to the communities that would use the pond, and output ranked candidate sites on a Leaflet map workspace.

## 2. Problem Statement
Manual identification of optimal pond locations in rural regions requires evaluating complex topographical features, slope gradients, drainage networks, land encumbrances, rainfall volume, and the location of the villages that will rely on the water. Without automated decision support systems, site selection can lead to ineffective water storage or high construction costs. This platform standardizes terrain and hydrological analysis to support decision-making.

## 3. Features
- **Dual Input Modes**: Direct location lookup via geocoding or manual upload of KML/KMZ contour files.
- **Automated DEM Handling**: OpenTopography DEM fetch for location mode; elevation interpolation (Barycentric / IDW) for uploaded contours.
- **Hydrological Engine**: Sink filling via priority-flood algorithm, D8 flow direction, flow accumulation via Kahn topological sorting, and reverse-BFS catchment delineation.
- **Beneficiary Proximity**: Measures each pond site's distance to villages and residential buildings, so an ideal site nobody can reach is never ranked highly.
- **Land Clearance & Exclusions**: OpenStreetMap integration to filter out existing water bodies, rivers, buildings, and transportation networks, plus measured exclusion-clearance evidence for every surviving site.
- **Multi-Factor Ranking (TOPSIS)**: Five independent factors (terrain, hydrology, water storage, practicality, beneficiaries) combined with the TOPSIS multi-criteria method.
- **Spatial Diversification**: The final top-N is guaranteed to be spatially spread out, with nearby alternatives kept visible rather than discarded.
- **Per-Rank Justification**: Every candidate in the top-N comes with a factor-by-factor proof of why it beat the next-ranked candidate.
- **Catchment Siltation & Downstream Conflict**: Reported-but-unranked indicators for maintenance outlook and downstream water-security risk.
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
- **Pydantic v2** (Validated, enforceable response schemas)

### Frontend
- **Vite 5** (Asset bundler and development proxy)
- **Vanilla HTML5 / CSS3 / JavaScript (ES6+)**
- **Leaflet 1.9.4** (Interactive mapping library)
- **Google Fonts** (Inter & JetBrains Mono)

## 6. Analysis Workflow
1. **Define Analysis Area**: Specify a place name or upload a KML contour file.
2. **Terrain Engine**: Generate grid DEM and derive slope matrix.
3. **Hydrology Engine**: Compute sink-filled DEM, D8 flow directions, flow accumulation grid, and per-candidate catchment delineation.
4. **Hard Exclusions**: Apply OSM land filtering (water, waterway, road, building) and DEM-derived drainage-channel buffers; record raw geometry distances for later clearance evidence.
5. **Five Factor Scoring**: For each surviving candidate, compute terrain, hydrology, water/storage, practical, and beneficiary-proximity factor scores.
6. **Concurrent Pre-fetch**: The network-bound per-candidate analyses (soil, land-use/land-cover) and the CPU-heavy geometry steps (nearest-road distance, exclusion-clearance distances) are batched in a bounded thread pool so that dozens of candidate requests overlap instead of running serially. This is the main lever on wall-clock time — the pipeline was I/O-bound before this change.
7. **TOPSIS Ranking**: Combine the five factors into a single 0-1 closeness-to-ideal score and rank all candidates.
8. **Spatial Diversification**: Select a spread-out final top-N, keeping skipped nearby sites visible.
9. **Justification & Reporting**: Attach a per-rank justification, exclusion-clearance evidence, siltation risk, and downstream-conflict flags to every chosen candidate.

## 7. Input Methods
- **Select Location**: Input village or district name (e.g., "IIT Bhilai, Kutelabhata, Chhattisgarh, India") and select search radius (0.5 km to 10 km).
- **Upload Contours**: Drop or select a valid `.kml` or `.kmz` file containing contour LineString vectors.

## 8. Location Analysis
When analyzing by location:
1. Nominatim / Open-Meteo geocodes the input string to latitude and longitude.
2. OpenTopography API downloads DEM raster tiles (Copernicus 30m / GLO-30).
3. System extracts elevation grid and derives contours and hydrology models, then runs the identical Stage-1-onward pipeline used for contour uploads.

## 9. Contour / KML Analysis
When uploading a KML file:
1. Parsed coordinates and elevation attributes are converted into 2D points.
2. Grid bounds are constructed at user-defined spatial resolution (default 10m).
3. Grid values are interpolated to construct a synthetic Digital Elevation Model.
4. The same build_dem → hydrology → exclusion → ranking pipeline runs for both input modes, so results are directly comparable.

## 10. DEM Generation
- Interpolation uses scipy griddata (Clough-Tocher / Linear / Nearest fallback).
- Elevation range and grid resolution are dynamically computed and displayed in the Terrain Analysis overlay.

## 11. Hydrology Analysis
- **Sink Filling**: Priority-queue flood algorithm eliminates artificial depressions.
- **Flow Direction**: D8 algorithm computes steepest descent path (1, 2, 4, 8, 16, 32, 64, 128 encoding).
- **Flow Accumulation**: Iterative Kahn topological sort accumulates upstream contributing cell counts.
- **Catchment Delineation**: Reverse breadth-first search (BFS) traces all cells draining into each candidate sink.
- **Drainage-Channel Safety**: DEM-derived high-accumulation corridors are treated as hard exclusions with a configurable percentile and buffer.

## 12. Candidate Ranking (TOPSIS)
Each surviving candidate is scored on five independent factors (0-100 raw scores, normalised to 0-1:

- **Terrain** — slope, elevation, and local relief from the DEM.
- **Hydrology** — the candidate's own delineated catchment size and its proximity to strong drainage channels.
- **Water Storage** — the smaller of estimated runoff supply and achievable storage capacity (`min(runoff, storage)`).
- **Practical** — soil suitability, land-use suitability, and road accessibility.
- **Beneficiary** — proximity to villages and residential buildings, using a distance-decay score (see `MAX_USEFUL_DISTANCE_M` in `beneficiary_service.py`).

The five factors are combined using **TOPSIS** (Technique for Order of Preference by Similarity to Ideal Solution):

1. Build a decision matrix (`rows = candidates`, `columns = factors`).
2. Apply weights (renormalised per candidate when a factor is missing) to get a weighted normalised matrix `v_ij = w_j * x_ij`.
3. Determine the ideal best `A+` (column maximum) and ideal worst `A-` (column minimum).
4. Compute Euclidean distance to `A+` (`S_plus`) and `A-` (`S_minus`) for each candidate.
5. Closeness coefficient `C_i = S_minus / (S_plus + S_minus)` becomes the final 0-1 score (1 = closest to ideal).
6. Rank candidates by `C_i` descending.

Default weights: terrain 0.255, hydrology 0.2125, water storage 0.2125, practical 0.17, beneficiary 0.15 (weights always sum to 1.0; a missing factor is dropped and the rest renormalised proportionally).

After ranking, a **spatial diversification pass** ensures the final top-N sites are more than `MIN_FINAL_SEPARATION_M` (default 400 m) apart. High-ranked candidates that sit within that radius of a selected site are kept in a separate `nearby_alternatives` list rather than silently discarded, and the number skipped is reported.

Every candidate in the top-N carries a `site_justification` object: its TOPSIS geometry (`S_plus`, `S_minus`, `C_i`), a factor-by-factor `margin_vs_next_rank` showing why it beat the next-ranked candidate, measured `exclusion_clearance` distances per feature type, `catchment_landcover` siltation risk, and a `downstream_conflict` flag — so the whole ranking is auditable and reproducible by hand.

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
│   ├── services/               # DEM fetch, land filter, beneficiary proximity,
│   │                           #   accessibility, soil, land-use, rainfall,
│   │                           #   water availability, storage, TOPSIS scoring,
│   │                           #   watershed (siltation + downstream conflict)
│   ├── terrain/                # DEM generation & slope computation
│   ├── schemas/                # Pydantic response models (validated, enforced)
│   └── validation/             # Standalone ground-truth validation script
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

## 18. Ground-Truth Validation
A standalone validation script at `backend/validation/ground_truth_check.py` compares the pipeline's recommendations against known real-world sites:

1. Fill `GROUND_TRUTH` (or pass `--points file.json`) with real `{lat, lon, label, note}` values, where `label` is `existing_pond` or `known_bad_site`.
2. Run `python -m backend.validation.ground_truth_check`.
3. The script runs each point through the real DEM-fetch → contour → `analyze_contour_file` → TOPSIS pipeline, records whether it was hard-excluded and its rank percentile, and writes `validation_report.json` plus a printed summary.

The summary reports how many `existing_pond` points landed in the top 30% of candidates and how many `known_bad_site` points were correctly excluded or landed in the bottom 30%. The coordinate list is intentionally left empty; it must be filled in with real locations — nothing is fabricated.

## 19. Limitations
- **Resolution**: Global DEM datasets (Copernicus 30m) provide general terrain guidance but lack high-precision micro-topography.
- **Ground Truth Verification**: OpenStreetMap land exclusion filters depend on mapped spatial features. Field survey and soil testing are required prior to engineering execution.
- **Missing Factors**: When external data (e.g. rainfall, soil, OSM settlement density) is unavailable, the affected factor is dropped and its weight renormalised over the remaining factors rather than replaced with a fabricated default.

## 20. Future Work
- Integration of high-resolution LiDAR / drone survey elevation rasters.
- Detailed geotechnical soil permeability profiles.
- Feeding catchment siltation and downstream-conflict indicators into a full watershed-management workflow.

---
*Developed for academic evaluation and geospatial decision support research.*
