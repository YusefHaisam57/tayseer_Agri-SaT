# ============================================================
# ai_core.py — Tayseer Agri-SaT Intelligent Agronomic AI Core
#
# Features:
#   1. Multi-Provider LLM Integration:
#      - Google Gemini (Gemini 1.5 Flash / 2.0 Flash)
#      - OpenAI / Groq / OpenRouter Compatible API
#      - Local Ollama
#      - Autonomous Fallback: Egyptian Agricultural Expert RAG Engine
#   2. Dynamic Farm Context Injection (NDVI, NDWI, ET, LST, TVDI, Weather, Alerts)
#   3. Agronomic RAG Knowledge Base (Egyptian soils, Toshka/Owainat/Delta,
#      pivot machinery, wheat, alfalfa, maize, potato, citrus, diseases)
#   4. Multi-Turn Conversational Memory (via Chat_Logs)
#   5. Agricultural Vision / Crop Disease Image Analysis
# ============================================================

import os
import re
import json
import base64
import logging
from datetime import datetime, timedelta
import requests
from typing import Tuple, List, Dict, Any, Optional

from config import Config
from database import (
    get_user_by_id, get_latest_farm_data, get_chat_history,
    log_chat, get_latest_pivot_telemetry
)
from ml_gee_engine import (
    fetch_weather_forecast, generate_agronomy_alerts,
    et_to_pump_hours, calculate_wue_numeric
)

log = logging.getLogger(__name__)

# ============================================================
# 1. AGRONOMIC RAG KNOWLEDGE BASE
# ============================================================

AGRONOMIC_KNOWLEDGE_BASE = [
    {
        "topic": "wheat_management",
        "keywords": ["قمح", "حبة", "سنابل", "تفريع", "صدأ", "رية المحاياة", "محصول القمح"],
        "content": (
            "🌾 إدارة محصول القمح في مصر:\n"
            "• مواعيد الري الحيوية: رية المحاياة (بعد 21-25 يوم من الزراعة)، مرحلة التفريع، مرحلة طرد السنابل (حرجة جداً)، مرحلة امتلاء الحبوب.\n"
            "• المقنن المائي الإجمالي: 1800 - 2200 م³/فدان حسب نوع الري والتربة.\n"
            "• الأمراض الشائعة: الصدأ الأصفر والبرتقالي (يظهر كبقع صفراء مسحوقية متوازية على الأوراق)، المكافحة: مبيدات ترايازول مثل تلت (تيلت) أو أوبس أو سومي-8 فور ظهور أول بقعة.\n"
            "• التسميد: 75-100 وحدة أزوت للفدان، تُضاف على دفعات قبل الري وتتوقف عند طرد السنابل. رشة عناصر صغرى (زنك + حديد + منجنيز) عند التفريع وطرد السنابل."
        )
    },
    {
        "topic": "alfalfa_management",
        "keywords": ["برسيم", "حجازي", "حشة", "أعلاف", "برسيم حجازي"],
        "content": (
            "🌱 إدارة البرسيم الحجازي:\n"
            "• الري: رية خفيفة بعد الحش مباشرة لتحفيز النموات الجديدة، ثم ريات منتظمة كل 4-7 أيام حسب درجات الحرارة.\n"
            "• التسميد: 100 كجم سوبر فوسفات + 50 كجم سلفات بوتاسيوم بعد كل حشتين لدعم الجذور والنمو الخضري.\n"
            "• الاحتياجات المائية: من أعلى المحاصيل استهلاكاً للمياه (ET يتراوح بين 6-9 مم/يوم صيفاً). يُنصح بمراقبة NDWI وTVDI لضبط التوقيت."
        )
    },
    {
        "topic": "maize_management",
        "keywords": ["ذرة", "شامية", "تزهير", "شواشي", "دودة الحشد", "عرنوس"],
        "content": (
            "🌽 إدارة الذرة الشامية:\n"
            "• المراحل الحرجة: فترة التزهير وطرد الشواشي والحرير — أي نقص مائي خلالها يسبب عقم السنابل وتراجع المحصول بنسبة 40%.\n"
            "• الآفات الخطيرة: دودة الحشد الخريفية — الفحص الدوري لقلب النبات والرش بمبيدات متخصصة (إيمامكتين بنزوات أو كوراجين) عند الغروب.\n"
            "• التسميد: 120 وحدة أزوت للفدان موزعة على 3 دفعات مع الري بالتنقيط أو المحوري حتى بداية التزهير."
        )
    },
    {
        "topic": "pivot_irrigation_machinery",
        "keywords": ["بيفوت", "محوري", "رشاشات", "ضغط", "أبراج", "إطارات", "عطل", "موتور", "جهد", "امبير"],
        "content": (
            "🚜 صيانة وتشغيل نظام الري المحوري (Center Pivot):\n"
            "• ضغط المركز: يجب أن يكون بين 35 إلى 45 PSI عند مدخل البيفوت؛ انخفاض الضغط يدل على تسريب أو عطل بالطلمبة، ارتفاعه يدل على انسداد رشاشات.\n"
            "• الأبراج الخارجية: تستهلك كمية مياه أكبر لكل متر طولي لأن سرعتها الخطية أعلى؛ أي انسداد في رشاشات الأبراج الطرفية يسبب فوراً انخفاض NDVI في الحلقات الخارجية.\n"
            "• الأعطال الكهربائية الشائعة: هبوط الجهد تحت 380V يسبب احتراق مواتير الأبراج؛ ارتفاع الأمبير فوق 4.5A يدل على غرز الإطارات في الطين أو تآكل الجيربوكس.\n"
            "• انتظام التوزيع (CU / Uniformity): يجب ألا يقل معامل التوزيع عن 85%، ويُفحص بتباين مؤشر NDVI القطري (CV < 15%)."
        )
    },
    {
        "topic": "satellite_indices_guide",
        "keywords": ["ndvi", "ndwi", "lst", "et", "tvdi", "مؤشر", "أقمار", "حرارة الأرض", "تبخر"],
        "content": (
            "🛰️ دليل المؤشرات الفضائية الزراعية في تيسير Agri-SaT:\n"
            "• NDVI (مؤشر الغطاء النباتي): <0.3 (تربة جرداء أو إنبات متعثر)، 0.3-0.55 (نمو متوسط/تغطية جزئية)، >0.6 (كثافة خضرية ممتازة ونشاط حيوي عالي).\n"
            "• NDWI (مؤشر المحتوى المائي): سالب يدل على جفاف النبات؛ فوق 0.15 يدل على رطوبة نباتية جيدة.\n"
            "• LST (حرارة سطح الأرض): الفصل بين حرارة الغطاء النباتي Canopy وحرارة التربة Soil يحدد بدقة هل الإجهاد حراري أم مائي.\n"
            "• TVDI (مؤشر الجفاف المشتق): فوق 0.75 مؤشر خطير على جفاف التربة يستوجب تشغيل الري فوراً.\n"
            "• ET (التبخر-نتح الفعلي): يحسب بالمم/يوم؛ يُضرب في مساحة الفدان (4200 م² × ET/1000) لتحديد الحجم المكعب لمياه الري المطلوبة يومياً."
        )
    },
    {
        "topic": "weather_heatwave_frost",
        "keywords": ["حرارة", "طقس", "موجة حارة", "صقيع", "رياح", "عواصف", "خماسين"],
        "content": (
            "🌤️ إدارة الطقس والتقلبات المناخية:\n"
            "• الموجات الحارة (>38°م): زيادة معدلات الري بنسبة 20-30%، والري ليلاً أو في الصباح الباكر؛ تجنب الري وقت الظهيرة لمنع لسعات الحرارة وصدمة الجذور.\n"
            "• الرياح العاتية (>35 كم/س): توقيف البيفوت في اتجاه موازٍ للرياح لتقليل مقاومة الرياح وحماية الهيكل الميكانيكي، وتأجيل الرش الورقي.\n"
            "• الصقيع الشتوي: إعطاء رية سريعة خفيفة لتدفئة التربة، ورش سيليكات البوتاسيوم أو الأحماض الأمينية لرفع مقاومة الخلايا النباتية."
        )
    },
    {
        "topic": "water_accounting_pump",
        "keywords": ["ساعات", "طلمبة", "متر مكعب", "فدان", "استهلاك المياه", "تشغيل", "wue"],
        "content": (
            "💧 حسابات الري وتصرف الطلمبات:\n"
            "• حجم الماء المطلوب (م³) = التبخر-نتح (ET مم/يوم) × 4.2 × مساحة المزرعة بالفدان.\n"
            "• ساعات تشغيل الطلمبة = حجم الماء المطلوب (م³) ÷ تصرف الطلمبة الفعلي (م³/ساعة).\n"
            "• كفاءة استخدام المياه (WUE): كجم محصول أو كتلة حيوية لكل م³ ماء؛ المعدل النموذجي فوق 1.2 كجم/م³."
        )
    }
]


from farm_data_provider import FarmDataProvider
from rag_engine import PersistentRAGEngine
from multimodal_engine import analyze_crop_image


def retrieve_rag_context(user_id: int, query: str, top_k: int = 3) -> Tuple[str, List[str]]:
    """
    Hybrid RAG retrieval: combines persistent user-uploaded documents (PDF/DOCX/CSV)
    with the foundational Egyptian agronomic knowledge base.
    Returns: (rag_text_context, list_of_cited_sources)
    """
    if not query:
        return "", []

    cited_sources = []
    snippets = []

    # 1. Query persistent tenant-isolated documents
    try:
        engine = PersistentRAGEngine(user_id=user_id)
        docs = engine.retrieve(query, top_k=2)
        for d in docs:
            snippets.append(f"📄 [وثيقة: {d['title']}]\n{d['content']}")
            cited_sources.append(f"مستند: {d['title']}")
    except Exception as e:
        log.warning(f"[AICore] Persistent RAG retrieval error: {e}")

    # 2. Query curated agronomic knowledge base
    q_tokens = set(re.findall(r"[\w']+", query.lower()))
    scored_docs = []

    for item in AGRONOMIC_KNOWLEDGE_BASE:
        score = 0
        for kw in item["keywords"]:
            if kw.lower() in query.lower():
                score += 3
        c_tokens = set(re.findall(r"[\w']+", item["content"].lower()))
        score += len(q_tokens.intersection(c_tokens))

        if score > 0:
            scored_docs.append((score, item["topic"], item["content"]))

    scored_docs.sort(key=lambda x: x[0], reverse=True)
    for _, topic, content in scored_docs[:2]:
        snippets.append(f"🌾 [قاعدة المعرفة الزراعية: {topic}]\n{content}")
        cited_sources.append(f"دليل تيسير الزراعي ({topic})")

    return "\n\n".join(snippets), cited_sources


# ============================================================
# 2. FARM CONTEXT BUILDER (GROUNDED IN FARMDATAPROVIDER)
# ============================================================

def build_farm_prompt_context(user_id: int) -> dict:
    """
    Extracts real telemetry, metrics, and weather via FarmDataProvider
    to strictly ground the LLM in the current physical state of the farm.
    NO FAKE NUMBERS.
    """
    provider = FarmDataProvider(user_id)
    meta = provider.get_farm_metadata()
    sat = provider.get_satellite_observations()
    weather = provider.get_weather_forecast()
    iot = provider.get_iot_telemetry()
    ml = provider.get_ml_predictions()

    metrics = sat.get("metrics", {})
    ndvi_val = metrics.get("ndvi", {}).get("value")
    ndwi_val = metrics.get("ndwi", {}).get("value")
    lst_val = metrics.get("lst", {}).get("value")
    et_val = metrics.get("et", {}).get("value")
    tvdi_val = metrics.get("tvdi", {}).get("value")
    biom_val = metrics.get("biomass", {}).get("value")
    cv_val = metrics.get("ndvi_cv", {}).get("value")

    # Pump hours from real ET
    area_fed = meta.get("area_feddan", 1.0) or 1.0
    est_pump_hours = None
    if et_val is not None:
        try:
            est_pump_hours = round((float(et_val) * 4.2 * area_fed) / 60.0, 1)
        except Exception:
            pass

    # Weather text
    weather_text = "بيانات الطقس غير متوفرة (DATA_UNAVAILABLE)"
    if weather.get("status") == "LIVE" and weather.get("current"):
        c = weather["current"]
        weather_text = (
            f"حرارة: {c.get('temp_min', '-')}-{c.get('temp_max', '-')}°م، "
            f"رطوبة: {c.get('humidity', '-')}%، رياح: {c.get('wind_speed', '-')} م/ث، "
            f"حالة: {c.get('description', 'مستقر')}"
        )

    # Alerts text
    alerts = weather.get("agro_alerts", [])
    alerts_str = " | ".join(a.get("message_ar", str(a)) for a in alerts) if alerts else "الظروف الجوية آمنة ولا توجد تنبيهات نشطة"

    # IoT Pivot state
    iot_str = "لا يوجد جهاز تحكم IoT متصل حالياً (DATA_UNAVAILABLE)"
    if iot.get("status") == "LIVE" and iot.get("telemetry"):
        t = iot["telemetry"]
        iot_str = f"حالة الجهاز: {t.get('overall_status','متصل')} | ضغط المركز: {t.get('center_pressure_psi','-')} PSI | عطل: {t.get('fault_message','لا يوجد')}"

    # ML prediction label
    ml_str = "بانتظار قراءات حديثة للتشخيص (INSUFFICIENT_DATA)"
    if ml.get("status") == "AVAILABLE":
        ml_str = f"{ml.get('predicted_label_ar')} (ثقة {ml.get('confidence', 0):.0%}) [تنبيه: نموذج قيد التطوير مبني على بيانات تدريبية افتراضية]"

    context_text = f"""
[بيانات المزرعة الحالية والمؤكدة]:
• اسم المزرعة: {meta.get('farm_name', 'مزرعتي')}
• المساحة: {area_fed:.1f} فدان | المحصول الأساسي: {meta.get('crop_type', 'قمح')}
• تاريخ رصد القمر الصناعي: {sat.get('observation_date', 'غير متوفر')} (الحالة: {sat.get('status', 'DATA_UNAVAILABLE')})
• الغطاء النباتي (NDVI): {ndvi_val if ndvi_val is not None else 'غير متوفر'}
• المحتوى المائي (NDWI): {ndwi_val if ndwi_val is not None else 'غير متوفر'}
• حرارة سطح الأرض (LST): {lst_val if lst_val is not None else 'غير متوفر'} °م
• البخر نتح اليومي (ET): {et_val if et_val is not None else 'غير متوفر'} مم/يوم
• ساعات تشغيل الطلمبة الموصى بها اليوم: {est_pump_hours if est_pump_hours is not None else 'غير متوفر'} ساعة
• مؤشر الجفاف (TVDI): {tvdi_val if tvdi_val is not None else 'غير متوفر'}
• تجانس ري البيفوت (NDVI CV): {cv_val if cv_val is not None else 'غير متوفر'}%
• حالة أجهزة الـ IoT / البيفوت: {iot_str}
• حالة الطقس (OpenWeatherMap): {weather_text}
• تنبيهات الأرصاد: {alerts_str}
• توقع نموذج الذكاء الاصطناعي (CatBoost): {ml_str}
"""
    return {
        "text": context_text.strip(),
        "provider_data": provider,
        "est_pump_hours": est_pump_hours,
        "sat_status": sat.get("status"),
        "meta": meta,
        "metrics": metrics,
    }



# ============================================================
# 3. MULTI-PROVIDER LLM CALLERS
# ============================================================

SYSTEM_PROMPT = """أنت "المهندس تيسير" (Tayseer Agri-SaT AI)، كبير المستشارين الزراعيين الرقميين لمنصة تيسير للاستشعار عن بعد وإدارة المزارع الذكية في مصر والعالم العربي.
شخصيتك:
- خبير زراعي مصري، ودود، علمي، عملي، ومباشر في الإجابة.
- تخاطب المزارع أو المهندس بلهجة مصرية مهذبة راقية مفهومة ("يا حاج" أو "يا بشمهندس").
- تعتمد دائماً على بيانات الأقمار الصناعية (NDVI, NDWI, ET, LST, TVDI) وتصرف الطلمبات والطقس المتوفرة في سياق المزرعة.
- تقدم دائماً أرقاماً وحلولاً محددة قابلة للتطبيق (مثل: ساعات تشغيل الطلمبة، أسماء المبيدات الشائعة المعتمدة في مصر، مواعيد الري المناسبة لتجنب البخر أو الرياح).
- إذا سأل المزارع سؤالاً خارج الزراعة (مثل سؤال تاريخي أو عام)، تجيبه باختصار ودقة وذكاء، ثم تعيده بلطف إلى متابعة أرضه.
- نسق إجاباتك بنقاط واضحة واستخدم الإيموجي المناسب لتسهيل القراءة."""


def _call_gemini_api(api_key: str, prompt: str, image_b64: str = None, mime_type: str = "image/jpeg") -> str | None:
    """Call Google Gemini 1.5 / 2.0 Flash API via REST."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    parts = [{"text": prompt}]
    
    if image_b64:
        parts.append({
            "inlineData": {
                "mimeType": mime_type,
                "data": image_b64
            }
        })

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": parts
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 900,
        }
    }

    try:
        resp = requests.post(url, json=payload, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts_out = candidates[0].get("content", {}).get("parts", [])
                if parts_out:
                    return parts_out[0].get("text", "").strip()
        else:
            log.warning(f"[AICore] Gemini HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.warning(f"[AICore] Gemini request failed: {e}")
    return None


def _call_openai_compatible_api(endpoint: str, api_key: str, model: str, prompt: str, image_b64: str = None) -> str | None:
    """Call OpenAI / Groq / OpenRouter / Ollama chat completions endpoint."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}" if api_key else ""
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    if image_b64:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]
        })
    else:
        messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 900
    }

    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
        else:
            log.warning(f"[AICore] OpenAI compatible HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.warning(f"[AICore] OpenAI compatible call failed: {e}")
    return None


def _call_ollama_local(prompt: str, model: str = "llama3:latest") -> str | None:
    """Call local Ollama instance if active."""
    ollama_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    url = f"{ollama_url}/api/generate"
    payload = {
        "model": model,
        "prompt": f"{SYSTEM_PROMPT}\n\n{prompt}",
        "stream": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=8)
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
    except Exception:
        pass
    return None


# ============================================================
# 4. EXPERT AGRONOMIC RULE-BASED FALLBACK ENGINE
# ============================================================

def _generate_expert_agronomic_fallback(user_id: int, message: str, ctx: dict, rag_info: str) -> str:
    """
    High-fidelity offline / fallback expert response generator.
    Guarantees deep, accurate, Arabic agronomic answers even without internet or API keys.
    """
    msg = message.lower().strip()
    fd = ctx.get("fd", {})
    ndvi = fd.get("ndvi")
    et = fd.get("et")
    est_hours = ctx.get("est_pump_hours")
    area = ctx.get("user", {}).get("area_feddan", 1.0) or 1.0

    # 1. Irrigation / Pump Queries
    if any(k in msg for k in ["أروي", "ري", "مية", "ماء", "سقي", "طلمبة", "موتور", "ساعات"]):
        if est_hours is not None and et is not None:
            advice = (
                f"أهلاً يا بشمهندس! 💧\n"
                f"بناءً على قراءة التبخر والنتح الحالية (ET = {et:.1f} مم/يوم) ومساحة أرضك ({area:.0f} فدان):\n\n"
                f"⏱️ **ساعات تشغيل جهاز الري اليوم**: يُوصى بتشغيل الطلمبة حوالي **{est_hours:.1f} ساعة**.\n\n"
            )
            if et > 6.0:
                advice += "⚠️ **تنبيه**: معدل البخر مرتفع، يُفضل تشغيل الري في الصباح الباكر أو بعد غروب الشمس لتقليل الفواقد.\n"
            else:
                advice += "✅ معدل البخر معتدل والتربة في حالة جيدة.\n"
            return advice
        elif est_hours is not None:
            return (
                f"أهلاً يا بشمهندس! 💧\n"
                f"بناءً على مساحة أرضك ({area:.0f} فدان):\n\n"
                f"⏱️ **ساعات تشغيل جهاز الري اليوم**: يُوصى بتشغيل الطلمبة حوالي **{est_hours:.1f} ساعة**.\n\n"
                f"✅ يُفضل تشغيل الري في الصباح الباكر أو المساء لتقليل الفواقد."
            )
        else:
            return (
                "يا حاج، عشان أحسبلك ساعات الري بدقة، محتاجين بيانات التبخر (ET). "
                "لكن كقاعدة عامة: في الجو المعتدل يحتاج الفدان 25-30 م³/يوم، وفي الصيف 35-45 م³/يوم. "
                "شغّل الري في الأوقات الباردة وتأكد من ضغط المركز! 🌱"
            )

    # 2. Crop Health / NDVI / Plant Status
    if any(k in msg for k in ["حالة", "محصول", "زرع", "صحة", "نبات", "مرض", "أخبار", "ndvi"]):
        if ndvi is not None:
            level = "ممتاز وكثيف 🌿" if ndvi >= 0.6 else "متوسط ويحتاج متابعة 🟡" if ndvi >= 0.4 else "ضعيف أو يعاني من إجهاد 🔴"
            res = (
                f"يا هلا يا حاج! 🛰️ تقرير صحة المحصول من الأقمار الصناعية:\n\n"
                f"• **مؤشر الغطاء النباتي (NDVI)**: {ndvi:.3f} ({level})\n"
                f"• **مؤشر التبخر (ET)**: {et or '-'} مم/يوم\n"
                f"• **الكتلة الحيوية التقديرية**: {fd.get('biomass') or '-'} طن/فدان\n\n"
            )
            if ndvi < 0.4:
                res += "💡 **توصية**: افحص قطاعات الأرض للتأكد من انتظام الرشاشات وعدم وجود إجهاد مائي أو فطري.\n"
            else:
                res += "✅ المحصول في حالة نشاط خضري جيد. استمر في برنامج الري والتسميد المتزن.\n"
            return res

    # 3. Center Pivot Machinery
    if any(k in msg for k in ["بيفوت", "محوري", "عطل", "برج", "رشاش", "جيربوكس", "كهرباء"]):
        return (
            "🔧 **فحص نظام الري المحوري (Center Pivot)**:\n\n"
            "1. **ضغط المركز**: افحص مقياس الضغط عند نقطة الارتكاز (يجب أن يكون بين 35-42 PSI).\n"
            "2. **الرشاشات الخارجية**: تأكد من نظافة فوهات آخر برج لأنها تروي أكبر مساحة.\n"
            "3. **المحاذاة والكهرباء**: إذا توقف البيفوت، افحص سلك الأمان (Safety Circuit) ومفتاح المحاذاة (Alignment Microswitch) في كل برج.\n"
            "4. **الإطارات والجيربوكس**: راقب علامات تسريب الزيت وعزم الدوران في الأبراج الوسطى."
        )

    # 4. If RAG found specific matches
    if rag_info:
        return (
            f"أهلاً بحضرتك يا حاج! 🌿\n\n"
            f"إليك الإرشادات الفنية بخصوص سؤالك:\n\n"
            f"{rag_info}\n\n"
            f"إذا احتجت تفاصيل أكثر عن التسميد أو توقيت الري، أنا في خدمتك!"
        )

    # 5. General intelligent response
    return (
        f"أهلاً بيك يا حاج! 🌿 أنا المهندس تيسير، مستشارك الزراعي الذكي.\n\n"
        f"أنا متابع مزرعتك ببيانات الأقمار الصناعية لحظة بلحظة. ممكن تسألني عن:\n"
        f"• 💧 **كم ساعة تشغل الطلمبة النهاردة؟**\n"
        f"• 🌾 **حالة المحصول ومؤشر NDVI؟**\n"
        f"• 🚜 **فحص أعطال البيفوت وضغط الرشاشات؟**\n"
        f"• 🌤️ **تأثير الطقس والموجات الحارة على الزرع؟**\n\n"
        f"اكتب سؤالك بأي صيغة وأنا هجاوبك على طول! 😊"
    )


# ============================================================
# 5. MAIN ENTRY POINT: ASK AGRI ASSISTANT
# ============================================================

def ask_agri_assistant(
    user_id: int,
    message: str,
    channel: str = "web",
    image_b64: str = None,
    return_meta: bool = False
) -> Any:
    """
    Unified intelligence pipeline:
    1. Loads live farm metrics & weather context for user via FarmDataProvider.
    2. Performs hybrid RAG lookup against persistent documents and agronomic knowledge base.
    3. Handles multimodal image inspection (spectral & leaf diagnostics).
    4. Calls available LLM providers in sequence (Gemini -> Groq -> OpenAI -> Ollama).
    5. Falls back to expert Egyptian agronomic rule engine if no keys/network.
    6. Logs the exchange to Chat_Logs.
    """
    clean_msg = (message or "").strip()
    if not clean_msg and not image_b64:
        msg = "أهلاً يا حاج! اتفضل اكتب سؤالك أو أرفق صورة للزرع وأنا جاهز أساعدك. 🌱"
        if return_meta:
            return {"response": msg, "provider": "system", "confidence": 1.0, "sources": []}
        return msg

    # Step 1: Context & RAG
    farm_ctx = build_farm_prompt_context(user_id)
    rag_snippet, cited_sources = retrieve_rag_context(user_id, clean_msg, top_k=2)

    # Step 2: Image diagnostics (if image attached)
    image_diagnostic_text = ""
    if image_b64:
        try:
            raw_img_bytes = base64.b64decode(image_b64)
            img_res = analyze_crop_image(raw_img_bytes)
            if img_res.get("status") == "ANALYZED":
                image_diagnostic_text = (
                    f"\n[نتائج التحليل الطيفي للصورة المرفقة]:\n"
                    f"• التشخيص المرجح: {img_res.get('disease_name_ar')} (درجة الثقة: {img_res.get('confidence_level')})\n"
                    f"• الأعراض المرصودة: {', '.join(img_res.get('observed_symptoms', []))}\n"
                    f"• الإجراء الموصى به: {img_res.get('recommended_action')}\n"
                )
                cited_sources.append("الفحص الطيفي للبصريات (Vision AI)")
            elif img_res.get("status") == "NOT_AGRICULTURAL":
                image_diagnostic_text = "\n[تنبيه الرؤية الحاسوبية]: الصورة المرفقة لا تظهر عليها معالم نباتية أو أجهزة ري زراعية.\n"
        except Exception as img_err:
            log.warning(f"[AICore] Image pre-analysis failed: {img_err}")

    # Step 3: Recent chat history for memory
    recent_history = get_chat_history(user_id, channel=channel, limit=3) or []
    history_str = ""
    for r in reversed(recent_history):
        history_str += f"المستخدم: {r['message']}\nالمساعد: {r['response'][:120]}...\n"

    # Step 4: Compose complete prompt
    augmented_prompt = f"""
{farm_ctx['text']}
{image_diagnostic_text}

[معلومات زراعية إرشادية ذات صلة (RAG)]:
{rag_snippet if rag_snippet else 'استند إلى المعرفة والممارسات الزراعية المصرية المعتمدة.'}

[سياق المحادثة السابقة]:
{history_str if history_str else 'محادثة جديدة.'}

[سؤال المستخدم الحالي]:
{clean_msg if clean_msg else 'يرجى فحص الصورة المرفقة وتقديم تقرير تشخيصي متكامل وإجراءات العلاج.'}
"""

    response_text = None
    provider_name = "egyptian_expert_engine"
    confidence = 0.85

    # Step 5: Try LLM Providers
    # Provider A: Google Gemini
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gemini_key and not gemini_key.startswith("your_"):
        try:
            response_text = _call_gemini_api(gemini_key, augmented_prompt, image_b64=image_b64)
            if response_text:
                provider_name = "Google Gemini 1.5 Flash"
                confidence = 0.95
        except Exception as e:
            log.warning(f"[AICore] Gemini call attempted but failed: {e}")

    # Provider B: Groq API
    if not response_text:
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            response_text = _call_openai_compatible_api(
                "https://api.groq.com/openai/v1/chat/completions",
                groq_key,
                "llama-3.3-70b-versatile",
                augmented_prompt,
                image_b64=image_b64
            )
            if response_text:
                provider_name = "Groq Llama-3.3"
                confidence = 0.90

    # Provider C: OpenAI API
    if not response_text:
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            response_text = _call_openai_compatible_api(
                "https://api.openai.com/v1/chat/completions",
                openai_key,
                "gpt-4o-mini",
                augmented_prompt,
                image_b64=image_b64
            )
            if response_text:
                provider_name = "OpenAI GPT-4o-mini"
                confidence = 0.92

    # Provider D: Local Ollama
    if not response_text and os.getenv("USE_OLLAMA", "false").lower() == "true":
        response_text = _call_ollama_local(augmented_prompt)
        if response_text:
            provider_name = "Ollama Local Llama3"
            confidence = 0.85

    # Provider E: Expert Rule-Based Agronomic Fallback
    if not response_text:
        log.info("[AICore] Operating with Egyptian Agronomic Expert Engine (RAG/Heuristic Fallback)")
        response_text = _generate_expert_agronomic_fallback(user_id, clean_msg, farm_ctx, rag_snippet)
        provider_name = "محرك تيسير الزراعي الخبير (بدون إنترنت)"
        confidence = 0.85

    # Step 6: Persist interaction in Chat_Logs
    try:
        log_chat(user_id, channel, clean_msg or "[صورة مرفقة]", response_text)
    except Exception as exc:
        log.warning(f"[AICore] Chat logging failed: {exc}")

    if return_meta:
        return {
            "response": response_text,
            "provider": provider_name,
            "confidence": confidence,
            "sources": cited_sources,
            "farm_name": farm_ctx.get("meta", {}).get("farm_name"),
            "data_freshness": farm_ctx.get("sat_status"),
        }

    return response_text

