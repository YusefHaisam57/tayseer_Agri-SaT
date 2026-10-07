/**
 * reports.js — Tayseer Agri-SaT Step 9
 * Custom Reports System — Frontend Controller
 *
 * Handles:
 *   • Report type / format / period selection UI
 *   • Calling POST /api/reports/generate
 *   • Triggering file download on success
 *   • Loading + rendering report history
 *   • Deleting individual reports (DELETE /api/reports/delete/<id>)
 *   • Toast notifications
 *   • Live preview card update
 */

'use strict';

// ── State ──────────────────────────────────────────────────────
const cfg = {
  reportType:     'farm_summary',
  format:         'word',
  dateRangeDays:  7,
  customDateFrom: '',
  customDateTo:   '',
  emailDelivery:  false,
  recipientEmail: '',
};

// ── Report metadata maps ───────────────────────────────────────
const TYPE_NAMES = {
  farm_summary: 'ملخص المزرعة',
  water_usage:  'استهلاك المياه',
  pivot_health: 'صحة البيفوت',
};

const FORMAT_NAMES = {
  word:  'Word (.docx)',
  excel: 'Excel (.xlsx)',
  pdf:   'PDF (.pdf)',
};

const FORMAT_ICONS = {
  word:  '📄',
  excel: '📊',
  pdf:   '📑',
};

const TYPE_SECTIONS = {
  farm_summary: 'NDVI، مياه، توصيات، طقس',
  water_usage:  'ET يومي، ساعات ري، WUE، WWFI',
  pivot_health: 'NDVI-CV، جمود حراري، صيانة',
};

// Formats allowed per report type
const ALLOWED_FORMATS = {
  farm_summary: ['word', 'excel', 'pdf'],
  water_usage:  ['excel'],
  pivot_health: ['word', 'excel', 'pdf'],
};


// ── Select report type ─────────────────────────────────────────
function selectType(type) {
  if (!TYPE_NAMES[type]) return;
  cfg.reportType = type;

  // Update card highlights
  document.querySelectorAll('.type-card').forEach(card => {
    card.classList.toggle('active', card.id === `t-${type}`);
  });

  // Update format availability
  _updateFormatCards(type);
  _updatePreview();
}


// ── Select output format ───────────────────────────────────────
function selectFormat(fmt) {
  const allowed = ALLOWED_FORMATS[cfg.reportType] || ['word', 'excel', 'pdf'];
  if (!allowed.includes(fmt)) return; // disabled
  cfg.format = fmt;

  document.querySelectorAll('.format-card').forEach(card => {
    card.classList.toggle('active', card.id === `f-${fmt}`);
  });

  // Hide/show format note
  const note = document.getElementById('format-note');
  if (note) note.style.display = 'none';

  _updatePreview();
}


// ── Select preset period ───────────────────────────────────────
function selectPeriod(days, btn) {
  cfg.dateRangeDays  = days;
  cfg.customDateFrom = '';
  cfg.customDateTo   = '';

  // Clear custom date inputs
  const fromEl = document.getElementById('date-from');
  const toEl   = document.getElementById('date-to');
  if (fromEl) fromEl.value = '';
  if (toEl)   toEl.value   = '';

  document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  _updatePreview();
}


// ── Handle custom date range ───────────────────────────────────
function handleCustomDate() {
  const from = document.getElementById('date-from')?.value;
  const to   = document.getElementById('date-to')?.value;

  if (from && to) {
    if (to < from) {
      showToast('تاريخ النهاية يجب أن يكون بعد تاريخ البداية', 'error');
      return;
    }
    const msPerDay = 86400000;
    const days = Math.max(1, Math.round((new Date(to) - new Date(from)) / msPerDay));
    cfg.dateRangeDays  = days;
    cfg.customDateFrom = from;
    cfg.customDateTo   = to;

    // Deactivate preset buttons
    document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
    _updatePreview();
  }
}


// ── Toggle email delivery ──────────────────────────────────────
function toggleEmail(chk) {
  cfg.emailDelivery = chk.checked;
  const wrap = document.getElementById('email-input-wrap');
  if (wrap) wrap.style.display = chk.checked ? 'block' : 'none';
}


// ── Generate report ────────────────────────────────────────────
async function generateReport() {
  // Collect email
  if (cfg.emailDelivery) {
    const emailEl = document.getElementById('recipient-email');
    const email   = emailEl ? emailEl.value.trim() : '';
    if (!email) {
      showToast('من فضلك أدخل البريد الإلكتروني', 'error');
      return;
    }
    cfg.recipientEmail = email;
  }

  // Validate water_usage → excel
  if (cfg.reportType === 'water_usage' && cfg.format !== 'excel') {
    showToast('تقرير استهلاك المياه متاح بصيغة Excel فقط', 'error');
    _forceFormat('excel');
    return;
  }

  // Show loading state
  _setBtnLoading(true);

  try {
    const payload = {
      report_type:     cfg.reportType,
      format:          cfg.format,
      date_range_days: cfg.dateRangeDays,
      email_delivery:  cfg.emailDelivery,
      recipient_email: cfg.recipientEmail,
    };

    const res  = await fetch('/api/reports/generate', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });

    const json = await res.json();

    if (json.success) {
      showToast('✅ تم إنشاء التقرير بنجاح! جاري التحميل…', 'success');

      // Trigger file download
      setTimeout(() => {
        window.location.href = `/api/reports/download/${encodeURIComponent(json.filename)}`;
      }, 600);

      // Refresh history list
      setTimeout(loadHistory, 1200);

    } else {
      showToast(`❌ ${json.error || 'فشل إنشاء التقرير'}`, 'error');
    }

  } catch (err) {
    showToast(`❌ خطأ في الشبكة: ${err.message}`, 'error');
  } finally {
    _setBtnLoading(false);
  }
}


// ── Load & render report history ───────────────────────────────
async function loadHistory() {
  const container = document.getElementById('history-list');
  if (!container) return;

  try {
    const res  = await fetch('/api/reports/history');
    const json = await res.json();

    if (!json.success || !json.reports || json.reports.length === 0) {
      container.innerHTML = '<div class="empty-history">📭 لا توجد تقارير سابقة</div>';
      return;
    }

    container.innerHTML = json.reports.map(r => _buildHistoryItem(r)).join('');

  } catch (err) {
    container.innerHTML = '<div class="empty-history" style="color:#ef4444">⚠️ تعذّر تحميل السجل</div>';
  }
}


// ── Delete a report ────────────────────────────────────────────
async function deleteReport(id, btnEl) {
  if (!confirm('هل تريد حذف هذا التقرير نهائياً؟')) return;

  try {
    const res  = await fetch(`/api/reports/delete/${id}`, { method: 'DELETE' });
    const json = await res.json();

    if (json.success) {
      showToast('تم حذف التقرير', 'success');
      // Remove from DOM
      btnEl?.closest('.history-item')?.remove();
      // If list now empty, reload
      const container = document.getElementById('history-list');
      if (container && container.querySelectorAll('.history-item').length === 0) {
        container.innerHTML = '<div class="empty-history">📭 لا توجد تقارير سابقة</div>';
      }
    } else {
      showToast(json.error || 'فشل الحذف', 'error');
    }
  } catch (err) {
    showToast(`خطأ: ${err.message}`, 'error');
  }
}


// ── Toast notification ─────────────────────────────────────────
function showToast(message, type = 'success') {
  const toast = document.getElementById('toast');
  if (!toast) return;

  toast.textContent = message;
  toast.style.background = type === 'error' ? '#ef4444' : '#10b981';
  toast.style.display    = 'block';

  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { toast.style.display = 'none'; }, 4000);
}


// ── Private helpers ────────────────────────────────────────────

function _updateFormatCards(type) {
  const allowed  = ALLOWED_FORMATS[type] || ['word', 'excel', 'pdf'];
  const note     = document.getElementById('format-note');

  document.querySelectorAll('.format-card').forEach(card => {
    const fmt = card.id.replace('f-', '');
    const ok  = allowed.includes(fmt);
    card.classList.toggle('disabled', !ok);
    if (!ok) card.classList.remove('active');
  });

  // If current format is not allowed, pick first allowed
  if (!allowed.includes(cfg.format)) {
    cfg.format = allowed[0];
    const keep = document.getElementById(`f-${cfg.format}`);
    if (keep) keep.classList.add('active');
  }

  // Show note for restricted types
  if (note) {
    note.style.display = allowed.length === 1 ? 'block' : 'none';
  }
}

function _forceFormat(fmt) {
  cfg.format = fmt;
  document.querySelectorAll('.format-card').forEach(c => {
    c.classList.toggle('active', c.id === `f-${fmt}`);
  });
  _updatePreview();
}

function _updatePreview() {
  const iconEl = document.getElementById('pv-icon');
  const nameEl = document.getElementById('pv-name');
  const metaEl = document.getElementById('pv-meta');
  if (!iconEl || !nameEl || !metaEl) return;

  iconEl.textContent = FORMAT_ICONS[cfg.format] || '📄';
  nameEl.textContent = TYPE_NAMES[cfg.reportType] || '';

  const periodText = cfg.customDateFrom
    ? `${cfg.customDateFrom} → ${cfg.customDateTo}`
    : `آخر ${cfg.dateRangeDays} يوم`;

  metaEl.innerHTML = `
    الصيغة: ${FORMAT_NAMES[cfg.format]}<br>
    الفترة: ${periodText}<br>
    الأقسام: ${TYPE_SECTIONS[cfg.reportType] || '—'}
  `;
}

function _setBtnLoading(loading) {
  const btn      = document.getElementById('btn-gen');
  const spinner  = document.getElementById('gen-spinner');
  const btnText  = document.getElementById('btn-gen-text');
  if (!btn) return;

  btn.disabled = loading;
  if (spinner) spinner.style.display = loading ? 'block' : 'none';
  if (btnText) btnText.textContent   = loading ? 'جاري الإنشاء…' : '🚀 إنشاء التقرير وتحميله';
}

function _buildHistoryItem(r) {
  const icon = FORMAT_ICONS[r.format] || '📄';
  const typeAr = TYPE_NAMES[r.report_type] || r.report_type;
  const fmtAr  = FORMAT_NAMES[r.format]   || r.format;
  const date   = r.generated_at ? new Date(r.generated_at).toLocaleString('ar-EG') : '—';
  const emailed = r.emailed_to ? ` · أُرسل إلى ${r.emailed_to}` : '';

  return `
    <div class="history-item" data-id="${r.id}">
      <div class="hi-icon">${icon}</div>
      <div class="hi-info">
        <div class="hi-title">${typeAr} — ${fmtAr}</div>
        <div class="hi-meta">${date}${emailed}</div>
      </div>
      <a href="/api/reports/download/${encodeURIComponent(r.filename)}"
         class="hi-download"
         title="تحميل التقرير">⬇️</a>
      <button onclick="deleteReport(${r.id}, this)"
              title="حذف"
              style="background:none;border:none;color:#ef4444;cursor:pointer;
                     font-size:14px;padding:4px 6px;border-radius:5px;
                     transition:background .15s"
              onmouseover="this.style.background='rgba(239,68,68,.1)'"
              onmouseout="this.style.background='none'">🗑️</button>
    </div>
  `;
}


// ── Init ───────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  // Initialise default date input values
  const today  = new Date();
  const toEl   = document.getElementById('date-to');
  const fromEl = document.getElementById('date-from');
  if (toEl)   toEl.max   = today.toISOString().split('T')[0];
  if (fromEl) fromEl.max = today.toISOString().split('T')[0];

  _updatePreview();
  loadHistory();
});
