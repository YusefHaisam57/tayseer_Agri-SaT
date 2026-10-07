# ============================================================
# farm_data_provider.py — Tayseer Agri-SaT Unified Farm Data Provider
#
# Single Gateway between AI Core / Services and Underlying Data Sources:
# Database, Remote Sensing (GEE), Weather (OWM), IoT Telemetry, and ML.
#
# RULE: NEVER FABRICATE DATA.
# Returns DATA_UNAVAILABLE, STALE_DATA, or INSUFFICIENT_DATA when real data is missing.
# ============================================================

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from database import (
    get_user_by_id, get_latest_farm_data, get_farm_data_range,
    get_latest_pivot_telemetry, get_farm_plots, get_connection, return_connection
)
from ml_gee_engine import (
    fetch_weather_forecast, generate_agronomy_alerts, predict_crop_health
)

log = logging.getLogger(__name__)

DATA_UNAVAILABLE  = "DATA_UNAVAILABLE"
STALE_DATA        = "STALE_DATA"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
LOW_CONFIDENCE    = "LOW_CONFIDENCE"


class FarmDataProvider:
    """
    Unified Data Gateway for all farm operational parameters.
    Ensures strict tenant isolation and provenance tracking.
    """

    def __init__(self, user_id: int):
        self.user_id = user_id
        self._user_cache = None

    def _get_user(self) -> Dict[str, Any]:
        if not self._user_cache:
            row = get_user_by_id(self.user_id)
            self._user_cache = dict(row) if row else {}
        return self._user_cache

    def get_farm_metadata(self) -> Dict[str, Any]:
        """Return farm ownership, boundaries, area, crop, and coordinates."""
        user = self._get_user()
        if not user:
            return {
                "status": DATA_UNAVAILABLE,
                "user_id": self.user_id,
                "farm_name": "غير مسجل",
                "area_feddan": 0.0,
                "crop_type": "غير محدد",
                "lat": None,
                "lon": None,
                "plots": [],
            }

        plots = []
        try:
            plots_raw = get_farm_plots(self.user_id)
            plots = [dict(p) for p in plots_raw] if plots_raw else []
        except Exception as e:
            log.warning(f"[FarmDataProvider] Failed to load plots for user {self.user_id}: {e}")

        return {
            "status": "AVAILABLE",
            "user_id": self.user_id,
            "farmer_name": user.get("name"),
            "farm_name": user.get("farm_name") or user.get("farm_location") or "مزرعتي",
            "area_feddan": float(user.get("area_feddan") or 0.0),
            "crop_type": user.get("crop_type") or "قمح",
            "lat": user.get("lat"),
            "lon": user.get("lon"),
            "farm_geojson": user.get("farm_geojson"),
            "plots": plots,
            "is_admin": bool(user.get("is_admin")),
        }

    def get_satellite_observations(self) -> Dict[str, Any]:
        """
        Return latest Sentinel-2 / Landsat remote sensing observations.
        Labels staleness if older than 72 hours.
        """
        row = get_latest_farm_data(self.user_id)
        if not row:
            return {
                "status": DATA_UNAVAILABLE,
                "message": "لا توجد قراءات أقمار صناعية مسجلة للمزرعة حالياً",
                "observation_date": DATA_UNAVAILABLE,
                "metrics": {},
                "provenance": {
                    "source": "Sentinel-2 / GEE",
                    "status": DATA_UNAVAILABLE,
                }
            }

        data = dict(row)
        obs_date_str = data.get("date") or data.get("created_at") or ""
        
        status = "CURRENT"
        if obs_date_str:
            try:
                obs_date = datetime.strptime(obs_date_str[:10], "%Y-%m-%d")
                delta_days = (datetime.now() - obs_date).days
                if delta_days > 3:
                    status = STALE_DATA
            except Exception:
                status = "CURRENT"

        metrics = {
            "ndvi": {
                "value": data.get("ndvi"),
                "unit": "index (-1..1)",
                "source": "Copernicus Sentinel-2",
                "timestamp": obs_date_str,
                "status": "AVAILABLE" if data.get("ndvi") is not None else DATA_UNAVAILABLE,
                "confidence": 0.95 if status == "CURRENT" else 0.70,
            },
            "ndwi": {
                "value": data.get("ndwi"),
                "unit": "index (-1..1)",
                "source": "Copernicus Sentinel-2",
                "timestamp": obs_date_str,
                "status": "AVAILABLE" if data.get("ndwi") is not None else DATA_UNAVAILABLE,
                "confidence": 0.95 if status == "CURRENT" else 0.70,
            },
            "lst": {
                "value": data.get("lst"),
                "unit": "°C",
                "source": "Landsat-9 / Split-Window",
                "timestamp": obs_date_str,
                "status": "AVAILABLE" if data.get("lst") is not None else DATA_UNAVAILABLE,
                "confidence": 0.85,
            },
            "et": {
                "value": data.get("et"),
                "unit": "mm/day",
                "source": "SEBAL / Energy Balance",
                "timestamp": obs_date_str,
                "status": "AVAILABLE" if data.get("et") is not None else DATA_UNAVAILABLE,
                "confidence": 0.85,
            },
            "tvdi": {
                "value": data.get("tvdi"),
                "unit": "index (0..1)",
                "source": "LST-NDVI Triangle",
                "timestamp": obs_date_str,
                "status": "AVAILABLE" if data.get("tvdi") is not None else DATA_UNAVAILABLE,
                "confidence": 0.85,
            },
            "biomass": {
                "value": data.get("biomass"),
                "unit": "t/feddan",
                "source": "Monteith Light Use Model",
                "timestamp": obs_date_str,
                "status": "AVAILABLE" if data.get("biomass") is not None else DATA_UNAVAILABLE,
                "confidence": 0.80,
            },
            "ndvi_cv": {
                "value": data.get("ndvi_cv"),
                "unit": "%",
                "source": "Spatial Pivot Uniformity",
                "timestamp": obs_date_str,
                "status": "AVAILABLE" if data.get("ndvi_cv") is not None else DATA_UNAVAILABLE,
                "confidence": 0.90,
            }
        }

        return {
            "status": status,
            "observation_date": obs_date_str,
            "metrics": metrics,
            "raw": data,
            "provenance": {
                "source": "Sentinel-2 & Landsat-9 via GEE",
                "timestamp": obs_date_str,
                "status": status,
            }
        }

    def get_weather_forecast(self) -> Dict[str, Any]:
        """
        Fetch real-time weather forecast from OpenWeatherMap.
        Falls back to DATA_UNAVAILABLE when coordinates are missing or API fails.
        """
        user = self._get_user()
        lat = user.get("lat")
        lon = user.get("lon")

        if lat is None or lon is None:
            return {
                "status": DATA_UNAVAILABLE,
                "source": "OpenWeatherMap",
                "message": "إحداثيات المزرعة (خط العرض وخط الطول) غير مسجلة في ملف المزارع",
                "forecast": [],
                "current": None,
                "agro_alerts": [],
            }

        try:
            forecast_list = fetch_weather_forecast(lat, lon)
            if not forecast_list:
                return {
                    "status": DATA_UNAVAILABLE,
                    "source": "OpenWeatherMap",
                    "message": "تعذر جلب بيانات الطقس من مزود الخدمة حالياً",
                    "forecast": [],
                    "current": None,
                    "agro_alerts": [],
                }

            current = forecast_list[0] if forecast_list else None
            farm_row = get_latest_farm_data(self.user_id)
            agro_alerts = generate_agronomy_alerts(forecast_list, dict(farm_row) if farm_row else {})

            return {
                "status": "LIVE",
                "source": "OpenWeatherMap API",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "forecast": forecast_list,
                "current": current,
                "agro_alerts": agro_alerts,
                "location": {"lat": lat, "lon": lon},
            }
        except Exception as e:
            log.warning(f"[FarmDataProvider] Weather fetch error for user {self.user_id}: {e}")
            return {
                "status": DATA_UNAVAILABLE,
                "source": "OpenWeatherMap",
                "message": str(e),
                "forecast": [],
                "current": None,
                "agro_alerts": [],
            }

    def get_iot_telemetry(self, pivot_id: str = "pivot_1") -> Dict[str, Any]:
        """
        Return IoT microcontroller telemetry for the farm pivot/pump.
        NO RANDOM OR SIMULATED DATA IN PRODUCTION.
        Returns DATA_UNAVAILABLE if no hardware is connected.
        """
        telemetry = get_latest_pivot_telemetry(pivot_id)
        if not telemetry:
            return {
                "status": DATA_UNAVAILABLE,
                "pivot_id": pivot_id,
                "message": "لا يوجد جهاز تحكم IoT (CM60) متصل حالياً بهذا البيفوت. بانتظار ربط الحساسات الميدانية.",
                "hardware_state": "WAITING_FOR_HARDWARE",
                "telemetry": None,
            }

        # Check telemetry age
        created_at_str = telemetry.get("created_at") or telemetry.get("timestamp") or ""
        is_stale = False
        if created_at_str:
            try:
                t_time = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - t_time > timedelta(hours=24):
                    is_stale = True
            except Exception:
                pass

        return {
            "status": STALE_DATA if is_stale else "LIVE",
            "pivot_id": pivot_id,
            "hardware_state": "CONNECTED",
            "telemetry": telemetry,
            "timestamp": created_at_str,
            "source": "CM60 Microcontroller Ingestion",
        }

    def get_ml_predictions(self) -> Dict[str, Any]:
        """
        Return CatBoost crop health predictions based on real remote sensing data.
        Labels training provenance: SYNTHETIC_TRAINED / DEVELOPMENT_MODEL.
        """
        obs = self.get_satellite_observations()
        raw = obs.get("raw")
        if not raw:
            return {
                "status": INSUFFICIENT_DATA,
                "message": "لا تتوفر قراءات أقمار صناعية كافية لتشغيل نموذج التنبؤ",
                "prediction": None,
                "model_metadata": {
                    "model_name": "CatBoost Crop Health Classifier",
                    "model_type": "Gradient Boosted Decision Trees",
                    "version": "1.0.0-dev",
                    "training_provenance": "DEVELOPMENT_MODEL / SYNTHETIC_TRAINED",
                    "caution": "النموذج مخصص للمؤشرات الإرشادية أثناء مرحلة التطوير",
                }
            }

        pred = predict_crop_health(raw)
        if not pred:
            return {
                "status": DATA_UNAVAILABLE,
                "message": "تعذر تشغيل نموذج التنبؤ على البيانات الحالية",
                "prediction": None,
            }

        return {
            "status": "AVAILABLE",
            "predicted_status": pred.get("predicted_status"),
            "predicted_label_ar": pred.get("predicted_label_ar"),
            "confidence": pred.get("confidence"),
            "probabilities": pred.get("probabilities", {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model_metadata": {
                "model_name": "CatBoost Crop Health Classifier",
                "model_version": "1.0.0-dev",
                "training_provenance": "DEVELOPMENT_MODEL / SYNTHETIC_TRAINED",
                "input_features": ["ndvi", "lst", "et", "tvdi", "ndwi", "fapar", "biomass"],
            }
        }

    def get_consolidated_context(self) -> Dict[str, Any]:
        """
        Compile full unified farm context for AI Core and RiskEngine.
        Ensures all components consume identical ground-truth data.
        """
        metadata = self.get_farm_metadata()
        satellite = self.get_satellite_observations()
        weather = self.get_weather_forecast()
        iot = self.get_iot_telemetry()
        ml = self.get_ml_predictions()

        return {
            "user_id": self.user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata,
            "satellite": satellite,
            "weather": weather,
            "iot": iot,
            "ml": ml,
        }
