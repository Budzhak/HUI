# -*- coding: utf-8 -*-
"""Посмотреть — выгрузка спецификации в Excel для правки и обратной загрузки
кнопкой «Забрать».

В книге (db 20.10.2026, версия 3):
  «<имя ведомости>» — копия таблицы Revit один в один (текст, объединения, ширина,
                    высота, шрифты). Только смотреть.
  «Как на виде»   — таблица как в ведомости + строка номеров граф (автофильтр).
                    ПРАВИТЬ ЗДЕСЬ. Под таблицей через строку — счётчик правок и
                    пояснения (цвета, причины «элементов без строки»). В шапке —
                    примечание с настоящим именем параметра, если графа названа иначе.
  «ElementId»     — эталон: по строке на элемент (по возрастанию ElementId),
                    значения как в таблице (формат поля ведомости), без формул.
  «Лист1»         — скрытый, служебный для «Забрать»: формулы берут правки с
                    «Как на виде», а неизменённые ячейки — точное значение из модели.
  «_ORIG», «_ORIG1», «_DBTOOLS_META» — скрытые служебные.

Значения сравниваются со строками ведомости в формате поля ведомости (единицы,
округление), поэтому «14.5» в таблице и 14,5 °C в модели — одно и то же.
Сопоставление — по группам элементов (поля сортировки/группировки), без перебора
«элемент × строка»: десятки тысяч элементов выгружаются за секунды.

Кнопка «Забрать» не менялась и читает книгу как прежде.
"""
import re
import time

from pyrevit import revit
import xyz_forms as forms   # единый стиль окон XYZ (lib/xyz_forms.py)
from Autodesk.Revit.DB import *

import System
from System import Type, Activator
from System.Reflection import BindingFlags

doc = revit.doc
T0 = time.time()
TIMES = []


def fmt_t(sec):
    sec = int(round(sec))
    return u"%d мин %02d с" % (sec // 60, sec % 60) if sec >= 60 else u"%d с" % sec


def tick(name):
    TIMES.append(u"%s — %s" % (name, fmt_t(time.time() - T0)))


FT_MM = 304.8
VIEW_SHEET = u"Как на виде"
ID_SHEET = u"ElementId"


def eid_int(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


# ============================================================
# БЛОК 1. Выбор спецификации
# ============================================================
class ScheduleItem(object):
    def __init__(self, schedule, display_name=None):
        self.schedule = schedule
        self.Name = display_name if display_name else schedule.Name


schedules = [s for s in FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements()
             if not s.IsTemplate and not s.IsTitleblockRevisionSchedule]
schedules = sorted(schedules, key=lambda x: x.Name)
items = [ScheduleItem(s) for s in schedules]

active_view = doc.ActiveView
if isinstance(active_view, ViewSchedule) and not active_view.IsTemplate \
        and not active_view.IsTitleblockRevisionSchedule:
    items.insert(0, ScheduleItem(active_view, u"▶ ТЕКУЩАЯ: {}".format(active_view.Name)))

selected_item = forms.SelectFromList.show(items, name_attr="Name", title="Выберите спецификацию")
if not selected_item:
    forms.alert("Отмена")
    raise SystemExit

selected = selected_item.schedule
T0 = time.time()                                 # время считается после выбора ведомости
definition = selected.Definition

elements = list(FilteredElementCollector(doc, selected.Id).WhereElementIsNotElementType().ToElements())
if not elements:
    forms.alert("В спецификации нет элементов.")
    raise SystemExit
elements.sort(key=lambda e: eid_int(e.Id))

fields = [definition.GetField(i) for i in range(definition.GetFieldCount())]
vis = [f for f in fields if not f.IsHidden]
NC = len(vis)


# ============================================================
# БЛОК 2. Параметр поля у элемента (экземпляр или тип)
# ============================================================
FDEF = {}                                        # ParameterId -> то, чем искать параметр


def _fdef(param_id):
    k = eid_int(param_id)
    if k not in FDEF:
        d = None
        if k < 0:
            try:
                d = BuiltInParameter(k)
            except Exception:
                d = None
        else:
            pe = doc.GetElement(param_id)
            d = pe.GetDefinition() if pe is not None else None
        FDEF[k] = d
    return FDEF[k]


TYPE_P = {}                                      # (тип, ParameterId) -> Parameter (кэш: типов мало)


def get_param_and_type(elem, param_id):
    """(Parameter или None, is_type_param); приоритет — где есть значение."""
    if param_id == ElementId.InvalidElementId:
        return None, False
    d = _fdef(param_id)
    if d is None:
        return None, False
    try:
        instance_param = elem.get_Parameter(d)
    except Exception:
        instance_param = None
    if instance_param is not None and instance_param.HasValue:
        return instance_param, False
    type_param = None
    type_id = elem.GetTypeId()
    if type_id != ElementId.InvalidElementId:
        tk = (eid_int(type_id), eid_int(param_id))
        if tk not in TYPE_P:
            te = doc.GetElement(type_id)
            try:
                TYPE_P[tk] = te.get_Parameter(d) if te is not None else None
            except Exception:
                TYPE_P[tk] = None
        type_param = TYPE_P[tk]
    if type_param is not None and type_param.HasValue:
        return type_param, True
    if instance_param is not None:
        return instance_param, False
    if type_param is not None:
        return type_param, True
    return None, False


def disp(param):
    v = param.AsValueString()
    if v is None:
        v = param.AsString()
    return v if v is not None else u""


def disp_field(param, field):
    """Значение так, как его показывает спецификация: формат (единицы, округление)
    поля спецификации, если он задан, иначе — формат проекта."""
    try:
        if param.StorageType == StorageType.Double:
            fo = field.GetFormatOptions()
            if fo is not None and not fo.UseDefault:
                opts = FormatValueOptions()
                opts.SetFormatOptions(fo)
                return UnitFormatUtils.Format(doc.GetUnits(), param.Definition.GetDataType(), param.AsDouble(), False, opts)
    except Exception:
        pass
    return disp(param)


# значения и статус ячеек по элементам: st = 'inst' | 'type' | 'ro' (экземпляр/вычисляемое,
# только чтение) | 'ro_type' (тип, только чтение — формула в семействе) | None (нет параметра)
VAL, ST = {}, {}
VT = {}                                          # значения «как в таблице» (формат поля спецификации)
# строки несгруппированной спецификации = сочетания значений полей сортировки/группировки
GROUP_FIELDS = []
for sg in definition.GetSortGroupFields():
    try:
        GROUP_FIELDS.append(definition.GetField(sg.FieldId).ParameterId)
    except Exception:
        pass
GKEY = {}
URLCOL = set()                                   # колонки-гиперссылки (тип данных «URL»)
for el in elements:
    k = eid_int(el.Id)
    vals, sts, vts = [], [], []
    for f in vis:
        p, is_type = get_param_and_type(el, f.ParameterId)
        if p is None:
            vals.append(None)
            vts.append(None)
            sts.append(None)
        else:
            vals.append(disp(p))
            vts.append(disp_field(p, f))
            try:
                if p.Definition.GetDataType() == SpecTypeId.String.Url:
                    URLCOL.add(len(vals) - 1)
            except Exception:
                pass
            if p.IsReadOnly:
                sts.append('ro_type' if is_type else 'ro')
            else:
                sts.append('type' if is_type else 'inst')
    VAL[k], ST[k], VT[k] = vals, sts, vts
    if GROUP_FIELDS:
        key = []
        for pid in GROUP_FIELDS:
            p, _ = get_param_and_type(el, pid)
            key.append(disp(p) if p is not None else u"")
        GKEY[k] = tuple(key)
tick(u"значения")


# ============================================================
# БЛОК 3. Таблица спецификации, как её показывает Revit
# ============================================================
td = selected.GetTableData()
sec_head = td.GetSectionData(SectionType.Header)
sec_body = td.GetSectionData(SectionType.Body)


def ctext(sec, r, c):
    """Текст ячейки, как его показывает Revit (у TableSectionData.GetCellText
    ячейки данных пустые — нужен ViewSchedule.GetCellText)."""
    st = SectionType.Header if sec is sec_head else SectionType.Body
    try:
        return selected.GetCellText(st, r, c) or u""
    except Exception:
        try:
            return sec.GetCellText(r, c) or u""
        except Exception:
            return u""


def mcell(sec, r, c):
    try:
        m = sec.GetMergedCell(r, c)
        return m.Top, m.Left, m.Bottom, m.Right
    except Exception:
        return r, c, r, c


b_r0, b_r1 = sec_body.FirstRowNumber, sec_body.LastRowNumber
b_c0, b_c1 = sec_body.FirstColumnNumber, sec_body.LastColumnNumber
table_ok = (b_c1 - b_c0 + 1) == NC and NC > 0

# сколько строк занимает шапка колонок: нижняя строка шапки = заголовки полей
H = 0
if table_ok and definition.ShowHeaders:
    for h in range(1, 7):
        r = b_r0 + h - 1
        if r > b_r1:
            break
        ok = True
        for j, f in enumerate(vis):
            t, l, _, _ = mcell(sec_body, r, b_c0 + j)
            if ctext(sec_body, t, l).strip() != (f.ColumnHeading or u"").strip():
                ok = False
                break
        if ok:
            H = h
            break
    if H == 0:
        table_ok = False

data_rows = list(range(b_r0 + H, b_r1 + 1)) if table_ok else []
ROWTXT = {}
SERVICE = set()
for r in data_rows:
    ROWTXT[r] = [ctext(sec_body, r, b_c0 + j) for j in range(NC)]
    for j in range(NC):
        t, l, b, rr = mcell(sec_body, r, b_c0 + j)
        if rr > l:                       # объединённая по ширине — заголовок/итог группы
            SERVICE.add(r)
            break
    if all(x.strip() == u"" for x in ROWTXT[r]):
        SERVICE.add(r)


# ============================================================
# БЛОК 4. Какие элементы стоят в какой строке
# (сравнение текстов строки с значениями элементов; числа — с допуском
# на округление, заданное в спецификации)
# ============================================================
NUM = re.compile(u"[-+−]?(?:\\d{1,3}(?:[   ]\\d{3})+|\\d+)(?:[.,]\\d+)?")


def numbers(s):
    out = []
    for m in NUM.finditer(s):
        t = re.sub(u"[   ]", u"", m.group(0)).replace(u"−", u"-").replace(u",", u".")
        try:
            out.append((float(t), len(t.split(u".")[1]) if u"." in t else 0))
        except Exception:
            pass
    return out, re.sub(u"\\s+", u"", NUM.sub(u"#", s))


def same(rt, et):
    rt, et = rt.strip(), et.strip()
    if rt == et:
        return True
    # пустой числовой параметр спецификация показывает как «0»
    if (rt == u"" and numbers(et)[0] == [(0.0, 0)]) or (et == u"" and [v for v, _ in numbers(rt)[0]] == [0.0]):
        return True
    na, ra = numbers(rt)
    nb, rb = numbers(et)
    if not na or len(na) != len(nb):
        return False
    if ra != rb and ra.replace(u"#", u"") and rb.replace(u"#", u""):
        # текст вокруг чисел разный (кроме случая, когда в одном из них нет единиц)
        if ra.split(u"#")[0] != rb.split(u"#")[0]:
            return False
    for (a, da), (b, _) in zip(na, nb):
        if abs(a - b) > 0.5 * 10 ** (-da) + 1e-9:
            return False
    return True


GROUPED = not definition.IsItemized
# графы с итогами (сумма/мин-макс): в сгруппированной строке там сумма по группе, а не
# значение одного элемента — при сопоставлении их не сравниваем
TOTALCOLS = set()
for j, f in enumerate(vis):
    try:
        if str(f.DisplayType) != u"Standard":
            TOTALCOLS.add(j)
    except Exception:
        try:
            if f.HasTotals:
                TOTALCOLS.add(j)
        except Exception:
            pass


def row_score(r, k):
    """None — не подходит; иначе оценка (совпавшие непустые ячейки минус штраф
    за расхождения; допускается не больше 10 % расхождений — форматы чисел и
    единиц в спецификации бывают свои)."""
    rt, ev = ROWTXT[r], VT[k]
    exact = mm = cmp = 0
    for j in range(NC):
        e = ev[j]
        if e is None or (GROUPED and j in TOTALCOLS):
            continue
        t = rt[j]
        if GROUPED and (t.strip() == u"" or t.strip().lower() in (u"<варианты>", u"<varies>", u"<различные>")):
            continue                     # «разные значения» в групповой строке
        cmp += 1
        if not same(t, e):
            mm += 1
            continue
        if t.strip() != u"":
            exact += 1
    if mm > max(1, int(0.1 * cmp)):
        return None
    return exact - 3 * mm


# Быстрое сопоставление (db 20.10: на 15 тыс. элементов старый перебор «каждый элемент × каждая
# строка» вешал Revit на час). Несгруппированная спецификация: элементы сначала собираются в группы
# по значениям полей сортировки/группировки (так их сводит Revit), с строками сравнивается одна
# группа целиком. Кандидаты — только строки с тем же текстом в якорной колонке.
VARIES = (u"<варианты>", u"<varies>", u"<различные>")


def _wild(t):
    t = t.strip()
    return t == u"" or t.lower() in VARIES


anchor = None
best = -1
sample = list(VAL.keys())[:3000]
for j in range(NC):
    # якорь — графа с наибольшим числом РАЗНЫХ нечисловых текстов (обычно «Наименование»):
    # по ней кандидатов на строку остаётся единицы, а не вся таблица
    cnt = len(set(VT[k][j] for k in sample if VT[k][j] and not numbers(VT[k][j])[0]))
    if j in TOTALCOLS:
        cnt = -1
    if cnt > best:
        best, anchor = cnt, j

ROW_ELS = dict((r, []) for r in data_rows)
assigned = set()
cand_rows = [r for r in data_rows if r not in SERVICE]
if table_ok:
    rows_by_anchor = {}
    for r in cand_rows:
        rows_by_anchor.setdefault(ROWTXT[r][anchor].strip(), []).append(r)
    wild_rows = [r for r in cand_rows if _wild(ROWTXT[r][anchor])]
    if GROUPED:
        groups = {}
        for el in elements:
            k = eid_int(el.Id)
            groups.setdefault(GKEY.get(k, ()), []).append(k)
        units = [groups[g] for g in sorted(groups.keys())]
    else:
        units = [[eid_int(e.Id)] for e in elements]
    # все пары «группа — строка» с оценкой, потом жадно от лучшей пары к худшей:
    # группа не может «увести» чужую строку, если у той есть пара лучше
    full_scan = len(units) * len(cand_rows) < 300000
    pairs = []
    for gi, members in enumerate(units):
        k0 = members[0]
        pool = rows_by_anchor.get((VT[k0][anchor] or u"").strip(), []) + wild_rows
        if not pool and full_scan:
            pool = cand_rows
        for r in set(pool):
            sc = row_score(r, k0)
            if sc is not None and (sc >= 1 or not GROUPED):
                pairs.append((sc, -r, gi, r))
    pairs.sort(reverse=True)
    used_r, used_g = set(), set()
    for sc, _, gi, r in pairs:
        if gi in used_g or r in used_r:
            continue
        used_g.add(gi)
        used_r.add(r)
        ROW_ELS[r].extend(units[gi])
        assigned.update(units[gi])
tick(u"сопоставление")

unmatched = [eid_int(e.Id) for e in elements if eid_int(e.Id) not in assigned]

# почему не нашлось: ближайшая строка и колонки, где текст не совпал (выводится в «Легенде»)
WHY = []
if table_ok and unmatched:
    for k in unmatched[:15]:
        pool = rows_by_anchor.get((VT[k][anchor] or u"").strip(), []) or cand_rows[:400]
        best = None
        for r in pool:
            diff = []
            for j in range(NC):
                e, t = VT[k][j], ROWTXT[r][j]
                if e is None or (GROUPED and (_wild(t) or j in TOTALCOLS)):
                    continue
                if not same(t, e):
                    diff.append(j)
            if best is None or len(diff) < len(best[1]):
                best = (r, diff)
        if best is not None:
            r, diff = best
            parts = [u"графа %d «%s»: в строке таблицы «%s», у элемента «%s»" % (j + 1, vis[j].ColumnHeading or vis[j].GetName(), ROWTXT[r][j], VT[k][j] or u"") for j in diff[:3]]
            WHY.append(u"ElementId %d (%s) — %s" % (k, VT[k][anchor] or u"", u"; ".join(parts) or u"подходящую строку уже заняла другая группа элементов"))


# ============================================================
# БЛОК 5. COM-хелперы Excel
# ============================================================
def com_get(obj, name, *args):
    return obj.GetType().InvokeMember(name, BindingFlags.GetProperty, None, obj, System.Array[object](args))


def com_set(obj, name, value):
    obj.GetType().InvokeMember(name, BindingFlags.SetProperty, None, obj, System.Array[object]([value]))


def com_call(obj, name, *args):
    return obj.GetType().InvokeMember(name, BindingFlags.InvokeMethod, None, obj, System.Array[object](args))


def rgb(r, g, b):
    return r + g * 256 + b * 65536


GRAY = rgb(217, 217, 217)
YELLOW = rgb(255, 244, 204)
PEACH = rgb(248, 203, 173)      # тип, задан формулой — менять нельзя
FILT = rgb(200, 230, 201)       # строка фильтров
RED = rgb(192, 0, 0)            # изменённая ячейка: жирный красный шрифт + красная рамка
M = System.Reflection.Missing.Value


def protect(ws):
    """Защита листа: менять можно только незаблокированные ячейки, но работают
    автофильтр и изменение ширины колонок / высоты строк (сортировка — нет: строки
    связаны формулами)."""
    try:
        com_call(ws, "Protect", M, M, M, M, M, M, True, True, M, M, M, M, M, False, True)
    except Exception:
        com_call(ws, "Protect")


def add_links(ws, cells):
    """Гиперссылки: текст ячейки остаётся, по щелчку открывается адрес."""
    links = com_get(ws, "Hyperlinks")
    for (r, c, url) in cells:
        try:
            com_call(links, "Add", com_get(ws, "Cells", r, c), url.strip(), M, url.strip(), url)
        except Exception:
            pass


def mark_changes(rg, orig_sheet, top_left):
    """Условное форматирование: значение отличается от исходного — жирный
    красный шрифт и красная рамка (заливка не меняется — цвет типа параметра
    остаётся виден)."""
    try:
        fcs = com_get(rg, "FormatConditions")
        fc = com_call(fcs, "Add", 2, M, u"=%s<>%s!%s" % (top_left, orig_sheet, top_left))
        f = com_get(fc, "Font")
        com_set(f, "Bold", True)
        com_set(f, "Color", RED)
        brs = com_get(fc, "Borders")
        for idx in (-4131, -4152, -4160, -4107):     # слева, справа, сверху, снизу
            try:
                b = com_get(brs, "Item", idx)
                com_set(b, "LineStyle", 1)
                com_set(b, "Color", RED)
            except Exception:
                pass
    except Exception:
        pass
HEAD = rgb(232, 245, 233)


def col_letter(n):
    s = u""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def addr(r, c):
    return u"%s%d" % (col_letter(c), r)


def rng(ws, r1, c1, r2, c2):
    return com_get(ws, "Range", u"%s:%s" % (addr(r1, c1), addr(r2, c2)))


_sep = [None]


def apply_cells(ws, cells, fn):
    """fn(range) для списка ячеек (r, c) — подряд идущие ячейки колонки сводятся в один
    диапазон «B2:B900», диапазоны — пачками через объединение адресов (на больших
    спецификациях это в сотни раз меньше обращений к Excel)."""
    bycol = {}
    for (r, c) in cells:
        bycol.setdefault(c, []).append(r)
    addrs = []
    for c in sorted(bycol):
        rows = sorted(set(bycol[c]))
        start = prev = rows[0]
        for r in rows[1:] + [None]:
            if r is not None and r == prev + 1:
                prev = r
                continue
            addrs.append(addr(start, c) if start == prev else u"%s:%s" % (addr(start, c), addr(prev, c)))
            if r is not None:
                start = prev = r
    apply_addrs(ws, addrs, fn)


def apply_addrs(ws, addrs, fn):
    """fn(range) для списка адресов «A1:C1» — пачками через объединение адресов."""
    i = 0
    while i < len(addrs):
        chunk, ln = [], 0
        while i < len(addrs) and ln + len(addrs[i]) + 1 < 240:
            chunk.append(addrs[i])
            ln += len(addrs[i]) + 1
            i += 1
        rg = None
        for sep in ([_sep[0]] if _sep[0] else []) + [u",", u";"]:
            try:
                rg = com_get(ws, "Range", sep.join(chunk))
                _sep[0] = sep
                break
            except Exception:
                rg = None
        if rg is not None:
            fn(rg)
        else:
            for a in chunk:
                fn(com_get(ws, "Range", a))


def merge_all(ws, boxes):
    """Объединить прямоугольники (r1, c1, r2, c2) пачками: Merge многообластного
    диапазона объединяет каждую область отдельно."""
    a = [u"%s:%s" % (addr(r1, c1), addr(r2, c2)) for (r1, c1, r2, c2) in boxes if (r1, c1) != (r2, c2)]
    if not a:
        return
    try:
        apply_addrs(ws, a, lambda rg: com_call(rg, "Merge"))
    except Exception:
        for x in a:                                  # запасной путь — по одному
            try:
                com_call(com_get(ws, "Range", x), "Merge")
            except Exception:
                pass


def col_widths(ws, widths):
    """{колонка: ширина} — колонки с одинаковой шириной одним обращением."""
    byw = {}
    for c, w in widths.items():
        byw.setdefault(round(w, 1), []).append(c)
    for w, cols in byw.items():
        apply_addrs(ws, [u"%s:%s" % (col_letter(c), col_letter(c)) for c in sorted(cols)],
                    lambda rg, w=w: com_set(rg, "ColumnWidth", w))


def fill(color):
    return lambda rg: com_set(com_get(rg, "Interior"), "Color", color)


def unlock(rg):
    com_set(rg, "Locked", False)


def write_array(ws, r1, c1, rows, prop):
    nr, nc = len(rows), max(len(x) for x in rows)
    arr = System.Array.CreateInstance(System.Object, nr, nc)
    for i, row in enumerate(rows):
        for j in range(nc):
            v = row[j] if j < len(row) else u""
            arr[i, j] = v if v is not None else u""
    com_set(rng(ws, r1, c1, r1 + nr - 1, c1 + nc - 1), prop, arr)


def q(s):
    return u'"' + (s or u"").replace(u'"', u'""') + u'"'


# ============================================================
# БЛОК 6. Книга
# ============================================================
excel = Activator.CreateInstance(Type.GetTypeFromProgID("Excel.Application"))
com_set(excel, "DisplayAlerts", False)
com_set(excel, "ScreenUpdating", False)
com_set(excel, "EnableEvents", False)
workbook = com_call(com_get(excel, "Workbooks"), "Add")
try:
    com_set(excel, "Calculation", -4135)         # вручную, пока пишем формулы (иначе пересчёт на каждую пачку)
except Exception:
    pass
tick(u"Excel открыт")
sheets = com_get(workbook, "Sheets")
sheet = com_get(workbook, "ActiveSheet")
com_set(sheet, "Name", u"Лист1")                 # имя, которое читает «Забрать»

# --------- «Как в Revit»
view_ws = None
cell_of = {}                                     # (элемент, колонка) -> адрес ячейки «Как в Revit»
editable = {}                                    # (row_excel, col) -> 'inst' | 'type'
if table_ok:
    view_ws = com_call(sheets, "Add", sheet)          # перед «Лист1»
    com_set(view_ws, "Name", VIEW_SHEET)
    # колонки Excel: после каждой URL-колонки — узкая колонка со ссылкой «↗»
    # (сама ячейка с адресом остаётся обычным текстом — править её просто,
    # а ссылка всегда открывает то, что в ней написано сейчас)
    XC, cc = [], 1
    for j in range(NC):
        XC.append(cc)
        cc += 2 if j in URLCOL else 1
    NCX = cc - 1

    def widen(row, link_mark=u""):
        out = []
        for j, v in enumerate(row):
            out.append(v)
            if j in URLCOL:
                out.append(link_mark if (link_mark and (v or u"").strip()) else u"")
        return out

    out_rows = []
    merges = []
    # заголовок спецификации (раздел «Заголовок»)
    if definition.ShowTitle:
        for r in range(sec_head.FirstRowNumber, sec_head.LastRowNumber + 1):
            t = ctext(sec_head, r, sec_head.FirstColumnNumber)
            out_rows.append([t] + [u""] * (NC - 1))
            merges.append((len(out_rows), 1, len(out_rows), NC))
    title_rows = len(out_rows)
    # шапка колонок с объединениями
    for h in range(H):
        r = b_r0 + h
        out_rows.append([ctext(sec_body, r, b_c0 + j) for j in range(NC)])
        for j in range(NC):
            t, l, b, rr = mcell(sec_body, r, b_c0 + j)
            if t == r and l == b_c0 + j and (b > t or rr > l):
                merges.append((title_rows + 1 + (t - b_r0), 1 + (l - b_c0),
                               title_rows + 1 + (b - b_r0), 1 + (rr - b_c0)))
    head_end = len(out_rows)
    # URL-колонка в шапке объединяется со своей колонкой-ссылкой
    for xr in range(title_rows + 1, head_end + 1):
        for j in URLCOL:
            inside = any(m[0] <= xr <= m[2] and m[1] <= j + 1 <= m[3] for m in merges)
            if not inside:
                merges.append((xr, j + 1, xr, j + 1))
    # строка с номерами граф под шапкой — на ней стоит автофильтр (у многоуровневой
    # шапки фильтр иначе встаёт на верхний уровень)
    out_rows.append([u"%d" % (j + 1) for j in range(NC)])
    filt_row = len(out_rows)
    xl_row = {}
    for r in data_rows:
        out_rows.append(list(ROWTXT[r]))
        xl_row[r] = len(out_rows)
    um_row0 = None
    if unmatched:
        out_rows.append([u"Ниже — элементы, которым не нашлась строка в таблице выше (причина — в пояснениях под таблицей). Их тоже можно править:"] + [u""] * (NC - 1))
        um_row0 = len(out_rows)
        for k in unmatched:
            out_rows.append([v or u"" for v in VT[k]])
    last = len(out_rows)
    data_first = filt_row + 1

    com_set(rng(view_ws, 1, 1, last, NCX), "NumberFormat", u"@")
    write_array(view_ws, 1, 1, [widen(x) for x in out_rows], "Value2")
    merge_all(view_ws, [(r1, XC[c1 - 1], r2, XC[c2 - 1] + (1 if (c2 - 1) in URLCOL else 0))
                        for (r1, c1, r2, c2) in merges])
    whole = rng(view_ws, 1, 1, last, NCX)
    com_set(com_get(whole, "Borders"), "LineStyle", 1)
    com_set(whole, "WrapText", False)
    com_set(whole, "VerticalAlignment", -4108)
    if last > head_end:
        com_set(rng(view_ws, head_end + 1, 1, last, NCX), "RowHeight", 15)
    if head_end:
        hr = rng(view_ws, 1, 1, head_end, NCX)
        com_set(hr, "WrapText", True)
        com_set(com_get(hr, "Font"), "Bold", True)
        com_set(hr, "HorizontalAlignment", -4108)
        com_set(com_get(hr, "Interior"), "Color", HEAD)
    fr = rng(view_ws, filt_row, 1, filt_row, NCX)
    com_set(com_get(fr, "Interior"), "Color", FILT)
    com_set(com_get(fr, "Font"), "Bold", True)
    com_set(fr, "HorizontalAlignment", -4108)
    com_set(fr, "RowHeight", 18)
    # примечание с настоящим именем параметра, если в шапке графа названа иначе
    if H:
        hb = b_r0 + H - 1
        done = set()
        for j, f in enumerate(vis):
            real = (f.GetName() or u"").strip()
            if not real or real == (f.ColumnHeading or u"").strip():
                continue
            t, l, _, _ = mcell(sec_body, hb, b_c0 + j)
            xr, xc = title_rows + 1 + (t - b_r0), XC[l - b_c0]
            if (xr, xc) in done:
                continue
            done.add((xr, xc))
            try:
                cmt = com_call(com_get(view_ws, "Cells", xr, xc), "AddComment", real)
                com_set(com_get(cmt, "Shape"), "Width", max(90, 7 * len(real)))
            except Exception:
                pass
    vw = {}
    for j in range(NC):
        try:
            w = sec_body.GetColumnWidth(b_c0 + j) * FT_MM
        except Exception:
            w = 25.0
        vw[XC[j]] = max(4.0, w / 1.4)
        if j in URLCOL:
            vw[XC[j] + 1] = 3.0
    col_widths(view_ws, vw)

    grays, yellows, opens, peaches = [], [], [], []
    for r in data_rows:
        xr = xl_row[r]
        els = ROW_ELS[r]
        for j in range(NC):
            if r in SERVICE or not els:
                grays.append((xr, XC[j]))
                continue
            sts = [ST[k][j] for k in els]
            origs = set(VT[k][j] for k in els)
            shown = ROWTXT[r][j]
            if sts and all(s == 'ro_type' for s in sts):
                peaches.append((xr, XC[j]))
                continue
            if any(s is None or s in ('ro', 'ro_type') for s in sts) or \
                    (len(origs) > 1) or len(shown) > 250 or any(len(o or u"") > 250 for o in origs):
                grays.append((xr, XC[j]))
                continue
            kind = 'type' if 'type' in sts else 'inst'
            editable[(xr, XC[j])] = kind
            (yellows if kind == 'type' else opens).append((xr, XC[j]))
            for k in els:
                cell_of[(k, j)] = (xr, XC[j], shown)
    if unmatched:
        grays += [(um_row0, XC[j]) for j in range(NC)]
        for i, k in enumerate(unmatched):
            xr = um_row0 + 1 + i
            for j in range(NC):
                st = ST[k][j]
                v = VT[k][j] or u""
                if st == 'ro_type':
                    peaches.append((xr, XC[j]))
                elif st in ('inst', 'type') and len(v) <= 250:
                    editable[(xr, XC[j])] = st
                    (yellows if st == 'type' else opens).append((xr, XC[j]))
                    cell_of[(k, j)] = (xr, XC[j], v)
                else:
                    grays.append((xr, XC[j]))
    apply_cells(view_ws, grays, fill(GRAY))
    apply_cells(view_ws, yellows, fill(YELLOW))
    apply_cells(view_ws, peaches, fill(PEACH))
    apply_cells(view_ws, yellows + opens, unlock)
    # ссылки «↗» рядом с адресами: формула HYPERLINK берёт текущий текст ячейки
    link_cells = []
    for xr in range(data_first, last + 1):
        if um_row0 is not None and xr == um_row0:
            continue
        for j in URLCOL:
            if (out_rows[xr - 1][j] or u"").strip():
                link_cells.append((xr, XC[j]))
    for (xr, xc) in link_cells:
        c = com_get(view_ws, "Cells", xr, xc + 1)
        com_set(c, "NumberFormat", u"General")
        a = addr(xr, xc)
        com_set(c, "Formula", u'=IF(%s="","",HYPERLINK(%s,"\u2197"))' % (a, a))
        com_set(c, "HorizontalAlignment", -4108)
    try:
        com_call(view_ws, "Activate")
        win = com_get(excel, "ActiveWindow")
        com_set(win, "SplitRow", filt_row)
        com_set(win, "FreezePanes", True)
    except Exception:
        pass
    # исходные значения — скрытый лист «_ORIG»: по нему подсвечиваются правки
    orig_ws = com_call(sheets, "Add", System.Reflection.Missing.Value, sheet)
    com_set(orig_ws, "Name", u"_ORIG")
    com_set(rng(orig_ws, 1, 1, last, NCX), "NumberFormat", u"@")
    orig_rows = [widen(x) for x in out_rows]
    for (xr, xc) in link_cells:
        orig_rows[xr - 1][xc] = u"\u2197"
    write_array(orig_ws, 1, 1, orig_rows, "Value2")
    com_set(orig_ws, "Visible", 0)
    if last > filt_row:
        mark_changes(rng(view_ws, data_first, 1, last, NCX), u"_ORIG", addr(data_first, 1))
        com_call(rng(view_ws, filt_row, 1, last, NCX), "AutoFilter")
    # под таблицей, через строку: счётчик правок и пояснения (вне автофильтра)
    cnt_row = last + 2
    a1, a2 = addr(data_first, 1), addr(max(last, data_first), NCX)
    cnt = com_get(view_ws, "Cells", cnt_row, 1)
    com_set(cnt, "NumberFormat", u"General")
    com_set(cnt, "Formula", u'="Изменено ячеек: "&SUMPRODUCT(--(%s:%s<>_ORIG!%s:%s))' % (a1, a2, a1, a2))
    com_set(com_get(cnt, "Font"), "Bold", True)
    com_set(com_get(cnt, "Font"), "Color", RED)
    LEG = [
        (None, u"Как пользоваться", True),
        (None, u"Правьте только на этой вкладке, сохраните книгу и загрузите её кнопкой «Забрать». Правка сгруппированной строки уходит во все элементы этой строки.", False),
        (None, u"Вкладка с именем ведомости — точная копия таблицы Revit (только смотреть). «%s» — эталон: по строке на элемент, значения как в таблице, без формул." % ID_SHEET, False),
        (None, u"Строка с номерами граф под шапкой — автофильтр. Примечание в шапке (красный уголок) — настоящее имя параметра, если графа в ведомости названа иначе.", False),
        (None, u"", False),
        (None, u"Цвета ячеек", True),
        (rgb(255, 255, 255), u"Белая — параметр экземпляра: меняется только у элементов этой строки.", False),
        (YELLOW, u"Жёлтая — параметр ТИПА: изменение затронет все экземпляры этого типа в модели.", False),
        (PEACH, u"Оранжевая — параметр типа, заданный формулой в семействе: менять нельзя.", False),
        (GRAY, u"Серая — менять нельзя: формула, вычисляемое значение, разные значения в сгруппированной строке, нет параметра, заголовки и итоги.", False),
        (None, u"Изменённое значение выделяется жирным красным шрифтом и красной рамкой.", False),
    ]
    if WHY:
        LEG += [(None, u"", False),
                (None, u"Служебное: элементы, которые скрипт не смог привязать к строке таблицы", True),
                (None, u"Это проверка самого скрипта выгрузки, а не ошибки модели. Для каждого показана самая похожая строка и графы, где её текст не совпал со значением элемента. Если таких элементов много — пришлите это Claude.", False)]
        LEG += [(None, w, False) for w in WHY]
    leg0 = cnt_row + 2
    com_set(rng(view_ws, leg0, 2, leg0 + len(LEG) - 1, 2), "NumberFormat", u"@")
    write_array(view_ws, leg0, 2, [[t] for (_, t, _) in LEG], "Value2")
    bycl = {}
    for i, (clr, _, _) in enumerate(LEG):
        if clr is not None:
            bycl.setdefault(clr, []).append((leg0 + i, 1))
    for clr, cells in bycl.items():
        apply_cells(view_ws, cells, fill(clr))
    apply_cells(view_ws, [c for cells in bycl.values() for c in cells], lambda rg: com_set(com_get(rg, "Borders"), "LineStyle", 1))
    apply_cells(view_ws, [(leg0 + i, 2) for i, (_, _, b) in enumerate(LEG) if b], lambda rg: com_set(com_get(rg, "Font"), "Bold", True))
    protect(view_ws)
    tick(u"«Как на виде»")


# цвета по элементам (для «ElementId» и «Лист1»): белый — экземпляр, жёлтый — тип, серый — нельзя
g1, y1, w1, p1 = [], [], [], []
for i, el in enumerate(elements):
    k = eid_int(el.Id)
    g1.append((i + 2, 1))
    g1.append((i + 2, NC + 2))
    for j in range(NC):
        st = ST[k][j]
        (y1 if st == 'type' else (w1 if st == 'inst' else (p1 if st == 'ro_type' else g1))).append((i + 2, j + 2))


def _fixed_widths(ws):
    ww = {1: 11, NC + 2: 12}
    for j in range(NC):
        ww[j + 2] = min(60, max(8, len(vis[j].GetName() or u"") + 2))
    col_widths(ws, ww)


# --------- «_ORIG1» (скрыт): точные значения из модели, раскладка как у «Лист1» — на них ссылается «Лист1»
orig1 = com_call(sheets, "Add", System.Reflection.Missing.Value, sheet)
com_set(orig1, "Name", u"_ORIG1")
o1 = [[u""] * (NC + 2)]
for el in elements:
    k = eid_int(el.Id)
    o1.append([u""] + [(v if v is not None else u"") for v in VAL[k]] + [u""])
com_set(rng(orig1, 1, 1, len(o1), NC + 2), "NumberFormat", u"@")
write_array(orig1, 1, 1, o1, "Value2")
com_set(orig1, "Visible", 0)

# --------- «ElementId»: эталон — по строке на элемент, значения как в таблице, без формул
id_ws = com_call(sheets, "Add", sheet)                # перед «Лист1»
com_set(id_ws, "Name", ID_SHEET)
eid_rows = [[u"ElementId"] + [f.GetName() for f in vis] + [u"UniqueId"]]
for el in elements:
    k = eid_int(el.Id)
    eid_rows.append([k] + [(v if v is not None else u"") for v in VT[k]] + [el.UniqueId])
com_set(rng(id_ws, 1, 2, len(eid_rows), NC + 2), "NumberFormat", u"@")
write_array(id_ws, 1, 1, eid_rows, "Value2")
apply_cells(id_ws, g1, fill(GRAY))
apply_cells(id_ws, y1, fill(YELLOW))
apply_cells(id_ws, p1, fill(PEACH))
hr1 = rng(id_ws, 1, 1, 1, NC + 2)
com_set(com_get(hr1, "Font"), "Bold", True)
com_set(com_get(hr1, "Interior"), "Color", HEAD)
for j, f in enumerate(vis):
    hd = (f.ColumnHeading or u"").strip()
    if hd and hd != (f.GetName() or u"").strip():
        try:
            com_call(com_get(id_ws, "Cells", 1, j + 2), "AddComment", u"В ведомости: " + hd)
        except Exception:
            pass
_fixed_widths(id_ws)
com_call(rng(id_ws, 1, 1, len(eid_rows), NC + 2), "AutoFilter")
protect(id_ws)

# --------- «Лист1» (для «Забрать», скрыт): только ссылки, значений в формулах нет.
# Не меняли ячейку на «Как на виде» (совпадает с _ORIG) — точное значение из _ORIG1;
# поменяли — новое значение с «Как на виде».
ref = u"'%s'!" % VIEW_SHEET
s1 = [[u"ElementId"] + [f.GetName() for f in vis] + [u"UniqueId"]]
for i, el in enumerate(elements):
    k = eid_int(el.Id)
    xr1 = i + 2
    row = [u"=%d" % k]
    for j in range(NC):
        if VAL[k][j] is None:
            row.append(u"")
            continue
        mod = u"_ORIG1!" + addr(xr1, j + 2)
        c = cell_of.get((k, j))
        if c is None:
            row.append(u"=" + mod)
        else:
            a = addr(c[0], c[1])
            expr = u"IF(EXACT(%s%s,_ORIG!%s),%s,\"\"&%s%s)" % (ref, a, a, mod, ref, a)
            row.append(u"=HYPERLINK(%s,%s)" % (expr, expr) if j in URLCOL else u"=" + expr)
    row.append(u"='%s'!%s" % (ID_SHEET, addr(xr1, NC + 2)))
    s1.append(row)
write_array(sheet, 1, 1, s1, "Formula")
apply_cells(sheet, g1, fill(GRAY))
apply_cells(sheet, y1, fill(YELLOW))
apply_cells(sheet, p1, fill(PEACH))
if not table_ok:
    # таблицу Revit разобрать не удалось — как раньше: правка прямо на «Лист1»
    apply_cells(sheet, y1 + w1, unlock)
_fixed_widths(sheet)
if elements and NC:
    mark_changes(rng(sheet, 2, 2, len(elements) + 1, NC + 1), u"_ORIG1", addr(2, 2))
com_call(rng(sheet, 1, 1, len(elements) + 1, NC + 2), "AutoFilter")
protect(sheet)
if table_ok:
    com_set(sheet, "Visible", 0)                      # «Лист1» — служебный для «Забрать», скрыт
tick(u"ElementId")


# --------- копия ведомости один в один (только смотреть): текст, объединения, ширина, высота, шрифты
def _rgb_of(c):
    try:
        return rgb(c.Red, c.Green, c.Blue)
    except Exception:
        return None


def _sheet_name(n):
    for ch in u'[]:*?/\\':
        n = n.replace(ch, u"_")
    n = n.strip(u"'")[:31]
    return n or u"Ведомость"


CP_FONT = [None, 0]
HALIGN = {u"Left": -4131, u"Center": -4108, u"Right": -4152}
VALIGN = {u"Top": -4160, u"Middle": -4108, u"Bottom": -4107}
try:
    cp_ws = com_call(sheets, "Add", view_ws if view_ws is not None else id_ws)
    nm = _sheet_name(selected.Name)
    if nm in (VIEW_SHEET, ID_SHEET, u"Лист1"):
        nm = nm + u" (Revit)"
    com_set(cp_ws, "Name", nm)
    rows_txt, styles, cmerges, heights = [], {}, [], []
    ncol = b_c1 - b_c0 + 1
    wrap_rows = []                                   # заголовок и шапка — с переносом, данные — без (как в Revit)
    for stype, sec in ((SectionType.Header, sec_head), (SectionType.Body, sec_body)):
        if sec is None or sec.NumberOfRows == 0 or sec.NumberOfColumns == 0:
            continue
        c0 = sec.FirstColumnNumber
        is_head = stype == SectionType.Header
        for r in range(sec.FirstRowNumber, sec.LastRowNumber + 1):
            xr = len(rows_txt) + 1
            row = []
            if is_head or r < sec.FirstRowNumber + H:
                wrap_rows.append(xr)
            if is_head:
                # строка заголовка ведомости в Revit идёт на всю ширину таблицы
                cmerges.append((xr, 1, xr, ncol))
            try:
                heights.append((xr, sec.GetRowHeight(r) * FT_MM * 2.835))
            except Exception:
                pass
            for j in range(ncol):
                c = c0 + j
                if c > sec.LastColumnNumber:
                    row.append(u"")
                    continue
                t, l, b, rr = mcell(sec, r, c)
                if (t, l) != (r, c):
                    row.append(u"")
                    continue
                try:
                    row.append(selected.GetCellText(stype, r, c) or u"")
                except Exception:
                    row.append(u"")
                if (b > t or rr > l) and not is_head:
                    cmerges.append((xr, j + 1, xr + (b - t), j + 1 + (rr - l)))
                try:
                    cs = sec.GetTableCellStyle(r, c)
                    size = cs.TextSize
                    size = size * FT_MM * 2.835 if size < 1 else size
                    key = (cs.FontName, round(size, 1), cs.IsFontBold, cs.IsFontItalic, cs.IsFontUnderline,
                           _rgb_of(cs.TextColor), _rgb_of(cs.BackgroundColor),
                           str(cs.FontHorizontalAlignment), str(cs.FontVerticalAlignment))
                except Exception:
                    key = None
                styles.setdefault(key, []).append((xr, j + 1))
            if is_head:
                txt = [x for x in row if x.strip()]
                row = [txt[0] if txt else u""] + [u""] * (ncol - 1)
            rows_txt.append(row)
    if rows_txt:
        nr = len(rows_txt)
        com_set(rng(cp_ws, 1, 1, nr, ncol), "NumberFormat", u"@")
        write_array(cp_ws, 1, 1, rows_txt, "Value2")
        whole_cp = rng(cp_ws, 1, 1, nr, ncol)
        com_set(com_get(whole_cp, "Borders"), "LineStyle", 1)
        com_set(whole_cp, "WrapText", False)
        apply_cells(cp_ws, [(xr, 1) for xr in wrap_rows], lambda rg: com_set(com_get(rg, "EntireRow"), "WrapText", True))
        # самый частый шрифт — на весь лист одним обращением, у групп меняется только отличие
        top = None
        for k_ in styles:
            if k_ is not None and (top is None or len(styles[k_]) > len(styles[top])):
                top = k_
        CP_FONT[0], CP_FONT[1] = (top[0], top[1]) if top else (None, 0)
        fw = com_get(whole_cp, "Font")
        if CP_FONT[0]:
            com_set(fw, "Name", CP_FONT[0])
        if CP_FONT[1] and 4 <= CP_FONT[1] <= 72:
            com_set(fw, "Size", CP_FONT[1])
        for key, cells in styles.items():
            if key is None:
                continue
            fname, size, bold, ital, und, tcol, bcol, ha, va = key

            def _st(rg, fname=fname, size=size, bold=bold, ital=ital, und=und, tcol=tcol, bcol=bcol, ha=ha, va=va):
                f = com_get(rg, "Font")
                if fname and fname != CP_FONT[0]:
                    com_set(f, "Name", fname)
                if size and 4 <= size <= 72 and abs(size - CP_FONT[1]) > 0.05:
                    com_set(f, "Size", size)
                if bold:
                    com_set(f, "Bold", True)
                if ital:
                    com_set(f, "Italic", True)
                if und:
                    com_set(f, "Underline", 2)
                if tcol is not None and tcol != 0:
                    com_set(f, "Color", tcol)
                if bcol is not None and bcol != rgb(255, 255, 255):
                    com_set(com_get(rg, "Interior"), "Color", bcol)
                if ha in HALIGN:
                    com_set(rg, "HorizontalAlignment", HALIGN[ha])
                if va in VALIGN:
                    com_set(rg, "VerticalAlignment", VALIGN[va])
            apply_cells(cp_ws, cells, _st)
        merge_all(cp_ws, cmerges)
        cw = {}
        for j in range(ncol):
            try:
                w = sec_body.GetColumnWidth(b_c0 + j) * FT_MM
            except Exception:
                w = 25.0
            cw[j + 1] = max(2.0, w / 1.9)
        col_widths(cp_ws, cw)
        byh = {}
        for (xr, h) in heights:
            byh.setdefault(round(max(3.0, min(409.0, h)), 1), []).append((xr, 1))
        for h, cells in byh.items():
            apply_cells(cp_ws, cells, lambda rg, h=h: com_set(com_get(rg, "EntireRow"), "RowHeight", h))
    protect(cp_ws)
except Exception as ex_cp:
    TIMES.append(u"копия ведомости не построена: %s" % ex_cp)
tick(u"копия ведомости")


# --------- «_DBTOOLS_META» (как раньше)
meta_sheet = com_call(sheets, "Add", System.Reflection.Missing.Value, sheet)
com_set(meta_sheet, "Name", "_DBTOOLS_META")
meta = [[u"excel_col", u"header", u"param_id", u"is_builtin", u"schedule_name"]]
for j, f in enumerate(vis):
    pid = f.ParameterId
    if pid == ElementId.InvalidElementId:
        meta.append([j + 2, f.GetName(), u"", False, selected.Name])
    else:
        meta.append([j + 2, f.GetName(), eid_int(pid), eid_int(pid) < 0, selected.Name])
write_array(meta_sheet, 1, 1, meta, "Value2")
com_set(meta_sheet, "Visible", 0)

if table_ok:
    com_call(view_ws, "Activate")
else:
    com_call(sheet, "Activate")
try:
    com_set(excel, "Calculation", -4105)         # снова автоматически — один пересчёт в конце
except Exception:
    pass
com_set(excel, "EnableEvents", True)
com_set(excel, "ScreenUpdating", True)
com_set(excel, "Visible", True)
tick(u"книга готова")


# ============================================================
# БЛОК 7. Итог
# ============================================================
msg = [u"Готово!", u""]
if table_ok:
    msg += [u"Правьте на вкладке «%s», потом сохраните книгу и загрузите её кнопкой «Забрать»." % VIEW_SHEET,
            u"Что значат цвета — под таблицей на вкладке «%s»." % VIEW_SHEET]
    if unmatched:
        msg += [u"", u"Без строки в спецификации: %d эл. — они внизу «%s», причина — под таблицей." % (len(unmatched), VIEW_SHEET)]
else:
    msg += [u"Вид спецификации разобрать не удалось — правка на «Лист1», как раньше.",
            u"Белые — экземпляр, жёлтые — тип, оранжевые — тип по формуле (нельзя), серые — нельзя."]
msg += [u"", u"Элементов: %d, строк: %d. Время: %s" % (len(elements), len(data_rows), u"; ".join(TIMES))]
forms.alert(u"\n".join(msg), title=u"Экспорт завершён")
