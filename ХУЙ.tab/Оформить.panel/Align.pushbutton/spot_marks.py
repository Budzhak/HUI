# -*- coding: utf-8 -*-
"""
Высотные отметки на взрыв-схемах (3D-виды с DisplacementElement).

Отметки ставятся только там, где высоту иначе не прочитать
(указание инженера 07.10.2026 - не перегружать чертёж):
1) вертикальные участки на стыке ранее выданного объёма (зелёный фильтр,
   settings['f_exc']) с текущим (розовый фильтр, settings['f_inc']) -
   отметка в точке стыка;
2) горизонтальные участки текущего объёма, у которых нет марки с отметками
   (тип марки с "Отметка" в имени): прямоугольные - низ, круглые - ось;
3) канальные блоки кондиционирования - нижняя точка.

Смещённые элементы: Revit рисует отметку в исходном положении элемента,
поэтому ставится тип "Белая выноска" - невидимая выноска ведёт к месту,
где элемент нарисован на схеме (так же, как инженер делал вручную).

Вызывается из script.py: spot_marks.run(doc, uidoc, settings)
"""
import math

from Autodesk.Revit.DB import *

FT_MM = 304.8


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _cat_int(el):
    c = el.Category
    return _iv(c.Id) if c is not None else None


def _bic(name):
    return int(getattr(BuiltInCategory, name))


CURVE_CATS = set(_bic(n) for n in ('OST_DuctCurves', 'OST_PipeCurves', 'OST_FlexDuctCurves',
                                   'OST_FlexPipeCurves'))
FIT_CATS = set(_bic(n) for n in ('OST_DuctFitting', 'OST_PipeFitting', 'OST_DuctAccessory',
                                 'OST_PipeAccessory'))


def _connectors(el):
    try:
        if isinstance(el, MEPCurve):
            cm = el.ConnectorManager
        elif isinstance(el, FamilyInstance) and el.MEPModel is not None:
            cm = el.MEPModel.ConnectorManager
        else:
            return []
        if cm is None:
            return []
        return [c for c in cm.Connectors if c.ConnectorType != ConnectorType.Logical]
    except Exception:
        return []


def _is_vertical(c):
    d = c.GetEndPoint(1) - c.GetEndPoint(0)
    return d.GetLength() > 1e-9 and abs(d.Normalize().Z) > 0.985


def _is_horizontal(c):
    d = c.GetEndPoint(1) - c.GetEndPoint(0)
    return d.GetLength() > 1e-9 and abs(d.Normalize().Z) < 0.2


class Ctx(object):
    pass


def _disp_map(doc, view):
    m = {}
    for de in FilteredElementCollector(doc, view.Id).OfClass(DisplacementElement):
        try:
            vec = de.GetAbsoluteDisplacement()
            for eid in de.GetDisplacedElementIds():
                m[_iv(eid)] = vec
        except Exception:
            pass
    return m


def _dkey(ctx, el):
    v = ctx.disp.get(_iv(el.Id))
    if v is None:
        return (0.0, 0.0, 0.0)
    return (round(v.X, 2), round(v.Y, 2), round(v.Z, 2))


def _end_status(ctx, curve_el, conn):
    """'covered' - к концу примыкает горизонтальный участок того же фрагмента
    (на нём своя отметка); 'riser' - продолжение стояка; 'open' - нужна отметка."""
    if not conn.IsConnected:
        return 'open'
    my = _dkey(ctx, curve_el)
    seen = set([_iv(curve_el.Id)])
    frontier = []
    for r in conn.AllRefs:
        o = r.Owner
        if o is not None and _iv(o.Id) not in seen:
            frontier.append(o)
    depth = 0
    while frontier and depth < 4:
        nxt = []
        for o in frontier:
            oid = _iv(o.Id)
            if oid in seen:
                continue
            seen.add(oid)
            if _dkey(ctx, o) != my:
                return 'open'                     # граница вынесенного фрагмента
            ci = _cat_int(o)
            if isinstance(o, MEPCurve) and ci in CURVE_CATS:
                c = o.Location.Curve if isinstance(o.Location, LocationCurve) else None
                if c is None:
                    continue
                if _is_vertical(c):
                    return 'riser'
                return 'covered'
            if ci in FIT_CATS:
                for fc in _connectors(o):
                    if not fc.IsConnected:
                        continue
                    for r in fc.AllRefs:
                        o2 = r.Owner
                        if o2 is not None and _iv(o2.Id) not in seen:
                            nxt.append(o2)
            else:
                return 'open'                     # оборудование, решётка и т.п.
        frontier = nxt
        depth += 1
    return 'open'


def _end_edge(ctx, el, pt):
    """Ребро торца элемента в точке pt (горизонтальное ребро на высоте pt.Z)."""
    opt = Options()
    opt.ComputeReferences = True
    opt.IncludeNonVisibleObjects = False
    opt.View = ctx.view
    best, best_d = None, None
    for g in _geom(el, opt):
        if not isinstance(g, Solid) or g.Edges.Size == 0:
            continue
        for e in g.Edges:
            if e.Reference is None:
                continue
            c = e.AsCurve()
            p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
            pm = c.Evaluate(0.5, True)
            if abs(p0.Z - pt.Z) > 0.02 or abs(p1.Z - pt.Z) > 0.02 or abs(pm.Z - pt.Z) > 0.02:
                continue
            # ребро, ближайшее к зрителю (видимое): максимальная проекция на -ViewDirection
            d = -(pm.DotProduct(ctx.view.ViewDirection))
            if best is None or d > best_d:
                best, best_d = (e, pm), d
    return best


def _geom(el, opt):
    """Геометрия элемента. На взрыв-схеме смещённый элемент отдаётся как
    GeometryInstance - берём исходную геометрию (GetSymbolGeometry): её ссылки
    принимает NewSpotElevation, точки - в исходном положении элемента."""
    res = []
    geo = el.get_Geometry(opt)
    if geo is None:
        return res
    stack = list(geo)
    while stack:
        g = stack.pop()
        if isinstance(g, GeometryInstance):
            stack.extend(list(g.GetSymbolGeometry()))
            continue
        res.append(g)
    return res


def _uv(ctx, p):
    return (p.DotProduct(ctx.R), p.DotProduct(ctx.U))


def _box_hit(ctx, box):
    for b in ctx.occupied:
        if not (box[2] <= b[0] or box[0] >= b[2] or box[3] <= b[1] or box[1] >= b[3]):
            return True
    return False


def _size(el, bip):
    try:
        p = el.get_Parameter(bip)
        if p is not None and p.HasValue:
            return p.AsDouble()
    except Exception:
        pass
    return 0.0


def _is_round(el):
    return _size(el, BuiltInParameter.RBS_CURVE_DIAMETER_PARAM) > 0 or \
        _size(el, BuiltInParameter.RBS_PIPE_DIAMETER_PARAM) > 0


def _axis_ref(ctx, el):
    """Ссылка на ось круглого воздуховода/трубы (линия ':0:LINEAR')."""
    opt = Options()
    opt.ComputeReferences = True
    opt.IncludeNonVisibleObjects = True
    opt.View = ctx.view
    best = None
    for g in _geom(el, opt):
        if isinstance(g, Line) and g.Reference is not None:
            rep = g.Reference.ConvertToStableRepresentation(el.Document)
            if rep.endswith(u":0:LINEAR"):
                return g.Reference, g
            best = (g.Reference, g)
    return best


def _bottom_edge(ctx, el, c):
    """Нижнее продольное ребро прямоугольного воздуховода (ближнее к зрителю)."""
    opt = Options()
    opt.ComputeReferences = True
    opt.View = ctx.view
    d = (c.GetEndPoint(1) - c.GetEndPoint(0)).Normalize()
    cands = []
    for g in _geom(el, opt):
        if not isinstance(g, Solid):
            continue
        for e in g.Edges:
            if e.Reference is None:
                continue
            ec = e.AsCurve()
            if not isinstance(ec, Line):
                continue
            ed = ec.Direction
            if abs(abs(ed.DotProduct(d)) - 1.0) > 1e-3:
                continue
            cands.append((ec.Evaluate(0.5, True).Z, -ec.Evaluate(0.5, True).DotProduct(ctx.view.ViewDirection), e, ec))
    if not cands:
        return None
    zmin = min(x[0] for x in cands)
    low = [x for x in cands if abs(x[0] - zmin) < 1e-3]
    low.sort(key=lambda x: -x[1])
    return low[0][2].Reference, low[0][3]


def _section_groups(ctx, curves):
    """Горизонтальные участки: соседние сегменты одного размера и системы
    одного фрагмента взрыв-схемы (через отводы/арматуру с 2 соединителями)."""
    by_id = dict((_iv(e.Id), e) for e in curves)
    parent = dict((k, k) for k in by_id)

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def key(e):
        sz = e.get_Parameter(BuiltInParameter.RBS_CALCULATED_SIZE)
        sy = e.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
        return (sz.AsString() if sz else u"", sy.AsString() if sy else u"", _dkey(ctx, e))

    keys = dict((k, key(e)) for k, e in by_id.items())
    for k, e in by_id.items():
        for cn in _connectors(e):
            if not cn.IsConnected:
                continue
            for r in cn.AllRefs:
                o = r.Owner
                if o is None or _iv(o.Id) == k:
                    continue
                nbs = []
                if _iv(o.Id) in by_id:
                    nbs.append(_iv(o.Id))
                elif _cat_int(o) in FIT_CATS:
                    fcs = _connectors(o)
                    if len(fcs) == 2:
                        for fc in fcs:
                            if fc.IsConnected:
                                for r2 in fc.AllRefs:
                                    o2 = r2.Owner
                                    if o2 is not None and _iv(o2.Id) in by_id and _iv(o2.Id) != k:
                                        nbs.append(_iv(o2.Id))
                for n in nbs:
                    if keys[n] == keys[k]:
                        a, b = find(k), find(n)
                        if a != b:
                            parent[b] = a
    groups = {}
    for k in by_id:
        groups.setdefault(find(k), []).append(by_id[k])
    return list(groups.values())


def _spot_boxes(ctx, pu, pv, tkey, sx):
    """Габариты марки-отметки db_М_Отметка_Схемы на листе (как штатная отметка
    "db_Стрелка_Проектная_Схемы_Вниз"): стрелка у точки, отвод вниз 5 мм,
    полка 12.5 мм вбок, текст под полкой."""
    mm = ctx.mm
    t0, t1 = pu + sx * 1.5 * mm, pu + sx * 12.5 * mm
    text = (min(t0, t1), pv - 9.0 * mm, max(t0, t1), pv - 5.3 * mm)
    sym = (min(pu - 2.0 * mm, pu + sx * 12.5 * mm), pv - 5.3 * mm,
           max(pu + 2.0 * mm, pu + sx * 12.5 * mm), pv + 0.3 * mm)
    return text, sym


def _hits(ctx, box):
    n = 0
    for b in ctx.occupied:
        if not (box[2] <= b[0] or box[0] >= b[2] or box[3] <= b[1] or box[1] >= b[3]):
            n += 1
    return n


def _place(ctx, doc, ref, pts, el, types, label, role=None):
    """Марка-отметка (elev_marks): перебор точки на ребре/оси и стороны полки,
    выбирается вариант без наложений на марки и другие отметки. Марка рисуется
    там, где элемент показан на взрыв-схеме, значение пересчитывает ElevUpdater."""
    import elev_marks
    mm = ctx.mm
    dp = ctx.disp.get(_iv(el.Id))
    best = None
    for k, pm in enumerate(pts):
        pu, pv = _uv(ctx, pm + dp if dp is not None else pm)
        for sx in (1, -1):
            text, sym = _spot_boxes(ctx, pu, pv, 'down', sx)
            score = 10.0 * _hits(ctx, text) + 4.0 * _hits(ctx, sym) + 0.15 * k + (0.0 if sx == 1 else 0.1)
            if best is None or score < best[0]:
                best = (score, pm, sx, (text, sym))
    score, pm, sx, boxes = best
    role = None
    if label == 'noelev':
        role = 'axis' if _is_round(el) else 'bottom'
    tag = elev_marks.place(doc, ctx.view, el, ref, pm, 'R' if sx == 1 else 'L', None, ctx.disp, role)
    ctx.occupied.extend(boxes)
    return tag, score


def run(doc, uidoc, settings=None):
    settings = settings or {}
    view = doc.ActiveView
    report = []
    if not isinstance(view, View3D):
        return u"Нужен 3D-вид (взрыв-схема)."
    ctx = Ctx()
    ctx.view = view
    ctx.R, ctx.U = view.RightDirection, view.UpDirection
    ctx.mm = view.Scale / FT_MM
    mm = ctx.mm
    ctx.disp = _disp_map(doc, view)

    names = {'down': settings.get('spot_down', u"db_Стрелка_Проектная_Схемы_Вниз"),
             'up': settings.get('spot_up', u"db_Стрелка_Проектная_Схемы_Вверх"),
             'wdown': settings.get('spot_wdown', u"db_Стрелка_Проектная_Схемы_Белая выноска_Вниз"),
             'wup': settings.get('spot_wup', u"db_Стрелка_Проектная_Схемы_Белая выноска_Вверх")}
    types = {}
    for t in FilteredElementCollector(doc).OfClass(SpotDimensionType):
        try:
            nm = t.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
        except Exception:
            continue
        for k, v in names.items():
            if nm == v:
                types[k] = t
    import elev_marks
    if len(elev_marks.mark_types(doc)) < 4:
        return u"В проект не загружено семейство марки «{}».".format(elev_marks.FAMILY)

    filt = None
    fexc = None
    for fid in view.GetFilters():
        f = doc.GetElement(fid)
        if isinstance(f, ParameterFilterElement):
            if f.Name == settings.get('f_inc'):
                filt = f
            if f.Name == settings.get('f_exc'):
                fexc = f

    def passes(f, e):
        try:
            if _cat_int(e) not in set(_iv(c) for c in f.GetCategories()):
                return False
            ef = f.GetElementFilter()
            return True if ef is None else ef.PassesFilter(e)
        except Exception:
            return False

    # занятые места: головы марок, существующие отметки
    occupied = []
    for t in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
        try:
            hu, hv = _uv(ctx, t.TagHeadPosition)
            sym_ = doc.GetElement(t.GetTypeId())
            tn = sym_.FamilyName + sym_.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
            nl = 3 if u"Отметка" in tn else 1
            txt = t.TagText or u""
            if nl == 3:
                w = 13 * mm
            else:
                w = max(len(txt) * 1.85 * mm + 2 * mm, 6 * mm)
            occupied.append((hu - w / 2, hv - 2.4 * nl * mm, hu + w / 2, hv + 2.4 * nl * mm))
        except Exception:
            pass
    has_spot = {}
    for sd in FilteredElementCollector(doc, view.Id).OfClass(SpotDimension):
        try:
            for r in sd.References:
                has_spot.setdefault(_iv(r.ElementId), []).append(sd.Origin)
                p0 = sd.LeaderShoulderPosition if sd.HasLeader else sd.Origin
                tu, tv = _uv(ctx, p0)
                occupied.append((tu - 12 * mm, tv - 4 * mm, tu + 12 * mm, tv + 4 * mm))
                break
        except Exception:
            pass
    try:
        import elev_marks as _em
        for _t in _em.mark_tags(doc, view):
            _info = _em.read_entity(_t)
            if _info:
                has_spot.setdefault(_iv(_info[0]), []).append(_info[2])
            hu, hv = _uv(ctx, _t.TagHeadPosition)
            occupied.append((hu - 13 * mm, hv - 9 * mm, hu + 13 * mm, hv + 0.5 * mm))
    except Exception:
        pass
    ctx.occupied = occupied

    # элементы, у которых высота уже читается по марке (типы "Отметка верха / ... низа")
    elev_tagged = set()
    for t in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
        try:
            sym_ = doc.GetElement(t.GetTypeId())
            tn = sym_.FamilyName + sym_.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
            if u"Отметка" in tn:
                for eid in t.GetTaggedLocalElementIds():
                    elev_tagged.add(_iv(eid))
        except Exception:
            pass

    def is_new(e):          # текущий объём в монтаж (розовый)
        return (filt is None or passes(filt, e)) and not (fexc is not None and passes(fexc, e))

    def is_old(e):          # ранее выданный объём (зелёный)
        return fexc is not None and passes(fexc, e)

    curves_v, curves_h, curves_v_old = [], [], []
    for e in FilteredElementCollector(doc, view.Id).OfClass(MEPCurve):
        if _cat_int(e) not in CURVE_CATS or not isinstance(e.Location, LocationCurve):
            continue
        c = e.Location.Curve
        if is_new(e):
            if _is_vertical(c):
                curves_v.append(e)
            elif _is_horizontal(c):
                curves_h.append(e)
        elif is_old(e) and _is_vertical(c):
            curves_v_old.append(e)

    made = {'joint': 0, 'noelev': 0, 'unit': 0}
    skipped, failed, crowded = 0, [], []
    done_pts = []

    def near_done(p):
        for q in done_pts:
            if q.DistanceTo(p) < 0.2:
                return True
        return False

    def reaches(e, conn, pred):
        """Доходит ли соединение conn элемента e (через фитинги/арматуру) до элемента pred."""
        seen = set([_iv(e.Id)])
        frontier = [r.Owner for r in conn.AllRefs if r.Owner is not None] if conn.IsConnected else []
        depth = 0
        while frontier and depth < 4:
            nxt = []
            for o in frontier:
                if _iv(o.Id) in seen:
                    continue
                seen.add(_iv(o.Id))
                if pred(o):
                    return True
                if _cat_int(o) in FIT_CATS:
                    for fc in _connectors(o):
                        if fc.IsConnected:
                            nxt.extend(r.Owner for r in fc.AllRefs if r.Owner is not None)
            frontier = nxt
            depth += 1
        return False

    def spot_at_end(e, conn, kind):
        pt = conn.Origin
        if near_done(pt):
            return
        if any(abs(o.Z - pt.Z) < 0.05 for o in has_spot.get(_iv(e.Id), [])):
            done_pts.append(pt)
            return
        hit = _end_edge(ctx, e, pt)
        if hit is None:
            failed.append(_iv(e.Id))
            return
        edge, pm = hit
        try:
            sd, sc = _place(ctx, doc, edge.Reference, [pm], e, types, kind)
            made[kind] += 1
            done_pts.append(pt)
            if sc >= 10:
                crowded.append(_iv(sd.Id))
        except Exception:
            failed.append(_iv(e.Id))

    # 1) вертикальные участки на стыке ранее выданного объёма (зелёный) с текущим (розовый)
    if settings.get('joints', True) and fexc is not None:
        for e in curves_v:
            for conn in _connectors(e):
                if conn.ConnectorType == ConnectorType.End and reaches(e, conn, is_old):
                    spot_at_end(e, conn, 'joint')

    # 2) горизонтальные участки, у которых нет марки с отметками
    if settings.get('noelev', True):
        for grp in _section_groups(ctx, curves_h):
            if any(_iv(g.Id) in elev_tagged for g in grp):
                continue
            if any(_iv(g.Id) in has_spot for g in grp):
                skipped += 1
                continue
            grp.sort(key=lambda g: -g.Location.Curve.Length)
            ok = False
            for el in grp[:3]:
                c = el.Location.Curve
                try:
                    hit = _axis_ref(ctx, el) if _is_round(el) else _bottom_edge(ctx, el, c)
                except Exception:
                    hit = None
                if hit is None:
                    continue
                ref, line = hit
                pts = [line.Evaluate(t, True) for t in (0.5, 0.35, 0.65, 0.2, 0.8)]
                try:
                    sd, sc = _place(ctx, doc, ref, pts, el, types, 'noelev',
                                    'axis' if _is_round(el) else 'bottom')
                    made['noelev'] += 1
                    if sc >= 10:
                        crowded.append(_iv(sd.Id))
                    ok = True
                    break
                except Exception:
                    continue
            if not ok:
                failed.append(_iv(grp[0].Id))

    # 3) канальные блоки кондиционирования - нижняя точка
    if settings.get('units', True):
        keys = settings.get('unit_keys', [u"Indoor", u"Кондиционер"])
        must = settings.get('unit_must', [u"Duct", u"Канальн"])
        for e in FilteredElementCollector(doc, view.Id).OfCategory(
                BuiltInCategory.OST_MechanicalEquipment).OfClass(FamilyInstance):
            fn = e.Symbol.FamilyName + u" " + _ename_t(e.Symbol)
            if not (any(k in fn for k in keys) and any(k in fn for k in must)):
                continue
            if _iv(e.Id) in has_spot:
                skipped += 1
                continue
            hit = _bottom_face(e)
            if hit is None:
                failed.append(_iv(e.Id))
                continue
            ref, pm = hit
            try:
                sd, sc = _place(ctx, doc, ref, [pm], e, types, 'unit')
                made['unit'] += 1
                if sc >= 10:
                    crowded.append(_iv(sd.Id))
            except Exception:
                failed.append(_iv(e.Id))

    report.append(u"Отметок на стыках с ранее выданным объёмом: {}".format(made['joint']))
    report.append(u"Отметок на участках без отметок в марках: {}".format(made['noelev']))
    report.append(u"Отметок на канальных блоках (низ): {}".format(made['unit']))
    if skipped:
        report.append(u"Уже были: {}".format(skipped))
    if crowded:
        report.append(u"Без свободного места (проверить): {} ({})".format(
            len(crowded), u",".join(str(x) for x in crowded[:30])))
    if failed:
        report.append(u"Не удалось: {} ({})".format(len(failed), u",".join(str(x) for x in failed[:20])))
    return u"\n".join(report)


def _ename_t(t):
    try:
        return t.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString() or u""
    except Exception:
        return u""


def _bottom_face(el):
    """Нижняя горизонтальная грань семейства. Ссылка - из геометрии символа
    (из геометрии экземпляра Revit её для отметки не принимает), точка -
    пересчитана в координаты проекта (исходное положение элемента)."""
    opt = Options()
    opt.ComputeReferences = True
    best = None
    geo = el.get_Geometry(opt)
    if geo is None:
        return None
    for g in geo:
        if not isinstance(g, GeometryInstance):
            continue
        tr = g.Transform
        for h in g.GetSymbolGeometry():
            if not isinstance(h, Solid) or h.Faces.Size == 0:
                continue
            for f in h.Faces:
                if not isinstance(f, PlanarFace) or f.Reference is None:
                    continue
                if tr.OfVector(f.FaceNormal).Z > -0.99:
                    continue
                try:
                    m = f.Triangulate()
                    p = (m.Vertices[0] + m.Vertices[1] + m.Vertices[2]) / 3.0
                except Exception:
                    p = f.Origin
                p = tr.OfPoint(p)
                if best is None or p.Z < best[0] - 1e-3 or (abs(p.Z - best[0]) < 1e-3 and f.Area > best[1]):
                    best = (p.Z, f.Area, f.Reference, p)
    if best is None:
        return None
    return best[2], best[3]
