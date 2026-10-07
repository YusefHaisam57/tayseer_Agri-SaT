# DATABASE REALITY REPORT
## Tayseer Agri-SaT — Phase 2: Database Reality
**Date:** 2026-10-07  
**Method:** Direct SQLite inspection + runtime pragma verification

---

## 1. Active Database

| Parameter | Value |
|---|---|
| Engine | SQLite 3 (via Python sqlite3 module) |
| File | tayseer_agrisat.db |
| Path | E:\Tayseer Agri-SaT\V_final\tayseer_Agri-SaT\tayseer_Agri-SaT\tayseer_agrisat.db |
| Size | 311,296 bytes |
| PostgreSQL | NOT SUPPORTED — SQLite-only codebase |
| Multi-DB | NOT IMPLEMENTED |

## 2. Table Inventory

| Table | Row Count | Notes |
|---|---|---|
| Users | 10 | Primary user table |
| Farm_Data | 2 | STALE — all rows dated 2026-03-11 |
| Chat_Logs | 37 | 37 chat exchanges stored |
| Pending_Changes | 0 | No pending farm name changes |
| audit_logs | 1 | Nearly empty — coverage failure |
| security_events | 2 | Minimal |
| user_sessions | 0 | No active JWT sessions |
| pivot_telemetry | 3 | 3 test rows |
| rag_documents | 0 | No uploaded documents |
| rag_chunks | 0 | No indexed content |
| farm_plots | 0 | No drawn farm boundaries |
| gee_tile_cache | 0 | No GEE tiles cached |
| report_logs | 0 | No generated reports logged |
| crop_library | 0 | Seed data not applied (INSERT OR IGNORE but table exists empty) |
| email_logs | 0 | |
| email_preferences | 2 | |
| roles | 4 | Seeded: super_admin, host, client, viewer |
| livestock_emissions | 0 | |
| crop_emissions | 0 | |
| soil_carbon_data | 0 | |
| barn_hotspots | 0 | |
| carbon_credits | 0 | |
| access_control | 0 | |
| rate_limits | 0 | |
| wwfi_data | 3 | 3 test rows |
| gee_processing_logs | 0 | |
| host_inbox | 0 | |
| plot_history | 0 | |
| wwfi_data | 3 | |

## 3. Schema Findings

### Foreign Keys
**DISABLED at runtime.** Although the connection helper calls `PRAGMA foreign_keys=ON`, this pragma does NOT persist across connections returned from the pool. The pool reuses connections without reapplying PRAGMAs. Result: foreign key constraints are NOT enforced.

### Tenant Isolation (Schema Level)
Every table that stores user data includes `user_id` columns referencing Users(id). Isolation is enforced at the SQL query level in application code (FarmDataProvider, RAGEngine), NOT by database-level row-level security.

### Data Issues
- Farm_Data has only 2 rows — 1 per user (user_id 1 and 2), both dated 2026-03-11
- Source is `weather_physics` and `physics` — not real GEE satellite data
- Pivot_Telemetry table exists (lowercase) but code expects `Pivot_Telemetry` (capitalized in some places)
- crop_library appears empty despite seed INSERT OR IGNORE — likely the IGNORE applied because id conflicts occurred

## 4. Questions Answered

| Question | Answer |
|---|---|
| Which DB is active? | SQLite only |
| Is SQLite still being used? | YES — exclusively |
| Is PostgreSQL supported? | NO |
| Can production run without SQLite-only? | NO — hardwired to SQLite |
| Duplicate DB paths? | NO — single path |
| Foreign keys enforced? | NO — pool reuse breaks PRAGMA persistence |
| Tenant relationships enforced? | YES at application level, NOT at DB level |
