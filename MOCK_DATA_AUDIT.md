# MOCK DATA AUDIT
## Tayseer Agri-SaT — Phase 5: Mock/Fake Data Elimination
**Date:** 2026-10-07  
**Method:** grep across all .py files for mock/fake/random patterns

---

## 1. Occurrences Found

### FILE: app.py (Line 3680-3692) — PRODUCTION FAKE DATA
```
# Fallback to demo data if no real records
if not has_data:
    summary = {
        "livestock": {"ch4_kg": 850.0, "nh3_kg": 279.0, "avg_heads": 15.5},
        "crops":     {"ch4_kg": 8010.0, "flooding_days": 85.0},
        "soil":      {"som_mg_kg": 28.5, "ncs_gC_m2_year": 245.0,
                      "total_sequestered_kg": 17762.5},
    }
```
**Classification: PRODUCTION FAKE DATA**  
**Impact:** /api/carbon/credits/calculate returns fabricated carbon credits when no real emissions data exists. Users see fictional USD values for carbon credits they do not have.  
**Required Fix:** Return DATA_UNAVAILABLE with explanation instead of invented numbers.

---

### FILE: gee_integration.py (Lines 448-498) — SIMULATED DEMO DATA
```python
has_fault   = random.random() < 0.15
v = round(random.uniform(460, 480), 1)
a = round(random.uniform(1.2, 1.8), 2)
p = round(random.uniform(30, 35), 1)
center_pressure = round(random.uniform(38, 42), 1)
speed_percent = round(random.uniform(30, 60))
direction = random.choice(['FWR', 'REV'])
```
**Classification: DEMO_ONLY — reached when no real telemetry exists**  
**Impact:** The `/api/gee/cm60-status/<pivot_id>` endpoint returns random simulated pivot telemetry when no DB record exists AND demo mode conditions are met.  
**Current Behavior:** Code path at lines 426-445 first checks DB for real data. If DB has no record, code falls to the demo simulation block.  
**Is demo_mode off?** The code does NOT check a SIMULATION_MODE flag — it falls through automatically when DB is empty.  
**Required Fix:** Remove the demo simulation block entirely. Return DATA_UNAVAILABLE / EXTERNAL_HARDWARE_REQUIRED when no real data exists.

---

### FILE: gee_carbon_sat.py (Lines 110, 114, 137, 139) — FALLBACK DUMMY IMAGES
```python
dummy_s2 = ee.Image.constant([1000, 1000, 1000, 1000, 1000]).rename(...)
ee.Algorithms.If(s2_col.size().gt(0), s2_col.median(), dummy_s2)
dummy_l8 = ee.Image.constant(300 / 0.00341802).rename("ST_B10")
```
**Classification: TEST/DEVELOPMENT — GEE fallback when no imagery available**  
**Impact:** If GEE is authenticated but no imagery is found for the date range, dummy constant images are returned. These would produce meaningless NDVI/LST values.  
**Required Fix:** Reject with INSUFFICIENT_DATA instead of using constant dummy images.

### FILE: gee_carbon_sat.py (Line 327) — DEMO DATA METHOD
```python
"""Demo data for the four carbon layers."""
```
**Classification: DEMO_ONLY — explicit demo method**  
**Status:** Acceptable if only called when GEE is offline. Needs guard to ensure it is never surfaced as real data.

---

### FILE: ml_gee_engine.py (Line 1187) — TRAINING SYNTHETIC DATA
```python
rng = np.random.default_rng(42)
```
**Classification: TRAINING CODE — NOT production path**  
**Impact:** None in production. This is inside `_build_synthetic_training_data()` which is only called by `train_model()`, never at runtime. The model is pre-trained and loaded from disk.

### FILE: ml_gee_engine.py (Lines 273, 432) — GEODESIC AREA CALCULATION
```python
area += (xj - xi) * (2.0 + math.sin(yi) + math.sin(yj))
```
**Classification: PRODUCTION — legitimate math**  
**Impact:** None — this is the Haversine/spherical polygon area formula, not fake data.

---

## 2. Summary

| File | Pattern | Classification | Action Required |
|---|---|---|---|
| app.py:3686 | Hardcoded carbon values | PRODUCTION FAKE DATA | YES — Return DATA_UNAVAILABLE |
| gee_integration.py:448-498 | random.random/uniform | DEMO_ONLY | YES — Remove or gate behind explicit SIMULATION_MODE=True flag |
| gee_carbon_sat.py:110,137 | dummy constant images | DEVELOPMENT_ONLY | YES — Replace with INSUFFICIENT_DATA |
| gee_carbon_sat.py:327 | demo data method | DEMO_ONLY | Guard with provenance label |
| ml_gee_engine.py:1187 | np.random in training | TRAINING_ONLY | NO — Not reachable at runtime |
| ml_gee_engine.py:273,432 | math.sin in area calc | LEGITIMATE_MATH | NO — Not fake data |

## 3. Verdict

2 production fake data locations confirmed:
- Carbon credits calculation returns fictional USD values
- Pivot telemetry simulation returns random electrical readings without hardware

These must be fixed before production classification can be elevated.
