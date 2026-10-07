# ============================================================
# database.py — Tayseer Agri-SaT Database Layer
#
# Responsibilities:
#   • Create / migrate the SQLite schema on first run
#   • Provide clean helper functions for all CRUD operations
#   • Keep SQL completely separate from business logic
# ============================================================

import sqlite3
import hashlib
import os
import threading
from datetime import datetime, timedelta
from config import Config

# ── Connection Pooling ────────────────────────────────────────
# Simple connection pool to reduce repeated connection overhead
_pool_lock = threading.Lock()
_pool = []
_pool_size = 5

def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection from pool. Creates new if needed."""
    global _pool
    
    # Try to reuse a connection from pool
    with _pool_lock:
        if _pool:
            conn = _pool.pop()
            try:
                # Test if connection is still valid
                conn.execute("SELECT 1")
                return conn
            except sqlite3.DatabaseError:
                # Connection died, create new one
                pass
    
    # Create new connection
    conn = sqlite3.connect(Config.DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL")  # concurrent read safety
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")  # Better write performance
    conn.execute("PRAGMA cache_size=5000")     # Larger cache
    return conn

def return_connection(conn: sqlite3.Connection) -> None:
    """Return a connection to the pool."""
    global _pool
    with _pool_lock:
        if len(_pool) < _pool_size:
            _pool.append(conn)


# ── Schema creation ──────────────────────────────────────────

SCHEMA_SQL = """
-- ============================================================
-- Users table
-- ============================================================
CREATE TABLE IF NOT EXISTS Users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    phone           TEXT    NOT NULL UNIQUE,   -- E.164 format, e.g. +201001234567
    email           TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,
    is_verified     INTEGER NOT NULL DEFAULT 1,    -- 1=verified (OTP removed)
    farm_geojson    TEXT,                          -- JSON string of drawn polygons (crops, house, etc)
    farm_name       TEXT,                          -- Official approved farm name
    pending_farm_name TEXT,                        -- Unapproved user-requested farm name
    is_admin        INTEGER NOT NULL DEFAULT 0,    -- 1=Admin (Host), 0=Client
    modules_access  TEXT    DEFAULT '["basic"]',   -- JSON array of allowed modules e.g. ["digital_twin"]
    farm_location   TEXT,                          -- free-text description
    crop_type       TEXT,                          -- e.g. "wheat", "corn"
    area_feddan     REAL    DEFAULT 0.0,           -- farm area in feddan units
    lat             REAL,                          -- WGS-84 latitude
    lon             REAL,                          -- WGS-84 longitude
    telegram_chat_id TEXT,                         -- linked Telegram chat ID
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Farm_Data table  (one row per daily snapshot per user)
-- ============================================================
CREATE TABLE IF NOT EXISTS Farm_Data (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
    date        TEXT    NOT NULL,       -- YYYY-MM-DD
    ndvi        REAL,                   -- Normalised Difference Vegetation Index (-1..1)
    lst         REAL,                   -- Land Surface Temperature (°C)
    lst_soil    REAL,                   -- Soil surface temperature (°C)
    lst_canopy  REAL,                   -- Canopy surface temperature (°C)
    ndwi        REAL,                   -- Normalised Difference Water Index
    et          REAL,                   -- Evapotranspiration (mm/day)
    biomass     REAL,                   -- Estimated above-ground biomass (t/ha)
    wue         REAL,                   -- Water Use Efficiency (kg/m³)
    tvdi        REAL,                   -- Temperature-Vegetation Dryness Index
    fapar       REAL,                   -- Fraction of Absorbed Photosynthetically Active Radiation
    ndvi_cv     REAL,                   -- Coefficient of Variation of NDVI (for pivot health)
    thermal_inertia REAL,              -- Thermal inertia proxy for pivot failure detection
    status      TEXT    DEFAULT 'healthy',  -- 'healthy' | 'water_stress' | 'heat_stress' | 'pivot_alert'
    pump_hours  REAL,                   -- Translated ET → pump running hours
    source      TEXT    DEFAULT 'gee',  -- 'gee' | 'csv' | 'physics'
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Chat_Logs table
-- ============================================================
CREATE TABLE IF NOT EXISTS Chat_Logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
    channel     TEXT    NOT NULL,   -- 'web' | 'whatsapp' | 'telegram'
    message     TEXT    NOT NULL,   -- user's incoming message
    response    TEXT    NOT NULL,   -- bot's reply
    timestamp   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Pending_Changes table  (farm name + future change requests)
-- ============================================================
CREATE TABLE IF NOT EXISTS Pending_Changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
    change_type     TEXT    NOT NULL,   -- 'farm_name' | 'farm_coords' | ...
    old_value       TEXT,
    new_value       TEXT    NOT NULL,
    status          TEXT    DEFAULT 'pending',   -- 'pending' | 'approved' | 'rejected'
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    reviewed_at     TEXT,
    reviewed_by     INTEGER REFERENCES Users(id)
);

-- ============================================================
-- GEE Tile Cache  (satellite imagery tile URLs per user/layer)
-- ============================================================
CREATE TABLE IF NOT EXISTS gee_tile_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
    layer_type      TEXT    NOT NULL,   -- 'ndvi' | 'et' | 'lst_soil' | ... | 'wwfi'
    tile_url        TEXT    NOT NULL,
    vis_params      TEXT,               -- JSON string of vis params used
    date_range_str  TEXT,               -- e.g. '2026-03-01/2026-03-12'
    generated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT,               -- NULL = never expires
    UNIQUE(user_id, layer_type)         -- one row per user per layer (upsert)
);

-- ============================================================
-- WWFI Data Table (Water Waste FingerPrint Index)
-- ============================================================
CREATE TABLE IF NOT EXISTS wwfi_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    farm_id         TEXT NOT NULL,               -- GEE active farm string
    plot_id         TEXT,                        -- e.g. pivot_01
    date            TEXT NOT NULL,               -- YYYY-MM-DD
    
    et_measured     REAL NOT NULL,               -- mm/day
    et_reference    REAL NOT NULL,               -- mm/day
    crop_coefficient REAL NOT NULL,              -- Kc
    pump_hours      REAL NOT NULL,               -- hours/day
    area_hectare    REAL NOT NULL,
    
    actual_water    REAL NOT NULL,               -- m3
    optimal_water   REAL NOT NULL,               -- m3
    wwfi_score      REAL NOT NULL,               -- percentage
    severity        TEXT NOT NULL,               -- 'efficient', 'moderate', 'severe'
    
    pattern_type    TEXT,                        -- 'systematic', 'random', 'operational'
    pattern_confidence REAL,                     -- 0.0 to 1.0
    
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- Pivot Telemetry Table (CM60 IoT / Microcontroller data)
-- ============================================================
CREATE TABLE IF NOT EXISTS pivot_telemetry (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    pivot_id             TEXT NOT NULL,
    timestamp            TEXT NOT NULL DEFAULT (datetime('now')),
    overall_status       TEXT NOT NULL DEFAULT 'Running',
    health_color         TEXT NOT NULL DEFAULT 'green',
    fault_message        TEXT DEFAULT '',
    center_pressure_psi  REAL DEFAULT 40.0,
    speed_percent        INTEGER DEFAULT 50,
    direction            TEXT DEFAULT 'FWR',
    towers_json          TEXT NOT NULL,
    raw_payload          TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Indexes for fast lookups
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_farm_data_user_date  ON Farm_Data      (user_id, date);
CREATE INDEX IF NOT EXISTS idx_chat_logs_user       ON Chat_Logs      (user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_users_phone          ON Users          (phone);
CREATE INDEX IF NOT EXISTS idx_users_telegram       ON Users          (telegram_chat_id);
CREATE INDEX IF NOT EXISTS idx_pending_user         ON Pending_Changes(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tile_cache_user      ON gee_tile_cache (user_id, layer_type);
CREATE INDEX IF NOT EXISTS idx_wwfi_farm_date       ON wwfi_data      (farm_id, date);
CREATE INDEX IF NOT EXISTS idx_wwfi_severity        ON wwfi_data      (severity, date);
CREATE INDEX IF NOT EXISTS idx_pivot_telemetry_id   ON pivot_telemetry(pivot_id, timestamp);
"""


def init_db() -> None:
    """Create all tables and indexes if they don't already exist.
    Safe to call on every application start-up (idempotent).
    """
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)

        # ── Migrations: add irrigation device columns if missing ──
        migrations = [
            "ALTER TABLE Users ADD COLUMN irrigation_type TEXT DEFAULT 'drip'",
            "ALTER TABLE Users ADD COLUMN pump_flow_rate REAL DEFAULT 50.0",
            "ALTER TABLE Users ADD COLUMN pump_power_kw REAL DEFAULT 0",
            # New well/turbine columns (2026-03)
            "ALTER TABLE Users ADD COLUMN well_flow_rate REAL DEFAULT 0",  # تصرف البئر م³/ساعة
            "ALTER TABLE Users ADD COLUMN well_depth REAL DEFAULT 0",       # عمق البئر بالمتر
            "ALTER TABLE Users ADD COLUMN pump_head_m REAL DEFAULT 0",      # ارتفاع الضخ بالمتر
            # V4.0 Enterprise Upgrade Columns
            "ALTER TABLE Users ADD COLUMN farm_name TEXT",
            "ALTER TABLE Users ADD COLUMN pending_farm_name TEXT",
            "ALTER TABLE Users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE Users ADD COLUMN modules_access TEXT DEFAULT '[\"basic\"]'",
            # V4.0 LST Separation
            "ALTER TABLE Farm_Data ADD COLUMN lst_soil REAL",
            "ALTER TABLE Farm_Data ADD COLUMN lst_canopy REAL",
            # Phase 1 — Pending_Changes table (created via SCHEMA_SQL, but guard migration)
            """CREATE TABLE IF NOT EXISTS Pending_Changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                change_type TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                reviewed_at TEXT,
                reviewed_by INTEGER REFERENCES Users(id)
            )""",
            # Phase 2 — GEE Tile Cache table
            """CREATE TABLE IF NOT EXISTS gee_tile_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                layer_type TEXT NOT NULL,
                tile_url TEXT NOT NULL,
                vis_params TEXT,
                date_range_str TEXT,
                generated_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT,
                UNIQUE(user_id, layer_type)
            )""",
            # Phase 2 — farm_type column for Pivot vs Orchard logic
            "ALTER TABLE Users ADD COLUMN farm_type TEXT DEFAULT 'pivot'",
            # Phase 3 — WhatsApp integration columns
            "ALTER TABLE Users ADD COLUMN whatsapp_verified INTEGER DEFAULT 0",
            "ALTER TABLE Users ADD COLUMN whatsapp_opt_in INTEGER DEFAULT 0",
            # Phase 4 — Telegram integration columns
            "ALTER TABLE Users ADD COLUMN telegram_chat_id TEXT",
            "ALTER TABLE Users ADD COLUMN telegram_username TEXT",
            "ALTER TABLE Users ADD COLUMN telegram_verified INTEGER DEFAULT 0",
            "ALTER TABLE Users ADD COLUMN telegram_opt_in INTEGER DEFAULT 0",
            # Phase 5 — Email notification tables
            """CREATE TABLE IF NOT EXISTS email_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER REFERENCES Users(id),
                recipient_email TEXT NOT NULL,
                subject         TEXT NOT NULL,
                template_name   TEXT,
                message_id      TEXT,
                status          TEXT DEFAULT 'sent',
                sent_at         TEXT DEFAULT (datetime('now')),
                opened_at       TEXT,
                error_message   TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS email_preferences (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER UNIQUE NOT NULL REFERENCES Users(id),
                daily_updates   INTEGER DEFAULT 1,
                weekly_reports  INTEGER DEFAULT 1,
                critical_alerts INTEGER DEFAULT 1,
                preferred_time  TEXT DEFAULT '07:00'
            )""",
            """CREATE TABLE IF NOT EXISTS host_inbox (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user_id    INTEGER NOT NULL REFERENCES Users(id),
                subject         TEXT NOT NULL,
                message         TEXT NOT NULL,
                status          TEXT DEFAULT 'unread',
                priority        TEXT DEFAULT 'normal',
                created_at      TEXT DEFAULT (datetime('now')),
                read_at         TEXT,
                replied_at      TEXT
            )""",
            "ALTER TABLE Users ADD COLUMN unsubscribe_token TEXT",
            # V4.1 — Agronomic precision columns
            "ALTER TABLE Farm_Data ADD COLUMN fvc REAL",         # Fractional Vegetation Cover
            "ALTER TABLE Farm_Data ADD COLUMN pump_hours_calc REAL",  # Recalculated pump hours with FVC-LST
            # Step 8 — 3D Farm Drawing Tool tables
            """CREATE TABLE IF NOT EXISTS farm_plots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                plot_type       TEXT    NOT NULL DEFAULT 'plot', -- 'pivot' | 'plot' | 'orchard' | 'greenhouse'
                plot_name       TEXT    NOT NULL,
                geometry        TEXT    NOT NULL,               -- GeoJSON geometry string
                center_lat      REAL,
                center_lon      REAL,
                radius_m        REAL,                           -- meters (pivots only)
                area_m2         REAL    NOT NULL DEFAULT 0,
                area_feddan     REAL    DEFAULT 0,
                area_hectare    REAL    DEFAULT 0,
                crop_type       TEXT,
                crop_variety    TEXT,
                planting_date   TEXT,
                expected_harvest TEXT,
                sector          TEXT,
                region          TEXT,
                soil_type       TEXT,
                notes           TEXT,
                active          INTEGER DEFAULT 1,
                created_at      TEXT    DEFAULT (datetime('now')),
                updated_at      TEXT    DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS plot_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                plot_id     INTEGER NOT NULL REFERENCES farm_plots(id) ON DELETE CASCADE,
                change_type TEXT    NOT NULL, -- 'created' | 'updated' | 'deleted' | 'crop_changed'
                old_data    TEXT,
                new_data    TEXT,
                changed_by  INTEGER REFERENCES Users(id),
                changed_at  TEXT    DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS crop_library (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                crop_name_ar        TEXT NOT NULL,
                crop_name_en        TEXT,
                category            TEXT,
                water_requirement   TEXT,
                growth_days         INTEGER,
                optimal_temp_min    REAL,
                optimal_temp_max    REAL,
                icon                TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_farm_plots_user ON farm_plots(user_id, active)",
            "CREATE INDEX IF NOT EXISTS idx_plot_history_plot ON plot_history(plot_id)",
            # Seed crop_library if empty
            """INSERT OR IGNORE INTO crop_library (id,crop_name_ar,crop_name_en,category,water_requirement,growth_days,icon)
                VALUES
                (1,'\u0642\u0645\u062d','Wheat','\u062d\u0628\u0648\u0628','\u0645\u062a\u0648\u0633\u0637',120,'\ud83c\udf3e'),
                (2,'\u0630\u0631\u0629','Corn','\u062d\u0628\u0648\u0628','\u0639\u0627\u0644\u064a',90,'\ud83c\udf3d'),
                (3,'\u0639\u0628\u0627\u062f \u0627\u0644\u0634\u0645\u0633','Sunflower','\u0632\u064a\u0648\u062a','\u0645\u062a\u0648\u0633\u0637',100,'\ud83c\udf3b'),
                (4,'\u0637\u0645\u0627\u0637\u0645','Tomato','\u062e\u0636\u0631\u0648\u0627\u062a','\u0639\u0627\u0644\u064a',70,'\ud83c\udf45'),
                (5,'\u0628\u0637\u0627\u0637\u0633','Potato','\u062e\u0636\u0631\u0648\u0627\u062a','\u0639\u0627\u0644\u064a',90,'\ud83e\udd54'),
                (6,'\u0628\u0631\u0633\u064a\u0645','Alfalfa','\u0639\u0644\u0641','\u0639\u0627\u0644\u064a',45,'\ud83c\udf3f'),
                (7,'\u0646\u062e\u064a\u0644','Date Palm','\u0641\u0627\u0643\u0647\u0629','\u0645\u062a\u0648\u0633\u0637',1825,'\ud83c\udf34'),
                (8,'\u0632\u064a\u062a\u0648\u0646','Olive','\u0641\u0627\u0643\u0647\u0629','\u0645\u0646\u062e\u0641\u0636',1825,'\ud83e\uded2'),
                (9,'\u0641\u0648\u0644','Fava Bean','\u0628\u0642\u0648\u0644\u064a\u0627\u062a','\u0645\u062a\u0648\u0633\u0637',100,'\ud83e\uded8'),
                (10,'\u0642\u0635\u0628 \u0633\u0643\u0631','Sugar Cane','\u0646\u0628\u0627\u062a\u0627\u062a','\u0639\u0627\u0644\u064a \u062c\u062f\u0627',365,'\ud83c\udf8b'),
                (11,'\u0628\u0635\u0644','Onion','\u062e\u0636\u0631\u0648\u0627\u062a','\u0645\u062a\u0648\u0633\u0637',90,'\ud83e\uddaa'),
                (12,'\u062a\u0641\u0627\u062d','Apple','\u0641\u0627\u0643\u0647\u0629','\u0645\u062a\u0648\u0633\u0637',150,'\ud83c\udf4e')
            """,
            # ── Step 9: Report Generation Logs ──────────────────────────
            """CREATE TABLE IF NOT EXISTS report_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                report_type     TEXT    NOT NULL,   -- 'farm_summary' | 'water_usage' | 'pivot_health'
                format          TEXT    NOT NULL,   -- 'word' | 'excel' | 'pdf'
                filename        TEXT    NOT NULL,
                filepath        TEXT    NOT NULL,
                date_range_days INTEGER DEFAULT 30,
                generated_at    TEXT    DEFAULT (datetime('now')),
                downloaded_at   TEXT,
                emailed_to      TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_report_logs_user ON report_logs(user_id, generated_at)",

            # ====================================================
            # Step 10 — Security & Multi-Tenancy
            # ====================================================

            # User security columns
            "ALTER TABLE Users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE Users ADD COLUMN last_login TEXT",
            "ALTER TABLE Users ADD COLUMN failed_login_attempts INTEGER DEFAULT 0",
            "ALTER TABLE Users ADD COLUMN account_locked_until TEXT",
            "ALTER TABLE Users ADD COLUMN password_changed_at TEXT",
            "ALTER TABLE Users ADD COLUMN api_key TEXT",
            "ALTER TABLE Users ADD COLUMN two_factor_secret TEXT",
            "ALTER TABLE Users ADD COLUMN two_factor_enabled INTEGER DEFAULT 0",

            # Roles table
            """CREATE TABLE IF NOT EXISTS roles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                role_name   TEXT UNIQUE NOT NULL,
                description TEXT,
                permissions TEXT NOT NULL DEFAULT '[]'
            )""",
            # Seed default roles
            """INSERT OR IGNORE INTO roles (id, role_name, description, permissions) VALUES
                (1, 'super_admin', 'مدير النظام الرئيسي', '["all"]'),
                (2, 'host', 'مالك الشركة — يدير العملاء',
                    '["manage_clients","view_all_farms","approve_changes","system_config"]'),
                (3, 'client', 'عميل — يدير مزرعته فقط',
                    '["view_own_farm","edit_farm_data","generate_reports"]'),
                (4, 'viewer', 'مشاهد فقط', '["view_own_farm"]')
            """,

            # JWT Sessions table
            """CREATE TABLE IF NOT EXISTS user_sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                token_hash  TEXT NOT NULL UNIQUE,
                ip_address  TEXT,
                user_agent  TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                expires_at  TEXT NOT NULL,
                revoked     INTEGER DEFAULT 0
            )""",
            "CREATE INDEX IF NOT EXISTS idx_sessions_user  ON user_sessions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_token ON user_sessions(token_hash)",

            # Audit log
            """CREATE TABLE IF NOT EXISTS audit_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER REFERENCES Users(id),
                action          TEXT NOT NULL,
                resource_type   TEXT,
                resource_id     INTEGER,
                old_value       TEXT,
                new_value       TEXT,
                ip_address      TEXT,
                user_agent      TEXT,
                success         INTEGER DEFAULT 1,
                error_message   TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_audit_user     ON audit_logs(user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_audit_action   ON audit_logs(action, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_logs(resource_type, resource_id)",

            # Access control
            """CREATE TABLE IF NOT EXISTS access_control (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                resource_type   TEXT NOT NULL,
                resource_id     INTEGER NOT NULL,
                permission      TEXT NOT NULL,
                granted_by      INTEGER REFERENCES Users(id),
                granted_at      TEXT DEFAULT (datetime('now')),
                expires_at      TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_access_user ON access_control(user_id, resource_type)",

            # Security events
            """CREATE TABLE IF NOT EXISTS security_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type      TEXT NOT NULL,
                severity        TEXT NOT NULL,
                user_id         INTEGER REFERENCES Users(id),
                ip_address      TEXT,
                description     TEXT NOT NULL,
                metadata        TEXT,
                resolved        INTEGER DEFAULT 0,
                resolved_by     INTEGER REFERENCES Users(id),
                resolved_at     TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_security_events ON security_events(severity, resolved, created_at)",

            # Rate limiting
            """CREATE TABLE IF NOT EXISTS rate_limits (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER REFERENCES Users(id),
                ip_address      TEXT,
                endpoint        TEXT NOT NULL,
                window_start    TEXT DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_rate_limits ON rate_limits(user_id, endpoint, window_start)",

            # Phase 11 — WWFI Engine
            """CREATE TABLE IF NOT EXISTS wwfi_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                farm_id TEXT NOT NULL,
                plot_id TEXT,
                date TEXT NOT NULL,
                et_measured REAL NOT NULL,
                et_reference REAL NOT NULL,
                crop_coefficient REAL NOT NULL,
                pump_hours REAL NOT NULL,
                area_hectare REAL NOT NULL,
                actual_water REAL NOT NULL,
                optimal_water REAL NOT NULL,
                wwfi_score REAL NOT NULL,
                severity TEXT NOT NULL,
                pattern_type TEXT,
                pattern_confidence REAL,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_wwfi_farm_date ON wwfi_data(farm_id, date)",
            "CREATE INDEX IF NOT EXISTS idx_wwfi_severity ON wwfi_data(severity, date)",

            # ====================================================
            # Step 12 — Carbon-Sat V2.0 (GEE Carbon Monitoring)
            # ====================================================

            # Livestock CH4 / NH3 emissions per paddock
            """CREATE TABLE IF NOT EXISTS livestock_emissions (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                 INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                date                    TEXT    NOT NULL,
                estimated_heads         REAL    NOT NULL,
                livestock_density_index REAL,
                ch4_kg_daily            REAL    NOT NULL,
                nh3_kg_daily            REAL    NOT NULL,
                ch4_kg_annual           REAL    NOT NULL,
                nh3_kg_annual           REAL    NOT NULL,
                area_hectares           REAL,
                method                  TEXT    DEFAULT 'sentinel-2',
                created_at              TEXT    DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_livestock_user_date ON livestock_emissions(user_id, date)",

            # Crop emissions (rice methane, etc.)
            """CREATE TABLE IF NOT EXISTS crop_emissions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                crop_type       TEXT    NOT NULL,
                date            TEXT    NOT NULL,
                flooding_days   REAL,
                ch4_kg_total    REAL    NOT NULL,
                n2o_kg_total    REAL    DEFAULT 0,
                co2_kg_total    REAL    DEFAULT 0,
                area_hectares   REAL,
                emission_factor REAL,
                methodology     TEXT    DEFAULT 'IPCC-2019',
                created_at      TEXT    DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_crop_emissions_user_date ON crop_emissions(user_id, date)",

            # Soil carbon sequestration data
            """CREATE TABLE IF NOT EXISTS soil_carbon_data (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                     INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                date                        TEXT    NOT NULL,
                som_mg_kg                   REAL    NOT NULL,
                npp_gC_m2_year              REAL,
                soil_respiration_gC_m2_year REAL,
                ncs_gC_m2_year              REAL    NOT NULL,
                total_carbon_sequestered_kg REAL,
                carbon_status               TEXT,
                area_hectares               REAL,
                created_at                  TEXT    DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_soil_carbon_user_date ON soil_carbon_data(user_id, date)",

            # Barn thermal hotspots
            """CREATE TABLE IF NOT EXISTS barn_hotspots (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                 INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                date                    TEXT    NOT NULL,
                hotspot_count           INTEGER,
                mean_lst_celsius        REAL,
                lst_anomaly_celsius     REAL,
                emission_severity       TEXT,
                created_at              TEXT    DEFAULT (datetime('now'))
            )""",

            # Carbon credits ledger
            """CREATE TABLE IF NOT EXISTS carbon_credits (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                     INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                calculation_period_start    TEXT    NOT NULL,
                calculation_period_end      TEXT    NOT NULL,
                total_carbon_sequestered_kg REAL    NOT NULL,
                total_emissions_kg_co2_eq   REAL    NOT NULL,
                net_carbon_balance_kg       REAL    NOT NULL,
                credits_earned              REAL,
                credit_price_usd            REAL,
                total_value_usd             REAL,
                verification_status         TEXT    DEFAULT 'pending',
                created_at                  TEXT    DEFAULT (datetime('now'))
            )""",

            # GEE processing run logs
            """CREATE TABLE IF NOT EXISTS gee_processing_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                process_type    TEXT    NOT NULL,
                status          TEXT    NOT NULL,
                start_time      TEXT    DEFAULT (datetime('now')),
                end_time        TEXT,
                error_message   TEXT,
                metadata        TEXT
            )"""
        ]  # end migrations list
        for sql in migrations:
            try:
                conn.execute(sql)
            except Exception:
                pass  # column already exists — safe to skip

    print(f"[DB] Schema ready — {Config.DATABASE_PATH}")


# ── User helpers ─────────────────────────────────────────────

def create_user(name: str, phone: str, email: str, password_hash: str,
                farm_location: str, crop_type: str, area_feddan: float,
                lat: float, lon: float, farm_geojson: str = None, 
                farm_name: str = None) -> int:
    """Insert a new user. Returns the new user's id."""
    sql = """
        INSERT INTO Users
            (name, phone, email, password_hash, farm_location,
             crop_type, area_feddan, lat, lon, farm_geojson, farm_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with get_connection() as conn:
        cur = conn.execute(
            sql, (name, phone, email, password_hash,
                  farm_location, crop_type, area_feddan, lat, lon, farm_geojson, farm_name)
        )
        conn.commit()
        return cur.lastrowid


def get_user_by_email(email: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM Users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_phone(phone: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM Users WHERE phone = ?", (phone,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM Users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_telegram_chat_id(chat_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM Users WHERE telegram_chat_id = ?", (chat_id,)
        ).fetchone()
        return dict(row) if row else None


def link_telegram(user_id: int, telegram_chat_id: str) -> None:
    """Associate a Telegram chat ID with a registered user account."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE Users SET telegram_chat_id = ? WHERE id = ?",
            (telegram_chat_id, user_id),
        )
        conn.commit()


def update_user_profile(user_id: int, **kwargs) -> tuple[bool, str]:
    """
    Dynamically update any subset of user profile fields.
    Returns (True, "success msg") or (False, "error msg").
    """
    allowed = {
        "name", "phone", "email", "password_hash",
        "farm_location", "crop_type", "area_feddan",
        "lat", "lon", "farm_geojson",
        # V4.0 additions
        "farm_name", "pending_farm_name", "is_admin", "modules_access",
        # Irrigation device / well fields
        "irrigation_type", "pump_flow_rate", "pump_power_kw",
        "well_flow_rate", "well_depth", "pump_head_m",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False, "لا توجد بيانات للتحديث."

    with get_connection() as conn:
        # Check uniqueness for email
        if "email" in fields:
            existing = conn.execute(
                "SELECT id FROM Users WHERE email = ? AND id != ?",
                (fields["email"], user_id)
            ).fetchone()
            if existing:
                return False, "البريد الإلكتروني محجوز لحساب آخر."

        # Check uniqueness for phone
        if "phone" in fields:
            existing = conn.execute(
                "SELECT id FROM Users WHERE phone = ? AND id != ?",
                (fields["phone"], user_id)
            ).fetchone()
            if existing:
                return False, "رقم الهاتف محجوز لحساب آخر."

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [user_id]
        conn.execute(f"UPDATE Users SET {set_clause} WHERE id = ?", values)
        conn.commit()

    return True, "تم تحديث البيانات بنجاح."


# ── Farm-data helpers ─────────────────────────────────────────

def upsert_farm_data(user_id: int, date: str, **metrics) -> None:
    """
    Insert a new farm-data row for the given user/date, or update it
    if a row for that date already exists. All metric columns are optional.
    """
    allowed_metrics = {
        "ndvi", "lst", "lst_soil", "lst_canopy", "ndwi", "et", "biomass", "wue",
        "tvdi", "fapar", "fvc", "ndvi_cv", "thermal_inertia",
        "status", "pump_hours", "source",
    }
    fields = {k: v for k, v in metrics.items() if k in allowed_metrics}

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM Farm_Data WHERE user_id = ? AND date = ?",
            (user_id, date),
        ).fetchone()

        if existing:
            if not fields:
                return
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [user_id, date]
            conn.execute(
                f"UPDATE Farm_Data SET {set_clause} WHERE user_id = ? AND date = ?",
                values,
            )
        else:
            cols = ["user_id", "date"] + list(fields.keys())
            placeholders = ", ".join("?" * len(cols))
            values = [user_id, date] + list(fields.values())
            conn.execute(
                f"INSERT INTO Farm_Data ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
        conn.commit()


def get_latest_farm_data(user_id: int) -> sqlite3.Row | None:
    """Return the most recent farm-data row for a user."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM Farm_Data
               WHERE user_id = ?
               ORDER BY date DESC, id DESC
               LIMIT 1""",
            (user_id,),
        ).fetchone()


def get_farm_data_range(user_id: int, days: int = 30) -> list[sqlite3.Row]:
    """Return farm-data rows covering the last `days` calendar days."""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM Farm_Data
               WHERE user_id = ? AND date >= ?
               ORDER BY date ASC""",
            (user_id, since),
        ).fetchall()


def get_all_farm_data(user_id: int) -> list[sqlite3.Row]:
    """Return every farm-data row for a user (for CSV export)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM Farm_Data WHERE user_id = ? ORDER BY date ASC",
            (user_id,),
        ).fetchall()


# ── Chat-log helpers ──────────────────────────────────────────

def log_chat(user_id: int, channel: str, message: str, response: str) -> None:
    """Persist a single chatbot exchange to the database."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO Chat_Logs (user_id, channel, message, response)
               VALUES (?, ?, ?, ?)""",
            (user_id, channel, message, response),
        )
        conn.commit()


def get_chat_history(user_id: int, channel: str = None,
                     limit: int = 50) -> list[sqlite3.Row]:
    """
    Return the last `limit` chat messages for a user.
    Optionally filter by channel ('web', 'whatsapp', 'telegram').
    """
    with get_connection() as conn:
        if channel:
            return conn.execute(
                """SELECT * FROM Chat_Logs
                   WHERE user_id = ? AND channel = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (user_id, channel, limit),
            ).fetchall()
        else:
            return conn.execute(
                """SELECT * FROM Chat_Logs
                   WHERE user_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()


# ── Admin Panel Helpers ─────────────────────────────────────

def get_all_users() -> list[sqlite3.Row]:
    """Return all users for the Admin dashboard."""
    with get_connection() as conn:
        return conn.execute("SELECT * FROM Users ORDER BY created_at DESC").fetchall()

def approve_farm_name(user_id: int, reviewed_by: int = None) -> bool:
    """Approve a pending farm name for a user."""
    with get_connection() as conn:
        row = conn.execute("SELECT pending_farm_name, farm_name FROM Users WHERE id = ?", (user_id,)).fetchone()
        if not row or not row["pending_farm_name"]:
            return False

        new_name = row["pending_farm_name"]
        conn.execute(
            "UPDATE Users SET farm_name = ?, pending_farm_name = NULL WHERE id = ?",
            (new_name, user_id)
        )
        # Also update Pending_Changes log if exists
        conn.execute(
            """UPDATE Pending_Changes SET status = 'approved', reviewed_at = datetime('now'), reviewed_by = ?
               WHERE user_id = ? AND change_type = 'farm_name' AND status = 'pending'""",
            (reviewed_by, user_id)
        )
        conn.commit()
    return True

def reject_farm_name(user_id: int, reviewed_by: int = None) -> bool:
    """Reject and clear a pending farm name."""
    with get_connection() as conn:
        conn.execute("UPDATE Users SET pending_farm_name = NULL WHERE id = ?", (user_id,))
        conn.execute(
            """UPDATE Pending_Changes SET status = 'rejected', reviewed_at = datetime('now'), reviewed_by = ?
               WHERE user_id = ? AND change_type = 'farm_name' AND status = 'pending'""",
            (reviewed_by, user_id)
        )
        conn.commit()
    return True


# ── Pending Changes Helpers ───────────────────────────────────

def create_pending_change(user_id: int, change_type: str,
                          new_value: str, old_value: str = None) -> int:
    """Log a pending change request. Returns new row id."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO Pending_Changes (user_id, change_type, old_value, new_value)
               VALUES (?, ?, ?, ?)""",
            (user_id, change_type, old_value, new_value)
        )
        conn.commit()
        return cur.lastrowid


def get_pending_changes(status: str = 'pending') -> list[sqlite3.Row]:
    """Return all pending change requests (optionally filtered by status)."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT pc.*, u.name AS user_name, u.email AS user_email
               FROM Pending_Changes pc
               JOIN Users u ON pc.user_id = u.id
               WHERE pc.status = ?
               ORDER BY pc.created_at DESC""",
            (status,)
        ).fetchall()


# ── GEE Tile Cache Helpers ────────────────────────────────────

def store_tile_urls(user_id: int, tiles: dict) -> None:
    """
    Upsert GEE tile URLs for a user.
    `tiles` is the dict returned by generate_gee_tiles():
        {layer_name: tile_url, ..., '_date': '...', '_palettes': {...}}
    Tiles expire after 6 hours.
    """
    import json as _json
    import datetime as _dt

    skip_keys = {'_date', '_palettes'}
    date_str   = tiles.get('_date', '')
    palettes   = tiles.get('_palettes', {})
    expires    = (_dt.datetime.utcnow() + _dt.timedelta(hours=6)).isoformat()

    with get_connection() as conn:
        for layer, url in tiles.items():
            if layer in skip_keys or not url:
                continue
            vis_json = _json.dumps(palettes.get(layer, {}))
            conn.execute(
                """INSERT INTO gee_tile_cache
                       (user_id, layer_type, tile_url, vis_params, date_range_str, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, layer_type)
                   DO UPDATE SET
                       tile_url=excluded.tile_url,
                       vis_params=excluded.vis_params,
                       date_range_str=excluded.date_range_str,
                       generated_at=datetime('now'),
                       expires_at=excluded.expires_at""",
                (user_id, layer, url, vis_json, date_str, expires)
            )
        conn.commit()


def get_cached_tiles(user_id: int) -> dict:
    """
    Return a dict of {layer_type: tile_url} for the user,
    including only non-expired rows.
    Returns empty dict if no cache exists.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT layer_type, tile_url, vis_params, date_range_str
               FROM gee_tile_cache
               WHERE user_id = ?
                 AND (expires_at IS NULL OR expires_at > datetime('now'))
               ORDER BY generated_at DESC""",
            (user_id,)
        ).fetchall()

    if not rows:
        return {}

    result = {'_palettes': {}}
    import json as _json
    for row in rows:
        result[row['layer_type']] = row['tile_url']
        try:
            result['_palettes'][row['layer_type']] = _json.loads(row['vis_params'] or '{}')
        except Exception:
            pass
    return result


def get_all_active_farms() -> list[sqlite3.Row]:
    """Return all users who have lat/lon set (active farms for tile generation)."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT id, lat, lon, farm_geojson, farm_type
               FROM Users
               WHERE lat IS NOT NULL AND lon IS NOT NULL
                 AND lat != 0 AND lon != 0""",
        ).fetchall()




# ── Password hashing convenience ─────────────────────────────

def hash_password(plain_text: str) -> str:
    """Return a Werkzeug-compatible PBKDF2-SHA256 hash."""
    from werkzeug.security import generate_password_hash
    return generate_password_hash(plain_text, method="pbkdf2:sha256", salt_length=16)


def check_password(password_hash: str, plain_text: str) -> bool:
    """Verify a plain-text password against a stored hash."""
    from werkzeug.security import check_password_hash
    return check_password_hash(password_hash, plain_text)


# ── Self-test (run directly: python database.py) ──────────────

if __name__ == "__main__":
    init_db()
    print("[DB] All tables created successfully.")

# ── Chart Timeseries Helper ───────────────────────────────────

def get_farm_data_timeseries(user_id: int, days: int = 30,
                              columns: list = None) -> list:
    """
    Return the last `days` days of Farm_Data rows for a user, ascending by date.
    Only rows where ALL requested `columns` are non-null are returned.
    Returns a list of dicts with 'date' plus requested columns.
    """
    if columns is None:
        columns = ["ndvi", "et", "biomass", "tvdi", "lst", "pump_hours"]

    _safe = {
        "ndvi", "et", "biomass", "tvdi", "lst", "pump_hours",
        "ndwi", "lst_soil", "lst_canopy", "status", "date"
    }
    cols = [c for c in columns if c in _safe]
    if not cols:
        cols = ["ndvi"]

    select = ", ".join(["date"] + [c for c in cols if c != "date"])
    null_parts = [f"{c} IS NOT NULL" for c in cols if c != "date"]
    null_clause = ("AND (" + " AND ".join(null_parts) + ")") if null_parts else ""

    sql = f"""
        SELECT {select}
        FROM   Farm_Data
        WHERE  user_id = ?
               {null_clause}
          AND  date >= date('now', '-{int(days)} days')
        ORDER BY date ASC
        LIMIT 500
    """
    with get_connection() as conn:
        rows = conn.execute(sql, (user_id,)).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# Step 10 — Security DB Helpers
# ============================================================

# ── Whitelisted fields for update_user_field ─────────────────
_USER_SECURITY_FIELDS = frozenset({
    'failed_login_attempts', 'account_locked_until', 'last_login',
    'password_hash', 'password_changed_at', 'is_active',
    'two_factor_enabled', 'two_factor_secret', 'api_key',
})


def update_user_field(user_id: int, field: str, value) -> None:
    """
    Single-field update for security-related User columns.
    Only whitelisted fields are accepted (prevents SQL injection).
    """
    if field not in _USER_SECURITY_FIELDS:
        raise ValueError(f"[Security] Field '{field}' not in update whitelist")
    with get_connection() as conn:
        conn.execute(f"UPDATE Users SET {field} = ? WHERE id = ?", (value, user_id))
        conn.commit()


# ── JWT Session helpers ───────────────────────────────────────

def create_session(user_id: int, token_hash: str, ip_address: str,
                   user_agent: str, expires_at: str) -> None:
    """Persist a new JWT session record."""
    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO user_sessions
                   (user_id, token_hash, ip_address, user_agent, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, token_hash, ip_address, user_agent, expires_at),
        )
        conn.commit()


def get_session_by_hash(token_hash: str):
    """Return the user_sessions row matching this token hash, or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM user_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
    return dict(row) if row else None


def revoke_session(token_hash: str) -> None:
    """Mark a single session as revoked."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE user_sessions SET revoked = 1 WHERE token_hash = ?",
            (token_hash,),
        )
        conn.commit()


def revoke_all_user_sessions(user_id: int) -> None:
    """Revoke all active sessions for a user (e.g. after password change)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE user_sessions SET revoked = 1 WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()


# ── Audit log helpers ─────────────────────────────────────────

def create_audit_log(user_id, action: str, resource_type=None, resource_id=None,
                     old_value=None, new_value=None, ip_address=None,
                     user_agent=None, success: int = 1,
                     error_message=None) -> None:
    """Insert a row into audit_logs."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO audit_logs
                   (user_id, action, resource_type, resource_id,
                    old_value, new_value, ip_address, user_agent,
                    success, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, action, resource_type, resource_id,
             old_value, new_value, ip_address, user_agent,
             success, error_message),
        )
        conn.commit()


def get_audit_logs(days: int = 30, user_id: int = None,
                   action: str = None, limit: int = 200) -> list:
    """Return audit_logs rows, newest first, with optional filters."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conditions = ["created_at >= ?"]
    params     = [since]

    if user_id:
        conditions.append("user_id = ?")
        params.append(user_id)
    if action:
        conditions.append("action = ?")
        params.append(action)

    where = " AND ".join(conditions)
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM audit_logs WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# ── Security event helpers ────────────────────────────────────

def create_security_event(event_type: str, severity: str, user_id=None,
                           ip_address=None, description: str = '',
                           metadata=None) -> None:
    """Insert a row into security_events."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO security_events
                   (event_type, severity, user_id, ip_address,
                    description, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_type, severity, user_id, ip_address, description, metadata),
        )
        conn.commit()


def get_security_events(severity: str = None, resolved: bool = None,
                        limit: int = 100) -> list:
    """Return security_events rows with optional filters."""
    conditions = []
    params     = []

    if severity:
        conditions.append("severity = ?")
        params.append(severity)
    if resolved is not None:
        conditions.append("resolved = ?")
        params.append(1 if resolved else 0)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM security_events {where} "
            f"ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_security_event(event_id: int, resolved_by: int) -> None:
    """Mark a security event as resolved."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE security_events
               SET resolved = 1, resolved_by = ?, resolved_at = datetime('now')
               WHERE id = ?""",
            (resolved_by, event_id),
        )
        conn.commit()


# ── Rate limit helpers ────────────────────────────────────────

def get_rate_limit_count(user_id, ip_address: str,
                         endpoint: str, window_start: str) -> int:
    """Count requests in the current sliding window."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM rate_limits
               WHERE endpoint = ?
                 AND window_start >= ?
                 AND (user_id = ? OR ip_address = ?)""",
            (endpoint, window_start, user_id, ip_address),
        ).fetchone()
    return (row['cnt'] if row else 0)


def increment_rate_limit(user_id, ip_address: str, endpoint: str) -> None:
    """Insert a rate_limits entry for the current request."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO rate_limits (user_id, ip_address, endpoint) VALUES (?, ?, ?)",
            (user_id, ip_address, endpoint),
        )
        conn.commit()


def cleanup_rate_limits(older_than_minutes: int = 120) -> None:
    """Delete rate_limit rows older than `older_than_minutes` (periodic GC)."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM rate_limits WHERE window_start < datetime('now', ? || ' minutes')",
            (f'-{older_than_minutes}',),
        )
        conn.commit()


# ── Access control helpers ────────────────────────────────────

def get_access_control(user_id: int, resource_type: str, resource_id) -> dict | None:
    """Return the access_control row for user+resource, or None."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM access_control
               WHERE user_id = ? AND resource_type = ? AND resource_id = ?
               ORDER BY granted_at DESC LIMIT 1""",
            (user_id, resource_type, resource_id),
        ).fetchone()
    return dict(row) if row else None


def grant_access(user_id: int, resource_type: str, resource_id: int,
                 permission: str, granted_by: int = None,
                 expires_at: str = None) -> None:
    """Grant explicit access to a resource."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO access_control
                   (user_id, resource_type, resource_id,
                    permission, granted_by, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, resource_type, resource_id,
             permission, granted_by, expires_at),
        )
        conn.commit()


# ── Role helpers ──────────────────────────────────────────────

def get_role_by_id(role_id: int) -> dict | None:
    """Return a roles row as dict, or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM roles WHERE id = ?", (role_id,)
        ).fetchone()
    return dict(row) if row else None


def get_all_roles() -> list:
    """Return all roles."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM roles ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# ── Security admin helpers ────────────────────────────────────

def toggle_user_active(user_id: int, is_active: bool) -> None:
    """Enable or disable a user account."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE Users SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, user_id),
        )
        conn.commit()


def unlock_user_account(user_id: int) -> None:
    """Manually unlock a locked account and reset failed attempts."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE Users
               SET account_locked_until = NULL, failed_login_attempts = 0
               WHERE id = ?""",
            (user_id,),
        )
        conn.commit()


# ── Step 10 Report Log Helpers (Step 9) ───────────────────────


def log_report(user_id: int, report_type: str, fmt: str,
               filename: str, filepath: str,
               date_range_days: int = 30,
               emailed_to: str = None) -> int:
    """
    Persist a report generation event to report_logs.
    Returns the new row id.
    """
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO report_logs
                   (user_id, report_type, format, filename, filepath,
                    date_range_days, emailed_to)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, report_type, fmt, filename, filepath,
             date_range_days, emailed_to),
        )
        conn.commit()
        return cur.lastrowid


def get_user_reports(user_id: int, limit: int = 20) -> list:
    """Return the most recent `limit` report_logs rows for a user."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, report_type, format, filename, filepath,
                      date_range_days, generated_at, emailed_to
               FROM   report_logs
               WHERE  user_id = ?
               ORDER  BY generated_at DESC
               LIMIT  ?""",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_report_downloaded(report_id: int) -> None:
    """Stamp downloaded_at on a report_logs row."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE report_logs SET downloaded_at = datetime('now') WHERE id = ?",
            (report_id,),
        )
        conn.commit()


if __name__ == "__main__":

    # Quick smoke-test
    test_hash = hash_password("test123")

    try:
        uid = create_user(
            name="Test Farmer",
            phone="+201001234567",
            email="test@farm.eg",
            password_hash=test_hash,
            farm_location="الفيوم، مصر",
            crop_type="wheat",
            area_feddan=50.0,
            lat=29.3084,
            lon=30.8428,
            farm_name="مزرعة الاختبار"
        )
        print(f"[DB] Created test user id={uid}")
        
        # Test admin powers
        update_user_profile(uid, is_admin=1)
        
        upsert_farm_data(uid, datetime.utcnow().strftime("%Y-%m-%d"),
                         ndvi=0.72, lst=35.5, et=5.8, biomass=12.4,
                         status="healthy", pump_hours=3.2, source="physics")
        row = get_latest_farm_data(uid)
        print(f"[DB] Latest farm data: NDVI={row['ndvi']}, ET={row['et']}")
        print("[DB] Smoke-test passed ✓")
    except sqlite3.IntegrityError:
        print("[DB] Smoke-test skipped (user already exists)")


# ============================================================
# WhatsApp helpers
# ============================================================

def update_whatsapp_optin(user_id: int, phone: str = None, verified: bool = False,
                          opted_in: bool = True) -> None:
    """Update WhatsApp opt-in status and optionally the phone number."""
    with get_connection() as conn:
        if phone:
            conn.execute(
                "UPDATE Users SET whatsapp_verified = ?, whatsapp_opt_in = ?, phone = ? WHERE id = ?",
                (1 if verified else 0, 1 if opted_in else 0, phone, user_id),
            )
        else:
            conn.execute(
                "UPDATE Users SET whatsapp_verified = ?, whatsapp_opt_in = ? WHERE id = ?",
                (1 if verified else 0, 1 if opted_in else 0, user_id),
            )
        conn.commit()


def get_whatsapp_opted_in_users() -> list:
    """Return all users who have opted in for WhatsApp updates."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, name, phone, farm_name, farm_location
               FROM Users
               WHERE whatsapp_opt_in = 1 AND phone IS NOT NULL AND phone != ''"""
        ).fetchall()
        return [dict(r) for r in rows]


def store_whatsapp_message(user_id: int, incoming: str, outgoing: str,
                           from_number: str = "") -> None:
    """Log a WhatsApp conversation turn in Chat_Logs (channel='whatsapp')."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO Chat_Logs (user_id, channel, message, response)
               VALUES (?, 'whatsapp', ?, ?)""",
            (user_id, incoming[:4000], outgoing[:4000]),
        )
        conn.commit()


def get_whatsapp_history(user_id: int, limit: int = 20) -> list:
    """Return the most recent WhatsApp chat log entries for a user."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT message AS user_msg, response AS bot_msg, timestamp
               FROM Chat_Logs
               WHERE user_id = ? AND channel = 'whatsapp'
               ORDER BY timestamp DESC
               LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# Telegram helpers
# ============================================================

def link_telegram(user_id: int, telegram_chat_id: str,
                  telegram_username: str = "") -> None:
    """Link or update a Telegram chat ID for a user (also sets opt-in)."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE Users
               SET telegram_chat_id = ?, telegram_username = ?,
                   telegram_verified = 1, telegram_opt_in = 1
               WHERE id = ?""",
            (str(telegram_chat_id), telegram_username or "", user_id),
        )
        conn.commit()


def link_telegram_by_email(email: str, telegram_chat_id: str,
                           telegram_username: str = "") -> bool:
    """Find user by email and link their Telegram. Returns True on success."""
    user = get_user_by_email(email)
    if not user:
        return False
    link_telegram(user["id"], telegram_chat_id, telegram_username)
    return True


def get_telegram_opted_in_users() -> list:
    """Return users who have opted in for Telegram updates."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, name, telegram_chat_id, farm_name
               FROM Users
               WHERE telegram_opt_in = 1
                 AND telegram_chat_id IS NOT NULL
                 AND telegram_chat_id != ''"""
        ).fetchall()
        return [dict(r) for r in rows]


def store_telegram_message(user_id: int, incoming: str, outgoing: str) -> None:
    """Log a Telegram conversation turn in Chat_Logs (channel='telegram')."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO Chat_Logs (user_id, channel, message, response)
               VALUES (?, 'telegram', ?, ?)""",
            (user_id, incoming[:4000], outgoing[:4000]),
        )
        conn.commit()


def get_telegram_history(user_id: int, limit: int = 20) -> list:
    """Return recent Telegram chat log entries for a user."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT message AS user_msg, response AS bot_msg, timestamp
               FROM Chat_Logs
               WHERE user_id = ? AND channel = 'telegram'
               ORDER BY timestamp DESC
               LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# Email helpers
# ============================================================

def log_email(user_id: int, recipient: str, subject: str,
              template: str = None, message_id: str = None,
              status: str = "sent", error: str = None) -> None:
    """Record a sent email in email_logs."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO email_logs
               (user_id, recipient_email, subject, template_name, message_id, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, recipient, subject, template, message_id, status, error),
        )
        conn.commit()


def get_email_preferences(user_id: int) -> dict:
    """Get or create email preferences for a user."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM email_preferences WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT OR IGNORE INTO email_preferences (user_id) VALUES (?)", (user_id,)
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM email_preferences WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else {}


def save_email_preferences(user_id: int, prefs: dict) -> None:
    """Upsert email preferences."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO email_preferences (user_id, daily_updates, weekly_reports,
               critical_alerts, preferred_time)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
               daily_updates   = excluded.daily_updates,
               weekly_reports  = excluded.weekly_reports,
               critical_alerts = excluded.critical_alerts,
               preferred_time  = excluded.preferred_time""",
            (
                user_id,
                1 if prefs.get("daily_updates", True)   else 0,
                1 if prefs.get("weekly_reports", True)  else 0,
                1 if prefs.get("critical_alerts", True) else 0,
                prefs.get("preferred_time", "07:00"),
            ),
        )
        conn.commit()


def get_opted_in_email_users(pref_col: str = "daily_updates") -> list:
    """Return users with a given email preference enabled and an email address."""
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT u.id, u.name, u.email, u.farm_name, u.unsubscribe_token,
                       ep.preferred_time
                FROM Users u
                LEFT JOIN email_preferences ep ON ep.user_id = u.id
                WHERE u.email IS NOT NULL AND u.email != ''
                  AND (ep.{pref_col} IS NULL OR ep.{pref_col} = 1)"""
        ).fetchall()
        return [dict(r) for r in rows]


# ── Host inbox helpers ─────────────────────────────────────────

def store_inbox_message(from_user_id: int, subject: str, message: str,
                        priority: str = "normal") -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO host_inbox (from_user_id, subject, message, priority)
               VALUES (?, ?, ?, ?)""",
            (from_user_id, subject, message, priority),
        )
        conn.commit()
        return cur.lastrowid


def get_inbox_messages(status: str = "all", priority: str = None) -> list:
    """Return inbox messages, optionally filtered by status."""
    with get_connection() as conn:
        if status in ("unread", "read", "replied", "archived"):
            rows = conn.execute(
                """SELECT hi.*, u.name AS from_user_name, u.email AS from_email,
                          u.farm_name
                   FROM host_inbox hi
                   LEFT JOIN Users u ON u.id = hi.from_user_id
                   WHERE hi.status = ?
                   ORDER BY hi.created_at DESC LIMIT 200""",
                (status,),
            ).fetchall()
        elif status == "urgent":
            rows = conn.execute(
                """SELECT hi.*, u.name AS from_user_name, u.email AS from_email,
                          u.farm_name
                   FROM host_inbox hi
                   LEFT JOIN Users u ON u.id = hi.from_user_id
                   WHERE hi.priority = 'urgent'
                   ORDER BY hi.created_at DESC LIMIT 200"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT hi.*, u.name AS from_user_name, u.email AS from_email,
                          u.farm_name
                   FROM host_inbox hi
                   LEFT JOIN Users u ON u.id = hi.from_user_id
                   ORDER BY hi.created_at DESC LIMIT 200"""
            ).fetchall()
        return [dict(r) for r in rows]


def get_inbox_message(msg_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT hi.*, u.name AS from_user_name, u.email AS from_email,
                      u.farm_name
               FROM host_inbox hi
               LEFT JOIN Users u ON u.id = hi.from_user_id
               WHERE hi.id = ?""",
            (msg_id,),
        ).fetchone()
        return dict(row) if row else {}


def mark_inbox_read(msg_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE host_inbox SET status='read', read_at=datetime('now') WHERE id=? AND status='unread'",
            (msg_id,),
        )
        conn.commit()


def mark_inbox_replied(msg_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE host_inbox SET status='replied', replied_at=datetime('now') WHERE id=?",
            (msg_id,),
        )
        conn.commit()


def get_unread_inbox_count() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM host_inbox WHERE status='unread'"
        ).fetchone()
        return row["cnt"] if row else 0


# ============================================================
# Step 8 — Farm Plots CRUD (3D Drawing Tool)
# ============================================================

def save_farm_plots(user_id: int, plots: list) -> dict:
    """
    Bulk-save all drawn plots for a user.
    Replaces previous active plots. Returns {saved, deleted} counts.

    Parameters
    ----------
    user_id : int
    plots   : list of dicts with keys:
        plot_name, plot_type, geometry (GeoJSON str), center_lat, center_lon,
        area_m2, area_feddan, area_hectare, crop_type, sector, region,
        soil_type, planting_date, notes
    """
    with get_connection() as conn:
        # Soft-delete previous plots
        deleted = conn.execute(
            "UPDATE farm_plots SET active=0, updated_at=datetime('now') WHERE user_id=? AND active=1",
            (user_id,)
        ).rowcount

        saved = 0
        for p in plots:
            geo = p.get("geometry", "")
            if isinstance(geo, dict):
                import json as _json
                geo = _json.dumps(geo)

            conn.execute("""
                INSERT INTO farm_plots
                    (user_id, plot_type, plot_name, geometry, center_lat, center_lon,
                     area_m2, area_feddan, area_hectare, crop_type, sector, region,
                     soil_type, planting_date, notes, active, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,datetime('now'),datetime('now'))
            """, (
                user_id,
                p.get("plot_type", "plot"),
                p.get("plot_name", "Unnamed"),
                geo,
                p.get("center_lat"),
                p.get("center_lon"),
                p.get("area_m2", 0),
                p.get("area_feddan", 0),
                p.get("area_hectare", 0),
                p.get("crop_type"),
                p.get("sector"),
                p.get("region"),
                p.get("soil_type"),
                p.get("planting_date"),
                p.get("notes"),
            ))

            # Log history
            last_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            conn.execute("""
                INSERT INTO plot_history (plot_id, change_type, new_data, changed_by)
                VALUES (?, 'created', ?, ?)
            """, (last_id, geo, user_id))
            saved += 1

        conn.commit()
    return {"saved": saved, "deleted": deleted}


def get_farm_plots(user_id: int) -> list:
    """Return all active plots for a user, ordered by creation date."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, plot_type, plot_name, geometry, center_lat, center_lon,
                   area_m2, area_feddan, area_hectare, crop_type, crop_variety,
                   sector, region, soil_type, planting_date, notes, created_at
            FROM farm_plots
            WHERE user_id=? AND active=1
            ORDER BY created_at ASC
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]


def get_farm_plot(plot_id: int) -> dict | None:
    """Return a single plot by id."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM farm_plots WHERE id=? AND active=1", (plot_id,)
        ).fetchone()
        return dict(row) if row else None


def update_farm_plot(plot_id: int, user_id: int, data: dict) -> bool:
    """
    Update metadata fields of an existing plot.
    Returns True if a row was modified.
    """
    allowed = [
        "plot_name", "crop_type", "crop_variety", "sector", "region",
        "soil_type", "planting_date", "notes",
    ]
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return False

    cols = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [plot_id, user_id]

    with get_connection() as conn:
        # Snapshot old data for history
        old = conn.execute("SELECT * FROM farm_plots WHERE id=? AND user_id=?",
                           (plot_id, user_id)).fetchone()
        if not old:
            return False

        conn.execute(
            f"UPDATE farm_plots SET {cols}, updated_at=datetime('now') WHERE id=? AND user_id=?",
            vals,
        )
        conn.execute("""
            INSERT INTO plot_history (plot_id, change_type, old_data, new_data, changed_by)
            VALUES (?, 'updated', ?, ?, ?)
        """, (plot_id, dict(old).__str__(), str(data), user_id))
        conn.commit()
    return True


def delete_farm_plot(plot_id: int, user_id: int) -> bool:
    """Soft-delete a single named plot. Returns True on success."""
    with get_connection() as conn:
        old = conn.execute("SELECT * FROM farm_plots WHERE id=? AND user_id=?",
                           (plot_id, user_id)).fetchone()
        if not old:
            return False

        conn.execute(
            "UPDATE farm_plots SET active=0, updated_at=datetime('now') WHERE id=? AND user_id=?",
            (plot_id, user_id)
        )
        conn.execute("""
            INSERT INTO plot_history (plot_id, change_type, old_data, changed_by)
            VALUES (?, 'deleted', ?, ?)
        """, (plot_id, str(dict(old)), user_id))
        conn.commit()
    return True


def delete_all_farm_plots(user_id: int) -> int:
    """Soft-delete ALL active plots for a user. Returns deleted count."""
    with get_connection() as conn:
        n = conn.execute(
            "UPDATE farm_plots SET active=0, updated_at=datetime('now') WHERE user_id=? AND active=1",
            (user_id,)
        ).rowcount
        conn.commit()
    return n


def get_crop_library() -> list:
    """Return all crops from the library, ordered by category."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM crop_library ORDER BY category, crop_name_ar"
        ).fetchall()
        return [dict(r) for r in rows]


def get_farm_plots_summary(user_id: int) -> dict:
    """
    Return a summary dict for dashboard widgets:
    total plots, pivot count, area totals, crop breakdown.
    """
    with get_connection() as conn:
        plots = conn.execute("""
            SELECT plot_type, crop_type, area_m2, area_feddan, area_hectare
            FROM farm_plots WHERE user_id=? AND active=1
        """, (user_id,)).fetchall()

        total_plots   = len(plots)
        pivots        = sum(1 for p in plots if p["plot_type"] == "pivot")
        fields        = total_plots - pivots
        total_m2      = sum(p["area_m2"]      or 0 for p in plots)
        total_feddan  = sum(p["area_feddan"]   or 0 for p in plots)
        total_hectare = sum(p["area_hectare"]  or 0 for p in plots)

        # Crop breakdown
        crop_counts: dict = {}
        for p in plots:
            crop = p["crop_type"] or "غير محدد"
            crop_counts[crop] = crop_counts.get(crop, 0) + 1

        return {
            "total_plots":   total_plots,
            "pivot_count":   pivots,
            "field_count":   fields,
            "total_m2":      round(total_m2, 1),
            "total_feddan":  round(total_feddan, 2),
            "total_hectare": round(total_hectare, 2),
            "crop_breakdown": crop_counts,
        }

# ============================================================
# Phase 11 — WWFI Engine Queries
# ============================================================

def save_wwfi_data(farm_id: str, date: str, et_measured: float, et_reference: float,
                   crop_coefficient: float, pump_hours: float, area_hectare: float,
                   actual_water: float, optimal_water: float, wwfi_score: float,
                   severity: str, pattern_type: str = None,
                   pattern_confidence: float = 0.0, plot_id: str = None) -> None:
    """Save or update the WWFI standard record for a given farm/plot correctly."""
    with get_connection() as conn:
        # Avoid duplicates on same day
        existing = conn.execute(
            "SELECT id FROM wwfi_data WHERE farm_id=? AND date=? AND IFNULL(plot_id,'')=?",
            (farm_id, date, plot_id or '')
        ).fetchone()
        
        if existing:
            conn.execute("""
                UPDATE wwfi_data
                SET et_measured=?, et_reference=?, crop_coefficient=?, pump_hours=?,
                    area_hectare=?, actual_water=?, optimal_water=?, wwfi_score=?,
                    severity=?, pattern_type=?, pattern_confidence=?
                WHERE id=?
            """, (et_measured, et_reference, crop_coefficient, pump_hours,
                  area_hectare, actual_water, optimal_water, wwfi_score, severity,
                  pattern_type, pattern_confidence, existing['id']))
        else:
            conn.execute("""
                INSERT INTO wwfi_data (
                    farm_id, plot_id, date, et_measured, et_reference, crop_coefficient,
                    pump_hours, area_hectare, actual_water, optimal_water,
                    wwfi_score, severity, pattern_type, pattern_confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (farm_id, plot_id, date, et_measured, et_reference, crop_coefficient,
                  pump_hours, area_hectare, actual_water, optimal_water,
                  wwfi_score, severity, pattern_type, pattern_confidence))
        conn.commit()

def get_wwfi_history(farm_id: str, days: int = 7, plot_id: str = None) -> list:
    """Return historical WWFI records for pattern analysis."""
    start_date_str = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    with get_connection() as conn:
        if plot_id:
            rows = conn.execute("""
                SELECT * FROM wwfi_data 
                WHERE farm_id=? AND plot_id=? AND date >= ?
                ORDER BY date ASC
            """, (farm_id, plot_id, start_date_str)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM wwfi_data 
                WHERE farm_id=? AND date >= ?
                ORDER BY date ASC
            """, (farm_id, start_date_str)).fetchall()
        
        return [dict(r) for r in rows]


# ============================================================
# Step 12 — Carbon-Sat V2.0 Helper Functions
# ============================================================

def save_livestock_emissions(
    user_id: int,
    date: str,
    estimated_heads: float,
    ch4_kg_daily: float,
    nh3_kg_daily: float,
    ch4_kg_annual: float,
    nh3_kg_annual: float,
    livestock_density: float = None,
    area_hectares: float = None,
) -> int:
    """
    Insert or upsert a livestock emissions record.
    Returns the new row id.
    """
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM livestock_emissions WHERE user_id = ? AND date = ?",
            (user_id, date),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE livestock_emissions
                   SET estimated_heads=?, livestock_density_index=?,
                       ch4_kg_daily=?, nh3_kg_daily=?,
                       ch4_kg_annual=?, nh3_kg_annual=?, area_hectares=?
                   WHERE id=?""",
                (
                    estimated_heads, livestock_density,
                    ch4_kg_daily, nh3_kg_daily,
                    ch4_kg_annual, nh3_kg_annual,
                    area_hectares, existing["id"],
                ),
            )
            conn.commit()
            return existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO livestock_emissions
                       (user_id, date, estimated_heads, livestock_density_index,
                        ch4_kg_daily, nh3_kg_daily, ch4_kg_annual, nh3_kg_annual,
                        area_hectares)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, date, estimated_heads, livestock_density,
                    ch4_kg_daily, nh3_kg_daily, ch4_kg_annual, nh3_kg_annual,
                    area_hectares,
                ),
            )
            conn.commit()
            return cur.lastrowid


def save_crop_emissions(
    user_id: int,
    crop_type: str,
    date: str,
    flooding_days: float,
    ch4_kg_total: float,
    area_hectares: float = None,
) -> int:
    """Insert a crop emissions record. Returns row id."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO crop_emissions
                   (user_id, crop_type, date, flooding_days, ch4_kg_total, area_hectares)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, crop_type, date, flooding_days, ch4_kg_total, area_hectares),
        )
        conn.commit()
        return cur.lastrowid


def save_soil_carbon(
    user_id: int,
    date: str,
    som_mg_kg: float,
    ncs_gC_m2_year: float,
    total_carbon_sequestered_kg: float,
    area_hectares: float = None,
    npp: float = None,
    respiration: float = None,
) -> int:
    """
    Insert or upsert a soil carbon record.
    Returns the row id.
    """
    carbon_status = "Sink" if ncs_gC_m2_year > 0 else "Source"
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM soil_carbon_data WHERE user_id = ? AND date = ?",
            (user_id, date),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE soil_carbon_data
                   SET som_mg_kg=?, npp_gC_m2_year=?, soil_respiration_gC_m2_year=?,
                       ncs_gC_m2_year=?, total_carbon_sequestered_kg=?,
                       carbon_status=?, area_hectares=?
                   WHERE id=?""",
                (
                    som_mg_kg, npp, respiration, ncs_gC_m2_year,
                    total_carbon_sequestered_kg, carbon_status,
                    area_hectares, existing["id"],
                ),
            )
            conn.commit()
            return existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO soil_carbon_data
                       (user_id, date, som_mg_kg, npp_gC_m2_year,
                        soil_respiration_gC_m2_year, ncs_gC_m2_year,
                        total_carbon_sequestered_kg, carbon_status, area_hectares)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, date, som_mg_kg, npp, respiration,
                    ncs_gC_m2_year, total_carbon_sequestered_kg,
                    carbon_status, area_hectares,
                ),
            )
            conn.commit()
            return cur.lastrowid


def get_livestock_history(user_id: int, days: int = 30) -> list:
    """Return livestock emission records for the last `days` days, newest first."""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM livestock_emissions
               WHERE user_id = ? AND date >= ?
               ORDER BY date DESC""",
            (user_id, since),
        ).fetchall()
    return [dict(r) for r in rows]


def get_soil_carbon_history(user_id: int, days: int = 30) -> list:
    """Return soil carbon records for the last `days` days, newest first."""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM soil_carbon_data
               WHERE user_id = ? AND date >= ?
               ORDER BY date DESC""",
            (user_id, since),
        ).fetchall()
    return [dict(r) for r in rows]


def get_carbon_summary(user_id: int, period_days: int = 365) -> dict:
    """
    Aggregate carbon metrics for a user over the given period.

    Returns a dict with three sub-dicts:
      livestock → {ch4_kg, nh3_kg, avg_heads}
      crops     → {ch4_kg, flooding_days}
      soil      → {som_mg_kg, ncs_gC_m2_year, total_sequestered_kg}
    """
    since = (datetime.utcnow() - timedelta(days=period_days)).strftime("%Y-%m-%d")

    with get_connection() as conn:
        lv = conn.execute(
            """SELECT
                   SUM(ch4_kg_annual)   AS total_ch4_livestock,
                   SUM(nh3_kg_annual)   AS total_nh3_livestock,
                   AVG(estimated_heads) AS avg_livestock
               FROM livestock_emissions
               WHERE user_id = ? AND date >= ?""",
            (user_id, since),
        ).fetchone()

        cr = conn.execute(
            """SELECT
                   SUM(ch4_kg_total)  AS total_ch4_crops,
                   SUM(flooding_days) AS total_flooding_days
               FROM crop_emissions
               WHERE user_id = ? AND date >= ?""",
            (user_id, since),
        ).fetchone()

        so = conn.execute(
            """SELECT
                   AVG(som_mg_kg)                   AS avg_som,
                   AVG(ncs_gC_m2_year)              AS avg_ncs,
                   SUM(total_carbon_sequestered_kg) AS total_sequestered
               FROM soil_carbon_data
               WHERE user_id = ? AND date >= ?""",
            (user_id, since),
        ).fetchone()

    return {
        "livestock": {
            "ch4_kg":    round(float(lv["total_ch4_livestock"] or 0), 2),
            "nh3_kg":    round(float(lv["total_nh3_livestock"] or 0), 2),
            "avg_heads": round(float(lv["avg_livestock"]       or 0), 1),
        },
        "crops": {
            "ch4_kg":        round(float(cr["total_ch4_crops"]     or 0), 2),
            "flooding_days": round(float(cr["total_flooding_days"]  or 0), 1),
        },
        "soil": {
            "som_mg_kg":             round(float(so["avg_som"]           or 0), 2),
            "ncs_gC_m2_year":        round(float(so["avg_ncs"]           or 0), 2),
            "total_sequestered_kg":  round(float(so["total_sequestered"]  or 0), 2),
        },
    }


# ============================================================
# CM60 Pivot Telemetry Helpers
# ============================================================

def save_pivot_telemetry(pivot_id: str, data: dict) -> int:
    """Save live CM60 telemetry packet for a pivot."""
    import json
    from datetime import datetime
    towers = data.get("towers", [])
    if isinstance(towers, str):
        towers_json = towers
    else:
        towers_json = json.dumps(towers, ensure_ascii=False)

    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO pivot_telemetry
               (pivot_id, timestamp, overall_status, health_color, fault_message,
                center_pressure_psi, speed_percent, direction, towers_json, raw_payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(pivot_id),
                data.get("timestamp") or datetime.utcnow().isoformat(),
                data.get("overall_status", "Running"),
                data.get("health_color", "green"),
                data.get("fault_message", ""),
                float(data.get("center_pressure_psi", 40.0)),
                int(data.get("speed_percent", 50)),
                data.get("direction", "FWR"),
                towers_json,
                data.get("raw_payload", ""),
            )
        )
        conn.commit()
        return cur.lastrowid


def get_latest_pivot_telemetry(pivot_id: str, max_age_hours: int = 12) -> dict | None:
    """Retrieve the latest valid IoT telemetry packet for a pivot if within max_age_hours."""
    import json
    from datetime import datetime
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM pivot_telemetry
               WHERE pivot_id = ?
               ORDER BY id DESC LIMIT 1""",
            (str(pivot_id),)
        ).fetchone()

        if not row:
            return None

        # Check freshness if timestamp is parseable
        try:
            ts_str = row["timestamp"].replace("Z", "")
            ts = datetime.fromisoformat(ts_str)
            age = (datetime.utcnow() - ts).total_seconds() / 3600.0
            if age > max_age_hours:
                return None
        except Exception:
            pass

        towers = []
        try:
            towers = json.loads(row["towers_json"])
        except Exception:
            towers = []

        return {
            "pivot_id":            row["pivot_id"],
            "timestamp":           row["timestamp"],
            "overall_status":      row["overall_status"],
            "health_color":        row["health_color"],
            "fault_message":       row["fault_message"] or "",
            "center_pressure_psi": row["center_pressure_psi"],
            "speed_percent":       row["speed_percent"],
            "direction":           row["direction"],
            "towers":              towers,
            "telemetry_source":    "LIVE_IOT",
            "is_simulated":        False,
        }
