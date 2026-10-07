#!/usr/bin/env python3
# verify_platform.py - Comprehensive platform verification
import sys, os, json, importlib, inspect

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("=" * 70)
print("TAYSEER AGRI-SAT PLATFORM VERIFICATION")
print("=" * 70)

# ── PHASE 0: Environment Check ────────────────────────────────────────
print("\n[PHASE 0] ENVIRONMENT")
print(f"  Python: {sys.version}")
print(f"  CWD: {os.getcwd()}")
print(f"  .env exists: {os.path.exists('.env')}")
print(f"  DB exists: {os.path.exists('tayseer_agrisat.db')}")

from dotenv import load_dotenv
load_dotenv()

print(f"  OWM_API_KEY: {'SET (' + os.getenv('OWM_API_KEY','')[:8] + '...)' if os.getenv('OWM_API_KEY') else 'NOT SET'}")
print(f"  TELEGRAM_BOT_TOKEN: {'your_telegram_bot_token' if os.getenv('TELEGRAM_BOT_TOKEN','').startswith('your_') else 'SET'}")
print(f"  GEE_SERVICE_ACCOUNT_KEY: {os.getenv('GEE_SERVICE_ACCOUNT_KEY','NOT SET')}")
gee_key_file = os.getenv('GEE_SERVICE_ACCOUNT_KEY', 'gee_service_account.json')
print(f"  GEE key file exists: {os.path.exists(gee_key_file)}")
print(f"  TWILIO_ACCOUNT_SID: {'SET (' + os.getenv('TWILIO_ACCOUNT_SID','')[:6] + '...)' if os.getenv('TWILIO_ACCOUNT_SID') else 'NOT SET'}")
print(f"  SMTP_HOST: {os.getenv('SMTP_HOST','NOT SET')}")
print(f"  SMTP_USER: {os.getenv('SMTP_USER','NOT SET')}")
print(f"  CATBOOST_MODEL: {'EXISTS' if os.path.exists(os.getenv('CATBOOST_MODEL_PATH','catboost_crop_health.cbm')) else 'MISSING'}")
print(f"  DEBUG mode: {os.getenv('DEBUG','False')}")
print(f"  SECRET_KEY: {'WEAK (default)' if os.getenv('SECRET_KEY','').startswith('tayseer-agrisat-secret') else 'CUSTOM'}")

# ── PHASE 1: Flask Routes ─────────────────────────────────────────────
print("\n[PHASE 1] FLASK ROUTES")
try:
    from app import app
    rules = sorted(str(r.rule) for r in app.url_map.iter_rules())
    print(f"  Total routes registered: {len(rules)}")
    
    expected_pages = ['/dashboard', '/risks', '/chat', '/farm-setup', '/map-view', 
                      '/reports', '/carbon', '/integrations', '/settings', '/admin']
    for p in expected_pages:
        found = p in rules
        print(f"  {'✓' if found else '✗'} {p}")
    
    print("\n  All registered routes:")
    for r in rules:
        print(f"    {r}")
        
except Exception as e:
    print(f"  ERROR loading app: {e}")

# ── PHASE 2: Mock Data Audit ──────────────────────────────────────────
print("\n[PHASE 2] MOCK DATA AUDIT")
import re, ast

mock_patterns = [
    (r'random\.random\(\)', 'random.random()'),
    (r'random\.uniform\(', 'random.uniform'),
    (r'random\.gauss\(', 'random.gauss'),
    (r'np\.random\.|numpy\.random\.', 'numpy random'),
    (r'math\.sin\(', 'math.sin'),
    (r'["\']fake["\']|fake_', 'fake'),
    (r'["\']dummy["\']|dummy_', 'dummy'),
    (r'["\']placeholder["\']', 'placeholder'),
    (r'demo.?data|test.?data', 'demo/test data'),
    (r'SIMULATION_MODE\s*=\s*True', 'simulation mode'),
    (r'hardcoded', 'hardcoded'),
]

py_files = [f for f in os.listdir('.') if f.endswith('.py') and not f.startswith('verify')]

for fn in sorted(py_files):
    with open(fn, 'r', encoding='utf-8', errors='ignore') as fh:
        content = fh.read()
    matches = []
    for pat, name in mock_patterns:
        for m in re.finditer(pat, content, re.IGNORECASE):
            line_no = content[:m.start()].count('\n') + 1
            ctx = content.split('\n')[line_no-1].strip()
            matches.append(f"  L{line_no}: [{name}] {ctx[:80]}")
    if matches:
        print(f"\n  FILE: {fn}")
        for m in matches[:10]:
            print(m)

# ── PHASE 3: Secret Hardcoding Check ──────────────────────────────────
print("\n[PHASE 3] SECRET HARDCODING IN CODE")
secret_patterns = [
    r'AC[0-9a-f]{32}',   # Twilio SID
    r'sk-[a-zA-Z0-9]{40,}',  # OpenAI
    r'1c9342[a-f0-9]+',  # OWM key prefix
    r'AIza[0-9A-Za-z\-_]{35}',  # Google API key
]
for fn in sorted(py_files):
    with open(fn, 'r', encoding='utf-8', errors='ignore') as fh:
        content = fh.read()
    for pat in secret_patterns:
        for m in re.finditer(pat, content):
            line_no = content[:m.start()].count('\n') + 1
            print(f"  ⚠ POSSIBLE SECRET in {fn}:L{line_no}: {m.group()[:12]}...")

# Check config.py for hardcoded defaults
print("\n  config.py defaults check:")
with open('config.py', 'r', encoding='utf-8', errors='ignore') as f:
    cfg = f.read()
hardcoded_in_config = re.findall(r'=\s*"(1c9342[^"]+|AQ\.[^"]+|925429[^"]+)"', cfg)
for h in hardcoded_in_config:
    print(f"  ⚠ Hardcoded default in config.py: {h[:20]}...")

# ── PHASE 4: OWM Live Test ─────────────────────────────────────────────
print("\n[PHASE 4] OPENWEATHERMAP LIVE TEST")
import urllib.request, json as _json
owm_key = os.getenv('OWM_API_KEY', '')
if owm_key and not owm_key.startswith('your_'):
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat=30.0444&lon=31.2357&appid={owm_key}&units=metric"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read())
            print(f"  ✓ OWM LIVE: {data.get('name','?')} temp={data['main']['temp']}°C, {data['weather'][0]['description']}")
            print(f"  Timestamp: {data['dt']}, Timezone: {data.get('timezone','N/A')}")
    except Exception as e:
        print(f"  ✗ OWM ERROR: {e}")
else:
    print("  OWM key not configured")

# ── PHASE 5: GEE Credential Check ─────────────────────────────────────
print("\n[PHASE 5] GEE CREDENTIAL CHECK")
gee_key = os.getenv('GEE_SERVICE_ACCOUNT_KEY', 'gee_service_account.json')
if os.path.exists(gee_key):
    print(f"  ✓ GEE service account JSON exists: {gee_key}")
    try:
        with open(gee_key) as f:
            gee_data = _json.load(f)
        print(f"  GEE project: {gee_data.get('project_id','?')}")
        print(f"  GEE type: {gee_data.get('type','?')}")
    except Exception as e:
        print(f"  ✗ GEE JSON parse error: {e}")
else:
    print(f"  ✗ GEE service account JSON NOT FOUND: {gee_key}")
    print(f"  GEE project env: {os.getenv('GEE_PROJECT','NOT SET')}")
    print(f"  STATUS: EXTERNAL_CREDENTIAL_REQUIRED")

# ── PHASE 6: ML Model Check ───────────────────────────────────────────
print("\n[PHASE 6] CATBOOST MODEL")
model_path = os.getenv('CATBOOST_MODEL_PATH', 'catboost_crop_health.cbm')
if os.path.exists(model_path):
    size = os.path.getsize(model_path)
    print(f"  ✓ Model file exists: {model_path} ({size} bytes)")
    try:
        from catboost import CatBoostClassifier
        model = CatBoostClassifier()
        model.load_model(model_path)
        print(f"  ✓ Model loaded successfully")
        print(f"  Feature count: {model.feature_count_}")
    except Exception as e:
        print(f"  ✗ Model load error: {e}")
else:
    print(f"  ✗ Model file NOT FOUND: {model_path}")

# ── PHASE 7: AI Core Check ────────────────────────────────────────────
print("\n[PHASE 7] AI CORE CHECK")
try:
    from ai_core import ask_agri_assistant
    result = ask_agri_assistant(2, "test query for verification", return_meta=True)
    if isinstance(result, dict):
        print(f"  ✓ ask_agri_assistant returned dict")
        print(f"  provider: {result.get('provider','?')}")
        print(f"  confidence: {result.get('confidence','?')}")
        print(f"  sources: {result.get('sources','?')}")
        resp = result.get('response','')
        print(f"  response length: {len(resp)} chars")
        print(f"  response preview: {resp[:100]}...")
    else:
        print(f"  ✗ Unexpected return type: {type(result)}")
except Exception as e:
    import traceback
    print(f"  ✗ AI Core ERROR: {e}")
    traceback.print_exc()

# ── PHASE 8: Risk Engine Check ─────────────────────────────────────────
print("\n[PHASE 8] RISK ENGINE CHECK")
try:
    from risk_engine import RiskEngine
    from farm_data_provider import FarmDataProvider
    provider = FarmDataProvider(user_id=2)
    engine = RiskEngine(user_id=2)
    result = engine.compute_from_provider(provider)
    print(f"  ✓ RiskEngine.compute_from_provider() returned {type(result)}")
    if isinstance(result, dict):
        print(f"  Keys: {list(result.keys())}")
        for key in ['overall_risk', 'land_crop', 'weather', 'satellite', 'data_status']:
            if key in result:
                print(f"  {key}: {result[key]}")
except Exception as e:
    import traceback
    print(f"  ✗ RiskEngine ERROR: {e}")
    traceback.print_exc()

# ── PHASE 9: RAG Engine Check ─────────────────────────────────────────
print("\n[PHASE 9] RAG ENGINE CHECK")
try:
    from rag_engine import RAGEngine
    rag = RAGEngine()
    print(f"  ✓ RAGEngine initialized")
    # Check if there are chunks in DB
    import sqlite3
    conn = sqlite3.connect('tayseer_agrisat.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM rag_chunks")
    chunk_cnt = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM rag_documents")
    doc_cnt = c.fetchone()[0]
    conn.close()
    print(f"  RAG documents in DB: {doc_cnt}")
    print(f"  RAG chunks in DB: {chunk_cnt}")
    if chunk_cnt == 0:
        print(f"  STATUS: NO DOCUMENTS INDEXED — RAG not functional")
    else:
        # Try a query
        try:
            result = rag.query(user_id=2, question="test question")
            print(f"  RAG query result: {result}")
        except Exception as e:
            print(f"  RAG query error: {e}")
except Exception as e:
    import traceback
    print(f"  ✗ RAG Engine ERROR: {e}")
    traceback.print_exc()

# ── PHASE 10: Telegram/WhatsApp Config ────────────────────────────────
print("\n[PHASE 10] TELEGRAM / WHATSAPP")
tg_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
print(f"  Telegram token: {'your_telegram_bot_token (NOT CONFIGURED)' if tg_token.startswith('your_') else 'SET'}")
twilio_sid = os.getenv('TWILIO_ACCOUNT_SID', '')
twilio_token = os.getenv('TWILIO_AUTH_TOKEN', '')
print(f"  Twilio SID: {'SET (' + twilio_sid[:6] + '...)' if twilio_sid else 'NOT SET'}")
print(f"  Twilio Token: {'SET' if twilio_token else 'NOT SET'}")

# Test Twilio connectivity (without exposing creds)
if twilio_sid and twilio_token:
    try:
        import urllib.request, base64
        credentials = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode()).decode()
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}.json",
            headers={"Authorization": f"Basic {credentials}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
            print(f"  ✓ Twilio LIVE: account status = {data.get('status','?')}")
    except Exception as e:
        print(f"  ✗ Twilio connectivity: {e}")

# ── PHASE 11: SMTP Config ─────────────────────────────────────────────
print("\n[PHASE 11] SMTP CONFIG")
smtp_host = os.getenv('SMTP_HOST', '')
smtp_user = os.getenv('SMTP_USER', '')
smtp_pass = os.getenv('SMTP_PASSWORD', '')
print(f"  SMTP host: {smtp_host}")
print(f"  SMTP user: {smtp_user}")
print(f"  SMTP password: {'SET' if smtp_pass else 'NOT SET'}")
# Check for comment in env value (found in .env: SMTP_USER=YusefHaisam57@gmail.com   # حط الإيميل)
if '#' in smtp_user:
    print(f"  ⚠ WARNING: SMTP_USER contains a comment character — may be malformed!")

# ── PHASE 12: Security Config ─────────────────────────────────────────
print("\n[PHASE 12] SECURITY")
secret_key = os.getenv('SECRET_KEY', '')
print(f"  SECRET_KEY: {'WEAK (predictable)' if 'secret' in secret_key.lower() and '2026' in secret_key else 'OK'}")
print(f"  DEBUG mode: {os.getenv('DEBUG','False')} {'⚠ PRODUCTION RISK' if os.getenv('DEBUG','').lower() == 'true' else ''}")

# Check security.py
if os.path.exists('security.py'):
    with open('security.py', 'r', encoding='utf-8', errors='ignore') as f:
        sec_content = f.read()
    print(f"  security.py size: {len(sec_content)} chars")
    for feature in ['csrf', 'rate_limit', 'IDOR', 'tenant', 'jwt', 'bcrypt', 'argon']:
        present = feature.lower() in sec_content.lower()
        print(f"  {'✓' if present else '✗'} {feature} present in security.py")

print("\n" + "=" * 70)
print("VERIFICATION COMPLETE")
print("=" * 70)
