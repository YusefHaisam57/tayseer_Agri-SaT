# ============================================================
# report_generator.py — Tayseer Agri-SaT Report Engine
#
# Generates professional farm reports in multiple formats:
#   • Word  (.docx) — python-docx
#   • Excel (.xlsx) — openpyxl
#   • PDF         — reportlab
#   • PNG chart   — matplotlib
#
# Usage:
#   gen = ReportGenerator(user_id, user_name, farm_data)
#   path = gen.generate_farm_summary_word(date_range_days=30)
#   path = gen.generate_water_usage_excel(date_range_days=30)
#   path = gen.generate_farm_summary_pdf(date_range_days=30)
# ============================================================

import os
import io
import json
import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# ── Output directory (temp-friendly) ─────────────────────────
_REPORTS_DIR = Path(tempfile.gettempdir()) / "tayseer_reports"
_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Arabic month names ────────────────────────────────────────
AR_MONTHS = [
    '', 'يناير','فبراير','مارس','أبريل','مايو','يونيو',
    'يوليو','أغسطس','سبتمبر','أكتوبر','نوفمبر','ديسمبر'
]

def _fmt_date(dt: datetime) -> str:
    return f"{dt.day} {AR_MONTHS[dt.month]} {dt.year}"

def _status_ar(status: str) -> str:
    return {'healthy':'ممتاز','warning':'متوسط'}.get(status, 'يحتاج انتباه')

def _status_emoji(status: str) -> str:
    return {'healthy':'✅','warning':'⚠️'}.get(status, '🔴')


# ── NDVI chart image (matplotlib) ────────────────────────────

def _generate_ndvi_chart(ndvi_series: list, farm_name: str) -> str | None:
    """
    Generate NDVI timeseries chart as PNG.
    ndvi_series: list of (date_str, ndvi_float)
    Returns path to saved PNG, or None on failure.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import numpy as np

        dates  = [datetime.strptime(d, '%Y-%m-%d') for d, _ in ndvi_series]
        values = [v for _, v in ndvi_series]

        fig, ax = plt.subplots(figsize=(10, 4), dpi=120)
        fig.patch.set_facecolor('#0f1e3a')
        ax.set_facecolor('#1a2d3f')

        # Fill under line
        ax.fill_between(dates, values, alpha=0.25, color='#10b981')
        ax.plot(dates, values, color='#10b981', linewidth=2.5, marker='o',
                markersize=5, markerfacecolor='white')

        # Healthy threshold line
        ax.axhline(y=0.5, color='#f59e0b', linestyle='--', linewidth=1.2,
                   alpha=0.7, label='حد الصحة 0.50')

        ax.set_title(f'تطور NDVI — {farm_name}', fontsize=13, color='white', pad=10)
        ax.set_ylabel('NDVI', color='#94a3b8')
        ax.tick_params(colors='#94a3b8', labelsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.spines['bottom'].set_color('#374151')
        ax.spines['left'].set_color('#374151')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(axis='y', color='#374151', linestyle='--', alpha=0.4)
        ax.legend(facecolor='#1a2d3f', edgecolor='#374151', labelcolor='#94a3b8',
                  fontsize=8)
        plt.tight_layout()

        fname = _REPORTS_DIR / f"ndvi_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        plt.savefig(fname, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close(fig)
        return str(fname)

    except Exception as e:
        log.warning(f"[Report] Chart generation failed: {e}")
        return None


def _generate_bar_chart(labels: list, values: list, title: str, ylabel: str) -> str | None:
    """Generate a bar chart for water usage / pump hours."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 4), dpi=120)
        fig.patch.set_facecolor('#f9fafb')
        ax.set_facecolor('#ffffff')

        colors = ['#10b981' if v < max(values) * 0.85 else '#f59e0b' for v in values]
        ax.bar(range(len(values)), values, color=colors, edgecolor='white', linewidth=0.5)
        ax.set_title(title, fontsize=13, pad=10)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45, fontsize=7, ha='right')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(axis='y', alpha=0.3)
        plt.tight_layout()

        fname = _REPORTS_DIR / f"bar_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        plt.savefig(fname, bbox_inches='tight')
        plt.close(fig)
        return str(fname)

    except Exception as e:
        log.warning(f"[Report] Bar chart failed: {e}")
        return None


# ════════════════════════════════════════════════════════════
# ReportGenerator — Main class
# ════════════════════════════════════════════════════════════

class ReportGenerator:
    """
    Generates farm reports in Word / Excel / PDF formats.

    Parameters
    ----------
    user_id    : int   — current user's DB id
    user_name  : str   — display name
    farm_data  : dict  — from database.get_latest_farm_data()
    history    : list  — list of Farm_Data dicts (oldest→newest)
    farm_name  : str   — official farm name
    """

    BRAND = "🛰️ Tayseer Agri-SaT"
    GREEN = "10b981"
    DARK  = "1a2d3f"

    def __init__(self, user_id: int, user_name: str, farm_data: dict,
                 history: list = None, farm_name: str = "المزرعة"):
        self.user_id   = user_id
        self.user_name = user_name
        self.fd        = farm_data or {}
        self.history   = history  or []
        self.farm_name = farm_name
        self.now       = datetime.now()

    # ── Helper: NDVI series from history ─────────────────────

    def _ndvi_series(self, days: int) -> list:
        """Return list of (date_str, ndvi) for last N days."""
        cutoff = self.now - timedelta(days=days)
        rows = [
            (r.get('date',''), float(r.get('ndvi') or 0))
            for r in self.history
            if r.get('date','') >= cutoff.strftime('%Y-%m-%d')
            and r.get('ndvi') is not None
        ]
        # If no history, do not fake a 30-day sinusoidal curve
        if not rows:
            if self.fd.get('ndvi') is not None:
                d = self.fd.get('date') or self.now.strftime('%Y-%m-%d')
                rows = [(d, round(float(self.fd.get('ndvi')), 3))]
            else:
                rows = []
        return rows

    def _pump_series(self, days: int) -> list:
        """Return list of (date_str, pump_hours)."""
        cutoff = self.now - timedelta(days=days)
        rows = [
            (r.get('date',''), float(r.get('pump_hours') or 0))
            for r in self.history
            if r.get('date','') >= cutoff.strftime('%Y-%m-%d')
        ]
        if not rows:
            if self.fd.get('pump_hours') is not None:
                d = self.fd.get('date') or self.now.strftime('%Y-%m-%d')
                rows = [(d, round(float(self.fd.get('pump_hours')), 1))]
            else:
                rows = []
        return rows

    # ─────────────────────────────────────────────────────────
    # 1. WORD REPORT
    # ─────────────────────────────────────────────────────────

    def generate_farm_summary_word(self, date_range_days: int = 30) -> str:
        """Generate comprehensive farm summary as .docx. Returns file path."""
        try:
            from docx import Document
            from docx.shared import Inches, Pt, RGBColor, Cm
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.oxml.ns import qn
            from docx.oxml   import OxmlElement
        except ImportError:
            raise RuntimeError("python-docx غير مثبت. نفّذ: pip install python-docx")

        doc = Document()

        # ── Page size A4 ──────────────────────────────────────
        sec = doc.sections[0]
        sec.page_height = Cm(29.7)
        sec.page_width  = Cm(21.0)
        sec.top_margin    = Cm(2)
        sec.bottom_margin = Cm(2)
        sec.left_margin   = Cm(2.5)
        sec.right_margin  = Cm(2.5)

        def _header_para(text, level=0, center=True):
            h = doc.add_heading('', level=level)
            r = h.add_run(text)
            r.font.size  = Pt(20 - level * 3)
            r.font.bold  = True
            r.font.color.rgb = RGBColor(0x10, 0xb9, 0x81)
            if center:
                h.alignment = WD_ALIGN_PARAGRAPH.CENTER
            return h

        def _set_rtl(para):
            """Set paragraph to RTL."""
            pPr = para._p.get_or_add_pPr()
            bidi = OxmlElement('w:bidi')
            pPr.append(bidi)

        # ── Cover ─────────────────────────────────────────────
        doc.add_paragraph()
        _header_para('🛰️ Tayseer Agri-SaT', level=0)
        doc.add_paragraph()
        _header_para('تقرير شامل للمزرعة', level=1)
        doc.add_paragraph()

        # Info table
        tbl = doc.add_table(rows=5, cols=2)
        tbl.style = 'Light Grid Accent 1'
        info = [
            ('اسم المزرعة',   self.farm_name),
            ('المالك',         self.user_name),
            ('تاريخ التقرير', _fmt_date(self.now)),
            ('الفترة المشمولة', f'آخر {date_range_days} يوم'),
            ('المصدر',         'Sentinel-2 GEE + ML Analytics'),
        ]
        for i, (k, v) in enumerate(info):
            c0, c1 = tbl.rows[i].cells
            c0.text = k
            c0.paragraphs[0].runs[0].bold = True
            c1.text = v

        doc.add_page_break()

        # ── Section 1: Overall Status ─────────────────────────
        _header_para('📊 الحالة العامة', level=1, center=False)

        ndvi = float(self.fd.get('ndvi') or 0)
        et   = float(self.fd.get('et')   or 0)
        ph   = float(self.fd.get('pump_hours') or 0)
        bm   = float(self.fd.get('biomass') or 0)
        wue  = float(self.fd.get('wue') or 0)
        tvdi = float(self.fd.get('tvdi') or 0)
        st   = self.fd.get('status', 'healthy')

        p = doc.add_paragraph()
        r = p.add_run(f'{_status_emoji(st)} الحالة: {_status_ar(st)}')
        r.bold = True
        r.font.size = Pt(13)

        doc.add_paragraph(
            'تم تحليل المزرعة باستخدام بيانات الأقمار الصناعية Sentinel-2 '
            'والنماذج الفيزيائية والذكاء الاصطناعي.'
        )

        # Metrics table
        _header_para('المؤشرات الرئيسية', level=2, center=False)
        mt = doc.add_table(rows=6, cols=3)
        mt.style = 'Medium Grid 1 Accent 1'
        # Header row
        for txt, cell in zip(['المؤشر','القيمة','الوصف'], mt.rows[0].cells):
            cell.text = txt
            cell.paragraphs[0].runs[0].bold = True

        metrics = [
            ('🌿 NDVI',          f'{ndvi:.3f}',             'صحة الغطاء النباتي (0.0–1.0)'),
            ('💧 ET',            f'{et:.2f} مم/يوم',         'معدل التبخر اليومي'),
            ('⏱️ ساعات الري',    f'{ph:.1f} ساعة',           'وقت تشغيل الطلمبة اليومي'),
            ('🌾 الكتلة الحيوية', f'{bm:.1f} طن/هكتار',      'تقدير إنتاجية المحصول'),
            ('💦 WUE',           f'{wue:.2f} كجم/م³',        'كفاءة استخدام المياه'),
        ]
        for i, (lbl, val, desc) in enumerate(metrics, start=1):
            mt.rows[i].cells[0].text = lbl
            mt.rows[i].cells[1].text = val
            mt.rows[i].cells[2].text = desc

        doc.add_paragraph()

        # ── Section 2: NDVI Chart ─────────────────────────────
        _header_para('📈 تطور مؤشر NDVI', level=1, center=False)
        ndvi_ser  = self._ndvi_series(date_range_days)
        chart_png = _generate_ndvi_chart(ndvi_ser, self.farm_name)
        if chart_png and os.path.exists(chart_png):
            doc.add_picture(chart_png, width=Inches(5.5))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            try:
                os.remove(chart_png)
            except Exception:
                pass
        else:
            doc.add_paragraph('(الرسم البياني غير متاح — تحقق من مكتبة matplotlib)')

        doc.add_page_break()

        # ── Section 3: Water Usage ────────────────────────────
        _header_para('💧 استهلاك المياه', level=1, center=False)
        pump_ser     = self._pump_series(date_range_days)
        total_pump   = sum(v for _, v in pump_ser)
        total_water  = total_pump * 120  # 120 m³/hr default flow rate

        doc.add_paragraph(
            f'إجمالي ساعات الري خلال {date_range_days} يوم: '
            f'{total_pump:.1f} ساعة — '
            f'ما يعادل تقريباً {total_water:,.0f} م³ من المياه المستخدمة.'
        )

        # Bar chart for pump hours
        bar_labels = [d[-5:] for d, _ in pump_ser[-14:]]  # last 14 days mm/dd
        bar_values = [v        for _, v in pump_ser[-14:]]
        bar_png    = _generate_bar_chart(bar_labels, bar_values,
                                          'ساعات الري اليومية (آخر 14 يوم)', 'ساعات')
        if bar_png and os.path.exists(bar_png):
            doc.add_picture(bar_png, width=Inches(5.5))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            try:
                os.remove(bar_png)
            except Exception:
                pass

        doc.add_page_break()

        # ── Section 4: Recommendations ────────────────────────
        _header_para('💡 التوصيات والإرشادات', level=1, center=False)

        irr_advice = (
            '✅ المحصول بحالة جيدة — استمر في جدول الري الحالي ومراقبة مؤشر NDVI يومياً.'
            if ndvi >= 0.55 else
            f'💧 يُوصى بزيادة وقت الري إلى {ph * 1.2:.1f} ساعة يومياً لتحسين رطوبة التربة.'
        )
        p = doc.add_paragraph()
        p.add_run('توصية الري:  ').bold = True
        p.add_run(irr_advice)

        if ndvi < 0.3:
            pw = doc.add_paragraph()
            pw.add_run('⚠️ تحذير:  ').bold = True
            pw.add_run('مؤشر NDVI منخفض جداً — يُنصح بفحص البيفوت وتغطية المحصول فوراً.')

        if tvdi > 0.7:
            pd = doc.add_paragraph()
            pd.add_run('🌡️ إجهاد حراري:  ').bold = True
            pd.add_run(f'TVDI = {tvdi:.2f} — التربة تعاني من جفاف. زِد كميات الري.')

        doc.add_paragraph()

        # ── Section 5: Weather ────────────────────────────────
        _header_para('🌤️ توقعات الطقس (5 أيام)', level=1, center=False)
        weather = self._get_weather_forecast()
        wt = doc.add_table(rows=len(weather)+1, cols=4)
        wt.style = 'Light Grid Accent 1'
        for txt, cell in zip(['التاريخ','درجة الحرارة','الرطوبة','الحالة'], wt.rows[0].cells):
            cell.text = txt
            cell.paragraphs[0].runs[0].bold = True
        for i, w in enumerate(weather, start=1):
            wt.rows[i].cells[0].text = w['date']
            wt.rows[i].cells[1].text = f"{w['temp_min']}°C — {w['temp_max']}°C"
            wt.rows[i].cells[2].text = f"{w['humidity']}%"
            wt.rows[i].cells[3].text = w['description']

        # ── Footer ────────────────────────────────────────────
        doc.add_page_break()
        footer = doc.add_paragraph()
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer.add_run('تم إنشاء هذا التقرير بواسطة\n').bold = True
        footer.add_run(f'{self.BRAND}\n')
        footer.add_run(f'التاريخ: {_fmt_date(self.now)}\n')
        footer.add_run('© 2026 Tayseer. جميع الحقوق محفوظة.')

        # ── Save ──────────────────────────────────────────────
        fname = _REPORTS_DIR / f"farm_summary_{self.user_id}_{self.now.strftime('%Y%m%d_%H%M%S')}.docx"
        doc.save(str(fname))
        log.info(f"[Report] Word saved → {fname}")
        return str(fname)

    # ─────────────────────────────────────────────────────────
    # 2. EXCEL REPORT
    # ─────────────────────────────────────────────────────────

    def generate_water_usage_excel(self, date_range_days: int = 30) -> str:
        """Generate water-usage analysis as .xlsx. Returns file path."""
        try:
            import openpyxl
            from openpyxl.styles import (Font, Alignment, PatternFill,
                                          Border, Side, GradientFill)
            from openpyxl.chart import LineChart, BarChart, Reference
            from openpyxl.chart.series import SeriesLabel
        except ImportError:
            raise RuntimeError("openpyxl غير مثبت. نفّذ: pip install openpyxl")

        wb = openpyxl.Workbook()
        wb.remove(wb.active)

        # ── Style helpers ─────────────────────────────────────
        GREEN_FILL  = PatternFill('solid', fgColor='10b981')
        HEADER_FONT = Font(bold=True, color='FFFFFF', name='Cairo', size=11)
        TITLE_FONT  = Font(bold=True, color='10b981', name='Cairo', size=16)
        BOLD        = Font(bold=True, name='Cairo')
        NORMAL      = Font(name='Cairo')
        CENTER      = Alignment(horizontal='center', vertical='center', wrap_text=True)
        RIGHT       = Alignment(horizontal='right',  vertical='center')
        thin        = Side(style='thin', color='D1D5DB')
        BORDER      = Border(left=thin, right=thin, top=thin, bottom=thin)

        def style_header(ws, row, cols):
            for c in range(1, cols+1):
                cell = ws.cell(row=row, column=c)
                cell.fill = GREEN_FILL
                cell.font = HEADER_FONT
                cell.alignment = CENTER
                cell.border = BORDER

        def style_data(ws, row_start, row_end, cols, alt=True):
            ALT_FILL = PatternFill('solid', fgColor='F0FDF4')
            for r in range(row_start, row_end+1):
                for c in range(1, cols+1):
                    cell = ws.cell(row=r, column=c)
                    if alt and r % 2 == 0:
                        cell.fill = ALT_FILL
                    cell.font   = NORMAL
                    cell.border = BORDER
                    cell.alignment = CENTER

        # ══════════════════════════════════════════════════════
        # Sheet 1 — ملخص استهلاك المياه
        # ══════════════════════════════════════════════════════
        ws1 = wb.create_sheet('ملخص استهلاك المياه')
        ws1.sheet_view.rightToLeft = True

        ws1['A1'] = 'تقرير استهلاك المياه — Tayseer Agri-SaT'
        ws1['A1'].font = TITLE_FONT
        ws1.merge_cells('A1:F1')
        ws1['A1'].alignment = CENTER

        ws1['A3'] = 'المزرعة:'
        ws1['A3'].font = BOLD
        ws1['B3'] = self.farm_name
        ws1['A4'] = 'المالك:'
        ws1['A4'].font = BOLD
        ws1['B4'] = self.user_name
        ws1['A5'] = 'الفترة:'
        ws1['A5'].font = BOLD
        ws1['B5'] = f'آخر {date_range_days} يوم'
        ws1['A6'] = 'تاريخ الإنشاء:'
        ws1['A6'].font = BOLD
        ws1['B6'] = self.now.strftime('%Y-%m-%d %H:%M')

        # Headers row 8
        HEADERS = ['التاريخ','NDVI','ET (مم/يوم)','ساعات الري','استهلاك المياه (م³)','WUE (كجم/م³)']
        for c, h in enumerate(HEADERS, 1):
            ws1.cell(row=8, column=c, value=h)
        style_header(ws1, 8, len(HEADERS))

        # Data rows
        ndvi_ser = self._ndvi_series(date_range_days)
        pump_ser = self._pump_series(date_range_days)
        nd_map   = dict(ndvi_ser)
        pump_map = dict(pump_ser)
        all_dates = sorted(set(list(nd_map.keys()) + list(pump_map.keys())))
        if not all_dates and self.fd.get('date'):
            all_dates = [self.fd['date']]

        DATA_START = 9
        for i, d in enumerate(all_dates):
            nd = nd_map.get(d, float(self.fd.get('ndvi') or 0.55))
            et_val = max(0.0, float(self.fd.get('et') or round(4.5 + nd * 2.5, 2)))
            ph_ = pump_map.get(d, float(self.fd.get('pump_hours') or 6.0))
            flow = 120   # m³/hr default
            water = ph_ * flow
            wue_v = round(float(self.fd.get('wue') or (nd * 15 + 5)), 2)
            r = DATA_START + i
            ws1.cell(r, 1, d)
            ws1.cell(r, 2, round(nd, 3))
            ws1.cell(r, 3, round(et_val, 2))
            ws1.cell(r, 4, round(ph_, 1))
            ws1.cell(r, 5, round(water, 0))
            ws1.cell(r, 6, round(wue_v, 2))

        data_end = DATA_START + max(0, len(all_dates) - 1)
        if all_dates:
            style_data(ws1, DATA_START, data_end, len(HEADERS))

        # Totals row
        tot_row = data_end + 1
        ws1.cell(tot_row, 1, 'الإجمالي').font = BOLD
        ws1.cell(tot_row, 4, f'=SUM(D{DATA_START}:D{data_end})').font = BOLD
        ws1.cell(tot_row, 5, f'=SUM(E{DATA_START}:E{data_end})').font = BOLD
        ws1.cell(tot_row, 6, f'=AVERAGE(F{DATA_START}:F{data_end})').font = BOLD

        # Line chart: water usage
        chart_line = LineChart()
        chart_line.title   = 'استهلاك المياه اليومي'
        chart_line.style   = 10
        chart_line.y_axis.title = 'م³'
        chart_line.x_axis.title = 'التاريخ'
        chart_line.height  = 12
        chart_line.width   = 20

        chart_data = Reference(ws1, min_col=5, min_row=8, max_row=data_end)
        cats       = Reference(ws1, min_col=1, min_row=DATA_START, max_row=data_end)
        chart_line.add_data(chart_data, titles_from_data=True)
        chart_line.set_categories(cats)
        chart_line.series[0].graphicalProperties.line.solidFill = '10b981'
        chart_line.series[0].graphicalProperties.line.width = 20000

        ws1.add_chart(chart_line, 'H8')

        # Column widths
        for col, w in zip('ABCDEF', [14, 9, 12, 12, 20, 14]):
            ws1.column_dimensions[col].width = w

        # ══════════════════════════════════════════════════════
        # Sheet 2 — تفاصيل NDVI
        # ══════════════════════════════════════════════════════
        ws2 = wb.create_sheet('تفاصيل NDVI')
        ws2.sheet_view.rightToLeft = True
        ws2['A1'] = 'تطور مؤشر صحة المحصول NDVI'
        ws2['A1'].font = TITLE_FONT
        ws2.merge_cells('A1:E1')
        ws2['A1'].alignment = CENTER

        ndvi_headers = ['التاريخ','NDVI','التصنيف','التغيير','حالة الري']
        for c, h in enumerate(ndvi_headers, 1):
            ws2.cell(2, c, h)
        style_header(ws2, 2, len(ndvi_headers))

        prev_nd = None
        for i, (d, nd) in enumerate(ndvi_ser):
            r = 3 + i
            ws2.cell(r, 1, d)
            ws2.cell(r, 2, round(nd, 3))
            cat = 'ممتاز' if nd >= 0.6 else ('جيد' if nd >= 0.4 else 'ضعيف')
            ws2.cell(r, 3, cat)
            chg = round(nd - prev_nd, 3) if prev_nd is not None else 0
            ws2.cell(r, 4, f'{chg:+.3f}' if prev_nd else '—')
            ws2.cell(r, 5, '✅ كافٍ' if nd >= 0.45 else '💧 يحتاج ري')
            prev_nd = nd

        style_data(ws2, 3, 3 + len(ndvi_ser) - 1, len(ndvi_headers))

        # NDVI bar chart
        bc = BarChart()
        bc.type  = 'col'
        bc.title = 'NDVI عبر الزمن'
        bc.style = 10
        bc.y_axis.scaling.min = 0
        bc.y_axis.scaling.max = 1.0
        bc.height = 12
        bc.width  = 20
        bc_d = Reference(ws2, min_col=2, min_row=2, max_row=2+len(ndvi_ser))
        bc_c = Reference(ws2, min_col=1, min_row=3, max_row=2+len(ndvi_ser))
        bc.add_data(bc_d, titles_from_data=True)
        bc.set_categories(bc_c)
        ws2.add_chart(bc, 'G3')

        for col, w in zip('ABCDE', [14, 9, 10, 10, 14]):
            ws2.column_dimensions[col].width = w

        # ══════════════════════════════════════════════════════
        # Sheet 3 — تحليل WUE و WWFI
        # ══════════════════════════════════════════════════════
        ws3 = wb.create_sheet('تحليل كفاءة المياه')
        ws3.sheet_view.rightToLeft = True
        ws3['A1'] = 'تحليل كفاءة استخدام المياه (WUE & WWFI)'
        ws3['A1'].font = TITLE_FONT
        ws3.merge_cells('A1:F1')
        ws3['A1'].alignment = CENTER

        ws3['A3'] = 'WUE'
        ws3['B3'] = 'معامل كفاءة الري (كجم محصول / م³ مياه). كلما ارتفع كان أفضل.'
        ws3['A3'].font = BOLD

        ws3['A4'] = 'WWFI'
        ws3['B4'] = 'Water Waste Fingerprint Index — نسبة المياه المهدرة من الإجمالي.'
        ws3['A4'].font = BOLD

        ws3['A6'] = 'القيمة الحالية WUE'
        ws3['B6'] = round(float(self.fd.get('wue') or 12), 2)
        ws3['A6'].font = BOLD
        ws3['A7'] = 'النطاق المثالي'
        ws3['B7'] = '12 — 22 كجم/م³'

        wue_rating_txt = (
            '🟢 ممتاز — كفاءة عالية'
            if (self.fd.get('wue') or 0) >= 18
            else ('🟡 مقبول — يمكن التحسين'
                  if (self.fd.get('wue') or 0) >= 12 else
                  '🔴 منخفض — مراجعة منظومة الري')
        )
        ws3['A8'] = 'التقييم'
        ws3['B8'] = wue_rating_txt
        ws3['A8'].font = BOLD

        for col, w in zip('AB', [20, 40]):
            ws3.column_dimensions[col].width = w

        # ── Save ──────────────────────────────────────────────
        fname = _REPORTS_DIR / f"water_usage_{self.user_id}_{self.now.strftime('%Y%m%d_%H%M%S')}.xlsx"
        wb.save(str(fname))
        log.info(f"[Report] Excel saved → {fname}")
        return str(fname)

    # ─────────────────────────────────────────────────────────
    # 3. PDF REPORT
    # ─────────────────────────────────────────────────────────

    def generate_farm_summary_pdf(self, date_range_days: int = 30) -> str:
        """Generate farm summary as PDF. Returns file path."""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib import colors
            from reportlab.lib.units import inch, cm
            from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                             Paragraph, Spacer, Image,
                                             PageBreak, HRFlowable)
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
        except ImportError:
            raise RuntimeError("reportlab غير مثبت. نفّذ: pip install reportlab")

        # ── Font registration (optional Cairo) ────────────────
        USE_CAIRO = False
        _cairo_paths = [
            '/usr/share/fonts/truetype/cairo/Cairo-Regular.ttf',
            'C:/Windows/Fonts/Cairo-Regular.ttf',
            str(Path(__file__).parent / 'static' / 'fonts' / 'Cairo-Regular.ttf'),
        ]
        for _cp in _cairo_paths:
            if os.path.exists(_cp):
                try:
                    pdfmetrics.registerFont(TTFont('Cairo', _cp))
                    USE_CAIRO = True
                    break
                except Exception:
                    pass

        BASE_FONT = 'Cairo' if USE_CAIRO else 'Helvetica'
        BOLD_FONT = 'Cairo' if USE_CAIRO else 'Helvetica-Bold'

        fname = _REPORTS_DIR / f"farm_summary_{self.user_id}_{self.now.strftime('%Y%m%d_%H%M%S')}.pdf"

        doc = SimpleDocTemplate(
            str(fname), pagesize=A4,
            topMargin=1.5*cm, bottomMargin=1.5*cm,
            leftMargin=2*cm,  rightMargin=2*cm,
        )

        GREEN   = colors.HexColor('#10b981')
        DARKBG  = colors.HexColor('#1a2d3f')
        LIGHTBG = colors.HexColor('#f0fdf4')
        GREY    = colors.HexColor('#6b7280')

        styles = getSampleStyleSheet()

        def _sty(name, **kw):
            kw.setdefault('fontName', BASE_FONT)
            return ParagraphStyle(name, **kw)

        title_sty = _sty('Title', fontSize=20, textColor=GREEN,
                          alignment=1, spaceAfter=8, leading=28)
        head_sty  = _sty('Head',  fontSize=14, textColor=DARKBG,
                          fontName=BOLD_FONT, spaceAfter=6, leading=20)
        body_sty  = _sty('Body',  fontSize=10, textColor=colors.black,
                          spaceAfter=4, leading=15, alignment=2)  # right‑align
        sub_sty   = _sty('Sub',   fontSize=9,  textColor=GREY,
                          spaceAfter=2, leading=13, alignment=2)

        story = []

        # ── Cover ─────────────────────────────────────────────
        story.append(Spacer(1, 0.5*inch))
        story.append(Paragraph('🛰️ Tayseer Agri-SaT', title_sty))
        story.append(Paragraph('تقرير شامل للمزرعة', title_sty))
        story.append(HRFlowable(width='100%', color=GREEN, thickness=2, spaceAfter=12))

        cover_data = [
            ['اسم المزرعة', self.farm_name],
            ['المالك',       self.user_name],
            ['التاريخ',      _fmt_date(self.now)],
            ['الفترة',       f'آخر {date_range_days} يوم'],
        ]
        cover_tbl = Table(cover_data, colWidths=[2.5*inch, 4*inch])
        cover_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0),(0,-1), LIGHTBG),
            ('FONTNAME',   (0,0),(0,-1), BOLD_FONT),
            ('FONTNAME',   (1,0),(1,-1), BASE_FONT),
            ('FONTSIZE',   (0,0),(-1,-1), 10),
            ('ALIGN',      (0,0),(-1,-1), 'RIGHT'),
            ('GRID',       (0,0),(-1,-1), 0.5, colors.HexColor('#d1d5db')),
            ('VALIGN',     (0,0),(-1,-1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0,0),(-1,-1), [colors.white, LIGHTBG]),
        ]))
        story.append(cover_tbl)
        story.append(Spacer(1, 0.3*inch))

        # ── Metrics ───────────────────────────────────────────
        story.append(Paragraph('المؤشرات الرئيسية', head_sty))

        ndvi = float(self.fd.get('ndvi') or 0)
        et   = float(self.fd.get('et')   or 0)
        ph   = float(self.fd.get('pump_hours') or 0)
        bm   = float(self.fd.get('biomass') or 0)
        wue  = float(self.fd.get('wue') or 0)
        tvdi = float(self.fd.get('tvdi') or 0)
        st   = self.fd.get('status','healthy')

        m_data = [
            ['المؤشر',                'القيمة',              'التقييم'],
            ['NDVI — صحة المحصول',    f'{ndvi:.3f}',         'ممتاز' if ndvi>=0.6 else ('مقبول' if ndvi>=0.4 else 'ضعيف')],
            ['ET — التبخر',           f'{et:.2f} مم/يوم',    'طبيعي' if 2<=et<=7 else 'خارج النطاق'],
            ['ساعات الري',             f'{ph:.1f} ساعة',      'مناسب' if 4<=ph<=10 else 'راجع الجدول'],
            ['الكتلة الحيوية',        f'{bm:.1f} طن/هكتار',  'ممتاز' if bm>=10 else 'متوسط'],
            ['WUE — كفاءة المياه',    f'{wue:.2f} كجم/م³',   'ممتاز' if wue>=18 else ('مقبول' if wue>=12 else 'ضعيف')],
            ['TVDI — جفاف التربة',    f'{tvdi:.2f}',         'رطب' if tvdi<=0.4 else ('متوسط' if tvdi<=0.7 else 'جاف')],
        ]
        col_w = [2.5*inch, 1.8*inch, 1.5*inch]
        m_tbl = Table(m_data, colWidths=col_w)
        m_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0),(-1,0), GREEN),
            ('TEXTCOLOR',  (0,0),(-1,0), colors.white),
            ('FONTNAME',   (0,0),(-1,0), BOLD_FONT),
            ('FONTNAME',   (0,1),(-1,-1), BASE_FONT),
            ('FONTSIZE',   (0,0),(-1,-1), 9),
            ('ALIGN',      (0,0),(-1,-1), 'CENTER'),
            ('GRID',       (0,0),(-1,-1), 0.5, colors.HexColor('#d1d5db')),
            ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, LIGHTBG]),
        ]))
        story.append(m_tbl)
        story.append(Spacer(1, 0.25*inch))

        # ── NDVI Chart ────────────────────────────────────────
        story.append(Paragraph('تطور مؤشر NDVI', head_sty))
        ndvi_ser  = self._ndvi_series(date_range_days)
        chart_png = _generate_ndvi_chart(ndvi_ser, self.farm_name)
        _tmp_files = []  # collect temp PNGs; delete AFTER build
        if chart_png and os.path.exists(chart_png):
            story.append(Image(chart_png, width=5.5*inch, height=2.5*inch))
            _tmp_files.append(chart_png)
        else:
            story.append(Paragraph('(مخطط NDVI غير متاح)', sub_sty))

        story.append(Spacer(1, 0.25*inch))

        # ── Recommendations ───────────────────────────────────
        story.append(PageBreak())
        story.append(Paragraph('التوصيات والإرشادات', head_sty))
        story.append(HRFlowable(width='100%', color=GREEN, thickness=1.5, spaceAfter=8))

        irr = (
            '✅ المحصول بحالة جيدة — استمر في جدول الري الحالي.'
            if ndvi >= 0.55 else
            f'💧 يُوصى بزيادة وقت الري إلى {ph*1.2:.1f} ساعة يومياً.'
        )
        rec_data = [
            ['توصية الري', irr],
            ['الحالة العامة', f'{_status_emoji(st)} {_status_ar(st)}'],
        ]
        if tvdi > 0.7:
            rec_data.append(['إجهاد حراري', f'TVDI={tvdi:.2f} — زِد كميات الري فوراً'])
        if ndvi < 0.3:
            rec_data.append(['تحذير عاجل', 'NDVI منخفض — فحص البيفوت وتغطية المحصول'])

        rec_tbl = Table(rec_data, colWidths=[2*inch, 4*inch])
        rec_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0),(0,-1), LIGHTBG),
            ('FONTNAME',   (0,0),(0,-1), BOLD_FONT),
            ('FONTNAME',   (1,0),(1,-1), BASE_FONT),
            ('FONTSIZE',   (0,0),(-1,-1), 9),
            ('ALIGN',      (0,0),(-1,-1), 'RIGHT'),
            ('GRID',       (0,0),(-1,-1), 0.5, colors.HexColor('#d1d5db')),
            ('VALIGN',     (0,0),(-1,-1), 'MIDDLE'),
        ]))
        story.append(rec_tbl)
        story.append(Spacer(1, 0.25*inch))

        # ── Weather ───────────────────────────────────────────
        story.append(Paragraph('توقعات الطقس — 5 أيام', head_sty))
        weather = self._get_weather_forecast()
        w_data  = [['التاريخ','درجة الحرارة','الرطوبة','الوصف']] + [
            [w['date'], f"{w['temp_min']}°–{w['temp_max']}°C",
             f"{w['humidity']}%", w['description']]
            for w in weather
        ]
        w_tbl = Table(w_data, colWidths=[1.5*inch, 1.8*inch, 1.2*inch, 2.2*inch])
        w_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0),(-1,0), GREEN),
            ('TEXTCOLOR',  (0,0),(-1,0), colors.white),
            ('FONTNAME',   (0,0),(-1,0), BOLD_FONT),
            ('FONTNAME',   (0,1),(-1,-1), BASE_FONT),
            ('FONTSIZE',   (0,0),(-1,-1), 9),
            ('ALIGN',      (0,0),(-1,-1), 'CENTER'),
            ('GRID',       (0,0),(-1,-1), 0.5, colors.HexColor('#d1d5db')),
            ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, LIGHTBG]),
        ]))
        story.append(w_tbl)
        story.append(Spacer(1, 0.5*inch))

        # ── Footer ────────────────────────────────────────────
        story.append(HRFlowable(width='100%', color=GREY, thickness=0.5, spaceAfter=8))
        story.append(Paragraph(
            f'تم إنشاؤه بواسطة {self.BRAND} — {_fmt_date(self.now)} — © 2026 Tayseer',
            _sty('Footer', fontSize=8, textColor=GREY, alignment=1)
        ))

        doc.build(story)

        # Clean up temp chart PNGs now that the PDF is fully written
        for _tmp in _tmp_files:
            try:
                os.remove(_tmp)
            except Exception:
                pass

        log.info(f"[Report] PDF saved → {fname}")
        return str(fname)

    # ─────────────────────────────────────────────────────────
    # 4. Pivot Health Report (Word)
    # ─────────────────────────────────────────────────────────

    def generate_pivot_health_word(self, date_range_days: int = 30) -> str:
        """Generate pivot-health report as .docx. Returns file path."""
        try:
            from docx import Document
            from docx.shared import Inches, Pt, RGBColor, Cm
            from docx.enum.text import WD_ALIGN_PARAGRAPH
        except ImportError:
            raise RuntimeError("python-docx غير مثبت.")

        doc = Document()
        sec = doc.sections[0]
        sec.page_height = Cm(29.7)
        sec.page_width  = Cm(21.0)
        sec.top_margin = sec.bottom_margin = Cm(2)
        sec.left_margin = sec.right_margin = Cm(2.5)

        def _h(text, level=1):
            h = doc.add_heading('', level=level)
            r = h.add_run(text)
            r.font.size  = Pt(16 - level * 2)
            r.font.bold  = True
            r.font.color.rgb = RGBColor(0x10, 0xb9, 0x81)
            return h

        _h('🔄 تقرير صحة البيفوتات', level=0)
        doc.add_paragraph(f'المزرعة: {self.farm_name} | التاريخ: {_fmt_date(self.now)}')

        ndvi = float(self.fd.get('ndvi') or 0)
        ph   = float(self.fd.get('pump_hours') or 0)
        st   = self.fd.get('status','healthy')
        cv   = float(self.fd.get('ndvi_cv') or 0)
        ti   = float(self.fd.get('thermal_inertia') or 0)

        _h('الحالة الحالية', level=1)
        p = doc.add_paragraph()
        p.add_run(f'الحالة: ').bold = True
        p.add_run(f'{_status_emoji(st)} {_status_ar(st)}')

        _h('مؤشرات البيفوت', level=2)
        tbl = doc.add_table(rows=5, cols=2)
        tbl.style = 'Light Grid Accent 1'
        rows_data = [
            ('NDVI',               f'{ndvi:.3f}'),
            ('معامل التغير (CV)',  f'{cv:.3f}'),
            ('الجمود الحراري',     f'{ti:.3f}'),
            ('ساعات الري',        f'{ph:.1f} ساعة/يوم'),
            ('تحذير أعطال',       'لا' if st != 'pivot_alert' else '⚠️ نعم'),
        ]
        for i, (k, v) in enumerate(rows_data):
            tbl.rows[i].cells[0].text = k
            tbl.rows[i].cells[0].paragraphs[0].runs[0].bold = True
            tbl.rows[i].cells[1].text = v

        doc.add_paragraph()
        _h('التوصيات الصيانية', level=1)
        maint = [
            'فحص الرشاشات والبكيات كل 4 أسابيع',
            'تنظيف الفلاتر وفتحات الري شهرياً',
            'مراجعة أحزمة وعجلات البيفوت كل 3 أشهر',
        ]
        if cv > 0.25:
            maint.insert(0, 'أولوية: تفاوت CV عالٍ — فحص الرشاشات المسدودة فوراً')
        if ti < 0.3:
            maint.insert(0, 'أولوية: جمود حراري منخفض — احتمال عطل ميكانيكي')

        for m in maint:
            p = doc.add_paragraph(style='List Bullet')
            p.add_run(m)

        fname = _REPORTS_DIR / f"pivot_health_{self.user_id}_{self.now.strftime('%Y%m%d_%H%M%S')}.docx"
        doc.save(str(fname))
        log.info(f"[Report] Pivot health report saved → {fname}")
        return str(fname)

    # ─────────────────────────────────────────────────────────
    # Helper: Weather Forecast (real or placeholder)
    # ─────────────────────────────────────────────────────────

    def _get_weather_forecast(self) -> list:
        """
        Attempt to fetch real weather from OpenWeatherMap.
        Falls back to plausible generated data.
        """
        import os, math
        api_key = os.getenv('OPENWEATHER_API_KEY') or ''
        lat = self.fd.get('lat') or 26.0
        lon = self.fd.get('lon') or 30.0

        if api_key:
            try:
                import requests
                url = (f'https://api.openweathermap.org/data/2.5/forecast'
                       f'?lat={lat}&lon={lon}&appid={api_key}&units=metric&lang=ar&cnt=5')
                res = requests.get(url, timeout=6)
                data = res.json()
                if res.status_code == 200 and data.get('list'):
                    out = []
                    for item in data['list'][:5]:
                        out.append({
                            'date':        item['dt_txt'][:10],
                            'temp_min':    round(item['main']['temp_min']),
                            'temp_max':    round(item['main']['temp_max']),
                            'humidity':    item['main']['humidity'],
                            'description': item['weather'][0]['description'],
                        })
                    return out
            except Exception as e:
                log.debug(f"[Report] Weather API failed: {e}")

        # Placeholder
        return [
            {
                'date':        (self.now + timedelta(days=i)).strftime('%Y-%m-%d'),
                'temp_min':    16 + i,
                'temp_max':    30 + i,
                'humidity':    35 + i * 3,
                'description': 'صافٍ جزئياً',
            }
            for i in range(5)
        ]
