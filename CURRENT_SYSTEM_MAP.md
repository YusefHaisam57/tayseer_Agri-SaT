# CURRENT_SYSTEM_MAP.md — Tayseer AgriSaT System Map
**Audit Date:** 2026-10-07  
**Platform Version:** v4.2.0-prod  
**Workspace Path:** `E:\Tayseer Agri-SaT\V_final\tayseer_Agri-SaT\tayseer_Agri-SaT`

---

## 1. Application Architecture Overview

Tayseer Agri-SaT is an end-to-end Agricultural Intelligence & Remote Sensing platform designed for Egyptian farming environments (Sharqia, Toshka, New Valley, Ismailia, Nile Delta). It integrates satellite Earth Observation (Copernicus Sentinel-2 & Landsat-9 via Google Earth Engine), real-time agronomic weather forecasts (OpenWeatherMap API), IoT microcontroller telemetry (CM60 Center Pivot controller), Machine Learning (CatBoost crop health model), and multi-channel AI advisory (Web, WhatsApp, Telegram).

```
                            [ USER INTERFACES ]
        ┌─────────────────────────┬─────────────────────────┐
    Web Dashboard / UI        WhatsApp Webhook        Telegram Webhook
  (Cairo RTL, Vanilla CSS)    (Twilio Sandbox)      (python-telegram-bot)
        └─────────────────────────┼─────────────────────────┘
                                  ▼
                      [ SECURITY & AUTH LAYER ]
                JWT / Session, RBAC, Tenant Isolation
                                  ▼
                        [ AI CORE ENGINE ]
           Multi-Provider LLM: Gemini / OpenAI / Groq / Ollama
                     Persistent Tenant-Isolated RAG
                                  ▼
                     [ FARM DATA PROVIDER ]
              Unified Gateway for Farm & Sensor Data
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
   [ DATABASE ]           [ REMOTE SENSING ]         [ IoT & SENSORS ]
SQLite WAL (Dev) /       Google Earth Engine       CM60 Pivot Controller
PostgreSQL (Prod)       OpenWeatherMap API        Sensor Readings
        │                         │                         │
        └─────────────────────────┼─────────────────────────┘
                                  ▼
                        [ RISK ENGINE ]
           Single Source of Truth for Farm Health & Risk
         (Land & Crop, Pivot, Machinery, Weather, ML Risks)
                                  ▼
                     [ REPORTS & ALERTS SYSTEM ]
                 Executive, Technical & Audit Reports
```

---

## 2. Backend Routes & API Inventory

### Authentication & User Management
- `GET /` — Entry route, redirects authenticated users to `/dashboard` or unauthenticated to `/login`.
- `GET /login` — Login page.
- `POST /auth/login` — Session/password authentication.
- `POST /auth/register` — Account registration.
- `GET /auth/logout` — Session teardown.
- `POST /api/auth/login` — JWT authentication endpoint for external/mobile clients.
- `POST /api/auth/change-password` — Secure credential update.

### Core Operational Pages
- `GET /dashboard` — High-level operational overview (KPIs, farm quick-glance, weather, alerts).
- `GET /risks` — **Central Risk Intelligence Center (المخاطر الأساسية)**. Consolidates Land, Pivot, Machinery, Weather, Satellite, ML predictions, and Action plans.
- `GET /chat` — **Tayseer AI Assistant (المساعد الذكي)**. Multimodal advisory powered by AI Core.
- `GET /farm-setup` — Farm geometry polygon drawing and plot management.
- `GET /map-view` — MapLibre 3D GIS satellite visualization and risk layer overlays.
- `GET /reports` — Analytical agronomic report generation (PDF/CSV/Executive).
- `GET /carbon` — Carbon-Sat greenhouse gas emissions & carbon sequestration accounting.
- `GET /integrations` — Real-time integration diagnostic dashboard (GEE, OWM, LLMs, IoT, Telegram, WhatsApp, SMTP).
- `GET /settings` — User preferences, notification triggers, and channel linking.
- `GET /profile` — Farmer profile and land parcel metadata.
- `GET /admin` — System administration and tenant oversight (role-restricted).
- `GET /host-inbox` — Administrative communications hub.

### Core REST APIs
- `GET /api/system/version` — Runtime application version, git commit, build time, and component health.
- `GET /health` & `GET /ready` — Health checks and dependency readiness probes.
- `GET /api/farm-data` — Latest remote sensing and biometric farm metrics.
- `GET /api/weather` — 5-day / 3-hour agricultural weather forecast from OpenWeatherMap.
- `GET /api/risks/dashboard` — Unified risk metrics, categorized scores, and evidence from `RiskEngine`.
- `GET /api/risks/history` — 30-day historical risk trends.
- `POST /api/run-pipeline` — Trigger satellite + physics + ML calculation pipeline.
- `POST /api/chat` — AI Core conversational endpoint with multimodal attachment support.
- `GET /api/chat-history` — Tenant-isolated multi-turn conversation retrieval.
- `POST /api/reports/generate` — Generate agronomic reports.
- `GET /api/reports/history` — Tenant report history and download links.
- `POST /api/gee/cm60-telemetry` — Ingestion endpoint for CM60 Pivot IoT telemetry.
- `GET /api/gee/cm60-status/<pivot_id>` — Query live/cached CM60 microcontroller status.
- `POST /api/webhook/whatsapp` — Twilio WhatsApp webhook (strictly one single handler).
- `POST /api/webhook/telegram` — Telegram bot webhook (strictly one single handler).

---

## 3. Database Layer (`tayseer_agrisat.db`)

Current active SQLite database in WAL mode with connection pooling:
- `Users`: Farmer and admin accounts, contact details, land area (`area_feddan`), coordinates (`lat`, `lon`), farm names, RBAC role.
- `Farm_Data`: Time-series observations per user (`ndvi`, `lst`, `ndwi`, `et`, `biomass`, `wue`, `tvdi`, `ndvi_cv`).
- `farm_plots`: Multi-plot geometric boundaries and crop assignments per tenant.
- `crop_library`: Egyptian agronomic profiles (Wheat, Alfalfa, Maize, Potato, Citrus, Tomato, Sugar beet).
- `pivot_telemetry`: IoT records from CM60 controller (`voltage`, `current`, `pressure`, `speed`, `direction`, `tower_status`, `raw_payload`).
- `Chat_Logs`: Conversation history per tenant/user.
- `report_logs`: Generated reports tracking.
- `audit_logs`: Security and access control audit trail.
- `security_events`: Security alerts, rate-limiting violations, IP blocks.
- `carbon_credits` & `soil_carbon_data`: Carbon accounting metrics.

---

## 4. Remote Sensing, Weather, ML & AI Engines

1. **Google Earth Engine (GEE)**:
   - Module: `ml_gee_engine.py` & `gee_integration.py`.
   - Datasets: Sentinel-2 Harmonized (`COPERNICUS/S2_SR_HARMONIZED`), Landsat-9 Surface Reflectance.
   - Indices: NDVI, NDWI, LST Split-Window, ET (SEBAL/Energy balance), TVDI, Biomass.
   - Status: Service Account key `tayseeragrisat-b55999a2322f.json` located. GCP project `tayseeragrisat` requires `serviceusage.serviceUsageConsumer` IAM role activation.

2. **OpenWeatherMap (OWM)**:
   - Module: `ml_gee_engine.py:fetch_weather_forecast`.
   - Status: **LIVE & VERIFIED**. Real-time 5-day / 3-hour forecasts for latitude/longitude coordinates.

3. **Machine Learning (CatBoost)**:
   - Model file: `catboost_crop_health.cbm`.
   - Classification: `healthy`, `water_stress`, `heat_stress`, `pivot_alert`.
   - Training Status: **DEVELOPMENT_MODEL / SYNTHETIC_TRAINED**. Must be explicitly labeled as development model.

4. **AI Core & Multi-Provider LLM**:
   - Module: `ai_core.py`.
   - Providers: Google Gemini (REST API), OpenAI / Groq / OpenRouter, Local Ollama.
   - Agronomic Fallback: Expert Egyptian dialect knowledge rules.

---

## 5. Audit of Duplications and Inconsistencies

1. **Duplicate Webhook Routes**:
   - WhatsApp: Routes registered at lines 1880 and 1994 in `app.py`.
   - Telegram: Routes registered at lines 1907 and 2156 in `app.py`.
   - Action: Consolidate into exactly one unified webhook handler per channel.

2. **Duplicate Health Checks**:
   - `GET /health` was registered twice (line 120 and line 1922).
   - Action: Unify into standard `/health` and `/ready`.

3. **Disconnected Risk Calculations**:
   - `risk_engine.py` was created, but certain pages and alerts still performed local ad-hoc risk checks.
   - Action: Route all risk computations exclusively through `RiskEngine`.

4. **IoT Telemetry Mocking in GEE Integration**:
   - `gee_integration.py` used `random.uniform()` when no IoT device was connected.
   - Action: Remove random generator from production path; return `DATA_UNAVAILABLE` when hardware telemetry is absent.

5. **Missing Unified FarmDataProvider**:
   - AI Core and services directly accessed disparate database helpers.
   - Action: Introduce `farm_data_provider.py` as single access gateway.

6. **Service Worker aggressive caching**:
   - `service-worker.js` cached stale HTML and static assets, leading to the owner seeing "website unchanged".
   - Action: Enforce Network-First cache policy with cache busting and version stamps.
