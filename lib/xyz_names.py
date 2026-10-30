# -*- coding: utf-8 -*-
"""
Наименования ОВ2 по формулам (XYZ.extension/lib/xyz_names.py), db 20.10.2026.

Сторонний Dynamic Updater при любом изменении геометрии переписывает ADSK_Наименование
своим шаблоном («Воздуховод из тонколистовой оцинкованной стали 650x400»,
«Отвод прямоугольного воздуховода 45° …», «ø6.35x0.35»), а ADSK_Количество — длиной ×1.
Выключить его можно только из самой надстройки, а его шаблон (склейка параметров) не умеет
условий — толщину стали и разные формы для отвода/перехода/утки не выразить.

NamesUpdater срабатывает ПОСЛЕ стороннего updater-а (приоритет Annotations > MEPAccessoriesFittingsSegmentsWires)
и возвращает:
  - воздуховоды: «Воздуховод из оц. стали 1000х400 мм, б = 0,7 мм», кол-во и площадь ×1,05;
  - фасонка: отвод/тройник/крестовина/переход/утка/врезка/заглушка по формулам;
  - гибкие воздуховоды: «Воздуховод гибкий теплоизолированный ⌀200 мм»;
  - изоляция воздуховодов K-Flex: «Теплоизоляция из вспененного каучука 10 мм» (+ «с покрытием AL CLAD»);
  - медь после сторонней надстройки («ø6.35x0.35») → «Труба медная ⌀6.35x0,35 мм»;
  - db_Раздел и db_Сортировка («код|наименование|марка») — для ведомости «Общая для сметы».
Только элементы с ADSK_Номер здания = «Основной дом».
"""
import re
from System import Guid
from Autodesk.Revit.DB import *

UPDATER_GUID = Guid("6f0f3b52-2b8e-4f3a-9a41-5c2d7e9b1a31")
BUILDING = u'Основной дом'
FT = 304.8
SECT = {1: u'1. Оборудование', 2: u'2. Воздуховоды и соединительные элементы', 3: u'3. Воздухораспределители',
        4: u'4. Фреонопроводы и соединительные элементы', 5: u'5. Конденсатопроводы (дренаж)',
        6: u'6. Опорные конструкции и крепёж'}
CODE2S = {'01': 1, '02': 1, '10': 3, '11': 2, '12': 2, '13': 2, '14': 2, '15': 2, '03': 4, '21': 4, '22': 4,
          '04': 5, '20': 5, '23': 5, '24': 5, '05': 6, '30': 6}
BICS = [BuiltInCategory.OST_DuctCurves, BuiltInCategory.OST_DuctFitting, BuiltInCategory.OST_FlexDuctCurves,
        BuiltInCategory.OST_DuctInsulations, BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_DuctAccessory,
        BuiltInCategory.OST_DuctTerminal]


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _cat(e):
    c = e.Category
    return _iv(c.Id) if c is not None else 0


def P(doc, e, n):
    p = e.LookupParameter(n)
    if p is None:
        t = doc.GetElement(e.GetTypeId())
        if t is not None:
            p = t.LookupParameter(n)
    return p


def S(doc, e, n):
    p = P(doc, e, n)
    if p is None or not p.HasValue:
        return None
    if p.StorageType == StorageType.String:
        return p.AsString()
    if p.StorageType == StorageType.Double:
        return p.AsDouble()
    if p.StorageType == StorageType.Integer:
        return p.AsInteger()
    return p.AsValueString()


def setp(e, n, v):
    p = e.LookupParameter(n)
    if p is None or p.IsReadOnly:
        return False
    if p.StorageType == StorageType.String:
        if (p.AsString() or u'') == v:
            return False
    elif p.StorageType == StorageType.Double:
        if abs(p.AsDouble() - v) < 1e-6:
            return False
    else:
        return False
    try:
        p.Set(v)
        return True
    except Exception:
        return False


def fam(doc, e):
    t = doc.GetElement(e.GetTypeId())
    try:
        return t.FamilyName
    except Exception:
        return u''


def mm(v):
    return int(round(v * FT))


def thick(roundf, d, w, h):
    if roundf:
        for lim, t in ((200, u'0,5'), (450, u'0,6'), (800, u'0,7'), (1250, u'1,0'), (1600, u'1,2'), (2000, u'1,4')):
            if d <= lim:
                return t
    else:
        m = max(w, h)
        for lim, t in ((250, u'0,5'), (1000, u'0,7'), (2000, u'0,9')):
            if m <= lim:
                return t
    return None


def fmtang(a):
    a = int(5 * round(a / 5.0))
    return 5 if a == 0 else a


def half(s):
    return s[:len(s) // 2] if s else s


def duct_name(doc, e):
    d = e.LookupParameter(u'Диаметр')
    if d is not None and d.HasValue and d.AsDouble() > 0:
        D = mm(d.AsDouble())
        return u'Воздуховод из оц. стали ⌀%d мм, б = %s мм' % (D, thick(True, D, 0, 0))
    w = mm(e.LookupParameter(u'Ширина').AsDouble())
    h = mm(e.LookupParameter(u'Высота').AsDouble())
    return u'Воздуховод из оц. стали %dх%d мм, б = %s мм' % (max(w, h), min(w, h), thick(False, 0, w, h))


def fit_name(doc, e, nm):
    model = S(doc, e, u'Модель') or u''
    size = S(doc, e, u'Размер') or u''
    f = fam(doc, e)
    if not size:
        return None
    if model == u'Отвод':
        ang = S(doc, e, 'ADSK_Размер_УголПоворота')
        if ang is None:
            ang = S(doc, e, u'Угол')
        if ang is None:
            return None
        a = fmtang(ang * 180 / 3.14159265358979)
        if u'руг' in f:
            sz = half(size)
        else:
            W, H = S(doc, e, 'ADSK_Размер_Ширина'), S(doc, e, 'ADSK_Размер_Высота')
            if not W or not H:
                return None
            sz = u'%dх%d' % (mm(W), mm(H))
        return u'Отвод %s° из оц. стали %s мм' % (a, sz)
    if model == u'Тройник':
        return None if u'штанообраз' in nm else u'Тройник из оц. стали %s мм' % size
    if model == u'Крестовина':
        return u'Крестовина из оц. стали %s мм' % size
    if model == u'Переход':
        L = S(doc, e, 'ADSK_Размер_Длина')
        tp = S(doc, e, u'db_Тип перехода')
        if not L:
            return None
        if not tp:
            return u'Переход из оц. стали %s мм, l=%d мм' % (size, mm(L))
        return u'Переход из оц. стали %s мм, l=%d мм, тип № %s' % (size, mm(L), tp)
    if model == u'Утка':
        L = S(doc, e, 'ADSK_Размер_Длина')
        H = S(doc, e, 'ADSK_Размер_Высота')
        if not L or H is None:
            return None
        return u'Утка из оц. стали %s мм, h=%d мм, l=%d мм' % (size, abs(mm(H)), mm(L))
    if model == u'Врезка':
        return u'Врезка из оц. стали %s мм' % half(size)
    if model == u'Заглушка':
        return u'Заглушка из оц. стали %s мм' % (size.replace('x', u'х') if u'⌀' not in size else size)
    return None


def flex_name(doc, e):
    d = e.LookupParameter(u'Диаметр')
    if d is None or not d.HasValue or d.AsDouble() <= 0:
        return None
    return u'Воздуховод гибкий теплоизолированный ⌀%d мм' % mm(d.AsDouble())


def ins_name(doc, e):
    t = doc.GetElement(e.GetTypeId())
    tn = Element.Name.GetValue(t) if t is not None else u''
    if not tn.startswith(u'db_K-Flex'):
        return None
    th = mm(e.Thickness)
    nm = u'Теплоизоляция из вспененного каучука %d мм' % th
    if u'AL CLAD' in tn:
        nm += u' с покрытием AL CLAD'
    return nm


def pins_name(doc, e, nm):
    t = doc.GetElement(e.GetTypeId())
    tn = Element.Name.GetValue(t) if t is not None else u''
    if u'AL CLAD' not in tn:
        return None
    m = re.search(u'для\\s+(.+)$', nm or u'')
    tail = m.group(1).split(u'-')[0] if m else None
    if tail is None:
        h = _host(doc, e)
        p = h.get_Parameter(BuiltInParameter.RBS_PIPE_OUTER_DIAMETER) if h is not None else None
        if p is None:
            return None
        tail = u'⌀%s' % (u'%.2f' % (p.AsDouble() * FT)).rstrip('0').rstrip('.')
    return u'Трубки теплоизоляционные из вспененного каучука с покрытием AL CLAD, b=%d мм для %s' % (mm(e.Thickness), tail)


COPPER = re.compile(u'^[øØ⌀](\\d+[.,]?\\d*)\\s*[xх]\\s*(\\d+[.,]?\\d*)$')


def copper_name(nm):
    m = COPPER.match(nm or u'')
    if not m:
        return None
    t = m.group(2).replace('.', ',')
    if t in (u'0', u'0,0'):
        return None
    return u'Труба медная ⌀%sx%s мм' % (m.group(1).replace(',', '.'), t)


DEF_CODE = {int(BuiltInCategory.OST_DuctCurves): (u'12', u'м'), int(BuiltInCategory.OST_DuctFitting): (u'14', u'шт.'),
            int(BuiltInCategory.OST_FlexDuctCurves): (u'13', u'м'), int(BuiltInCategory.OST_DuctInsulations): (u'15', u'м²'),
            int(BuiltInCategory.OST_DuctAccessory): (u'11', u'шт.')}
# арматура-оборудование (нагреватели, охладители) — в раздел «1. Оборудование» (db 20.10)
ACC_EQUIP = re.compile(u'^(Нагреватель|Охладитель|Воздухонагреватель|Воздухоохладитель|Клапан противопожарный|Парокамера)', re.U)
INS_RESERVE = 1.1    # запас изоляции 10 % (db 20.10)
DRAIN_RESERVE = 1.2   # запас ППР-трубы дренажа (db 20.10: «с запасом 20 %, потом поправлю»)


def _neighbours(e):
    cm = None
    try:
        cm = e.ConnectorManager
    except Exception:
        pass
    if cm is None:
        try:
            cm = e.MEPModel.ConnectorManager
        except Exception:
            cm = None
    res = []
    if cm is None:
        return res
    for c in cm.Connectors:
        try:
            for r in c.AllRefs:
                o = r.Owner
                if o is not None and _iv(o.Id) != _iv(e.Id) and not isinstance(o, MEPSystem):
                    res.append(o)
        except Exception:
            pass
    return res


def _adopt(doc, e, c):
    """Новый элемент (Revit сам вставил переход/отвод): здание от соседа, код и единица по категории."""
    n = 0
    if not (S(doc, e, u'ADSK_Номер здания') or u''):
        for o in _neighbours(e):
            if (S(doc, o, u'ADSK_Номер здания') or u'') == BUILDING:
                if setp(e, u'ADSK_Номер здания', BUILDING):
                    n += 1
                break
    if (S(doc, e, u'ADSK_Номер здания') or u'') == BUILDING and c in DEF_CODE and not (S(doc, e, u'db_Категория') or u''):
        if setp(e, u'db_Категория', DEF_CODE[c][0]):
            n += 1
        if not (S(doc, e, u'db_Единица измерения') or u'') and setp(e, u'db_Единица измерения', DEF_CODE[c][1]):
            n += 1
    return n


def ins_qty(doc, e, h):
    """Количество изоляции = площадь/длина того, что она покрывает, × INS_RESERVE (запас 10 %, db 20.10):
    воздуховод — его поверхность, фасонка/арматура — их ADSK_Размер_Площадь, трубки — длина;
    дренаж ППР (код 23) — ×1,2, как сама труба (запас трассы перекрывает 10 %)."""
    if h is None:
        return None
    hc = _cat(h)
    if _cat(e) == _iv(ElementId(BuiltInCategory.OST_PipeInsulations)):
        L = e.get_Parameter(BuiltInParameter.CURVE_ELEM_LENGTH)
        if L is None or not L.HasValue:
            return None
        k = DRAIN_RESERVE if (hc == _iv(ElementId(BuiltInCategory.OST_PipeCurves)) and (S(doc, h, u'db_Категория') or u'') == u'23') else INS_RESERVE
        return round(L.AsDouble() * FT / 1000.0 * k, 2)
    if hc == _iv(ElementId(BuiltInCategory.OST_DuctCurves)):
        return round(h.get_Parameter(BuiltInParameter.RBS_CURVE_SURFACE_AREA).AsDouble() * 0.09290304 * INS_RESERVE, 2)
    p = h.LookupParameter(u'ADSK_Размер_Площадь')
    if p is not None and p.HasValue and p.StorageType == StorageType.Double and p.AsDouble() > 0:
        return round(p.AsDouble() * 0.09290304 * INS_RESERVE, 2)
    a = e.get_Parameter(BuiltInParameter.RBS_CURVE_SURFACE_AREA)
    return round(a.AsDouble() * 0.09290304 * INS_RESERVE, 2) if a is not None and a.HasValue else None


def _host(doc, e):
    try:
        return doc.GetElement(e.HostElementId)
    except Exception:
        return None


def process(doc, ids):
    """Привести наименования/ключи. ids — ElementId. Возвращает число изменённых значений."""
    n = 0
    wst = doc.GetWorksetTable()
    for i in ids:
        e = doc.GetElement(i)
        if e is None or not e.IsValidObject:
            continue
        c = _cat(e)
        ispins = c == _iv(ElementId(BuiltInCategory.OST_PipeInsulations))
        isins = c == _iv(ElementId(BuiltInCategory.OST_DuctInsulations)) or ispins
        src = _host(doc, e) if isins else e
        if src is None:
            continue
        if not isins:
            try:
                n += _adopt(doc, e, c)
            except Exception:
                pass
        if (S(doc, src, u'ADSK_Номер здания') or u'') != BUILDING:
            continue
        nm = S(doc, e, u'ADSK_Наименование') or u''
        if u'Не учитывать' in nm:
            continue
        new = None
        try:
            if c == _iv(ElementId(BuiltInCategory.OST_DuctCurves)):
                new = duct_name(doc, e)
                L = e.get_Parameter(BuiltInParameter.CURVE_ELEM_LENGTH).AsDouble() * FT / 1000.0
                A = e.get_Parameter(BuiltInParameter.RBS_CURVE_SURFACE_AREA).AsDouble() * 0.09290304
                if setp(e, u'ADSK_Количество', round(L * 1.05, 2)):
                    n += 1
                p = e.LookupParameter(u'ADSK_Размер_Площадь')
                if p is not None and p.StorageType == StorageType.Double and setp(e, u'ADSK_Размер_Площадь', round(A * 1.05, 2) / 0.09290304):
                    n += 1
            elif c == _iv(ElementId(BuiltInCategory.OST_DuctFitting)):
                if (S(doc, e, u'Модель') or u'') != u'Соединение':
                    new = fit_name(doc, e, nm)
            elif c == _iv(ElementId(BuiltInCategory.OST_FlexDuctCurves)):
                new = flex_name(doc, e)
            elif ispins:
                q = ins_qty(doc, e, src)
                if q is not None and setp(e, u'ADSK_Количество', q):
                    n += 1
                new = pins_name(doc, e, nm)
            elif isins:
                q = ins_qty(doc, e, src)
                if q is not None and setp(e, u'ADSK_Количество', q):
                    n += 1
                new = ins_name(doc, e)
                if new:
                    if setp(e, u'ADSK_Номер здания', BUILDING):
                        n += 1
                    if not (S(doc, e, u'db_Категория') or u'') and setp(e, u'db_Категория', u'15'):
                        n += 1
                    if setp(e, u'db_Единица измерения', u'м²'):
                        n += 1
            elif c == _iv(ElementId(BuiltInCategory.OST_PipeCurves)):
                new = copper_name(nm)
                if (S(doc, e, u'db_Категория') or u'') == u'23' and nm.startswith(u'Труба полипропиленовая'):
                    L = e.get_Parameter(BuiltInParameter.CURVE_ELEM_LENGTH).AsDouble() * FT / 1000.0
                    if setp(e, u'ADSK_Количество', round(L * DRAIN_RESERVE, 2)):
                        n += 1
            elif c == _iv(ElementId(BuiltInCategory.OST_DuctAccessory)):
                if ACC_EQUIP.match(nm) and setp(e, u'db_Категория', u'02'):
                    n += 1
        except Exception:
            new = None
        if new and new != nm and setp(e, u'ADSK_Наименование', new):
            n += 1
            nm = new
        # раздел и ключ сортировки
        code = S(doc, e, u'db_Категория') or u''
        if not code:
            continue
        if code == '25':
            h = _host(doc, e)
            hc = (S(doc, h, u'db_Категория') or u'') if h is not None else u''
            try:
                hws = wst.GetWorkset(h.WorksetId).Name if h is not None else u''
            except Exception:
                hws = u''
            s = 5 if (hc in ('23', '24') or hws == u'db_Дренаж') else 4
        else:
            s = CODE2S.get(code)
        if s is None:
            continue
        if setp(e, u'db_Раздел', SECT[s]):
            n += 1
        if code != '10':
            nm = S(doc, e, u'ADSK_Наименование') or u''
            key = eq_key(doc, e, code, nm) if s == 1 else code + u'|' + nm + u'|' + (S(doc, e, u'ADSK_Марка') or u'')
            if setp(e, u'db_Сортировка', key):
                n += 1
    return n


INS_SKIP = re.compile(u'^(Шумоглушитель|Глушитель|Охладитель|Нагреватель|Гибкая вставка|Фильтр|Парокамера)', re.U)
INS_NEED = re.compile(u'^(Дроссель|Клапан|Пленум)', re.U)


def _ins_like(doc, e, depth=4):
    """Изоляция ближайшего соседа по сети: (тип, толщина) или None."""
    seen = set([_iv(e.Id)])
    front = [e]
    for _ in range(depth):
        nxt = []
        for x in front:
            for o in _neighbours(x):
                if _iv(o.Id) in seen:
                    continue
                seen.add(_iv(o.Id))
                for i in InsulationLiningBase.GetInsulationIds(doc, o.Id):
                    ins = doc.GetElement(i)
                    if ins is not None:
                        return ins.GetTypeId(), ins.Thickness
                nxt.append(o)
        front = nxt
    return None


def insulate(doc, default_type_name=u'db_K-Flex ST AD', default_mm=10):
    """Изоляция воздуховодов, фасонки, дросселей/клапанов «Основного дома», у которых её нет:
    тип и толщина — как у ближайшего изолированного соседа, иначе ST AD 10 мм.
    С глушителей, охладителей, нагревателей, гибких вставок и гибких воздуховодов — снять.
    Возвращает (добавлено, снято, ids новых)."""
    from Autodesk.Revit.DB.Mechanical import DuctInsulation
    dflt = None
    for t in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_DuctInsulations).WhereElementIsElementType():
        if Element.Name.GetValue(t) == default_type_name:
            dflt = t
    add, rem, new = 0, 0, []
    for bic in (BuiltInCategory.OST_DuctCurves, BuiltInCategory.OST_DuctFitting,
                BuiltInCategory.OST_DuctAccessory, BuiltInCategory.OST_FlexDuctCurves):
        for e in list(FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()):
            if (S(doc, e, u'ADSK_Номер здания') or u'') != BUILDING:
                continue
            nm = S(doc, e, u'ADSK_Наименование') or u''
            if u'Не учитывать' in nm:
                continue
            ex = list(InsulationLiningBase.GetInsulationIds(doc, e.Id))
            off = bic == BuiltInCategory.OST_FlexDuctCurves or (bic == BuiltInCategory.OST_DuctAccessory and INS_SKIP.match(nm))
            if off:
                for i in ex:
                    doc.Delete(i)
                    rem += 1
                continue
            if ex:
                continue
            if bic == BuiltInCategory.OST_DuctAccessory and not INS_NEED.match(nm):
                continue
            if bic == BuiltInCategory.OST_DuctFitting and (S(doc, e, u'Модель') or u'') == u'Соединение':
                continue
            like = _ins_like(doc, e)
            if like is None:
                if dflt is None:
                    continue
                like = (dflt.Id, default_mm / FT)
            try:
                ins = DuctInsulation.Create(doc, e.Id, like[0], like[1])
                new.append(ins.Id)
                add += 1
            except Exception:
                pass
    return add, rem, new


# порядок раздела «1. Оборудование» (db 20.10): вентустановки по позиции ХОВС, наружные блоки
# ККБ1, ККБ2, VRF1, VRF2, прочие наружные, внутренние блоки, ЭРВ (LG подряд), всё остальное
OUT_ORDER = [u'ККБ1', u'ККБ2', u'VRF1', u'VRF2', u'HYDROKIT']


def _sys_rank(dev):
    for i, x in enumerate(OUT_ORDER):
        if x in dev:
            return i
    return len(OUT_ORDER)


def eq_key(doc, e, code, nm):
    mk = S(doc, e, u'ADSK_Марка') or u''
    dev = S(doc, e, u'ADSK_Имя устройства') or u''
    pos = S(doc, e, u'db_Позиция')
    pos = u'' if pos in (None, u'') else u'%s' % pos
    mf = (S(doc, e, u'ADSK_Завод-изготовитель') or u'').upper()
    tail = u'|' + nm + u'|' + mk
    if code == u'01' and pos:
        try:
            return u'1|%07.2f|%s' % (float(pos.replace(u',', u'.')), dev) + tail
        except Exception:
            pass
    if u'LG' in mf:
        if nm.startswith(u'Наружный блок'):
            i = _sys_rank(dev)
            return u'2|%d|%s' % (i, dev) + tail
        if nm.startswith(u'Внутренний блок') or nm.startswith(u'Гидромодуль'):
            return u'3|%d|%s' % (_sys_rank(dev), dev) + tail
        return u'4' + tail
    return u'5|' + code + tail


EQ_BICS = [BuiltInCategory.OST_MechanicalEquipment, BuiltInCategory.OST_DuctAccessory, BuiltInCategory.OST_PipeAccessory,
           BuiltInCategory.OST_PlumbingFixtures, BuiltInCategory.OST_ElectricalEquipment]


def sort_equipment(doc):
    """Ключи сортировки всего раздела «1. Оборудование». Возвращает число изменений."""
    n = 0
    for b in EQ_BICS:
        for e in FilteredElementCollector(doc).OfCategory(b).WhereElementIsNotElementType():
            if (S(doc, e, u'ADSK_Номер здания') or u'') != BUILDING or (S(doc, e, u'db_Раздел') or u'') != SECT[1]:
                continue
            nm = S(doc, e, u'ADSK_Наименование') or u''
            if setp(e, u'db_Сортировка', eq_key(doc, e, S(doc, e, u'db_Категория') or u'', nm)):
                n += 1
    return n


def all_ids(doc):
    res = []
    for b in BICS:
        res.extend(FilteredElementCollector(doc).OfCategory(b).WhereElementIsNotElementType().ToElementIds())
    return res


DEBUG = False


def _dbgw(t):
    import io, os
    with io.open(os.path.join(os.environ['TEMP'], 'zb_names_upd.txt'), 'a', encoding='utf-8') as fh:
        fh.write(t + u'\n')


def _dbg(doc, ids, tag):
    for i in ids[:5]:
        e = doc.GetElement(i)
        if e is None:
            continue
        _dbgw(u'%s %d: %s | %s' % (tag, _iv(i), S(doc, e, u'ADSK_Наименование'), S(doc, e, u'db_Сортировка')))


class NamesUpdater(IUpdater):
    def __init__(self, addin_id):
        self._uid = UpdaterId(addin_id, UPDATER_GUID)

    def GetUpdaterId(self):
        return self._uid

    def GetUpdaterName(self):
        return u"XYZ: наименования ОВ2"

    def GetAdditionalInformation(self):
        return u"Возвращает ADSK_Наименование/кол-во по формулам после других надстроек"

    def GetChangePriority(self):
        return ChangePriority.Annotations

    def Execute(self, data):
        try:
            ids = list(data.GetModifiedElementIds()) + list(data.GetAddedElementIds())
            if ids:
                doc = data.GetDocument()
                if DEBUG:
                    _dbg(doc, ids, u'до')
                k = process(doc, ids)
                if DEBUG:
                    _dbg(doc, ids, u'после (%d)' % k)
        except Exception:
            if DEBUG:
                import traceback
                _dbgw(traceback.format_exc())


def _cat_list(bics):
    from System.Collections.Generic import List as _L
    lst = _L[BuiltInCategory]()
    for b in bics:
        lst.Add(b)
    return lst


def register(addin_id):
    up = NamesUpdater(addin_id)
    if UpdaterRegistry.IsUpdaterRegistered(up.GetUpdaterId()):
        UpdaterRegistry.UnregisterUpdater(up.GetUpdaterId())
    UpdaterRegistry.RegisterUpdater(up, True)
    flt = ElementMulticategoryFilter(_cat_list([
        BuiltInCategory.OST_DuctCurves, BuiltInCategory.OST_DuctFitting, BuiltInCategory.OST_FlexDuctCurves,
        BuiltInCategory.OST_DuctInsulations, BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_PipeInsulations]))
    UpdaterRegistry.AddTrigger(up.GetUpdaterId(), flt, Element.GetChangeTypeGeometry())
    UpdaterRegistry.AddTrigger(up.GetUpdaterId(), flt, Element.GetChangeTypeElementAddition())
    return up
