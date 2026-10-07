#!/usr/bin/env python3
# verify_idor_and_routes.py - IDOR and live API verification
import sys, os, json, time

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

print("=" * 70)
print("IDOR / SECURITY / API VERIFICATION")
print("=" * 70)

# ── Test IDOR via Flask test client ────────────────────────────
print("\n[PHASE 3] IDOR SECURITY TESTS")
try:
    from app import app
    client = app.test_client()
    
    # Simulate User A logged in (user_id=1)
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    
    # Test 1: User A accessing own farm data
    resp = client.get('/api/farm-data')
    print(f"  User A /api/farm-data: HTTP {resp.status_code}")
    if resp.status_code == 200:
        data = resp.get_json()
        if data:
            uid_in_resp = data.get('user_id') or data.get('id')
            print(f"    user_id in response: {uid_in_resp} (expected: 1)")
    
    # Test 2: User A requesting risks dashboard
    resp = client.get('/api/risks/dashboard')
    print(f"  User A /api/risks/dashboard: HTTP {resp.status_code}")
    
    # Test 3: Chat as User A
    resp = client.post('/api/chat', 
        data=json.dumps({'message': 'IDOR test: what farm data do you see?'}),
        content_type='application/json')
    print(f"  User A /api/chat: HTTP {resp.status_code}")
    if resp.status_code == 200:
        data = resp.get_json()
        if data:
            print(f"    provider: {data.get('provider','?')}")
    
    # Test 4: IDOR — now simulate User B (id=2), try to access report or farm data 
    # by changing session to user 2
    with client.session_transaction() as sess:
        sess['user_id'] = 2
    
    resp = client.get('/api/farm-data')
    print(f"  User B /api/farm-data: HTTP {resp.status_code}")
    if resp.status_code == 200:
        data = resp.get_json()
        if data:
            uid_in_resp = data.get('user_id')
            print(f"    user_id in response: {uid_in_resp} (expected: 2, IDOR if !=2)")
    
    # Test 5: Try to access admin routes as non-admin user
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # non-admin user
    
    resp = client.get('/admin')
    print(f"  Non-admin accessing /admin: HTTP {resp.status_code}")
    if resp.status_code == 200:
        print(f"    ⚠ WARNING: Non-admin user accessed /admin!")
    elif resp.status_code in [403, 401, 302]:
        print(f"    ✓ Admin properly blocked")
    
    # Test 6: API endpoint without auth
    with client.session_transaction() as sess:
        sess.clear()
    
    resp = client.get('/api/farm-data')
    print(f"  Unauthenticated /api/farm-data: HTTP {resp.status_code}")
    if resp.status_code == 200:
        print(f"    ⚠ WARNING: Unauthenticated access to farm data!")
    elif resp.status_code in [401, 403, 302]:
        print(f"    ✓ Auth properly enforced")
    
    # Test 7: Unauthenticated chat
    resp = client.post('/api/chat',
        data=json.dumps({'message': 'test without auth'}),
        content_type='application/json')
    print(f"  Unauthenticated /api/chat: HTTP {resp.status_code}")
    
    # Test 8: Unauthenticated risks
    resp = client.get('/api/risks/dashboard')
    print(f"  Unauthenticated /api/risks/dashboard: HTTP {resp.status_code}")
    
    # Test 9: Attempt to impersonate via hidden parameter
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # non-admin
    resp = client.get('/api/risks/dashboard?user_id=2')
    print(f"  IDOR via ?user_id=2 param: HTTP {resp.status_code}")
    if resp.status_code == 200:
        data = resp.get_json()
        # Check if response has User 2's farm data
        farm_id = data.get('farm_id') if data else None
        print(f"    farm_id in response: {farm_id}")
        if farm_id == 2 or farm_id == '2':
            print(f"    ⚠ IDOR VULNERABILITY: User 1 got User 2's risks!")
        else:
            print(f"    ✓ No IDOR via query param (response tied to session user)")

except Exception as e:
    import traceback
    print(f"  ERROR: {e}")
    traceback.print_exc()

# ── Test WhatsApp Webhook Signature ───────────────────────────
print("\n[PHASE 20] WHATSAPP WEBHOOK SECURITY")
try:
    from app import app
    client = app.test_client()
    
    # Test without valid Twilio signature
    resp = client.post('/api/webhook/whatsapp',
        data={'From': 'whatsapp:+201001234567', 'Body': 'test'},
        headers={'X-Twilio-Signature': 'invalid_signature'})
    print(f"  Invalid Twilio signature: HTTP {resp.status_code}")
    if resp.status_code in [401, 403]:
        print(f"    ✓ Signature validation working")
    else:
        print(f"    ⚠ Signature validation may not be enforced (got {resp.status_code})")
    
    # Test duplicate webhook routes
    rules = [str(r.rule) for r in app.url_map.iter_rules()]
    wa_routes = [r for r in rules if 'whatsapp' in r.lower() and 'webhook' in r.lower()]
    print(f"  WhatsApp webhook routes: {wa_routes}")
    if len(wa_routes) > 1:
        print(f"    ⚠ WARNING: Duplicate webhook routes found!")
    else:
        print(f"    ✓ Single webhook route confirmed")
    
    tg_routes = [r for r in rules if 'telegram' in r.lower() and 'webhook' in r.lower()]
    print(f"  Telegram webhook routes: {tg_routes}")

except Exception as e:
    print(f"  ERROR: {e}")

# ── Test API endpoints returning data ──────────────────────────
print("\n[PHASE 6] WEATHER API TEST")
try:
    from app import app
    client = app.test_client()
    
    with client.session_transaction() as sess:
        sess['user_id'] = 2  # user with coords
    
    resp = client.get('/api/weather')
    print(f"  /api/weather: HTTP {resp.status_code}")
    if resp.status_code == 200:
        data = resp.get_json()
        if data:
            print(f"    forecast entries: {len(data.get('forecast', []))}")
            print(f"    source: {data.get('source','?')}")
            print(f"    has real data: {len(data.get('forecast',[])) > 0}")

except Exception as e:
    print(f"  ERROR: {e}")

# ── Test Report Generation ─────────────────────────────────────
print("\n[PHASE 18] REPORT GENERATION TEST")
try:
    from app import app
    client = app.test_client()
    
    with client.session_transaction() as sess:
        sess['user_id'] = 2
    
    resp = client.post('/api/reports/generate',
        data=json.dumps({'report_type': 'farm_summary', 'format': 'json', 'days': 30}),
        content_type='application/json')
    print(f"  /api/reports/generate: HTTP {resp.status_code}")
    if resp.status_code == 200:
        data = resp.get_json()
        if data:
            print(f"    success: {data.get('success','?')}")
            print(f"    data_status: {data.get('data_status','?')}")
    elif resp.status_code == 500:
        data = resp.get_json()
        print(f"    ERROR: {data}")

except Exception as e:
    import traceback
    print(f"  ERROR: {e}")
    traceback.print_exc()

# ── Check for duplicate routes ─────────────────────────────────
print("\n[PHASE 24] DUPLICATE ROUTE CHECK")
try:
    from app import app
    rules = [(str(r.rule), list(r.methods)) for r in app.url_map.iter_rules()]
    route_dict = {}
    for rule, methods in rules:
        key = (rule, tuple(sorted(methods)))
        route_dict[key] = route_dict.get(key, 0) + 1
    
    duplicates = {k: v for k, v in route_dict.items() if v > 1}
    if duplicates:
        print(f"  ⚠ Duplicate routes found: {duplicates}")
    else:
        print(f"  ✓ No duplicate routes detected")
    
    # Check for health endpoint duplicates
    health_routes = [r for r, m in rules if 'health' in r.lower()]
    print(f"  Health routes: {health_routes}")

except Exception as e:
    print(f"  ERROR: {e}")

print("\n" + "=" * 70)
print("IDOR / SECURITY VERIFICATION COMPLETE")
print("=" * 70)
