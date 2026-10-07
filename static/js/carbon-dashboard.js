/**
 * ═══════════════════════════════════════════════════════════════
 *  Tayseer Carbon-Sat V2.0 — Enterprise Dashboard Logic
 *  Target: Ismailia Poultry/Livestock Farm
 * ═══════════════════════════════════════════════════════════════
 */

let chartInstance = null;

// Show Toast Notification
function showToast(msg, type = 'info') {
    const container = document.getElementById('toasts');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = msg;
    container.appendChild(toast);
    
    // Trigger reflow for animation
    void toast.offsetWidth;
    toast.classList.add('show');
    
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// Top Banner
function showBanner(msg, type = 'info') {
    const b = document.getElementById('banner');
    b.textContent = msg;
    b.className = `info`; // can be extended
    b.classList.remove('hidden');
}

// ── Initialization ──
window.addEventListener('DOMContentLoaded', () => {
    initChart();
    // Load data automatically on start
    loadMatrix();
});

// ── API: Load Carbon Matrix (4 Layers) ──
async function loadMatrix() {
    const btn = document.getElementById('btn-matrix');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ جاري الاتصال بالأقمار الصناعية وتحليل البيانات...';
    }
    
    try {
        // Fallback to GET for simple retrieval, though POST can send specific polygons
        const res = await fetch('/api/carbon/matrix', {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        
        const data = await res.json();
        
        if (data.success) {
            updateDashboard(data);
            showToast('✅ تم تحديث بيانات المزرعة بنجاح', 'ok');
            if (data.demo_mode) {
                showBanner('⚠️ النظام يعمل في وضع المحاكاة (Demo Mode). لم يتم تفعيل GEE Service Account.', 'info');
            }
        } else {
            showToast('❌ خطأ في الخادم: ' + (data.error || 'Unknown error'), 'err');
        }
        
    } catch (err) {
        console.error('Matrix error:', err);
        showToast('❌ فشل الاتصال بالخادم', 'err');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '📡 تحديث كل الطبقات الأربع عبر الأقمار الصناعية';
        }
    }
}

// ── Update UI ──
function updateDashboard(data) {
    // 1. Summary Cards
    document.getElementById('s-som').textContent  = data.som_mg_kg.toFixed(0);
    document.getElementById('s-ncs').textContent  = data.ncs_gC_m2_year.toFixed(1);
    document.getElementById('s-heat').textContent = data.barn_heat_celsius.toFixed(1);
    document.getElementById('s-ch4').textContent  = data.ch4_index.toFixed(2);
    
    // 2. Layer 1: SOM
    document.getElementById('l-som').textContent = data.som_mg_kg.toFixed(0);
    // Range 0 - 8000
    let somPct = Math.min((data.som_mg_kg / 8000) * 100, 100);
    document.getElementById('som-bar').style.width = somPct + '%';
    document.getElementById('som-pct').textContent = somPct.toFixed(0) + '%';
    
    // 3. Layer 2: NCS
    document.getElementById('l-ncs').textContent = data.ncs_gC_m2_year.toFixed(1);
    let ncsStatus = 'متعادل';
    let ncsPct = 50; // center
    if (data.ncs_gC_m2_year > 0) {
        ncsStatus = 'ماص للكربون (Sink)';
        ncsPct = 50 + Math.min((data.ncs_gC_m2_year / 1000) * 50, 50);
    } else if (data.ncs_gC_m2_year < 0) {
        ncsStatus = 'مصدر كربون (Source)';
        ncsPct = 50 - Math.min((Math.abs(data.ncs_gC_m2_year) / 100) * 50, 50);
    }
    document.getElementById('ncs-bar').style.width = ncsPct + '%';
    document.getElementById('ncs-status').textContent = ncsStatus;
    
    // 4. Layer 3: Barn Heat (LST)
    document.getElementById('l-heat').textContent = data.barn_heat_celsius.toFixed(1);
    let heatClass = 'معتدل';
    // Assume range 20 to 45
    let heatPct = Math.max(0, Math.min(((data.barn_heat_celsius - 20) / 25) * 100, 100));
    if (data.barn_heat_celsius > 35) heatClass = 'ساخن جداً';
    else if (data.barn_heat_celsius < 25) heatClass = 'بارد';
    document.getElementById('heat-bar').style.width = heatPct + '%';
    document.getElementById('heat-class').textContent = heatClass;
    
    // 5. Layer 4: CH4 Index
    document.getElementById('l-ch4').textContent = data.ch4_index.toFixed(2);
    let ch4Class = 'متوسط';
    // Assume range 10 to 50
    let ch4Pct = Math.max(0, Math.min(((data.ch4_index - 10) / 40) * 100, 100));
    if (data.ch4_index > 35) ch4Class = 'بؤرة انبعاث عالية';
    else if (data.ch4_index < 20) ch4Class = 'انبعاثات منخفضة';
    document.getElementById('ch4-bar').style.width = ch4Pct + '%';
    document.getElementById('ch4-class').textContent = ch4Class;
    
    // Keep area in memory for credits calc
    window.farmAreaHa = data.area_ha || 18.6;
    window.farmNcs = data.ncs_gC_m2_year || 0;
    
    updateChart(data);
}

// ── Chart.js ──
function initChart() {
    const ctx = document.getElementById('emissions-chart');
    if (!ctx) return;
    
    chartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو'],
            datasets: [
                {
                    label: 'انبعاثات CH₄ (كجم)',
                    data: [0, 0, 0, 0, 0, 0],
                    backgroundColor: 'rgba(239, 68, 68, 0.7)', // Red
                    borderRadius: 4
                },
                {
                    label: 'انبعاثات NH₃ (كجم)',
                    data: [0, 0, 0, 0, 0, 0],
                    backgroundColor: 'rgba(245, 158, 11, 0.7)', // Amber
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'top', labels: { font: { family: 'Cairo' } } }
            },
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8' } },
                x: { grid: { display: false }, ticks: { color: '#94a3b8', font: { family: 'Cairo' } } }
            }
        }
    });
}

function updateChart(data) {
    if (!chartInstance) return;
    // Generate some mock historical data based on the current CH4 index
    const baseCh4 = data.ch4_index * 10;
    const baseNh3 = data.ch4_index * 3;
    
    const ch4Data = Array.from({length: 6}, () => baseCh4 + (Math.random() * 20 - 10));
    const nh3Data = Array.from({length: 6}, () => baseNh3 + (Math.random() * 5 - 2.5));
    
    chartInstance.data.datasets[0].data = ch4Data;
    chartInstance.data.datasets[1].data = nh3Data;
    chartInstance.update();
}

// ── Credits Calculator ──
function calcCredits() {
    const period = parseInt(document.getElementById('cr-period').value) || 365;
    const price = parseFloat(document.getElementById('cr-price').value) || 15;
    
    if (!window.farmAreaHa) {
        showToast('يرجى تحديث بيانات الأقمار الصناعية أولاً', 'err');
        return;
    }
    
    // Basic calculation based on NCS and Area
    // NCS is g C/m2/year. 
    // Total C sequestered = NCS * area_m2 / 1000 = kg C/year
    const areaM2 = window.farmAreaHa * 10000;
    const totalC_kg_year = window.farmNcs * areaM2 / 1000;
    
    // Scale by period
    const totalC_kg_period = totalC_kg_year * (period / 365);
    const totalC_tons = totalC_kg_period / 1000;
    
    // Mock emissions to calculate a net balance (assume farm emits 40% of what it sequesters if sink, else just emissions)
    let emissions_tons = 0;
    let seq_tons = 0;
    let net_tons = 0;
    
    if (totalC_tons > 0) {
        seq_tons = totalC_tons;
        emissions_tons = seq_tons * 0.4; // arbitrary mock
        net_tons = seq_tons - emissions_tons;
    } else {
        emissions_tons = Math.abs(totalC_tons) + 10; // arbitrary mock
        seq_tons = 0;
        net_tons = -emissions_tons;
    }
    
    const value = net_tons > 0 ? net_tons * price : 0;
    
    document.getElementById('cr-emissions').textContent = emissions_tons.toFixed(2);
    document.getElementById('cr-seq').textContent = seq_tons.toFixed(2);
    document.getElementById('cr-bal').textContent = net_tons.toFixed(2);
    document.getElementById('cr-val').textContent = value.toFixed(2);
    
    document.getElementById('cr-result').classList.remove('hidden');
    
    const msgDiv = document.getElementById('cr-msg');
    if (net_tons > 0) {
        msgDiv.innerHTML = `✅ <strong>تهانينا!</strong> مزرعتك تعتبر مخزناً للكربون وتستحق رصيداً بقيمة <strong>$${value.toFixed(2)}</strong> بناءً على الأسعار الحالية.`;
        msgDiv.style.color = '#10b981';
    } else {
        msgDiv.innerHTML = `⚠️ <strong>تنبيه:</strong> المزرعة حالياً مصدر للانبعاثات الكربونية. يرجى مراجعة إدارة القطيع والمخلفات لتحسين البصمة الكربونية.`;
        msgDiv.style.color = '#ef4444';
    }
}
