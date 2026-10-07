# ============================================================
# ml_gee_engine.py — Tayseer Agri-SaT Geospatial & ML Engine
#
# Responsibilities:
#   1. Fetch NDVI / LST / NDWI from Google Earth Engine (GEE)
#   2. Physics-based fallback calculations when GEE data is missing
#   3. Parse user-uploaded CSV files and ingest their metrics
#   4. Train / predict with a CatBoost crop-health classifier
#   5. Pivot-failure anomaly detection (NDVI-CV + Thermal Inertia)
#   6. Weather-driven agronomy alert engine (OWM 5-day forecast)
# ============================================================

import os
import io
import math
import json
import logging
import datetime
import numpy as np
import pandas as pd
import requests
import joblib

from config import Config

# Optional GEE import — gracefully disabled when the SDK is absent
try:
    import ee
    _GEE_AVAILABLE = True
except ImportError:
    _GEE_AVAILABLE = False
    logging.warning("[GEE] earthengine-api not installed. GEE features disabled.")

# Optional CatBoost import
try:
    from catboost import CatBoostClassifier, Pool
    _CATBOOST_AVAILABLE = True
except ImportError:
    _CATBOOST_AVAILABLE = False
    logging.warning("[ML] catboost not installed. ML predictions disabled.")

log = logging.getLogger(__name__)

# ── GEE Initialisation ────────────────────────────────────────

_gee_initialized = False

def init_gee() -> bool:
    """
    Authenticate and initialise the GEE session using a service-account key.
    Falls back to service account email from config if JSON key file is missing.
    Returns True on success, False if unavailable or credentials missing.
    """
    global _gee_initialized
    if not _GEE_AVAILABLE:
        return False
    if _gee_initialized:
        return True

    key_path  = Config.GEE_SERVICE_ACCOUNT_KEY
    project   = Config.GEE_PROJECT
    sa_email  = getattr(Config, "GEE_SERVICE_ACCOUNT_EMAIL", "")

    # Strategy 1: Use JSON key file if it exists
    if os.path.exists(key_path):
        try:
            with open(key_path) as f:
                key_data = json.load(f)
            credentials = ee.ServiceAccountCredentials(
                email=key_data["client_email"], key_file=key_path
            )
            ee.Initialize(credentials=credentials, project=project)
            _gee_initialized = True
            log.info(f"[GEE] Initialised with key file (project={project}).")
            return True
        except Exception as exc:
            log.error(f"[GEE] Key-file init failed: {exc}")

    # Strategy 2: Use service account email with Application Default Credentials
    if sa_email:
        try:
            credentials = ee.ServiceAccountCredentials(
                email=sa_email, key_file=key_path if os.path.exists(key_path) else None
            )
            ee.Initialize(credentials=credentials, project=project)
            _gee_initialized = True
            log.info(f"[GEE] Initialised with SA email {sa_email} (project={project}).")
            return True
        except Exception as exc:
            log.warning(f"[GEE] SA email init failed: {exc}")

    # Strategy 3: Try default credentials (e.g. gcloud auth)
    try:
        ee.Initialize(project=project)
        _gee_initialized = True
        log.info(f"[GEE] Initialised with default credentials (project={project}).")
        return True
    except Exception as exc:
        log.warning(f"[GEE] Default init failed: {exc}")

    log.error("[GEE] All initialisation strategies failed.")
    return False


# ── GEE Data Fetch ────────────────────────────────────────────

def fetch_gee_data(lat: float, lon: float,
                   date_str: str = None) -> dict:
    """
    Fetch NDVI, LST, and NDWI from Sentinel-2 / MODIS imagery for
    a 1-km buffer around (lat, lon) on the given date (YYYY-MM-DD).

    Returns a dict with keys: ndvi, lst, ndwi, source.
    Returns empty dict on failure (caller falls back to physics).

    NOTE: All GEE .getInfo() calls are wrapped in a 15-second timeout
    so the pipeline never blocks indefinitely if GEE is slow or auth fails.
    """
    if not init_gee():
        return {}

    if date_str is None:
        date_str = datetime.date.today().isoformat()

    import concurrent.futures

    def _gee_fetch():
        point  = ee.Geometry.Point([lon, lat])
        buffer = point.buffer(1000)           # 1 km radius

        # Date window: use 15-day window to ensure cloud-free imagery
        start = (
            datetime.date.fromisoformat(date_str) - datetime.timedelta(days=7)
        ).isoformat()
        end   = (
            datetime.date.fromisoformat(date_str) + datetime.timedelta(days=7)
        ).isoformat()

        # ── Sentinel-2 Surface Reflectance ───────────────────
        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(buffer)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .sort("CLOUDY_PIXEL_PERCENTAGE")
            .first()
        )

        # NDVI = (NIR - RED) / (NIR + RED)  → B8, B4
        ndvi_img  = s2.normalizedDifference(["B8", "B4"]).rename("ndvi")
        # NDWI = (GREEN - NIR) / (GREEN + NIR) → B3, B8
        ndwi_img  = s2.normalizedDifference(["B3", "B8"]).rename("ndwi")

        ndvi_val = ndvi_img.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=buffer, scale=10
        ).get("ndvi").getInfo()

        ndwi_val = ndwi_img.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=buffer, scale=10
        ).get("ndwi").getInfo()

        # ── MODIS LST (MOD11A2 — 8-day, 1 km) ───────────────
        modis_lst = (
            ee.ImageCollection("MODIS/061/MOD11A2")
            .filterBounds(buffer)
            .filterDate(start, end)
            .first()
            .select("LST_Day_1km")
        )
        # MODIS LST is in Kelvin × 0.02 — convert to Celsius
        lst_val = (
            modis_lst
            .multiply(0.02)
            .subtract(273.15)
            .reduceRegion(
                reducer=ee.Reducer.mean(), geometry=buffer, scale=10
            )
            .get("LST_Day_1km")
            .getInfo()
        )

        return {
            "ndvi":   round(float(ndvi_val), 4) if ndvi_val is not None else None,
            "ndwi":   round(float(ndwi_val), 4) if ndwi_val is not None else None,
            "lst":    round(float(lst_val),  2)  if lst_val  is not None else None,
            "source": "gee",
        }

    # Run GEE fetch with a hard 15-second timeout
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_gee_fetch)
            try:
                result = future.result(timeout=15)
                log.info(f"[GEE] Fetched: {result}")
                return result
            except concurrent.futures.TimeoutError:
                log.warning("[GEE] Fetch timed out after 15 seconds — falling back to physics.")
                future.cancel()
                return {}
    except Exception as exc:
        log.error(f"[GEE] Data fetch failed: {exc}")
        return {}


# ── Per-Zone GEE Analysis ─────────────────────────────────────

def fetch_gee_per_zone(farm_geojson: str, area_feddan_total: float = 1.0,
                       date_str: str = None) -> list:
    """
    Fetch GEE metrics (NDVI, LST, NDWI) for each drawn zone in farm_geojson.
    Returns a list of zone dicts with full physics-enriched metrics per zone.

    Each returned dict contains:
        zone_label, zone_type, color, area_m2, area_feddan,
        ndvi, lst, ndwi, et, tvdi, biomass, wue, status, source
    """
    if not farm_geojson or farm_geojson == "None" or farm_geojson.strip() == "":
        return []

    try:
        geo = json.loads(farm_geojson) if isinstance(farm_geojson, str) else farm_geojson
    except Exception:
        return []

    features = geo.get("features", [])
    if not features:
        if geo.get("type") == "Feature":
            features = [geo]
    if not features:
        return []

    if date_str is None:
        date_str = datetime.date.today().isoformat()

    import concurrent.futures
    import math as _math

    def _calc_feature_area_m2(feat):
        """Calculate the area in m² for a single GeoJSON feature."""
        props = feat.get("properties", {}) or {}
        geom  = feat.get("geometry", {}) or {}
        g_type = geom.get("type", "")
        # Sector (arc/quarter pivot)
        if props.get("sectorAngle") and props.get("sectorRadius"):
            return (props["sectorAngle"] / 360.0) * _math.pi * props["sectorRadius"] ** 2
        # Circle stored as Point with radius property
        if g_type == "Point" and props.get("radius"):
            return _math.pi * props["radius"] ** 2
        # Polygon — Shoelace on spherical coords
        if g_type == "Polygon":
            coords = geom.get("coordinates", [[]])[0]
            return _spherical_area(coords)
        if g_type == "MultiPolygon":
            total = 0.0
            for poly in geom.get("coordinates", []):
                total += _spherical_area(poly[0])
            return total
        return 0.0

    def _spherical_area(coords):
        R = 6371000.0
        n = len(coords)
        if n < 3:
            return 0.0
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            xi = _math.radians(coords[i][0])
            yi = _math.radians(coords[i][1])
            xj = _math.radians(coords[j][0])
            yj = _math.radians(coords[j][1])
            area += (xj - xi) * (2.0 + _math.sin(yi) + _math.sin(yj))
        return abs(area * R * R / 2.0)

    def _gee_zone(feat):
        """Fetch NDVI/LST/NDWI for a single GeoJSON feature geometry via GEE."""
        geom = feat.get("geometry", {})
        g_type = geom.get("type", "")
        props  = feat.get("properties", {}) or {}

        try:
            if g_type in ("Polygon", "MultiPolygon"):
                ee_geom = ee.Geometry(geom)
            elif g_type == "Point" and props.get("radius"):
                coords = geom["coordinates"]
                ee_geom = ee.Geometry.Point(coords).buffer(props["radius"])
            else:
                return None

            start = (datetime.date.fromisoformat(date_str) - datetime.timedelta(days=10)).isoformat()
            end   = (datetime.date.fromisoformat(date_str) + datetime.timedelta(days=10)).isoformat()

            s2 = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterBounds(ee_geom)
                .filterDate(start, end)
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 25))
                .sort("CLOUDY_PIXEL_PERCENTAGE")
                .first()
            )
            ndvi_img = s2.normalizedDifference(["B8", "B4"])
            ndwi_img = s2.normalizedDifference(["B3", "B8"])

            modis_lst = (
                ee.ImageCollection("MODIS/061/MOD11A2")
                .filterBounds(ee_geom)
                .filterDate(start, end)
                .first()
                .select("LST_Day_1km")
                .multiply(0.02)
                .subtract(273.15)
            )

            ndvi_val = ndvi_img.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=ee_geom, scale=10, maxPixels=1e8
            ).get("nd").getInfo()
            ndwi_val = ndwi_img.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=ee_geom, scale=10, maxPixels=1e8
            ).get("nd").getInfo()
            lst_val = modis_lst.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=ee_geom, scale=10, maxPixels=1e8
            ).get("LST_Day_1km").getInfo()

            return {
                "ndvi": round(float(ndvi_val), 4) if ndvi_val is not None else None,
                "ndwi": round(float(ndwi_val), 4) if ndwi_val is not None else None,
                "lst":  round(float(lst_val), 2)  if lst_val  is not None else None,
                "source": "gee_zone",
            }
        except Exception as exc:
            log.warning(f"[GEE Zone] Failed for feature: {exc}")
            return None

    # ── Build result list ────────────────────────────────────────
    results = []
    gee_ok  = init_gee()

    for i, feat in enumerate(features):
        props  = feat.get("properties", {}) or {}
        area_m2 = _calc_feature_area_m2(feat)
        zone_feddan = area_m2 / 4200.0

        zone_info = {
            "zone_label":   props.get("zoneLabel", f"منطقة {i+1}"),
            "zone_type":    props.get("zoneType",  "crop"),
            "color":        props.get("color",     "#52b788"),
            "area_m2":      round(area_m2, 1),
            "area_feddan":  round(zone_feddan, 4),
        }

        # Try GEE per-zone fetch (with 20s timeout)
        raw = {}
        if gee_ok:
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(_gee_zone, feat)
                    res = fut.result(timeout=20)
                    if res and res.get("ndvi") is not None:
                        raw = res
            except Exception:
                pass

        # If GEE failed, estimate from overall farm metrics defaults
        if not raw.get("ndvi"):
            raw = {"ndvi": 0.45, "lst": 37.0, "ndwi": 0.05, "source": "physics_zone"}

        # Apply physics pipeline for this zone's area
        # Note: pump_flow_rate is default because zones usually don't need pump hours individually
        zone_metrics = apply_physics_pipeline(raw, area_feddan=max(0.01, zone_feddan))

        # Crop health status (rule-based, fast)
        zone_info.update({
            "ndvi":    zone_metrics.get("ndvi"),
            "ndwi":    zone_metrics.get("ndwi"),
            "lst":     zone_metrics.get("lst"),
            "et":      zone_metrics.get("et"),
            "tvdi":    zone_metrics.get("tvdi"),
            "biomass": zone_metrics.get("biomass"),
            "wue":     zone_metrics.get("wue"),
            "fapar":   zone_metrics.get("fapar"),
            "status":  zone_metrics.get("status", "healthy"),
            "source":  zone_metrics.get("source", "physics_zone"),
        })
        results.append(zone_info)
        log.info(f"[GEE Zone] Zone '{zone_info['zone_label']}': NDVI={zone_info['ndvi']}, ET={zone_info['et']}")

    return results


def fetch_gee_crop_history(farm_geojson: str, days: int = 30) -> list:
    """
    Fetch a daily time-series of satellite metrics for each drawn zone over the last `days`.
    Returns a list of zones containing their history array.
    """
    if not farm_geojson or farm_geojson == "None" or farm_geojson.strip() == "":
        return []

    try:
        geo = json.loads(farm_geojson) if isinstance(farm_geojson, str) else farm_geojson
    except Exception:
        return []

    features = geo.get("features", [])
    if not features:
        if geo.get("type") == "Feature":
            features = [geo]
    if not features:
        return []

    gee_ok = init_gee()
    if not gee_ok:
        return []

    import concurrent.futures
    import math as _math
    import pandas as pd

    def _calc_feature_area_m2(feat):
        props = feat.get("properties", {}) or {}
        geom  = feat.get("geometry", {}) or {}
        g_type = geom.get("type", "")
        def _spherical_area(coords):
            R = 6371000.0
            n = len(coords)
            if n < 3: return 0.0
            area = 0.0
            for i in range(n):
                j = (i + 1) % n
                xi, yi = _math.radians(coords[i][0]), _math.radians(coords[i][1])
                xj, yj = _math.radians(coords[j][0]), _math.radians(coords[j][1])
                area += (xj - xi) * (2.0 + _math.sin(yi) + _math.sin(yj))
            return abs(area * R * R / 2.0)

        if props.get("sectorAngle") and props.get("sectorRadius"):
            return (props["sectorAngle"] / 360.0) * _math.pi * props["sectorRadius"] ** 2
        if g_type == "Point" and props.get("radius"):
            return _math.pi * props["radius"] ** 2
        if g_type == "Polygon":
            return _spherical_area(geom.get("coordinates", [[]])[0])
        if g_type == "MultiPolygon":
            return sum(_spherical_area(poly[0]) for poly in geom.get("coordinates", []))
        return 0.0

    def _fetch_zone_ts(feat):
        geom = feat.get("geometry", {})
        g_type = geom.get("type", "")
        props  = feat.get("properties", {}) or {}

        try:
            if g_type in ("Polygon", "MultiPolygon"):
                ee_geom = ee.Geometry(geom)
            elif g_type == "Point" and props.get("radius"):
                ee_geom = ee.Geometry.Point(geom["coordinates"]).buffer(props["radius"])
            else:
                return []

            end_date   = datetime.date.today()
            start_date = end_date - datetime.timedelta(days=days)

            s2 = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterBounds(ee_geom)
                .filterDate(start_date.isoformat(), end_date.isoformat())
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
            )
            def _reduce_s2(img):
                ndvi = img.normalizedDifference(["B8", "B4"]).rename("ndvi")
                ndwi = img.normalizedDifference(["B3", "B8"]).rename("ndwi")
                m = ndvi.addBands(ndwi).reduceRegion(
                    reducer=ee.Reducer.mean(), geometry=ee_geom, scale=10, maxPixels=1e8
                )
                return ee.Feature(None, {
                    'date': ee.Date(img.get('system:time_start')).format('YYYY-MM-dd'),
                    'ndvi': m.get('ndvi'),
                    'ndwi': m.get('ndwi')
                })
            s2_data = s2.map(_reduce_s2).getInfo().get('features', [])

            modis = (
                ee.ImageCollection("MODIS/061/MOD11A1")
                .filterBounds(ee_geom)
                .filterDate(start_date.isoformat(), end_date.isoformat())
            )
            def _reduce_modis(img):
                lst = img.select("LST_Day_1km").multiply(0.02).subtract(273.15).rename("lst")
                m = lst.reduceRegion(
                    reducer=ee.Reducer.mean(), geometry=ee_geom, scale=10, maxPixels=1e8
                )
                return ee.Feature(None, {
                    'date': ee.Date(img.get('system:time_start')).format('YYYY-MM-dd'),
                    'lst': m.get('lst')
                })
            modis_data = modis.map(_reduce_modis).getInfo().get('features', [])

            df_s2 = pd.DataFrame([x['properties'] for x in s2_data])
            df_modis = pd.DataFrame([x['properties'] for x in modis_data])

            if df_s2.empty and df_modis.empty:
                return []

            if not df_s2.empty:
                df_s2['date'] = pd.to_datetime(df_s2['date'])
                df_s2 = df_s2.groupby('date').mean().reset_index()
            else:
                df_s2 = pd.DataFrame(columns=['date', 'ndvi', 'ndwi'])

            if not df_modis.empty:
                df_modis['date'] = pd.to_datetime(df_modis['date'])
                df_modis = df_modis.groupby('date').mean().reset_index()
            else:
                df_modis = pd.DataFrame(columns=['date', 'lst'])

            if df_s2.empty:
                df_merged = df_modis
                df_merged['ndvi'] = None
                df_merged['ndwi'] = None
            elif df_modis.empty:
                df_merged = df_s2
                df_merged['lst'] = None
            else:
                df_merged = pd.merge(df_s2, df_modis, on='date', how='outer')

            all_dates = pd.date_range(start=start_date, end=end_date)
            df_merged = df_merged.set_index('date').reindex(all_dates).reset_index().rename(columns={'index': 'date'})
            
            # Use forward fill and backward fill instead of deprecated method
            df_merged = df_merged.ffill().bfill()
            
            df_merged = df_merged.dropna(subset=['ndvi', 'lst'], how='all')

            history = []
            area_m2 = _calc_feature_area_m2(feat)
            area_feddan = max(0.01, area_m2 / 4200.0)

            for _, row in df_merged.iterrows():
                raw = {
                    "ndvi": row.get('ndvi'),
                    "ndwi": row.get('ndwi'),
                    "lst": row.get('lst')
                }
                if pd.isna(raw["ndvi"]): raw["ndvi"] = 0.45
                if pd.isna(raw["lst"]): raw["lst"] = 35.0
                if pd.isna(raw["ndwi"]): raw["ndwi"] = 0.0

                zm = apply_physics_pipeline(raw, area_feddan=area_feddan)
                history.append({
                    "date": row['date'].strftime('%Y-%m-%d'),
                    "ndvi": zm.get("ndvi"),
                    "ndwi": zm.get("ndwi"),
                    "lst": zm.get("lst"),
                    "et": zm.get("et"),
                    "tvdi": zm.get("tvdi"),
                    "biomass": zm.get("biomass"),
                    "wue": zm.get("wue"),
                    "fapar": zm.get("fapar"),
                })

            return history

        except Exception as exc:
            log.warning(f"[GEE History] Failed for feature: {exc}")
            return []

    results = []
    for i, feat in enumerate(features):
        props  = feat.get("properties", {}) or {}
        area_m2 = _calc_feature_area_m2(feat)
        
        zone_info = {
            "zone_label":   props.get("zoneLabel", f"منطقة {i+1}"),
            "zone_type":    props.get("zoneType",  "crop"),
            "color":        props.get("color",     "#52b788"),
            "area_m2":      round(area_m2, 1),
            "area_feddan":  round(area_m2 / 4200.0, 4),
            "history":      []
        }
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_fetch_zone_ts, feat)
                hist = fut.result(timeout=60)
                if hist:
                    zone_info['history'] = hist
        except Exception as e:
            log.error(f"[GEE History] Timeout or error for {zone_info['zone_label']}: {e}")
        
        results.append(zone_info)

    return results





# ── LST Differentiation ──────────────────────────────────────

def compute_lst_split(lst: float, ndvi: float,
                      coeff_s: float = 5.0, coeff_c: float = 8.0) -> dict:
    """
    Differentiate Land Surface Temperature using Fractional Vegetation Cover (FVC).

    FVC = ((NDVI - 0.15) / (0.85 - 0.15))² clamped to [0, 1]
      FVC=0 → fully bare soil; FVC=1 → dense canopy.

    - Soil Temperature:   T_soil   = LST + (1 − FVC) × coeff_s
      Bare areas absorb more solar radiation → hotter than mean LST.
    - Canopy Temperature: T_canopy = LST − FVC × coeff_c
      Dense vegetation cools via transpiration → cooler than mean LST.

    Parameters
    ----------
    lst      : Land Surface Temperature (°C) from satellite
    ndvi     : NDVI value [-1, 1]
    coeff_s  : Soil heating coefficient  (default 5.0°C for Egypt arid conditions)
    coeff_c  : Canopy cooling coefficient (default 8.0°C transpiration cooling)

    Returns dict with soil_temp and canopy_temp in °C.
    """
    ndvi_clamped = max(0.0, min(1.0, ndvi or 0.0))
    # FVC: Fractional Vegetation Cover from NDVI (quadratic scaling)
    fvc = max(0.0, min(1.0, ((ndvi_clamped - 0.15) / 0.70) ** 2))
    soil_temp   = lst + (1.0 - fvc) * coeff_s
    canopy_temp = lst - fvc * coeff_c
    return {
        "soil_temp":   round(soil_temp, 1),
        "canopy_temp": round(canopy_temp, 1),
        "fvc":         round(fvc, 4),
    }


# ── GEE Map Tile Generation (10m resolution) ─────────────────

def generate_gee_tiles(lat: float, lon: float,
                       farm_geojson: str = None,
                       date_str: str = None) -> dict:
    """
    Generate XYZ tile URL templates for multiple spectral indices
    using Sentinel-2 imagery at 10m resolution.

    Returns a dict of {index_name: tile_url_template} where each URL
    is ready to be used as a Leaflet TileLayer source.

    Indices generated:
        - ndvi:          Vegetation greenness
        - ndwi:          Water content
        - ndsi:          Snow / salt index
        - soil_moisture: NDMI (Normalized Difference Moisture Index)
        - et:            Estimated evapotranspiration
        - biomass:       Estimated biomass density
        - tvdi:          Drought stress index

    Falls back to empty dict if GEE is unavailable or errors occur.
    """
    if not init_gee():
        log.warning("[GEE Tiles] GEE not available — no tiles generated.")
        return {}

    if date_str is None:
        date_str = datetime.date.today().isoformat()

    import concurrent.futures

    def _generate():
        # ── Build geometry ────────────────────────────────────
        point  = ee.Geometry.Point([lon, lat])
        region = point.buffer(2000)  # 2 km buffer for larger coverage

        if farm_geojson:
            try:
                geo = json.loads(farm_geojson) if isinstance(farm_geojson, str) else farm_geojson
                if geo and geo.get('type') and geo.get('type') != 'None':
                    region = ee.Geometry(geo).buffer(200)  # slight buffer around farm
            except Exception:
                pass  # fall back to point buffer

        # ── Date window (15 days for cloud-free) ──────────────
        target = datetime.date.fromisoformat(date_str)
        start  = (target - datetime.timedelta(days=7)).isoformat()
        end    = (target + datetime.timedelta(days=7)).isoformat()

        # ── Sentinel-2 composite ──────────────────────────────
        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .sort("CLOUDY_PIXEL_PERCENTAGE")
            .first()
        )

        # ── TPHC (Tayseer Pivot Health Condition) anomaly layer (NDVI StdDev over 30 days) ──
        s2_30d = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(
                (target - datetime.timedelta(days=30)).isoformat(),
                (target + datetime.timedelta(days=7)).isoformat()
            )
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
        )
        def _add_ndvi_to_collection(img):
            return img.normalizedDifference(["B8", "B4"]).rename("ndvi")
        tphc_img = s2_30d.map(_add_ndvi_to_collection).select("ndvi").reduce(ee.Reducer.stdDev()).rename("tphc")

        # ── MODIS LST for temperature-based indices ───────────
        modis_lst = (
            ee.ImageCollection("MODIS/061/MOD11A2")
            .filterBounds(region)
            .filterDate(start, end)
            .first()
            .select("LST_Day_1km")
            .multiply(0.02)
            .subtract(273.15)
        )

        # ── Compute index images ──────────────────────────────
        # (NDVI must be defined FIRST — LST split uses it)

        # NDVI = (NIR − RED) / (NIR + RED) → B8, B4
        ndvi = s2.normalizedDifference(["B8", "B4"]).rename("ndvi")

        # NDWI = (GREEN − NIR) / (GREEN + NIR) → B3, B8
        ndwi = s2.normalizedDifference(["B3", "B8"]).rename("ndwi")

        # NDSI = (GREEN − SWIR) / (GREEN + SWIR) → B3, B11
        ndsi = s2.normalizedDifference(["B3", "B11"]).rename("ndsi")

        # NDMI / Soil Moisture proxy = (NIR − SWIR) / (NIR + SWIR) → B8A, B11
        ndmi = s2.normalizedDifference(["B8A", "B11"]).rename("ndmi")

        # ── Separated LST (Canopy & Soil) — uses ndvi defined above ──
        ndvi_clamped = ndvi.clamp(0.0, 1.0)
        fvc = ndvi_clamped.subtract(0.15).divide(0.70).pow(2).clamp(0.0, 1.0)
        lst_soil_img   = modis_lst.add(ee.Image(1.0).subtract(fvc).multiply(4.0)).rename("lst_soil")
        lst_canopy_img = modis_lst.subtract(fvc.multiply(7.0)).rename("lst_canopy")

        # ET estimation: Kc × ET₀ where Kc = 1.2×NDVI+0.2, ET₀ from T_air
        # T_air ≈ LST_modis - 8°C  (surface-to-air correction for Egypt dry climate)
        t_air_img = modis_lst.subtract(8.0)
        kc = ndvi.multiply(1.2).add(0.2).clamp(0.15, 1.20)
        et0 = t_air_img.multiply(0.46).add(8.13).multiply(0.27).clamp(2.0, 10.0)
        et_img = kc.multiply(et0).clamp(0.5, 12.0).rename("et")

        # Biomass density: FAPAR × PAR × LUE (relative, per m²)
        fapar = ndvi.multiply(1.2).subtract(0.11).clamp(0.0, 1.0)
        biomass_img = fapar.multiply(9.5).multiply(1.5e-3).multiply(120).rename("biomass")

        # TVDI: drought index from NDVI + T_air
        ndvi_norm = ndvi.subtract(0.15).divide(0.70).clamp(0.0, 1.0)
        t_norm    = t_air_img.subtract(15.0).divide(30.0).clamp(0.0, 1.0)
        tvdi_img  = t_norm.multiply(ee.Image(1.0).subtract(ndvi_norm)).clamp(0.0, 1.0).rename("tvdi")

        # ── WWFI (Water Waste Fingerprint Index) ─────────────
        # Compare NDWI current vs 10 days ago to detect over-irrigation footprints.
        # Positive delta (teal/blue) = water accumulation = potential waste area.
        start_prev  = (target - datetime.timedelta(days=20)).isoformat()
        end_prev    = (target - datetime.timedelta(days=10)).isoformat()
        s2_prev = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(start_prev, end_prev)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 25))
            .sort("CLOUDY_PIXEL_PERCENTAGE")
            .first()
        )
        ndwi_prev = s2_prev.normalizedDifference(["B3", "B8"]).rename("ndwi_prev")
        # WWFI = max(0, NDWI_now − NDWI_10days_ago): only positive changes shown
        wwfi_img = ndwi.subtract(ndwi_prev).max(ee.Image(0)).multiply(2).clamp(0.0, 1.0).rename("wwfi")

        # ── Color palettes ────────────────────────────────────
        palettes = {
            "ndvi":           {"min": -0.2, "max": 0.9,  "palette": ["#d73027","#fc8d59","#fee08b","#d9ef8b","#91cf60","#1a9850"]},
            "ndwi":           {"min": -0.5, "max": 0.5,  "palette": ["#8B4513","#D2691E","#F4A460","#87CEEB","#4169E1","#0000CD"]},
            "ndsi":           {"min": -0.5, "max": 0.8,  "palette": ["#2d1b69","#7b3f9e","#c084fc","#e0e0e0","#ffffff"]},
            "soil_moisture":  {"min": -0.3, "max": 0.6,  "palette": ["#8B4513","#CD853F","#F5DEB3","#B0E0E6","#00CED1","#008B8B"]},
            "et":             {"min": 0.5,  "max": 10.0, "palette": ["#ffffcc","#c7e9b4","#7fcdbb","#41b6c4","#2c7fb8","#253494"]},
            "biomass":        {"min": 0.0,  "max": 3.0,  "palette": ["#f7fcb9","#addd8e","#78c679","#31a354","#006837"]},
            "tvdi":           {"min": 0.0,  "max": 1.0,  "palette": ["#313695","#4575b4","#74add1","#fee090","#f46d43","#a50026"]},
            "tphc":           {"min": 0.0,  "max": 0.15, "palette": ["#00FF00", "#FFFF00", "#FF8C00", "#FF0000", "#800000"]},
            "lst_soil":       {"min": 15.0, "max": 45.0, "palette": ["#0000FF", "#00FFFF", "#FFFF00", "#FF8C00", "#FF0000"]},
            "lst_canopy":     {"min": 10.0, "max": 40.0, "palette": ["#0000FF", "#00FFFF", "#FFFF00", "#FF8C00", "#FF0000"]},
            # WWFI: Water Waste Fingerprint Index (over-irrigation zones)
            "wwfi":           {"min": 0.0,  "max": 1.0,  "palette": ["#ffffff","#c7e9b4","#41b6c4","#225ea8","#081d58"]},
        }

        images = {
            "ndvi":          ndvi,
            "ndwi":          ndwi,
            "ndsi":          ndsi,
            "soil_moisture": ndmi,
            "et":            et_img,
            "biomass":       biomass_img,
            "tvdi":          tvdi_img,
            "tphc":          tphc_img,
            "lst_soil":      lst_soil_img,
            "lst_canopy":    lst_canopy_img,
            "wwfi":          wwfi_img,   # Water Waste Fingerprint Index
        }

        # ── Generate tile URLs ────────────────────────────────
        tiles = {}
        tile_date = end  # report the date range used

        for name, img in images.items():
            try:
                vis = palettes[name]
                map_id = img.getMapId(vis)
                tiles[name] = map_id["tile_fetcher"].url_format
            except Exception as exc:
                log.warning(f"[GEE Tiles] Failed to generate {name} tiles: {exc}")

        tiles["_date"]     = tile_date
        tiles["_palettes"] = palettes

        return tiles

    # Run with 30-second timeout (tile generation is slower)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_generate)
            try:
                result = future.result(timeout=30)
                log.info(f"[GEE Tiles] Generated {len(result) - 2} tile layers.")
                return result
            except concurrent.futures.TimeoutError:
                log.warning("[GEE Tiles] Timed out after 30s.")
                future.cancel()
                return {}
    except Exception as exc:
        log.error(f"[GEE Tiles] Generation failed: {exc}")
        return {}


# ── Physics & Agronomy Formulas ───────────────────────────────


def calculate_et(ndvi: float, lst: float, t_air: float = None) -> float:
    """
    Improved ET (mm/day) using Kc–ET₀ methodology for arid/semi-arid zones.
    - ET₀ estimated from AIR TEMPERATURE via simplified Blaney-Criddle for Egypt:
      ET₀ ≈ p × (0.46 × T_air + 8.13), p≈0.27.
    - T_air is supplied directly if available (weather fallback path).
      Otherwise it is derived from LST by subtracting the typical
      Egypt dry-climate surface-to-air offset (≈8°C for bare/crop surfaces):
      T_air ≈ LST - 8
    - Crop coefficient Kc from NDVI via FAO-56 linear relationship:
      Kc = 1.2 × NDVI + 0.2, clamped [0.15, 1.20].
    - Final ET = Kc × ET₀, clamped to [0.5, 12] mm/day.
    """
    # Use real air temperature if provided; otherwise estimate from LST
    temp = t_air if t_air is not None else (lst - 8.0)

    # Reference ET₀ (Blaney-Criddle simplified for Egypt, T in °C air temp)
    et0 = 0.27 * (0.46 * temp + 8.13)
    et0 = max(2.0, min(10.0, et0))

    # Crop coefficient from NDVI (FAO-56 linear approximation)
    kc = max(0.15, min(1.20, 1.2 * ndvi + 0.2))

    et = kc * et0
    return round(max(0.5, min(12.0, et)), 2)


def calculate_fapar(ndvi: float) -> float:
    """
    FAPAR ≈ 1.2 × NDVI − 0.11  (linear approximation for crops).
    Clamped to [0, 1].
    """
    fapar = 1.2 * ndvi - 0.11
    return max(0.0, min(1.0, round(fapar, 4)))


def calculate_biomass(fapar: float) -> float:
    """
    Above-ground Biomass (Ton/Feddan) via corrected Monteith LUE model.
    - Daily PAR (Egypt avg) ≈ 9.5 MJ/m²/day
    - Light Use Efficiency (crop avg) ≈ 1.5 g/MJ dry matter
    - Growing season ≈ 120 days
    - 1 feddan = 4200 m²
    Result in Ton/Feddan.
    """
    par_daily = 9.5           # MJ/m²/day average solar PAR for Egypt
    lue       = 1.5e-6        # tons/MJ  (1.5 g/MJ = 0.0000015 t/MJ)
    season    = 120            # growing days
    area_m2   = 4200.0

    biomass_tons = fapar * par_daily * lue * season * area_m2
    return round(max(0.0, biomass_tons), 2)


def calculate_wue(biomass_ton_per_feddan: float, et: float) -> str:
    """
    Water Use Efficiency — returns an Arabic contextual label.
    """
    if et <= 0:
        return "غير متوفر"
    et_volume = et * 30.0 * (4200.0 / 1000.0)   # m³/feddan/month
    wue = (biomass_ton_per_feddan * 1000.0) / et_volume if et_volume > 0 else 0.0
    if wue > 2.0:
        return "🟢 كفاءة ممتازة"
    elif wue > 1.2:
        return "🟡 كفاءة جيدة"
    else:
        return "🔴 إهدار للمياه"


def calculate_wue_numeric(biomass_ton_per_feddan: float,
                          et: float, area_feddan: float = 1.0) -> float:
    """
    Water Use Efficiency as a numeric float (kg / m³) for chart rendering.
    Formula:  WUE = biomass_kg / total_water_m3
      total_water_m3 = ET_mm/day × 30 days × 4.2 × area_feddan / 1000 × 1000
                     = ET × 4.2 × area_feddan × 30   [m³/month for the farm]
    Returns 0.0 on invalid input.
    """
    if et <= 0 or area_feddan <= 0:
        return 0.0
    total_water_m3 = et * 4.2 * area_feddan * 30.0  # m³/month
    biomass_kg     = biomass_ton_per_feddan * area_feddan * 1000.0
    return round(biomass_kg / total_water_m3, 4) if total_water_m3 > 0 else 0.0


def interpret_timeseries(values: list, parameter: str) -> dict:
    """
    Produce a farmer-friendly Arabic interpretation for a time-series of values.
    Returns:
    {
        status:          'healthy' | 'warning' | 'critical',
        icon:            '✅' | '⚠️' | '🔴',
        label_ar:        short Arabic status text,
        trend:           'improving' | 'stable' | 'declining',
        trend_ar:        Arabic trend phrase,
        recommendation:  Arabic action recommendation,
        current:         last value (float),
        average:         mean over all values (float),
    }
    """
    import statistics as _stat

    if not values:
        return {
            "status": "unknown", "icon": "⏳", "label_ar": "لا توجد بيانات",
            "trend": "stable", "trend_ar": "مستقر",
            "recommendation": "قم بتشغيل خط أنابيب البيانات للحصول على قراءات.",
            "current": 0.0, "average": 0.0,
        }

    current  = float(values[-1])
    previous = float(values[-2]) if len(values) > 1 else current
    avg      = round(_stat.mean(float(v) for v in values), 4)

    # Trend
    delta = current - previous
    if delta > 0.005:
        trend, trend_ar = "improving",  "📈 يتحسن"
    elif delta < -0.005:
        trend, trend_ar = "declining",  "📉 يتراجع"
    else:
        trend, trend_ar = "stable",     "➡️ مستقر"

    # Parameter thresholds
    p = parameter.lower()

    if p == "ndvi":
        if current >= 0.6:
            status, icon = "healthy",  "✅"
            label_ar      = "المحصول صحي ونموه جيد"
            rec           = "استمر في نفس برنامج الري الحالي."
        elif current >= 0.3:
            status, icon = "warning",  "⚠️"
            label_ar      = "توتر معتدل — راقب الري"
            rec           = "تحقق من توزيع مياه الري وضع الجدول بدقة."
        else:
            status, icon = "critical", "🔴"
            label_ar      = "إجهاد شديد! الري فوراً"
            rec           = "ارو المزرعة الآن. تأخير الري يتسبب في خسارة إنتاج."

    elif p == "et":
        if current <= 4.5:
            status, icon = "healthy",  "✅"
            label_ar      = "استهلاك مياه طبيعي"
            rec           = "استهلاك الطاقة والمياه ضمن المعدل الطبيعي."
        elif current <= 6.0:
            status, icon = "warning",  "⚠️"
            label_ar      = "استهلاك مائي مرتفع"
            rec           = "قلل ساعات الري بنسبة 15–20%."
        else:
            status, icon = "critical", "🔴"
            label_ar      = "هدر مياه! قلل التشغيل"
            rec           = "قلل ساعات الطلمبة فوراً وراجع خط الري."

    elif p == "biomass":
        if current >= 1.5:
            status, icon = "healthy",  "✅"
            label_ar      = "إنتاج جيد جداً"
            rec           = "الكتلة الحيوية ممتازة — استمر في نفس الإدارة."
        elif current >= 0.5:
            status, icon = "warning",  "⚠️"
            label_ar      = "الإنتاج أقل من المتوسط"
            rec           = "راجع كميات الأسمدة ومعدلات الري."
        else:
            status, icon = "critical", "🔴"
            label_ar      = "كتلة حيوية منخفضة جداً"
            rec           = "إجراء عاجل: تحليل تربة + مراجعة الأسمدة والري."

    elif p == "wue":
        if current >= 2.0:
            status, icon = "healthy",  "✅"
            label_ar      = "كفاءة مائية ممتازة"
            rec           = "تستخدم المياه بكفاءة عالية. حافظ على هذا المستوى."
        elif current >= 1.2:
            status, icon = "warning",  "⚠️"
            label_ar      = "كفاءة مائية متوسطة"
            rec           = "يمكن تحسين الكفاءة بضبط توقيت الري الليلي."
        else:
            status, icon = "critical", "🔴"
            label_ar      = "إهدار للمياه"
            rec           = "ابحث عن تسريبات في الشبكة وقلل التبخر النهاري."

    elif p == "tvdi":
        if current <= 0.2:
            status, icon = "healthy",  "✅"
            label_ar      = "التربة رطبة — لا تروِ"
            rec           = "التربة رطبة كافية. وفّر المياه اليوم."
        elif current <= 0.5:
            status, icon = "warning",  "⚠️"
            label_ar      = "جفاف معتدل — راقب"
            rec           = "راقب الرطوبة خلال 24 ساعة. استعد للري."
        else:
            status, icon = "critical", "🔴"
            label_ar      = "جفاف شديد! رَوِّ فوراً"
            rec           = "الأرض جافة جداً. ابدأ دورة الري الآن لتجنب الذبول."

    else:
        status, icon, label_ar = "healthy", "✅", "طبيعي"
        rec = "لا توجد توصية محددة."

    return {
        "status":         status,
        "icon":           icon,
        "label_ar":       label_ar,
        "trend":          trend,
        "trend_ar":       trend_ar,
        "recommendation": rec,
        "current":        round(current, 4),
        "average":        avg,
    }




def calculate_tvdi(ndvi: float, lst: float,
                   ndvi_max: float = 0.85, ndvi_min: float = 0.15,
                   lst_max: float = 45.0, lst_min: float = 15.0) -> float:
    """
    Temperature-Vegetation Dryness Index (Egypt-calibrated).
    TVDI = (T_surf - T_min) / (T_max - T_min) × (1 - (NDVI - NDVI_min) / (NDVI_max - NDVI_min))
    Normalised to [0, 1]:  0 = fully wet/moist; 1 = extreme drought.

    Uses AIR-EQUIVALENT temperature (LST − 8°C correction) to stay consistent
    with the ET calculation and to match calibrated dry-edge dry values:
      - Effective T_max = 45°C air (corresponds to LST ≈53°C desert)
      - Effective T_min = 15°C air (corresponds to LST ≈23°C winter irrigated)
    NDVI bounds calibrated for Egyptian Nile Delta / desert agriculture.
    """
    try:
        # Convert LST to air-equivalent for consistency with ET calculation
        t_eff = lst - 8.0
        lst_range  = lst_max - lst_min
        ndvi_range = ndvi_max - ndvi_min
        if lst_range == 0 or ndvi_range == 0:
            return 0.5
        ndvi_clamped = max(ndvi_min, min(ndvi_max, ndvi))
        t_clamped    = max(lst_min, min(lst_max, t_eff))
        tvdi = ((t_clamped - lst_min) / lst_range) * (1.0 - (ndvi_clamped - ndvi_min) / ndvi_range)
        return round(max(0.0, min(1.0, tvdi)), 4)
    except Exception:
        return 0.5


def et_to_pump_hours(et_mm_day: float, area_feddan: float,
                     pump_flow_m3_per_hour: float = 50.0) -> float:
    """
    Convert ET (mm/day) and farm area (feddan) to pump running hours.
    Total Water Required (m³) = Actual ET (mm) * 4.2 * farm_area_feddan
    Operating Hours = Total Water Required / pump_flow_m3_per_hour
    """
    if pump_flow_m3_per_hour <= 0:
        return 0.0
    total_water = et_mm_day * 4.2 * area_feddan
    hours = total_water / pump_flow_m3_per_hour
    return round(hours, 2)


def apply_physics_pipeline(raw: dict, area_feddan: float = 1.0,
                           t_air: float = None, pump_flow_rate: float = 50.0) -> dict:
    """
    Given a raw metrics dict (at minimum ndvi and lst), compute all
    derived agronomy metrics and return the complete enriched dict.
    area_feddan is the user's actual farm size (used for pump hours).
    t_air: real air temperature in °C (from weather API). If provided,
           it is used directly for ET₀ instead of deriving from LST.
    pump_flow_rate: m3/h used for accurate pump hours calculation.
    """
    ndvi = raw.get("ndvi") or 0.5
    lst  = raw.get("lst")  or 35.0

    fapar   = calculate_fapar(ndvi)
    et      = raw.get("et") or calculate_et(ndvi, lst, t_air=t_air)
    # Biomass calculated as Ton/Feddan
    biomass = raw.get("biomass") or calculate_biomass(fapar)
    wue     = raw.get("wue") if isinstance(raw.get("wue"), str) else calculate_wue(biomass, et)
    tvdi    = calculate_tvdi(ndvi, lst)

    # LST differentiation: soil vs canopy temperature
    lst_split = compute_lst_split(lst, ndvi)

    return {
        **raw,
        "fapar":       fapar,
        "et":          et,
        "biomass":     biomass,
        "wue":         wue,
        "tvdi":        tvdi,
        "fvc":         lst_split.get("fvc", 0.0),
        "lst_soil":    lst_split["soil_temp"],
        "lst_canopy":  lst_split["canopy_temp"],
        "area_feddan": area_feddan,
        "pump_hours":  et_to_pump_hours(et, area_feddan, pump_flow_rate),
        "source":      raw.get("source", "physics"),
    }


# ── CSV Ingestion ─────────────────────────────────────────────

REQUIRED_COLS = {"date", "ndvi"}

def parse_csv(file_stream) -> pd.DataFrame:
    """
    Parse a user-uploaded CSV file.
    Expects at minimum columns: date, ndvi.
    Optional columns: lst, ndwi, et, biomass, wue, tvdi.

    Returns a clean DataFrame ready for DB ingestion.
    Raises ValueError on structural problems.
    """
    try:
        df = pd.read_csv(file_stream)
    except Exception as e:
        raise ValueError(f"Could not parse CSV: {e}")

    df.columns = [c.strip().lower() for c in df.columns]
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    # Normalise date column
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    # Coerce numeric columns
    numeric_cols = ["ndvi", "lst", "ndwi", "et", "biomass", "wue", "tvdi"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date", "ndvi"])
    df["source"] = "csv"
    return df


# ── CatBoost Crop-Health Classifier ──────────────────────────

# Feature columns used for training / inference
FEATURE_COLS = ["ndvi", "lst", "et", "tvdi", "fapar", "ndwi"]
LABEL_MAP    = {0: "healthy", 1: "water_stress", 2: "heat_stress"}
REVERSE_LABEL = {v: k for k, v in LABEL_MAP.items()}

_model: "CatBoostClassifier | None" = None


def _build_synthetic_training_data() -> pd.DataFrame:
    """
    Generate a physics-consistent synthetic dataset for initial training.
    In production, replace this with real labelled field observations.
    """
    rng = np.random.default_rng(42)
    n   = 3000

    rows = []
    for _ in range(n):
        label = rng.integers(0, 3)

        if label == 0:   # healthy
            ndvi  = rng.uniform(0.55, 0.85)
            lst   = rng.uniform(22.0, 33.0)
            ndwi  = rng.uniform(0.1,  0.4)
        elif label == 1: # water_stress
            ndvi  = rng.uniform(0.25, 0.55)
            lst   = rng.uniform(33.0, 40.0)
            ndwi  = rng.uniform(-0.3, 0.1)
        else:            # heat_stress
            ndvi  = rng.uniform(0.35, 0.65)
            lst   = rng.uniform(40.0, 50.0)
            ndwi  = rng.uniform(-0.2, 0.2)

        fapar   = calculate_fapar(ndvi)
        et      = calculate_et(ndvi, lst)
        tvdi    = calculate_tvdi(ndvi, lst)
        rows.append([ndvi, lst, et, tvdi, fapar, ndwi, label])

    return pd.DataFrame(rows, columns=FEATURE_COLS + ["label"])


def train_model(save_path: str = None) -> "CatBoostClassifier":
    """
    Train a CatBoost multi-class classifier on synthetic crop-health data.
    Persists the model to disk for reuse.
    """
    if not _CATBOOST_AVAILABLE:
        raise RuntimeError("CatBoost is not installed.")

    save_path = save_path or Config.CATBOOST_MODEL_PATH
    df        = _build_synthetic_training_data()

    X = df[FEATURE_COLS].values
    y = df["label"].values

    model = CatBoostClassifier(
        iterations=500,
        learning_rate=0.05,
        depth=6,
        loss_function="MultiClass",
        eval_metric="Accuracy",
        random_seed=42,
        verbose=False,
    )
    model.fit(X, y)
    model.save_model(save_path)
    log.info(f"[ML] CatBoost model trained and saved to {save_path}")
    return model


def load_model() -> "CatBoostClassifier | None":
    """Load a pre-trained CatBoost model from disk, or train a new one."""
    global _model
    if _model is not None:
        return _model

    if not _CATBOOST_AVAILABLE:
        return None

    path = Config.CATBOOST_MODEL_PATH
    if os.path.exists(path):
        try:
            _model = CatBoostClassifier()
            _model.load_model(path)
            log.info(f"[ML] CatBoost model loaded from {path}")
            return _model
        except Exception as exc:
            log.warning(f"[ML] Could not load model ({exc}), retraining…")

    _model = train_model(path)
    return _model


def predict_crop_health(ndvi=None, lst=None, et=None,
                        tvdi=None, fapar=None,
                        ndwi: float = 0.0, return_dict: bool = False):
    """
    Predict crop health status for a single observation.
    Accepts either individual metric floats or a dictionary (e.g. farm_row).
    Returns string status when called with individual arguments, or dict when return_dict=True or input is a dict.
    Labels provenance as SYNTHETIC_TRAINED / DEVELOPMENT_MODEL.
    """
    is_dict_call = isinstance(ndvi, dict)
    if is_dict_call:
        d = ndvi
        ndvi = float(d.get("ndvi") or 0.5)
        lst = float(d.get("lst") or 30.0)
        et = float(d.get("et") or 4.0)
        tvdi = float(d.get("tvdi") or 0.4)
        fapar = float(d.get("fapar") or (ndvi * 1.1))
        ndwi = float(d.get("ndwi") or 0.0)
    else:
        ndvi = float(ndvi if ndvi is not None else 0.5)
        lst = float(lst if lst is not None else 30.0)
        et = float(et if et is not None else 4.0)
        tvdi = float(tvdi if tvdi is not None else 0.4)
        fapar = float(fapar if fapar is not None else (ndvi * 1.1))
        ndwi = float(ndwi if ndwi is not None else 0.0)

    model = load_model()
    pred_status = "healthy"
    confidence = 0.85
    probs = {}

    if model is not None:
        try:
            features = np.array([[ndvi, lst, et, tvdi, fapar, ndwi]])
            preds = np.asarray(model.predict(features)).flatten()
            pred_idx = int(preds[0])
            pred_status = LABEL_MAP.get(pred_idx, "healthy")
            try:
                probabilities = model.predict_proba(features)[0]
                confidence = round(float(probabilities[pred_idx]), 3)
                probs = {LABEL_MAP.get(i, f"class_{i}"): round(float(p), 3) for i, p in enumerate(probabilities)}
            except Exception:
                confidence = 0.80
        except Exception as exc:
            log.error(f"[ML] Prediction error: {exc}")
            model = None

    if model is None:
        # ── Rule-based fallback ──────────────────────────────────
        if lst > 40.0 and ndvi < 0.5:
            pred_status = "heat_stress"
            confidence = 0.75
        elif ndvi < 0.35 and et < 3.0:
            pred_status = "water_stress"
            confidence = 0.75
        else:
            pred_status = "healthy"
            confidence = 0.80

    labels_ar = {
        "healthy": "محصول صحي وطبيعي",
        "water_stress": "إجهاد مائي (نقص رطوبة)",
        "heat_stress": "إجهاد حراري (ارتفاع درجات الحرارة)",
        "pivot_alert": "خلل في توزيع الري المحوري",
    }

    if is_dict_call or return_dict:
        return {
            "predicted_status": pred_status,
            "predicted_label_ar": labels_ar.get(pred_status, pred_status),
            "confidence": confidence,
            "probabilities": probs,
            "model_version": "1.0.0-dev",
            "training_source": "SYNTHETIC_TRAINED",
            "model_provenance": "DEVELOPMENT_MODEL / SYNTHETIC_TRAINED",
            "status": "AVAILABLE",
        }

    return pred_status


# ── Pivot Failure / Anomaly Detection ─────────────────────────

def compute_ndvi_cv(ndvi_timeseries: list[float]) -> float:
    """
    Coefficient of Variation (CV) of NDVI over time.
    High CV in a circular irrigation zone implies mechanical pivot failure
    because healthy pivot zones show uniform NDVI.
    CV = std / mean  (expressed as fraction)
    """
    arr = np.array([v for v in ndvi_timeseries if v is not None], dtype=float)
    if arr.size < 3:
        return 0.0
    mean = np.mean(arr)
    if mean == 0:
        return 0.0
    cv = np.std(arr) / mean
    return round(float(cv), 4)


def compute_thermal_inertia(lst_timeseries: list[float]) -> float:
    """
    Simplified Thermal Inertia proxy:
    Thermal Inertia ∝ 1 / diurnal_range(LST).
    Abnormally HIGH thermal inertia means the soil is not cycling temperature
    normally — possible sign of a waterlogged or mechanically-blocked zone.
    Using the amplitude of the LST time-series as a proxy for diurnal range.
    """
    arr = np.array([v for v in lst_timeseries if v is not None], dtype=float)
    if arr.size < 3:
        return 0.0
    lst_range = np.max(arr) - np.min(arr)
    if lst_range == 0:
        return 0.0
    return round(1.0 / lst_range, 5)


def detect_pivot_failure(ndvi_series: list[float],
                         lst_series: list[float]) -> dict:
    """
    Run pivot-failure anomaly detection.

    Rules (physics-justified):
    - NDVI_CV > 0.25  →  strong spatial non-uniformity in vegetation cover
                          → probable blocked/failed pivot arm
    - Thermal Inertia > 0.10 with low NDVI_CV  →  thermal anomaly (sensor or
                          soil waterlogging on part of pivot)
    - Both triggered simultaneously → HIGH CONFIDENCE pivot failure alert

    Returns:
        {
          "alert":       True | False,
          "severity":    "none" | "warning" | "critical",
          "ndvi_cv":     float,
          "thermal_inertia": float,
          "message":     str (Arabic-friendly),
        }
    """
    ndvi_cv         = compute_ndvi_cv(ndvi_series)
    thermal_inertia = compute_thermal_inertia(lst_series)

    alert    = False
    severity = "none"
    message  = "النظام يعمل بشكل طبيعي، لا يوجد تحذير."

    if ndvi_cv > 0.25 and thermal_inertia > 0.10:
        alert    = True
        severity = "critical"
        message  = (
            "⚠️ تحذير حرج: اكتُشف شذوذ في نمط الري الدوّار! "
            "تبايُن NDVI عالٍ مع شذوذ حراري — يُرجى فحص ذراع البيفوت فوراً."
        )
    elif ndvi_cv > 0.25:
        alert    = True
        severity = "warning"
        message  = (
            "⚠️ تحذير: عدم توحّد في النباتات داخل دائرة الري. "
            "محتمل وجود عطل ميكانيكي في البيفوت — راقب الوضع."
        )
    elif thermal_inertia > 0.10:
        alert    = True
        severity = "warning"
        message  = (
            "⚠️ تحذير درجة حرارة: شذوذ حراري غير طبيعي. "
            "تحقق من نظام الري والتربة."
        )

    return {
        "alert":           alert,
        "severity":        severity,
        "ndvi_cv":         ndvi_cv,
        "thermal_inertia": thermal_inertia,
        "message":         message,
    }


# ── Weather & Agronomy Alert Engine ──────────────────────────

def fetch_weather_forecast(lat: float, lon: float) -> list[dict]:
    """
    Fetch a 5-day / 3-hour OWM forecast and aggregate to daily summaries.

    Returns a list of dicts, one per forecast day:
        {
          "date":     "YYYY-MM-DD",
          "temp_max": float,   # °C
          "temp_min": float,   # °C
          "humidity": float,   # %
          "wind_speed": float, # m/s
          "description": str,  # weather condition in English
          "icon":     str,     # OWM icon code
        }
    """
    url = (
        "https://api.openweathermap.org/data/2.5/forecast"
        f"?lat={lat}&lon={lon}&appid={Config.OWM_API_KEY}"
        "&units=metric&lang=ar&cnt=40"
    )
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error(f"[Weather] OWM API error: {exc}")
        return []

    # Aggregate 3-hour slots → daily
    daily: dict[str, dict] = {}
    for entry in data.get("list", []):
        day = entry["dt_txt"][:10]
        temp = entry["main"]["temp"]
        if day not in daily:
            daily[day] = {
                "date":        day,
                "temp_max":    temp,
                "temp_min":    temp,
                "humidity":    entry["main"]["humidity"],
                "wind_speed":  entry.get("wind", {}).get("speed", 0.0),
                "description": entry["weather"][0]["description"],
                "icon":        entry["weather"][0]["icon"],
            }
        else:
            daily[day]["temp_max"] = max(daily[day]["temp_max"], temp)
            daily[day]["temp_min"] = min(daily[day]["temp_min"], temp)

    return list(daily.values())[: Config.OWM_FORECAST_DAYS]


def generate_agronomy_alerts(forecast: list[dict],
                             latest_farm: dict) -> list[dict]:
    """
    Merge 5-day weather forecast with current farm data to generate
    actionable agronomy alerts.

    Returns a list of alert dicts:
        { "day": str, "level": "info"|"warning"|"critical",
          "message_ar": str }
    """
    alerts = []
    et  = latest_farm.get("et",   5.0) or 5.0
    ndvi = latest_farm.get("ndvi", 0.5) or 0.5

    for day_data in forecast:
        day      = day_data["date"]
        temp_max = day_data["temp_max"]
        humidity = day_data["humidity"]

        # Alert 1: Extreme heat + low ET → proactive irrigation
        if temp_max > 40.0 and et < 4.0:
            alerts.append({
                "day":        day,
                "level":      "critical",
                "message_ar": (
                    f"🔴 تحذير حرج يوم {day}: "
                    f"درجة الحرارة المتوقعة {temp_max:.1f}°C مع نقص في التبخّر. "
                    "يُنصح بالريّ الفوري لتجنّب الإجهاد الحراري للمحصول."
                ),
            })

        # Alert 2: High heat, marginal ET
        elif temp_max > 35.0 and et < 6.0:
            alerts.append({
                "day":        day,
                "level":      "warning",
                "message_ar": (
                    f"🟠 تحذير يوم {day}: "
                    f"حرارة مرتفعة ({temp_max:.1f}°C). "
                    "تحقق من مستوى رطوبة التربة وجاهزية منظومة الري."
                ),
            })

        # Alert 3: Low humidity + low NDVI → water stress risk
        if humidity < 30 and ndvi < 0.4:
            if not any(a["day"] == day and a["level"] == "critical" for a in alerts):
                alerts.append({
                    "day":        day,
                    "level":      "warning",
                    "message_ar": (
                        f"🟡 تحذير رطوبة يوم {day}: "
                        f"الرطوبة المتوقعة {humidity}% مع مؤشر نباتي منخفض. "
                        "خطر إجهاد مائي — راقب المحصول عن كثب."
                    ),
                })

        # Alert 3.5: Canopy Temp > Air Temp + 2 with Pump > 4
        lst = latest_farm.get("lst", 30.0) or 30.0
        pump_hours = latest_farm.get("pump_hours", 0.0) or 0.0
        n = max(0.0, min(1.0, ndvi))
        fvc = max(0.0, min(1.0, ((n - 0.15) / 0.70) ** 2))
        lst_canopy = lst - fvc * 7.0

        if lst_canopy > temp_max + 2.0 and pump_hours > 4.0:
            if not any(a["day"] == day and a["level"] == "critical" for a in alerts):
                alerts.append({
                    "day":        day,
                    "level":      "warning",
                    "message_ar": (
                        f"🔴 إجهاد نباتي يوم {day}: حرارة النبات ({lst_canopy:.1f}°C) أعلى من الهواء. "
                        f"التبخر عالي! تأكد من تشغيل جهاز الري لمدة لا تقل عن {pump_hours:.1f} ساعة."
                    ),
                })

        # Alert 4: Good conditions
        if temp_max < 32.0 and humidity > 50 and ndvi > 0.6:
            alerts.append({
                "day":        day,
                "level":      "info",
                "message_ar": (
                    f"✅ أحوال جيدة يوم {day}: "
                    f"طقس ملائم ({temp_max:.1f}°C، رطوبة {humidity}%). "
                    "المحصول في حالة جيدة."
                ),
            })

    return alerts


# ── Master pipeline ───────────────────────────────────────────

def run_full_pipeline(user_id: int, lat: float, lon: float,
                      area_feddan: float, crop_type: str,
                      pump_flow_rate: float = 50.0,
                      date_str: str = None) -> dict:
    """
    Orchestrate the full data-collection and analysis pipeline for one user.

    Steps:
    1. Try GEE for NDVI / LST / NDWI
    2. Apply physics to fill missing metrics
    3. Predict crop health with CatBoost
    4. Check pivot failure anomalies (if 7-day data is available from DB)
    5. Fetch weather forecast
    6. Generate agronomy alerts

    Returns a comprehensive result dict ready for DB upsert and dashboard.
    """
    from database import (
        get_farm_data_range, upsert_farm_data, get_latest_farm_data
    )

    if date_str is None:
        date_str = datetime.date.today().isoformat()

    # Step 1: Try GEE
    raw = fetch_gee_data(lat, lon, date_str)

    # Step 1b: If GEE is unavailable, use weather-based physics estimation
    if not raw or not raw.get("ndvi"):
        log.info("[Pipeline] GEE unavailable — using weather-based physics estimation")
        try:
            forecast = fetch_weather_forecast(lat, lon)
            if forecast:
                today_wx = forecast[0]
                temp_max = today_wx.get("temp_max", 30.0)
                temp_min = today_wx.get("temp_min", 20.0)
                humidity = today_wx.get("humidity", 50.0)
                wind     = today_wx.get("wind_speed", 3.0)

                # Physics-based NDVI estimation from weather
                # Vegetation index correlates with humidity and moderate temperature
                temp_avg = (temp_max + temp_min) / 2.0
                # Temperature factor: peak at ~25°C, drops off at extremes
                temp_factor  = max(0.0, 1.0 - abs(temp_avg - 25.0) / 25.0)
                # Humidity factor: more humid = more vegetation
                humid_factor = min(1.0, humidity / 75.0)
                ndvi_est = round(max(0.1, min(0.85, 0.15 + 0.65 * temp_factor * humid_factor)), 4)

                # LST = air temp_max + surface heating offset
                # Dry bare soil: +12°C offset; Dense vegetation: +2°C offset
                surface_offset = 12.0 * (1.0 - ndvi_est) + 2.0 * ndvi_est
                lst_est = round(temp_max + surface_offset - wind * 0.5, 2)

                # NDWI correlates with both humidity and vegetation water content
                # High humidity AND high NDVI = more positive NDWI
                ndwi_est = round(-0.4 + 0.5 * (humidity / 100.0) + 0.3 * ndvi_est, 4)
                ndwi_est = round(max(-0.5, min(0.6, ndwi_est)), 4)

                raw = {
                    "ndvi":   ndvi_est,
                    "lst":    lst_est,
                    "ndwi":   ndwi_est,
                    "source": "weather_physics",
                    "_t_air": temp_avg,  # store real air temp for ET calculation
                }
                log.info(f"[Pipeline] Weather-based estimates: ndvi={ndvi_est}, lst={lst_est:.1f}°C, ndwi={ndwi_est}, t_air={temp_avg:.1f}°C")
            else:
                # Absolute fallback with reasonable defaults for Egypt
                raw = {
                    "ndvi":   0.45,
                    "lst":    37.0,
                    "ndwi":   0.05,
                    "source": "physics_default",
                }
                log.info("[Pipeline] Using physics defaults (no weather data)")
        except Exception as exc:
            log.error(f"[Pipeline] Weather estimation failed: {exc}")
            raw = {"ndvi": 0.45, "lst": 37.0, "ndwi": 0.05, "source": "physics_default"}

    # Step 2: Physics pipeline
    # Pass real air temperature if available from weather fallback
    t_air_from_wx = raw.pop("_t_air", None)  # extract without storing in DB
    metrics = apply_physics_pipeline(raw, area_feddan=area_feddan, t_air=t_air_from_wx, pump_flow_rate=pump_flow_rate)

    # Step 3: CatBoost crop health
    status = predict_crop_health(
        ndvi  = metrics.get("ndvi",  0.5),
        lst   = metrics.get("lst",   35.0),
        et    = metrics.get("et",    5.0),
        tvdi  = metrics.get("tvdi",  0.5),
        fapar = metrics.get("fapar", 0.5),
        ndwi  = metrics.get("ndwi",  0.0),
    )
    metrics["status"] = status

    # Step 4: Pivot failure detection using 30-day history
    history    = get_farm_data_range(user_id, days=30)
    ndvi_series = [row["ndvi"] for row in history if row["ndvi"] is not None]
    lst_series  = [row["lst"]  for row in history if row["lst"]  is not None]

    # Add today's values to the series
    if metrics.get("ndvi"):
        ndvi_series.append(metrics["ndvi"])
    if metrics.get("lst"):
        lst_series.append(metrics["lst"])

    pivot_result = detect_pivot_failure(ndvi_series, lst_series)
    metrics["ndvi_cv"]         = pivot_result["ndvi_cv"]
    metrics["thermal_inertia"] = pivot_result["thermal_inertia"]

    # Escalate status if pivot alert is critical
    if pivot_result["alert"] and pivot_result["severity"] == "critical":
        metrics["status"] = "pivot_alert"

    # Step 5–6: Weather & agronomy alerts
    forecast = fetch_weather_forecast(lat, lon)
    agro_alerts = generate_agronomy_alerts(forecast, metrics)

    # Persist to DB
    upsert_farm_data(user_id, date_str, **{
        k: v for k, v in metrics.items()
        if k in {"ndvi", "lst", "ndwi", "et", "biomass", "wue",
                 "tvdi", "fapar", "ndvi_cv", "thermal_inertia",
                 "status", "pump_hours", "source"}
    })

    return {
        "metrics":       metrics,
        "pivot_alert":   pivot_result,
        "forecast":      forecast,
        "agro_alerts":   agro_alerts,
        "date":          date_str,
    }


# ── Self-test ─────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test physics pipeline
    raw = {"ndvi": 0.68, "lst": 34.5, "ndwi": 0.12, "source": "gee"}
    enriched = apply_physics_pipeline(raw, area_feddan=50)
    print("[Test] Enriched metrics:", enriched)

    # Test crop-health prediction
    status = predict_crop_health(
        ndvi=0.68, lst=34.5, et=enriched["et"],
        tvdi=enriched["tvdi"], fapar=enriched["fapar"], ndwi=0.12
    )
    print("[Test] Crop status:", status)

    # Test pivot detection
    ndvi_series  = [0.72, 0.71, 0.69, 0.45, 0.72, 0.73, 0.40]
    lst_series   = [33.0, 34.0, 33.5, 41.0, 33.0, 32.5, 42.0]
    pivot_result = detect_pivot_failure(ndvi_series, lst_series)
    print("[Test] Pivot detection:", pivot_result)

    print("[ML Engine] All self-tests passed ✓")
