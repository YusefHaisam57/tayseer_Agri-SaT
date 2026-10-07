# ============================================================
# gee_carbon_sat.py — Tayseer Carbon-Sat V2.0
#
# Target:  Poultry & Livestock Farm — Ismailia / Port-Said Road
# GEE:     Sentinel-2 SR + Landsat 9 Thermal
# Metrics: SOM, NCS, Barn Heat Plumes, CH4 Hotspots
#
# Modes:
#   • GEE Live   — requires initialized earthengine-api
#   • Demo Mode  — realistic simulation when GEE not configured
#
# Author:  Tayseer AgriSaT Engineering Team
# ============================================================

import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# ── Real farm polygon (Ismailia / Port-Said Road, Sweet Canal) ──
FARM_POLYGON_ISMAILIA = [
    [32.300768, 31.196686],
    [32.302485, 31.196705],
    [32.301992, 31.209869],
    [32.300399, 31.209883],
    [32.300761, 31.196651],
]

FARM_START_DATE = "2022-06-01"
FARM_END_DATE   = "2024-04-20"
FARM_CRS        = "EPSG:32636"   # UTM Zone 36N

# ── GEE availability ─────────────────────────────────────────
try:
    import ee
    _EE_AVAILABLE = True
except ImportError:
    _EE_AVAILABLE = False
    log.warning("[CarbonSat] earthengine-api not installed — demo mode active.")


class CarbonSatProcessor:
    """
    GEE Carbon Monitoring Processor for Tayseer.

    Calculates four carbon/emission layers at 10×10 m resolution:
      1. SOM  — Soil Organic Matter (mg/kg)
      2. NCS  — Net Carbon Supply   (g C/m²/year)
      3. Barn Heat Plumes            (°C LST)
      4. CH4  — Methane Hotspots    (relative index)

    Falls back to demo mode automatically when GEE is unavailable.
    """

    def __init__(self):
        self.demo_mode = True

        if not _EE_AVAILABLE:
            log.warning("[CarbonSat] Running in demo mode (earthengine-api missing).")
            return

        try:
            ee.Initialize()
            self.demo_mode = False
            log.info("[CarbonSat] ✅ GEE initialised successfully.")
        except Exception as exc:
            log.warning(f"[CarbonSat] GEE init failed — demo mode: {exc}")

    # ──────────────────────────────────────────────────────────
    # FULL CARBON MATRIX (all 4 layers in one GEE call)
    # ──────────────────────────────────────────────────────────

    def calculate_carbon_matrix(
        self,
        user_id: int,
        farm_geometry: dict = None,
        start_date: str = FARM_START_DATE,
        end_date: str = FARM_END_DATE,
    ) -> dict:
        """
        Compute all four carbon layers for the farm polygon.

        Args:
            user_id:       Logged-in user ID (for DB persistence).
            farm_geometry: GeoJSON Polygon. Defaults to Ismailia farm.
            start_date:    Analysis window start (YYYY-MM-DD).
            end_date:      Analysis window end   (YYYY-MM-DD).

        Returns:
            dict with keys: som, ncs, barn_heat, ch4, area_ha, demo_mode.
        """
        if self.demo_mode:
            return self._demo_carbon_matrix()

        try:
            coords = (
                farm_geometry["coordinates"][0]
                if farm_geometry
                else FARM_POLYGON_ISMAILIA
            )
            roi = ee.Geometry.Polygon(coords)

            # ── Sentinel-2 SR ─────────────────────────────────
            s2_col = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterBounds(roi)
                .filterDate(start_date, end_date)
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
            )
            dummy_s2 = ee.Image.constant([1000, 1000, 1000, 1000, 1000]).rename(
                ["B2", "B4", "B8", "B11", "B12"]
            )
            s2 = ee.Image(
                ee.Algorithms.If(s2_col.size().gt(0), s2_col.median(), dummy_s2)
            ).reproject(crs=FARM_CRS, scale=10).clip(roi)

            bsi = s2.expression(
                "((B11+B4)-(B8+B2))/((B11+B4)+(B8+B2)+0.0001)",
                {"B11": s2.select("B11"), "B4": s2.select("B4"),
                 "B8":  s2.select("B8"),  "B2": s2.select("B2")},
            ).rename("BSI")

            savi = s2.expression(
                "1.5*(B8-B4)/(B8+B4+0.5)",
                {"B8": s2.select("B8"), "B4": s2.select("B4")},
            ).rename("SAVI")

            ndvi = s2.normalizedDifference(["B8", "B4"]).rename("NDVI")
            manure_idx = s2.normalizedDifference(["B11", "B8"]).rename("Manure_Index")

            # ── Landsat 9 (thermal / LST) ─────────────────────
            l8_col = (
                ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
                .filterBounds(roi)
                .filterDate(start_date, end_date)
            )
            dummy_l8 = ee.Image.constant(300 / 0.00341802).rename("ST_B10")
            l8 = ee.Image(
                ee.Algorithms.If(l8_col.size().gt(0), l8_col.median(), dummy_l8)
            )
            lst = (
                l8.select("ST_B10")
                .multiply(0.00341802)
                .add(149.0)
                .subtract(273.15)
                .reproject(crs=FARM_CRS, scale=10)
                .clip(roi)
                .rename("LST")
            )

            # ── Carbon calculations ───────────────────────────
            som_img = savi.divide(bsi.add(1.5)).multiply(12000).rename("SOM_mg_kg")
            npp_proxy = ndvi.multiply(1500)
            resp_img = lst.expression("1.5 * exp(0.05 * LST)", {"LST": lst})
            ncs_img = npp_proxy.subtract(resp_img).rename("NCS")
            heat_img = lst.rename("Barn_Heat_Plume")
            ch4_img = lst.multiply(manure_idx.add(1)).multiply(1.5).rename("CH4_Emissions")

            carbon_matrix = ee.Image([som_img, ncs_img, heat_img, ch4_img]).reproject(
                crs=FARM_CRS, scale=10
            ).clip(roi)

            # ── Reduce to scalar statistics ───────────────────
            stats = carbon_matrix.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=roi,
                scale=10,
                maxPixels=1e9,
            ).getInfo()

            area_ha = float(roi.area().getInfo()) / 10_000

            result = {
                "success":   True,
                "demo_mode": False,
                "som_mg_kg":        round(float(stats.get("SOM_mg_kg") or 0), 2),
                "ncs_gC_m2_year":   round(float(stats.get("NCS")        or 0), 2),
                "barn_heat_celsius": round(float(stats.get("Barn_Heat_Plume") or 0), 2),
                "ch4_index":         round(float(stats.get("CH4_Emissions")   or 0), 4),
                "area_ha":           round(area_ha, 2),
                "carbon_status":     "Sink" if (stats.get("NCS") or 0) > 0 else "Source",
            }

            self._save_all(user_id, result)
            return result

        except Exception as exc:
            log.error(f"[CarbonSat] carbon_matrix error: {exc}")
            res = self._demo_carbon_matrix()
            res["error_detail"] = str(exc)
            return res

    # ──────────────────────────────────────────────────────────
    # LIVESTOCK EMISSIONS (CH4 / NH3 — IPCC Tier 2)
    # ──────────────────────────────────────────────────────────

    def calculate_livestock_emissions(
        self,
        user_id: int,
        paddock_geometry: dict = None,
        date_range_days: int = 30,
    ) -> dict:
        """
        Estimate livestock CH4 and NH3 using Sentinel-2 density proxy.
        Defaults to the real Ismailia farm polygon.
        """
        if self.demo_mode:
            return self._demo_livestock()

        try:
            coords = (
                paddock_geometry["coordinates"][0]
                if paddock_geometry
                else FARM_POLYGON_ISMAILIA
            )
            roi = ee.Geometry.Polygon(coords)

            end_dt   = datetime.utcnow()
            start_dt = end_dt - timedelta(days=date_range_days)

            s2 = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterBounds(roi)
                .filterDate(start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            )

            def _density(img):
                ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
                bright = img.select(["B2", "B3", "B4"]).reduce(ee.Reducer.mean()).rename("B")
                return ndvi.multiply(-1).add(1).multiply(bright).divide(1000).rename("D")

            density_img = s2.map(_density).mean()
            stats = density_img.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=roi, scale=10, maxPixels=1e9
            ).getInfo()

            mean_d    = float(stats.get("D") or 0)
            area_ha   = float(roi.area().getInfo()) / 10_000
            heads     = mean_d * 5 * area_ha
            ch4_daily = (heads * 55) / 365
            nh3_daily = (heads * 18) / 365

            self._save_livestock(user_id, heads, ch4_daily, nh3_daily,
                                 ch4_daily * 365, nh3_daily * 365, mean_d, area_ha)

            return {
                "success": True, "demo_mode": False,
                "estimated_livestock":   round(heads, 2),
                "ch4_kg_per_day":        round(ch4_daily, 3),
                "nh3_kg_per_day":        round(nh3_daily, 3),
                "ch4_kg_per_year":       round(ch4_daily * 365, 2),
                "nh3_kg_per_year":       round(nh3_daily * 365, 2),
                "livestock_density_index": round(mean_d, 4),
                "area_hectares":          round(area_ha, 2),
            }

        except Exception as exc:
            log.error(f"[CarbonSat] livestock error: {exc}")
            res = self._demo_livestock()
            res["error_detail"] = str(exc)
            return res

    # ──────────────────────────────────────────────────────────
    # SOIL CARBON (SOM + NCS)
    # ──────────────────────────────────────────────────────────

    def calculate_soil_carbon(
        self, user_id: int, field_geometry: dict = None
    ) -> dict:
        """Compute SOM and NCS for the farm polygon."""
        if self.demo_mode:
            return self._demo_soil()

        # Delegates to calculate_carbon_matrix for efficiency
        full = self.calculate_carbon_matrix(user_id, field_geometry)
        return {
            "success":    full["success"],
            "demo_mode":  full["demo_mode"],
            "som_mg_kg":  full["som_mg_kg"],
            "ncs_gC_m2_year": full["ncs_gC_m2_year"],
            "total_carbon_sequestered_kg_year": round(
                full["ncs_gC_m2_year"] * full["area_ha"] * 10_000 / 1_000, 2
            ),
            "carbon_status": full["carbon_status"],
            "area_hectares": full["area_ha"],
        }

    # ──────────────────────────────────────────────────────────
    # DB PERSISTENCE (thin wrappers)
    # ──────────────────────────────────────────────────────────

    def _save_all(self, user_id: int, result: dict) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            from database import save_soil_carbon
            save_soil_carbon(
                user_id=user_id, date=today,
                som_mg_kg=result["som_mg_kg"],
                ncs_gC_m2_year=result["ncs_gC_m2_year"],
                total_carbon_sequestered_kg=result["ncs_gC_m2_year"] * result["area_ha"] * 10,
                area_hectares=result["area_ha"],
            )
        except Exception as exc:
            log.warning(f"[CarbonSat] DB save_all failed: {exc}")

    def _save_livestock(self, user_id, heads, ch4d, nh3d, ch4y, nh3y, density, area):
        try:
            from database import save_livestock_emissions
            save_livestock_emissions(
                user_id=user_id,
                date=datetime.utcnow().strftime("%Y-%m-%d"),
                estimated_heads=heads,
                ch4_kg_daily=ch4d, nh3_kg_daily=nh3d,
                ch4_kg_annual=ch4y, nh3_kg_annual=nh3y,
                livestock_density=density, area_hectares=area,
            )
        except Exception as exc:
            log.warning(f"[CarbonSat] DB livestock failed: {exc}")

    # ──────────────────────────────────────────────────────────
    # DEMO FALLBACKS — realistic data for Ismailia poultry farm
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _demo_carbon_matrix() -> dict:
        """Demo data for the four carbon layers."""
        return {
            "success": True, "demo_mode": True,
            # SOM: semi-arid Egyptian soil (low-moderate)
            "som_mg_kg": 4850.0,
            # NCS: slight carbon sink (managed pasture)
            "ncs_gC_m2_year": 185.4,
            # Barn thermal plume (active ventilation)
            "barn_heat_celsius": 34.7,
            # CH4 hotspot index
            "ch4_index": 28.3,
            "area_ha": 18.6,
            "carbon_status": "Sink",
        }

    @staticmethod
    def _demo_livestock() -> dict:
        return {
            "success": True, "demo_mode": True,
            "estimated_livestock": 220.0,
            "ch4_kg_per_day": 33.15,
            "nh3_kg_per_day": 10.85,
            "ch4_kg_per_year": 12100.0,
            "nh3_kg_per_year": 3960.0,
            "livestock_density_index": 0.62,
            "area_hectares": 18.6,
        }

    @staticmethod
    def _demo_soil() -> dict:
        return {
            "success": True, "demo_mode": True,
            "som_mg_kg": 4850.0,
            "ncs_gC_m2_year": 185.4,
            "total_carbon_sequestered_kg_year": 34_484.0,
            "carbon_status": "Sink",
            "area_hectares": 18.6,
        }

    # ── Keep old demo names for backward compat with existing routes ──
    @staticmethod
    def _demo_livestock_emissions():
        return CarbonSatProcessor._demo_livestock()

    @staticmethod
    def _demo_rice_emissions() -> dict:
        return {
            "success": True, "demo_mode": True,
            "flooding_days": 0, "ch4_kg_total": 0,
            "ch4_kg_per_hectare": 0, "area_hectares": 18.6,
        }

    @staticmethod
    def _demo_soil_carbon():
        return CarbonSatProcessor._demo_soil()


# Module-level singleton
carbon_processor = CarbonSatProcessor()
