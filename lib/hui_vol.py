# -*- coding: utf-8 -*-
"""Объёмы пространств — логика кнопки «Захуярить → Объёмы» (db 21.10.2026).

Объём = DirectShape категории «Обобщённые модели» по геометрии пространства.
Связь с пространством — ApplicationId = «ХУЙ.Объёмы», ApplicationDataId = UniqueId пространства: повторный
«Создать» обновляет форму и значения, а не плодит копии. В «Комментарии» объёма пишется «#объём».
Значения параметров пространства копируются в объём; параметр должен быть привязан к «Обобщённым моделям»
(кнопка привязывает по «да» — это изменение привязки).
Раскраска — только фильтрами вида: «db_Объём_<параметр>_<значение>» = параметр равно значению И
«Комментарии» содержит «#объём»; сплошная заливка, прозрачность одной цифрой, тёмные рёбра.
"""
import re

from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, ElementId, FilteredElementCollector, DirectShape,
                               SpatialElementGeometryCalculator, SpatialElementBoundaryOptions, GeometryObject,
                               StorageType, Category, ParameterFilterElement, ParameterFilterRuleFactory,
                               ElementParameterFilter, FilterRule, OverrideGraphicSettings, Color, FillPatternElement,
                               Solid, FilteredWorksetCollector, WorksetKind, Part, InternalDefinition, CurveLoop,
                               GeometryCreationUtilities, SolidUtils, Transform, XYZ)
from System.Collections.Generic import List

APP_ID = u'ХУЙ.Объёмы'
LAST = {'mesh': 0}   # сколько объёмов последнего build() построено сеткой
MARK = u'#объём'
PREFIX = u'db_Объём_'
MP_WORKSET = u'db_Пространства_Зонирование'
DEF_PARAMS = [u'ADSK_Наименование приточной системы', u'ADSK_Наименование вытяжной системы']
PALETTE = ['#00E676', '#2962FF', '#FF4081', '#FFD600', '#00B8D4', '#FF6D00', '#AA00FF', '#64DD17', '#D50000',
           '#3E2723', '#1DE9B6', '#C51162', '#6200EA', '#AEEA00', '#0091EA', '#FFAB00', '#DD2C00', '#00C853',
           '#304FFE', '#F50057', '#00BFA5', '#FFC400', '#651FFF', '#76FF03']


def palette_ext():
    """Большая палитра: базовые 24 + оттенки по кругу (3 насыщенности/яркости), без повторов."""
    import colorsys
    out = list(PALETTE)
    for sv in ((0.85, 0.95), (0.55, 0.85), (0.9, 0.6)):
        for k in range(24):
            r, g, b = colorsys.hsv_to_rgb(k / 24.0, sv[0], sv[1])
            hx = '#%02X%02X%02X' % (int(r * 255), int(g * 255), int(b * 255))
            if hx not in out:
                out.append(hx)
    return out


def _dist(a, b):
    ra, ga, ba = hexrgb(a)
    rb, gb, bb = hexrgb(b)
    return ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5


# Палитры в духе coolors.co (db 21.10: «рандомные цвета — безвкусица»): гармоничные наборы, у каждого — свой характер.
# Набор шире 10 цветов дополняется приглушёнными (темнее) и светлыми вариантами тех же оттенков.
PALETTES = [
    (u'Природа', ['#264653', '#2A9D8F', '#E9C46A', '#F4A261', '#E76F51', '#606C38', '#DDA15E', '#BC6C25',
                  '#219EBC', '#8ECAE6', '#023047', '#FFB703', '#FB8500']),
    (u'Сочная', ['#F94144', '#F3722C', '#F8961E', '#F9C74F', '#90BE6D', '#43AA8B', '#4D908E', '#577590',
                 '#277DA1', '#F9844A']),
    (u'Ретро', ['#EF476F', '#FFD166', '#06D6A0', '#118AB2', '#073B4C', '#E63946', '#A8DADC', '#457B9D',
                '#1D3557', '#F4A261']),
    (u'Пастель', ['#FFADAD', '#FFD6A5', '#FDFFB6', '#CAFFBF', '#9BF6FF', '#A0C4FF', '#BDB2FF', '#FFC6FF',
                  '#CDB4DB', '#FFAFCC', '#BDE0FE', '#A2D2FF']),
    (u'Скандинавия', ['#CCD5AE', '#E9EDC9', '#FAEDCD', '#D4A373', '#CAD2C5', '#84A98C', '#52796F', '#354F52',
                      '#E3D5CA', '#D5BDAF', '#B7B7A4', '#6B705C']),
    (u'Море', ['#03045E', '#0077B6', '#00B4D8', '#90E0EF', '#CAF0F8', '#F6BD60', '#F28482', '#84A59D',
               '#F5CAC3', '#023E8A', '#48CAE4']),
    (u'Закат', ['#355070', '#6D597A', '#B56576', '#E56B6F', '#EAAC8B', '#FFCDB2', '#E5989B', '#B5838D',
                '#6D6875', '#F7B267', '#F4845F']),
]
PALETTE_NAMES = [n for n, c in PALETTES]
DEFAULT_PALETTE = u'Природа'


def _mix(hx, to, k):
    r, g, b = hexrgb(hx)
    t = 255 if to == 'white' else 0
    return '#%02X%02X%02X' % tuple(int(round(c + (t - c) * k)) for c in (r, g, b))


def palette_colors(name):
    base = dict(PALETTES).get(name) or dict(PALETTES)[DEFAULT_PALETTE]
    out = list(base)
    for hx in base:            # приглушённые
        out.append(_mix(hx, 'black', 0.28))
    for hx in base:            # светлые
        out.append(_mix(hx, 'white', 0.45))
    seen = []
    for hx in out:
        if hx not in seen:
            seen.append(hx)
    return seen


# ---- цветовой круг (как colorscheme.ru / Itten): схемы по углам на художественном круге RYB
SCHEMES = [
    ('mono', u'Монохром (один оттенок)', [0]),
    ('analog', u'Аналогия (соседние ±30°)', [0, -30, 30]),
    ('acc_analog', u'Акцентированная аналогия (+ акцент 180°)', [0, -30, 30, 180]),
    ('compl', u'Контраст (комплементарные 180°)', [0, 180]),
    ('split', u'Мягкий контраст (150° и 210°)', [0, 150, 210]),
    ('triad', u'Триада (120°)', [0, 120, 240]),
    ('tetrad', u'Тетрада (прямоугольник 60°/120°)', [0, 60, 180, 240]),
    ('square', u'Квадрат (90°)', [0, 90, 180, 270]),
]
TONES = [('vivid', u'Насыщенный', 0.72, 0.86), ('pastel', u'Пастель', 0.32, 0.96),
         ('muted', u'Приглушённый', 0.38, 0.72), ('deep', u'Глубокий', 0.68, 0.52)]
# художественный круг RYB → оттенок RGB (опорные точки Иттена: красный, оранжевый, жёлтый, зелёный, синий, фиолетовый)
_RYB = [(0, 0), (60, 35), (120, 60), (180, 120), (240, 225), (300, 280), (360, 360)]


def _interp(x, pts, inv=False):
    x %= 360
    for (a1, b1), (a2, b2) in zip(pts, pts[1:]):
        if inv:
            a1, b1, a2, b2 = b1, a1, b2, a2
        if a1 <= x <= a2:
            return b1 + (b2 - b1) * (x - a1) / float(a2 - a1 or 1)
    return x


def ryb_to_rgb_hue(h):
    return _interp(h, _RYB) % 360


def rgb_to_ryb_hue(h):
    return _interp(h, _RYB, inv=True) % 360


def wheel_colors(base_hex, scheme='triad', tone='vivid', n=12):
    """Цвета схемы: оттенки по углам схемы от базового цвета (на круге RYB), для каждого — варианты светлоты
    в выбранном тоне (как 5 вариантов на оттенок у colorscheme.ru). Порядок: сначала все оттенки в основном варианте,
    затем светлее, темнее и т. д. — соседние значения получают разные оттенки."""
    import colorsys
    r, g, b = hexrgb(base_hex or '#2A9D8F')
    h, _, _ = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    h0 = rgb_to_ryb_hue(h * 360)
    angles = dict((k, a) for k, nm, a in SCHEMES).get(scheme) or [0, 120, 240]
    s0, v0 = dict((k, (s_, v_)) for k, nm, s_, v_ in TONES).get(tone) or (0.72, 0.86)
    variants = [(1.0, 1.0), (0.55, 1.12), (1.0, 0.72), (0.3, 1.18), (0.9, 0.52), (0.75, 0.88), (0.45, 0.6)]
    if scheme == 'mono':
        variants = [(1.0, 1.0), (0.55, 1.12), (1.0, 0.72), (0.3, 1.18), (0.9, 0.52), (0.75, 0.88), (0.45, 0.6),
                    (0.18, 1.2), (1.0, 0.4), (0.6, 0.8)]
    out = []
    for ks, kv in variants:
        for a in angles:
            hh = ryb_to_rgb_hue(h0 + a) / 360.0
            rr, gg, bb = colorsys.hsv_to_rgb(hh, max(0.05, min(1.0, s0 * ks)), max(0.12, min(1.0, v0 * kv)))
            hx = '#%02X%02X%02X' % (int(rr * 255), int(gg * 255), int(bb * 255))
            if hx not in out:
                out.append(hx)
    return out


def free_colors(used, n, min_dist=45, shuffle=False, palette=None, wheel=None):
    """n цветов из палитры palette (имя из PALETTES), непохожих (дальше min_dist) на used и друг на друга.
    Порядок — как задуман в палитре; shuffle — перемешать (каждый «Подобрать заново» даёт новый набор)."""
    out = []
    if wheel:   # {'scheme', 'tone', 'base'} — схема цветового круга вместо готовой палитры
        pool = wheel_colors(wheel.get('base'), wheel.get('scheme'), wheel.get('tone'))
        min_dist = min(min_dist, 30)
        if shuffle:
            import random
            k = len(dict((x, a) for x, nm, a in SCHEMES).get(wheel.get('scheme')) or [0])
            head, tail = pool[:k], pool[k:]
            random.shuffle(tail)
            pool = head + tail
        shuffle = False
    else:
        pool = palette_colors(palette or DEFAULT_PALETTE)
    if shuffle:
        import random
        nb = len(dict(PALETTES).get(palette or DEFAULT_PALETTE) or [])
        head, tail = pool[:nb], pool[nb:]
        random.shuffle(head)
        random.shuffle(tail)
        pool = head + tail     # сначала основные цвета палитры, потом их варианты
    for lim in (min_dist, min_dist * 0.5, 0):
        for hx in pool:
            if len(out) >= n:
                return out
            if hx in out:
                continue
            if all(_dist(hx, u) > lim for u in list(used) + out):
                out.append(hx)
    while len(out) < n:
        out.append(pool[len(out) % len(pool)])
    return out


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


GM = ElementId(BuiltInCategory.OST_GenericModel)


# ------------------------------------------------------------------ пространства и объёмы
def spaces(doc, scope='model', view=None, sel_ids=None):
    sid = _iv(ElementId(BuiltInCategory.OST_MEPSpaces))
    if scope == 'selection':
        out = [doc.GetElement(i) for i in (sel_ids or [])]
        return [e for e in out if e is not None and e.Category is not None and _iv(e.Category.Id) == sid]
    col = FilteredElementCollector(doc, view.Id) if (scope == 'view' and view is not None) else FilteredElementCollector(doc)
    return [s for s in col.OfCategory(BuiltInCategory.OST_MEPSpaces).WhereElementIsNotElementType() if s.Area > 0]


def our_volumes(doc):
    """{UniqueId пространства: DirectShape}."""
    out = {}
    for ds in FilteredElementCollector(doc).OfClass(DirectShape):
        if (ds.ApplicationId or u'') == APP_ID:
            out[ds.ApplicationDataId or u''] = ds
    return out


def modplus_volumes(doc):
    """DirectShape без ApplicationId в рабочем наборе зонирования + части из них: (ds_ids, part_ids)."""
    ws = None
    for w in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
        if w.Name == MP_WORKSET:
            ws = _iv(w.Id)
    if ws is None:
        return [], []
    ds_ids = []
    for ds in FilteredElementCollector(doc).OfClass(DirectShape):
        if (ds.ApplicationId or u'') == u'' and _iv(ds.WorksetId) == ws and ds.Category is not None \
                and _iv(ds.Category.Id) == _iv(GM):
            ds_ids.append(ds.Id)
    keep = set(_iv(i) for i in ds_ids)
    part_ids = []
    for p in FilteredElementCollector(doc).OfClass(Part):
        try:
            if any(_iv(l.HostElementId) in keep for l in p.GetSourceElementIds()):
                part_ids.append(p.Id)
        except Exception:
            pass
    return ds_ids, part_ids


def _extrusion(sp):
    """Выдавливание контура пространства на его высоту: от уровня + смещение низа."""
    try:
        opt = SpatialElementBoundaryOptions()
        loops = List[CurveLoop]()
        for segs in sp.GetBoundarySegments(opt):
            cl = CurveLoop()
            for sg in segs:
                cl.Append(sg.GetCurve())
            loops.Add(cl)
        if not loops.Count:
            return None
        h = sp.UnboundedHeight
        if h <= 1e-6:
            return None
        z0 = sp.Level.Elevation + sp.get_Parameter(BuiltInParameter.ROOM_LOWER_OFFSET).AsDouble()
        dz = z0 - loops[0].GetPlane().Origin.Z
        if abs(dz) > 1e-9:
            t = Transform.CreateTranslation(XYZ(0, 0, dz))
            moved = List[CurveLoop]()
            for cl in loops:
                moved.Add(CurveLoop.CreateViaTransform(cl, t))
            loops = moved
        return GeometryCreationUtilities.CreateExtrusionGeometry(loops, XYZ.BasisZ, h)
    except Exception:
        return None


def candidates(calc, sp):
    """Тела пространства по порядку: расчётная геометрия, её копия, выдавливание контура на высоту."""
    out = []
    try:
        s = calc.CalculateSpatialElementGeometry(sp).GetGeometry()
        if s is not None and s.Volume > 1e-6:
            out.append(s)
            try:
                out.append(SolidUtils.Clone(s))
            except Exception:
                pass
    except Exception:
        pass
    e = _extrusion(sp)
    if e is not None and e.Volume > 1e-6:
        out.append(e)
    return out


def space_solid(calc, sp):
    c = candidates(calc, sp)
    return c[0] if c else None


def tessellate(solid):
    """Сетка из треугольников граней тела (TessellatedShapeBuilder: тело, если выйдет, иначе сетка)."""
    from Autodesk.Revit.DB import (TessellatedShapeBuilder, TessellatedFace, TessellatedShapeBuilderTarget,
                                   TessellatedShapeBuilderFallback)
    try:
        tb = TessellatedShapeBuilder()
        tb.OpenConnectedFaceSet(True)
        for f in solid.Faces:
            m = f.Triangulate()
            for i in range(m.NumTriangles):
                t = m.get_Triangle(i)
                pts = List[XYZ]()
                pts.Add(t.get_Vertex(0))
                pts.Add(t.get_Vertex(1))
                pts.Add(t.get_Vertex(2))
                tb.AddFace(TessellatedFace(pts, ElementId.InvalidElementId))
        tb.CloseConnectedFaceSet()
        tb.Target = TessellatedShapeBuilderTarget.AnyGeometry
        tb.Fallback = TessellatedShapeBuilderFallback.Mesh
        tb.Build()
        return list(tb.GetBuildResult().GetGeometricalObjects())
    except Exception:
        return []


def pick_valid(ds, cands):
    for s in cands:
        try:
            if ds.IsValidGeometry(s):
                return s
        except Exception:
            pass
    return None


def space_label(sp):
    num = u''
    try:
        num = sp.Number or u''
    except Exception:
        pass
    nm = u''
    try:
        p = sp.get_Parameter(BuiltInParameter.ROOM_NAME)
        nm = (p.AsString() or u'') if p is not None else u''
    except Exception:
        pass
    return (u'%s %s' % (num, nm)).strip() or u'Пространство %d' % _iv(sp.Id)


# ------------------------------------------------------------------ параметры
def string_params(doc, els, limit=200):
    names = set()
    for e in els[:limit]:
        for p in e.Parameters:
            try:
                if p.StorageType in (StorageType.String, StorageType.Double, StorageType.Integer) and not p.IsReadOnly:
                    names.add(p.Definition.Name)
            except Exception:
                pass
    return sorted(names)


def bindings(doc):
    """{имя: (Definition, ElementBinding)}."""
    out = {}
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        out[it.Key.Name] = (it.Key, it.Current)
    return out


def bound_to_gm(doc, name, b=None):
    b = b or bindings(doc)
    if name not in b:
        return None
    return b[name][1].Categories.Contains(Category.GetCategory(doc, BuiltInCategory.OST_GenericModel))


def bind_gm(doc, names):
    """Добавить «Обобщённые модели» к привязке параметров (в транзакции). Возвращает список привязанных."""
    b = bindings(doc)
    done = []
    gm = Category.GetCategory(doc, BuiltInCategory.OST_GenericModel)
    for n in names:
        if n not in b:
            continue
        d, bd = b[n]
        if bd.Categories.Contains(gm):
            continue
        bd.Categories.Insert(gm)
        if doc.ParameterBindings.ReInsert(d, bd):
            done.append(n)
    return done


def pval(el, name):
    p = el.LookupParameter(name)
    if p is None or not p.HasValue:
        return u''
    if p.StorageType == StorageType.String:
        return p.AsString() or u''
    try:
        return p.AsValueString() or u''
    except Exception:
        return u''


def copy_param(src, dst, name):
    a = src.LookupParameter(name)
    b = dst.LookupParameter(name)
    if a is None or b is None or b.IsReadOnly or a.StorageType != b.StorageType:
        return False
    if a.StorageType == StorageType.String:
        v = a.AsString() or u''
        if (b.AsString() or u'') != v:
            b.Set(v)
        return True
    if a.StorageType == StorageType.Double:
        b.Set(a.AsDouble())
        return True
    if a.StorageType == StorageType.Integer:
        b.Set(a.AsInteger())
        return True
    return False


# ------------------------------------------------------------------ создать / обновить / удалить
def plan(doc, sps):
    """Проба: (новые, обновить, без геометрии)."""
    ex = our_volumes(doc)
    calc = SpatialElementGeometryCalculator(doc, SpatialElementBoundaryOptions())
    new, upd, bad = [], [], []
    for sp in sps:
        if space_solid(calc, sp) is None:
            bad.append(sp)
        elif sp.UniqueId in ex:
            upd.append(sp)
        else:
            new.append(sp)
    return new, upd, bad


def build(doc, sps, params, ws_name=None):
    """В транзакции: создать/обновить объёмы. (создано, обновлено, без геометрии, значений)."""
    ex = our_volumes(doc)
    calc = SpatialElementGeometryCalculator(doc, SpatialElementBoundaryOptions())
    ws_id = None
    if ws_name and doc.IsWorkshared:
        for w in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
            if w.Name == ws_name:
                ws_id = _iv(w.Id)
    nc = nu = nb = nv = 0
    nmesh = 0
    for sp in sps:
        cands = candidates(calc, sp)
        if not cands:
            nb += 1
            continue
        ds = ex.get(sp.UniqueId)
        fresh = ds is None
        if fresh:
            ds = DirectShape.CreateElement(doc, GM)
        s = pick_valid(ds, cands)
        geo = List[GeometryObject]()
        if s is not None:
            geo.Add(s)
        else:
            # сложная форма (короткие рёбра ~1 мм, много контуров): тело не проходит проверку DirectShape —
            # строим сетку из треугольников той же геометрии (db 21.10: «тела должны создаваться»)
            for g_ in tessellate(cands[0]):
                geo.Add(g_)
            if not geo.Count or not ds.IsValidShape(geo):
                if fresh:
                    doc.Delete(ds.Id)
                nb += 1
                continue
            nmesh += 1
        if fresh:
            ds.ApplicationId = APP_ID
            ds.ApplicationDataId = sp.UniqueId
            nc += 1
            if ws_id is not None:
                try:
                    ds.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM).Set(ws_id)
                except Exception:
                    pass
        else:
            nu += 1
        ds.SetShape(geo)
        try:
            ds.SetName(space_label(sp))
        except Exception:
            pass
        c = ds.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if c is not None and (c.AsString() or u'') != MARK:
            c.Set(MARK)
        for n in params:
            if copy_param(sp, ds, n):
                nv += 1
    LAST['mesh'] = nmesh
    return nc, nu, nb, nv


def delete(doc, ids):
    n = 0
    for i in ids:
        try:
            if doc.GetElement(i) is not None:
                doc.Delete(i)
                n += 1
        except Exception:
            pass
    return n


# ------------------------------------------------------------------ раскраска фильтрами
def hexrgb(h):
    h = h.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def value_counts(doc, name):
    d = {}
    for ds in our_volumes(doc).values():
        v = pval(ds, name)
        d[v] = d.get(v, 0) + 1
    return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))


def safe(s):
    return re.sub(u'[\\\\:{}\\[\\]|;<>?`~]', u'_', s)[:80]


def filter_name(param, value):
    short = param.replace(u'ADSK_', u'').replace(u'Наименование ', u'')
    return safe(PREFIX + short + u'_' + value)


def _param_id(doc, name):
    b = bindings(doc)
    if name in b:
        d = b[name][0]
        if isinstance(d, InternalDefinition):
            return d.Id
    return None


def paint(doc, view, param, colors, transparency=0, edge='#202020', empty_transp=None):
    """В транзакции. colors — [(значение, '#RRGGBB')]. Фильтры прежней раскраски объёмов на виде снимаются.
    (фильтров на виде, создано новых)."""
    pid = _param_id(doc, param)
    if pid is None:
        raise Exception(u'Параметр «%s» не найден среди привязанных к модели' % param)
    cid = ElementId(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    cats = List[ElementId]()
    cats.Add(GM)
    solid = None
    for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
        if fp.GetFillPattern().IsSolidFill:
            solid = fp.Id
            break
    existing = dict((f.Name, f) for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement))
    for fid in list(view.GetFilters()):
        f = doc.GetElement(fid)
        if f is not None and f.Name.startswith(PREFIX):
            view.RemoveFilter(fid)
    er, eg, eb = hexrgb(edge)
    created = 0
    on_view = 0
    for value, hx in colors:
        if not value:
            continue
        rules = List[FilterRule]()
        rules.Add(ParameterFilterRuleFactory.CreateEqualsRule(pid, value))
        rules.Add(ParameterFilterRuleFactory.CreateContainsRule(cid, MARK))
        ef = ElementParameterFilter(rules)
        name = filter_name(param, value)
        pf = existing.get(name)
        if pf is None:
            pf = ParameterFilterElement.Create(doc, name, cats, ef)
            existing[name] = pf
            created += 1
        else:
            pf.SetCategories(cats)
            pf.SetElementFilter(ef)
        r, g, b = hexrgb(hx)
        col = Color(r, g, b)
        ogs = OverrideGraphicSettings()
        if solid is not None:
            ogs.SetSurfaceForegroundPatternId(solid)
            ogs.SetCutForegroundPatternId(solid)
        ogs.SetSurfaceForegroundPatternColor(col)
        ogs.SetCutForegroundPatternColor(col)
        ogs.SetSurfaceTransparency(int(transparency))
        ogs.SetProjectionLineColor(Color(er, eg, eb))
        ogs.SetCutLineColor(Color(er, eg, eb))
        view.AddFilter(pf.Id)
        view.SetFilterOverrides(pf.Id, ogs)
        view.SetFilterVisibility(pf.Id, True)
        on_view += 1
    if empty_transp is not None:
        # бесцветные объёмы (параметр пуст): своя прозрачность 0…99, 100 — скрыть (db 21.10)
        from Autodesk.Revit.DB import LogicalOrFilter, ElementFilter
        fl = List[ElementFilter]()
        for r_ in (ParameterFilterRuleFactory.CreateHasNoValueParameterRule(pid),
                   ParameterFilterRuleFactory.CreateEqualsRule(pid, u'')):
            rr = List[FilterRule]()
            rr.Add(r_)
            rr.Add(ParameterFilterRuleFactory.CreateContainsRule(cid, MARK))
            fl.Add(ElementParameterFilter(rr))
        ef = LogicalOrFilter(fl)
        name = filter_name(param, u'(пусто)')
        pf = existing.get(name)
        if pf is None:
            pf = ParameterFilterElement.Create(doc, name, cats, ef)
            created += 1
        else:
            pf.SetCategories(cats)
            pf.SetElementFilter(ef)
        ogs = OverrideGraphicSettings()
        ogs.SetSurfaceTransparency(int(min(99, max(0, empty_transp))))
        view.AddFilter(pf.Id)
        view.SetFilterOverrides(pf.Id, ogs)
        view.SetFilterVisibility(pf.Id, int(empty_transp) < 100)
        on_view += 1
    return on_view, created


# ------------------------------------------------------------------ легенда (как «Заливка цветом»)
LEGEND_PREFIX = u'db_Легенда_Объёмы_'
MM = 1.0 / 304.8


def _legend_base(doc):
    from Autodesk.Revit.DB import View, ViewType
    for vw in FilteredElementCollector(doc).OfClass(View):
        if vw.ViewType == ViewType.Legend and not vw.IsTemplate:
            return vw
    return None


def legend_name(param):
    return safe(LEGEND_PREFIX + param.replace(u'ADSK_', u'').replace(u'Наименование ', u''))


def _nm(el):
    from Autodesk.Revit.DB import Element
    try:
        return Element.Name.GetValue(el)
    except Exception:
        return u''


def _region_type(doc, hx, cache):
    from Autodesk.Revit.DB import FilledRegionType
    if hx in cache:
        return cache[hx]
    name = u'db_Объём_' + hx.lstrip('#').upper()
    types = list(FilteredElementCollector(doc).OfClass(FilledRegionType))
    t = next((x for x in types if _nm(x) == name), None)
    if t is None:
        t = types[0].Duplicate(name)
    solid = next(fp.Id for fp in FilteredElementCollector(doc).OfClass(FillPatternElement)
                 if fp.GetFillPattern().IsSolidFill)
    r, g, b = hexrgb(hx)
    t.ForegroundPatternId = solid
    t.ForegroundPatternColor = Color(r, g, b)
    try:
        t.BackgroundPatternId = ElementId.InvalidElementId
    except Exception:
        pass
    cache[hx] = t.Id
    return t.Id


def _text_type(doc, size_mm, fallback=None):
    from Autodesk.Revit.DB import TextNoteType
    best = None
    for t in FilteredElementCollector(doc).OfClass(TextNoteType):
        try:
            s = t.get_Parameter(BuiltInParameter.TEXT_SIZE).AsDouble() * 304.8
        except Exception:
            continue
        if abs(s - size_mm) < 0.01 and (best is None or u'Основной' in _nm(t)):
            best = t
    if best is not None:
        return best.Id
    if fallback is not None:
        return fallback
    from Autodesk.Revit.DB import ElementTypeGroup
    return doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType)


LABEL_MODES = [('val_desc', u'Значение — расшифровка'), ('val', u'Только значение'),
               ('desc', u'Только расшифровка (пусто — значение)')]


def label(value, desc, tail=u'', mode='val_desc'):
    """Подпись строки легенды. tail — итог в скобках («27 шт.», «17 610 м³/ч») или пусто."""
    desc = (desc or u'').strip()
    if mode == 'val':
        t = value
    elif mode == 'desc':
        t = desc or value
    else:
        t = value + (u' — ' + desc if desc else u'')
    return t + (u' (%s)' % tail if tail else u'')


DEFAULT_PARTS = [{'field': 'value', 'prefix': u'', 'suffix': u''},
                 {'field': 'desc', 'prefix': u' — ', 'suffix': u''},
                 {'field': 'count', 'prefix': u' (', 'suffix': u' пом.)'}]


def compose(parts, ctx):
    """Подпись по полям, как метка Revit: [{'field', 'prefix', 'suffix'}]; пустое поле выпадает целиком."""
    out = []
    for p in parts:
        val = (ctx.get(p.get('field') or u'') or u'').strip()
        if val in (u'', u'0') and p.get('field') == 'count':
            continue
        if not val:
            continue
        out.append((p.get('prefix') or u'') + val + (p.get('suffix') or u''))
    return u''.join(out).strip()


def number_params(doc, els, limit=200):
    """Числовые параметры пространств (для суммы в легенде): имя → None."""
    names = set()
    for e in els[:limit]:
        for p in e.Parameters:
            try:
                if p.StorageType == StorageType.Double:
                    names.add(p.Definition.Name)
            except Exception:
                pass
    return sorted(names)


def space_stats(doc, group_param, sps=None):
    """По ПРОСТРАНСТВАМ (источник правды, db 21.10): [(значение, шт.)] по убыванию, а также
    сколько объёмов устарело (значение у объёма ≠ у пространства) и пространства без объёма."""
    vols = our_volumes(doc)
    d = {}
    stale = 0
    novol = []
    for sp in (sps if sps is not None else spaces(doc, 'model')):
        val = pval(sp, group_param)
        d[val] = d.get(val, 0) + 1
        ds = vols.get(sp.UniqueId)
        if ds is None:
            novol.append(sp)
        elif pval(ds, group_param) != val:
            stale += 1
    return sorted(d.items(), key=lambda kv: (-kv[1], kv[0])), stale, novol


def sums(doc, group_param, sum_param, sps=None):
    """Сумма sum_param по всем размещённым пространствам каждого значения group_param — значение и слагаемые
    берутся у пространства (не у объёма: он мог устареть или не построиться). {значение: текст в единицах проекта}."""
    from Autodesk.Revit.DB import UnitFormatUtils
    tot = {}
    spec = None
    for sp in (sps if sps is not None else spaces(doc, 'model')):
        p = sp.LookupParameter(sum_param)
        if p is None or not p.HasValue or p.StorageType != StorageType.Double:
            continue
        val = pval(sp, group_param)
        spec = spec or p.Definition.GetDataType()
        tot[val] = tot.get(val, 0.0) + p.AsDouble()
    out = {}
    for val, x in tot.items():
        try:
            out[val] = UnitFormatUtils.Format(doc.GetUnits(), spec, x, False)
        except Exception:
            out[val] = u'%.2f' % x
    return out


def total(doc, group_param, sum_param, sps, values):
    """Итог суммы sum_param по пространствам, чьё значение group_param входит в values (строки легенды)."""
    from Autodesk.Revit.DB import UnitFormatUtils
    vs = set(values)
    x = 0.0
    spec = None
    for sp in (sps if sps is not None else spaces(doc, 'model')):
        if pval(sp, group_param) not in vs:
            continue
        p = sp.LookupParameter(sum_param)
        if p is None or not p.HasValue or p.StorageType != StorageType.Double:
            continue
        spec = spec or p.Definition.GetDataType()
        x += p.AsDouble()
    if spec is None:
        return u''
    try:
        return UnitFormatUtils.Format(doc.GetUnits(), spec, x, False)
    except Exception:
        return u'%.2f' % x


FOOT_FIELDS = [('n', u'Количество строк легенды'), ('nsp', u'Количество пространств')]
DEFAULT_FOOT = {'line': True, 'count_on': True, 'note_on': False, 'note': u'',
                'parts': [{'field': 'n', 'prefix': u'Итого · ', 'suffix': u' систем'}]}


def _paper_width(doc, tid, w):
    from Autodesk.Revit.DB import TextNote
    try:
        return max(TextNote.GetMinimumAllowedWidth(doc, tid), min(TextNote.GetMaximumAllowedWidth(doc, tid), w))
    except Exception:
        return w


def make_legend(doc, param, rows, title=None, foot=None):
    """В транзакции. rows — [(подпись, '#RRGGBB')]. Легенда «db_Легенда_Объёмы_<параметр>»: создаётся копией любой
    легенды (без элементов) или перерисовывается своя. foot (db 22.10) — итог под строками:
    {'line': bool, 'count': текст счётчика или '', 'note': примечание или '', 'note_old': прежний текст примечания}.
    Линия — от левого края заливок до конца последней строки; счётчик и примечание переносятся по её длине.
    Сохраняются типы текста (заголовок, строки, счётчик, примечание), стиль линии и положение легенды. Возвращает вид."""
    from Autodesk.Revit.DB import (View, ViewType, ViewDuplicateOption, FilledRegion, TextNote, TextNoteOptions,
                                   HorizontalTextAlignment, CurveLoop, Line, XYZ, DetailCurve, CurveElement)
    foot = foot or {}
    name = legend_name(param)
    lg = None
    for vw in FilteredElementCollector(doc).OfClass(View):
        if vw.ViewType == ViewType.Legend and not vw.IsTemplate and vw.Name == name:
            lg = vw
    if lg is None:
        base = _legend_base(doc)
        if base is None:
            raise Exception(u'В проекте нет ни одной легенды — создайте любую (Вид → Легенды), её копия станет основой.')
        lg = doc.GetElement(base.Duplicate(ViewDuplicateOption.Duplicate))
        lg.Name = name
    k = lg.Scale * MM          # 1 мм на листе в единицах модели
    keep = {}
    notes = [e for e in FilteredElementCollector(doc).OfClass(TextNote) if _iv(e.OwnerViewId) == _iv(lg.Id)]
    old = [e.Id for e in FilteredElementCollector(doc).OfClass(FilledRegion) if _iv(e.OwnerViewId) == _iv(lg.Id)]
    if notes:
        # db 21.10: типы текста, которые инженер поменял руками, и положение легенды — сохраняются
        top = max(notes, key=lambda t: t.Coord.Y)
        keep['title'] = top.GetTypeId()
        keep['title_text'] = top.Text.strip()
        keep['origin'] = top.Coord
        rx = top.Coord.X + 19 * k
        rws = [t for t in notes if _iv(t.Id) != _iv(top.Id) and abs(t.Coord.X - rx) < 2 * k]
        ft = sorted([t for t in notes if _iv(t.Id) != _iv(top.Id) and abs(t.Coord.X - rx) >= 2 * k],
                    key=lambda t: -t.Coord.Y)
        if rws:
            cnt = {}
            for t in rws:
                tid = t.GetTypeId()
                cnt[_iv(tid)] = (cnt.get(_iv(tid), (0, tid))[0] + 1, tid)
            keep['row'] = max(cnt.values(), key=lambda x: x[0])[1]
        nz = lambda x: u'\n'.join(l.strip() for l in (x or u'').replace(u'\r\n', u'\n').replace(u'\r', u'\n').strip().split(u'\n'))
        nk = set(nz(x) for x in (foot.get('note'), foot.get('note_old')) if nz(x))
        for t in ft:   # итог: примечание узнаём по тексту, остальное — счётчик
            if nz(t.Text) in nk:
                keep.setdefault('note_t', t.GetTypeId())
            else:
                keep.setdefault('count_t', t.GetTypeId())
        ox0 = top.Coord.X
        sk = set()   # линии эскизов заливок — не наши
        for i in old:
            try:
                sk |= set(_iv(x) for x in doc.GetElement(i).GetDependentElements(None))
            except Exception:
                pass
        for c in FilteredElementCollector(doc).OfClass(CurveElement):
            if _iv(c.OwnerViewId) != _iv(lg.Id) or not isinstance(c, DetailCurve) or _iv(c.Id) in sk:
                continue
            g = c.GeometryCurve
            a, b = g.GetEndPoint(0), g.GetEndPoint(1)
            if abs(a.Y - b.Y) < 1e-6 and abs(min(a.X, b.X) - ox0) < 0.5 * k:   # своя линия итога
                keep['line_style'] = c.LineStyle.Id
                old.append(c.Id)
        old += [t.Id for t in notes]
    for i in old:
        if doc.GetElement(i) is not None:
            doc.Delete(i)
    t35 = keep.get('title') or _text_type(doc, 3.5)
    t25 = keep.get('row') or _text_type(doc, 2.5)
    o = keep.get('origin') or XYZ(0, 0, 0)
    try:
        rs = doc.GetElement(t25).get_Parameter(BuiltInParameter.TEXT_SIZE).AsDouble() * 304.8
    except Exception:
        rs = 2.5
    step = max(7.5, rs * 2.6)   # шаг строк под выбранный размер текста
    ox, y = o.X, o.Y
    TextNote.Create(doc, lg.Id, XYZ(ox, y, 0), keep.get('title_text') or title or param, t35)
    y -= 9 * k
    cache = {}
    last = None
    for text, hx in rows:
        x0, x1, y0, y1 = ox, ox + 15 * k, y - 5 * k, y
        loop = CurveLoop()
        pts = [XYZ(x0, y0, 0), XYZ(x1, y0, 0), XYZ(x1, y1, 0), XYZ(x0, y1, 0)]
        for a in range(4):
            loop.Append(Line.CreateBound(pts[a], pts[(a + 1) % 4]))
        loops = List[CurveLoop]()
        loops.Add(loop)
        FilledRegion.Create(doc, _region_type(doc, hx, cache), lg.Id, loops)
        last = TextNote.Create(doc, lg.Id, XYZ(ox + 19 * k, y - 0.6 * k, 0), text, t25)
        y -= step * k
    want = foot.get('line') or (foot.get('count') or u'').strip() or (foot.get('note') or u'').strip()
    if last is None or not want:
        return lg
    doc.Regenerate()
    bb = last.get_BoundingBox(lg)
    right = bb.Max.X if bb is not None else ox + 19 * k + last.Width * lg.Scale
    width = (right - ox) / lg.Scale           # ширина текста — в единицах листа
    cur = y + step * k - 10 * k               # 5 мм под заливкой последней строки
    if foot.get('line'):
        dc = doc.Create.NewDetailCurve(lg, Line.CreateBound(XYZ(ox, cur, 0), XYZ(right, cur, 0)))
        if keep.get('line_style') is not None:
            try:
                dc.LineStyle = doc.GetElement(keep['line_style'])
            except Exception:
                pass
        cur -= 3.5 * k
    for key, txt_, tid in (('count', foot.get('count'), keep.get('count_t') or _text_type(doc, 3.0, t35)),
                           ('note', foot.get('note'), keep.get('note_t') or t25)):
        txt_ = (txt_ or u'').strip().replace(u'\r\n', u'\r').replace(u'\n', u'\r')
        if not txt_:
            continue
        op = TextNoteOptions(tid)
        op.HorizontalAlignment = HorizontalTextAlignment.Left
        tn = TextNote.Create(doc, lg.Id, XYZ(ox, cur, 0), _paper_width(doc, tid, width), txt_, op)
        doc.Regenerate()
        b2 = tn.get_BoundingBox(lg)
        cur = (b2.Min.Y if b2 is not None else cur - 5 * k) - 1.5 * k
    return lg
