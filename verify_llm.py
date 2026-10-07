#!/usr/bin/env python3
# verify_llm_providers.py - LLM provider verification
import sys, os, json, time

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

print("=" * 70)
print("LLM PROVIDER VERIFICATION (PHASE 12)")
print("=" * 70)

def _key_ok(v):
    return bool(v and not v.startswith('your_') and len(v) > 8)

providers = {
    'Gemini': os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY'),
    'Groq': os.getenv('GROQ_API_KEY'),
    'OpenAI': os.getenv('OPENAI_API_KEY'),
    'OpenRouter': os.getenv('OPENROUTER_API_KEY'),
    'Ollama': 'local_endpoint',
}

print("\nProvider Configuration Status:")
for name, key in providers.items():
    if name == 'Ollama':
        # Test Ollama local endpoint
        try:
            import urllib.request
            with urllib.request.urlopen('http://localhost:11434/api/tags', timeout=3) as r:
                data = json.loads(r.read())
                models = [m['name'] for m in data.get('models', [])]
                print(f"  Ollama: CONFIGURED_LIVE — models: {models}")
        except Exception as e:
            print(f"  Ollama: NOT_AVAILABLE — {e}")
        continue
    
    if not _key_ok(key):
        print(f"  {name}: NOT_CONFIGURED")
        continue
    
    print(f"  {name}: KEY_PRESENT ({key[:8]}...)")
    
    # Try a minimal inference test
    if name == 'Gemini':
        try:
            import urllib.request as ur
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
            body = json.dumps({"contents": [{"parts": [{"text": "Reply: OK"}]}]}).encode()
            req = ur.Request(url, data=body, headers={"Content-Type": "application/json"})
            t0 = time.time()
            with ur.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                latency = round((time.time()-t0)*1000)
                text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                print(f"    ✓ Gemini LIVE_VERIFIED — latency:{latency}ms, response:'{text[:30]}'")
        except Exception as e:
            print(f"    ✗ Gemini INVALID_CREDENTIAL or OFFLINE: {e}")
    
    elif name == 'Groq':
        try:
            import urllib.request as ur
            url = "https://api.groq.com/openai/v1/chat/completions"
            body = json.dumps({
                "model": "llama3-8b-8192",
                "messages": [{"role": "user", "content": "Reply: OK"}],
                "max_tokens": 10
            }).encode()
            req = ur.Request(url, data=body, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}"
            })
            t0 = time.time()
            with ur.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                latency = round((time.time()-t0)*1000)
                text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                print(f"    ✓ Groq LIVE_VERIFIED — latency:{latency}ms, response:'{text[:30]}'")
        except Exception as e:
            print(f"    ✗ Groq INVALID_CREDENTIAL or OFFLINE: {e}")
    
    elif name == 'OpenAI':
        try:
            import urllib.request as ur
            url = "https://api.openai.com/v1/chat/completions"
            body = json.dumps({
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Reply: OK"}],
                "max_tokens": 10
            }).encode()
            req = ur.Request(url, data=body, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}"
            })
            t0 = time.time()
            with ur.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                latency = round((time.time()-t0)*1000)
                text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                print(f"    ✓ OpenAI LIVE_VERIFIED — latency:{latency}ms, response:'{text[:30]}'")
        except Exception as e:
            print(f"    ✗ OpenAI INVALID_CREDENTIAL or OFFLINE: {e}")

print("\nActual AI Core behavior (what runs when user sends a message):")
try:
    from ai_core import ask_agri_assistant
    import time
    t0 = time.time()
    result = ask_agri_assistant(2, "ما حالة المزرعة؟", return_meta=True)
    latency = round((time.time()-t0)*1000)
    print(f"  Provider used: {result.get('provider','?')}")
    print(f"  Confidence: {result.get('confidence','?')}")
    print(f"  Sources: {result.get('sources','?')}")
    print(f"  Latency: {latency}ms")
    print(f"  Response length: {len(result.get('response',''))}")
    print(f"  Is real LLM? {'YES' if 'gemini' in str(result.get('provider','')).lower() or 'groq' in str(result.get('provider','')).lower() else 'NO - Using heuristic/RAG fallback'}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n" + "=" * 70)
print("LLM VERIFICATION COMPLETE")
print("=" * 70)
