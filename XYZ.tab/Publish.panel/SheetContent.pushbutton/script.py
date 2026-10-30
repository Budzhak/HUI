# -*- coding: utf-8 -*-
"""Состав листов → Excel (то, что в диспетчере проекта лежит под каждым листом).

Выбираешь коллекцию листов / группу из диспетчера проекта (например,
«РД › ВК/ОВ/ТМ») и получаешь в Excel два листа:

  «Содержимое листов» — плоская таблица, одна строка на каждый вид:
      Коллекция | Раздел проекта | Лист | Название листа | Тип | Вид | Заголовок на листе | Как в диспетчере | Масштаб | Формат | ID
      (по ней удобно фильтровать и сверять с планом в других программах);
  «Как в диспетчере»  — то же деревом: строка листа, под ней его виды;
      графа «Масштаб»: у листа — формат (А0…А4, А3x3), у вида — «1 : 100»;
      графа «Заголовок на листе»: у вида — его параметр «Заголовок на листе».

Что считается содержимым листа: виды на видовых экранах (планы, разрезы, 3D,
чертёжные виды), легенды и спецификации. Служебные ведомости изменений
не выводятся. Порядок — как в диспетчере: листы по номеру, виды по тексту
«Тип: имя». Листы без видов остаются в таблице с пустым содержимым.

Excel открывается с готовой книгой, ничего не сохраняется — сохрани сам.
"""
import re
import time

from pyrevit import revit, DB
import xyz_forms as forms   # единый стиль окон XYZ (lib/xyz_forms.py)

import System
from System import Type, Activator
from System.Reflection import BindingFlags

doc = revit.doc

SECTION_PARAM = u'ADSK_Штамп Раздел проекта'

INVISIBLE = u' ­​‌‍‎‏⁠‪‫‬‭‮﻿'

# виды, у которых масштаб не имеет смысла
NO_SCALE = ('Schedule', 'PanelSchedule', 'ColumnSchedule', 'Legend', 'CostReport', 'LoadsReport',
            'PresureLossReport', 'Report', 'DrawingSheet', 'ProjectBrowser', 'SystemBrowser',
            'Internal', 'Undefined')


# ======================================================================
#  Мелочи
# ======================================================================
def nat_key(text):
    parts = re.split(u'(\\d+)', text or u'')
    return [(0, int(p), u'') if p.isdigit() else (1, 0, p.lower()) for p in parts if p != u'']


def clean(text):
    """Без невидимых символов и пробелов по краям (в проекте они различают одинаковые номера)."""
    t = text or u''
    i, j = 0, len(t)
    while i < j and (t[i].isspace() or t[i] in INVISIBLE):
        i += 1
    while j > i and (t[j - 1].isspace() or t[j - 1] in INVISIBLE):
        j -= 1
    return t[i:j]


def idval(eid):
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


def col_letter(n):
    s = u''
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = u'%s%s' % (chr(65 + r), s)
    return s


# ======================================================================
#  Данные из Revit
# ======================================================================
def collection_name(sheet):
    """Коллекция листов (Revit 2025+)."""
    try:
        cid = sheet.SheetCollectionId
        if cid is None or cid == DB.ElementId.InvalidElementId:
            return u''
        el = doc.GetElement(cid)
        return el.Name if el is not None else u''
    except Exception:
        return u''


def folder_path(sheet, org):
    """Папки диспетчера проекта (текущая организация листов)."""
    if org is None:
        return ()
    try:
        return tuple(fi.Name for fi in org.GetFolderItems(sheet.Id))
    except Exception:
        return ()


def section_value(sheet):
    p = sheet.LookupParameter(SECTION_PARAM)
    if p is None:
        return u''
    return p.AsString() or p.AsValueString() or u''


# как в диспетчере проекта (русский Revit); остальные типы берём из параметра вида «Семейство»
VIEW_LABELS = {
    'FloorPlan': u'План этажа', 'CeilingPlan': u'План потолка', 'EngineeringPlan': u'Структурный план',
    'AreaPlan': u'План площади', 'Elevation': u'Фасад', 'Section': u'Разрез', 'Detail': u'Узел',
    'ThreeD': u'3D вид', 'DraftingView': u'Чертежный вид', 'Legend': u'Легенда',
    'Schedule': u'Спецификация', 'PanelSchedule': u'Панельная ведомость',
    'ColumnSchedule': u'Ведомость колонн', 'Walkthrough': u'Пролёт', 'Rendering': u'Визуализация',
}


def view_label(v):
    name = str(v.ViewType)
    if name in VIEW_LABELS:
        return VIEW_LABELS[name]
    try:
        p = v.get_Parameter(DB.BuiltInParameter.VIEW_FAMILY)
        if p is not None and p.AsString():
            return p.AsString()
    except Exception:
        pass
    return name


def view_scale(v):
    if str(v.ViewType) in NO_SCALE:
        return u''
    try:
        return u'1 : %d' % v.Scale
    except Exception:
        return u''


A_SIZES = ((u'А0', 841, 1189), (u'А1', 594, 841), (u'А2', 420, 594), (u'А3', 297, 420), (u'А4', 210, 297))


def _tb_param(tb, name):
    for el in (tb, getattr(tb, 'Symbol', None)):
        if el is None:
            continue
        p = el.LookupParameter(name)
        if p is not None and p.HasValue:
            if p.StorageType == DB.StorageType.Integer:
                return p.AsInteger()
            try:
                return int(round(float((p.AsValueString() or p.AsString() or u'').replace(u',', u'.'))))
            except Exception:
                return None
    return None


def sheet_format(sheet):
    """Формат листа по основной надписи: параметр «Формат А» (+ «Кратность»),
    иначе — по размеру надписи (ближайший А0…А4, с кратностью по длинной стороне)."""
    try:
        tbs = list(DB.FilteredElementCollector(doc, sheet.Id).OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                   .WhereElementIsNotElementType())
    except Exception:
        tbs = []
    best, area = None, 0.0
    for tb in tbs:
        try:
            w = tb.get_Parameter(DB.BuiltInParameter.SHEET_WIDTH).AsDouble() * 304.8
            h = tb.get_Parameter(DB.BuiltInParameter.SHEET_HEIGHT).AsDouble() * 304.8
        except Exception:
            w = h = 0.0
        if w * h > area:
            best, area, bw, bh = tb, w * h, w, h
    if best is None:
        return u''
    fa = _tb_param(best, u'Формат А')
    kr = _tb_param(best, u'Кратность')
    if fa is not None and 0 <= fa <= 4:
        return u'А%d' % fa + (u'x%d' % kr if kr and kr > 1 else u'')
    a, b = sorted((bw, bh))
    for name, s1, s2 in A_SIZES:          # основной формат
        if abs(a - s1) <= 5 and abs(b - s2) <= 5:
            return name
    for name, s1, s2 in A_SIZES:          # дополнительный: короткая сторона = длинной стороне А, длинная = k × короткой
        k = int(round(b / float(s1)))
        if abs(a - s2) <= 5 and k >= 3 and abs(b - s1 * k) <= 5:
            return u'%sx%d' % (name, k)
    return u'%d×%d' % (round(bw), round(bh))


def view_title(v):
    """Параметр вида «Заголовок на листе» (VIEW_DESCRIPTION)."""
    try:
        p = v.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
        if p is not None:
            return p.AsString() or u''
    except Exception:
        pass
    return u''


def sheet_contents(sheet):
    """Виды на листе: видовые экраны, легенды, спецификации (без дублей)."""
    found = {}
    try:
        for vpid in sheet.GetAllViewports():
            vp = doc.GetElement(vpid)
            v = doc.GetElement(vp.ViewId) if vp is not None else None
            if v is not None:
                found[idval(v.Id)] = v
    except Exception:
        pass
    try:
        for ssi in DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.ScheduleSheetInstance):
            v = doc.GetElement(ssi.ScheduleId)
            if v is None:
                continue
            if isinstance(v, DB.ViewSchedule) and v.IsTitleblockRevisionSchedule:
                continue
            found[idval(v.Id)] = v
    except Exception:
        pass
    try:
        for vid in sheet.GetAllPlacedViews():
            v = doc.GetElement(vid)
            if v is not None and idval(v.Id) not in found:
                if isinstance(v, DB.ViewSchedule) and v.IsTitleblockRevisionSchedule:
                    continue
                found[idval(v.Id)] = v
    except Exception:
        pass
    res = []
    for v in found.values():
        kind = view_label(v)
        res.append(dict(kind=kind, name=v.Name, text=u'%s: %s' % (kind, v.Name),
                        scale=view_scale(v), title=view_title(v), vid=idval(v.Id)))
    res.sort(key=lambda d: d['text'].lower())
    return res


def collect(sheets):
    try:
        org = DB.BrowserOrganization.GetCurrentBrowserOrganizationForSheets(doc)
    except Exception:
        org = None
    data = []
    for sh in sheets:
        data.append(dict(sheet=sh, number=clean(sh.SheetNumber), name=sh.Name,
                         coll=collection_name(sh), path=folder_path(sh, org),
                         section=clean(section_value(sh)), sid=idval(sh.Id)))
    return data


def group_key(d):
    return (d['coll'], d['path'])


def group_text(key):
    coll, path = key
    parts = ([coll] if coll else []) + list(path)
    return u' › '.join(parts) if parts else u'(без группы)'


# ======================================================================
#  COM-хелперы Excel (как в Export.pushbutton)
# ======================================================================
def com_get(obj, name, *args):
    return obj.GetType().InvokeMember(
        name, BindingFlags.GetProperty, None, obj, System.Array[object](args))


def com_set(obj, name, value):
    obj.GetType().InvokeMember(
        name, BindingFlags.SetProperty, None, obj, System.Array[object]([value]))


def com_call(obj, name, *args):
    return obj.GetType().InvokeMember(
        name, BindingFlags.InvokeMethod, None, obj, System.Array[object](args))


def rgb(r, g, b):
    return r + g * 256 + b * 65536


DARK = rgb(30, 42, 34)          # #1E2A22 — шапка, как в окнах плагина
LIGHT = rgb(232, 245, 233)      # #E8F5E9 — строка листа
WHITE = rgb(255, 255, 255)


def rng(ws, r1, c1, r2, c2):
    return com_get(ws, 'Range', u'%s%d:%s%d' % (col_letter(c1), r1, col_letter(c2), r2))


def fill(range_, color):
    com_set(com_get(range_, 'Interior'), 'Color', color)


def font(range_, bold=None, color=None):
    f = com_get(range_, 'Font')
    if bold is not None:
        com_set(f, 'Bold', bold)
    if color is not None:
        com_set(f, 'Color', color)


_sep = [None]


def get_union(ws, addrs):
    """Диапазон из нескольких кусков. Разделитель зависит от настроек Excel (',' или ';') — пробуем оба."""
    if len(addrs) == 1:
        return com_get(ws, 'Range', addrs[0])
    for sep in ([_sep[0]] if _sep[0] else []) + [u',', u';']:
        try:
            rg = com_get(ws, 'Range', sep.join(addrs))
            _sep[0] = sep
            return rg
        except Exception:
            continue
    return None


def format_rows(ws, rows, ncols, color, bold, font_color):
    """Оформить много строк за несколько вызовов (по 12 диапазонов); если объединение не вышло —
    по одной строке: медленнее, но работает."""
    addrs = [u'A%d:%s%d' % (r, col_letter(ncols), r) for r in rows]
    for i in range(0, len(addrs), 12):
        chunk = addrs[i:i + 12]
        rg = get_union(ws, chunk)
        parts = [rg] if rg is not None else [com_get(ws, 'Range', a) for a in chunk]
        for part in parts:
            fill(part, color)
            font(part, bold=bold, color=font_color)


def write_table(ws, header, rows):
    """Шапка + строки одним махом (двумерный массив) — быстро даже на тысячах строк."""
    ncols = len(header)
    nrows = len(rows) + 1
    arr = System.Array.CreateInstance(System.Object, nrows, ncols)
    for j, h in enumerate(header):
        arr[0, j] = h
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            arr[i + 1, j] = v if v is not None else u''
    body = rng(ws, 1, 1, nrows, ncols)
    com_set(body, 'NumberFormat', u'@')     # текст: «0», «4.1» не превращаются в числа и даты
    com_set(body, 'Value2', arr)
    head = rng(ws, 1, 1, 1, ncols)
    fill(head, DARK)
    font(head, bold=True, color=WHITE)
    return nrows


# ======================================================================
#  Запуск
# ======================================================================
def main():
    sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements())
    if not sheets:
        forms.alert(u'В проекте нет листов.', title=u'Состав листов')
        return
    t0 = time.time()
    data = collect(sheets)
    t_collect = time.time() - t0

    # --- какие группы выгружаем
    groups = {}
    for d in data:
        groups.setdefault(group_key(d), []).append(d)
    keys = sorted(groups, key=lambda k: (nat_key(k[0]), [nat_key(p) for p in k[1]]))
    labels = [u'%s  ·  листов: %d' % (group_text(k), len(groups[k])) for k in keys]
    if len(keys) > 1:
        picked = forms.SelectFromList.show(labels, multiselect=True, title=u'Какие группы листов выгрузить',
                                           button_name=u'В Excel')
        if not picked:
            return
        keys = [k for k, lb in zip(keys, labels) if lb in picked]

    # --- строки
    t1 = time.time()
    flat, tree = [], []
    n_sheets = n_views = 0
    for k in keys:
        items = sorted(groups[k], key=lambda d: (nat_key(d['number']), d['number']))
        coll = k[0]
        tree.append((u'group', group_text(k), u'', u'', u''))
        for d in items:
            n_sheets += 1
            contents = sheet_contents(d['sheet'])
            n_views += len(contents)
            label = u'%s - %s' % (d['number'], d['name'])
            fmt = sheet_format(d['sheet'])
            tree.append((u'sheet', label, u'', fmt, u''))
            base = [coll, d['section'] or u' › '.join(k[1]), d['number'], d['name']]
            if not contents:
                flat.append(base + [u'', u'', u'', u'', u'', fmt, d['sid'], u''])
            for c in contents:
                flat.append(base + [c['kind'], c['name'], c['title'], c['text'], c['scale'], fmt, d['sid'], c['vid']])
                tree.append((u'view', u'', c['text'], c['scale'], c['title']))
    if not flat:
        forms.alert(u'Нет листов в выбранных группах.', title=u'Состав листов')
        return

    t2 = time.time()

    # --- Excel
    try:
        excel = Activator.CreateInstance(Type.GetTypeFromProgID('Excel.Application'))
    except Exception:
        forms.alert(u'Не удалось запустить Excel. Он установлен на этом компьютере?', title=u'Состав листов')
        return
    com_set(excel, 'DisplayAlerts', False)
    com_set(excel, 'ScreenUpdating', False)     # без перерисовки каждой ячейки — главное ускорение
    try:
        com_set(excel, 'EnableEvents', False)
        com_set(excel, 'Calculation', -4135)    # xlCalculationManual
    except Exception:
        pass
    com_set(excel, 'Visible', True)
    try:
        wb = com_call(com_get(excel, 'Workbooks'), 'Add')
        ws = com_get(wb, 'ActiveSheet')
        com_set(ws, 'Name', u'Содержимое листов')

        header = [u'Коллекция', SECTION_PARAM, u'Лист', u'Название листа', u'Тип', u'Вид', u'Заголовок на листе',
                  u'Как в диспетчере', u'Масштаб', u'Формат', u'ID листа', u'ID вида']
        write_table(ws, header, flat)
        try:
            com_call(rng(ws, 1, 1, 1, len(header)), 'AutoFilter')
        except Exception:
            pass
        com_call(com_get(ws, 'Columns'), 'AutoFit')
        try:
            com_call(ws, 'Activate')
            win = com_get(excel, 'ActiveWindow')
            com_set(win, 'SplitColumn', 0)
            com_set(win, 'SplitRow', 1)
            com_set(win, 'FreezePanes', True)
        except Exception:
            pass

        # --- дерево
        ws2 = com_call(com_get(wb, 'Sheets'), 'Add', System.Reflection.Missing.Value,
                       com_get(wb, 'ActiveSheet'))
        com_set(ws2, 'Name', u'Как в диспетчере')
        write_table(ws2, [u'Лист', u'Содержимое', u'Масштаб', u'Заголовок на листе'], [[a[1], a[2], a[3], a[4]] for a in tree])
        com_call(com_get(ws2, 'Columns'), 'AutoFit')
        group_rows = [i + 2 for i, a in enumerate(tree) if a[0] == u'group']
        sheet_rows = [i + 2 for i, a in enumerate(tree) if a[0] == u'sheet']
        try:
            format_rows(ws2, group_rows, 4, DARK, True, WHITE)
            format_rows(ws2, sheet_rows, 4, LIGHT, True, None)
        except Exception:
            pass
        try:
            com_call(ws2, 'Activate')
            win2 = com_get(excel, 'ActiveWindow')
            com_set(win2, 'SplitColumn', 0)
            com_set(win2, 'SplitRow', 1)
            com_set(win2, 'FreezePanes', True)
        except Exception:
            pass
        com_call(ws, 'Activate')
    finally:
        try:
            com_set(excel, 'Calculation', -4105)    # xlCalculationAutomatic
            com_set(excel, 'EnableEvents', True)
        except Exception:
            pass
        com_set(excel, 'ScreenUpdating', True)
    t3 = time.time()

    forms.alert(u'Готово!\n\nЛистов: %d\nВидов, легенд и спецификаций на них: %d\n\n'
                u'Книга открыта в Excel, не сохранена.\n'
                u'Время: чтение Revit %.1f с · Excel %.1f с'
                % (n_sheets, n_views, t_collect + (t2 - t1), t3 - t2),
                title=u'Состав листов')


if __name__ == '__main__':
    main()
