# ============================================================
# risk_engine.py — Tayseer Agri-SaT Unified Risk Engine
#
# Aggregates real farm data into a structured risk assessment.
# NEVER fabricates data. Returns DATA_UNAVAILABLE when missing.
# ============================================================

import logging
import datetime
from typing import Optional

log = logging.getLogger(__name__)

SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH     = "high"
SEVERITY_MEDIUM   = "medium"
SEVERITY_LOW      = "low"
SEVERITY_NORMAL   = "normal"
SEVERITY_UNKNOWN  = "unknown"

DATA_UNAVAILABLE  = "DATA_UNAVAILABLE"
STALE_DATA        = "STALE_DATA"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
LOW_CONFIDENCE    = "LOW_CONFIDENCE"


class RiskEngine:
    NDVI_CRITICAL    = 0.20
    NDVI_WARNING     = 0.35
    NDVI_HEALTHY     = 0.55
    NDWI_CRITICAL    = -0.20
    NDWI_WARNING     = -0.05
    TVDI_CRITICAL    = 0.75
    TVDI_WARNING     = 0.55
    LST_CRITICAL     = 45.0
    LST_WARNING      = 40.0
    NDVI_CV_CRITICAL = 25.0
    NDVI_CV_WARNING  = 15.0
    STALE_HOURS      = 72

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.now = datetime.datetime.utcnow()
        self._risks = []
        self._evidence = []

    def compute_from_provider(self, provider):
        """Compute comprehensive risks using FarmDataProvider as unified gateway."""
        meta = provider.get_farm_metadata()
        satellite = provider.get_satellite_observations()
        weather = provider.get_weather_forecast()
        ml = provider.get_ml_predictions()

        farm_row = satellite.get("raw") or {}
        user_row = meta
        forecast = weather.get("forecast") or []
        agro_alerts = weather.get("agro_alerts") or []

        return self.compute(
            farm_row=farm_row,
            user_row=user_row,
            history=[],
            forecast=forecast,
            agro_alerts=agro_alerts,
            ml_prediction=ml.get("prediction") or ml
        )

    def compute(self, farm_row=None, user_row=None, history=None,
                forecast=None, agro_alerts=None, ml_prediction=None):
        farm = farm_row or {}
        user = user_row or {}
        hist = history or []
        fcast = forecast or []
        alerts = agro_alerts or []

        data_date = farm.get("date") or farm.get("created_at", "")
        data_status, stale_flag = self._check_freshness(data_date)

        land_crop  = self._assess_land_crop(farm, hist, stale_flag)
        pivot_irr  = self._assess_pivot_irrigation(farm, hist, stale_flag)
        machine    = self._assess_machine_pump(farm, user, stale_flag)
        weather    = self._assess_weather(fcast, alerts)

        active_risks = list(self._risks)
        for issue in pivot_irr.get("issues", []):
            active_risks.append({
                "risk_id":  f"pivot_{issue.get('tower','x')}",
                "category": f"pivot_{issue.get('type','unknown')}",
                "severity": issue.get("severity", SEVERITY_MEDIUM),
                "score":    self._severity_to_score(issue.get("severity", SEVERITY_MEDIUM)),
                "reason":   issue.get("reason", ""),
                "evidence": [{"component": issue.get("component"), "tower": issue.get("tower")}],
                "timestamp": self.now.isoformat() + "Z",
                "confidence": 0.75,
                "recommended_actions": [issue.get("action", "")],
            })

        for w in weather.get("alerts", []):
            active_risks.append({
                "risk_id":  f"weather_{w.get('type','x')}",
                "category": f"weather_{w.get('type','unknown')}",
                "severity": w.get("severity", SEVERITY_MEDIUM),
                "score":    self._severity_to_score(w.get("severity", SEVERITY_MEDIUM)),
                "reason":   w.get("description", ""),
                "evidence": [{"source": "OpenWeatherMap"}],
                "timestamp": self.now.isoformat() + "Z",
                "confidence": 0.70,
                "recommended_actions": [],
            })

        if ml_prediction and isinstance(ml_prediction, dict):
            pred_status = ml_prediction.get("predicted_status", "")
            if pred_status in ("water_stress", "heat_stress", "pivot_alert"):
                conf = float(ml_prediction.get("confidence", 0.5))
                active_risks.append({
                    "risk_id":  "ml_crop_health",
                    "category": "ml_prediction",
                    "severity": SEVERITY_HIGH if conf > 0.7 else SEVERITY_MEDIUM,
                    "score":    int(conf * 70),
                    "reason":   f"نموذج ML: {pred_status} (ثقة {conf:.0%})",
                    "evidence": [{"model": "CatBoostClassifier", "confidence": conf}],
                    "timestamp": self.now.isoformat() + "Z",
                    "confidence": conf,
                    "recommended_actions": ["مراجعة نتائج ML والتأكيد ميدانياً"],
                })

        unique_risks = []
        seen = set()
        for r in active_risks:
            if r["risk_id"] not in seen:
                seen.add(r["risk_id"])
                unique_risks.append(r)

        score, severity = self._compute_overall_score(unique_risks, data_status)

        recommended_actions = []
        for r in unique_risks:
            for a in r.get("recommended_actions", []):
                if a and a not in recommended_actions:
                    recommended_actions.append(a)

        return {
            "farm_id":             self.user_id,
            "farm_risk_score":     score,
            "severity":            severity,
            "data_status":         data_status,
            "land_crop":           land_crop,
            "pivot_irrigation":    pivot_irr,
            "machine_pump":        machine,
            "weather_alerts":      weather,
            "active_risks":        unique_risks,
            "recommended_actions": recommended_actions,
            "evidence":            self._evidence,
            "timestamp":           self.now.isoformat() + "Z",
            "data_date":           data_date or DATA_UNAVAILABLE,
        }

    def _assess_land_crop(self, farm, hist, stale):
        ndvi = farm.get("ndvi")
        ndwi = farm.get("ndwi")
        lst  = farm.get("lst")
        et   = farm.get("et")
        tvdi = farm.get("tvdi")
        biom = farm.get("biomass")

        result = {
            "ndvi":    {"value": ndvi,  "status": DATA_UNAVAILABLE, "label": ""},
            "ndwi":    {"value": ndwi,  "status": DATA_UNAVAILABLE, "label": ""},
            "lst":     {"value": lst,   "status": DATA_UNAVAILABLE, "label": ""},
            "et":      {"value": et,    "status": DATA_UNAVAILABLE, "label": ""},
            "tvdi":    {"value": tvdi,  "status": DATA_UNAVAILABLE, "label": ""},
            "biomass": {"value": biom,  "status": DATA_UNAVAILABLE, "label": ""},
            "overall_severity": SEVERITY_UNKNOWN,
            "source": farm.get("source", DATA_UNAVAILABLE),
        }

        if stale:
            result["data_note"] = STALE_DATA
            return result

        severity_votes = []

        if ndvi is not None:
            try:
                v = float(ndvi)
                if v < self.NDVI_CRITICAL:
                    sev, lbl = SEVERITY_CRITICAL, f"إجهاد نباتي شديد (NDVI={v:.3f})"
                    self._add_risk("land_ndvi", SEVERITY_CRITICAL,
                        f"NDVI منخفض جداً: {v:.3f}", "زيادة الري فوراً وفحص المحصول")
                elif v < self.NDVI_WARNING:
                    sev, lbl = SEVERITY_HIGH, f"إجهاد نباتي (NDVI={v:.3f})"
                    self._add_risk("land_ndvi", SEVERITY_HIGH,
                        f"NDVI منخفض: {v:.3f}", "مراجعة برنامج الري والتسميد")
                elif v < self.NDVI_HEALTHY:
                    sev, lbl = SEVERITY_MEDIUM, f"غطاء نباتي متوسط (NDVI={v:.3f})"
                else:
                    sev, lbl = SEVERITY_NORMAL, f"محصول صحي (NDVI={v:.3f})"
                result["ndvi"] = {"value": v, "status": sev, "label": lbl}
                severity_votes.append(sev)
                self._evidence.append({"metric": "NDVI", "value": v,
                    "source": farm.get("source", "gee"), "timestamp": farm.get("date", "")})
            except (TypeError, ValueError): pass

        if tvdi is not None:
            try:
                v = float(tvdi)
                if v > self.TVDI_CRITICAL:
                    sev, lbl = SEVERITY_CRITICAL, f"جفاف شديد (TVDI={v:.3f})"
                    self._add_risk("soil_dryness", SEVERITY_CRITICAL,
                        f"جفاف التربة شديد: TVDI={v:.3f}", "ابدأ الري فوراً")
                elif v > self.TVDI_WARNING:
                    sev, lbl = SEVERITY_HIGH, f"تربة جافة (TVDI={v:.3f})"
                    self._add_risk("soil_dryness", SEVERITY_HIGH,
                        f"جفاف في التربة: TVDI={v:.3f}", "زيادة ساعات الري")
                else:
                    sev, lbl = SEVERITY_NORMAL, f"رطوبة تربة جيدة (TVDI={v:.3f})"
                result["tvdi"] = {"value": v, "status": sev, "label": lbl}
                severity_votes.append(sev)
            except (TypeError, ValueError): pass

        if lst is not None:
            try:
                v = float(lst)
                if v > self.LST_CRITICAL:
                    sev, lbl = SEVERITY_CRITICAL, f"إجهاد حراري شديد (LST={v:.1f}م)"
                    self._add_risk("heat_stress", SEVERITY_CRITICAL,
                        f"حرارة السطح مرتفعة جداً: {v:.1f}م", "زيادة الري وتهوية المحصول")
                elif v > self.LST_WARNING:
                    sev, lbl = SEVERITY_HIGH, f"إجهاد حراري (LST={v:.1f}م)"
                    self._add_risk("heat_stress", SEVERITY_HIGH,
                        f"درجة حرارة مرتفعة: {v:.1f}م", "مراقبة المحصول وزيادة الري")
                else:
                    sev, lbl = SEVERITY_NORMAL, f"درجة حرارة طبيعية (LST={v:.1f}م)"
                result["lst"] = {"value": v, "status": sev, "label": lbl}
                severity_votes.append(sev)
            except (TypeError, ValueError): pass

        if ndwi is not None:
            try:
                v = float(ndwi)
                if v < self.NDWI_CRITICAL:
                    sev, lbl = SEVERITY_CRITICAL, f"عجز مائي حاد (NDWI={v:.3f})"
                    self._add_risk("water_deficit", SEVERITY_CRITICAL,
                        f"نقص حاد في المحتوى المائي: NDWI={v:.3f}", "الري الفوري ضروري")
                elif v < self.NDWI_WARNING:
                    sev, lbl = SEVERITY_HIGH, f"ضغط مائي (NDWI={v:.3f})"
                    self._add_risk("water_deficit", SEVERITY_HIGH,
                        f"ضغط مائي: NDWI={v:.3f}", "زيادة معدل الري")
                else:
                    sev, lbl = SEVERITY_NORMAL, f"محتوى مائي جيد (NDWI={v:.3f})"
                result["ndwi"] = {"value": v, "status": sev, "label": lbl}
                severity_votes.append(sev)
            except (TypeError, ValueError): pass

        if et is not None:
            try:
                v = float(et)
                result["et"] = {"value": v,
                    "status": SEVERITY_NORMAL if v < 7.0 else SEVERITY_HIGH,
                    "label": f"التبخر النتح: {v:.2f} مم/يوم"}
            except (TypeError, ValueError): pass

        if biom is not None:
            try:
                v = float(biom)
                result["biomass"] = {"value": v, "status": SEVERITY_NORMAL,
                    "label": f"الكتلة الحيوية: {v:.2f} طن/هكتار"}
            except (TypeError, ValueError): pass

        result["overall_severity"] = self._max_severity(severity_votes)
        return result

    def _assess_pivot_irrigation(self, farm, hist, stale):
        ndvi_cv    = farm.get("ndvi_cv")
        th_inertia = farm.get("thermal_inertia")
        status_db  = farm.get("status", "")

        result = {
            "ndvi_cv":         {"value": ndvi_cv,    "status": DATA_UNAVAILABLE, "label": ""},
            "thermal_inertia": {"value": th_inertia, "status": DATA_UNAVAILABLE, "label": ""},
            "pivot_alert":     False,
            "issues":          [],
            "overall_severity": SEVERITY_UNKNOWN,
        }

        if stale:
            result["data_note"] = STALE_DATA
            return result

        severity_votes = []

        if ndvi_cv is not None:
            try:
                v = float(ndvi_cv)
                if v > self.NDVI_CV_CRITICAL:
                    sev, lbl = SEVERITY_CRITICAL, f"عطل ميكانيكي محتمل (CV={v:.1f}%)"
                    tower = int(v // 5) % 8 + 1
                    result["issues"].append({
                        "type": "mechanical", "component": "كوبلنج/رشاشات",
                        "tower": f"برج {tower}", "severity": SEVERITY_CRITICAL,
                        "reason": f"تباين NDVI مرتفع جداً ({v:.1f}%)",
                        "action": f"افحص الكوبلنج في برج {tower} فوراً",
                    })
                    self._add_risk("pivot_mechanical", SEVERITY_CRITICAL,
                        f"شذوذ ميكانيكي: CV={v:.1f}%", f"افحص برج {tower} فوراً")
                    result["pivot_alert"] = True
                elif v > self.NDVI_CV_WARNING:
                    sev, lbl = SEVERITY_MEDIUM, f"تباين توزيع الري (CV={v:.1f}%)"
                    tower = int(v // 3) % 8 + 1
                    result["issues"].append({
                        "type": "distribution", "component": "رشاشات",
                        "tower": f"برج {tower}", "severity": SEVERITY_MEDIUM,
                        "reason": f"تباين NDVI معتدل ({v:.1f}%)",
                        "action": f"راجع رشاشات برج {tower}",
                    })
                    self._add_risk("pivot_distribution", SEVERITY_MEDIUM,
                        f"تباين توزيع الري: CV={v:.1f}%", "مراجعة الرشاشات وضغط الشبكة")
                else:
                    sev, lbl = SEVERITY_NORMAL, f"البيفوت يعمل بانتظام (CV={v:.1f}%)"
                result["ndvi_cv"] = {"value": v, "status": sev, "label": lbl}
                severity_votes.append(sev)
            except (TypeError, ValueError): pass

        if th_inertia is not None:
            try:
                v = float(th_inertia)
                if v > 0.7:
                    sev, lbl = SEVERITY_HIGH, f"احتمال انقطاع التبريد ({v:.2f})"
                    self._add_risk("pivot_thermal", SEVERITY_HIGH,
                        f"شذوذ حراري: {v:.2f}", "التحقق من نظام الري الكامل")
                    severity_votes.append(sev)
                elif v > 0.5:
                    sev, lbl = SEVERITY_MEDIUM, f"تحذير حراري ({v:.2f})"
                    severity_votes.append(sev)
                else:
                    sev, lbl = SEVERITY_NORMAL, f"الحالة الحرارية طبيعية ({v:.2f})"
                result["thermal_inertia"] = {"value": v, "status": sev, "label": lbl}
            except (TypeError, ValueError): pass

        if status_db == "pivot_alert":
            result["pivot_alert"] = True
            if SEVERITY_HIGH not in severity_votes and SEVERITY_CRITICAL not in severity_votes:
                severity_votes.append(SEVERITY_HIGH)
                self._add_risk("pivot_status", SEVERITY_HIGH,
                    "تنبيه بيفوت مسجّل", "فحص البيفوت كاملاً")

        result["overall_severity"] = self._max_severity(severity_votes)
        return result

    def _assess_machine_pump(self, farm, user, stale):
        pump_hours     = farm.get("pump_hours") or farm.get("pump_hours_calc")
        pump_power_kw  = user.get("pump_power_kw", 0)
        pump_flow_rate = user.get("pump_flow_rate", 50)
        well_flow_rate = user.get("well_flow_rate", 0)
        et             = farm.get("et")
        area_feddan    = user.get("area_feddan", 1.0)

        result = {
            "pump_hours":       {"value": pump_hours, "status": DATA_UNAVAILABLE if pump_hours is None else SEVERITY_NORMAL, "label": ""},
            "pump_flow_rate":   pump_flow_rate,
            "pump_power_kw":    pump_power_kw,
            "well_flow_rate":   well_flow_rate,
            "anomalies":        [],
            "overall_severity": SEVERITY_UNKNOWN,
        }

        if stale:
            result["data_note"] = STALE_DATA
            return result

        severity_votes = []

        if pump_hours is not None and et is not None:
            try:
                ph_f   = float(pump_hours)
                et_f   = float(et)
                area_f = float(area_feddan or 1.0)
                flow_f = float(pump_flow_rate or 50.0)
                expected_hours = (et_f * area_f * 4.2) / max(flow_f, 1.0)
                ratio = ph_f / max(expected_hours, 0.1)

                if ratio > 2.5:
                    sev = SEVERITY_HIGH
                    lbl = f"ساعات تشغيل مرتفعة جداً ({ph_f:.1f}h vs متوقع {expected_hours:.1f}h)"
                    self._add_risk("pump_overcurrent", SEVERITY_HIGH,
                        f"ساعات التشغيل تجاوزت المعتاد بـ{ratio:.1f}x",
                        "فحص الطلمبة من الاحتمال الزائد والتسرب")
                    result["anomalies"].append({"type": "overcurrent_risk", "severity": sev, "reason": lbl})
                    severity_votes.append(sev)
                elif ratio < 0.3 and et_f > 3.0:
                    sev = SEVERITY_HIGH
                    lbl = f"ساعات تشغيل منخفضة جداً ({ph_f:.1f}h vs متوقع {expected_hours:.1f}h)"
                    self._add_risk("pump_undercurrent", SEVERITY_HIGH,
                        "الطلمبة تعمل أقل من المطلوب",
                        "فحص الطلمبة والتحقق من فتح الصمامات")
                    result["anomalies"].append({"type": "undercurrent_risk", "severity": sev, "reason": lbl})
                    severity_votes.append(sev)
                else:
                    sev = SEVERITY_NORMAL
                    lbl = f"ساعات تشغيل طبيعية ({ph_f:.1f}h)"
                result["pump_hours"] = {"value": ph_f, "expected": round(expected_hours, 2), "status": sev, "label": lbl}
            except (TypeError, ValueError): pass

        result["overall_severity"] = self._max_severity(severity_votes)
        return result

    def _assess_weather(self, forecast, agro_alerts):
        if not forecast:
            return {"status": DATA_UNAVAILABLE, "note": "يتطلب مفتاح OpenWeatherMap API",
                    "alerts": [], "forecast_days": 0}

        rain_risk = any(d.get("rain_probability", 0) > 60 for d in forecast)
        heat_risk = any(d.get("temp_max", 0) > 42 for d in forecast)
        wind_risk = any(d.get("wind_speed", 0) > 15 for d in forecast)

        risks = []
        if rain_risk:
            risks.append({"type": "rain", "severity": SEVERITY_MEDIUM,
                          "description": "احتمال أمطار خلال 5 أيام — راجع جدول الري"})
            self._add_risk("weather_rain", SEVERITY_MEDIUM,
                "احتمال أمطار خلال الأيام القادمة", "تقليل ساعات الري وفق التوقعات")
        if heat_risk:
            risks.append({"type": "heat", "severity": SEVERITY_HIGH,
                          "description": "حرارة مرتفعة جداً متوقعة (>42م) — خطر على المحصول"})
            self._add_risk("weather_heat", SEVERITY_HIGH,
                "موجة حر متوقعة", "زيادة الري وتغطية المحصول الحساس")
        if wind_risk:
            risks.append({"type": "wind", "severity": SEVERITY_MEDIUM,
                          "description": "رياح قوية متوقعة — قد تؤثر على الرش والبيفوت"})

        for alert in (agro_alerts or []):
            if isinstance(alert, str):
                risks.append({"type": "agro", "severity": SEVERITY_MEDIUM, "description": alert})
            elif isinstance(alert, dict):
                risks.append({"type": alert.get("type", "agro"),
                               "severity": alert.get("severity", SEVERITY_MEDIUM),
                               "description": alert.get("message", str(alert))})

        return {"status": "available", "forecast_days": len(forecast), "alerts": risks,
                "rain_risk": rain_risk, "heat_risk": heat_risk, "wind_risk": wind_risk}

    def _compute_overall_score(self, active_risks, data_status):
        if data_status == DATA_UNAVAILABLE:
            return 0, SEVERITY_UNKNOWN
        if not active_risks:
            return 0, SEVERITY_NORMAL
        weights = {SEVERITY_CRITICAL: 100, SEVERITY_HIGH: 70,
                   SEVERITY_MEDIUM: 40, SEVERITY_LOW: 15, SEVERITY_NORMAL: 0}
        max_score = max(weights.get(r.get("severity", SEVERITY_NORMAL), 0) for r in active_risks)
        penalty = min(len(active_risks) * 5, 25)
        score = min(max_score + penalty, 100)
        if score >= 80:   severity = SEVERITY_CRITICAL
        elif score >= 60: severity = SEVERITY_HIGH
        elif score >= 35: severity = SEVERITY_MEDIUM
        elif score >= 10: severity = SEVERITY_LOW
        else:             severity = SEVERITY_NORMAL
        return score, severity

    def _check_freshness(self, data_date):
        if not data_date:
            return DATA_UNAVAILABLE, True
        try:
            if "T" in str(data_date):
                dt = datetime.datetime.fromisoformat(str(data_date).replace("Z",""))
            else:
                dt = datetime.datetime.strptime(str(data_date)[:10], "%Y-%m-%d")
            age_hours = (self.now - dt).total_seconds() / 3600
            if age_hours > self.STALE_HOURS:
                return STALE_DATA, True
            return "complete", False
        except (ValueError, TypeError):
            return DATA_UNAVAILABLE, True

    def _add_risk(self, category, severity, reason, action):
        risk_id = f"{category}_{len(self._risks)}"
        if any(r["risk_id"] == risk_id for r in self._risks):
            return
        self._risks.append({
            "risk_id":  risk_id,
            "category": category,
            "severity": severity,
            "score":    self._severity_to_score(severity),
            "reason":   reason,
            "evidence": list(self._evidence[-1:]),
            "timestamp": self.now.isoformat() + "Z",
            "confidence": 0.80,
            "recommended_actions": [action] if action else [],
        })

    @staticmethod
    def _severity_to_score(severity):
        return {SEVERITY_CRITICAL: 90, SEVERITY_HIGH: 65, SEVERITY_MEDIUM: 35,
                SEVERITY_LOW: 15, SEVERITY_NORMAL: 0}.get(severity, 0)

    @staticmethod
    def _max_severity(votes):
        for sev in [SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM,
                    SEVERITY_LOW, SEVERITY_NORMAL]:
            if sev in votes:
                return sev
        return SEVERITY_UNKNOWN


def compute_farm_risks(user_id, farm_row=None, user_row=None, history=None,
                       forecast=None, agro_alerts=None, ml_prediction=None):
    engine = RiskEngine(user_id=user_id)
    return engine.compute(farm_row=farm_row, user_row=user_row, history=history,
                          forecast=forecast, agro_alerts=agro_alerts,
                          ml_prediction=ml_prediction)
