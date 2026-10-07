# Tayseer-AgriSaT V4.0 Upgrade

This plan addresses the massive architectural and agronomic updates required for Tayseer-AgriSaT V4.0, focusing strictly on maintaining application stability and step-by-step modularity.

## Proposed Changes

### Backend Models & Agronomic Equations
Update the equations in [ml_gee_engine.py](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/ml_gee_engine.py) and profile integration in [app.py](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/app.py) and [database.py](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/database.py).

#### [MODIFY] [ml_gee_engine.py](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/ml_gee_engine.py)
- Modify [compute_lst_split](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/ml_gee_engine.py#597-614) to use Fractional Vegetation Cover (FVC) derived from NDVI:
  `FVC = ((NDVI - 0.15) / (0.85 - 0.15))^2` clamped to `[0,1]`.
  `canopy_temp = LST - FVC * coefficient_c`
  `soil_temp = LST + (1 - FVC) * coefficient_s`
- Update Biomass to `Ton/Feddan` instead of `t/ha` (`t/ha` / `2.38`).
- Update the physics pipeline [apply_physics_pipeline](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/ml_gee_engine.py#891-926) to take into account the user's `pump_flow_rate_m3_per_hr` and use it to calculate [pump_hours](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/ml_gee_engine.py#875-889) optimally:
  `Total Water Required (m³) = ET_mm * 4.2 * farm_area_feddan`
  `Operating Hours = Total Water Required / pump_flow_rate_m3_per_hr`
- Modify WUE representation to map values to an agronomic scale (e.g., "Excellent Conversion", "High Water Waste") based on crop alerts.

#### [MODIFY] [app.py](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/app.py)
- Integrate `pump_flow_rate_m3_per_hr` seamlessly with the profile update APIs (it might alias to the existing `pump_flow_rate` column or `well_flow_rate`).
- Feed the new [pump_hours](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/ml_gee_engine.py#875-889) and Agronomic WUE scale to the APIs.
- Enhance the AI Chat API endpoint to support TPHC context mapping and the 6 predefined quick-action buttons.

---

### Global UI & Map Engine Modularity
Frontend map UI, user profile additions, and offline integrations.

#### [MODIFY] [templates/profile.html](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/templates/profile.html)
- Add dynamic `farm_area_feddan` and `pump_flow_rate_m3_per_hr` fields.

#### [MODIFY] [templates/dashboard.html](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/templates/dashboard.html) & Javascript
- Upgrade Leaflet/charts to display Biomass in `Ton/Feddan` and display proper Soil vs. Canopy temps.
- Change wording to "ساعات تشغيل جهاز الري".
- Update the Map Title to "خريطة المخاطر".
- Implement Offline Mode Caching via Service Workers, storing [farm_data](file:///d:/Tayseer%20Agri-SaT/Platform/tayseer_Agri-SaT/app.py#355-440) JSON to `IndexedDB` or `localStorage`.
- Add the Chatbot offline JS rule-engine fallback.

## Verification Plan
### Automated Tests
- Validate Agronomic Math conversions locally (Ton/Feddan, FVC, Pump hours).
- Test Service Worker logic offline to ensure successful local prediction and metrics delivery.

### Manual Verification
- Render the map to confirm `10x10m` pixel constraint functionality.
- Confirm TPHC anomalous pixel mappings visually on standard test geoJSONs.
