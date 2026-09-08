# Coding

- Prefers minimal, targeted changes over rewriting the whole project. Confidence: 0.8
- Prefers not adding unnecessary dependencies — use the standard library where reasonable. Confidence: 0.7
- Prefers preserving core/scientific algorithm logic; does not want results faked or numerics altered merely for optimization. Confidence: 0.7
- When optimizing or adding features, wants explicit verification that the API response schema, candidate ranking/scoring, hydrology calculations, and external integrations are byte-for-byte unchanged (no unintentional behavioral/contract changes); explicitly prefers NOT adding required top-level response fields that would break the Pydantic model. Confidence: 0.8
- Prefers the backend to remain resilient (correct 4xx/500 JSON error handling instead of empty responses). Confidence: 0.6
- Refuses to hardcode/fake data (coordinates, roads, buildings, water bodies); wants real geospatial data from existing OSM/land-use infrastructure, inspecting existing services before implementing. Confidence: 0.95
- Prefers configurable parameters (documented defaults) over values hardcoded throughout the code. Confidence: 0.7
- Prefers batch fetching/geometric processing over one HTTP request per raster cell or per feature; performance-focused so slow per-item loops don't recur. Confidence: 0.7
- Wants user-facing explanations of why a decision was made (e.g. why an area was excluded) without leaking internal implementation details. Confidence: 0.6
- For frontend/UI improvements, prefers pure styling/CSS changes that preserve all existing functionality — no changes to JS logic, API calls, or HTML structure, and post-edit verification that behavior is untouched. Confidence: 0.8
- UI aesthetic preference: polished, modern, clean, professional look suited to an environmental/GIS app — a green/earth/water palette, subtle shadows, borders, and rounded corners, plus improved spacing, alignment, typography, and visual hierarchy. Confidence: 0.8
- For the dark GIS/environmental UI, wants a dark interface that is explicitly NOT flat — layered surface hierarchy, strong visual hierarchy, readable typography, subtle depth/shadows, and refined corners, but uses restrained/fast transitions and avoids excessive neon/glow effects or over-glossy "floating glass card" styling. Confidence: 0.8