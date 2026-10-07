/* =============================================================
   farm-drawing.js — Tayseer Agri-SaT Step 8
   3D Farm Drawing Tool — MapLibre GL JS + Leaflet.PM + Turf.js
   ============================================================= */
"use strict";

// ── Global State ─────────────────────────────────────────────
var _map = null;
var _draw = null;            // MapLibre Draw control
var _plots = [];             // [{id,name,type,geojson,area_m2,area_feddan,...,color,featureId}]
var _editingId = null;        // plot id being edited
var _drawingType = null;      // 'pivot' | 'plot'
var _plotCounter = { pivot: 0, plot: 0 };
var _pendingFeature = null;   // MapboxDraw feature awaiting form submission
var _3dActive = false;
var _bearing = 0;
var _pitch = 0;
var _markerSearch = null;     // temp search marker

const FEDDAN  = 4200;         // 1 feddan = 4200 m²
const HECTARE = 10000;
const QIRAT   = 175;

// Colours per type
const COLORS = {
  pivot: { stroke: '#3b82f6', fill: '#3b82f6', line: '#2563eb' },
  plot:  { stroke: '#f59e0b', fill: '#f59e0b', line: '#d97706' },
};

// Crop options (Arabic + emoji)
const CROPS = [
  { v: 'قمح',         e: '🌾' },
  { v: 'ذرة',         e: '🌽' },
  { v: 'عباد الشمس', e: '🌻' },
  { v: 'طماطم',      e: '🍅' },
  { v: 'بطاطس',      e: '🥔' },
  { v: 'برسيم',      e: '🌿' },
  { v: 'نخيل',       e: '🌴' },
  { v: 'زيتون',      e: '🫒' },
  { v: 'فول',        e: '🫘' },
  { v: 'قصب سكر',    e: '🎋' },
  { v: 'أخرى',       e: '➕' },
];

// ── Map Initialisation ────────────────────────────────────────

function initMap() {
  mapboxgl.accessToken = '';  // not needed for free vector tiles

  // Resolve starting center from user data injected in HTML
  var lat = (typeof FARM_LAT !== 'undefined' && FARM_LAT) ? FARM_LAT : 26.8206;
  var lon = (typeof FARM_LON !== 'undefined' && FARM_LON) ? FARM_LON : 30.8025;
  var zoom = (typeof FARM_LAT !== 'undefined' && FARM_LAT) ? 14 : 6;

  _map = new maplibregl.Map({
    container: 'map',
    style: _buildSatelliteStyle(),
    center: [lon, lat],
    zoom: zoom,
    pitch: 0,
    bearing: 0,
    maxPitch: 85,
    antialias: true,
    attributionControl: false,
  });

  _map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-left');
  _map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left');
  _map.addControl(new maplibregl.FullscreenControl(), 'top-left');

  // MapboxDraw for drawing polygons & circles
  _draw = new MapboxDraw({
    displayControlsDefault: false,
    controls: {},
    styles: _drawStyles(),
  });
  _map.addControl(_draw);

  _map.on('load', function () {
    _initSources();
    _loadExistingPlots();
    _refreshAllLayers();
    _populateCropDropdown();
  });

  // Drawing events
  _map.on('draw.create',  _onDrawCreate);
  _map.on('draw.update',  _onDrawUpdate);
  _map.on('draw.delete',  _onDrawDelete);
  _map.on('draw.selectionchange', _onSelectionChange);

  // Click on plot layers to open popup
  ['plots-fill', 'pivots-fill'].forEach(function(lyr) {
    _map.on('click', lyr, _onLayerClick);
    _map.on('mouseenter', lyr, function () { _map.getCanvas().style.cursor = 'pointer'; });
    _map.on('mouseleave', lyr, function () { _map.getCanvas().style.cursor = ''; });
  });

  // Keyboard: Escape cancels drawing
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') cancelDrawing();
  });

  _showToast('🗺️ الخريطة جاهزة — ابدأ الرسم!', 'info');
}

// ── Base Layer Styles ─────────────────────────────────────────

function _buildSatelliteStyle() {
  return {
    version: 8,
    sources: {
      'google-sat': {
        type: 'raster',
        tiles: ['https://mt0.google.com/vt/lyrs=s&x={x}&y={y}&z={z}'],
        tileSize: 256,
        maxzoom: 22,
      }
    },
    layers: [{
      id: 'bg',
      type: 'raster',
      source: 'google-sat',
      paint: { 'raster-saturation': 0.1 }
    }]
  };
}

function _buildHybridStyle() {
  return {
    version: 8,
    sources: {
      'google-hyb': {
        type: 'raster',
        tiles: ['https://mt0.google.com/vt/lyrs=y&x={x}&y={y}&z={z}'],
        tileSize: 256,
        maxzoom: 22,
      }
    },
    layers: [{
      id: 'bg',
      type: 'raster',
      source: 'google-hyb',
    }]
  };
}

function _buildOSMStyle() {
  return {
    version: 8,
    sources: {
      'osm': {
        type: 'raster',
        tiles: ['https://a.tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
        maxzoom: 19,
      }
    },
    layers: [{
      id: 'bg',
      type: 'raster',
      source: 'osm',
    }]
  };
}

// ── GeoJSON Sources ───────────────────────────────────────────

function _initSources() {
  // Plots source (polygons)
  if (!_map.getSource('plots-src')) {
    _map.addSource('plots-src', {
      type: 'geojson',
      data: _buildFC(_plots.filter(p => p.type === 'plot')),
    });
    _map.addLayer({
      id: 'plots-fill',
      type: 'fill',
      source: 'plots-src',
      paint: {
        'fill-color': ['coalesce', ['get', 'fill'], COLORS.plot.fill],
        'fill-opacity': 0.25,
      }
    });
    _map.addLayer({
      id: 'plots-border',
      type: 'line',
      source: 'plots-src',
      paint: {
        'line-color': ['coalesce', ['get', 'stroke'], COLORS.plot.stroke],
        'line-width': 3,
        'line-dasharray': [1, 0],
      }
    });
  }

  // Pivots source (circles approximated as polygons)
  if (!_map.getSource('pivots-src')) {
    _map.addSource('pivots-src', {
      type: 'geojson',
      data: _buildFC(_plots.filter(p => p.type === 'pivot')),
    });
    _map.addLayer({
      id: 'pivots-fill',
      type: 'fill',
      source: 'pivots-src',
      paint: {
        'fill-color': COLORS.pivot.fill,
        'fill-opacity': 0.2,
      }
    });
    _map.addLayer({
      id: 'pivots-border',
      type: 'line',
      source: 'pivots-src',
      paint: {
        'line-color': COLORS.pivot.stroke,
        'line-width': 3,
      }
    });
  }

  // Labels source
  if (!_map.getSource('labels-src')) {
    _map.addSource('labels-src', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });
    _map.addLayer({
      id: 'labels-layer',
      type: 'symbol',
      source: 'labels-src',
      layout: {
        'text-field': ['get', 'label'],
        'text-font': ['Open Sans Bold'],
        'text-size': 13,
        'text-anchor': 'center',
        'text-allow-overlap': true,
      },
      paint: {
        'text-color': '#ffffff',
        'text-halo-color': '#000000',
        'text-halo-width': 2,
      }
    });
  }
}

function _buildFC(plotsArr) {
  return {
    type: 'FeatureCollection',
    features: plotsArr.map(function(p) {
      return {
        type: 'Feature',
        id: p.id,
        geometry: p.geometry,
        properties: {
          id:       p.id,
          name:     p.name,
          type:     p.type,
          crop:     p.crop_type || '',
          area_fed: (p.area_m2 / FEDDAN).toFixed(2),
          fill:     p.type === 'pivot' ? COLORS.pivot.fill : COLORS.plot.fill,
          stroke:   p.type === 'pivot' ? COLORS.pivot.stroke : COLORS.plot.stroke,
          label:    p.name + (p.crop_type ? '\n' + _cropEmoji(p.crop_type) : ''),
        }
      };
    })
  };
}

function _buildLabelsFC() {
  var features = _plots.map(function(p) {
    var center = turf.centroid(p.geometry);
    return {
      type: 'Feature',
      geometry: center.geometry,
      properties: { label: p.name + (p.crop_type ? ' ' + _cropEmoji(p.crop_type) : '') }
    };
  });
  return { type: 'FeatureCollection', features: features };
}

function _refreshAllLayers() {
  if (!_map.getSource('plots-src')) return;
  _map.getSource('plots-src').setData(_buildFC(_plots.filter(p => p.type === 'plot')));
  _map.getSource('pivots-src').setData(_buildFC(_plots.filter(p => p.type === 'pivot')));
  _map.getSource('labels-src').setData(_buildLabelsFC());
  _updateStatsBanner();
  _renderPlotsList();
}

// ── Drawing Tools ─────────────────────────────────────────────

function startDrawing(type) {
  _drawingType = type;

  // Reset active highlight on buttons
  document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active'));
  var btn = document.getElementById('btn-draw-' + type);
  if (btn) btn.classList.add('active');

  // Hide plots list, show progress hint
  _setPanel('hint');
  document.getElementById('hint-type').textContent =
    type === 'pivot' ? '🔄 ارسم دائرة البيفوت' : '📐 ارسم حدود الحوض';

  // Start drawing
  if (type === 'pivot') {
    _draw.changeMode('draw_circle');
  } else {
    _draw.changeMode('draw_polygon');
  }

  _showToast(type === 'pivot'
    ? '🔄 انقر على المركز واسحب لتحديد نصف قطر البيفوت'
    : '📐 انقر لإضافة نقاط المضلع — انقر مرتين للإنهاء', 'info', 5000);
}

function cancelDrawing() {
  _draw.deleteAll();
  _draw.changeMode('simple_select');
  _drawingType = null;
  _pendingFeature = null;
  _editingId = null;

  document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active'));
  _setPanel('list');
  _resetForm();
}

// ── Draw Events ───────────────────────────────────────────────

function _onDrawCreate(e) {
  if (!e.features || !e.features.length) return;
  var feat = e.features[0];
  _pendingFeature = feat;

  var area = _calcArea(feat.geometry);
  _updateAreaDisplay(area);

  // Auto-generate default name
  var typeForCounter = _drawingType || 'plot';
  _plotCounter[typeForCounter]++;
  var defaultName = typeForCounter === 'pivot'
    ? 'P' + String(_plotCounter[typeForCounter]).padStart(2, '0')
    : 'Plot ' + _plotCounter[typeForCounter];

  document.getElementById('plot-name').value = defaultName;
  document.getElementById('plot-type-display').textContent =
    typeForCounter === 'pivot' ? '🔄 بيفوت' : '📐 حوض';

  _setPanel('form');
  document.getElementById('plot-name').focus();
}

function _onDrawUpdate(e) {
  if (!e.features || !e.features.length) return;
  _pendingFeature = e.features[0];
  var area = _calcArea(_pendingFeature.geometry);
  _updateAreaDisplay(area);
}

function _onDrawDelete() {
  cancelDrawing();
}

function _onSelectionChange(e) {
  // Deselect if user clicks off
  if (!e.features || !e.features.length) {
    _closePopup();
  }
}

// ── Area Calculation ──────────────────────────────────────────

function _calcArea(geometry) {
  try {
    // Handle circle drawn by draw_circle mode (stored as Point + radius property)
    if (geometry.type === 'Point') {
      // draw_circle stores radius in draw feature properties; fallback to 0
      return { m2: 0, feddan: 0, hectare: 0 };
    }
    var fc = { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: geometry, properties: {} }] };
    var m2 = turf.area(fc);
    return {
      m2:      m2,
      feddan:  m2 / FEDDAN,
      hectare: m2 / HECTARE,
      qirat:   m2 / QIRAT,
    };
  } catch(e) {
    return { m2: 0, feddan: 0, hectare: 0, qirat: 0 };
  }
}

function _calcAreaFromFeature(feat) {
  // draw_circle uses 'Circle' type with center + radius
  if (feat.properties && feat.properties.isCircle && feat.properties.center) {
    var r = feat.properties.radiusInKm ? feat.properties.radiusInKm * 1000 : 0;
    var m2 = Math.PI * r * r;
    return { m2: m2, feddan: m2 / FEDDAN, hectare: m2 / HECTARE, qirat: m2 / QIRAT };
  }
  return _calcArea(feat.geometry);
}

function _updateAreaDisplay(area) {
  _setText('area-m2',      area.m2      ? area.m2.toFixed(0)      : '0');
  _setText('area-feddan',  area.feddan  ? area.feddan.toFixed(2)  : '0');
  _setText('area-hectare', area.hectare ? area.hectare.toFixed(2) : '0');
  _setText('area-qirat',   area.qirat   ? area.qirat.toFixed(1)   : '0');
}

// ── Save Plot ─────────────────────────────────────────────────

function savePlot() {
  var name     = document.getElementById('plot-name').value.trim();
  var crop     = document.getElementById('crop-type').value;
  var sector   = document.getElementById('sector').value.trim();
  var region   = document.getElementById('region').value.trim();
  var soil     = document.getElementById('soil-type').value;
  var plDate   = document.getElementById('planting-date').value;
  var notes    = document.getElementById('plot-notes').value.trim();

  if (!name) { _showToast('⚠️ من فضلك أدخل اسم القطعة', 'error'); return; }
  if (!crop)  { _showToast('⚠️ من فضلك اختر نوع المحصول', 'error'); return; }
  if (!_pendingFeature && !_editingId) {
    _showToast('⚠️ لا توجد قطعة مرسومة', 'error'); return;
  }

  // Resolve geometry
  var feat = _pendingFeature;
  var geometry, area;

  if (feat) {
    // Is it a circle drawn by draw_circle_mode?
    if (feat.properties && feat.properties.isCircle) {
      var center = feat.properties.center;
      var radiusKm = feat.properties.radiusInKm || 0;
      // Convert to polygon for storage (64-point circle approximation)
      geometry = turf.circle(center, radiusKm, { steps: 64 }).geometry;
      area = _calcAreaFromFeature(feat);
    } else {
      geometry = feat.geometry;
      area = _calcArea(geometry);
    }
  } else if (_editingId !== null) {
    // editing existing — find it
    var existing = _plots.find(function(p) { return p.id === _editingId; });
    if (!existing) { cancelDrawing(); return; }
    geometry = existing.geometry;
    area = _calcArea(geometry);
  }

  var centroid = turf.centroid({ type: 'Feature', geometry: geometry, properties: {} });
  var centerLon = centroid.geometry.coordinates[0];
  var centerLat = centroid.geometry.coordinates[1];

  if (_editingId !== null) {
    // Update existing
    var idx = _plots.findIndex(function(p) { return p.id === _editingId; });
    if (idx !== -1) {
      _plots[idx] = Object.assign(_plots[idx], {
        name: name, crop_type: crop, sector: sector, region: region,
        soil_type: soil, planting_date: plDate, notes: notes,
        area_m2: area.m2, area_feddan: area.feddan, area_hectare: area.hectare,
        center_lat: centerLat, center_lon: centerLon, type: _drawingType || _plots[idx].type,
      });
    }
    _showToast('✅ تم تحديث القطعة: ' + name, 'success');
  } else {
    // New plot
    var newId = Date.now();
    _plots.push({
      id:           newId,
      name:         name,
      type:         _drawingType || 'plot',
      geometry:     geometry,
      area_m2:      area.m2,
      area_feddan:  area.feddan,
      area_hectare: area.hectare,
      area_qirat:   area.qirat,
      crop_type:    crop,
      sector:       sector,
      region:       region,
      soil_type:    soil,
      planting_date: plDate,
      notes:        notes,
      center_lat:   centerLat,
      center_lon:   centerLon,
    });
    _showToast('✅ تم حفظ القطعة: ' + name, 'success');
  }

  // Clear draw
  _draw.deleteAll();
  _draw.changeMode('simple_select');
  _pendingFeature = null;
  _editingId = null;
  _drawingType = null;
  document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active'));

  _resetForm();
  _setPanel('list');
  _refreshAllLayers();
  _saveToLocalStorage();
}

// ── Edit / Delete ─────────────────────────────────────────────

function editPlot(id) {
  var plot = _plots.find(function(p) { return p.id == id; });
  if (!plot) return;

  _editingId = plot.id;
  _drawingType = plot.type;

  // Populate form
  document.getElementById('plot-name').value        = plot.name;
  document.getElementById('crop-type').value        = plot.crop_type || '';
  document.getElementById('sector').value           = plot.sector || '';
  document.getElementById('region').value           = plot.region || '';
  document.getElementById('soil-type').value        = plot.soil_type || '';
  document.getElementById('planting-date').value    = plot.planting_date || '';
  document.getElementById('plot-notes').value       = plot.notes || '';
  document.getElementById('plot-type-display').textContent =
    plot.type === 'pivot' ? '🔄 بيفوت' : '📐 حوض';

  _updateAreaDisplay({
    m2: plot.area_m2, feddan: plot.area_feddan,
    hectare: plot.area_hectare, qirat: plot.area_qirat || 0,
  });

  _setPanel('form');
  _closePopup();
  _showToast('✏️ عدّل البيانات ثم اضغط "حفظ"', 'info');
}

function deletePlot(id) {
  var plot = _plots.find(function(p) { return p.id == id; });
  if (!plot) return;

  if (!confirm('هل أنت متأكد من حذف "' + plot.name + '"؟')) return;

  _plots = _plots.filter(function(p) { return p.id != id; });
  _closePopup();
  _refreshAllLayers();
  _saveToLocalStorage();
  _showToast('🗑️ تم حذف القطعة: ' + plot.name, 'info');
}

function zoomToPlot(id) {
  var plot = _plots.find(function(p) { return p.id == id; });
  if (!plot) return;
  var bbox = turf.bbox(plot.geometry);
  _map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 60, duration: 800 });
}

// ── Map Click Popup ───────────────────────────────────────────

function _onLayerClick(e) {
  if (!e.features || !e.features.length) return;
  var props = e.features[0].properties;
  var plot = _plots.find(function(p) { return p.id == props.id || p.name === props.name; });
  if (!plot) return;

  var html = '<div class="map-popup" dir="rtl">'
    + '<h3 style="margin:0 0 8px;color:#10b981;font-size:15px;">' + plot.name + '</h3>'
    + '<div class="popup-row"><span class="popup-key">النوع:</span> <span>' + (plot.type === 'pivot' ? '🔄 بيفوت' : '📐 حوض') + '</span></div>'
    + '<div class="popup-row"><span class="popup-key">المحصول:</span> <span>' + _cropEmoji(plot.crop_type) + ' ' + (plot.crop_type || '—') + '</span></div>'
    + '<div class="popup-row"><span class="popup-key">المساحة:</span> <span>' + (plot.area_feddan ? plot.area_feddan.toFixed(2) : '0') + ' فدان (' + (plot.area_m2 ? (plot.area_m2).toFixed(0) : '0') + ' م²)</span></div>'
    + (plot.sector ? '<div class="popup-row"><span class="popup-key">القطاع:</span> <span>' + plot.sector + '</span></div>' : '')
    + (plot.region ? '<div class="popup-row"><span class="popup-key">المنطقة:</span> <span>' + plot.region + '</span></div>' : '')
    + (plot.soil_type ? '<div class="popup-row"><span class="popup-key">التربة:</span> <span>' + plot.soil_type + '</span></div>' : '')
    + (plot.planting_date ? '<div class="popup-row"><span class="popup-key">زراعة:</span> <span>' + plot.planting_date + '</span></div>' : '')
    + (plot.notes ? '<div class="popup-row"><span class="popup-key">ملاحظات:</span> <span>' + plot.notes + '</span></div>' : '')
    + '<div class="popup-actions">'
    + '<button onclick="editPlot(' + plot.id + ')" class="popup-btn popup-btn-edit">✏️ تعديل</button>'
    + '<button onclick="deletePlot(' + plot.id + ')" class="popup-btn popup-btn-del">🗑️ حذف</button>'
    + '</div></div>';

  if (_markerSearch) { _markerSearch.remove(); _markerSearch = null; }

  new maplibregl.Popup({ closeOnClick: true, maxWidth: '260px', className: 'farm-popup' })
    .setLngLat([plot.center_lon, plot.center_lat])
    .setHTML(html)
    .addTo(_map);
}

function _closePopup() {
  document.querySelectorAll('.maplibregl-popup').forEach(function(el) { el.remove(); });
}

// ── View Controls ─────────────────────────────────────────────

function setMapStyle(style) {
  var styles = {
    satellite: _buildSatelliteStyle(),
    hybrid:    _buildHybridStyle(),
    osm:       _buildOSMStyle(),
  };
  document.querySelectorAll('.style-btn').forEach(function(b) { b.classList.remove('active'); });
  var activeBtn = document.getElementById('btn-style-' + style);
  if (activeBtn) activeBtn.classList.add('active');

  _map.setStyle(styles[style] || _buildSatelliteStyle());
  _map.once('styledata', function () {
    _initSources();
    _refreshAllLayers();
  });
}

function toggle3D() {
  _3dActive = !_3dActive;
  if (_3dActive) {
    _map.easeTo({ pitch: 45, duration: 800 });
    _pitch = 45;
    document.getElementById('btn-3d-toggle').classList.add('active');
    document.getElementById('btn-3d-toggle').innerHTML = '🏔️ إيقاف 3D';
  } else {
    _map.easeTo({ pitch: 0, duration: 800 });
    _pitch = 0;
    document.getElementById('btn-3d-toggle').classList.remove('active');
    document.getElementById('btn-3d-toggle').innerHTML = '🏔️ تفعيل 3D';
  }
}

function rotateBearing(delta) {
  _bearing = (_bearing + delta + 360) % 360;
  _map.easeTo({ bearing: _bearing, duration: 500 });
}

function adjustPitch(delta) {
  _pitch = Math.max(0, Math.min(85, _pitch + delta));
  _map.easeTo({ pitch: _pitch, duration: 400 });
}

function resetMapView() {
  _bearing = 0;
  _pitch   = 0;
  _3dActive = false;
  _map.easeTo({ bearing: 0, pitch: 0, duration: 600 });
  document.getElementById('btn-3d-toggle').classList.remove('active');
  document.getElementById('btn-3d-toggle').innerHTML = '🏔️ تفعيل 3D';

  if (_plots.length > 0) {
    var combined = turf.bbox({ type: 'FeatureCollection', features: _plots.map(function(p) {
      return { type: 'Feature', geometry: p.geometry, properties: {} };
    })});
    _map.fitBounds([[combined[0], combined[1]], [combined[2], combined[3]]], { padding: 60, duration: 800 });
  }
}

// ── Location Search ───────────────────────────────────────────

async function handleSearch(e) {
  if (e.key !== 'Enter') return;
  var q = document.getElementById('location-search').value.trim();
  if (!q) return;

  var btn = document.getElementById('btn-search');
  if (btn) { btn.disabled = true; btn.textContent = '⌛'; }

  try {
    var url = 'https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(q);
    var res  = await fetch(url, { headers: { 'Accept-Language': 'ar,en' } });
    var data = await res.json();

    if (data && data.length > 0) {
      var r = data[0];
      _map.flyTo({ center: [parseFloat(r.lon), parseFloat(r.lat)], zoom: 15, duration: 1200 });

      if (_markerSearch) _markerSearch.remove();
      _markerSearch = new maplibregl.Marker({ color: '#10b981' })
        .setLngLat([parseFloat(r.lon), parseFloat(r.lat)])
        .setPopup(new maplibregl.Popup().setHTML('<div dir="rtl" style="font-family:Cairo,sans-serif;font-size:13px;">' + r.display_name + '</div>'))
        .addTo(_map);
      _markerSearch.getPopup().addTo(_map);
    } else {
      _showToast('❌ لم يتم العثور على الموقع', 'error');
    }
  } catch(err) {
    _showToast('⚠️ خطأ في البحث: ' + err.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔍'; }
  }
}

// ── Import / Export ───────────────────────────────────────────

function importGeoJSON() {
  document.getElementById('file-input').click();
}

function handleFileImport(event) {
  var file = event.target.files[0];
  if (!file) return;

  var reader = new FileReader();
  reader.onload = function(e) {
    try {
      var fc = JSON.parse(e.target.result);
      if (!fc || !fc.features) throw new Error('ليس ملف GeoJSON صحيح');

      var added = 0;
      fc.features.forEach(function(feat) {
        if (!feat.geometry) return;
        if (feat.geometry.type !== 'Polygon' && feat.geometry.type !== 'MultiPolygon') return;

        var area = _calcArea(feat.geometry);
        var centroid = turf.centroid(feat).geometry.coordinates;
        var props = feat.properties || {};
        var typeGuess = (props.plot_type || 'plot');
        _plotCounter[typeGuess] = (_plotCounter[typeGuess] || 0) + 1;

        _plots.push({
          id:            Date.now() + added,
          name:          props.plot_name || props.name || 'Imported ' + (_plots.length + 1),
          type:          typeGuess,
          geometry:      feat.geometry,
          area_m2:       area.m2,
          area_feddan:   area.feddan,
          area_hectare:  area.hectare,
          area_qirat:    area.qirat,
          crop_type:     props.crop_type || '',
          sector:        props.sector || '',
          region:        props.region || '',
          soil_type:     props.soil_type || '',
          planting_date: props.planting_date || '',
          notes:         props.notes || 'Imported from file',
          center_lat:    centroid[1],
          center_lon:    centroid[0],
        });
        added++;
      });

      _refreshAllLayers();
      _saveToLocalStorage();

      if (added === 0) {
        _showToast('⚠️ لم يتم العثور على مضلعات في الملف', 'error');
      } else {
        var combined = turf.bbox({ type: 'FeatureCollection', features: _plots.map(function(p) {
          return { type: 'Feature', geometry: p.geometry, properties: {} };
        })});
        _map.fitBounds([[combined[0], combined[1]], [combined[2], combined[3]]], { padding: 60 });
        _showToast('✅ تم استيراد ' + added + ' قطعة من الملف', 'success');
      }
    } catch(err) {
      _showToast('❌ خطأ: ' + err.message, 'error');
    }
    event.target.value = '';  // reset input
  };
  reader.readAsText(file);
}

function exportGeoJSON() {
  if (_plots.length === 0) { _showToast('⚠️ لا توجد قطع للتصدير', 'error'); return; }

  var fc = {
    type: 'FeatureCollection',
    features: _plots.map(function(p) {
      return {
        type: 'Feature',
        geometry: p.geometry,
        properties: {
          plot_name:     p.name,
          plot_type:     p.type,
          crop_type:     p.crop_type,
          sector:        p.sector,
          region:        p.region,
          soil_type:     p.soil_type,
          planting_date: p.planting_date,
          area_m2:       p.area_m2,
          area_feddan:   p.area_feddan,
          area_hectare:  p.area_hectare,
          notes:         p.notes,
        }
      };
    })
  };

  var blob = new Blob([JSON.stringify(fc, null, 2)], { type: 'application/json' });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'farm_plots_' + new Date().toISOString().slice(0,10) + '.geojson';
  a.click();
  _showToast('✅ تم تصدير ' + _plots.length + ' قطعة', 'success');
}

// ── Finish Setup ──────────────────────────────────────────────

async function finishSetup() {
  if (_plots.length === 0) {
    _showToast('⚠️ من فضلك ارسم قطعة واحدة على الأقل قبل الإنهاء', 'error');
    return;
  }

  var btn = document.getElementById('btn-finish');
  btn.disabled = true;
  btn.innerHTML = '⏳ جاري الحفظ...';

  try {
    var payload = _plots.map(function(p) {
      return {
        plot_name:     p.name,
        plot_type:     p.type,
        geometry:      JSON.stringify(p.geometry),
        center_lat:    p.center_lat,
        center_lon:    p.center_lon,
        area_m2:       p.area_m2,
        area_feddan:   p.area_feddan,
        area_hectare:  p.area_hectare,
        crop_type:     p.crop_type,
        sector:        p.sector,
        region:        p.region,
        soil_type:     p.soil_type,
        planting_date: p.planting_date,
        notes:         p.notes,
      };
    });

    var res  = await fetch('/api/farm/save-plots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plots: payload }),
    });
    var data = await res.json();

    if (data.success) {
      localStorage.removeItem('tayseer_draft_plots');
      _showToast('🎉 ' + data.message, 'success', 3000);
      setTimeout(function() { window.location.href = '/dashboard'; }, 2500);
    } else {
      throw new Error(data.error || 'فشل الحفظ');
    }
  } catch(err) {
    _showToast('❌ ' + err.message, 'error');
    btn.disabled = false;
    btn.innerHTML = '✅ إنهاء وحفظ البيانات';
  }
}

// ── Plot List Rendering ───────────────────────────────────────

function _renderPlotsList() {
  var container = document.getElementById('plots-list');
  var count = _plots.length;
  document.getElementById('plots-count').textContent = count;

  if (count === 0) {
    container.innerHTML = '<div class="empty-state">📋 لم تُرسم أي قطع بعد</div>';
    return;
  }

  container.innerHTML = _plots.map(function(plot) {
    var color = plot.type === 'pivot' ? '#3b82f6' : '#f59e0b';
    return '<div class="plot-card" onclick="zoomToPlot(' + plot.id + ')">'
      + '<div class="plot-card-header">'
      + '<span class="plot-card-name" style="color:' + color + '">' + plot.name + '</span>'
      + '<span class="plot-badge badge-' + plot.type + '">' + (plot.type === 'pivot' ? '🔄 بيفوت' : '📐 حوض') + '</span>'
      + '</div>'
      + '<div class="plot-card-info">'
      + _cropEmoji(plot.crop_type) + ' ' + (plot.crop_type || '—')
      + ' &nbsp;•&nbsp; ' + (plot.area_feddan ? plot.area_feddan.toFixed(2) : '0') + ' فدان'
      + (plot.sector ? ' &nbsp;•&nbsp; ' + plot.sector : '')
      + '</div>'
      + '<div class="plot-card-actions">'
      + '<button class="act-btn act-edit" onclick="event.stopPropagation();editPlot(' + plot.id + ')">✏️</button>'
      + '<button class="act-btn act-del" onclick="event.stopPropagation();deletePlot(' + plot.id + ')">🗑️</button>'
      + '<button class="act-btn act-zoom" onclick="event.stopPropagation();zoomToPlot(' + plot.id + ')">🔍</button>'
      + '</div>'
      + '</div>';
  }).join('');
}

// ── Stats Banner ──────────────────────────────────────────────

function _updateStatsBanner() {
  var totalFed    = _plots.reduce(function(s, p) { return s + (p.area_feddan || 0); }, 0);
  var totalHa     = _plots.reduce(function(s, p) { return s + (p.area_hectare || 0); }, 0);
  var pivotCount  = _plots.filter(function(p) { return p.type === 'pivot'; }).length;
  var plotCount   = _plots.filter(function(p) { return p.type === 'plot';  }).length;

  _setText('stat-total-plots', _plots.length);
  _setText('stat-total-fed',   totalFed.toFixed(1));
  _setText('stat-total-ha',    totalHa.toFixed(1));
  _setText('stat-pivots',      pivotCount);
  _setText('stat-plots',       plotCount);
}

// ── Crop Dropdown ─────────────────────────────────────────────

function _populateCropDropdown() {
  var sel = document.getElementById('crop-type');
  if (!sel) return;
  sel.innerHTML = '<option value="">-- اختر المحصول --</option>'
    + CROPS.map(function(c) {
        return '<option value="' + c.v + '">' + c.e + ' ' + c.v + '</option>';
      }).join('');
}

function _cropEmoji(crop) {
  if (!crop) return '🌱';
  var found = CROPS.find(function(c) { return c.v === crop; });
  return found ? found.e : '🌱';
}

// ── UI Helpers ────────────────────────────────────────────────

function _setPanel(name) {
  // name: 'hint' | 'form' | 'list'
  ['hint-panel', 'form-panel', 'list-panel'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  var target = document.getElementById(name + '-panel');
  if (target) target.style.display = 'block';
}

function _resetForm() {
  ['plot-name','sector','region','plot-notes'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.value = '';
  });
  ['crop-type','soil-type','planting-date'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.value = '';
  });
  _updateAreaDisplay({ m2: 0, feddan: 0, hectare: 0, qirat: 0 });
}

function _setText(id, val) {
  var el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ── Toast Notifications ───────────────────────────────────────

function _showToast(msg, type, duration) {
  duration = duration || 3500;
  var bg = { success: '#10b981', error: '#ef4444', info: '#3b82f6', warn: '#f59e0b' }[type] || '#374151';
  var el = document.createElement('div');
  el.style.cssText = [
    'position:fixed;bottom:24px;right:24px;z-index:99999;',
    'background:' + bg + ';color:#fff;',
    'padding:12px 20px;border-radius:10px;',
    'font-family:Cairo,sans-serif;font-size:14px;font-weight:600;',
    'box-shadow:0 4px 20px rgba(0,0,0,0.3);',
    'animation:slideUp .3s ease;',
    'max-width:320px;direction:rtl;',
  ].join('');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(function() {
    el.style.opacity = '0';
    el.style.transition = 'opacity .4s';
    setTimeout(function() { if (el.parentNode) el.parentNode.removeChild(el); }, 400);
  }, duration);
}

// ── localStorage persistence ──────────────────────────────────

function _saveToLocalStorage() {
  try {
    var stored = _plots.map(function(p) {
      var cp = Object.assign({}, p);
      return cp;
    });
    localStorage.setItem('tayseer_draft_plots', JSON.stringify(stored));
  } catch(e) {}
}

function _loadFromLocalStorage() {
  try {
    var raw = localStorage.getItem('tayseer_draft_plots');
    if (!raw) return;
    var stored = JSON.parse(raw);
    if (Array.isArray(stored) && stored.length > 0) {
      _plots = stored;
      _showToast('📋 تم استعادة ' + stored.length + ' قطعة من الجلسة السابقة', 'info');
    }
  } catch(e) {}
}

function clearDraft() {
  if (!confirm('هل تريد مسح كل القطع المحفوظة مؤقتاً؟')) return;
  _plots = [];
  localStorage.removeItem('tayseer_draft_plots');
  _draw.deleteAll();
  _refreshAllLayers();
  _showToast('🗑️ تم مسح المسودة', 'info');
}

// ── Load Existing Plots from backend ─────────────────────────

async function _loadExistingPlots() {
  // First restore draft from localStorage
  _loadFromLocalStorage();

  // Then try backend
  try {
    var res  = await fetch('/api/farm/get-plots');
    var data = await res.json();
    if (data.success && data.plots && data.plots.length > 0) {
      // Merge — backend takes precedence; skip if local already has them
      if (_plots.length === 0) {
        _plots = data.plots.map(function(p) {
          return {
            id:            p.id,
            name:          p.plot_name,
            type:          p.plot_type,
            geometry:      typeof p.geometry === 'string' ? JSON.parse(p.geometry) : p.geometry,
            area_m2:       p.area_m2,
            area_feddan:   p.area_feddan,
            area_hectare:  p.area_hectare,
            area_qirat:    (p.area_m2 / QIRAT),
            crop_type:     p.crop_type,
            sector:        p.sector,
            region:        p.region,
            soil_type:     p.soil_type,
            planting_date: p.planting_date,
            notes:         p.notes,
            center_lat:    p.center_lat,
            center_lon:    p.center_lon,
          };
        });
      }
      _refreshAllLayers();

      if (_plots.length > 0) {
        var combined = turf.bbox({ type: 'FeatureCollection', features: _plots.map(function(p) {
          return { type: 'Feature', geometry: p.geometry, properties: {} };
        })});
        _map.fitBounds([[combined[0], combined[1]], [combined[2], combined[3]]], { padding: 60 });
      }
    } else {
      _refreshAllLayers();
    }
  } catch(err) {
    // Backend unavailable — use localStorage only
    _refreshAllLayers();
  }
}

// ── Draw Styles ───────────────────────────────────────────────

function _drawStyles() {
  return [
    {
      id: 'gl-draw-polygon-fill',
      type: 'fill',
      filter: ['all', ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']],
      paint: { 'fill-color': '#10b981', 'fill-opacity': 0.15 }
    },
    {
      id: 'gl-draw-polygon-stroke-active',
      type: 'line',
      filter: ['all', ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']],
      paint: { 'line-color': '#10b981', 'line-width': 2.5, 'line-dasharray': [0.2, 2] }
    },
    {
      id: 'gl-draw-polygon-midpoint',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'midpoint']],
      paint: { 'circle-radius': 4, 'circle-color': '#10b981' }
    },
    {
      id: 'gl-draw-vertex-halo',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'vertex']],
      paint: { 'circle-radius': 8, 'circle-color': '#fff' }
    },
    {
      id: 'gl-draw-vertex',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'vertex']],
      paint: { 'circle-radius': 5, 'circle-color': '#10b981' }
    },
    {
      id: 'gl-draw-line',
      type: 'line',
      filter: ['all', ['==', '$type', 'LineString'], ['!=', 'mode', 'static']],
      paint: { 'line-color': '#10b981', 'line-width': 2.5 }
    },
  ];
}

// ── Global Entry Point ────────────────────────────────────────

window.addEventListener('DOMContentLoaded', function() {
  // Inject CSS for toast animation
  var style = document.createElement('style');
  style.textContent = '@keyframes slideUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}';
  document.head.appendChild(style);
});
