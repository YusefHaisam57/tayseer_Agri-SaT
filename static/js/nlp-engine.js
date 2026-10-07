/**
 * Tayseer-AgriSaT — nlp-engine.js
 * Offline Egyptian Dialect NLP Engine (Mini-Tayseer)
 * Runs entirely in the browser — no server call required.
 * Author: Tayseer Platform v4.0  |  2026
 */

class EgyptianNLP {
    constructor(farmData) {
        this.fd = farmData || {};

        // ──────────── Egyptian Dialect Keyword Dictionary ────────────────
        this.kw = {
            irrigation: [
                'ري', 'روي', 'رية', 'أروي', 'اروي', 'مية', 'ميه', 'الري',
                'موتور', 'طلمبة', 'نروي', 'اشغل', 'ري النهارده', 'ري النهاردة'
            ],
            pivot: [
                'بيفوت', 'بايفوت', 'البيفوت', 'الري المحوري', 'pivot',
                'بيفوتات', 'محوري'
            ],
            crop: [
                'محصول', 'زرع', 'نبات', 'حالة الزرع', 'الزرع', 'المحصول',
                'الارض', 'أرضي', 'أرض', 'النبات'
            ],
            weather: [
                'طقس', 'الجو', 'حرارة', 'امطار', 'مطر', 'الحرارة',
                'الطقس', 'درجة', 'رطوبة', 'شتا', 'صيف', 'خريف'
            ],
            problem: [
                'عطل', 'مشكلة', 'خراب', 'عطلان', 'مش شغال', 'واقف',
                'توقف', 'بايظ', 'بايظة', 'عطل', 'هنا انبرت', 'بايظ'
            ],
            disease: [
                'مرض', 'امراض', 'حشرات', 'افات', 'آفات', 'تلوث',
                'مرضاء', 'فطر', 'بياض', 'اصفرار', 'ذبول'
            ],
            hours: [
                'ساعات', 'ساعة', 'كام ساعة', 'وقت', 'تشغيل', 'ساعات التشغيل',
                'كتير', 'شغال', 'وقت الري'
            ],
            biomass: [
                'انتاج', 'إنتاج', 'كتلة', 'كمية', 'طن', 'فدان',
                'محصول الكتلة', 'الانتاج', 'الإنتاج', 'الغلة'
            ],
            status: [
                'ازاي', 'إزاي', 'عامل', 'حالته', 'حالة', 'مستوى',
                'ايه حالة', 'إيه', 'الوضع', 'احوال'
            ],
            wue: [
                'كفاءة', 'كفاءة المياه', 'مياه', 'استهلاك', 'wue',
                'هدر', 'توفير', 'ترشيد', 'مياه ضايعة'
            ],
            tvdi: [
                'tvdi', 'جفاف', 'رطوبة التربة', 'يابس', 'جاف', 'ناشف',
                'جفاف الارض', 'ارض جافة'
            ],
            alerts: [
                'تحذير', 'تنبيه', 'انذار', 'مشاكل', 'اخبار', 'آخر اخبار',
                'ايه في', 'إيه في', 'اخبار المزرعة'
            ],
        };

        // Greetings
        this.greetings = [
            'السلام', 'صباح', 'مساء', 'اهلا', 'أهلا', 'هاي', 'هلو',
            'ازيك', 'ازيكم', 'هايو', 'مرحبا', 'مرحباً'
        ];
    }

    // ─────────────────── Intent Detection ──────────────────────────────────

    detectIntent(msg) {
        const m = msg.toLowerCase().trim()
            .replace(/[\u064b-\u065f]/g, '')  // strip harakat
            .replace(/ة/g, 'ه')               // normalize ta marbouta
            .replace(/أ|إ|آ/g, 'ا');          // normalize alef

        // Greetings first
        if (this.greetings.some(g => m.includes(g))) return { type: 'greeting' };

        // Alerts
        if (this._any(m, this.kw.alerts)) return { type: 'alerts' };

        // Pivot problem (must have BOTH pivot + problem OR just problem)
        if (this._any(m, this.kw.problem) && this._any(m, this.kw.pivot))
            return { type: 'pivot_problem' };
        if (this._any(m, this.kw.problem) && !this._any(m, this.kw.crop))
            return { type: 'pivot_problem' };

        // Irrigation
        if (this._any(m, this.kw.irrigation)) {
            if (m.includes('النهارده') || m.includes('النهاردة') ||
                m.includes('اليوم') || m.includes('دلوقتي'))
                return { type: 'irrigation_today' };
            if (this._any(m, this.kw.hours))
                return { type: 'pump_hours' };
            return { type: 'irrigation_general' };
        }

        // Pump hours alone
        if (this._any(m, this.kw.hours)) return { type: 'pump_hours' };

        // Weather
        if (this._any(m, this.kw.weather)) return { type: 'weather' };

        // TVDI / soil dryness
        if (this._any(m, this.kw.tvdi)) return { type: 'tvdi' };

        // Disease
        if (this._any(m, this.kw.disease)) return { type: 'disease' };

        // WUE
        if (this._any(m, this.kw.wue)) return { type: 'wue' };

        // Biomass / production
        if (this._any(m, this.kw.biomass)) return { type: 'biomass' };

        // Crop / general status
        if (this._any(m, this.kw.crop) || this._any(m, this.kw.status))
            return { type: 'crop_status' };

        // Unclear — caller may fall back to online
        return { type: 'unclear' };
    }

    _any(text, list) {
        return list.some(w => text.includes(w));
    }

    // ─────────────────── Response Generator ────────────────────────────────

    respond(msg) {
        const intent = this.detectIntent(msg);
        return this._dispatch(intent.type);
    }

    _dispatch(type) {
        switch (type) {
            case 'greeting': return this._greeting();
            case 'alerts': return this._alerts();
            case 'pivot_problem': return this._pivotProblem();
            case 'irrigation_today': return this._irrigationToday();
            case 'irrigation_general': return this._irrigationGeneral();
            case 'pump_hours': return this._pumpHours();
            case 'crop_status': return this._cropStatus();
            case 'weather': return this._weather();
            case 'disease': return this._disease();
            case 'wue': return this._wue();
            case 'biomass': return this._biomass();
            case 'tvdi': return this._tvdi();
            default: return this._unclear();
        }
    }

    // ── Individual Response Methods ──────────────────────────────────────────

    _greeting() {
        const picks = [
            'أهلاً يا حاج! 🌾 عامل إيه؟ اسألني عن أرضك براحتك.',
            'مرحباً بيك! 👋 في خدمتك — اسأل عن الري، المحصول، أو البيفوت.',
            'السلام عليكم! أنا Mini-Tayseer — مساعدك الزراعي الذكي. 🌱',
            'أهلاً وسهلاً يا حاج! إيه اللي تعرفه عن مزرعتك تعايزه؟',
        ];
        return picks[Math.floor(Math.random() * picks.length)];
    }

    _alerts() {
        const d = this.fd;
        if (!d || !d.insights) return this._noData();
        const alerts = (d.insights.critical_alerts || []);
        if (!alerts.length)
            return '✅ ما فيش تنبيهات حرجة دلوقتي — المزرعة تمام!';
        let r = `🚨 **عندك ${alerts.length} تنبيه(ات):**\n\n`;
        alerts.forEach((a, i) => { r += `${i + 1}. 🔴 ${a}\n`; });
        return r;
    }

    _pivotProblem() {
        const d = this.fd;
        if (!d || !d.pivots) return this._noData();

        const bad = d.pivots.filter(p => p.issues && p.issues.length > 0);
        if (!bad.length)
            return '✅ كل البيفوتات شغالة تمام! مفيش أعطال. 🔄';

        let r = `🚨 **عندك ${bad.length} بيفوت فيهم مشاكل:**\n\n`;
        bad.forEach(pv => {
            r += `**${pv.name}`;
            if (pv.crop) r += ` (${pv.crop})`;
            r += `:**\n`;
            pv.issues.forEach(iss => {
                const sev = iss.severity === 'حرج' ? '🔴' : '🟡';
                const kind = iss.type === 'mechanical' ? 'عطل ميكانيكي' :
                    iss.type === 'electrical' ? 'عطل كهربي' :
                        iss.type === 'water_stress' ? 'إجهاد مائي' : 'مشكلة';
                r += `${sev} ${kind} — **${iss.component}**\n`;
                if (iss.tower) r += `   📍 في ${iss.tower}\n`;
                r += `   📝 ${iss.action}\n\n`;
            });
        });
        return r;
    }

    _irrigationToday() {
        const d = this.fd;
        if (!d || !d.insights) return this._noData();

        const advice = d.insights.irrigation_today || '';
        const tvdi = (d.overall || {}).tvdi || null;
        const tvdiTxt = tvdi !== null ? ` (TVDI: ${parseFloat(tvdi).toFixed(2)})` : '';

        if (advice.startsWith('لا') || (tvdi !== null && tvdi < 0.2)) {
            return `✅ **لا تروِ النهارده!**\n\nالأرض لسه رطبة${tvdiTxt}.\n💡 وفّر المياه والكهرباء — مفيش داعي للري دلوقتي.`;
        } else if (tvdi !== null && tvdi < 0.5) {
            return `🟡 **الأرض جافة نسبياً${tvdiTxt}.**\n\nراقب الرطوبة خلال 6–12 ساعة.\n💡 لو شفت ذبول في الأوراق، اشغل الطلمبة.`;
        } else {
            return `🔴 **اروِ دلوقتي!**\n\nالأرض جافة${tvdiTxt}.\n💡 شغّل الطلمبة فوراً لتجنب إجهاد الجذور.`;
        }
    }

    _irrigationGeneral() {
        const d = this.fd;
        if (!d || !d.overall) return this._noData();
        const et = parseFloat((d.overall.et_avg || 4.5).toFixed(2));
        return `💧 **معلومات الري:**\n\nمتوسط التبخر-النتح (ET): **${et} مم/يوم**\n\n` +
            (et > 6 ? '🔴 الاستهلاك مرتفع! قلل ساعات التشغيل 15–20%.' :
                et > 4.5 ? '🟡 الاستهلاك طبيعي مع مراقبة.' :
                    '✅ الاستهلاك ضمن المعدل الطبيعي.');
    }

    _pumpHours() {
        const d = this.fd;
        if (!d || !d.pivots) return this._noData();

        const total = d.pivots.reduce((s, p) => s + (parseFloat(p.pump_hours) || 0), 0);
        let r = `⏱️ **ساعات تشغيل الطلمبة:**\n\n`;
        r += `**الإجمالي:** ${total.toFixed(1)} ساعة لكل البيفوتات\n\n`;
        r += `**التفاصيل:**\n`;
        d.pivots.forEach(p => {
            const hrs = (parseFloat(p.pump_hours) || 0).toFixed(1);
            r += `• ${p.name}: **${hrs} ساعة** (${p.crop || 'محصول'})\n`;
        });
        return r;
    }

    _cropStatus() {
        const d = this.fd;
        if (!d || !d.overall) return this._noData();
        const ndvi = parseFloat((d.overall.ndvi_avg || 0.5).toFixed(3));
        const et = parseFloat((d.overall.et_avg || 4.5).toFixed(2));
        const status = d.overall.status || 'unknown';

        let emoji, msg;
        if (status === 'healthy' || ndvi >= 0.6) { emoji = '✅'; msg = 'المحصول صحي ونموه ممتاز'; }
        else if (status === 'warning' || ndvi >= 0.3) { emoji = '🟡'; msg = 'المحصول متوسط — راقب الري والتسميد'; }
        else { emoji = '🔴'; msg = 'المحصول في إجهاد! محتاج تدخل فوري'; }

        return `${emoji} **${msg}**\n\n🌱 NDVI: **${ndvi}** (${ndvi >= 0.6 ? 'ممتاز' : ndvi >= 0.3 ? 'متوسط' : 'منخفض'})\n` +
            `💧 ET: **${et} مم/يوم**\n` +
            (d.insights && d.insights.critical_alerts && d.insights.critical_alerts.length
                ? `\n🚨 فيه **${d.insights.critical_alerts.length}** تنبيه — اسألني "إيه التنبيهات؟"`
                : '\n✅ لا توجد تنبيهات حرجة.');
    }

    _weather() {
        const d = this.fd;
        if (!d || !d.weather || !d.weather.length)
            return '☁️ مفيش بيانات طقس محفوظة حالياً.\n💡 اتصل بالنت واضغط "تحديث" عشان أحمّل توقعات الطقس.';

        let r = '🌤️ **توقعات الطقس — 5 أيام:**\n\n';
        d.weather.slice(0, 5).forEach(day => {
            const icon = day.rain_probability > 30 ? '🌧️' :
                day.humidity > 60 ? '🌥️' : '☀️';
            r += `${icon} **${day.date}:** ${day.temp_min}°C – ${day.temp_max}°C | رطوبة ${day.humidity}% | ${day.description}\n`;
        });
        return r;
    }

    _disease() {
        const d = this.fd;
        const risk = ((d.insights || {}).disease_risk || 'منخفض');
        if (risk === 'منخفض' || risk === 'low')
            return '✅ **خطر الأمراض منخفض** حالياً 🌿\n\nالحمد لله المحصول سليم.\n💡 استمر في المراقبة الدورية كل أسبوع.';
        if (risk === 'متوسط' || risk === 'medium')
            return '🟡 **خطر متوسط** — راقب الأوراق كويس.\n\n🔍 دور على بقع أو اصفرار في الحواف. لو لقيت حاجة غريبة، صوّرها وأرسلها للمهندس.';
        return '🔴 **تحذير! خطر عالٍ.**\n\n🚨 افحص الأوراق فوراً واستشر مهندس زراعي. لا تأخر التدخل!';
    }

    _wue() {
        const d = this.fd;
        const wue = parseFloat(((d.overall || {}).wue_numeric || 0));
        const label = (d.insights || {}).water_waste || '';
        if (!wue && !label)
            return '💧 مفيش بيانات كفاءة المياه حالياً. اضغط "تحديث".';
        const status = wue >= 2.0 ? '🟢 كفاءة ممتازة' :
            wue >= 1.2 ? '🟡 كفاءة جيدة' : '🔴 إهدار للمياه';
        return `💧 **كفاءة استخدام المياه (WUE):** ${status}\n\n` +
            (wue ? `القيمة: **${wue.toFixed(3)} كجم/م³**\n` : '') +
            (label ? `${label}\n` : '') +
            (wue < 1.2 ? '\n💡 ابحث عن تسريبات في خط الري وقلّل التبخر الليلي.' : '');
    }

    _biomass() {
        const d = this.fd;
        if (!d || !d.overall) return this._noData();
        const b = parseFloat((d.overall.biomass_total || 0).toFixed(3));
        const fed = (b * 0.42).toFixed(3);
        const qirat = (b * 0.42 / 24).toFixed(4);
        const quality = b > 100 ? 'ممتازة 🏆' : b > 70 ? 'جيدة 👍' : 'متوسطة 🟡';
        return `🌾 **الكتلة الحيوية (الإنتاج المتوقع):**\n\n` +
            `• للهكتار: **${b.toFixed(2)} طن/هكتار**\n` +
            `• للفدان: **${fed} طن/فدان**\n` +
            `• للقيراط: **${qirat} طن/قيراط**\n\n` +
            `📊 الإنتاجية ${quality}`;
    }

    _tvdi() {
        const d = this.fd;
        const tvdi = parseFloat(((d.overall || {}).tvdi || 0.5));
        if (tvdi <= 0.2)
            return `✅ **TVDI = ${tvdi.toFixed(3)}** — الأرض رطبة. وفّر المياه اليوم!`;
        if (tvdi <= 0.5)
            return `🟡 **TVDI = ${tvdi.toFixed(3)}** — جفاف معتدل. راقب الرطوبة خلال 24 ساعة.`;
        return `🔴 **TVDI = ${tvdi.toFixed(3)}** — جفاف شديد! رَوِّ فوراً لتجنب الذبول.`;
    }

    _unclear() {
        return `معلش يا حاج، مش فاهم سؤالك كويس 😅\n\nجرّب تسأل عن:\n` +
            `• 💧 الري: "أروي النهارده؟"\n` +
            `• 🌿 المحصول: "إيه حالة الزرع؟"\n` +
            `• 🔧 البيفوت: "في عطل في البيفوت؟"\n` +
            `• 🌤️ الطقس: "الجو عامل إيه؟"\n` +
            `• ⏱️ الطلمبة: "كام ساعة أشغل الطلمبة؟"\n` +
            `• 🌾 الإنتاج: "إيه كمية الكتلة الحيوية؟"`;
    }

    _noData() {
        return '⚠️ مفيش بيانات محفوظة حالياً.\n\n💡 اتصل بالنت واضغط **"🔄 تحديث البيانات"** عشان أحمّل بيانات مزرعتك الأولى.';
    }
}

// Export
window.EgyptianNLP = EgyptianNLP;
