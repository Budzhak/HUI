# -*- coding: utf-8 -*-
"""Количество с запасом — логика кнопки «Захуярить → Количество» (db 21.10.2026).

Ничего не пишет само: compute() только считает и возвращает пробу «было → станет»,
apply() пишет то, что показано в пробе. Правила — таблица, которую правит инженер;
хранятся в файле рядом с моделью (<имя файла модели>_ХУЙ_Количество.json).

Правило: {'on': True, 'cat': 'OST_DuctCurves',
          'conds': [{'host': False, 'param': u'db_Категория', 'op': u'=', 'value': u'23'}],
          'measure': 'length', 'target': u'ADSK_Количество', 'reserve': 5.0}
Правила проверяются сверху вниз; для каждого параметра-цели (ADSK_Количество,
ADSK_Размер_Площадь) элемент берёт ПЕРВОЕ подошедшее правило.

Здание — по областям видимости (Scope Box): имя рамки без хвоста « (N)» = ADSK_Номер здания.
Элемент в рамке, если центр его габарита внутри рамки (в плане и по высоте).
"""
import io
import os
import re
import json
import math

from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, ElementId, FilteredElementCollector,
                               StorageType, Options, Line, LocationPoint, LocationCurve, XYZ)

FT = 0.3048
FT2 = 0.09290304
BUILDING_PARAM = u'ADSK_Номер здания'
NAME_PARAM = u'ADSK_Наименование'

CATS = [
    (u'Воздуховоды', 'OST_DuctCurves'),
    (u'Гибкие воздуховоды', 'OST_FlexDuctCurves'),
    (u'Соединительные детали воздуховодов', 'OST_DuctFitting'),
    (u'Арматура воздуховодов', 'OST_DuctAccessory'),
    (u'Воздухораспределители', 'OST_DuctTerminal'),
    (u'Изоляция воздуховодов', 'OST_DuctInsulations'),
    (u'Трубы', 'OST_PipeCurves'),
    (u'Гибкие трубы', 'OST_FlexPipeCurves'),
    (u'Соединительные детали трубопроводов', 'OST_PipeFitting'),
    (u'Арматура трубопроводов', 'OST_PipeAccessory'),
    (u'Изоляция труб', 'OST_PipeInsulations'),
    (u'Оборудование', 'OST_MechanicalEquipment'),
]
CAT_NAME = dict((k, n) for n, k in CATS)

MEASURES = [
    ('length', u'длина, м'),
    ('surface', u'площадь поверхности, м²'),
    ('adsk_area', u'ADSK_Размер_Площадь, м²'),
    ('host_surface', u'поверхность основы, м²'),
    ('host_surface_res', u'поверхность основы + её запас, м²'),
    ('host_length_res', u'длина основы + её запас, м'),
    ('count', u'штуки'),
]
MEASURE_NAME = dict(MEASURES)
MEASURE_UNIT = {'length': u'м', 'surface': u'м²', 'adsk_area': u'м²', 'host_surface': u'м²', 'host_surface_res': u'м²',
                'host_length_res': u'м', 'count': u'шт.'}
# db_Количество_факт — факт без запаса (db 21.10: таблицы фреонопроводов на схемах берут количество отсюда,
# а в ADSK_Количество — с запасом для итоговой спецификации)
FACT_PARAM = u'db_Количество_факт'
TARGETS = [u'ADSK_Количество', u'ADSK_Размер_Площадь', FACT_PARAM]
OPS = [u'=', u'≠', u'содержит', u'не содержит', u'начинается с']

DEFAULT_RULES = [
    {'on': True, 'cat': 'OST_DuctCurves', 'conds': [], 'measure': 'length', 'target': u'ADSK_Количество', 'reserve': 5.0},
    {'on': True, 'cat': 'OST_DuctCurves', 'conds': [], 'measure': 'surface', 'target': u'ADSK_Размер_Площадь', 'reserve': 5.0},
    {'on': True, 'cat': 'OST_FlexDuctCurves', 'conds': [], 'measure': 'length', 'target': u'ADSK_Количество', 'reserve': 10.0},
    {'on': True, 'cat': 'OST_DuctInsulations', 'conds': [], 'measure': 'host_surface_res', 'target': u'ADSK_Количество', 'reserve': 10.0},
    {'on': True, 'cat': 'OST_PipeInsulations', 'conds': [], 'measure': 'host_length_res', 'target': u'ADSK_Количество', 'reserve': 10.0},
    {'on': True, 'cat': 'OST_PipeCurves',
     'conds': [{'host': False, 'param': u'db_Категория', 'op': u'=', 'value': u'23'}],
     'measure': 'length', 'target': u'ADSK_Количество', 'reserve': 20.0},
    {'on': True, 'cat': 'OST_PipeCurves', 'conds': [], 'measure': 'length', 'target': u'ADSK_Количество', 'reserve': 0.0},
]


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def bic(name):
    return getattr(BuiltInCategory, name)


def cat_id(name):
    return _iv(ElementId(bic(name)))


# ------------------------------------------------------------------ файл правил
def rules_path(doc):
    p = doc.PathName or u''
    if p:
        d, f = os.path.split(p)
        return os.path.join(d, os.path.splitext(f)[0] + u'_ХУЙ_Количество.json')
    base = os.path.join(os.environ.get('APPDATA', u''), u'pyRevit')
    return os.path.join(base, (doc.Title or u'модель') + u'_ХУЙ_Количество.json')


def model_key(doc):
    """Ключ модели для запоминания файла правил: путь хранилища (для совместной работы) или файла."""
    try:
        if doc.IsWorkshared:
            from Autodesk.Revit.DB import ModelPathUtils
            return ModelPathUtils.ConvertModelPathToUserVisiblePath(doc.GetWorksharingCentralModelPath())
    except Exception:
        pass
    return doc.PathName or doc.Title


def current_path(doc):
    """Файл правил, выбранный для этой модели в прошлый раз (%APPDATA%\\pyRevit\\hui_settings.json,
    ключ qty_rules), иначе — по умолчанию рядом с моделью."""
    try:
        import hui_settings
        p = (hui_settings.get('qty_rules') or {}).get(model_key(doc))
        if p:
            return p
    except Exception:
        pass
    return rules_path(doc)


def remember_path(doc, path):
    try:
        import hui_settings
        m = dict(hui_settings.get('qty_rules') or {})
        m[model_key(doc)] = path
        hui_settings.set('qty_rules', m)
    except Exception:
        pass


def load_settings(doc, path=None):
    """(settings, path, из_файла)."""
    path = path or current_path(doc)
    st = {'rules': [dict(r, conds=[dict(c) for c in r['conds']]) for r in DEFAULT_RULES],
          'skip_excl': True, 'write_building': True, 'frames': None}
    if os.path.exists(path):
        try:
            with io.open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            st.update(data)
            return st, path, True
        except Exception:
            pass
    return st, path, False


def save_settings(doc, st, path=None):
    path = path or current_path(doc)
    data = {'rules': st['rules'], 'skip_excl': bool(st.get('skip_excl', True)),
            'write_building': bool(st.get('write_building', True)), 'frames': st.get('frames'),
            'ws_off': st.get('ws_off') or [], 'scope': st.get('scope') or 'model'}
    txt = json.dumps(data, ensure_ascii=False, indent=1)
    if not isinstance(txt, type(u'')):
        txt = txt.decode('utf-8')
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(txt)
    remember_path(doc, path)
    return path


# ------------------------------------------------------------------ рамки (области видимости)
def building_of(frame_name):
    return re.sub(u'\\s*\\(\\d+\\)\\s*$', u'', frame_name or u'').strip()


def _hull(pts):
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def frames(doc):
    """Области видимости: [{'id', 'name', 'building', 'poly', 'zmin', 'zmax'}]."""
    out = []
    opt = Options()
    for sb in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_VolumeOfInterest).WhereElementIsNotElementType():
        pts = []
        try:
            for g in sb.get_Geometry(opt):
                if isinstance(g, Line):
                    pts.append(g.GetEndPoint(0))
                    pts.append(g.GetEndPoint(1))
        except Exception:
            pass
        if pts:
            zmin = min(p.Z for p in pts)
            zmax = max(p.Z for p in pts)
            poly = _hull([(round(p.X, 6), round(p.Y, 6)) for p in pts])
        else:
            bb = sb.get_BoundingBox(None)
            if bb is None:
                continue
            zmin, zmax = bb.Min.Z, bb.Max.Z
            poly = [(bb.Min.X, bb.Min.Y), (bb.Max.X, bb.Min.Y), (bb.Max.X, bb.Max.Y), (bb.Min.X, bb.Max.Y)]
        out.append({'id': _iv(sb.Id), 'name': sb.Name, 'building': building_of(sb.Name),
                    'poly': poly, 'zmin': zmin, 'zmax': zmax})
    out.sort(key=lambda f: f['name'])
    return out


def _in_poly(x, y, poly):
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _center(el):
    """Точка элемента для проверки рамки: центр габарита; если габарита нет (у части гибких
    воздуховодов get_BoundingBox = None, промах 21.10) — середина линии / точка вставки / центр основы."""
    bb = el.get_BoundingBox(None)
    if bb is not None:
        return (bb.Min + bb.Max) * 0.5
    loc = el.Location
    try:
        if isinstance(loc, LocationCurve):
            return loc.Curve.Evaluate(0.5, True)
        if isinstance(loc, LocationPoint):
            return loc.Point
    except Exception:
        pass
    try:
        pts = list(el.Points)
        if pts:
            return pts[len(pts) // 2]
    except Exception:
        pass
    try:
        h = el.Document.GetElement(el.HostElementId)
        if h is not None:
            return _center(h)
    except Exception:
        pass
    return None


def frame_of(el, frs):
    c = _center(el)
    if c is None:
        return None
    for f in frs:
        if f['zmin'] - 1e-6 <= c.Z <= f['zmax'] + 1e-6 and _in_poly(c.X, c.Y, f['poly']):
            return f
    return None


# ------------------------------------------------------------------ параметры
def host_of(doc, el):
    try:
        hid = el.HostElementId
    except Exception:
        return None
    try:
        return doc.GetElement(hid)
    except Exception:
        return None


def find_param(doc, el, name):
    if el is None:
        return None
    p = el.LookupParameter(name)
    if p is None:
        try:
            t = doc.GetElement(el.GetTypeId())
            if t is not None:
                p = t.LookupParameter(name)
        except Exception:
            p = None
    return p


def pstr(doc, el, name):
    if el is None:
        return u''
    if name == u'Семейство':
        try:
            return doc.GetElement(el.GetTypeId()).FamilyName or u''
        except Exception:
            return u''
    if name == u'Тип':
        try:
            t = doc.GetElement(el.GetTypeId())
            p = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            return (p.AsString() or u'') if p is not None else u''
        except Exception:
            return u''
    p = find_param(doc, el, name)
    if p is None or not p.HasValue:
        return u''
    if p.StorageType == StorageType.String:
        return p.AsString() or u''
    try:
        return p.AsValueString() or u''
    except Exception:
        return u''


def param_names(doc, els, limit=400):
    """Имена параметров у элементов (и их типов) — для выпадающего списка условий."""
    names = set([u'Семейство', u'Тип'])
    for el in els[:limit]:
        if el is None:
            continue
        for src in (el, doc.GetElement(el.GetTypeId())):
            if src is None:
                continue
            try:
                for p in src.Parameters:
                    n = p.Definition.Name
                    if n:
                        names.add(n)
            except Exception:
                pass
    return sorted(names)


def value_counts(doc, els, name):
    d = {}
    for el in els:
        v = pstr(doc, el, name)
        d[v] = d.get(v, 0) + 1
    return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))


def cond_ok(doc, el, host, c):
    src = host if c.get('host') else el
    v = pstr(doc, src, c.get('param') or u'')
    w = (c.get('value') or u'')
    op = c.get('op') or u'='
    if op == u'=':
        return v == w
    if op == u'≠':
        return v != w
    if op == u'содержит':
        return w in v
    if op == u'не содержит':
        return w not in v
    if op == u'начинается с':
        return v.startswith(w)
    return False


# ------------------------------------------------------------------ замеры
def _conn_length(el):
    """Длина фасонной детали по оси, м: у отвода (2 соединителя, есть поворот) — длина дуги
    (как считал BIM2B: 90° ⌀6.35 → 0,047 м), у остальных — сумма расстояний от точки вставки до соединителей."""
    try:
        loc = el.Location
        if isinstance(loc, LocationPoint):
            o = loc.Point
        else:
            return None
        cs = list(el.MEPModel.ConnectorManager.Connectors)
        if not cs:
            return None
        if len(cs) == 2:
            d1 = cs[0].Origin.DistanceTo(o)
            d2 = cs[1].Origin.DistanceTo(o)
            th = math.pi - cs[0].CoordinateSystem.BasisZ.AngleTo(cs[1].CoordinateSystem.BasisZ)
            if th > 1e-3 and (d1 + d2) > 1e-9:
                return (d1 + d2) / 2.0 * th / math.tan(th / 2.0) * FT
        return sum(c.Origin.DistanceTo(o) for c in cs) * FT
    except Exception:
        return None


def _len(el):
    p = el.get_Parameter(BuiltInParameter.CURVE_ELEM_LENGTH)
    if p is not None and p.HasValue and p.AsDouble() > 0:
        return p.AsDouble() * FT
    return None


def _surf(el):
    p = el.get_Parameter(BuiltInParameter.RBS_CURVE_SURFACE_AREA)
    if p is not None and p.HasValue and p.AsDouble() > 0:
        return p.AsDouble() * FT2
    return None


def _adsk_area(doc, el):
    p = find_param(doc, el, u'ADSK_Размер_Площадь')
    if p is not None and p.HasValue and p.StorageType == StorageType.Double and p.AsDouble() > 0:
        return p.AsDouble() * FT2
    return None


def measure(doc, el, host, m):
    """(значение, причина_если_нет)."""
    if m == 'count':
        return 1.0, None
    if m == 'length':
        v = _len(el)
        if v is None and host is not None:
            v = _len(host)
            if v is None:
                v = _conn_length(host)
        if v is None:
            v = _conn_length(el)
        return v, (None if v else u'нет длины')
    if m == 'surface':
        v = _surf(el)
        return v, (None if v else u'нет площади поверхности')
    if m == 'adsk_area':
        v = _adsk_area(doc, el)
        return v, (None if v else u'пустой ADSK_Размер_Площадь')
    if m == 'host_surface':
        if host is None:
            return None, u'нет основы'
        v = _surf(host)
        if v is None:
            v = _adsk_area(doc, host)
        return v, (None if v else u'у основы нет площади (ADSK_Размер_Площадь пуст)')
    return None, u'неизвестный способ'


def _rule_reserve(doc, el, rules, kind):
    hc = _iv(el.Category.Id)
    hh = None
    for n, r in enumerate(rules):
        if not r.get('on') or cat_id(r['cat']) != hc:
            continue
        if kind == 'len' and r['measure'] != 'length':
            continue
        if kind == 'area' and r['measure'] not in ('surface', 'adsk_area'):
            continue
        if r['conds'] and hh is None:
            hh = host_of(doc, el)
        if all(cond_ok(doc, el, hh, c) for c in r['conds']):
            return (float(r.get('reserve') or 0), n + 1, u'')
    return None


def host_reserve(doc, host, rules, kind, cache):
    """Запас основы по её правилу: kind 'len' — правило с замером «длина», 'area' — «площадь».
    Если основу (отвод, тройник, переход) не ловит ни одно правило — берётся запас трубы или
    воздуховода, присоединённого к ней. (запас %, № правила, пометка) или None (запас 0)."""
    if host is None or host.Category is None:
        return None
    k = (_iv(host.Id), kind)
    if k in cache:
        return cache[k]
    found = _rule_reserve(doc, host, rules, kind)
    if found is None:
        try:
            for c in host.MEPModel.ConnectorManager.Connectors:
                for ref in c.AllRefs:
                    o = ref.Owner
                    if o is None or _iv(o.Id) == _iv(host.Id) or o.Category is None:
                        continue
                    if not isinstance(o.Location, LocationCurve):
                        continue
                    f2 = _rule_reserve(doc, o, rules, kind)
                    if f2 is not None:
                        found = (f2[0], f2[1], u', по присоединённому: %s' % o.Category.Name)
                        break
                if found is not None:
                    break
        except Exception:
            pass
    cache[k] = found
    return found


def read_target(doc, el, target):
    """(значение в единицах ведомости, параметр) или (None, параметр/None)."""
    p = find_param(doc, el, target)
    if p is None:
        return None, None
    if not p.HasValue:
        return None, p
    if p.StorageType == StorageType.Double:
        v = p.AsDouble()
        if target == u'ADSK_Размер_Площадь':
            v *= FT2
        elif target == u'ADSK_Размер_Длина':
            v *= FT
        return v, p
    if p.StorageType == StorageType.Integer:
        return float(p.AsInteger()), p
    try:
        return float((p.AsString() or u'').replace(u',', u'.')), p
    except Exception:
        return None, p


# ------------------------------------------------------------------ сбор и расчёт
def collect(doc, scope, view_id=None, sel_ids=None, cats=None):
    cats = cats or [k for n, k in CATS]
    out = []
    if scope == 'selection':
        want = set(cat_id(c) for c in cats)
        for i in sel_ids or []:
            el = doc.GetElement(i)
            if el is not None and el.Category is not None and _iv(el.Category.Id) in want:
                out.append(el)
        return out
    for c in cats:
        if scope == 'view' and view_id is not None:
            col = FilteredElementCollector(doc, view_id)
        else:
            col = FilteredElementCollector(doc)
        out.extend(list(col.OfCategory(bic(c)).WhereElementIsNotElementType()))
    return out


def prepare(doc, els, frs, chosen_ids):
    """Разложить элементы по рамкам. Возвращает (в_расчёт, вне_рамок, здание≠рамка).
    Без выбранных рамок — всё в расчёт, здание не трогаем."""
    chosen = [f for f in frs if f['id'] in (chosen_ids or [])]
    if not chosen:
        return [(el, None) for el in els], [], []
    blds = set(f['building'] for f in chosen)
    inside, outside, mism = [], [], []
    for el in els:
        f = frame_of(el, chosen)
        cur = pstr(doc, el, BUILDING_PARAM)
        if f is None:
            if cur == u'' or cur in blds:
                outside.append(el)
            continue
        inside.append((el, f))
        if cur != f['building']:
            mism.append((el, cur, f))
    return inside, outside, mism


def outside_fix(doc, outside, frs):
    """Элементы вне отмеченных рамок, у которых здание записано как у отмеченной рамки (db 21.10: «если элемент
    не в рамке — в параметре здания не должно быть написано, что он относится к этому зданию»).
    Новое здание — по любой другой рамке, в которой элемент стоит; нет такой — пусто.
    [(элемент, сейчас, станет)]."""
    out = []
    for el in outside:
        cur = pstr(doc, el, BUILDING_PARAM)
        if not cur:
            continue
        f = frame_of(el, frs)
        new = f['building'] if f is not None else u''
        if new != cur:
            out.append((el, cur, new))
    return out


def match_counts(doc, items, rules):
    """Сколько элементов поймало каждое правило (первое подошедшее для своей цели)."""
    cnt = [0] * len(rules)
    by_cat = {}
    for i, r in enumerate(rules):
        if r.get('on'):
            by_cat.setdefault(cat_id(r['cat']), []).append(i)
    for el, f in items:
        if el.Category is None:
            continue
        idx = by_cat.get(_iv(el.Category.Id))
        if not idx:
            continue
        host = None
        done = set()
        for i in idx:
            r = rules[i]
            if r['target'] in done:
                continue
            if r['conds'] and host is None:
                host = host_of(doc, el)
            if all(cond_ok(doc, el, host, c) for c in r['conds']):
                cnt[i] += 1
                done.add(r['target'])
    return cnt


def compute(doc, items, rules, skip_excl=True):
    """Проба. items — [(элемент, рамка|None)]. Ничего не пишет."""
    res = {'changes': [], 'totals': {}, 'nodata': [], 'norule': [], 'same': 0, 'counts': [0] * len(rules)}
    hcache = {}
    by_cat = {}
    for i, r in enumerate(rules):
        if r.get('on'):
            by_cat.setdefault(cat_id(r['cat']), []).append(i)
    for el, f in items:
        if el.Category is None:
            continue
        idx = by_cat.get(_iv(el.Category.Id))
        if not idx:
            continue
        name = pstr(doc, el, NAME_PARAM)
        if skip_excl and name.startswith(u'!'):
            continue
        host = host_of(doc, el)
        done = set()
        for i in idx:
            r = rules[i]
            t = r['target']
            if t in done:
                continue
            if not all(cond_ok(doc, el, host, c) for c in r['conds']):
                continue
            done.add(t)
            res['counts'][i] += 1
            m = r['measure']
            k_own = 1.0 + float(r.get('reserve') or 0) / 100.0
            if m in ('host_length_res', 'host_surface_res'):
                val, why = measure(doc, el, host, 'length' if m == 'host_length_res' else 'host_surface')
                hr = host_reserve(doc, host, rules, 'len' if m == 'host_length_res' else 'area', hcache)
                k_host = 1.0 + (hr[0] if hr else 0.0) / 100.0
                note = (u'%.2f × %.2f (запас основы%s) × %.2f' % (
                    val, k_host, (u', правило %d%s' % (hr[1], hr[2])) if hr else u' — нет правила', k_own)) \
                    if val is not None else u''
            else:
                val, why = measure(doc, el, host, m)
                k_host = 1.0
                note = (u'%.2f × %.2f' % (val, k_own)) if val is not None else u''
            old, p = read_target(doc, el, t)
            if p is None:
                res['nodata'].append((el, name, t, u'нет параметра ' + t))
                continue
            if val is None:
                res['nodata'].append((el, name, t, why))
                continue
            new = round(val * k_host * k_own, 3 if t == FACT_PARAM else 2)  # факт — до мм
            key = (name, t, MEASURE_UNIT.get(r['measure'], u''))
            tot = res['totals'].setdefault(key, [0, 0.0, 0.0])
            tot[0] += 1
            tot[1] += old or 0.0
            tot[2] += new
            if old is not None and abs(new - old) < (0.0005 if t == FACT_PARAM else 0.005):
                res['same'] += 1
                continue
            if p.IsReadOnly:
                res['nodata'].append((el, name, t, u'параметр только для чтения (формула в семействе?)'))
                continue
            res['changes'].append((el, name, t, old, new, i, note))
        if not done:
            res['norule'].append((el, name))
    return res


def apply(doc, changes, mism=None, fixes=None):
    """Записать. Вызывать внутри транзакции. fixes — из outside_fix (здание у элементов вне рамок).
    Возвращает (значений, зданий, ошибок)."""
    nv = nb = ne = 0
    for ch in changes:
        el, name, t, old, new = ch[:5]
        p = find_param(doc, el, t)
        try:
            if p.StorageType == StorageType.Double:
                p.Set(new / FT2 if t == u'ADSK_Размер_Площадь' else (new / FT if t == u'ADSK_Размер_Длина' else new))
            elif p.StorageType == StorageType.Integer:
                p.Set(int(round(new)))
            else:
                p.Set((u'%.2f' % new).replace(u'.', u','))
            nv += 1
        except Exception:
            ne += 1
    todo = [(el, f['building']) for el, cur, f in (mism or [])] + [(el, new) for el, cur, new in (fixes or [])]
    for el, new in todo:
        p = el.LookupParameter(BUILDING_PARAM)
        try:
            if p is not None and not p.IsReadOnly:
                p.Set(new)
                nb += 1
            else:
                ne += 1
        except Exception:
            ne += 1
    return nv, nb, ne
