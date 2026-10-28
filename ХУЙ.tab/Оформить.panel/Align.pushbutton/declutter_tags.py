# -*- coding: utf-8 -*-
"""
«Развести наложенные марки» — команда кнопки «По красоте».

Марки, которые наезжают на другие марки, марки помещений/пространств,
размеры, отметки и тексты, переносятся на ближайшее свободное место
с выноской. Трубы, воздуховоды, лотки и короба — «мягкое» препятствие:
свободное место без пересечения с ними предпочтительнее, но если такого
нет, марка может лечь поверх трубы. Марки, которые ни на что не
наезжают, остаются на месте.

Вызов из script.py:   declutter_tags.run(doc, uidoc, tags)
Ядро (без окон и транзакций, нужна открытая Transaction):
                      declutter_tags.declutter_view(doc, view, only_ids=None)
"""
import math

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, CategoryType, Dimension,
    FilteredElementCollector, IndependentTag, LeaderEndCondition,
    LocationCurve, SpatialElementTag, SpotDimension, SubTransaction,
    TextNote, Transaction, TransactionGroup, View3D, ViewPlan, ViewSection,
    ViewType, XYZ, ElementId,
)

# ------------------------------------------------------------------
# Настройки (мм на бумаге)
# ------------------------------------------------------------------
GAP_MM = 0.8          # зазор между марками
STEP_MM = 2.0         # шаг поиска свободного места
MAX_R_MM = 40.0       # дальше этого марку не уносим
DIRS = 24             # направлений поиска
CURVE_PENALTY = 12.0  # «цена» пересечения трубы/воздуховода (в мм пути)
LEADER_PENALTY = 6.0  # «цена» выноски, проходящей через другую марку
LEADER_MIN_MM = 3.0   # сдвиг меньше — выноску не включаем
PASSES = 3            # проходов с проверкой результата
BIG_DIM_MM = 60.0     # размер с рамкой больше этого по обеим осям — только по линии

MEP_CATS = [
    BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_DuctCurves,
    BuiltInCategory.OST_FlexPipeCurves, BuiltInCategory.OST_FlexDuctCurves,
    BuiltInCategory.OST_CableTray, BuiltInCategory.OST_Conduit,
]

OK_VIEW_TYPES = (ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan,
                 ViewType.AreaPlan, ViewType.Section, ViewType.Elevation,
                 ViewType.Detail)


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


# ------------------------------------------------------------------
# Геометрия в координатах вида (u — вправо, v — вверх), футы модели
# ------------------------------------------------------------------
class _Frame(object):
    def __init__(self, view):
        self.R = view.RightDirection
        self.U = view.UpDirection
        self.O = view.Origin
        self.mm = view.Scale / 304.8   # 1 мм бумаги в футах модели

    def uv(self, p):
        d = p - self.O
        return (d.DotProduct(self.R), d.DotProduct(self.U))

    def xyz(self, u, v, z_from):
        """Точка вида -> XYZ; глубину берём от z_from."""
        n = self.R.CrossProduct(self.U)
        depth = (z_from - self.O).DotProduct(n)
        return self.O + self.R * u + self.U * v + n * depth

    def box(self, bb):
        if bb is None:
            return None
        us, vs = [], []
        for x in (bb.Min.X, bb.Max.X):
            for y in (bb.Min.Y, bb.Max.Y):
                for z in (bb.Min.Z, bb.Max.Z):
                    u, v = self.uv(XYZ(x, y, z))
                    us.append(u)
                    vs.append(v)
        return [min(us), min(vs), max(us), max(vs)]


def _ov(a, b, g):
    return a[0] < b[2] + g and b[0] < a[2] + g and a[1] < b[3] + g and b[1] < a[3] + g


def _seg_hits_box(p, q, b, pad):
    """Отрезок p-q (u,v) пересекает прямоугольник b, расширенный на pad (Лианг–Барски)."""
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x0 - (b[0] - pad)), (dx, (b[2] + pad) - x0),
                   (-dy, y0 - (b[1] - pad)), (dy, (b[3] + pad) - y0)):
        if abs(pp) < 1e-12:
            if qq < 0:
                return False
            continue
        r = qq / pp
        if pp < 0:
            if r > t1:
                return False
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return False
            if r < t1:
                t1 = r
    return True


# ------------------------------------------------------------------
# Сбор данных вида
# ------------------------------------------------------------------
def _tags(doc, view):
    return [t for t in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag)]


def _measure_heads(doc, view, fr, tags):
    """Рамка текста марки относительно её головы. Ничего не меняет.

    Грабли Revit: рамка марки включает конец выноски, даже если выноска
    выключена (HasLeader = False). Поэтому на время замера выноска
    ВКЛЮЧАЕТСЯ, её конец и излом ставятся вплотную к голове — тогда рамка
    равна рамке текста. Всё откатывается.
    """
    rel = {}
    tiny = 0.05 * fr.mm
    heads = dict((_iv(t.Id), t.TagHeadPosition) for t in tags)
    st = SubTransaction(doc)
    st.Start()
    try:
        for t in tags:
            try:
                h = t.TagHeadPosition
                if not t.HasLeader:
                    t.HasLeader = True
                if t.LeaderEndCondition != LeaderEndCondition.Free:
                    t.LeaderEndCondition = LeaderEndCondition.Free
                for r in t.GetTaggedReferences():
                    t.SetLeaderEnd(r, h + fr.R * tiny)
                    try:
                        if t.HasLeaderElbow(r):
                            t.SetLeaderElbow(r, h)
                    except Exception:
                        pass
            except Exception:
                pass
        doc.Regenerate()
        for t in tags:
            b = fr.box(t.get_BoundingBox(view))
            if b is None:
                continue
            hu, hv = fr.uv(heads[_iv(t.Id)])
            rel[_iv(t.Id)] = (b[0] - hu, b[1] - hv, b[2] - hu, b[3] - hv)
    finally:
        st.RollBack()
    return rel


def _hard_obstacles(doc, view, fr, skip_ids):
    """Марки помещений/пространств, размеры, отметки, тексты, неподвижные марки."""
    out = []
    lim = BIG_DIM_MM * fr.mm
    for e in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        if _iv(e.Id) in skip_ids:
            continue
        cat = e.Category
        if cat is None or cat.CategoryType != CategoryType.Annotation:
            continue
        if not isinstance(e, (SpatialElementTag, Dimension, TextNote, IndependentTag)):
            continue
        if isinstance(e, Dimension) and not isinstance(e, SpotDimension):
            # длинная цепочка по диагонали дала бы огромную рамку — берём линию размера
            b = fr.box(e.get_BoundingBox(view))
            if b is None:
                continue
            if (b[2] - b[0]) > lim and (b[3] - b[1]) > lim:
                continue
            out.append(b)
            continue
        b = fr.box(e.get_BoundingBox(view))
        if b is not None:
            out.append(b)
    return out


def _half_width(e):
    for bip in (BuiltInParameter.RBS_PIPE_OUTER_DIAMETER, BuiltInParameter.RBS_CURVE_DIAMETER_PARAM,
                BuiltInParameter.RBS_CURVE_WIDTH_PARAM, BuiltInParameter.RBS_CABLETRAY_WIDTH_PARAM,
                BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM):
        try:
            p = e.get_Parameter(bip)
            if p is not None and p.HasValue and p.AsDouble() > 0:
                return p.AsDouble() / 2.0
        except Exception:
            pass
    return 0.0


def _mep_segments(doc, view, fr):
    segs = []
    for bic in MEP_CATS:
        try:
            col = FilteredElementCollector(doc, view.Id).OfCategory(bic).WhereElementIsNotElementType()
        except Exception:
            continue
        for e in col:
            loc = e.Location
            if not isinstance(loc, LocationCurve):
                continue
            try:
                c = loc.Curve
                p, q = fr.uv(c.GetEndPoint(0)), fr.uv(c.GetEndPoint(1))
            except Exception:
                continue
            segs.append((p, q, _half_width(e)))
    return segs


def _anchor(t, fr):
    """Точка, куда смотрит выноска (конец выноски или голова)."""
    try:
        for r in t.GetTaggedReferences():
            if t.HasLeader and t.LeaderEndCondition == LeaderEndCondition.Free:
                return fr.uv(t.GetLeaderEnd(r))
    except Exception:
        pass
    try:
        for el in t.GetTaggedLocalElements():
            loc = el.Location
            if isinstance(loc, LocationCurve):
                c = loc.Curve
                return fr.uv(c.Evaluate(0.5, True))
            try:
                return fr.uv(loc.Point)
            except Exception:
                pass
    except Exception:
        pass
    return fr.uv(t.TagHeadPosition)


# ------------------------------------------------------------------
# Раскладка одного вида
# ------------------------------------------------------------------
def _plan(doc, view, fr, tags, movable, rel):
    """Возвращает {id: (u, v)} новых голов и список марок без места."""
    mm = fr.mm
    g = GAP_MM * mm
    ids_all = set(_iv(t.Id) for t in tags)
    hard = _hard_obstacles(doc, view, fr, ids_all)
    # неподвижные марки этого вида — тоже жёсткие препятствия
    for t in tags:
        i = _iv(t.Id)
        if i not in movable and i in rel:
            hu, hv = fr.uv(t.TagHeadPosition)
            r = rel[i]
            hard.append([hu + r[0], hv + r[1], hu + r[2], hv + r[3]])
    segs = _mep_segments(doc, view, fr)

    cur = {}
    for t in tags:
        i = _iv(t.Id)
        if i in movable and i in rel:
            hu, hv = fr.uv(t.TagHeadPosition)
            r = rel[i]
            cur[i] = (hu, hv, [hu + r[0], hv + r[1], hu + r[2], hv + r[3]])

    # порядок: сверху вниз, слева направо
    order = sorted(cur.keys(), key=lambda i: (-cur[i][1], cur[i][0]))
    placed = []
    done = set()
    moves, failed = {}, []
    tag_by_id = dict((_iv(t.Id), t) for t in tags)

    def curve_hits(b):
        n = 0
        for p, q, hw in segs:
            if _seg_hits_box(p, q, b, hw * 0.5):
                n += 1
        return n

    for i in order:
        hu, hv, me = cur[i]
        others = [cur[j][2] for j in order if j != i and j not in done]
        blocked = any(_ov(me, b, g) for b in hard) or any(_ov(me, b, g) for b in placed)
        if not blocked:
            # наезд на ещё не разложенную марку решает та, что ниже по списку
            placed.append(me)
            done.add(i)
            continue
        r = rel[i]
        au, av = _anchor(tag_by_id[i], fr)
        best, best_cost = None, None
        rad = STEP_MM
        while rad <= MAX_R_MM + 1e-6:
            for k in range(DIRS):
                a = 2 * math.pi * k / DIRS
                nu, nv = hu + math.cos(a) * rad * mm, hv + math.sin(a) * rad * mm
                bx = [nu + r[0], nv + r[1], nu + r[2], nv + r[3]]
                if any(_ov(bx, b, g) for b in hard) or any(_ov(bx, b, g) for b in placed):
                    continue
                if any(_ov(bx, b, g) for b in others):
                    continue
                cost = rad + CURVE_PENALTY * curve_hits(bx)
                if best_cost is not None and cost >= best_cost:
                    continue
                # выноска от точки элемента к ближайшему краю рамки
                eu = min(max(au, bx[0]), bx[2])
                ev = min(max(av, bx[1]), bx[3])
                lh = sum(1 for b in placed if _seg_hits_box((au, av), (eu, ev), b, 0))
                cost += LEADER_PENALTY * lh
                if best_cost is None or cost < best_cost:
                    best, best_cost = (nu, nv, bx), cost
            # чистое место на этом радиусе найдено — дальше не ищем
            if best is not None and best_cost <= rad + 1e-6:
                break
            rad += STEP_MM
        if best is None:
            failed.append(i)
            placed.append(me)
        else:
            moves[i] = (best[0], best[1])
            placed.append(best[2])
        done.add(i)
    return moves, failed


def _apply(doc, view, fr, moves):
    mm = fr.mm
    for i, (nu, nv) in moves.items():
        t = doc.GetElement(ElementId(i))
        old = t.TagHeadPosition
        new = fr.xyz(nu, nv, old)
        dist = old.DistanceTo(new) / mm
        try:
            if not t.HasLeader and dist >= LEADER_MIN_MM:
                t.HasLeader = True
        except Exception:
            pass
        t.TagHeadPosition = new
        # излом выноски, оставшийся на старом месте, убираем
        try:
            for r in t.GetTaggedReferences():
                if t.HasLeaderElbow(r):
                    t.SetLeaderElbow(r, new)
        except Exception:
            pass


def _count_overlaps(doc, view, fr, tags, movable):
    rel = _measure_heads(doc, view, fr, tags)
    g = GAP_MM * fr.mm * 0.5
    ids_all = set(_iv(t.Id) for t in tags)
    hard = _hard_obstacles(doc, view, fr, ids_all)
    boxes = {}
    for t in tags:
        i = _iv(t.Id)
        if i in rel:
            hu, hv = fr.uv(t.TagHeadPosition)
            r = rel[i]
            boxes[i] = [hu + r[0], hv + r[1], hu + r[2], hv + r[3]]
    bad = set()
    keys = list(boxes.keys())
    for a in range(len(keys)):
        ia = keys[a]
        for b in range(a + 1, len(keys)):
            ib = keys[b]
            if _ov(boxes[ia], boxes[ib], g):
                if ia in movable:
                    bad.add(ia)
                if ib in movable:
                    bad.add(ib)
        if ia in movable and any(_ov(boxes[ia], h, g) for h in hard):
            bad.add(ia)
    return bad, rel


def declutter_view(doc, view, only_ids=None):
    """Ядро. Нужна открытая транзакция. Возвращает словарь с итогами."""
    fr = _Frame(view)
    tags = _tags(doc, view)
    res = {'view': view.Name, 'tags': len(tags), 'moved': set(), 'left': 0}
    if not tags:
        return res
    movable = set(_iv(t.Id) for t in tags)
    if only_ids:
        movable &= set(only_ids)
    bad, rel = _count_overlaps(doc, view, fr, tags, movable)
    for _ in range(PASSES):
        if not bad:
            break
        moves, failed = _plan(doc, view, fr, tags, bad, rel)
        if not moves:
            break
        _apply(doc, view, fr, moves)
        doc.Regenerate()
        res['moved'].update(moves.keys())
        bad, rel = _count_overlaps(doc, view, fr, tags, movable)
    res['left'] = len(bad)
    res['moved'] = len(res['moved'])
    return res


# ------------------------------------------------------------------
# Кнопка
# ------------------------------------------------------------------
def _usable(v):
    try:
        return (not v.IsTemplate) and v.ViewType in OK_VIEW_TYPES
    except Exception:
        return False


class _VItem(object):
    def __init__(self, v):
        self.view = v
        self.name = v.Name

    def __str__(self):
        return self.name


def run(doc, uidoc, sel_tags=None):
    import hui_forms as forms
    view = doc.ActiveView
    A_SEL = u"Только выделенные марки ({})".format(len(sel_tags or []))
    A_CUR = u"Все марки на текущем виде"
    A_MANY = u"Все марки на нескольких видах…"
    opts = []
    if sel_tags:
        opts.append(A_SEL)
    if _usable(view):
        opts.append(A_CUR)
    opts.append(A_MANY)
    ch = forms.CommandSwitchWindow.show(
        opts, message=u"Развести наложенные марки: где?")
    if not ch:
        return
    only = None
    if ch == A_SEL:
        views = [view]
        only = set(_iv(t.Id) for t in sel_tags)
    elif ch == A_CUR:
        views = [view]
    else:
        allv = [v for v in FilteredElementCollector(doc).OfClass(ViewPlan) if _usable(v)]
        allv += [v for v in FilteredElementCollector(doc).OfClass(ViewSection) if _usable(v)]
        allv = sorted(allv, key=lambda v: v.Name)
        picked = forms.SelectFromList.show(
            [_VItem(v) for v in allv], title=u"Виды для раскладки марок", name_attr="name",
            multiselect=True, button_name=u"Развести")
        if not picked:
            return
        views = [p.view for p in picked]
    if not _usable(view) and ch != A_MANY:
        forms.alert(u"Команда работает на планах, разрезах и фасадах.",
                    title=u"Развести наложенные марки")
        return

    tg = TransactionGroup(doc, u"Развести наложенные марки")
    tg.Start()
    lines = []
    try:
        for v in views:
            t = Transaction(doc, u"Развести марки: " + v.Name)
            t.Start()
            try:
                r = declutter_view(doc, v, only)
                t.Commit()
            except Exception:
                t.RollBack()
                raise
            line = u"{}: марок {}, перенесено {}".format(r['view'], r['tags'], r['moved'])
            if r['left']:
                line += u", осталось наложений {} — поправьте вручную".format(r['left'])
            lines.append(line)
        tg.Assimilate()
    except Exception:
        tg.RollBack()
        raise
    forms.alert(u"\n".join(lines) + u"\n\nОтменить всё — Ctrl+Z (одна операция).",
                title=u"Развести наложенные марки")
