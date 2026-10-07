#!/usr/bin/env python3
# verify_db.py - Database inspection for production acceptance
import sqlite3, os, sys, json

sys.stdout.reconfigure(encoding='utf-8')

db_path = 'tayseer_agrisat.db'
print(f"DB path: {os.path.abspath(db_path)}")
print(f"DB exists: {os.path.exists(db_path)}")
if os.path.exists(db_path):
    print(f"DB size: {os.path.getsize(db_path)} bytes")

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Check foreign keys pragma
cur.execute("PRAGMA foreign_keys")
fk = cur.fetchone()[0]
print(f"Foreign keys enforced: {bool(fk)}")

# Check WAL mode
cur.execute("PRAGMA journal_mode")
jm = cur.fetchone()[0]
print(f"Journal mode: {jm}")

# List tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print(f"\nTables ({len(tables)}):")
for t in tables:
    try:
        cur.execute(f"SELECT COUNT(*) FROM [{t}]")
        cnt = cur.fetchone()[0]
        print(f"  {t}: {cnt} rows")
    except Exception as e:
        print(f"  {t}: ERROR {e}")

# Check Users table schema
print("\n--- Users table sample (masked) ---")
try:
    cur.execute("SELECT id, name, email, is_admin, is_verified, farm_name, lat, lon FROM Users LIMIT 5")
    rows = cur.fetchall()
    for r in rows:
        print(dict(r))
except Exception as e:
    print(f"  Users ERROR: {e}")

# Check Farm_Data
print("\n--- Farm_Data sample ---")
try:
    cur.execute("SELECT id, user_id, date, ndvi, lst, source, status FROM Farm_Data ORDER BY id DESC LIMIT 5")
    rows = cur.fetchall()
    for r in rows:
        print(dict(r))
except Exception as e:
    print(f"  Farm_Data ERROR: {e}")

# Check Pivot_Telemetry if exists
print("\n--- Pivot_Telemetry ---")
if 'Pivot_Telemetry' in tables:
    cur.execute("SELECT COUNT(*) FROM Pivot_Telemetry")
    cnt = cur.fetchone()[0]
    print(f"  Pivot_Telemetry: {cnt} rows")
    if cnt > 0:
        cur.execute("SELECT * FROM Pivot_Telemetry LIMIT 3")
        for r in cur.fetchall():
            print(dict(r))
else:
    print("  Pivot_Telemetry table NOT FOUND")

# Check RAG chunks
print("\n--- RAG/Document chunks ---")
for t in ['rag_chunks', 'rag_documents', 'RAG_Chunks', 'Documents', 'documents']:
    if t in tables:
        cur.execute(f"SELECT COUNT(*) FROM [{t}]")
        cnt = cur.fetchone()[0]
        print(f"  {t}: {cnt} rows")
        if cnt > 0:
            cur.execute(f"SELECT * FROM [{t}] LIMIT 1")
            r = cur.fetchone()
            print(f"  Sample: {dict(r)}")

# Check Audit_Logs
print("\n--- Audit Logs ---")
for t in ['audit_logs', 'Audit_Logs', 'AuditLogs', 'audit_log']:
    if t in tables:
        cur.execute(f"SELECT COUNT(*) FROM [{t}]")
        cnt = cur.fetchone()[0]
        print(f"  {t}: {cnt} rows")

# Check Security / Auth tables
print("\n--- Security Tables ---")
for t in ['JWT_Blacklist', 'User_Sessions', 'user_sessions', 'jwt_blacklist', 'otp_codes', 'OTP_Codes']:
    if t in tables:
        cur.execute(f"SELECT COUNT(*) FROM [{t}]")
        cnt = cur.fetchone()[0]
        print(f"  {t}: {cnt} rows")

conn.close()
print("\n=== DB inspection complete ===")
