# -*- coding: utf-8 -*-
"""Оформление ведомостей: перенос настроек вида с одной ведомости на другие с теми же графами
(кнопка «Под копирку», панель «Обмен», db 18.10.2026).

Переносится: порядок граф, видимость, заголовки граф (текст, выравнивание, ориентация),
ширина граф, формат чисел, стиль ячеек граф (шрифт, размер, начертание, цвет, границы,
выравнивание), стиль ячеек шапки граф, заголовок ведомости (высота, стиль), группы заголовков,
типы текста (заголовок / шапка / тело), «показывать заголовок / шапку / линии сетки».
Высоту строк шапки граф и строк данных Revit API менять не даёт — её задаёт сам Revit по тексту.
НЕ переносится: фильтры, сортировка, состав граф — это содержание, а не вид.

    import xyz_schedfmt as sf
    sf.same_fields(src, tgt)            # -> (одинаковые графы?, только порядок другой?)
    notes = sf.copy_format(doc, src, tgt)   # в открытой транзакции
"""
from Autodesk.Revit.DB import SectionType, ScheduleFieldType

FT_MM = 304.8


def _iv(eid):
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


def field_key(f):
    """Чем графа является (а не как она подписана): параметр + тип поля; у расчётных — имя."""
    pid = _iv(f.ParameterId)
    ft = str(f.FieldType)
    if pid == -1 or f.IsCalculatedField:
        return (ft, -1, f.GetName())
    return (ft, pid)


def fields(vs):
    d = vs.Definition
    return [d.GetField(i) for i in range(d.GetFieldCount())]


def same_fields(src, tgt):
    a = [field_key(f) for f in fields(src)]
    b = [field_key(f) for f in fields(tgt)]
    if sorted(a) != sorted(b):
        return False, False
    return True, a != b


def candidates(doc, src):
    """Ведомости проекта с теми же графами, что у src. -> [(ведомость, только порядок другой)]."""
    from Autodesk.Revit.DB import FilteredElementCollector, ViewSchedule
    out = []
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            if vs.Id == src.Id or vs.IsTemplate or vs.IsTitleblockRevisionSchedule or vs.IsInternalKeynoteSchedule:
                continue
        except Exception:
            continue
        ok, reorder = same_fields(src, vs)
        if ok:
            out.append((vs, reorder))
    out.sort(key=lambda x: x[0].Name)
    return out


# ------------------------------------------------------------------ строки таблицы
def _head_rows(vs, sec):
    """Строки шапки граф в теле: от начала до первой целиком пустой строки (её Revit ставит
    между шапкой и данными). -> (строки шапки, пустая строка или None)."""
    if not vs.Definition.ShowHeaders:
        return [], None
    r0 = sec.FirstRowNumber
    n = sec.NumberOfRows
    heads = []
    for r in range(r0, r0 + n):
        txt = [vs.GetCellText(SectionType.Body, r, c) for c in range(sec.FirstColumnNumber,
                                                                       sec.FirstColumnNumber + sec.NumberOfColumns)]
        if not any(t for t in txt):
            return heads, r
        heads.append(r)
        if len(heads) >= 4:
            break
    return heads, None


def _data_rows(vs, sec):
    heads, blank = _head_rows(vs, sec)
    skip = set(heads)
    if blank is not None:
        skip.add(blank)
    r0 = sec.FirstRowNumber
    return [r for r in range(r0, r0 + sec.NumberOfRows) if r not in skip]


def _merged_groups(vs, sec, rows):
    """Группы заголовков (объединённые ячейки шапки): [(верх, лево, низ, право, текст)]."""
    out, seen = [], set()
    for r in rows:
        for c in range(sec.FirstColumnNumber, sec.FirstColumnNumber + sec.NumberOfColumns):
            try:
                m = sec.GetMergedCell(r, c)
            except Exception:
                continue
            key = (m.Top, m.Left, m.Bottom, m.Right)
            if key in seen or (m.Top == m.Bottom and m.Left == m.Right):
                continue
            seen.add(key)
            out.append((m.Top, m.Left, m.Bottom, m.Right, vs.GetCellText(SectionType.Body, m.Top, m.Left)))
    return out


# ------------------------------------------------------------------ перенос
def _pairs(src, tgt):
    tmap = {}
    for f in fields(tgt):
        tmap.setdefault(field_key(f), []).append(f)
    out = []
    for f in fields(src):
        lst = tmap.get(field_key(f)) or []
        if lst:
            out.append((f, lst.pop(0)))
    return out


def copy_format(doc, src, tgt):
    """Перенести оформление src → tgt (графы те же). Возвращает список замечаний."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ScheduleFieldId
    notes = []
    sd, td = src.Definition, tgt.Definition
    tmap = {}
    for f in fields(tgt):
        tmap.setdefault(field_key(f), []).append(f)
    pairs = []
    for f in fields(src):
        lst = tmap.get(field_key(f)) or []
        if lst:
            pairs.append((f, lst.pop(0)))
    # порядок граф
    try:
        paired = set(_iv_fid(g.FieldId) for f, g in pairs)
        rest = [td.GetFieldId(i) for i in range(td.GetFieldCount()) if _iv_fid(td.GetFieldId(i)) not in paired]
        order = List[ScheduleFieldId]([g.FieldId for f, g in pairs] + rest)
        cur = [_iv_fid(td.GetFieldId(i)) for i in range(td.GetFieldCount())]
        if [_iv_fid(x) for x in order] != cur:
            td.SetFieldOrder(order)
    except Exception as ex:
        notes.append(u'порядок граф: %s' % ex)
    # графы
    for f, g in pairs:
        for name in ('ColumnHeading', 'HorizontalAlignment', 'VerticalAlignment', 'HeadingOrientation',
                     'IsHidden', 'GridColumnWidth', 'SheetColumnWidth'):
            try:
                v = getattr(f, name)
                if getattr(g, name) != v:
                    setattr(g, name, v)
            except Exception as ex:
                notes.append(u'«%s» %s: %s' % (f.ColumnHeading, name, ex))
        try:
            g.SetFormatOptions(f.GetFormatOptions())
        except Exception:
            pass
        try:
            g.SetStyle(f.GetStyle())
        except Exception as ex:
            notes.append(u'«%s» стиль: %s' % (f.ColumnHeading, ex))
    # общее
    for name in ('ShowTitle', 'ShowHeaders', 'ShowGridLines'):
        try:
            v = getattr(sd, name)
            if getattr(td, name) != v:
                setattr(td, name, v)
        except Exception as ex:
            notes.append(u'%s: %s' % (name, ex))
    for name in ('TitleTextTypeId', 'HeaderTextTypeId', 'BodyTextTypeId'):
        try:
            setattr(tgt, name, getattr(src, name))
        except Exception as ex:
            notes.append(u'%s: %s' % (name, ex))
    doc.Regenerate()
    # заголовок ведомости (секция Header)
    try:
        hs, ht = src.GetTableData().GetSectionData(SectionType.Header), tgt.GetTableData().GetSectionData(SectionType.Header)
        if hs is not None and ht is not None:
            for r in range(min(hs.NumberOfRows, ht.NumberOfRows)):
                ht.SetRowHeight(ht.FirstRowNumber + r, hs.GetRowHeight(hs.FirstRowNumber + r))
                for c in range(min(hs.NumberOfColumns, ht.NumberOfColumns)):
                    try:
                        ht.SetCellStyle(ht.FirstRowNumber + r, ht.FirstColumnNumber + c,
                                        hs.GetTableCellStyle(hs.FirstRowNumber + r, hs.FirstColumnNumber + c))
                    except Exception:
                        pass
    except Exception as ex:
        notes.append(u'заголовок ведомости: %s' % ex)
    # шапка граф: группы заголовков, высота, стиль ячеек
    try:
        bs, bt = src.GetTableData().GetSectionData(SectionType.Body), tgt.GetTableData().GetSectionData(SectionType.Body)
        hs_rows, hs_blank = _head_rows(src, bs)
        groups = _merged_groups(src, bs, hs_rows)
        ht_rows, _ = _head_rows(tgt, bt)
        old = _merged_groups(tgt, bt, ht_rows)
        if [x[:4] + (x[4],) for x in groups] != [x[:4] + (x[4],) for x in old]:
            for (t, l, b, r, txt) in old:
                try:
                    tgt.UngroupHeaders(t, l, b, r)
                except Exception:
                    pass
            for (t, l, b, r, txt) in groups:
                try:
                    tgt.GroupHeaders(t, l, b, r, txt)
                except Exception as ex:
                    notes.append(u'группа заголовков «%s»: %s' % (txt, ex))
            doc.Regenerate()
            bt = tgt.GetTableData().GetSectionData(SectionType.Body)
        ht_rows, ht_blank = _head_rows(tgt, bt)
        # высоту строк тела стандартной ведомости Revit API менять не даёт («forbidden for cells in
        # standard schedule body sections», проверено 18.10.2026) — только стиль ячеек шапки граф
        for rs, rt in zip(hs_rows, ht_rows):
            for c in range(min(bs.NumberOfColumns, bt.NumberOfColumns)):
                try:
                    if bt.AllowOverrideCellStyle(rt, bt.FirstColumnNumber + c):
                        bt.SetCellStyle(rt, bt.FirstColumnNumber + c, bs.GetTableCellStyle(rs, bs.FirstColumnNumber + c))
                except Exception:
                    pass
    except Exception as ex:
        notes.append(u'шапка граф: %s' % ex)
    return notes


def _iv_fid(fid):
    try:
        return fid.IntegerValue
    except Exception:
        return str(fid)


# ------------------------------------------------------------------ ширины граф (Shift+щелчок)
def widths(vs):
    """[(графа, заголовок, ширина мм)] — видимые графы в порядке ведомости."""
    return [(f, f.ColumnHeading, f.GridColumnWidth * FT_MM) for f in fields(vs) if not f.IsHidden]


# ------------------------------------------------------------------ содержание (по выбору, db 18.10)
FIELD_CALC = ('DisplayType', 'TotalByAssemblyType', 'MultipleValuesDisplayType', 'MultipleValuesCustomText')


def copy_field_calc(doc, src, tgt):
    """Свойства граф «Вычислять итоги / мин / макс», «указание нескольких значений»."""
    notes = []
    for f, g in _pairs(src, tgt):
        for name in FIELD_CALC:
            try:
                v = getattr(f, name)
                if getattr(g, name) != v:
                    setattr(g, name, v)
            except Exception as ex:
                if name == 'DisplayType':
                    notes.append(u'«%s» итоги: %s' % (f.ColumnHeading, ex))
    return notes


def copy_sorting(doc, src, tgt):
    """Сортировка/группирование: поля, заголовки/колонтитулы/пустые строки, общий итог,
    «Для каждого экземпляра»."""
    from Autodesk.Revit.DB import ScheduleSortGroupField
    notes = []
    sd, td = src.Definition, tgt.Definition
    m = dict((_iv_fid(f.FieldId), g.FieldId) for f, g in _pairs(src, tgt))
    try:
        td.ClearSortGroupFields()
        for i in range(sd.GetSortGroupFieldCount()):
            a = sd.GetSortGroupField(i)
            fid = m.get(_iv_fid(a.FieldId))
            if fid is None:
                notes.append(u'сортировка: нет графы в цели')
                continue
            b = ScheduleSortGroupField(fid, a.SortOrder)
            for name in ('ShowHeader', 'ShowFooter', 'ShowFooterTitle', 'ShowFooterCount', 'ShowBlankLine'):
                try:
                    setattr(b, name, getattr(a, name))
                except Exception:
                    pass
            td.AddSortGroupField(b)
    except Exception as ex:
        notes.append(u'сортировка: %s' % ex)
    for name in ('IsItemized', 'ShowGrandTotal', 'ShowGrandTotalTitle', 'ShowGrandTotalCount', 'GrandTotalTitle'):
        try:
            v = getattr(sd, name)
            if getattr(td, name) != v:
                setattr(td, name, v)
        except Exception as ex:
            notes.append(u'%s: %s' % (name, ex))
    return notes


def copy_filters(doc, src, tgt):
    """Фильтры образца. Где у цели уже есть фильтр по той же графе — остаётся её значение
    (у каждой ведомости своя система: «Имя системы = Х1.ККБ2» не станет «Х1.ККБ1»)."""
    notes = []
    sd, td = src.Definition, tgt.Definition
    m = dict((_iv_fid(f.FieldId), g.FieldId) for f, g in _pairs(src, tgt))
    own = {}
    for i in range(td.GetFilterCount()):
        x = td.GetFilter(i)
        own.setdefault(_iv_fid(x.FieldId), []).append(x)
    new = []
    for i in range(sd.GetFilterCount()):
        a = sd.GetFilter(i)
        fid = m.get(_iv_fid(a.FieldId))
        if fid is None:
            notes.append(u'фильтр: нет графы в цели')
            continue
        mine = own.get(_iv_fid(fid)) or []
        if mine:
            b = mine.pop(0)
            if b.FilterType != a.FilterType:
                b.FilterType = a.FilterType
            new.append(b)
        else:
            a.FieldId = fid
            new.append(a)
    try:
        td.ClearFilters()
        for x in new:
            td.AddFilter(x)
    except Exception as ex:
        notes.append(u'фильтры: %s' % ex)
    return notes
