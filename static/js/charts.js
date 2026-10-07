/**
 * Tayseer-AgriSaT — charts.js
 * Advanced Charts System — Chart.js 4.x
 * Supports: NDVI, ET, Biomass, WUE, TVDI
 * Author: Tayseer Platform v4.0  |  2026
 */

// ─── Constants ──────────────────────────────────────────────────────────────

const CHART_PARAMS = {
    ndvi: {
        label: 'NDVI — مؤشر الغطاء النباتي',
        type: 'line',
        fill: true,
        color: '#10b981',
        bgColor: 'rgba(16,185,129,0.12)',
        unit: '-',
        yMin: 0, yMax: 1,
        thresholds: [
            { y: 0.3, color: '#ef4444', dash: [6, 4], label: 'حد الخطر (0.3)' },
            { y: 0.6, color: '#10b981', dash: [6, 4], label: 'صحي (0.6+)' },
        ],
    },
    et: {
        label: 'ET — التبخر-النتح (مم/يوم)',
        type: 'bar',
        fill: false,
        color: '#3b82f6',
        bgColor: 'rgba(59,130,246,0.70)',
        unit: 'مم/يوم',
        yMin: 0, yMax: null,
        thresholds: [
            { y: 4.5, color: '#10b981', dash: [5, 5], label: 'الكمية الموصى بها (4.5)' },
            { y: 6.0, color: '#ef4444', dash: [5, 5], label: 'حد الإهدار (6.0)' },
        ],
    },
    biomass: {
        label: 'كتلة حيوية — طن/فدان',
        type: 'line',
        fill: true,
        color: '#fbbf24',
        bgColor: 'rgba(251,191,36,0.20)',
        unit: 'طن/فدان',
        yMin: 0, yMax: null,
        thresholds: [],
    },
    wue: {
        label: 'WUE — كفاءة المياه (كجم/م³)',
        type: 'bar',
        fill: false,
        color: null,   // per-bar color
        bgColor: null,
        unit: 'كجم/م³',
        yMin: 0, yMax: null,
        thresholds: [
            { y: 2.0, color: '#10b981', dash: [5, 5], label: 'ممتاز (2.0+)' },
            { y: 1.2, color: '#fbbf24', dash: [5, 5], label: 'جيد (1.2+)' },
        ],
    },
    tvdi: {
        label: 'TVDI — مؤشر الجفاف',
        type: 'bar',
        fill: false,
        color: null,
        bgColor: null,
        unit: '-',
        yMin: 0, yMax: 1,
        thresholds: [
            { y: 0.2, color: '#10b981', dash: [5, 5], label: 'رطب (< 0.2)' },
            { y: 0.5, color: '#ef4444', dash: [5, 5], label: 'جاف (> 0.5)' },
        ],
    },
};

// ─── Utility ─────────────────────────────────────────────────────────────────

function _wueColor(v) {
    if (v >= 2.0) return '#10b981';
    if (v >= 1.2) return '#fbbf24';
    return '#ef4444';
}

function _tvdiColor(v) {
    if (v <= 0.2) return '#10b981';
    if (v <= 0.5) return '#fbbf24';
    return '#ef4444';
}

function _shortDate(iso) {
    // "2026-03-12" → "12/3"
    const p = iso.split('-');
    return p[2].replace(/^0/, '') + '/' + p[1].replace(/^0/, '');
}

// ─── ChartManager Class ──────────────────────────────────────────────────────

class ChartManager {
    constructor(canvasId) {
        this._canvasId = canvasId;
        this._instance = null;
        this._lastDates = [];
        this._lastVals = [];
        this._lastParam = 'ndvi';
    }

    /**
     * Build annotation plugin objects for threshold lines.
     */
    _buildAnnotations(param) {
        const meta = CHART_PARAMS[param] || CHART_PARAMS.ndvi;
        const anns = {};
        (meta.thresholds || []).forEach((t, i) => {
            anns['thr' + i] = {
                type: 'line',
                yMin: t.y,
                yMax: t.y,
                borderColor: t.color,
                borderWidth: 1.5,
                borderDash: t.dash || [5, 5],
                label: {
                    content: t.label,
                    display: true,
                    position: 'start',
                    color: t.color,
                    font: { size: 10, family: 'Cairo' },
                    padding: 3,
                    backgroundColor: 'rgba(0,0,0,0.55)',
                }
            };
        });
        return anns;
    }

    /**
     * Build the Chart.js dataset for a given param.
     */
    _buildDataset(param, labels, values) {
        const meta = CHART_PARAMS[param] || CHART_PARAMS.ndvi;
        let bgColor, pointColor;

        if (param === 'wue') {
            bgColor = values.map(_wueColor);
            pointColor = values.map(_wueColor);
        } else if (param === 'tvdi') {
            bgColor = values.map(_tvdiColor);
            pointColor = values.map(_tvdiColor);
        } else {
            bgColor = meta.bgColor;
            pointColor = meta.color;
        }

        const dataset = {
            label: meta.label,
            data: values,
            borderColor: meta.color || '#6366f1',
            backgroundColor: bgColor,
            tension: 0.4,
            fill: meta.fill,
            pointRadius: values.length > 20 ? 2 : 4,
            borderWidth: 2,
        };
        return dataset;
    }

    /**
     * Render or update the chart with new data.
     */
    render(param, labels, values) {
        this._lastParam = param;
        this._lastDates = labels;
        this._lastVals = values;

        const meta = CHART_PARAMS[param] || CHART_PARAMS.ndvi;
        const canvas = document.getElementById(this._canvasId);
        if (!canvas) return;

        const shortLabels = labels.map(_shortDate);

        const chartConfig = {
            type: meta.type,
            data: {
                labels: shortLabels,
                datasets: [this._buildDataset(param, shortLabels, values)],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 600, easing: 'easeInOutQuart' },
                interaction: { intersect: false, mode: 'index' },
                plugins: {
                    legend: {
                        display: true,
                        labels: { color: '#d1fae5', font: { family: 'Cairo', size: 12 } },
                    },
                    tooltip: {
                        backgroundColor: 'rgba(16,17,26,0.95)',
                        titleColor: '#4ade80',
                        bodyColor: '#d1fae5',
                        titleFont: { family: 'Cairo', size: 13, weight: '700' },
                        bodyFont: { family: 'Cairo', size: 12 },
                        padding: 10,
                        callbacks: {
                            label: (ctx) => {
                                const v = ctx.parsed.y;
                                if (param === 'biomass') {
                                    const fed = v.toFixed(3);
                                    const ha = (v / 0.42).toFixed(3);
                                    return `${fed} طن/فدان  |  ${ha} طن/هكتار`;
                                }
                                return `${meta.label}: ${v.toFixed ? v.toFixed(3) : v} ${meta.unit}`;
                            },
                        },
                    },
                    annotation: {
                        annotations: this._buildAnnotations(param),
                    },
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255,255,255,0.06)' },
                        ticks: { color: '#9ca3af', font: { family: 'Cairo', size: 10 } },
                    },
                    y: {
                        min: meta.yMin !== null ? meta.yMin : undefined,
                        max: meta.yMax !== null ? meta.yMax : undefined,
                        grid: { color: 'rgba(255,255,255,0.06)' },
                        ticks: { color: '#9ca3af', font: { family: 'Cairo', size: 10 } },
                        title: {
                            display: !!meta.unit,
                            text: meta.unit,
                            color: '#6b7280',
                            font: { family: 'Cairo', size: 11 },
                        },
                    },
                },
            },
        };

        // Destroy old chart first
        if (this._instance) {
            this._instance.destroy();
            this._instance = null;
        }

        // Re-register annotation plugin if available
        if (window.ChartAnnotation && typeof window.ChartAnnotation !== 'undefined') {
            if (!Chart.registry.plugins.get('annotation')) {
                Chart.register(window.ChartAnnotation);
            }
        }

        this._instance = new Chart(canvas, chartConfig);
    }

    /** Export current chart as PNG download */
    download(filename) {
        if (!this._instance) return;
        const url = this._instance.toBase64Image('image/png', 1.0);
        const link = document.createElement('a');
        link.download = filename || ('tayseer-' + this._lastParam + '-' + new Date().toISOString().slice(0, 10) + '.png');
        link.href = url;
        link.click();
    }
}

// ─── Time Period Builder ─────────────────────────────────────────────────────

const TIME_PERIODS = {
    '5days': { label: '5 أيام', api: '5days' },
    '10days': { label: '10 أيام', api: '10days' },
    '1month': { label: 'شهر كامل', api: '1month' },
    '3months': { label: '3 شهور (الموسم)', api: '3months' },
};

// ─── Client-side Interpretation Fallback ─────────────────────────────────────
// (mirrors server logic; used for instant UI feedback)

function interpretClientSide(values, param) {
    if (!values || !values.length) {
        return { status: 'unknown', icon: '⏳', label_ar: 'لا توجد بيانات', trend: 'stable', trend_ar: 'مستقر', recommendation: 'لا توجد بيانات كافية.', current: 0, average: 0 };
    }
    const current = values[values.length - 1];
    const previous = values.length > 1 ? values[values.length - 2] : current;
    const avg = values.reduce((a, b) => a + b, 0) / values.length;
    const delta = current - previous;

    let trend = 'stable', trend_ar = '➡️ مستقر';
    if (delta > 0.005) { trend = 'improving'; trend_ar = '📈 يتحسن'; }
    else if (delta < -0.005) { trend = 'declining'; trend_ar = '📉 يتراجع'; }

    let status, icon, label_ar, rec;

    if (param === 'ndvi') {
        if (current >= 0.6) { status = 'healthy'; icon = '✅'; label_ar = 'المحصول صحي'; rec = 'استمر في نفس برنامج الري.'; }
        else if (current >= 0.3) { status = 'warning'; icon = '⚠️'; label_ar = 'توتر معتدل'; rec = 'تحقق من جدول الري.'; }
        else { status = 'critical'; icon = '🔴'; label_ar = 'إجهاد شديد!'; rec = 'ارو الآن. تأخير يسبب خسارة.'; }
    } else if (param === 'et') {
        if (current <= 4.5) { status = 'healthy'; icon = '✅'; label_ar = 'استهلاك طبيعي'; rec = 'مثالي.'; }
        else if (current <= 6.0) { status = 'warning'; icon = '⚠️'; label_ar = 'استهلاك مرتفع'; rec = 'قلل 15–20%.'; }
        else { status = 'critical'; icon = '🔴'; label_ar = 'هدر مياه!'; rec = 'قلل فوراً.'; }
    } else if (param === 'biomass') {
        if (current >= 1.5) { status = 'healthy'; icon = '✅'; label_ar = 'إنتاج جيد جداً'; rec = 'ممتاز.'; }
        else if (current >= 0.5) { status = 'warning'; icon = '⚠️'; label_ar = 'أقل من المتوسط'; rec = 'راجع الأسمدة.'; }
        else { status = 'critical'; icon = '🔴'; label_ar = 'كتلة منخفضة جداً'; rec = 'فحص تربة عاجل.'; }
    } else if (param === 'wue') {
        if (current >= 2.0) { status = 'healthy'; icon = '✅'; label_ar = 'كفاءة ممتازة'; rec = 'حافظ على هذا المستوى.'; }
        else if (current >= 1.2) { status = 'warning'; icon = '⚠️'; label_ar = 'كفاءة متوسطة'; rec = 'ضبط توقيت الري.'; }
        else { status = 'critical'; icon = '🔴'; label_ar = 'إهدار للمياه'; rec = 'تحقق من تسريبات.'; }
    } else if (param === 'tvdi') {
        if (current <= 0.2) { status = 'healthy'; icon = '✅'; label_ar = 'التربة رطبة'; rec = 'وفّر المياه اليوم.'; }
        else if (current <= 0.5) { status = 'warning'; icon = '⚠️'; label_ar = 'جفاف معتدل'; rec = 'استعد للري.'; }
        else { status = 'critical'; icon = '🔴'; label_ar = 'جفاف شديد!'; rec = 'ابدأ الري الآن.'; }
    } else {
        status = 'healthy'; icon = '✅'; label_ar = 'طبيعي'; rec = '';
    }

    return {
        status, icon, label_ar, trend, trend_ar, recommendation: rec,
        current: +current.toFixed(4), average: +avg.toFixed(4)
    };
}

// ─── Unit Conversion Panel Helper ────────────────────────────────────────────

function updateUnitPanel(param, value) {
    const panel = document.getElementById('unit-conversion-panel');
    if (!panel) return;

    if (param === 'biomass') {
        const hai = value;
        const fed = (value * 0.42).toFixed(3);
        const qirat = (value * 0.42 / 24).toFixed(4);
        panel.innerHTML = `
        <div class="flex gap-4 flex-wrap justify-center text-center">
            <div style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:10px 18px;">
                <p style="font-size:10px;color:#9ca3af;">للهكتار</p>
                <p style="font-size:1.4rem;font-weight:700;color:#fbbf24;">${hai.toFixed(3)} طن</p>
            </div>
            <div style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:10px 18px;">
                <p style="font-size:10px;color:#9ca3af;">للفدان</p>
                <p style="font-size:1.4rem;font-weight:700;color:#4ade80;">${fed} طن</p>
            </div>
            <div style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:10px 18px;">
                <p style="font-size:10px;color:#9ca3af;">للقيراط</p>
                <p style="font-size:1.4rem;font-weight:700;color:#60a5fa;">${qirat} طن</p>
            </div>
        </div>`;
        panel.style.display = 'block';
    } else {
        panel.style.display = 'none';
    }
}

// ─── TVDI Explanation Panel ───────────────────────────────────────────────────

function updateTvdiPanel(param, current) {
    const panel = document.getElementById('tvdi-explanation-panel');
    if (!panel) return;
    if (param !== 'tvdi') { panel.style.display = 'none'; return; }

    let msg, color;
    if (current <= 0.2) { msg = `TVDI الحالي: ${current.toFixed(3)} → الأرض رطبة، وفّر المياه اليوم`; color = '#10b981'; }
    else if (current <= 0.5) { msg = `TVDI الحالي: ${current.toFixed(3)} → جفاف معتدل، راقب خلال 24 ساعة`; color = '#fbbf24'; }
    else { msg = `TVDI الحالي: ${current.toFixed(3)} → جفاف شديد! رَوِّ فوراً`; color = '#ef4444'; }

    panel.innerHTML = `
    <div style="border-right:4px solid ${color};padding:8px 14px;background:${color}12;border-radius:8px;">
        <h5 style="font-size:12px;color:${color};font-weight:700;margin:0 0 6px;">💡 إزاي تستفيد من رقم TVDI؟</h5>
        <ul style="font-size:11px;color:#d1fae5;margin:0;padding:0;list-style:none;line-height:1.8;">
            <li>✅ 0.0 – 0.2 : أرض رطبة، لا تروِ</li>
            <li>🟡 0.2 – 0.5 : معتدل، راقب</li>
            <li>🔴 0.5 – 1.0 : جاف! رَوِّ فوراً</li>
        </ul>
        <p style="font-size:11px;color:#9ca3af;margin:6px 0 0;">${msg}</p>
    </div>`;
    panel.style.display = 'block';
}

// ─── Status Panel Renderer ────────────────────────────────────────────────────

function renderStatusPanel(interp, unit) {
    const panel = document.getElementById('chart-status-panel');
    if (!panel || !interp) return;

    const colors = { healthy: '#10b981', warning: '#f59e0b', critical: '#ef4444', unknown: '#6b7280' };
    const c = colors[interp.status] || '#6b7280';

    panel.innerHTML = `
    <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:10px 14px;
        background:${c}12;border:1px solid ${c}40;border-radius:12px;">
        <span style="font-size:1.5rem;">${interp.icon}</span>
        <div style="flex:1;min-width:150px;">
            <p style="font-weight:700;color:${c};font-size:13px;margin:0;">${interp.label_ar}</p>
            <p style="color:#9ca3af;font-size:11px;margin:2px 0 0;">${interp.trend_ar} &nbsp;|&nbsp;
               الحالي: <strong style="color:#d1fae5;">${interp.current} ${unit || ''}</strong> &nbsp;
               المتوسط: <strong style="color:#d1fae5;">${interp.average} ${unit || ''}</strong>
            </p>
        </div>
        <div style="max-width:320px;">
            <p style="font-size:12px;color:#d1fae5;margin:0;">💡 ${interp.recommendation}</p>
        </div>
    </div>`;
}

// Export public API
window.TayseerCharts = {
    CHART_PARAMS,
    ChartManager,
    TIME_PERIODS,
    interpretClientSide,
    updateUnitPanel,
    updateTvdiPanel,
    renderStatusPanel,
};
