// =========================================================
// Tayseer Agri-SaT — Dashboard Logic (SPA & API)
// =========================================================

let farmData = {};
let forecastData = [];
let map;
let drawControl; // For Mapbox Draw if added later
let satelliteStyle = {
    'version': 8,
    'sources': {
        'satellite': {
            'type': 'raster',
            'tiles': ['https://mt1.google.com/vt/lyrs=y&hl=ar&x={x}&y={y}&z={z}'],
            'tileSize': 256
        }
    },
    'layers': [
        {
            'id': 'satellite-layer',
            'type': 'raster',
            'source': 'satellite',
            'minzoom': 0,
            'maxzoom': 22
        }
    ]
};
let streetStyle = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json';

let ndviLayerActive = false, etLayerActive = false;
let activeGEELayerName = null;
let chatOpen = false;

// ── Translation Helper ───────────────────────────────────────
window.t = function (ar, en) {
    return window.CURRENT_LANG === 'en' ? en : ar;
};

// ── Init ─────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
    initMap();
    loadFarmData();
    loadWeather();
    loadGEETiles();               // fetch GEE satellite tile URLs
    fetchFullAddress();           // جلب العنوان الكامل بالعربي
    updateAreaFromGeoJSON();      // حساب المساحة من الأشكال المرسومة

    // Pre-fill irrigation settings from user profile
    const typeSelect = document.getElementById('irrigation-type-select');
    const flowInput = document.getElementById('pump-flow-input');
    const powerInput = document.getElementById('pump-power-input');
    if (typeSelect) typeSelect.value = window.IRRIGATION_TYPE || 'drip';
    if (flowInput) flowInput.value = window.PUMP_FLOW_RATE || '';
    if (powerInput) powerInput.value = window.PUMP_POWER_KW || '';

    switchTab('overview');

    setInterval(() => {
        console.log('[AutoRefresh] Refreshing data...');
        loadFarmData();
        loadWeather();
    }, 60000);
});

// ── SPA Tab Navigation ───────────────────────────────────────
function switchTab(sectionId) {
    document.querySelectorAll('.dashboard-section').forEach(sec => sec.classList.remove('active'));
    const target = document.getElementById(`section-${sectionId}`);
    if (target) target.classList.add('active');
    document.querySelectorAll('.nav-link').forEach(link => {
        if (link.getAttribute('data-target') === sectionId) link.classList.add('active');
        else link.classList.remove('active');
    });
    if (sectionId === 'map' && map) {
        setTimeout(() => map.resize(), 200);
    }
}

// ── Irrigation Settings Save ─────────────────────────────────
async function saveIrrigationSettings() {
    const typeVal = document.getElementById('irrigation-type-select').value;
    const flowVal = document.getElementById('pump-flow-input').value;
    const powerVal = document.getElementById('pump-power-input').value;

    const payload = {
        irrigation_type: typeVal,
        pump_flow_rate: flowVal ? parseFloat(flowVal) : null,
        pump_power_kw: powerVal ? parseFloat(powerVal) : null
    };

    const msgEl = document.getElementById('irrigation-save-msg');
    msgEl.style.display = 'inline-block';
    msgEl.className = 'ml-3 mr-3 text-sm text-yellow-400';
    msgEl.innerText = window.t('جاري الحفظ...', 'Saving...');

    try {
        const res = await fetch('/api/profile/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.success) {
            msgEl.className = 'ml-3 mr-3 text-sm text-green-400';
            msgEl.innerText = window.t('✅ تم الحفظ. جاري التحديث...', '✅ Saved. Refreshing...');

            // Wait 1s then reload to apply new formulas
            setTimeout(() => {
                window.location.reload();
            }, 1000);
        } else {
            msgEl.className = 'ml-3 mr-3 text-sm text-red-400';
            msgEl.innerText = '❌ ' + (data.message || 'Error');
        }
    } catch (e) {
        msgEl.className = 'ml-3 mr-3 text-sm text-red-400';
        msgEl.innerText = '❌ ' + e.message;
    }
}

// ── Reverse Geocoding ────────────────────────────────────────
async function fetchFullAddress() {
    const el = document.getElementById('farm-full-address');
    if (!el) return;
    try {
        const url = `https://nominatim.openstreetmap.org/reverse?format=json&lat=${window.USER_LAT}&lon=${window.USER_LON}&addressdetails=1&accept-language=ar`;
        const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
        const data = await res.json();
        if (data && data.address) {
            const a = data.address;
            const parts = [];
            if (a.village || a.suburb || a.quarter || a.neighbourhood)
                parts.push(a.village || a.suburb || a.quarter || a.neighbourhood);
            if (a.city || a.town || a.county || a.district)
                parts.push(a.city || a.town || a.county || a.district);
            if (a.state || a.region) parts.push(a.state || a.region);
            if (a.country) parts.push(a.country);
            el.textContent = parts.filter(Boolean).join(window.t(' ، ', ', ')) || data.display_name || '—';
        } else { el.textContent = '—'; }
    } catch { el.textContent = '—'; }
}

// ── Area Calculation Helpers ──────────────────────────────────
function calculatePolygonAreaM2(coordinates) {
    const toRad = d => d * Math.PI / 180;
    const R = 6371000;
    let area = 0;
    const n = coordinates.length;
    if (n < 3) return 0;
    for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        const xi = toRad(coordinates[i][0]);
        const yi = toRad(coordinates[i][1]);
        const xj = toRad(coordinates[j][0]);
        const yj = toRad(coordinates[j][1]);
        area += (xj - xi) * (2 + Math.sin(yi) + Math.sin(yj));
    }
    return Math.abs(area * R * R / 2);
}

function calculateCircleAreaM2(radiusM) {
    return Math.PI * radiusM * radiusM;
}

function formatAreaArabic(areaM2) {
    const FEDDAN_M2 = 4200;
    const QIRAT_M2 = 175;
    const feddan = areaM2 / FEDDAN_M2;
    if (feddan >= 1.0) return `${feddan.toFixed(2)} ${window.t('فدان', 'Feddan')}`;
    const qirat = areaM2 / QIRAT_M2;
    if (qirat >= 1.0) return `${qirat.toFixed(1)} ${window.t('قيراط', 'Qirat')}`;
    return `${Math.round(areaM2)} ${window.t('م²', 'm²')}`;
}

// ── Area Display from drawn GeoJSON ──────────────────────────
function updateAreaFromGeoJSON() {
    if (!window.FARM_GEOJSON || window.FARM_GEOJSON === 'None' || window.FARM_GEOJSON.trim() === '') return;
    const areaEl = document.getElementById('area-feddan');
    if (!areaEl) return;
    try {
        const geoData = JSON.parse(window.FARM_GEOJSON);
        const features = geoData.features || (geoData.type === 'Feature' ? [geoData] : []);
        let totalM2 = 0;
        features.forEach(f => {
            const props = f.properties || {};
            const geom = f.geometry;
            if (!geom) return;
            if (props.sectorAngle && props.sectorRadius) {
                // نصف دوائر / ربع دوائر: صيغة تحليلية دقيقة
                totalM2 += (props.sectorAngle / 360) * Math.PI * props.sectorRadius ** 2;
            } else if (geom.type === 'Point' && props.radius) {
                totalM2 += Math.PI * props.radius ** 2;
            } else if (geom.type === 'Polygon') {
                totalM2 += calculatePolygonAreaM2(geom.coordinates[0]);
            } else if (geom.type === 'MultiPolygon') {
                geom.coordinates.forEach(poly => { totalM2 += calculatePolygonAreaM2(poly[0]); });
            }
        });
        if (totalM2 > 0) {
            const FEDDAN = 4200, QIRAT = 175;
            const feddan = totalM2 / FEDDAN;
            if (feddan >= 1.0) {
                areaEl.textContent = feddan.toFixed(2);
            } else {
                const qirat = totalM2 / QIRAT;
                const parentSpan = areaEl.parentElement;
                if (qirat >= 1.0) {
                    if (parentSpan) parentSpan.innerHTML = `🌾 ${qirat.toFixed(1)} ${window.t('قيراط', 'Qirat')}`;
                } else {
                    if (parentSpan) parentSpan.innerHTML = `🌾 ${Math.round(totalM2)} ${window.t('م²', 'm²')}`;
                }
            }
        }
    } catch (e) { console.error('Area calc error:', e); }
}

// ── Map ──────────────────────────────────────────────────────
// Stores {index: leafletLayer} so loadZoneMetrics can re-style after API returns
window._zoneLayers = {};

function _ndviHealthColor(ndvi) {
    // Returns {border, fill, emoji, label_ar, label_en} based on NDVI
    if (ndvi === null || ndvi === undefined) return { border: '#888', fill: '#888', emoji: '❓', label_ar: 'غير معروف', label_en: 'Unknown' };
    if (ndvi >= 0.55) return { border: '#16a34a', fill: '#22c55e', emoji: '✅', label_ar: 'سليم', label_en: 'Healthy' };
    if (ndvi >= 0.35) return { border: '#d97706', fill: '#f59e0b', emoji: '⚠️', label_ar: 'يحتاج متابعة', label_en: 'Needs Attention' };
    return { border: '#dc2626', fill: '#ef4444', emoji: '🔴', label_ar: 'في خطر', label_en: 'At Risk' };
}

function _zoneHealthBadgeHtml(zone) {
    const ndvi = zone.ndvi;
    const h = _ndviHealthColor(ndvi);
    const rtl = window.CURRENT_LANG === 'ar';
    const area = zone.area_feddan >= 1
        ? `${zone.area_feddan.toFixed(2)} ${window.t('فدان', 'fed')}`
        : `${(zone.area_m2 / 175).toFixed(1)} ${window.t('قيراط', 'qr')}`;

    const statusLabel = rtl ? h.label_ar : h.label_en;
    const wueVal = zone.wue != null ? zone.wue.toFixed(2) : '—';
    const etVal = zone.et != null ? zone.et.toFixed(1) : '—';
    const tvdiVal = zone.tvdi != null ? zone.tvdi.toFixed(2) : '—';
    const bioVal = zone.biomass != null ? zone.biomass.toFixed(2) : '—';
    const ndviVal = ndvi != null ? ndvi.toFixed(3) : '—';
    const lstVal = zone.lst != null ? `${zone.lst.toFixed(1)}°C` : '—';
    const sourceTag = zone.source === 'gee_zone'
        ? `<span style="background:#0f4c81;color:#93c5fd;padding:1px 6px;border-radius:6px;font-size:10px;">GEE ✓</span>`
        : `<span style="background:#44403c;color:#d6d3d1;padding:1px 6px;border-radius:6px;font-size:10px;">Physics</span>`;

    return `
<div style="min-width:220px;max-width:270px;font-family:inherit;direction:${rtl ? 'rtl' : 'ltr'}">
  <div style="font-size:15px;font-weight:700;color:#fff;margin-bottom:6px;">${zone.zone_label}</div>
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;">
    <span style="font-size:18px;">${h.emoji}</span>
    <span style="font-weight:700;color:${h.fill};font-size:13px;">${statusLabel}</span>
    ${sourceTag}
  </div>
  <table style="width:100%;font-size:12px;border-collapse:collapse;color:#e2e8f0;">
    <tr><td style="padding:2px 4px;color:#9ca3af;">📐 ${window.t('المساحة', 'Area')}</td><td style="padding:2px 4px;font-weight:600;">${area}</td></tr>
    <tr><td style="padding:2px 4px;color:#9ca3af;">🌿 NDVI</td><td style="padding:2px 4px;font-weight:600;color:${h.fill};">${ndviVal}</td></tr>
    <tr><td style="padding:2px 4px;color:#9ca3af;">🌡️ LST</td><td style="padding:2px 4px;font-weight:600;">${lstVal}</td></tr>
    <tr><td style="padding:2px 4px;color:#9ca3af;">💧 ET (${window.t('مم/يوم', 'mm/d')})</td><td style="padding:2px 4px;font-weight:600;">${etVal}</td></tr>
    <tr><td style="padding:2px 4px;color:#9ca3af;">☀️ TVDI</td><td style="padding:2px 4px;font-weight:600;">${tvdiVal}</td></tr>
    <tr><td style="padding:2px 4px;color:#9ca3af;">🌾 ${window.t('كتلة حيوية', 'Biomass')} (${window.t('طن/فدان','t/fed')})</td><td style="padding:2px 4px;font-weight:600;">${bioVal}</td></tr>
    <tr><td style="padding:2px 4px;color:#9ca3af;">⚡ WUE (${window.t('كجم/م³', 'kg/m³')})</td><td style="padding:2px 4px;font-weight:600;">${wueVal}</td></tr>
  </table>
</div>`;
}

function initMap() {
    map = new maplibregl.Map({
        container: 'farm-map',
        style: satelliteStyle,
        center: [window.USER_LON, window.USER_LAT],
        zoom: 14,
        pitch: 45, // Enable 3D tilt by default
        bearing: -17.6,
        attributionControl: false
    });

    map.addControl(new maplibregl.NavigationControl());

    map.on('load', () => {
        // Add User Marker
        const el = document.createElement('div');
        el.innerHTML = '<div style="font-size:28px;line-height:1;text-shadow:0 2px 4px rgba(0,0,0,0.5);">🌿</div>';
        new maplibregl.Marker(el)
            .setLngLat([window.USER_LON, window.USER_LAT])
            .setPopup(new maplibregl.Popup({ offset: 25 }).setHTML(`<b>${window.USER_NAME}</b><br>${window.t('موقعك الرئيسي', 'Main Location')}`))
            .addTo(map);

        if (window.FARM_GEOJSON && window.FARM_GEOJSON !== 'None' && window.FARM_GEOJSON.trim() !== '') {
            try {
                const geoData = JSON.parse(window.FARM_GEOJSON);

                map.addSource('farm-zones', {
                    'type': 'geojson',
                    'data': geoData
                });

                map.addLayer({
                    'id': 'farm-zones-fill',
                    'type': 'fill',
                    'source': 'farm-zones',
                    'paint': {
                        'fill-color': ['get', 'color'],
                        'fill-opacity': 0.35
                    }
                });

                map.addLayer({
                    'id': 'farm-zones-outline',
                    'type': 'line',
                    'source': 'farm-zones',
                    'paint': {
                        'line-color': ['get', 'color'],
                        'line-width': 2.5
                    }
                });

                // Calculate total area for display
                let totalAreaM2 = 0;
                const features = geoData.features || (geoData.type === 'Feature' ? [geoData] : []);
                features.forEach(feature => {
                    const geom = feature.geometry;
                    let areaM2 = 0;
                    if (feature.properties && feature.properties.sectorAngle && feature.properties.sectorRadius) {
                        areaM2 = (feature.properties.sectorAngle / 360) * Math.PI * feature.properties.sectorRadius ** 2;
                        totalAreaM2 += areaM2;
                    } else if (geom && geom.type === 'Polygon') {
                        areaM2 = calculatePolygonAreaM2(geom.coordinates[0]);
                        totalAreaM2 += areaM2;
                    } else if (geom && geom.type === 'MultiPolygon') {
                        geom.coordinates.forEach(poly => { areaM2 += calculatePolygonAreaM2(poly[0]); });
                        totalAreaM2 += areaM2;
                    }
                });

                if (totalAreaM2 > 0) {
                    const areaDisplay = document.getElementById('map-area-display');
                    if (areaDisplay) {
                        areaDisplay.innerHTML = `📐 ${window.t('إجمالي مساحة الأرض:', 'Total Land Area:')} <b style="color:#4ade80;">${formatAreaArabic(totalAreaM2)}</b>`;
                        areaDisplay.style.display = 'block';
                    }
                }

                // Temporary bounds fitting logic (simplified for MapLibre)
                const coordinates = features[0].geometry.coordinates[0];
                const bounds = coordinates.reduce((bounds, coord) => {
                    return bounds.extend(coord);
                }, new maplibregl.LngLatBounds(coordinates[0], coordinates[0]));
                map.fitBounds(bounds, { padding: 50 });

                // Load per-zone metrics
                loadZoneMetrics();

            } catch (e) {
                console.error("Failed to parse farm_geojson:", e);
            }
        }
    }); // end on map load
}

// ── Per-Zone Metrics Loader ───────────────────────────────────
async function loadZoneMetrics() {
    const panelEl = document.getElementById('zone-metrics-panel');
    if (panelEl) panelEl.innerHTML = `<div style="color:#9ca3af;font-size:12px;padding:8px;">${window.t('⏳ جاري تحليل كل منطقة بالقمر الصناعي...', '⏳ Analysing zones via satellite...')}</div>`;

    try {
        const res = await fetch('/api/zone-metrics');
        const data = await res.json();

        if (!data.success || !data.zones || data.zones.length === 0) {
            if (panelEl) panelEl.innerHTML = `<div style="color:#6b7280;font-size:12px;padding:8px;">${window.t('لا توجد مناطق محددة.', 'No zones defined.')}</div>`;
            return;
        }

        const zones = data.zones;
        console.log(`[ZoneMetrics] Loaded ${zones.length} zones`);

        // Re-style each zone layer and update its popup
        zones.forEach((zone, idx) => {
            const layer = window._zoneLayers[idx];
            if (!layer) return;

            const h = _ndviHealthColor(zone.ndvi);

            // Update polygon fill color to health status
            if (layer.setStyle) {
                layer.setStyle({
                    color: h.border,
                    fillColor: h.fill,
                    weight: 2.5,
                    fillOpacity: 0.45,
                });
            }

            // Replace popup with rich zone health card
            layer.setPopupContent(_zoneHealthBadgeHtml(zone));
        });

        // Build zone summary panel below the map
        if (panelEl) {
            panelEl.innerHTML = `
<div style="padding:4px 0 2px;font-size:11px;color:#9ca3af;font-weight:600;letter-spacing:.05em;">
  ${window.t('📊 ملخص صحة المناطق', '📊 Zone Health Summary')}
</div>
<div style="display:flex;flex-direction:column;gap:6px;margin-top:6px;">
  ${zones.map((z, i) => {
                const h = _ndviHealthColor(z.ndvi);
                const ndviText = z.ndvi != null ? z.ndvi.toFixed(2) : '—';
                const etText = z.et != null ? `${z.et.toFixed(1)} ${window.t('مم', 'mm')}` : '—';
                const area = z.area_feddan >= 1
                    ? `${z.area_feddan.toFixed(1)} ${window.t('فدان', 'fed')}`
                    : `${(z.area_m2 / 175).toFixed(0)} ${window.t('قيراط', 'qr')}`;
                return `
<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:8px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.07);">
  <div style="width:12px;height:12px;border-radius:50%;background:${h.fill};flex-shrink:0;"></div>
  <div style="flex:1;min-width:0;">
    <div style="font-size:12px;font-weight:600;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${z.zone_label}</div>
    <div style="font-size:11px;color:#9ca3af;">${area} · NDVI ${ndviText} · ET ${etText}</div>
  </div>
  <div style="font-size:14px;">${h.emoji}</div>
</div>`;
            }).join('')}
</div>`;
        }

        // Also update the crop health badge (risk map)
        if (zones.length > 0 && typeof updateRiskMapBadge === 'function') {
            // Use first/primary zone for the badge, or aggregate
            const primaryZone = zones.find(z => z.zone_type === 'crop') || zones[0];
            updateRiskMapBadge({
                ndvi: primaryZone.ndvi,
                et: primaryZone.et,
                biomass: primaryZone.biomass,
            });
        }

    } catch (err) {
        console.warn('[ZoneMetrics] Failed to load:', err);
        if (panelEl) panelEl.innerHTML = `<div style="color:#f87171;font-size:12px;padding:8px;">${window.t('⚠️ تعذّر تحميل بيانات المناطق.', '⚠️ Failed to load zone data.')}</div>`;
    }
}



function setBaseLayer(type) {
    if (type === 'satellite') {
        map.setStyle(satelliteStyle);
    } else {
        map.setStyle(streetStyle);
    }
    // Note: Changing style removes custom layers in MapLibre/Mapbox.
    // In a fully robust implementation we would need to re-add farm-zones and gee-layer on 'style.load'.
    // We will listen for style.load to re-add them if necessary.
}

map?.on('style.load', () => {
    // Basic re-addition logic could go here, or we simply let the user reload data.
});

// ── GEE Satellite Layer System ───────────────────────────────
let geeTileUrls = {};
let geePalettes = {};

// Layer display names for legends
const geeLayerLabels = {
    ndvi: { ar: 'مؤشر الغطاء النباتي (NDVI)', en: 'Vegetation Index (NDVI)' },
    ndwi: { ar: 'مؤشر الماء (NDWI)', en: 'Water Index (NDWI)' },
    ndsi: { ar: 'مؤشر NDSI', en: 'NDSI Index' },
    soil_moisture: { ar: 'رطوبة التربة (NDMI)', en: 'Soil Moisture (NDMI)' },
    et: { ar: 'التبخّر الفعلي (ET) mm/day', en: 'Actual ET (mm/day)' },
    biomass: { ar: 'الكتلة الحيوية (طن/فدان)', en: 'Biomass (t/feddan)' },
    tvdi: { ar: 'مؤشر الجفاف (TVDI)', en: 'Drought Index (TVDI)' },
};

async function loadGEETiles() {
    try {
        const res = await fetch('/api/gee-tiles');
        const data = await res.json();
        if (data.success && data.tiles) {
            geeTileUrls = data.tiles;
            geePalettes = data.palettes || {};
            console.log('[GEE] Tile URLs loaded:', Object.keys(geeTileUrls));

            // Auto-load NDVI layer for Risk Map
            if (!activeGEELayerName) toggleGEELayer('ndvi');
        } else {
            console.warn('[GEE] Tiles not available:', data.message);
        }
    } catch (err) {
        console.warn('[GEE] Failed to load tiles:', err);
    }
}

function toggleGEELayer(layerName) {
    // If same layer is active, turn it off
    if (activeGEELayerName === layerName) {
        if (geeLayerObj) map.removeLayer(geeLayerObj);
        geeLayerObj = null;
        activeGEELayerName = null;
        // Reset all buttons to outline
        document.querySelectorAll('#gee-layer-buttons button').forEach(btn => {
            btn.classList.remove('btn-green');
            btn.classList.add('btn-outline');
        });
        // Hide legend
        const legend = document.getElementById('gee-legend');
        if (legend) legend.style.display = 'none';
        return;
    }

    // Check if we have GEE tile URL for this layer
    const tileUrl = geeTileUrls[layerName];

    if (map.getLayer('gee-layer')) {
        map.removeLayer('gee-layer');
        map.removeSource('gee-source');
    }

    if (tileUrl) {
        map.addSource('gee-source', {
            'type': 'raster',
            'tiles': [tileUrl],
            'tileSize': 256
        });
        map.addLayer({
            'id': 'gee-layer',
            'type': 'raster',
            'source': 'gee-source',
            'paint': {
                'raster-opacity': 0.7
            }
        });
    }

    activeGEELayerName = layerName;

    // Update button states
    document.querySelectorAll('#gee-layer-buttons button').forEach(btn => {
        if (btn.getAttribute('data-layer') === layerName) {
            btn.classList.remove('btn-outline');
            btn.classList.add('btn-green');
        } else {
            btn.classList.remove('btn-green');
            btn.classList.add('btn-outline');
        }
    });

    // Show legend
    showGEELegend(layerName);
}

function showGEELegend(layerName) {
    const legend = document.getElementById('gee-legend');
    if (!legend) return;

    const palette = geePalettes[layerName];
    const label = geeLayerLabels[layerName]
        ? (window.CURRENT_LANG === 'ar' ? geeLayerLabels[layerName].ar : geeLayerLabels[layerName].en)
        : layerName;

    const labelEl = document.getElementById('gee-legend-label');
    const gradEl = document.getElementById('gee-legend-gradient');
    const minEl = document.getElementById('gee-legend-min');
    const maxEl = document.getElementById('gee-legend-max');

    if (labelEl) labelEl.textContent = label;

    if (palette && palette.palette) {
        gradEl.style.background = `linear-gradient(to right, ${palette.palette.join(', ')})`;
        if (minEl) minEl.textContent = palette.min;
        if (maxEl) maxEl.textContent = palette.max;
    } else {
        gradEl.style.background = '#555';
        if (minEl) minEl.textContent = '';
        if (maxEl) maxEl.textContent = '';
    }

    legend.style.display = 'block';
}

// Backward compatibility — map old functions to new system
function toggleNDVILayer() { toggleGEELayer('ndvi'); }
function toggleETLayer() { toggleGEELayer('et'); }


async function loadFarmData() {
    try {
        const res = await fetch('/api/farm-data');
        const data = await res.json();

        // Save to offline cache
        localStorage.setItem('tayseer_offline_farm_data', JSON.stringify(data));

        const loader = document.getElementById('loading-overlay');
        if (loader) loader.classList.add('hidden');

        if (data.success && data.farm && Object.keys(data.farm).length > 0
            && data.farm.ndvi !== undefined && data.farm.ndvi !== null) {
            farmData = data.farm;
            updateKPIs();
            renderNDVIChart(data.chart || []);
            renderAlerts(data.pivot_alert, data.agro_alerts || []);
        } else {
            showPipelineWaiting(data.pipeline_running);
        }
    } catch (e) {
        console.error("Error fetching farm data", e);
        const loader = document.getElementById('loading-overlay');
        if (loader) loader.classList.add('hidden');

        // Fallback
        const offlineData = localStorage.getItem('tayseer_offline_farm_data');
        if (offlineData) {
            const data = JSON.parse(offlineData);
            if (data.farm) {
                farmData = data.farm;
                updateKPIs();
                renderNDVIChart(data.chart || []);
                renderAlerts(data.pivot_alert, data.agro_alerts || []);
                return;
            }
        }

        showPipelineWaiting(false);
    }
}

async function loadWeather() {
    try {
        const res = await fetch('/api/weather');
        const data = await res.json();

        localStorage.setItem('tayseer_offline_weather', JSON.stringify(data));

        if (data.success && data.forecast && data.forecast.length > 0) {
            forecastData = data.forecast;
            renderWeather();
            renderCurrentWeather();
            if (data.agro_alerts && data.agro_alerts.length > 0 && farmData && farmData.ndvi != null) {
                renderAlerts(null, data.agro_alerts);
            }
        } else { renderWeatherError(); }
    } catch (e) {
        console.error("Error fetching weather", e);
        const offlineData = localStorage.getItem('tayseer_offline_weather');
        if (offlineData) {
            const data = JSON.parse(offlineData);
            if (data.forecast) {
                forecastData = data.forecast;
                renderWeather();
                renderCurrentWeather();
                if (data.agro_alerts && farmData && farmData.ndvi != null) {
                    renderAlerts(null, data.agro_alerts);
                }
                return;
            }
        }
        renderWeatherError();
    }
}

async function runPipeline(btnEl) {
    const btn = btnEl || document.activeElement;
    const oldHtml = btn ? btn.innerHTML : '';
    if (btn) { btn.innerHTML = '⏳ جاري التحديث...'; btn.disabled = true; }
    const loader = document.getElementById('loading-overlay');
    if (loader) loader.classList.remove('hidden');
    try {
        const res = await fetch('/api/run-pipeline', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            await loadFarmData();
            await loadWeather();
        } else {
            if (loader) loader.classList.add('hidden');
            alert(window.t("فشل التحديث: ", "Update failed: ") + (data.message || window.t('خطأ غير معروف', 'Unknown error')));
        }
    } catch (err) {
        console.error('Pipeline error:', err);
        if (loader) loader.classList.add('hidden');
        alert(window.t("خطأ في الشبكة أثناء تشغيل التحليل.", "Network error while running analysis."));
    }
    if (btn) { btn.innerHTML = oldHtml; btn.disabled = false; }
}

function downloadCSV() { window.location.href = "/api/download-csv"; }

async function uploadCSV(input) {
    if (!input.files || input.files.length === 0) return;
    const formData = new FormData();
    formData.append('file', input.files[0]);
    const statusEl = document.getElementById('csv-status');
    if (statusEl) statusEl.innerHTML = `<span class="text-blue-400">${window.t('جاري الرفع والتحليل... ⏳', 'Uploading & parsing... ⏳')}</span>`;
    try {
        const res = await fetch('/api/upload-csv', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.success) {
            if (statusEl) statusEl.innerHTML = `<span class="text-green-400">${window.t('تم دمج البيانات بنجاح! ✅', 'Data merged successfully! ✅')}</span>`;
            setTimeout(() => { if (statusEl) statusEl.innerText = ''; }, 3000);
            loadFarmData();
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-400">${window.t('فشل:', 'Failed:')} ${data.message}</span>`;
        }
    } catch {
        if (statusEl) statusEl.innerHTML = `<span class="text-red-400">${window.t('خطأ في الاتصال!', 'Connection error!')}</span>`;
    }
    input.value = "";
}

// ── Pipeline Waiting State ────────────────────────────────────
function showPipelineWaiting(pipelineRunning) {
    const badge = document.getElementById('status-badge');
    if (badge) {
        badge.className = 'badge text-lg badge-healthy';
        badge.innerText = pipelineRunning ? window.t('📡 جاري التحليل...', '📡 Analyzing...') : window.t('📡 لا توجد بيانات بعد', '📡 No data yet');
    }
    const alertsEl = document.getElementById('alerts-container');
    if (alertsEl) {
        alertsEl.innerHTML = pipelineRunning ? `
            <div class="alert-box alert-info">
                <div class="text-2xl mt-1 animate-spin">⚙️</div>
                <div>
                    <h4 class="font-bold text-blue-400 mb-1">${window.t('جاري تحليل بيانات القمر الصناعي...', 'Analyzing Satellite Data...')}</h4>
                    <p class="text-sm text-gray-300">
                      ${window.t('الـ Pipeline شغّال — هيستغرق دقيقة أو دقيقتين في أول مرة.', 'Pipeline running — might take a minute initially.')}<br>
                      ${window.t('الصفحة بتتحدّث <b>أوتوماتيكياً كل 60 ثانية</b> وهتظهر البيانات لوحدها. ✅', 'Page autorefreshes <b>every 60 seconds</b>. ✅')}
                    </p>
                </div>
            </div>` : `
            <div class="alert-box alert-info">
                <div class="text-2xl mt-1">📡</div>
                <div>
                    <h4 class="font-bold text-blue-400 mb-1">${window.t('لا توجد بيانات بعد', 'No data yet')}</h4>
                    <p class="text-sm text-gray-300">${window.t('اضغط زر <b>🔄 تحديث البيانات</b> لتشغيل التحليل الآن.', 'Click <b>🔄 Refresh Data</b> to run analysis.')}</p>
                </div>
            </div>`;
    }
    ['kpi-ndvi', 'kpi-et', 'kpi-biomass', 'kpi-lst', 'kpi-tvdi', 'kpi-wue', 'kpi-ndwi', 'kpi-pump'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerText = '—';
    });
}

// ── KPI Update ────────────────────────────────────────────────
function updateKPIs() {
    const f = farmData;
    const fmt = (v, d = 2) => (v !== null && v !== undefined) ? Number(v).toFixed(d) : '—';

    const badge = document.getElementById('status-badge');
    if (badge) {
        badge.className = 'badge text-lg';
        if (f.status === 'healthy') { badge.classList.add('badge-healthy'); badge.innerText = window.t('✅ المحصول ممتاز', '✅ Crop Healthy'); }
        else if (f.status === 'water_stress') { badge.classList.add('badge-water_stress'); badge.innerText = window.t('💧 إجهاد مائي', '💧 Water Stress'); }
        else if (f.status === 'heat_stress') { badge.classList.add('badge-heat_stress'); badge.innerText = window.t('🔥 إجهاد حراري', '🔥 Heat Stress'); }
        else if (f.status === 'pivot_alert') { badge.classList.add('badge-pivot_alert'); badge.innerText = window.t('⚠️ تحذير بيفوت', '⚠️ Pivot Alert'); }
        else if (f.ndvi != null) {
            if (f.ndvi > 0.6) { badge.classList.add('badge-healthy'); badge.innerText = window.t('✅ المحصول بحالة ممتازة', '✅ Crop in Excellent Condition'); }
            else if (f.ndvi > 0.35) { badge.classList.add('badge-water_stress'); badge.innerText = window.t('⚠️ المحصول يحتاج متابعة', '⚠️ Crop Needs Attention'); }
            else { badge.classList.add('badge-heat_stress'); badge.innerText = window.t('🚨 المحصول في خطر', '🚨 Crop at Risk'); }
        } else {
            badge.classList.add('badge-healthy'); badge.innerText = window.t('📡 شغّل التحليل', '📡 Run Analysis');
        }
    }
    renderLandCondition(f);
    renderCropDetails(f);
    updateRiskMapBadge(f);  // خريطة المخاطر — بادج صحة المحصول
}

// ── Risk Map Crop Health Badge Update ──────────────────────────────
function updateRiskMapBadge(f) {
    const badgeContainer = document.getElementById('risk-map-health-badge');
    const cropBadge = document.getElementById('risk-map-crop-badge');
    const ndviEl = document.getElementById('risk-map-ndvi-val');
    const etEl = document.getElementById('risk-map-et-val');
    const biomassEl = document.getElementById('risk-map-biomass-val');

    if (!badgeContainer || !f) return;
    badgeContainer.style.display = 'flex';

    if (f.ndvi != null) {
        cropBadge.className = 'badge text-sm';
        if (f.ndvi > 0.6) { cropBadge.classList.add('badge-healthy'); cropBadge.innerText = window.t('✅ محصول سليم', '✅ Healthy Crop'); }
        else if (f.ndvi > 0.35) { cropBadge.classList.add('badge-water_stress'); cropBadge.innerText = window.t('⚠️ يحتاج متابعة', '⚠️ Needs Attention'); }
        else { cropBadge.classList.add('badge-heat_stress'); cropBadge.innerText = window.t('🚨 خطر على المحصول', '🚨 Crop at Risk'); }
    } else {
        cropBadge.className = 'badge badge-healthy';
        cropBadge.innerText = window.t('⏳ جاري التحليل', '⏳ Loading...');
    }
    if (ndviEl) ndviEl.textContent = f.ndvi != null ? `NDVI: ${f.ndvi.toFixed(2)}` : '';
    if (etEl) etEl.textContent = f.et != null ? `ET: ${f.et.toFixed(1)} mm/يوم` : '';
    if (biomassEl) biomassEl.textContent = f.biomass != null ? `كتلة: ${f.biomass.toFixed(2)} ${window.t('طن/فدان','t/fed')}` : '';
}

// ── Weather Rendering ─────────────────────────────────────────
function renderWeather() {
    const grid = document.getElementById('weather-grid');
    if (!grid) return;
    if (!forecastData || !forecastData.length) {
        grid.innerHTML = '<p class="text-sm text-gray-500 col-span-5 text-center">لا توجد بيانات طقس — تأكد من مفتاح API</p>';
        return;
    }
    const iconMap = {
        '01d': '☀️', '01n': '🌙', '02d': '⛅', '02n': '☁️', '03d': '☁️', '03n': '☁️',
        '04d': '☁️', '04n': '☁️', '09d': '🌧️', '09n': '🌧️', '10d': '🌦️', '10n': '🌧️',
        '11d': '⛈️', '11n': '⛈️', '13d': '🌨️', '13n': '🌨️', '50d': '🌫️', '50n': '🌫️',
    };
    grid.innerHTML = forecastData.slice(0, 5).map(day => {
        const icon = iconMap[day.icon] || '🌤️';
        const dayLocale = window.CURRENT_LANG === 'ar' ? 'ar-EG' : 'en-US';
        const dayName = new Date(day.date + 'T12:00:00').toLocaleDateString(dayLocale, { weekday: 'short' });
        return `
            <div class="weather-day">
                <p class="text-xs text-gray-500 mb-1">${dayName}</p>
                <p class="text-sm text-gray-400 mb-2">${day.date.substring(5)}</p>
                <div class="text-3xl mb-2">${icon}</div>
                <p class="text-lg font-bold text-white">${Math.round(day.temp_max)}° <span class="text-sm text-gray-500 font-normal">/ ${Math.round(day.temp_min)}°</span></p>
                <p class="text-xs text-blue-300 mt-1">💧 ${day.humidity}%</p>
                <p class="text-xs text-gray-400 mt-1">🌬️ ${day.wind_speed ? day.wind_speed.toFixed(1) : '—'} م/ث</p>
                <p class="text-xs text-green-300 mt-1">${day.description || ''}</p>
            </div>`;
    }).join('');
}

function renderCurrentWeather() {
    if (!forecastData || !forecastData.length) return;
    const today = forecastData[0];
    const cwTemp = document.getElementById('cw-temp');
    const cwHum = document.getElementById('cw-humidity');
    const cwWind = document.getElementById('cw-wind');
    const cwDesc = document.getElementById('cw-desc');
    if (cwTemp) cwTemp.innerText = `${Math.round(today.temp_max)}° / ${Math.round(today.temp_min)}°`;
    if (cwHum) cwHum.innerText = `${today.humidity}%`;
    if (cwWind) cwWind.innerText = `${today.wind_speed ? today.wind_speed.toFixed(1) : '—'} م/ث`;
    if (cwDesc) cwDesc.innerText = today.description || '—';
}

function renderWeatherError() {
    const grid = document.getElementById('weather-grid');
    if (grid) grid.innerHTML = `<p class="text-sm text-red-400 col-span-5 text-center">⚠️ ${window.t('تعذر تحميل بيانات الطقس — تأكد من مفتاح OpenWeatherMap في ملف .env', 'Unable to load weather — check OpenWeatherMap API key')}</p>`;
    ['cw-temp', 'cw-humidity', 'cw-wind'].forEach(id => { const el = document.getElementById(id); if (el) el.innerText = '—'; });
    const cwDesc = document.getElementById('cw-desc'); if (cwDesc) cwDesc.innerText = window.t('غير متاح', 'N/A');
}

// ── Per-Crop Chart System ─────────────────────────────────────

// State
let _chartHistory = [];       // raw daily records from API fallback
let _cropZonesData = null;    // Real per-zone fetched history
let _chartIntervalDays = 5;   // currently selected interval
let _chartMetric = 'ndvi';    // currently selected metric

// Crop icon mapping (Arabic name → emoji)
const _cropIcons = {
    'قمح': '🌾', 'ذرة': '🌽', 'طماطم': '🍅', 'عنب': '🍇', 'بطاطس': '🥔',
    'زيتون': '🫒', 'فراولة': '🍓', 'مانجو': '🥭', 'فول': '🌱', 'بصل': '🧅',
    'ثوم': '🧄', 'برتقال': '🍊', 'قصب': '🎋', 'قطن': '☁️', 'بنجر': '🌰',
    'عباد': '🌻', 'سمسم': '🫘', 'بطيخ': '🍉', 'خيار': '🥒', 'باذنجان': '🍆',
};

function _cropIcon(label) {
    for (const [key, val] of Object.entries(_cropIcons)) {
        if (label && label.includes(key)) return val;
    }
    return '🌱';
}

// ── Interval grouping ──────────────────────────────────────────
// Groups sorted daily history into N-day buckets and averages each metric.
// Returns: [ { label, dateRange, count, ndvi, et, biomass, wue, ndwi, tvdi, lst, fapar }, ... ]
function groupHistoryByInterval(history, intervalDays) {
    if (!history || history.length === 0) return [];

    // Sort ascending by date
    const sorted = [...history]
        .filter(r => r.date)
        .sort((a, b) => new Date(a.date) - new Date(b.date));

    if (sorted.length === 0) return [];

    const buckets = [];
    let i = 0;
    while (i < sorted.length) {
        const bucketStart = new Date(sorted[i].date);
        const bucketEnd = new Date(bucketStart);
        bucketEnd.setDate(bucketEnd.getDate() + intervalDays - 1);

        // Collect all records within [bucketStart, bucketEnd]
        const rows = [];
        while (i < sorted.length) {
            const d = new Date(sorted[i].date);
            if (d <= bucketEnd) {
                rows.push(sorted[i]);
                i++;
            } else {
                break;
            }
        }

        if (rows.length === 0) continue;

        // Average each metric (skip nulls)
        const avg = (metric) => {
            const vals = rows.map(r => r[metric]).filter(v => v !== null && v !== undefined && !isNaN(Number(v)));
            if (vals.length === 0) return null;
            return vals.reduce((s, v) => s + Number(v), 0) / vals.length;
        };

        // Label: "DD/MM – DD/MM"
        const fmt = (d) => `${d.getDate()}/${d.getMonth() + 1}`;
        const lastDate = new Date(rows[rows.length - 1].date);
        const label = `${fmt(new Date(rows[0].date))}–${fmt(lastDate)}`;

        buckets.push({
            label,
            dateRange: `${rows[0].date} → ${rows[rows.length - 1].date}`,
            count: rows.length,
            ndvi: avg('ndvi'),
            et: avg('et'),
            biomass: avg('biomass'),
            wue: avg('wue'),
            ndwi: avg('ndwi'),
            tvdi: avg('tvdi'),
            lst: avg('lst'),
            fapar: avg('fapar'),
        });
    }
    return buckets;
}

// ── Per-zone metric derivation ─────────────────────────────────
// Given a bucket (averaged globals) and a zone's area, compute accurate
// per-zone: biomass (tons), ET (mm/day for that zone), WUE (kg/m³)
//
// Formulas (physics-based, consistent with ml_gee_engine.py):
//   fapar     = 1.2 × ndvi − 0.11, clamped [0,1]
//   biomass   = fapar × PAR_daily × LUE × season_days × area_m2     [tons]
//               (PAR=9.5 MJ/m²/day, LUE=1.5e-6 t/MJ, season=120 d)
//   ET_vol    = et_mm_per_day / 1000 × intervalDays × area_m2       [m³]
//   WUE       = (biomass × 1000 kg) / ET_vol (m³)                   [kg/m³]
function computeZoneMetrics(bucket, zoneAreaM2) {
    const PAR = 9.5;          // MJ/m²/day  (Egypt avg)
    const LUE = 1.5e-6;       // tons/MJ
    const SEASON = 120;          // growing days

    const ndvi = bucket.ndvi != null ? bucket.ndvi : 0;
    const et = bucket.et != null ? bucket.et : 0;
    const ndwi = bucket.ndwi;
    const tvdi = bucket.tvdi;
    const lst = bucket.lst;

    let lst_soil = null;
    let lst_canopy = null;
    if (lst != null) {
        let n = Math.max(0, Math.min(1, ndvi));
        let fvc = Math.max(0, Math.min(1, Math.pow((n - 0.15) / 0.70, 2)));
        lst_soil = lst + (1.0 - fvc) * 4.0;
        lst_canopy = lst - fvc * 7.0;
    }

    const fapar = bucket.fapar != null
        ? bucket.fapar
        : Math.max(0, Math.min(1, 1.2 * ndvi - 0.11));

    // Biomass for this zone (tons)
    const biomass = fapar * PAR * LUE * SEASON * zoneAreaM2;

    // ET volume for the interval (m³) — exact: mm/day × days × m²/1000
    const etVol = (et / 1000.0) * _chartIntervalDays * zoneAreaM2;

    // WUE: kg biomass / m³ water
    const wue = (etVol > 0) ? ((biomass * 1000.0) / etVol) : null;

    return {
        ndvi: ndvi,
        et: et,
        biomass: biomass,
        wue: wue,
        ndwi: ndwi,
        tvdi: tvdi,
        lst: lst,
        lst_soil: lst_soil,
        lst_canopy: lst_canopy,
    };
}

// ── Metric display config ──────────────────────────────────────
const _metricConfig = {
    ndvi: {
        label_ar: 'مؤشر الغطاء النباتي (NDVI)',
        label_en: 'Vegetation Index (NDVI)',
        unit: '', decimals: 2, max: 1.0,
        color: (v) => v >= 0.6 ? '#22c55e' : v >= 0.35 ? '#eab308' : '#ef4444',
    },
    et: {
        label_ar: 'التبخر الفعلي (ET mm/يوم)',
        label_en: 'Evapotranspiration ET (mm/day)',
        unit: ' mm', decimals: 1, max: 12.0,
        color: (v) => v >= 6 ? '#3b82f6' : v >= 3 ? '#eab308' : '#ef4444',
    },
    biomass: {
        label_ar: 'الكتلة الحيوية (طن/فدان)',
        label_en: 'Biomass (t/Feddan)',
        unit: ' طن/فد', decimals: 3, max: null,
        color: () => '#16a34a',
    },
    wue: {
        label_ar: 'كفاءة المياه (كجم/م³)',
        label_en: 'Water Use Efficiency (kg/m³)',
        unit: ' kg/m³', decimals: 2, max: 5.0,
        color: (v) => v >= 1.5 ? '#22c55e' : v >= 0.8 ? '#eab308' : '#ef4444',
    },
    lst_soil: {
        label_ar: 'حرارة التربة (LST Soil)',
        label_en: 'Soil Temp (LST Soil)',
        unit: '°C', decimals: 1, max: 50.0,
        color: (v) => v >= 40 ? '#ef4444' : v >= 30 ? '#eab308' : '#3b82f6',
    },
    lst_canopy: {
        label_ar: 'حرارة النبات (LST Canopy)',
        label_en: 'Canopy Temp (LST Canopy)',
        unit: '°C', decimals: 1, max: 45.0,
        color: (v) => v >= 35 ? '#ef4444' : v >= 25 ? '#eab308' : '#22c55e',
    },
};

// ── Main render entry-point ────────────────────────────────────
function renderPerCropCharts(history) {
    _chartHistory = history || [];
    loadCropCharts(); // Fetch precise per-zone data from GEE
}

async function loadCropCharts() {
    const container = document.getElementById('per-crop-charts-container');
    if (!container) return;

    // Show spinner if data isn't loaded yet
    if (!_cropZonesData) {
        container.innerHTML = `<div class="p-8 text-center text-gray-400">
            <div class="text-3xl mb-4 animate-spin">⏳</div>
            <p>${window.t('جاري جلب بيانات القمر الصناعي الدقيقة لكل منطقة (قد يستغرق لحظات)...', 'Fetching accurate satellite data per zone (may take a moment)...')}</p>
        </div>`;
    }

    try {
        const res = await fetch(`/api/crop-charts?days=30`);
        const data = await res.json();
        if (data.success) {
            _cropZonesData = data.zones || [];
            if (data.global_history) {
                _chartHistory = data.global_history;
            }
            _redrawCharts();
        } else {
            container.innerHTML = `<p class="text-red-400 text-sm py-6 text-center">${window.t('فشل تحميل بيانات المحاصيل التاريخية', 'Failed to load historical crop data')}</p>`;
        }
    } catch (e) {
        console.error("Error loading crop charts", e);
        container.innerHTML = `<p class="text-red-400 text-sm py-6 text-center">${window.t('خطأ في الاتصال بالسيرفر', 'Server connection error')}</p>`;
    }
}

function _redrawCharts() {
    const container = document.getElementById('per-crop-charts-container');
    if (!container) return;

    // 1. If we have precise per-zone data directly from GEE
    if (_cropZonesData && _cropZonesData.length > 0) {
        let html = '';
        let totalAreaM2 = 0;

        _cropZonesData.forEach(zone => {
            totalAreaM2 += zone.area_m2;
            const buckets = groupHistoryByInterval(zone.history, _chartIntervalDays);
            if (buckets.length > 0) {
                html += _buildCropPanelFromGEE(zone, buckets);
            }
        });

        if (html) {
            container.innerHTML = html;
            const noteEl = document.getElementById('chart-data-source-note');
            if (noteEl) noteEl.textContent = window.t(
                `📍 ${_cropZonesData.length} منطقة محللة بدقة من القمر الصناعي — فترات ${_chartIntervalDays} أيام`,
                `📍 ${_cropZonesData.length} accurate zones from satellite — ${_chartIntervalDays}-day periods`
            );
            return;
        }
    }

    // 2. Fallback to older mechanism if no cropZonesData or no zones drawn
    if (!_chartHistory || _chartHistory.length === 0) {
        container.innerHTML = `<p class="text-gray-500 text-sm py-6 text-center">${window.t('لا توجد سجلات كافية بعد.', 'Not enough records yet.')}</p>`;
        return;
    }

    const buckets = groupHistoryByInterval(_chartHistory, _chartIntervalDays);
    if (buckets.length === 0) {
        container.innerHTML = `<p class="text-gray-500 text-sm py-6 text-center">${window.t('⏳ جاري معالجة البيانات…', '⏳ Processing data…')}</p>`;
        return;
    }

    const zones = _parseZones();
    if (zones.length === 0) {
        container.innerHTML = _buildSingleGlobalChart(buckets);
        const noteEl = document.getElementById('chart-data-source-note');
        if (noteEl) noteEl.textContent = window.t('📊 بيانات المزرعة الكلية (لم يتم رسم مناطق)', '📊 Global farm data (no zones drawn)');
        return;
    }

    const totalAreaM2 = zones.reduce((s, z) => s + z.areaM2, 0);
    let html = zones.map(zone => _buildCropPanel(zone, buckets, totalAreaM2)).join('');
    container.innerHTML = html;

    const noteEl = document.getElementById('chart-data-source-note');
    if (noteEl) noteEl.textContent = window.t(
        `⚠️ تم استخدام المتوسط العام للمزرعة (ارسم المناطق بدقة لنتائج أفضل)`,
        `⚠️ Farm average used (draw accurate zones for better results)`
    );
}

function _buildCropPanelFromGEE(zone, buckets) {
    const cfg = _metricConfig[_chartMetric] || _metricConfig.ndvi;
    const icon = _cropIcon(zone.zone_label);
    const areaText = formatAreaArabic(zone.area_m2);

    const vals = buckets.map(b => {
        const zm = computeZoneMetrics(b, zone.area_m2);
        return { label: b.label, value: zm[_chartMetric], zm };
    });

    const rawMax = Math.max(...vals.map(v => (v.value != null ? v.value : 0)));
    const scaleMax = cfg.max || (rawMax > 0 ? rawMax * 1.1 : 1);

    const barsHtml = vals.map(v => {
        const value = v.value;
        const pct = value != null && scaleMax > 0 ? Math.max(4, Math.min(100, (value / scaleMax) * 100)) : 4;
        const bgColor = value != null ? cfg.color(value) : '#4b5563';
        const valLabel = value != null ? value.toFixed(cfg.decimals) + cfg.unit : '—';
        return `
<div class="crop-bar-group">
  <div class="crop-bar-value">${valLabel}</div>
  <div class="crop-bar-fill" style="height:${pct}%;background:${bgColor};opacity:0.88;" data-val="${valLabel}" title="${v.label}: ${valLabel}"></div>
  <div class="crop-bar-label">${v.label}</div>
</div>`;
    }).join('');

    const latest = vals.length > 0 ? vals[vals.length - 1].zm : null;
    const fmtNum = (v, d) => (v != null && !isNaN(v)) ? Number(v).toFixed(d) : '—';

    const statPills = [
        { label: 'NDVI', color: '#22c55e', val: fmtNum(latest?.ndvi, 2) },
        { label: `ET (${window.t('مم', 'mm')})`, color: '#3b82f6', val: fmtNum(latest?.et, 1) },
        { label: window.t('كتلة', 'Biomass'), color: '#fbbf24', val: fmtNum(latest?.biomass, 2) },
        { label: `TVDI`, color: '#f97316', val: fmtNum(latest?.tvdi, 2) },
    ].map(p => `
<div class="crop-stat-pill" style="border-top-color:${p.color}; padding: 4px 6px;">
  <span class="crop-stat-pill-label" style="font-size: 10px;">${p.label}</span>
  <span class="crop-stat-pill-val" style="font-size: 13px;">${p.val}</span>
</div>`).join('');

    const metricLabel = window.CURRENT_LANG === 'ar' ? cfg.label_ar : cfg.label_en;

    return `
<div class="crop-chart-panel" style="padding: 12px;">
  <div class="crop-chart-header" style="margin-bottom: 8px;">
    <span style="font-size:22px;">${icon}</span>
    <div style="flex:1;">
      <div style="font-size:14px;font-weight:700;color:#fff;">${zone.zone_label}</div>
      <div style="font-size:10px;color:#9ca3af;">${areaText} &nbsp;·&nbsp; ${metricLabel}</div>
    </div>
  </div>
  <div class="crop-bar-chart" style="min-height:120px; gap: 4px;">
    ${barsHtml || `<p style="color:#6b7280;font-size:12px;margin:auto;">${window.t('لا توجد بيانات', 'No data')}</p>`}
  </div>
  <div class="crop-stat-row" style="margin-top: 8px; gap: 4px;">
    ${statPills}
  </div>
</div>`;
}

// ── Zone parser ────────────────────────────────────────────────
function _parseZones() {
    if (!window.FARM_GEOJSON || window.FARM_GEOJSON === 'None' || window.FARM_GEOJSON.trim() === '') return [];
    try {
        const geoData = JSON.parse(window.FARM_GEOJSON);
        const features = geoData.features || (geoData.type === 'Feature' ? [geoData] : []);
        const zones = [];
        features.forEach(f => {
            const props = f.properties || {};
            const geom = f.geometry;
            if (!geom) return;
            let m2 = 0;
            if (props.sectorAngle && props.sectorRadius) {
                m2 = (props.sectorAngle / 360) * Math.PI * props.sectorRadius ** 2;
            } else if (geom.type === 'Point' && props.radius) {
                m2 = Math.PI * props.radius ** 2;
            } else if (geom.type === 'Polygon') {
                m2 = calculatePolygonAreaM2(geom.coordinates[0]);
            } else if (geom.type === 'MultiPolygon') {
                geom.coordinates.forEach(poly => { m2 += calculatePolygonAreaM2(poly[0]); });
            }
            if (m2 > 0) {
                zones.push({
                    label: props.zoneLabel || window.t('منطقة مزرعة', 'Farm Zone'),
                    color: props.color || '#52b788',
                    areaM2: m2,
                });
            }
        });
        return zones;
    } catch { return []; }
}

// ── Builds one per-crop panel HTML ─────────────────────────────
function _buildCropPanel(zone, buckets, totalAreaM2) {
    const cfg = _metricConfig[_chartMetric] || _metricConfig.ndvi;
    const icon = _cropIcon(zone.label);
    const areaText = formatAreaArabic(zone.areaM2);

    // Per-zone values for each bucket
    const vals = buckets.map(b => {
        const zm = computeZoneMetrics(b, zone.areaM2);
        return { label: b.label, value: zm[_chartMetric], zm };
    });

    // Determine max for scaling bars
    const rawMax = Math.max(...vals.map(v => (v.value != null ? v.value : 0)));
    const scaleMax = cfg.max || (rawMax > 0 ? rawMax * 1.1 : 1);

    // Build bars HTML
    const barsHtml = vals.map(v => {
        const value = v.value;
        const pct = value != null && scaleMax > 0
            ? Math.max(4, Math.min(100, (value / scaleMax) * 100))
            : 4;
        const bgColor = value != null ? cfg.color(value) : '#4b5563';
        const valLabel = value != null ? value.toFixed(cfg.decimals) + cfg.unit : '—';
        return `
<div class="crop-bar-group">
  <div class="crop-bar-value">${valLabel}</div>
  <div class="crop-bar-fill" style="height:${pct}%;background:${bgColor};opacity:0.88;" data-val="${valLabel}" title="${v.label}: ${valLabel}"></div>
  <div class="crop-bar-label">${v.label}</div>
</div>`;
    }).join('');

    // Latest bucket stat pills
    const latest = vals.length > 0 ? vals[vals.length - 1].zm : null;
    const fmtNum = (v, d) => (v != null && !isNaN(v)) ? Number(v).toFixed(d) : '—';

    const statPills = [
        { label: 'NDVI', color: '#22c55e', val: fmtNum(latest?.ndvi, 2) },
        { label: `ET (${window.t('مم', 'mm')})`, color: '#3b82f6', val: fmtNum(latest?.et, 1) },
        { label: window.t('كتلة', 'Biomass'), color: '#fbbf24', val: fmtNum(latest?.biomass, 2) },
        { label: `TVDI`, color: '#f97316', val: fmtNum(latest?.tvdi, 2) },
    ].map(p => `
<div class="crop-stat-pill" style="border-top-color:${p.color}; padding: 4px 6px;">
  <span class="crop-stat-pill-label" style="font-size: 10px;">${p.label}</span>
  <span class="crop-stat-pill-val" style="font-size: 13px;">${p.val}</span>
</div>`).join('');

    const metricLabel = window.CURRENT_LANG === 'ar' ? cfg.label_ar : cfg.label_en;

    return `
<div class="crop-chart-panel" style="padding: 12px;">
  <div class="crop-chart-header" style="margin-bottom: 8px;">
    <span style="font-size:22px;">${icon}</span>
    <div style="flex:1;">
      <div style="font-size:14px;font-weight:700;color:#fff;">${zone.label}</div>
      <div style="font-size:10px;color:#9ca3af;">${areaText} &nbsp;·&nbsp; ${metricLabel}</div>
    </div>
  </div>

  <!-- Bar chart -->
  <div class="crop-bar-chart" style="min-height:120px; gap: 4px;">
    ${barsHtml || `<p style="color:#6b7280;font-size:12px;margin:auto;">${window.t('لا توجد بيانات', 'No data')}</p>`}
  </div>

  <!-- Latest bucket stats -->
  <div class="crop-stat-row" style="margin-top: 8px; gap: 4px;">
    ${statPills}
  </div>
</div>`;
}

// ── Global fallback chart (no zones drawn) ─────────────────────
function _buildSingleGlobalChart(buckets) {
    const cfg = _metricConfig[_chartMetric] || _metricConfig.ndvi;
    const vals = buckets.map(b => ({ label: b.label, value: b[_chartMetric] }));
    const rawMax = Math.max(...vals.map(v => (v.value != null ? v.value : 0)));
    const scaleMax = cfg.max || (rawMax > 0 ? rawMax * 1.1 : 1);
    const metricLabel = window.CURRENT_LANG === 'ar' ? cfg.label_ar : cfg.label_en;

    const barsHtml = vals.map(v => {
        const value = v.value;
        const pct = value != null && scaleMax > 0 ? Math.max(4, Math.min(100, (value / scaleMax) * 100)) : 4;
        const bgColor = value != null ? cfg.color(value) : '#4b5563';
        const valLabel = value != null ? value.toFixed(cfg.decimals) + cfg.unit : '—';
        return `
<div class="crop-bar-group">
  <div class="crop-bar-value">${valLabel}</div>
  <div class="crop-bar-fill" style="height:${pct}%;background:${bgColor};opacity:0.88;" data-val="${valLabel}"></div>
  <div class="crop-bar-label">${v.label}</div>
</div>`;
    }).join('');

    return `
<div class="crop-chart-panel" style="padding: 12px;">
  <div class="crop-chart-header" style="margin-bottom: 8px;">
    <span style="font-size:22px;">🌾</span>
    <div>
      <div style="font-size:14px;font-weight:700;color:#fff;">${window.t('بيانات المزرعة الكلية', 'Total Farm Data')}</div>
      <div style="font-size:10px;color:#9ca3af;">${metricLabel}</div>
    </div>
  </div>
  <div class="crop-bar-chart" style="min-height:120px; gap: 4px;">${barsHtml}</div>
</div>`;
}

// ── Control callbacks ──────────────────────────────────────────
function setChartInterval(days) {
    _chartIntervalDays = days;
    // Update button styles
    document.querySelectorAll('[id^="btn-interval-"]').forEach(btn => {
        btn.classList.remove('chart-ctrl-active');
    });
    const activeBtn = document.getElementById(`btn-interval-${days}`);
    if (activeBtn) activeBtn.classList.add('chart-ctrl-active');
    _redrawCharts();
}

function setChartMetric(metric) {
    _chartMetric = metric;
    // Update button styles
    document.querySelectorAll('[id^="btn-metric-"]').forEach(btn => {
        btn.classList.remove('chart-ctrl-active');
    });
    const activeBtn = document.getElementById(`btn-metric-${metric}`);
    if (activeBtn) activeBtn.classList.add('chart-ctrl-active');
    _redrawCharts();
}

// Legacy compat: keep renderNDVIChart pointing to new system
function renderNDVIChart(history) {
    renderPerCropCharts(history);
}



// ── Alerts ───────────────────────────────────────────────────
function renderAlerts(pivotResult, agroAlerts) {
    const container = document.getElementById('alerts-container');
    if (!container) return;
    let html = '';

    if (pivotResult && pivotResult.alert) {
        const isCritical = pivotResult.severity === 'critical';
        html += `
        <div class="alert-box ${isCritical ? 'alert-critical' : 'alert-warning'}">
            <div class="text-2xl mt-1">${isCritical ? '🚨' : '⚠️'}</div>
            <div>
                <h4 class="font-bold ${isCritical ? 'text-red-400' : 'text-orange-400'} mb-1">${window.t('تحذير البيفوت', 'Pivot Alert')}</h4>
                <p class="text-sm text-gray-300">${window.CURRENT_LANG === 'ar' ? pivotResult.message : pivotResult.message_en || pivotResult.message}</p>
                <p class="text-xs text-gray-500 mt-1">NDVI CV: ${pivotResult.ndvi_cv} | Thermal Inertia: ${pivotResult.thermal_inertia}</p>
            </div>
        </div>`;
    }

    const f = farmData || {};
    if (f.status === 'water_stress') {
        html += `
        <div class="alert-box alert-warning">
            <div class="text-2xl mt-1">💧</div>
            <div>
                <h4 class="font-bold text-orange-400 mb-1">${window.t('إجهاد مائي مكتشف', 'Water Stress Detected')}</h4>
                <p class="text-sm text-gray-300">NDVI = ${f.ndvi ? Number(f.ndvi).toFixed(2) : '—'} | ET = ${f.et ? Number(f.et).toFixed(1) : '—'} ${window.t('مم/يوم', 'mm/day')}<br>${window.t('التربة تعاني من نقص الرطوبة. يُنصح بتشغيل المضخات.', 'Soil lacks moisture. It is advised to turn on the pumps.')}</p>
            </div>
        </div>`;
    }
    if (f.status === 'heat_stress') {
        html += `
        <div class="alert-box alert-critical">
            <div class="text-2xl mt-1">🔥</div>
            <div>
                <h4 class="font-bold text-red-400 mb-1">${window.t('إجهاد حراري مكتشف', 'Heat Stress Detected')}</h4>
                <p class="text-sm text-gray-300">LST = ${f.lst ? Number(f.lst).toFixed(1) : '—'}°C | NDVI = ${f.ndvi ? Number(f.ndvi).toFixed(2) : '—'}<br>${window.t('حرارة السطح مرتفعة جداً. يُنصح بالري الليلي.', 'Surface temperature is very high. Night irrigation advised.')}</p>
            </div>
        </div>`;
    }

    if (agroAlerts && agroAlerts.length > 0) {
        agroAlerts.forEach(alert => {
            const cssClass = alert.level === 'critical' ? 'alert-critical' : alert.level === 'warning' ? 'alert-warning' : 'alert-info';
            const icon = alert.level === 'critical' ? '🚨' : alert.level === 'warning' ? '⚠️' : '✅';
            const alertText = window.CURRENT_LANG === 'en' && alert.message_en ? alert.message_en : alert.message_ar;
            html += `
            <div class="alert-box ${cssClass}">
                <div class="text-2xl mt-1">${icon}</div>
                <div><p class="text-sm text-gray-200">${alertText}</p></div>
            </div>`;
        });
    }

    // تحذيرات مبنية على بيانات جهاز الري وكفاءة المياه
    const effectiveFlowForAlert = (window.WELL_FLOW_RATE > 0) ? window.WELL_FLOW_RATE : window.PUMP_FLOW_RATE;
    if (effectiveFlowForAlert > 0 && f.et != null && window.AREA_FEDDAN > 0) {
        const totalAreaM2 = window.AREA_FEDDAN * 4200;
        const totalDailyM3 = (f.et / 1000.0) * totalAreaM2;
        const totalIrrigHours = totalDailyM3 / effectiveFlowForAlert;

        // إذا كان نوع الري بيفوت محوري وساعات التشغيل مرتفعة
        if (window.IRRIGATION_TYPE === 'pivot' && totalIrrigHours > 20) {
            html += `
            <div class="alert-box alert-warning">
                <div class="text-2xl mt-1">⏰</div>
                <div>
                    <h4 class="font-bold text-orange-400 mb-1">${window.t('ساعات تشغيل البيفوت مرتفعة', 'Pivot Runtime Too High')}</h4>
                    <p class="text-sm text-gray-300">${window.t(`ساعات تشغيل البيفوت المطلوبة اليوم ${totalIrrigHours.toFixed(1)} ساعة. راجع تصرف البئر أو قلل سرعة الدوران.`, `Pivot requires ${totalIrrigHours.toFixed(1)}h today. Check well flow rate or slow the rotation.`)}</p>
                </div>
            </div>`;
        }

        // إذا كانت WUE منخفضة والتصرف موجود
        if (f.wue != null && f.wue < 0.8) {
            html += `
            <div class="alert-box alert-warning">
                <div class="text-2xl mt-1">💧</div>
                <div>
                    <h4 class="font-bold text-orange-400 mb-1">${window.t('كفاءة مياه منخفضة', 'Low Water Efficiency')}</h4>
                    <p class="text-sm text-gray-300">${window.t(`كفاءة المياه الحالية ${f.wue.toFixed(2)} كجم/م³. راجع تصرف جهاز الري وجدولة الري.`, `Water efficiency is ${f.wue.toFixed(2)} kg/m³. Review irrigation scheduling.`)}</p>
                </div>
            </div>`;
        }

        // إذا NDVI منخفض وساعات التشغيل منخفضة أيضاً
        if (f.ndvi != null && f.ndvi < 0.35 && totalIrrigHours < 2) {
            html += `
            <div class="alert-box alert-warning">
                <div class="text-2xl mt-1">🌱</div>
                <div>
                    <h4 class="font-bold text-orange-400 mb-1">${window.t('كمية مياه غير كافية', 'Insufficient Irrigation')}</h4>
                    <p class="text-sm text-gray-300">${window.t('مؤشر الغطاء النباتي ضعيف وساعات الري منخفضة جداً. قد تكون كمية المياه غير كافية.', 'NDVI is low and irrigation hours are very short. Water supply may be insufficient.')}</p>
                </div>
            </div>`;
        }
    }

    if (!html) {
        if (f.ndvi != null) {
            html = `
            <div class="alert-box alert-info">
                <div class="text-2xl mt-1">✅</div>
                <div>
                    <h4 class="font-bold text-green-400 mb-1">${window.t('الوضع سليم ومستقر', 'Status Healthy and Stable')}</h4>
                    <p class="text-sm text-gray-300">NDVI = ${Number(f.ndvi).toFixed(2)} | ET = ${f.et ? Number(f.et).toFixed(1) : '—'} ${window.t('مم/يوم', 'mm/day')} | LST = ${f.lst ? Number(f.lst).toFixed(1) : '—'}°C<br>${window.t('لا توجد تحذيرات. المحصول في حالة جيدة.', 'No warnings found. Field is in good condition.')}</p>
                </div>
            </div>`;
        } else {
            html = `
            <div class="alert-box alert-info">
                <div class="text-2xl mt-1">📡</div>
                <div>
                    <h4 class="font-bold text-blue-400 mb-1">${window.t('جاري تحميل التحليل', 'Loading Analysis')}</h4>
                    <p class="text-sm text-gray-300">${window.t('يتم الآن تحليل بيانات الأرض — ستظهر النتائج خلال لحظات.', 'Analyzing satellite metrics — Results will appear shortly.')}</p>
                </div>
            </div>`;
        }
    }
    container.innerHTML = html;
}

// ── Land Condition (6 cards) ──────────────────────────────────
function renderLandCondition(f) {
    const hasData = f && (f.ndvi !== null && f.ndvi !== undefined);

    // Pull distinct crops from drawn map zones
    let cropType = window.CROP_TYPE || 'المحصول';
    if (window.FARM_GEOJSON && window.FARM_GEOJSON !== 'None' && window.FARM_GEOJSON.trim() !== '') {
        try {
            const geoData = JSON.parse(window.FARM_GEOJSON);
            const features = geoData.features || (geoData.type === 'Feature' ? [geoData] : []);
            let crops = [];
            features.forEach(feat => {
                if (feat.properties && feat.properties.zoneLabel) {
                    crops.push(feat.properties.zoneLabel);
                }
            });
            if (crops.length > 0) {
                cropType = [...new Set(crops)].join('، ');
            }
        } catch (e) { }
    }

    // Update the top header crop type
    const topCropEl = document.getElementById('crop-type');
    if (topCropEl) topCropEl.innerText = cropType;

    // 1. نوع المحصول + حالة NDVI ───────────────────────────────
    const vegEl = document.getElementById('lc-vegetation');
    const vegStatus = document.getElementById('lc-veg-status');
    const vegDetail = document.getElementById('lc-veg-detail');
    if (vegEl && vegStatus && vegDetail) {
        vegStatus.innerText = cropType;  // اسم المحصول فقط
        if (hasData) {
            if (f.ndvi > 0.6) { vegDetail.innerText = `${window.t('✅ ممتاز', '✅ Excellent')} | NDVI=${f.ndvi.toFixed(2)}`; vegEl.style.borderColor = '#22c55e'; }
            else if (f.ndvi > 0.35) { vegDetail.innerText = `${window.t('🟡 متوسط', '🟡 Average')} | NDVI=${f.ndvi.toFixed(2)}`; vegEl.style.borderColor = '#eab308'; }
            else { vegDetail.innerText = `${window.t('🔴 ضعيف', '🔴 Poor')} | NDVI=${f.ndvi.toFixed(2)}`; vegEl.style.borderColor = '#ef4444'; }
        } else {
            vegDetail.innerText = window.t('شغّل تحليل القمر الصناعي', 'Run Satellite Analysis'); vegEl.style.borderColor = '#6b7280';
        }
    }

    // 2. رطوبة التربة ──────────────────────────────────────────
    const moistEl = document.getElementById('lc-moisture');
    const moistStatus = document.getElementById('lc-moist-status');
    const moistDetail = document.getElementById('lc-moist-detail');
    if (moistEl && moistStatus && moistDetail) {
        if (hasData && f.ndwi != null) {
            if (f.ndwi > 0.3) { moistStatus.innerText = window.t('🟢 رطوبة عالية', '🟢 High Moisture'); moistDetail.innerText = `NDWI=${f.ndwi.toFixed(2)} — ${window.t('مشبعة', 'Saturated')}`; moistEl.style.borderColor = '#3b82f6'; }
            else if (f.ndwi > 0.0) { moistStatus.innerText = window.t('🟡 رطوبة متوسطة', '🟡 Mod Moisture'); moistDetail.innerText = `NDWI=${f.ndwi.toFixed(2)} — ${window.t('راقب الري', 'Monitor Irrigation')}`; moistEl.style.borderColor = '#eab308'; }
            else { moistStatus.innerText = window.t('🔴 جفاف', '🔴 Dry'); moistDetail.innerText = `NDWI=${f.ndwi.toFixed(2)} — ${window.t('ري عاجل', 'Urgent Irrigation')}`; moistEl.style.borderColor = '#ef4444'; }
        } else if (hasData && f.tvdi != null) {
            if (f.tvdi < 0.3) { moistStatus.innerText = window.t('🟢 رطبة', '🟢 Moist'); moistDetail.innerText = `TVDI=${f.tvdi.toFixed(2)}`; moistEl.style.borderColor = '#3b82f6'; }
            else if (f.tvdi < 0.6) { moistStatus.innerText = window.t('🟡 متوسطة', '🟡 Medium'); moistDetail.innerText = `TVDI=${f.tvdi.toFixed(2)}`; moistEl.style.borderColor = '#eab308'; }
            else { moistStatus.innerText = window.t('🔴 جافة', '🔴 Dry'); moistDetail.innerText = `TVDI=${f.tvdi.toFixed(2)}`; moistEl.style.borderColor = '#ef4444'; }
        } else {
            moistStatus.innerText = '—'; moistDetail.innerText = window.t('شغّل التحليل', 'Run Analysis'); moistEl.style.borderColor = '#6b7280';
        }
    }

    // 3. حرارة التربة — FVC-based (consistent with ml_gee_engine.py) ─────
    const tempEl = document.getElementById('lc-temp');
    const tempStatus = document.getElementById('lc-temp-status');
    const tempDetail = document.getElementById('lc-temp-detail');
    if (tempEl && tempStatus && tempDetail) {
        if (hasData && f.lst != null) {
            let soilTemp;
            if (f.lst_soil != null) {
                soilTemp = f.lst_soil;  // use backend-computed FVC-based value
            } else {
                // Fallback: FVC-based formula matching compute_lst_split
                const ndvi0 = Math.max(0, Math.min(1, f.ndvi || 0));
                const fvc = Math.max(0, Math.min(1, Math.pow((ndvi0 - 0.15) / 0.70, 2)));
                soilTemp = f.lst + (1.0 - fvc) * 5.0;
            }
            if (soilTemp > 45) { tempStatus.innerText = `🔴 ${soilTemp.toFixed(1)}°C`; tempDetail.innerText = window.t('تربة حارة جداً — خطر', 'Very hot soil — Danger'); tempEl.style.borderColor = '#ef4444'; }
            else if (soilTemp > 38) { tempStatus.innerText = `🟠 ${soilTemp.toFixed(1)}°C`; tempDetail.innerText = window.t('حرارة مرتفعة — راقب الري', 'High temp — Monitor irrigation'); tempEl.style.borderColor = '#f97316'; }
            else if (soilTemp > 22) { tempStatus.innerText = `🟢 ${soilTemp.toFixed(1)}°C`; tempDetail.innerText = window.t('حرارة تربة مناسبة للنمو', 'Good temp for growth'); tempEl.style.borderColor = '#22c55e'; }
            else { tempStatus.innerText = `🔵 ${soilTemp.toFixed(1)}°C`; tempDetail.innerText = window.t('تربة باردة — قد يتباطأ النمو', 'Cold soil — Slower growth'); tempEl.style.borderColor = '#3b82f6'; }
        } else { tempStatus.innerText = '—'; tempDetail.innerText = window.t('شغّل التحليل', 'Run Analysis'); tempEl.style.borderColor = '#6b7280'; }
    }

    // 4. حرارة النبات — FVC-based canopy cooling ────────────
    const vegTempEl = document.getElementById('lc-veg-temp');
    const vegTempStatus = document.getElementById('lc-veg-temp-status');
    const vegTempDetail = document.getElementById('lc-veg-temp-detail');
    if (vegTempEl && vegTempStatus && vegTempDetail) {
        if (hasData && f.lst != null) {
            let vegTemp;
            if (f.lst_canopy != null) {
                vegTemp = f.lst_canopy;  // backend FVC-based value
            } else {
                // Fallback: FVC-based formula matching compute_lst_split
                const ndvi0 = Math.max(0, Math.min(1, f.ndvi || 0));
                const fvc = Math.max(0, Math.min(1, Math.pow((ndvi0 - 0.15) / 0.70, 2)));
                vegTemp = f.lst - fvc * 8.0;
            }
            if (vegTemp > 40) { vegTempStatus.innerText = `🔴 ${vegTemp.toFixed(1)}°C`; vegTempDetail.innerText = window.t('غلق الثغور — خطر على المحصول', 'Stomata closure — Crop danger'); vegTempEl.style.borderColor = '#ef4444'; }
            else if (vegTemp > 32) { vegTempStatus.innerText = `🟠 ${vegTemp.toFixed(1)}°C`; vegTempDetail.innerText = window.t('حرارة مرتفعة — راقب المحصول', 'High heat — Monitor crop'); vegTempEl.style.borderColor = '#f97316'; }
            else if (vegTemp > 15) { vegTempStatus.innerText = `🟢 ${vegTemp.toFixed(1)}°C`; vegTempDetail.innerText = window.t('حرارة نبات مناسبة — نمو سليم', 'Good plant temp — Healthy growth'); vegTempEl.style.borderColor = '#22c55e'; }
            else { vegTempStatus.innerText = `🔵 ${vegTemp.toFixed(1)}°C`; vegTempDetail.innerText = window.t('نبات بارد — بطيء العمليات', 'Cold plant — Slow processing'); vegTempEl.style.borderColor = '#3b82f6'; }
        } else { vegTempStatus.innerText = '—'; vegTempDetail.innerText = window.t('شغّل التحليل', 'Run Analysis'); vegTempEl.style.borderColor = '#6b7280'; }
    }

    // 5. LST الخام = القراءة الفضائية الحقيقية ─────────────────
    const lstCombEl = document.getElementById('lc-lst-combined');
    const lstStatus = document.getElementById('lc-lst-status');
    const lstDetail = document.getElementById('lc-lst-detail');
    if (lstCombEl && lstStatus) {
        if (hasData && f.lst != null) {
            lstStatus.innerText = `${f.lst.toFixed(1)}°C`;
            if (lstDetail) lstDetail.innerText = window.t('قيمة MODIS/Sentinel الخام', 'Raw MODIS/Sentinel Data');
            lstCombEl.style.borderColor = f.lst > 45 ? '#ef4444' : f.lst > 35 ? '#f97316' : f.lst > 20 ? '#22c55e' : '#3b82f6';
        } else {
            lstStatus.innerText = '—'; if (lstDetail) lstDetail.innerText = window.t('من بيانات الأقمار', 'From satellite data');
            lstCombEl.style.borderColor = '#6b7280';
        }
    }

    // 6. مؤشر الجفاف TVDI ──────────────────────────────────────
    const droughtEl = document.getElementById('lc-drought');
    const droughtStatus = document.getElementById('lc-drought-status');
    const droughtDetail = document.getElementById('lc-drought-detail');
    if (droughtEl && droughtStatus && droughtDetail) {
        if (hasData && f.tvdi != null) {
            if (f.tvdi > 0.7) { droughtStatus.innerText = `🔴 ${f.tvdi.toFixed(2)}`; droughtDetail.innerText = window.t('جفاف شديد — ري عاجل', 'Severe drought — Urgent irrigation'); droughtEl.style.borderColor = '#ef4444'; }
            else if (f.tvdi > 0.4) { droughtStatus.innerText = `🟡 ${f.tvdi.toFixed(2)}`; droughtDetail.innerText = window.t('جفاف متوسط — راقب الري', 'Moderate drought — Monitor irrigation'); droughtEl.style.borderColor = '#eab308'; }
            else { droughtStatus.innerText = `🟢 ${f.tvdi.toFixed(2)}`; droughtDetail.innerText = window.t('رطوبة تربة جيدة', 'Good soil moisture'); droughtEl.style.borderColor = '#22c55e'; }
        } else { droughtStatus.innerText = '—'; droughtDetail.innerText = window.t('شغّل التحليل', 'Run Analysis'); droughtEl.style.borderColor = '#6b7280'; }
    }
}

// Helper for WUE rating display — كفاءة المياه بوحدة كجم/م³
const formatWUE = (wueVal) => {
    if (wueVal == null || wueVal <= 0) return '—';
    const num = wueVal.toFixed(2);
    // Agricultural thresholds for Egyptian crops (kg biomass per m³ water)
    // WUE < 0.8 = poor, 0.8–1.5 = average, > 1.5 = excellent
    if (wueVal > 2.0) return window.CURRENT_LANG === 'ar'
        ? `✅ ممتاز (${num} كجم/م³)` : `✅ Excellent (${num} kg/m³)`;
    if (wueVal > 1.5) return window.CURRENT_LANG === 'ar'
        ? `🟢 جيد (${num} كجم/م³)` : `🟢 Good (${num} kg/m³)`;
    if (wueVal > 0.8) return window.CURRENT_LANG === 'ar'
        ? `🟡 متوسط (${num} كجم/م³)` : `🟡 Average (${num} kg/m³)`;
    return window.CURRENT_LANG === 'ar'
        ? `🔴 ضعيف (${num} كجم/م³)` : `🔴 Poor (${num} kg/m³)`;
};

// ── Per-Crop Biomass Display (New Tab) ───────────────────────────
function renderCropDetails(f) {
    if (!window.FARM_GEOJSON || window.FARM_GEOJSON === 'None' || window.FARM_GEOJSON.trim() === '') return;

    const areaDisplay = document.getElementById('map-area-display');
    const cropGrid = document.getElementById('crop-cards-grid');

    try {
        const geoData = JSON.parse(window.FARM_GEOJSON);
        const features = geoData.features || (geoData.type === 'Feature' ? [geoData] : []);

        let cropAreas = [];
        let totalMappedAreaM2 = 0;

        features.forEach(feature => {
            const props = feature.properties || {};
            const geom = feature.geometry;
            if (!geom) return;

            let m2 = 0;
            if (props.sectorAngle && props.sectorRadius) {
                m2 = (props.sectorAngle / 360) * Math.PI * props.sectorRadius ** 2;
            } else if (geom.type === 'Point' && props.radius) {
                m2 = Math.PI * props.radius ** 2;
            } else if (geom.type === 'Polygon') {
                m2 = calculatePolygonAreaM2(geom.coordinates[0]);
            } else if (geom.type === 'MultiPolygon') {
                geom.coordinates.forEach(poly => { m2 += calculatePolygonAreaM2(poly[0]); });
            }

            if (m2 > 0) {
                totalMappedAreaM2 += m2;
                cropAreas.push({
                    label: props.zoneLabel || 'منطقة مزرعة',
                    areaM2: m2
                });
            }
        });

        // 1. Update the base area on the Overview tab
        if (areaDisplay && totalMappedAreaM2 > 0) {
            areaDisplay.innerHTML = `📐 ${window.t('إجمالي مساحة الأرض المستهدفة:', 'Targeted Land Area:')} <b style="color:#4ade80;">${formatAreaArabic(totalMappedAreaM2)}</b>`;
        }

        // 2. Populate the Crops tab Grid if we have crop sections drawn
        if (cropGrid && cropAreas.length > 0 && f) {
            let cardsHtml = '';

            // Helpful icons dictionary based on common crop names in Arabic
            const cropIcons = {
                'قمح': '🌾', 'ذرة': '🌽', 'طماطم': '🍅', 'عنب': '🍇', 'بطاطس': '🥔',
                'زيتون': '🫒', 'فراولة': '🍓', 'مانجو': '🥭', 'فول': '🌱', 'بصل': '🧅',
                'ثوم': '🧄', 'برتقال': '🍊'
            };

            cropAreas.forEach(crop => {
                let cropBiomass = f.biomass != null ? (crop.areaM2 / 4200) * f.biomass : null;

                // Per-crop ET: proportional to area fraction of total
                let cropET = null;
                if (f.et != null && totalMappedAreaM2 > 0) {
                    cropET = f.et * (crop.areaM2 / totalMappedAreaM2);
                }

                // Per-crop WUE: Biomass(kg) / Water volume(m³)
                // Water volume = ET(mm/day) × 30 days × area(m²) / 1000
                let cropWUE = null;
                let waterVolumeM3 = 0;
                if (cropBiomass != null && cropET != null && cropET > 0) {
                    waterVolumeM3 = (cropET / 1000.0) * 30.0 * crop.areaM2;
                    if (waterVolumeM3 > 0) {
                        cropWUE = (cropBiomass * 1000.0) / waterVolumeM3;  // kg/m³
                    }
                }

                // حساب ساعات تشغيل جهاز الري وكمية المياه الداخلة للأرض
                let irrigHours = 0;
                let irrigKwh = 0;
                let dailyWaterM3 = 0;
                // استخدم تصرف البئر إن وجد، وإلا استخدم معدل المضخة القديم
                const effectiveFlowRate = (window.WELL_FLOW_RATE > 0)
                    ? window.WELL_FLOW_RATE
                    : (window.PUMP_FLOW_RATE > 0 ? window.PUMP_FLOW_RATE : 0);

                if (effectiveFlowRate > 0 && cropET != null && crop.areaM2 > 0) {
                    // كمية المياه اليومية: ET(mm) × مساحة(م²) ÷ 1000 = م³/يوم
                    dailyWaterM3 = (cropET / 1000.0) * crop.areaM2;
                    // ساعات تشغيل جهاز الري = كمية المياه (م³) ÷ تصرف البئر (م³/ساعة)
                    irrigHours = dailyWaterM3 / effectiveFlowRate;
                    if (window.PUMP_POWER_KW > 0) {
                        irrigKwh = irrigHours * window.PUMP_POWER_KW;
                    }
                }

                // Find matching icon or default
                let icon = '🌱';
                for (const [key, val] of Object.entries(cropIcons)) {
                    if (crop.label.includes(key)) { icon = val; break; }
                }

                const fmt = (v, d = 2) => (v !== null && v !== undefined) ? Number(v).toFixed(d) : '—';

                cardsHtml += `
                <div class="glass p-5 rounded-xl border-t-4" style="border-color: #52b788; background: linear-gradient(180deg, rgba(82, 183, 136, 0.05) 0%, rgba(0,0,0,0) 100%);">
                    <div class="flex items-center gap-3 mb-4 border-b border-white/10 pb-3">
                        <span class="text-3xl">${icon}</span>
                        <h4 class="text-lg font-bold text-white">${crop.label}</h4>
                    </div>
                    
                    <div class="flex flex-col gap-3">
                        <div class="flex items-center justify-between pb-2 border-b border-white/5">
                            <span class="text-sm text-gray-400">${window.t('📐 مساحة الجزء', '📐 Sector Area')}</span>
                            <span class="font-bold text-[#4ade80]">${formatAreaArabic(crop.areaM2)}</span>
                        </div>
                        
                        <div class="grid grid-cols-2 gap-2 mt-2">
                            <div class="bg-black/20 p-2 rounded-lg text-center" style="border-top: 2px solid #10b981;">
                                <p class="text-[10px] text-gray-400 mb-1">${window.t('الكتلة الحيوية', 'Biomass')}</p>
                                <p class="text-lg font-bold text-[#fbbf24]">${cropBiomass !== null ? cropBiomass.toFixed(2) + ' ' + window.t('طن/فدان', 't/fed') : '—'}</p>
                            </div>
                            
                            <div class="bg-black/20 p-2 rounded-lg text-center" style="border-top: 2px solid #10b981;">
                                <p class="text-[10px] text-gray-400 mb-1">NDVI</p>
                                <p class="text-lg font-bold text-white">${fmt(f.ndvi, 2)}</p>
                            </div>
                            
                            <div class="bg-black/20 p-2 rounded-lg text-center" style="border-top: 2px solid #3b82f6;">
                                <p class="text-[10px] text-gray-400 mb-1">${window.t('التبخّر (ET mm/يوم)', 'ET (mm/day)')}</p>
                                <p class="text-lg font-bold text-white">${fmt(f.et, 1)}</p>
                            </div>
                            
                            <div class="bg-black/20 p-2 rounded-lg text-center" style="border-top: 2px solid #3b82f6;">
                                <p class="text-[10px] text-gray-400 mb-1">${window.t('كفاءة المياه كجم/م³', 'Water Eff. kg/m³')}</p>
                                <p class="text-sm font-bold text-[#60a5fa] mt-1">${formatWUE(cropWUE)}</p>
                            </div>
                            
                            <div class="bg-black/20 p-2 rounded-lg text-center" style="border-top: 2px solid #f97316;">
                                <p class="text-[10px] text-gray-400 mb-1">${window.t('حرارة السطح (LST)', 'LST (°C)')}</p>
                                <p class="text-lg font-bold text-white">${fmt(f.lst, 1)}°C</p>
                            </div>
                            
                            <div class="bg-black/20 p-2 rounded-lg text-center" style="border-top: 2px solid #ef4444;">
                                <p class="text-[10px] text-gray-400 mb-1">${window.t('مؤشر الجفاف (TVDI)', 'Drought (TVDI)')}</p>
                                <p class="text-lg font-bold text-white">${fmt(f.tvdi, 2)}</p>
                            </div>

                            <div class="bg-black/20 p-2 rounded-lg text-center" style="border-top: 2px solid #22d3ee;">
                                <p class="text-[10px] text-gray-400 mb-1">${window.t('كمية المياه الداخلة', 'Water Volume (m³/day)')}</p>
                                <p class="text-lg font-bold text-[#22d3ee]">${dailyWaterM3 > 0 ? dailyWaterM3.toFixed(1) + ' م³' : '—'}</p>
                                <p class="text-[9px] text-gray-500">${window.t('ET × مساحة / 1000', 'ET × Area / 1000')}</p>
                            </div>

                            <div class="bg-black/20 p-2 rounded-lg text-center" style="border-top: 2px solid #0ea5e9;">
                                <p class="text-[10px] text-gray-400 mb-1">${window.t('مؤشر رطوبة النبات (NDWI)', 'Water Content (NDWI)')}</p>
                                <p class="text-lg font-bold text-white">${fmt(f.ndwi, 2)}</p>
                            </div>

                            <div class="col-span-2 bg-black/20 p-2 rounded-lg text-center flex items-center justify-around" style="border-top: 2px solid #cbd5e1;">
                                <div>
                                    <p class="text-[10px] text-gray-400 mb-1">${window.t('⭐ ساعات تشغيل جهاز الري', '⭐ Irrigation Device Hours')}</p>
                                    <p class="text-lg font-bold text-[#e2e8f0]">${irrigHours > 0 ? irrigHours.toFixed(1) + ' ' + window.t('ساعة', 'h') : '—'}</p>
                                </div>
                                <div class="border-l border-white/10 pl-4 ml-4">
                                    <p class="text-[10px] text-gray-400 mb-1">${window.t('استهلاك الكهرباء', 'Power Usage')}</p>
                                    <p class="text-lg font-bold text-[#fbbf24]">${irrigKwh > 0 ? irrigKwh.toFixed(1) + ' kWh' : '—'}</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>`;
            });

            cropGrid.innerHTML = cardsHtml;
        }

    } catch (e) {
        console.error("Error computing crop details grid:", e);
    }
}

// ── Chatbot ──────────────────────────────────────────────────
function toggleChatbot() {
    const win = document.getElementById('chatbot-window');
    if (!win) return;
    chatOpen = !chatOpen;
    if (chatOpen) {
        win.classList.add('open');
        const inp = document.getElementById('chat-input');
        if (inp) inp.focus();
    } else {
        win.classList.remove('open');
    }
}

function addMessage(text, isUser) {
    const msgs = document.getElementById('chat-messages');
    if (!msgs) return;
    const div = document.createElement('div');
    div.className = `chat-bubble ${isUser ? 'bubble-user text-white' : 'bubble-bot text-green-100'}`;
    div.innerHTML = text.replace(/\n/g, '<br>');
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
}

function showTyping() {
    const msgs = document.getElementById('chat-messages');
    if (!msgs) return;
    const div = document.createElement('div');
    div.id = 'typing-indicator';
    div.className = 'chat-bubble bubble-bot typing-indicator';
    div.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
}

function hideTyping() {
    const el = document.getElementById('typing-indicator');
    if (el) el.remove();
}

async function sendChat(messageText) {
    const input = document.getElementById('chat-input');
    const text = messageText || (input ? input.value.trim() : '');
    if (!text) return;
    if (!messageText && input) input.value = '';
    addMessage(text, true);
    showTyping();
    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text })
        });
        const data = await res.json();
        hideTyping();
        if (data.success) addMessage(data.response, false);
        else addMessage(window.t("عذراً، حصل خطأ في الاتصال معايا. جرّب تاني.", "Sorry, connection error occurred. Try again."), false);
    } catch {
        hideTyping();
        addMessage(window.t("الإنترنت فاصل يا حاج، مش قادر أوصل للسيرفر.", "No internet connection, cannot reach server."), false);
    }
}

function sendQuick(q) { sendChat(q); }
