/**
 * command-center.js
 * Geo-Spatial Intelligence Command Center UI
 * 
 * Manages:
 * - Input mode switching (location vs upload)
 * - Workflow step progression
 * - Candidate inspector panel
 * - Candidate strip
 * - Analysis drawer
 * - Map layer controls
 * - Progress/loading states
 * - No-results state
 * 
 * Integrates with existing app.js and location.js
 */

// =========================================================
// GLOBAL STATE
// =========================================================

let currentMode = 'location';
let currentStep = '01';
let selectedCandidateId = null;
let currentAnalysisData = null;
let drawerOpen = false;
let inspectorVisible = false;

// =========================================================
// UTILITY
// =========================================================

function fmtNum(value, digits = 2) {
  if (value === null || value === undefined) return 'N/A';
  return Number(value).toFixed(digits);
}

function fmtInt(value) {
  if (value === null || value === undefined) return 'N/A';
  return Math.round(Number(value)).toLocaleString();
}

function fmtPct(value, digits) {
  if (digits === undefined) digits = 0;
  if (value === null || value === undefined) return 'N/A';
  return (Number(value) * 100).toFixed(digits) + '%';
}

function fmtBytes(n) {
  if (n == null) return 'N/A';
  const num = Number(n);
  if (num >= 1e6) return (num / 1e6).toFixed(2) + ' Mm\u00b3';
  if (num >= 1e3) return (num / 1e3).toFixed(1) + ' km\u00b3';
  return Math.round(num) + ' m\u00b3';
}

function getMFScore(candidate) {
  if (candidate && candidate.multi_factor_score && candidate.multi_factor_score.final_score != null) {
    return candidate.multi_factor_score;
  }
  return null;
}

function getConfidenceClass(conf) {
  if (!conf) return '';
  return 'mf-inspector__confidence--' + String(conf).toLowerCase();
}

function getFactorCard(factorName, factorData) {
  if (!factorData) {
    return '<div class="mf-factor-card mf-factor-card--unavailable">' +
      '<div class="mf-factor-card__name">' + factorName + '</div>' +
      '<div class="mf-factor-card__score">Data unavailable</div>' +
      '<div class="mf-factor-card__note">Excluded from final weight</div>' +
      '</div>';
  }
  var score = factorData.suitability_score || factorData.climate_score ||
              factorData.accessibility_score || factorData.water_availability_score || 0;
  var weight = factorData.weight || 0;
  var note = factorData.note || factorData.recommendation || factorData.soil_texture ||
             factorData.dominant_land_use || '';
  return '<div class="mf-factor-card">' +
    '<div class="mf-factor-card__name">' + factorName + '</div>' +
    '<div class="mf-factor-card__row">' +
      '<div class="mf-factor-card__score">' + Math.round(Number(score)) + '/100</div>' +
      '<div class="mf-factor-card__weight">wt: ' + (weight ? (weight * 100).toFixed(0) + '%' : 'N/A') + '</div>' +
    '</div>' +
    '<div class="mf-factor-card__bar"><div class="mf-factor-card__bar-fill" style="width:' + Math.min(100, Math.max(0, score)) + '%"></div></div>' +
    '<div class="mf-factor-card__note">' + (note || '') + '</div>' +
    '</div>';
}

function buildMFFactorCards(candidate) {
  var mf = getMFScore(candidate);
  if (!mf || !mf.factor_breakdown) return '<div class="constraints-empty">Multi-factor breakdown unavailable</div>';
  var fb = mf.factor_breakdown;
  var factors = [
    ['Terrain & Hydrology', fb.terrain],
    ['Catchment', fb.catchment],
    ['Soil', fb.soil],
    ['Rainfall / Climate', fb.rainfall],
    ['Land Use', fb.land_use],
    ['Accessibility', fb.accessibility]
  ];
  var cards = [];
  for (var i = 0; i < factors.length; i++) {
    cards.push(getFactorCard(factors[i][0], factors[i][1]));
  }
  return cards.join('');
}

function buildSection(title, metrics, extraHtml) {
  var html = '<div class="drawer-section"><div class="drawer-section__title">' + title + '</div>';
  if (metrics) html += '<div class="drawer-section__grid">' + metrics + '</div>';
  if (extraHtml) html += extraHtml;
  html += '</div>';
  return html;
}

function metric(key, val) {
  return '<div class="drawer-metric"><span class="drawer-metric__key">' + key + '</span><span class="drawer-metric__val">' + val + '</span></div>';
}

function buildConfidenceSection(data) {
  var dc = data.data_confidence;
  if (!dc) return '';
  var conf = String(dc.overall_confidence || 'N/A').toUpperCase();
  var confClass = 'confidence-badge--' + String(dc.overall_confidence || '').toLowerCase();
  var avail = dc.data_sources_available || [];
  var miss = dc.data_sources_missing || [];
  var lims = (dc.limitations || []).slice(0, 5);
  var html = '<div class="confidence-section">' +
    '<div class="confidence-section__header">' +
      '<div class="confidence-section__title">Data Confidence</div>' +
      '<div class="confidence-badge ' + confClass + '">' + conf + '</div>' +
    '</div>' +
    '<div class="confidence-lists">' +
      '<div class="confidence-list confidence-list--available">' +
        '<div class="confidence-list__label">Available (' + avail.length + ')</div>' +
        '<ul>' + avail.map(function(s) { return '<li>' + s + '</li>'; }).join('') + '</ul>' +
      '</div>' +
      '<div class="confidence-list confidence-list--missing">' +
        '<div class="confidence-list__label">Missing (' + miss.length + ')</div>' +
        '<ul>' + (miss.length > 0 ? miss.map(function(s) { return '<li>' + s + '</li>'; }).join('') : '<li>None</li>') + '</ul>' +
      '</div>' +
    '</div>';
  if (lims.length > 0) {
    html += '<div class="confidence-limitations"><ul>' +
      lims.map(function(l) { return '<li>' + l + '</li>'; }).join('') + '</ul></div>';
  }
  html += '</div>';
  return html;
}

function buildConstraintsSection(candidate) {
  var ec = candidate.environmental_constraints;
  if (!ec) return '';
  var hard = ec.hard_constraints || [];
  var soft = ec.soft_constraints || [];
  var warns = ec.warnings || [];
  var html = '<div class="constraints-section"><div class="drawer-section__title" style="margin-bottom:10px">Environmental Constraints</div>';
  if (hard.length > 0) html += '<div class="constraints-group constraints-group--hard"><div class="constraints-group__label">HARD CONSTRAINTS</div><ul class="constraints-group__list">' + hard.map(function(s) { return '<li>' + s + '</li>'; }).join('') + '</ul></div>';
  if (soft.length > 0) html += '<div class="constraints-group constraints-group--soft"><div class="constraints-group__label">SOFT CONSTRAINTS</div><ul class="constraints-group__list">' + soft.map(function(s) { return '<li>' + s + '</li>'; }).join('') + '</ul></div>';
  if (warns.length > 0) html += '<div class="constraints-group constraints-group--warnings"><div class="constraints-group__label">WARNINGS</div><ul class="constraints-group__list">' + warns.map(function(s) { return '<li>' + s + '</li>'; }).join('') + '</ul></div>';
  if (hard.length === 0 && soft.length === 0 && warns.length === 0) html += '<div class="constraints-empty">No environmental constraints triggered.</div>';
  html += '</div>';
  return html;
}

function buildSoilSection(candidate) {
  var s = candidate.soil_analysis;
  if (!s) return '';
  if (!s.available) return buildSection('Soil Analysis', '', '<div class="constraints-empty">Soil data unavailable: ' + (s.error || 'service failed') + '</div>');
  var m = metric('Source', s.source || 'N/A') +
          metric('Texture', s.soil_texture || 'N/A') +
          metric('Sand', s.sand_percent != null ? s.sand_percent + '%' : 'N/A') +
          metric('Silt', s.silt_percent != null ? s.silt_percent + '%' : 'N/A') +
          metric('Clay', s.clay_percent != null ? s.clay_percent + '%' : 'N/A') +
          metric('Permeability', s.permeability || 'N/A') +
          metric('Water Retention', s.water_retention || 'N/A') +
          metric('Score', s.suitability_score != null ? s.suitability_score + '/100' : 'N/A') +
          metric('Risk Level', s.risk_level || 'N/A');
  var extra = s.recommendation ? '<div class="drawer-metric" style="grid-column:1/-1;margin-top:6px"><span class="drawer-metric__key">Recommendation</span><span class="drawer-metric__val" style="font-size:11px">' + s.recommendation + '</span></div>' : '';
  return buildSection('Soil Analysis', m, extra);
}

function buildClimateSection(candidate, data) {
  var c = candidate.climate_analysis || data.climate_analysis;
  if (!c) return '';
  if (!c.available) return buildSection('Rainfall & Climate', '', '<div class="constraints-empty">Climate data unavailable: ' + (c.error || 'service failed') + '</div>');
  var m = metric('Source', c.source || 'N/A') +
          metric('Period', c.period || 'N/A') +
          metric('Annual Rainfall', c.annual_rainfall_mm != null ? c.annual_rainfall_mm + ' mm' : 'N/A') +
          metric('Monsoon Rainfall', c.monsoon_rainfall_mm != null ? c.monsoon_rainfall_mm + ' mm' : 'N/A') +
          metric('Reliability', c.rainfall_reliability || 'N/A') +
          metric('Seasonality', c.seasonality || 'N/A') +
          metric('Climate Score', c.climate_score != null ? c.climate_score + '/100' : 'N/A');
  var extra = c.recommendation ? '<div class="drawer-metric" style="grid-column:1/-1;margin-top:6px"><span class="drawer-metric__key">Recommendation</span><span class="drawer-metric__val" style="font-size:11px">' + c.recommendation + '</span></div>' : '';
  return buildSection('Rainfall & Climate', m, extra);
}

function buildLandUseSection(candidate) {
  var l = candidate.land_use_analysis;
  if (!l) return '';
  if (!l.available) return buildSection('Land Use', '', '<div class="constraints-empty">Land use unavailable: ' + (l.error || 'service failed') + '</div>');
  var m = metric('Source', l.source || 'N/A') +
          metric('Dominant', l.dominant_land_use || 'N/A') +
          metric('Score', l.suitability_score != null ? l.suitability_score + '/100' : 'N/A');
  var extra = '';
  if (l.restrictions && l.restrictions.length > 0) extra += '<div class="drawer-metric" style="grid-column:1/-1"><span class="drawer-metric__key">Restrictions</span><span class="drawer-metric__val" style="font-size:11px">' + l.restrictions.join('; ') + '</span></div>';
  var dist = l.land_use_distribution || {};
  Object.keys(dist).slice(0, 5).forEach(function(k) {
    m += metric(k, dist[k] + ' cells');
  });
  if (l.recommendation) extra += '<div class="drawer-metric" style="grid-column:1/-1;margin-top:6px"><span class="drawer-metric__key">Recommendation</span><span class="drawer-metric__val" style="font-size:11px">' + l.recommendation + '</span></div>';
  return buildSection('Land Use', m, extra);
}

function buildAccessibilitySection(candidate) {
  var a = candidate.accessibility_analysis;
  if (!a) return '';
  if (!a.available) return buildSection('Accessibility', '', '<div class="constraints-empty">Accessibility unavailable: ' + (a.error || 'service failed') + '</div>');
  var m = metric('Source', a.source || 'N/A') +
          metric('Nearest Road', a.nearest_road_distance_m != null ? a.nearest_road_distance_m + ' m' : 'N/A') +
          metric('Score', a.accessibility_score != null ? a.accessibility_score + '/100' : 'N/A') +
          metric('Access', a.construction_access || 'N/A');
  var extra = a.recommendation ? '<div class="drawer-metric" style="grid-column:1/-1;margin-top:6px"><span class="drawer-metric__key">Recommendation</span><span class="drawer-metric__val" style="font-size:11px">' + a.recommendation + '</span></div>' : '';
  return buildSection('Accessibility', m, extra);
}

function buildWaterAvailSection(candidate) {
  var w = candidate.water_availability;
  if (!w) return '';
  if (!w.available) return buildSection('Water Availability', '', '<div class="constraints-empty">Water availability unavailable: ' + (w.error || 'missing data') + '</div>');
  var m = metric('Catchment', w.catchment_area_m2 != null ? (w.catchment_area_m2 / 10000).toFixed(2) + ' ha' : 'N/A') +
          metric('Rainfall', w.annual_rainfall_mm != null ? w.annual_rainfall_mm + ' mm' : 'N/A') +
          metric('Runoff Coeff.', w.runoff_coefficient != null ? w.runoff_coefficient : 'N/A') +
          metric('Est. Runoff', fmtInt(w.estimated_annual_runoff_m3) + ' m3/yr') +
          metric('Confidence', w.confidence || 'N/A');
  var extra = '<div class="storage-disclaimer">Preliminary estimate. Formula: Annual Rainfall x Catchment Area x Runoff Coefficient. Engineering-grade precision requires field survey.</div>';
  return buildSection('Water Availability', m, extra);
}

function buildStorageSection(candidate) {
  var s = candidate.storage_estimation;
  if (!s) return '';
  if (!s.available) return buildSection('Storage Estimation', '', '<div class="constraints-empty">Storage estimation unavailable: ' + (s.error || 'missing data') + '</div>');
  var m = metric('Surface Area', s.estimated_surface_area_m2 != null ? s.estimated_surface_area_m2.toFixed(0) + ' m2' : 'N/A') +
          metric('Avg. Depth', s.estimated_average_depth_m != null ? s.estimated_average_depth_m + ' m' : 'N/A') +
          metric('Max Depth', s.estimated_max_depth_m != null ? s.estimated_max_depth_m + ' m' : 'N/A') +
          metric('Est. Volume', fmtInt(s.estimated_storage_volume_m3) + ' m3') +
          metric('Confidence', s.confidence || 'N/A');
  var extra = '<div class="storage-disclaimer">' + (s.disclaimer || 'Preliminary terrain-based estimate. Detailed field survey and geotechnical validation required.') + '</div>';
  return buildSection('Storage Estimation', m, extra);
}

function buildMultiFactorHero(candidate) {
  var mf = getMFScore(candidate);
  if (!mf) return '<div class="drawer-section"><div class="drawer-section__title">Final Multi-Factor Suitability</div><div class="constraints-empty">Multi-factor scoring unavailable</div></div>';
  var score = Math.round(Number(mf.final_score) || 0);
  var conf = String(mf.confidence || '').toUpperCase();
  var weights = mf.weights_used || {};
  var wlist = Object.keys(weights).map(function(k) { return k.toUpperCase() + ': ' + weights[k]; }).join(' | ');
  var avail = (mf.available_factors || []).map(function(f) { return f.toUpperCase(); }).join(', ');
  var unavail = (mf.unavailable_factors || []).map(function(f) { return f.toUpperCase(); }).join(', ');
  return '<div class="drawer-section">' +
    '<div class="drawer-section__title">Final Multi-Factor Suitability</div>' +
    '<div style="display:flex;align-items:center;gap:14px;margin-bottom:14px">' +
      '<div style="font-family:var(--font-mono);font-size:56px;font-weight:700;color:var(--selected);line-height:1;letter-spacing:-0.02em">' + score + '</div>' +
      '<div style="flex:1">' +
        '<div style="font-family:var(--font-mono);font-size:9px;letter-spacing:0.1em;text-transform:uppercase;color:var(--text-dim);margin-bottom:2px">FINAL SCORE / 100</div>' +
        '<div style="font-size:14px;color:var(--selected);font-weight:700;margin-bottom:4px">' + (mf.category || 'N/A') + '</div>' +
        '<div style="font-size:10px;color:var(--text-muted)">Confidence: <span class="confidence-badge confidence-badge--' + String(mf.confidence || '').toLowerCase() + '">' + conf + '</span></div>' +
      '</div>' +
    '</div>' +
    '<div class="mf-inspector__bar-wrap" style="margin-bottom:12px"><div class="mf-inspector__bar" style="width:' + score + '%"></div></div>' +
    '<div class="drawer-metric"><span class="drawer-metric__key">Weights Used</span><span class="drawer-metric__val" style="font-size:10px">' + wlist + '</span></div>' +
    '<div class="drawer-metric"><span class="drawer-metric__key">Available</span><span class="drawer-metric__val">' + (avail || 'None') + '</span></div>' +
    '<div class="drawer-metric"><span class="drawer-metric__key">Unavailable</span><span class="drawer-metric__val">' + (unavail || 'None') + '</span></div>' +
    '<div class="drawer-section__title" style="margin-top:16px;margin-bottom:10px;font-size:10px;letter-spacing:0.08em;text-transform:uppercase">Factor Breakdown</div>' +
    '<div class="mf-factors-grid">' + buildMFFactorCards(candidate) + '</div>' +
    '</div>';
}

function getRating(score) {
  if (score >= 95) return 'EXCELLENT';
  if (score >= 85) return 'VERY GOOD';
  if (score >= 75) return 'GOOD';
  if (score >= 65) return 'FAIR';
  if (score >= 50) return 'MODERATE';
  return 'POOR';
}

function medalEmoji(rank) {
  if (rank === 1) return String.fromCodePoint(0x1F947);
  if (rank === 2) return String.fromCodePoint(0x1F948);
  if (rank === 3) return String.fromCodePoint(0x1F949);
  return String.fromCodePoint(0x2B50) + ' #' + rank;
}

// =========================================================
// MODE SWITCHING
// =========================================================

function switchMode(mode) {
  currentMode = mode;
  
  const locationBtn = document.getElementById('modeLocationBtn');
  const uploadBtn = document.getElementById('modeUploadBtn');
  const locationContent = document.getElementById('locationModeContent');
  const uploadContent = document.getElementById('uploadModeContent');
  
  if (mode === 'location') {
    locationBtn.classList.add('input-mode-toggle__btn--active');
    uploadBtn.classList.remove('input-mode-toggle__btn--active');
    locationContent.classList.remove('hidden');
    uploadContent.classList.add('hidden');
  } else {
    uploadBtn.classList.add('input-mode-toggle__btn--active');
    locationBtn.classList.remove('input-mode-toggle__btn--active');
    uploadContent.classList.remove('hidden');
    locationContent.classList.add('hidden');
  }
}

// =========================================================
// RADIUS SELECTOR
// =========================================================

function initRadiusSelector() {
  const btns = document.querySelectorAll('.radius-btn');
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      btns.forEach(b => b.classList.remove('radius-btn--active'));
      btn.classList.add('radius-btn--active');
      const radius = btn.dataset.radius;
      document.getElementById('analysisRadius').value = radius;
    });
  });
}

// =========================================================
// WORKFLOW STEPS
// =========================================================

function setWorkflowStep(step) {
  const steps = ['01', '02', '03', '04'];
  const stepIndex = steps.indexOf(step);
  
  steps.forEach((s, i) => {
    const el = document.getElementById('step' + s);
    if (!el) return;
    el.classList.remove('workflow-step--active', 'workflow-step--complete', 'workflow-step--running');
    
    if (i < stepIndex) {
      el.classList.add('workflow-step--complete');
    } else if (s === step) {
      el.classList.add('workflow-step--active');
    }
  });
  
  currentStep = step;
}

function completeWorkflowStep(step) {
  const el = document.getElementById('step' + step);
  if (el) {
    el.classList.remove('workflow-step--active', 'workflow-step--running');
    el.classList.add('workflow-step--complete');
  }
}

function activateWorkflowStep(step) {
  const el = document.getElementById('step' + step);
  if (el) {
    el.classList.remove('workflow-step--complete', 'workflow-step--running');
    el.classList.add('workflow-step--active');
  }
  currentStep = step;
}

// =========================================================
// STATUS UPDATES
// =========================================================

function setStatus(status, message) {
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  const headerText = document.getElementById('headerStatusText');
  
  dot.classList.remove('status-indicator__dot--idle', 'status-indicator__dot--working');
  
  switch(status) {
    case 'idle':
      dot.classList.add('status-indicator__dot--idle');
      text.textContent = 'STANDBY';
      headerText.textContent = 'AWAITING ANALYSIS INPUT';
      break;
    case 'working':
      dot.classList.add('status-indicator__dot--working');
      text.textContent = 'PROCESSING';
      headerText.innerHTML = '<span class="analyzing">ANALYZING TERRAIN...</span>';
      break;
    case 'complete':
      dot.classList.add('status-indicator__dot');
      text.textContent = 'ANALYSIS COMPLETE';
      headerText.textContent = 'ANALYSIS COMPLETE';
      break;
    case 'error':
      dot.classList.add('status-indicator__dot--idle');
      text.textContent = 'ERROR';
      headerText.textContent = 'ANALYSIS FAILED';
      break;
  }
}

// =========================================================
// PROGRESS OVERLAY
// =========================================================

function showProgress() {
  const el = document.getElementById('analysisProgress');
  el.classList.add('analysis-progress--visible');
  
  const steps = ['progStep1', 'progStep2', 'progStep3', 'progStep4', 'progStep5', 'progStep6'];
  steps.forEach((id, i) => {
    const el = document.getElementById(id);
    if (el) {
      el.classList.remove('progress-step--done', 'progress-step--active');
      if (i < 5) el.classList.add('progress-step--done');
      else el.classList.add('progress-step--active');
    }
  });
  
  animateProgress();
}

let progressAnimFrame = null;
function animateProgress() {
  const bar = document.getElementById('progressBarFill');
  const pct = document.getElementById('progressPct');
  let progress = 60;
  
  if (progressAnimFrame) cancelAnimationFrame(progressAnimFrame);
  
  function tick() {
    if (progress < 95) {
      progress += Math.random() * 3;
      if (progress > 95) progress = 95;
      bar.style.width = progress + '%';
      pct.textContent = 'Processing: ' + Math.round(progress) + '%';
      progressAnimFrame = requestAnimationFrame(() => setTimeout(tick, 400));
    }
  }
  tick();
}

function hideProgress() {
  const el = document.getElementById('analysisProgress');
  el.classList.remove('analysis-progress--visible');
  if (progressAnimFrame) {
    cancelAnimationFrame(progressAnimFrame);
    progressAnimFrame = null;
  }
}

function setProgressDone() {
  const bar = document.getElementById('progressBarFill');
  const pct = document.getElementById('progressPct');
  bar.style.width = '100%';
  pct.textContent = 'Analysis complete';
  
  const lastStep = document.getElementById('progStep6');
  if (lastStep) {
    lastStep.classList.remove('progress-step--active');
    lastStep.classList.add('progress-step--done');
    const icon = lastStep.querySelector('.progress-step__icon');
    if (icon) icon.innerHTML = '&radic;';
  }
  
  setTimeout(hideProgress, 600);
}

// =========================================================
// MAP LAYER CONTROLS
// =========================================================

function initMapLayerControls() {
  const layerStreet = document.getElementById('layerStreet');
  const layerSatellite = document.getElementById('layerSatellite');
  const layerContours = document.getElementById('layerContours');
  const layerCandidates = document.getElementById('layerCandidates');
  const layerCatchment = document.getElementById('layerCatchment');
  
  // These reference the layerControl from app.js
  layerStreet.addEventListener('change', () => {
    if (typeof street !== 'undefined') {
      if (layerStreet.checked) map.addLayer(street);
      else map.removeLayer(street);
    }
  });
  
  layerSatellite.addEventListener('change', () => {
    if (typeof satellite !== 'undefined') {
      if (layerSatellite.checked) map.addLayer(satellite);
      else map.removeLayer(satellite);
    }
  });
  
  layerContours.addEventListener('change', () => {
    if (typeof originalContourLayer !== 'undefined') {
      if (layerContours.checked) map.addLayer(originalContourLayer);
      else map.removeLayer(originalContourLayer);
    }
  });
  
  layerCandidates.addEventListener('change', () => {
    toggleCandidateMarkers(layerCandidates.checked);
  });
  
  layerCatchment.addEventListener('change', () => {
    toggleCatchmentLayer(layerCatchment.checked);
  });
}

function toggleCandidateMarkers(visible) {
  if (typeof candidateMarkers !== 'undefined') {
    candidateMarkers.forEach(marker => {
      if (visible) map.addLayer(marker);
      else map.removeLayer(marker);
    });
  }
}

let catchmentLayerRef = null;

function toggleCatchmentLayer(visible) {
  if (analysisLayers && analysisLayers.length > 0) {
    analysisLayers.forEach(layer => {
      if (layer._isCatchment) {
        if (visible) map.addLayer(layer);
        else map.removeLayer(layer);
      }
    });
  }
}

// Global reference to current data for strip click handler
let currentStripData = null;


// Store data globally for the click handler
window._stripData = null;

function renderCandidateStrip(candidates, recommendedId) {
  const strip = document.getElementById('candidateStrip');
  const scroll = document.getElementById('stripScroll');
  const count = document.getElementById('stripCount');
  
  if (!candidates || candidates.length === 0) {
    strip.classList.add('candidate-strip--empty');
    return;
  }
  
  strip.classList.remove('candidate-strip--empty');
  count.textContent = candidates.length + ' candidate' + (candidates.length !== 1 ? 's' : '');
  scroll.innerHTML = '';
  
  candidates.slice(0, 20).forEach(candidate => {
    const isSelected = candidate.candidate_id === selectedCandidateId;
    const card = document.createElement('div');
    card.className = 'candidate-card' + (isSelected ? ' candidate-card--selected' : '');
    card.dataset.candidateId = candidate.candidate_id;
    
    const areaKm2 = ((candidate.catchment?.area_hectares || 0) / 100).toFixed(2);
    
    var mfScore = candidate.multi_factor_score && candidate.multi_factor_score.final_score != null
      ? Math.round(Number(candidate.multi_factor_score.final_score))
      : null;
    var mfCategory = mfScore != null ? (candidate.multi_factor_score.category || '') : '';
    var terrainScore = fmtNum(candidate.suitability_score, 0);

    card.innerHTML = `      <div class="candidate-card__medal">${medalEmoji(candidate.final_rank || candidate.rank)}</div>      <div class="candidate-card__rank">RANK ${String(candidate.final_rank || candidate.rank).padStart(2, '0')}</div>      <div class="candidate-card__score">${mfScore != null ? mfScore : terrainScore}</div>      <div class="candidate-card__score-label">${mfScore != null ? mfCategory.toUpperCase() : 'SUITABILITY'}</div>      <div class="candidate-card__divider"></div>      <div class="candidate-card__meta">        <div class="candidate-card__meta-row">          <span class="candidate-card__meta-key">Terrain</span>          <span class="candidate-card__meta-val">${terrainScore} pts</span>        </div>        <div class="candidate-card__meta-row">          <span class="candidate-card__meta-key">Catchment</span>          <span class="candidate-card__meta-val">${areaKm2} km2</span>        </div>      </div>    `;
    
    card.addEventListener('click', () => {
      const data = window._stripData;
      if (data) selectCandidate(candidate, data);
    });
    
    scroll.appendChild(card);
  });
}

function selectCandidateFromStrip(candidate) {
  const data = window._stripData;
  if (data) selectCandidate(candidate, data);
}

// =========================================================
// CANDIDATE INSPECTOR
// =========================================================

function showInspector(candidate, data) {
  const inspector = document.getElementById('candidateInspector');
  inspector.classList.remove('candidate-inspector--hidden');
  inspectorVisible = true;
  
  const isBest = candidate.candidate_id === data.recommended_candidate_id;
  
  document.getElementById('inspectorRank').textContent = '#' + String(candidate.rank).padStart(2, '0');
  document.getElementById('inspectorBestTag').style.display = isBest ? 'inline-block' : 'none';
  document.getElementById('inspectorScore').textContent = fmtNum(candidate.suitability_score, 0);
  document.getElementById('inspectorRating').textContent = getRating(candidate.suitability_score);
  
  // Flow accumulation as normalized score
  const maxFlow = Math.max(...data.candidates.map(c => c.flow_accumulation_cells || 1));
  const flowNorm = maxFlow > 0 ? ((candidate.flow_accumulation_cells || 0) / maxFlow * 100).toFixed(0) : 0;
  document.getElementById('inspectorFlow').textContent = flowNorm;
  document.getElementById('inspectorFlowBar').style.width = flowNorm + '%';
  
  // Valley position (derived from suitability score - flow component)
  const valleyScore = Math.min(100, Math.max(0, candidate.suitability_score - 5)).toFixed(0);
  document.getElementById('inspectorValley').textContent = valleyScore;
  document.getElementById('inspectorValleyBar').style.width = valleyScore + '%';
  
  // Drainage (slope-based component)
  const drainageScore = Math.min(100, Math.max(0, candidate.suitability_score - 2)).toFixed(0);
  document.getElementById('inspectorDrainage').textContent = drainageScore;
  document.getElementById('inspectorDrainageBar').style.width = drainageScore + '%';
  
  // Quick stats
  document.getElementById('inspectorElev').textContent = fmtNum(candidate.elevation_m, 1) + ' m';
  const areaKm2 = ((candidate.catchment?.area_hectares || 0) / 100).toFixed(2);
  document.getElementById('inspectorCatchment').textContent = areaKm2 + ' km2';
  document.getElementById('inspectorCells').textContent = fmtInt(candidate.flow_accumulation_cells);
  document.getElementById('inspectorStorage').textContent = fmtInt(candidate.water?.estimated_storage_capacity_m3) + ' m3';

  // Multi-factor score
  const mf = getMFScore(candidate);
  if (mf) {
    const score = Math.round(Number(mf.final_score) || 0);
    document.getElementById('inspectorMFScore').textContent = score;
    document.getElementById('inspectorMFBBar').style.width = score + '%';
    document.getElementById('inspectorMFCategory').textContent = mf.category || 'N/A';

    const avail = (mf.available_factors || []).map(function(f) { return String(f).toUpperCase(); });
    document.getElementById('inspectorMFFactors').textContent =
      avail.length > 0
        ? avail.length + ' factors: ' + avail.join(' \u00b7 ')
        : 'No factors available';

    const unavail = mf.unavailable_factors || [];
    const unavailEl = document.getElementById('inspectorMFUnavailable');
    if (unavail.length > 0) {
      unavailEl.style.display = 'block';
      document.getElementById('inspectorMFUnavailableList').textContent =
        unavail.map(function(f) { return String(f).toUpperCase(); }).join(', ');
    } else {
      unavailEl.style.display = 'none';
    }

    const confBadge = document.getElementById('inspectorConfidence');
    const conf = String(mf.confidence || '').toUpperCase();
    confBadge.textContent = conf || '';
    confBadge.className = 'mf-inspector__confidence ' + getConfidenceClass(mf.confidence);
  } else {
    document.getElementById('inspectorMFScore').textContent = '--';
    document.getElementById('inspectorMFBBar').style.width = '0%';
    document.getElementById('inspectorMFCategory').textContent = 'N/A';
    document.getElementById('inspectorMFFactors').textContent = 'Multi-factor analysis unavailable';
    document.getElementById('inspectorMFUnavailable').style.display = 'none';
    document.getElementById('inspectorConfidence').textContent = '';
  }

  selectedCandidateId = candidate.candidate_id;
  
  // Update strip selection
  updateStripSelection(candidate.candidate_id);
  
  // Animate bars
  setTimeout(() => {
    document.getElementById('inspectorFlowBar').style.width = flowNorm + '%';
    document.getElementById('inspectorValleyBar').style.width = valleyScore + '%';
    document.getElementById('inspectorDrainageBar').style.width = drainageScore + '%';
  }, 50);
}

function hideInspector() {
  const inspector = document.getElementById('candidateInspector');
  inspector.classList.add('candidate-inspector--hidden');
  inspectorVisible = false;
  selectedCandidateId = null;
}

function selectCandidate(candidate, data) {
  // Update map marker selection
  if (typeof candidateMarkers !== 'undefined') {
    const marker = candidateMarkers.get(candidate.candidate_id);
    if (marker) {
      map.setView([candidate.latitude, candidate.longitude], Math.max(map.getZoom(), 16));
      marker.openPopup();
    }
  }
  
  showInspector(candidate, data);
  
  // Highlight the selected strip card
  document.querySelectorAll('.candidate-card').forEach(card => {
    if (Number(card.dataset.candidateId) === candidate.candidate_id) {
      card.classList.add('candidate-card--selected');
      card.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    } else {
      card.classList.remove('candidate-card--selected');
    }
  });
}

function updateStripSelection(candidateId) {
  document.querySelectorAll('.candidate-card').forEach(card => {
    if (Number(card.dataset.candidateId) === candidateId) {
      card.classList.add('candidate-card--selected');
    } else {
      card.classList.remove('candidate-card--selected');
    }
  });
}

// =========================================================
// ANALYSIS DRAWER
// =========================================================

function openDrawer() {
  document.getElementById('drawerOverlay').classList.add('analysis-drawer-overlay--open');
  document.getElementById('analysisDrawer').classList.add('analysis-drawer--open');
  drawerOpen = true;
  document.body.classList.add('no-scroll');
}

function closeDrawer() {
  document.getElementById('drawerOverlay').classList.remove('analysis-drawer-overlay--open');
  document.getElementById('analysisDrawer').classList.remove('analysis-drawer--open');
  drawerOpen = false;
  document.body.classList.remove('no-scroll');
}

function populateDrawer(candidate, data) {
  const body = document.getElementById('drawerBody');
  
  const isBest = candidate.candidate_id === data.recommended_candidate_id;
  
  const flowRainNorm = candidate.flow_accumulation_cells && data.terrain
    ? Math.min(100, (candidate.flow_accumulation_cells / (data.terrain.grid_rows * data.terrain.grid_columns) * 1000)).toFixed(1)
    : 0;
  
  body.innerHTML = `    <!-- CANDIDATE HEADER -->    <div class="drawer-section">      <div class="drawer-section__title">Candidate ${String(candidate.rank).padStart(2, '0') + (isBest ? ' — RECOMMENDED' : '')}</div>      <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">        <div style="font-family:var(--font-mono);font-size:48px;font-weight:700;color:var(--selected);line-height:1;letter-spacing:-0.02em">${fmtNum(candidate.suitability_score, 0)}</div>        <div>          <div style="font-family:var(--font-mono);font-size:9px;letter-spacing:0.1em;text-transform:uppercase;color:var(--text-dim)">SUITABILITY SCORE</div>          <div style="font-size:12px;color:var(--selected);font-weight:600;margin-top:4px">${getRating(candidate.suitability_score)}</div>        </div>      </div>      <div class="drawer-metric">        <span class="drawer-metric__key">Coordinates</span>        <span class="drawer-metric__val">${candidate.latitude.toFixed(6)}, ${candidate.longitude.toFixed(6)}</span>      </div>    </div>        <!-- TERRAIN -->    <div class="drawer-section">      <div class="drawer-section__title">Terrain</div>      <div class="drawer-section__grid">        <div class="drawer-metric">          <span class="drawer-metric__key">Elevation</span>          <span class="drawer-metric__val">${fmtNum(candidate.elevation_m, 2)} m AMSL</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Slope</span>          <span class="drawer-metric__val">${fmtNum(candidate.slope_percent, 2)}%</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">DEM Resolution</span>          <span class="drawer-metric__val">${fmtNum(data.terrain?.grid_resolution_m, 0)} m</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Grid Size</span>          <span class="drawer-metric__val">${fmtInt(data.terrain?.grid_rows)} x ${fmtInt(data.terrain?.grid_columns)}</span>        </div>      </div>    </div>        <!-- HYDROLOGY -->    <div class="drawer-section">      <div class="drawer-section__title">Hydrology</div>      <div class="drawer-section__grid">        <div class="drawer-metric drawer-metric--bar">          <div class="drawer-metric__header">            <span class="drawer-metric__key">Flow Accumulation</span>            <span class="drawer-metric__score">${fmtInt(candidate.flow_accumulation_cells)} cells</span>          </div>          <div class="metric-bar" style="height:6px">            <div class="metric-bar__fill metric-bar__fill--primary" style="width:${flowRainNorm}%"></div>          </div>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Flow Normalized</span>          <span class="drawer-metric__val">${flowRainNorm}%</span>        </div>      </div>    </div>        <!-- CATCHMENT -->    <div class="drawer-section">      <div class="drawer-section__title">Catchment</div>      <div class="drawer-section__grid">        <div class="drawer-metric">          <span class="drawer-metric__key">Area</span>          <span class="drawer-metric__val">${fmtNum(candidate.catchment?.area_hectares, 2)} ha (${fmtNum((candidate.catchment?.area_hectares || 0) / 100, 2)} km2)</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Cell Count</span>          <span class="drawer-metric__val">${fmtInt(candidate.catchment?.cell_count)}</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Grid Coverage</span>          <span class="drawer-metric__val">${fmtNum(data.terrain ? (candidate.catchment?.cell_count / (data.terrain.grid_rows * data.terrain.grid_columns) * 100) : 0, 1)}%</span>        </div>      </div>    </div>        <!-- POND STORAGE -->    <div class="drawer-section">      <div class="drawer-section__title">Pond Storage</div>      <div class="drawer-section__grid">        <div class="drawer-metric">          <span class="drawer-metric__key">Pond Area</span>          <span class="drawer-metric__val">${fmtInt(candidate.water?.pond_area_m2)} m2 (${fmtNum(candidate.water?.pond_area_hectares, 4)} ha)</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Recommended Depth</span>          <span class="drawer-metric__val">${fmtNum(candidate.water?.recommended_depth_m, 2)} m</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Storage Capacity</span>          <span class="drawer-metric__val">${fmtInt(candidate.water?.estimated_storage_capacity_m3)} m3</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Shape Factor</span>          <span class="drawer-metric__val">${fmtNum(candidate.water?.shape_factor, 3)}</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Local Relief</span>          <span class="drawer-metric__val">${fmtNum(candidate.water?.local_relief_m, 2)} m</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Search Radius</span>          <span class="drawer-metric__val">${fmtNum(candidate.water?.pond_radius_used_m, 0)} m</span>        </div>      </div>    </div>        <!-- WATER BALANCE -->    <div class="drawer-section">      <div class="drawer-section__title">Water Balance</div>      <div class="drawer-section__grid">        <div class="drawer-metric">          <span class="drawer-metric__key">Runoff Coefficient</span>          <span class="drawer-metric__val">${fmtNum(candidate.water?.runoff_coefficient, 2)}</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Annual Runoff</span>          <span class="drawer-metric__val">${fmtInt(candidate.water?.estimated_annual_runoff_m3)} m3/year</span>        </div>        ${(candidate.water?.runoff_to_storage_ratio !== null ? '<div class="drawer-metric"><span class="drawer-metric__key">Runoff/Storage Ratio</span><span class="drawer-metric__val">${fmtNum(candidate.water?.runoff_to_storage_ratio, 2)}</span></div>' : '')}        ${(candidate.water?.potential_fill_percent !== null ? '<div class="drawer-metric"><span class="drawer-metric__key">Potential Fill</span><span class="drawer-metric__val">${fmtNum(candidate.water?.potential_fill_percent, 1)}%</span></div>' : '')}      </div>    </div>        <!-- RAINFALL -->    ${(data.rainfall?.available ? '<div class="drawer-section"><div class="drawer-section__title">Rainfall</div><div class="drawer-section__grid"><div class="drawer-metric"><span class="drawer-metric__key">Source</span><span class="drawer-metric__val">${data.rainfall.source}</span></div><div class="drawer-metric"><span class="drawer-metric__key">Average Annual</span><span class="drawer-metric__val">${fmtNum(data.rainfall.average_annual_rainfall_mm, 1)} mm/year</span></div><div class="drawer-metric"><span class="drawer-metric__key">Period</span><span class="drawer-metric__val">${data.rainfall.period}</span></div></div></div>' : '')}        <!-- LAND FILTER -->    <div class="drawer-section">      <div class="drawer-section__title">Land Filter</div>      <div class="drawer-section__grid">        <div class="drawer-metric">          <span class="drawer-metric__key">Source</span>          <span class="drawer-metric__val">${(data.land_filter?.source || 'N/A')}</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Free Cells</span>          <span class="drawer-metric__val">${fmtInt(data.land_filter?.free_cell_count)}</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Excluded Cells</span>          <span class="drawer-metric__val">${fmtInt(data.land_filter?.excluded_cell_count)}</span>        </div>        <div class="drawer-metric">          <span class="drawer-metric__key">Status</span>          <span class="drawer-metric__val" style="color:${(candidate.land_status?.includes('free') ? 'var(--success)' : 'var(--text-muted)')}">${candidate.land_status}</span>        </div>      </div>    </div>
    <!-- ENVIRONMENTAL CONSTRAINTS -->
    ${buildConstraintsSection(candidate)}

    <!-- SOIL ANALYSIS -->
    ${buildSoilSection(candidate)}

    <!-- CLIMATE -->
    ${buildClimateSection(candidate, data)}

    <!-- LAND USE -->
    ${buildLandUseSection(candidate)}

    <!-- ACCESSIBILITY -->
    ${buildAccessibilitySection(candidate)}

    <!-- WATER AVAILABILITY -->
    ${buildWaterAvailSection(candidate)}

    <!-- STORAGE ESTIMATION -->
    ${buildStorageSection(candidate)}

    <!-- MULTI-FACTOR HERO -->
    ${buildMultiFactorHero(candidate)}

    <!-- DATA CONFIDENCE -->
    ${buildConfidenceSection(data)}
    `;
}

// =========================================================
// FILE UPLOAD
// =========================================================

function initFileUpload() {
  const zone = document.getElementById('uploadZone');
  const input = document.getElementById('contourFile');
  
  zone.addEventListener('click', () => input.click());
  
  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    zone.classList.add('upload-zone--dragover');
  });
  
  zone.addEventListener('dragleave', () => {
    zone.classList.remove('upload-zone--dragover');
  });
  
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('upload-zone--dragover');
    if (e.dataTransfer.files.length > 0) {
      input.files = e.dataTransfer.files;
      handleFileSelect(e.dataTransfer.files[0]);
    }
  });
  
  input.addEventListener('change', () => {
    if (input.files.length > 0) {
      handleFileSelect(input.files[0]);
    }
  });
}

function handleFileSelect(file) {
  const display = document.getElementById('uploadedFileDisplay');
  if (!file) return;
  
  const ext = file.name.split('.').pop().toLowerCase();
  if (ext !== 'kml' && ext !== 'kmz') {
    display.innerHTML = '<div style="color:var(--danger);font-size:11px;padding:8px 0;font-family:var(--font-mono)">Only KML and KMZ files supported</div>';
    return;
  }
  
  display.innerHTML = `    <div class="uploaded-file">      <span class="uploaded-file__icon">        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>      </span>      <span class="uploaded-file__name" title="${file.name}">${file.name}</span>      <span class="uploaded-file__clear" id="clearUpload">&times;</span>    </div>  `;
  
  document.getElementById('clearUpload').addEventListener('click', () => {
    input.value = '';
    display.innerHTML = '';
  });
}

// =========================================================
// ANALYSIS RESULT INTEGRATION
// =========================================================
//
// These functions hook into the existing analysis flow.
// They override the old UI display functions to use the new
// command center UI instead.
// =========================================================

function onAnalysisStart(mode) {
  setStatus('working', 'Processing...');
  showProgress();
  
  if (mode === 'location') {
    activateWorkflowStep('01');
    const statusEl = document.getElementById('locationStatus');
    if (statusEl) {
      statusEl.textContent = 'Finding location and acquiring terrain data...';
      statusEl.className = '';
    }
  } else {
    activateWorkflowStep('01');
    const statusEl = document.getElementById('status');
    if (statusEl) {
      statusEl.textContent = 'Analyzing terrain and hydrology...';
      statusEl.className = '';
    }
  }
  
  // Hide inspector and results on new analysis
  hideInspector();
  document.getElementById('noResults').classList.remove('no-results--visible');
  document.getElementById('candidateStrip').classList.add('candidate-strip--empty');
}

function onAnalysisComplete(data, mode) {
  setProgressDone();
  setStatus('complete', 'Analysis complete');
  
  // Hide no-results panel when candidates exist
  const noResultsEl = document.getElementById('noResults');
  if (noResultsEl && data.candidates && data.candidates.length > 0) {
    noResultsEl.classList.remove('no-results--visible');
  }

  // Update terrain info overlay
  if (data.terrain) {
    const terrain = data.terrain;
    const terrainInfo = document.getElementById('terrainInfo');
    if (terrainInfo) {
      terrainInfo.innerHTML = `
        <div class="terrain-info__row">
          <span class="terrain-info__key">Status</span>
          <span class="terrain-info__tag terrain-info__tag--ready">COMPLETE</span>
        </div>
        <div class="terrain-info__row">
          <span class="terrain-info__key">DEM</span>
          <span class="terrain-info__val">${fmtNum(terrain.grid_resolution_m, 0)} m</span>
        </div>
        <div class="terrain-info__row">
          <span class="terrain-info__key">Contours</span>
          <span class="terrain-info__val">${fmtInt(terrain.contour_line_count)}</span>
        </div>
        <div class="terrain-info__row">
          <span class="terrain-info__key">Elevation</span>
          <span class="terrain-info__val">${fmtNum(terrain.minimum_elevation_m, 0)} - ${fmtNum(terrain.maximum_elevation_m, 0)} m</span>
        </div>
      `;
    }
  }
  
  // Update workflow steps
  completeWorkflowStep('01');
  activateWorkflowStep('02');
  setTimeout(() => {
    completeWorkflowStep('02');
    activateWorkflowStep('03');
    setTimeout(() => {
      completeWorkflowStep('03');
      activateWorkflowStep('04');
      setTimeout(() => {
        completeWorkflowStep('04');
      }, 300);
    }, 300);
  }, 300);
  
  // Store data globally for strip click handlers
  window._stripData = data;
  currentAnalysisData = data;
  
  // Render candidate strip
  renderCandidateStrip(data.candidates, data.recommended_candidate_id);
  
  // Auto-select top candidate
  if (data.candidates && data.candidates.length > 0) {
    if (noResultsEl) noResultsEl.classList.remove('no-results--visible');
    const top = data.candidates.find(c => c.candidate_id === data.recommended_candidate_id) || data.candidates[0];
    if (top) {
      setTimeout(() => {
        showInspector(top, data);
      }, 100);
    }
  } else {
    // No candidates
    if (noResultsEl) noResultsEl.classList.add('no-results--visible');
    hideInspector();
  }
}

function onAnalysisError(message, mode) {
  hideProgress();
  setStatus('error', 'Analysis failed');
  
  const statusEl = mode === 'location' 
    ? document.getElementById('locationStatus') 
    : document.getElementById('status');
  
  if (statusEl) {
    statusEl.textContent = message;
    statusEl.className = 'error';
  }
}

// Expose on window scope for reliable cross-script calls
window.onAnalysisComplete = onAnalysisComplete;
window.onAnalysisError = onAnalysisError;

// =========================================================
// VIEW FULL ANALYSIS BUTTON
// =========================================================

function handleViewFullAnalysis() {
  const data = window._stripData || currentAnalysisData;
  const selected = selectedCandidateId;
  
  if (!data) return;
  
  const candidate = data.candidates.find(c => c.candidate_id === selected) 
    || data.candidates[0];
  
  if (candidate) {
    populateDrawer(candidate, data);
    openDrawer();
  }
}

// =========================================================
// EVENT LISTENERS
// =========================================================

function initEventListeners() {
  // Mode switching
  document.getElementById('modeLocationBtn').addEventListener('click', () => switchMode('location'));
  document.getElementById('modeUploadBtn').addEventListener('click', () => switchMode('upload'));
  
  // Inspector close
  document.getElementById('inspectorClose').addEventListener('click', hideInspector);
  
  // Drawer
  document.getElementById('drawerClose').addEventListener('click', closeDrawer);
  document.getElementById('drawerOverlay').addEventListener('click', closeDrawer);
  document.getElementById('viewFullAnalysisBtn').addEventListener('click', handleViewFullAnalysis);
  
  // No results diagnostics
  document.getElementById('viewDiagnosticsBtn').addEventListener('click', () => {
    // Could open a diagnostics panel, for now just close the state
    document.getElementById('noResults').classList.remove('no-results--visible');
  });
  
  // Escape key closes drawer
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (drawerOpen) closeDrawer();
      else if (inspectorVisible) hideInspector();
    }
  });
}

// =========================================================
// LEGACY STATUS OVERRIDES
// =========================================================
//
// Override the old status text functions to also update
// the new UI status.
// =========================================================

const _origSetStatus = null;
const _origSetLocationStatus = null;

function ccOverrideLegacyStatus() {
  // Override analyzeBtn click
  const analyzeBtn = document.getElementById('analyzeBtn');
  if (analyzeBtn) {
    const origHandler = analyzeBtn._clickHandler;
    analyzeBtn.addEventListener('click', async () => {
      onAnalysisStart('upload');
      // Call original logic
      if (typeof origAnalyzeBtnHandler === 'function') {
        // Will be called by original handler
      }
    }, true);
  }
}

// =========================================================
// ANALYSIS BUTTON INTERCEPTION
// =========================================================
//
// We intercept the original analyze buttons to show the new
// progress UI while still calling the original handlers.
// =========================================================

(function interceptAnalysisButtons() {
  // Intercept analyzeBtn (KML upload)
  const analyzeBtn = document.getElementById('analyzeBtn');
  if (analyzeBtn) {
    analyzeBtn.addEventListener('click', function(e) {
      onAnalysisStart('upload');
    }, false);
  }
  
  // Intercept analyzeLocationBtn (auto location)
  const analyzeLocationBtn = document.getElementById('analyzeLocationBtn');
  if (analyzeLocationBtn) {
    analyzeLocationBtn.addEventListener('click', function(e) {
      onAnalysisStart('location');
    }, false);
  }
})();

// =========================================================
// OVERRIDE showResults, drawAnalysis, renderCandidateTable
// =========================================================
//
// The existing app.js functions populate the DOM with old
// UI. We replace them to instead populate the new UI.
// =========================================================

(function overrideLegacyDisplayFunctions() {
  // showResults and drawAnalysis - call originals, then supplement with new UI
  // This ensures the map gets drawn and data gets processed by original app.js,
  // then we layer the new command center UI on top.

  if (typeof window.showResults === 'function') {
    const originalShowResults = window.showResults;
    window.showResults = function(data) {
      // Call original (draws map, updates #results div, etc.)
      originalShowResults(data);
      // CRITICAL: store the actual API response data BEFORE setting the flag.
      // checkAnalysisComplete() reads currentAnalysisData / window._stripData
      // to call onAnalysisComplete() with the real payload.
      currentAnalysisData = data;
      window._stripData = data;
      // Set the "results drawn" flag for the post-analysis hook
      _resultsDrawn = true;
      checkAnalysisComplete();
      // Supplement with new UI - update terrain info overlay
      if (data.terrain) {
        const terrain = data.terrain;
        const terrainInfo = document.getElementById('terrainInfo');
        if (terrainInfo) {
          terrainInfo.innerHTML = `
            <div class="terrain-info__row">
              <span class="terrain-info__key">Status</span>
              <span class="terrain-info__tag terrain-info__tag--ready">COMPLETE</span>
            </div>
            <div class="terrain-info__row">
              <span class="terrain-info__key">DEM</span>
              <span class="terrain-info__val">${fmtNum(terrain.grid_resolution_m, 0)} m</span>
            </div>
            <div class="terrain-info__row">
              <span class="terrain-info__key">Contours</span>
              <span class="terrain-info__val">${fmtInt(terrain.contour_line_count)}</span>
            </div>
            <div class="terrain-info__row">
              <span class="terrain-info__key">Elevation</span>
              <span class="terrain-info__val">${fmtNum(terrain.minimum_elevation_m, 0)} - ${fmtNum(terrain.maximum_elevation_m, 0)} m</span>
            </div>
          `;
        }
      }
    };
  }

  if (typeof window.drawAnalysis === 'function') {
    const originalDrawAnalysis = window.drawAnalysis;
    window.drawAnalysis = function(data) {
      // Draw the analysis on map (unchanged)
      originalDrawAnalysis(data);
      // CRITICAL: ensure the data is available for checkAnalysisComplete()
      currentAnalysisData = data;
      window._stripData = data;
      // Set the "analysis complete" flag for the post-analysis hook
      _analysisComplete = true;
      checkAnalysisComplete();
      // After drawing, mark catchment layer for toggle control
      if (typeof analysisLayers !== 'undefined' && analysisLayers && analysisLayers.length > 0) {
        analysisLayers.forEach(layer => {
          layer._isCatchment = true;
        });
      }
    };
  }

  // Suppress renderCandidateTable - the new UI uses strip instead
  if (typeof window.renderCandidateTable === 'function') {
    window.renderCandidateTable = function() {
      // No-op - new UI uses renderCandidateStrip
    };
  }
})();

// =========================================================
// OVERRIDE LOCATION.JS STATUS UPDATES
// =========================================================
//
// location.js updates #locationStatus during its analysis
// flow. We intercept those calls to also update the new UI.
// =========================================================

(function interceptLocationAnalysis() {
  // Monitor the locationStatus element
  const locationStatus = document.getElementById('locationStatus');
  if (locationStatus) {
    const observer = new MutationObserver((mutations) => {
      mutations.forEach(mutation => {
        if (mutation.type === 'characterData' || mutation.type === 'childList') {
          const text = locationStatus.textContent;
          // Update terrain status based on progress
          if (text.includes('Finding location')) {
            document.getElementById('terrainStatus').textContent = 'GEOCODING';
            document.getElementById('terrainStatus').className = 'terrain-info__tag terrain-info__tag--dem';
          } else if (text.includes('DEM') || text.includes('OpenTopography')) {
            document.getElementById('terrainStatus').textContent = 'DOWNLOADING DEM';
            document.getElementById('terrainStatus').className = 'terrain-info__tag terrain-info__tag--dem';
          } else if (text.includes('contour') || text.includes('terrain')) {
            document.getElementById('terrainStatus').textContent = 'PROCESSING';
            document.getElementById('terrainStatus').className = 'terrain-info__tag terrain-info__tag--hydrology';
          }
          
          if (text.includes('Error') || text.includes('error')) {
            locationStatus.className = 'error';
          } else if (text.includes('successfully') || text.includes('completed')) {
            locationStatus.className = 'success';
          }
        }
      });
    });
    observer.observe(locationStatus, { characterData: true, childList: true, subtree: true });
  }
  
  // Also monitor regular status
  const statusEl = document.getElementById('status');
  if (statusEl) {
    const observer = new MutationObserver((mutations) => {
      mutations.forEach(mutation => {
        const text = statusEl.textContent;
        if (text.includes('Error') || text.includes('error')) {
          statusEl.className = 'error';
        } else if (text.includes('successfully') || text.includes('completed')) {
          statusEl.className = 'success';
        }
      });
    });
    observer.observe(statusEl, { characterData: true, childList: true, subtree: true });
  }
})();

// =========================================================
// POST-ANALYSIS HOOK
// =========================================================
//
// After both showResults AND drawAnalysis are called,
// we need to finalize the new UI. Since these are called
// sequentially in the original code, we use a flag.
// =========================================================

let _resultsDrawn = false;
let _analysisComplete = false;

function checkAnalysisComplete() {
  if (_resultsDrawn && _analysisComplete) {
    // Finalize UI
    setTimeout(() => {
      const data = window._stripData || currentAnalysisData;
      if (data) {
        onAnalysisComplete(data, currentMode);
      }
      _resultsDrawn = false;
      _analysisComplete = false;
    }, 100);
  }
}

// Note: The post-analysis hook (setting _resultsDrawn / _analysisComplete flags
// and calling checkAnalysisComplete) is now integrated into overrideLegacyDisplayFunctions
// which is the single override point for showResults and drawAnalysis.

// =========================================================
// INITIALIZATION
// =========================================================

function initCommandCenter() {
  initRadiusSelector();
  initFileUpload();
  initMapLayerControls();
  initEventListeners();
  setStatus('idle', 'Ready');
  
  // Set initial radius display
  const radiusInput = document.getElementById('analysisRadius');
  if (radiusInput) {
    radiusInput.value = '3000';
  }
  
  // Add analysis radius hidden input if missing
  if (!document.getElementById('analysisRadius')) {
    const hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.id = 'analysisRadius';
    hidden.value = '3000';
    document.getElementById('locationName').parentElement.appendChild(hidden);
  }
}

// Run after DOM is ready and after app.js/location.js load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initCommandCenter);
} else {
  // DOM already ready, but ensure app.js has run first
  setTimeout(initCommandCenter, 100);
}
