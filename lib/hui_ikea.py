# -*- coding: utf-8 -*-
"""
IKEA — сборочные схемы узлов (ХУЙ -> Взорвать).

Идея как у инструкций IKEA: узел показывается в сборе, рядом — тот же узел,
разобранный на детали вдоль своих соединений; у каждой детали марка-позиция,
под схемой — ведомость с картинками деталей.

Основные функции
  node(doc, elements, name, ...)      — 3D-виды «в сборе» и «разнесённый», фильтр узла,
                                        марки позиций, ведомость с картинками, лист (по желанию);
  explode_by_connectors(...)          — разнос по соединителям от базового элемента
                                        (шаг по размеру деталей, трубы: на месте / с узлом / скрыть);
  explode_floors(doc, view, upper)    — для систем: оба этажа на одном виде, верхний этаж
                                        разнесён вверх, марки верхнего этажа берутся с поэтажного вида;
  type_images(doc, types)             — картинки типов в «Изображение типа» (для ведомости).

Все функции работают внутри открытой транзакции вызывающего (кроме node — у него своя).
"""
import os
import clr
from System import Guid
from System.Collections.Generic import List
from Autodesk.Revit.DB import *

FT = 304.8
NODE_PARAM = u"db_Узел"
NODE_PARAM_GUID = "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e71"
POS_PARAMS = (u"ADSK_Позиция", u"db_Позиция")
NAME_PARAMS = (u"ADSK_Наименование", u"db_Наименование")
MARK_PARAMS = (u"ADSK_Марка", u"db_Марка")

MEP_CATS = [BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_PipeFitting,
            BuiltInCategory.OST_PipeAccessory, BuiltInCategory.OST_MechanicalEquipment,
            BuiltInCategory.OST_PlumbingFixtures, BuiltInCategory.OST_PlumbingEquipment,
            BuiltInCategory.OST_FlexPipeCurves, BuiltInCategory.OST_DuctCurves,
            BuiltInCategory.OST_DuctFitting, BuiltInCategory.OST_DuctAccessory,
            BuiltInCategory.OST_GenericModel, BuiltInCategory.OST_PipeInsulations]
CURVE_CATS = (int(BuiltInCategory.OST_PipeCurves), int(BuiltInCategory.OST_DuctCurves),
              int(BuiltInCategory.OST_FlexPipeCurves), int(BuiltInCategory.OST_FlexDuctCurves))


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _cat_list(bics):
    lst = List[BuiltInCategory]()
    for b in bics:
        lst.Add(b)
    return lst


def _ids(items):
    lst = List[ElementId]()
    for i in items:
        lst.Add(i if isinstance(i, ElementId) else i.Id)
    return lst


def _p(el, names):
    for nm in names:
        p = el.LookupParameter(nm)
        if p is not None and p.HasValue:
            v = p.AsString() if p.StorageType == StorageType.String else p.AsValueString()
            if v:
                return v
    t = el.Document.GetElement(el.GetTypeId())
    if t is not None:
        for nm in names:
            p = t.LookupParameter(nm)
            if p is not None and p.HasValue:
                v = p.AsString() if p.StorageType == StorageType.String else p.AsValueString()
                if v:
                    return v
    return u""


def is_curve(el):
    return el.Category is not None and _iv(el.Category.Id) in CURVE_CATS


# ------------------------------------------------------------------ параметр узла
def ensure_node_param(doc):
    """Текстовый параметр экземпляра db_Узел у MEP-категорий (через временный ФОП)."""
    app = doc.Application
    cats = CategorySet()
    for b in MEP_CATS:
        try:
            cats.Insert(Category.GetCategory(doc, b))
        except Exception:
            pass
    bm = doc.ParameterBindings
    it = bm.ForwardIterator()
    while it.MoveNext():
        if it.Key.Name == NODE_PARAM:
            return True
    old = app.SharedParametersFilename
    tmp = os.path.join(os.environ['TEMP'], 'zb_fop_ikea.txt')
    with open(tmp, 'w') as f:
        f.write('')
    try:
        app.SharedParametersFilename = tmp
        fop = app.OpenSharedParameterFile()
        grp = fop.Groups.Create('IKEA')
        opt = ExternalDefinitionCreationOptions(NODE_PARAM, SpecTypeId.String.Text)
        opt.GUID = Guid(NODE_PARAM_GUID)
        dfn = grp.Definitions.Create(opt)
        bm.Insert(dfn, InstanceBinding(cats), GroupTypeId.Text)
    finally:
        try:
            app.SharedParametersFilename = old or ''
        except Exception:
            pass
    return True



def mark_node(elements, name):
    for el in elements:
        p = el.LookupParameter(NODE_PARAM)
        if p is not None and not p.IsReadOnly:
            p.Set(name)


def node_filter(doc, name):
    """Фильтр «всё, что НЕ этот узел» (для скрытия на видах узла)."""
    fname = u"IKEA_НЕ_" + name
    for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement):
        if f.Name == fname:
            return f
    pid = None
    bm = doc.ParameterBindings
    it = bm.ForwardIterator()
    while it.MoveNext():
        if it.Key.Name == NODE_PARAM:
            for sp in FilteredElementCollector(doc).OfClass(SharedParameterElement):
                if sp.Name == NODE_PARAM:
                    pid = sp.Id
    rule = ParameterFilterRuleFactory.CreateNotEqualsRule(pid, name)
    cats = List[ElementId]()
    for b in MEP_CATS:
        try:
            cats.Add(Category.GetCategory(doc, b).Id)
        except Exception:
            pass
    return ParameterFilterElement.Create(doc, fname, cats, ElementParameterFilter(rule))


# ------------------------------------------------------------------ виды
def _iso_type(doc):
    for t in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        if t.ViewFamily == ViewFamily.ThreeDimensional:
            return t
    return None


def _bbox(elements, margin_ft=0.5):
    mn = [1e9, 1e9, 1e9]
    mx = [-1e9, -1e9, -1e9]
    for el in elements:
        bb = el.get_BoundingBox(None)
        if bb is None:
            continue
        for i, a in enumerate((bb.Min.X, bb.Min.Y, bb.Min.Z)):
            mn[i] = min(mn[i], a)
        for i, a in enumerate((bb.Max.X, bb.Max.Y, bb.Max.Z)):
            mx[i] = max(mx[i], a)
    b = BoundingBoxXYZ()
    b.Min = XYZ(mn[0] - margin_ft, mn[1] - margin_ft, mn[2] - margin_ft)
    b.Max = XYZ(mx[0] + margin_ft, mx[1] + margin_ft, mx[2] + margin_ft)
    return b


def view_orientation(elements):
    """Вид как в инструкциях: сверху-спереди-слева на «лицо» главного прибора
    (самый большой элемент с FacingOrientation), чуть сбоку."""
    main = None
    for el in elements:
        if isinstance(el, FamilyInstance) and not is_curve(el):
            if main is None or _size(el) > _size(main):
                main = el
    f = XYZ(0, -1, 0)
    try:
        if main is not None:
            fo = XYZ(main.FacingOrientation.X, main.FacingOrientation.Y, 0)
            if fo.GetLength() > 0.3:
                f = fo.Normalize()
    except Exception:
        pass
    # прямоугольная изометрия ГОСТ 2.317: оси модели под 120°, вертикали строго вертикальны
    if abs(f.X) >= abs(f.Y):                 # «лицо» прибора — к ближайшей оси модели
        f = XYZ(1 if f.X > 0 else -1, 0, 0)
    else:
        f = XYZ(0, 1 if f.Y > 0 else -1, 0)
    look = f.Negate()                       # смотрим на лицо прибора
    side = XYZ.BasisZ.CrossProduct(look).Normalize()
    d = (look + side).Normalize()            # 45° между осями
    fwd = XYZ(d.X, d.Y, -0.70710678).Normalize()
    up = (XYZ.BasisZ - fwd.Multiply(XYZ.BasisZ.DotProduct(fwd))).Normalize()
    bb = _bbox(elements, 0)
    c = (bb.Min + bb.Max) / 2.0
    eye = c - fwd.Multiply(100.0)
    return ViewOrientation3D(eye, up, fwd)


def isolate_mep(doc, view):
    """На виде узла — только инженерные категории: архитектура, связи и подложки скрыты."""
    keep = set(int(b) for b in MEP_CATS) | set([int(BuiltInCategory.OST_PipeInsulations)])
    for c in doc.Settings.Categories:
        try:
            if c.CategoryType == CategoryType.Model and _iv(c.Id) not in keep and view.CanCategoryBeHidden(c.Id):
                view.SetCategoryHidden(c.Id, True)
            elif c.CategoryType == CategoryType.Model and _iv(c.Id) in keep:
                pass
            if c.Name.lower().find('.dwg') >= 0 and view.CanCategoryBeHidden(c.Id):
                view.SetCategoryHidden(c.Id, True)
        except Exception:
            pass
    links = [li.Id for li in FilteredElementCollector(doc).OfClass(RevitLinkInstance)]
    imps = [ii.Id for ii in FilteredElementCollector(doc).OfClass(ImportInstance) if not ii.ViewSpecific]
    for grp in (links, imps):
        if grp:
            try:
                view.HideElements(_ids(grp))
            except Exception:
                pass


def make_view(doc, name, elements, orient=None, scale=20, style=DisplayStyle.ShadingWithEdges):
    v = View3D.CreateIsometric(doc, _iso_type(doc).Id)
    v.ViewTemplateId = ElementId.InvalidElementId
    taken = set(x.Name for x in FilteredElementCollector(doc).OfClass(View3D))
    nm, k = name, 2
    while nm in taken:
        nm = u"%s (%d)" % (name, k)
        k += 1
    v.Name = nm
    v.SetOrientation(orient or view_orientation(elements))
    v.SetSectionBox(_bbox(elements, 0.3))
    v.IsSectionBoxActive = True
    v.Scale = scale
    v.DetailLevel = ViewDetailLevel.Fine
    v.DisplayStyle = style
    for b in (BuiltInCategory.OST_Levels, BuiltInCategory.OST_Grids, BuiltInCategory.OST_Sections,
              BuiltInCategory.OST_RvtLinks, BuiltInCategory.OST_VolumeOfInterest):
        try:
            v.SetCategoryHidden(ElementId(b), True)
        except Exception:
            pass
    isolate_mep(doc, v)
    try:
        p = v.get_Parameter(BuiltInParameter.VIEWER_MODEL_CLIP_BOX_ACTIVE)
    except Exception:
        p = None
    return v


def hide_box(view):
    try:
        view.get_Parameter(BuiltInParameter.MODEL_GRAPHICS_STYLE)  # noqa
        p = view.LookupParameter(u"Границы 3D вида")
        if p:
            p.Set(0)
    except Exception:
        pass


def hide_except(doc, view, keep_elements):
    """Скрыть на виде все модельные элементы, кроме keep_elements (после Regenerate)."""
    keep = set(_iv(e.Id) for e in keep_elements)
    other = []
    for e in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        try:
            if (e.Category is not None and e.Category.CategoryType == CategoryType.Model
                    and _iv(e.Id) not in keep and e.CanBeHidden(view)):
                other.append(e.Id)
        except Exception:
            pass
    if other:
        view.HideElements(_ids(other))
    return len(other)


def widen_box(view, elements, shift, margin_ft=0.5):
    """Рамка 3D-вида по деталям с учётом разноса (чтобы ничего не обрезалось)."""
    pts = []
    for el in elements:
        bb = el.get_BoundingBox(None)
        if bb is None:
            continue
        s = shift.get(_iv(el.Id), XYZ.Zero)
        pts += [bb.Min + s, bb.Max + s, bb.Min, bb.Max]
    b = BoundingBoxXYZ()
    b.Min = XYZ(min(p.X for p in pts) - margin_ft, min(p.Y for p in pts) - margin_ft, min(p.Z for p in pts) - margin_ft)
    b.Max = XYZ(max(p.X for p in pts) + margin_ft, max(p.Y for p in pts) + margin_ft, max(p.Z for p in pts) + margin_ft)
    view.SetSectionBox(b)


def internal_curves(elements, parts):
    """Трубы из elements, у которых все подключённые соединители ведут в parts
    (трубы внутри узла — разносятся вместе с деталями, как в ТМ2)."""
    ids = set(_iv(e.Id) for e in parts)
    res = []
    for e in elements:
        if not is_curve(e):
            continue
        owners = []
        for c in _connectors(e):
            if c.IsConnected:
                for r in c.AllRefs:
                    if r.Owner is not None and _iv(r.Owner.Id) != _iv(e.Id):
                        owners.append(_iv(r.Owner.Id))
        if len(owners) >= 2 and all(o in ids for o in owners):
            res.append(e)
    return res


# ------------------------------------------------------------------ разнос
def _connectors(el):
    mgr = None
    try:
        mgr = el.MEPModel.ConnectorManager
    except Exception:
        try:
            mgr = el.ConnectorManager
        except Exception:
            mgr = None
    if mgr is None:
        return []
    return [c for c in mgr.Connectors]


def _size(el):
    bb = el.get_BoundingBox(None)
    if bb is None:
        return 0.3
    d = bb.Max - bb.Min
    return max(d.X, d.Y, d.Z)


def _bfs(queue, shift, ids, step, by_size, pipes):
    while queue:
        cur = queue.pop(0)
        cs = shift[_iv(cur.Id)]
        for c in _connectors(cur):
            if not c.IsConnected:
                continue
            for r in c.AllRefs:
                nxt = r.Owner
                if nxt is None or _iv(nxt.Id) not in ids or _iv(nxt.Id) in shift:
                    continue
                d = c.CoordinateSystem.BasisZ
                k = step
                if by_size:
                    k = step + 0.35 * _size(nxt)
                if is_curve(nxt) and pipes == 'stay':
                    k = 0.0
                shift[_iv(nxt.Id)] = cs + d.Multiply(k)
                queue.append(nxt)


def explode_by_connectors(doc, view, elements, base=None, step_mm=50, by_size=True,
                          pipes='with', max_shift_mm=None):
    """Разнос узла на виде: от базового элемента по соединителям, каждая следующая
    деталь сдвигается дальше вдоль оси соединения. pipes: 'stay' — трубы на месте,
    'with' — едут с узлом, 'hide' — скрыть на виде. Возвращает {id: XYZ сдвиг}."""
    ids = set(_iv(e.Id) for e in elements)
    step = step_mm / FT
    shift = {}
    # каждая связная часть узла разносится от своей самой крупной детали
    rest = sorted([e for e in elements if not is_curve(e)] or list(elements), key=_size, reverse=True)
    if base is not None:
        rest.insert(0, base)
    queue = []
    for b in rest:
        if _iv(b.Id) in shift:
            continue
        shift[_iv(b.Id)] = XYZ.Zero
        queue = [b]
        _bfs(queue, shift, ids, step, by_size, pipes)
    hidden = []
    for el in elements:
        if _iv(el.Id) not in shift:
            shift[_iv(el.Id)] = XYZ.Zero
        if pipes == 'hide' and is_curve(el):
            hidden.append(el.Id)
    if hidden:
        view.HideElements(_ids(hidden))
    for el in elements:
        v = shift[_iv(el.Id)]
        if v.GetLength() < 1e-6 or el.Id in hidden:
            continue
        if not DisplacementElement.IsAllowedAsDisplacedElement(el):
            continue
        try:
            DisplacementElement.Create(doc, _ids([el.Id]), v, view, None)
        except Exception:
            pass
    return shift


def explode_floors(doc, view, upper_view, gap_mm=800, below_mm=-2000):
    """Оба этажа на одном 3D-виде, верхний этаж (что видно на upper_view и не видно
    на view) разнесён вверх без наложения; марки верхнего этажа переносятся
    с upper_view с сохранением раскладки. Возвращает (сдвиг, число элементов, марок)."""
    flt = ElementMulticategoryFilter(_cat_list(MEP_CATS))

    def vis(v):
        return set(_iv(e.Id) for e in FilteredElementCollector(doc, v.Id).WherePasses(flt)
                   .WhereElementIsNotElementType())
    sa = vis(view)
    upper = [i for i in vis(upper_view) if i not in sa]
    U = view.UpDirection

    def span(ids):
        vals = []
        for i in ids:
            el = doc.GetElement(ElementId(i))
            loc = el.Location
            if isinstance(loc, LocationCurve):
                vals += [loc.Curve.GetEndPoint(0).DotProduct(U), loc.Curve.GetEndPoint(1).DotProduct(U)]
            elif isinstance(loc, LocationPoint):
                vals.append(loc.Point.DotProduct(U))
        vals.sort()
        return vals[int(len(vals) * 0.01)], vals[int(len(vals) * 0.99)]
    lo1, hi1 = span(sa)
    lo2, hi2 = span(upper)
    D = XYZ(0, 0, (hi1 - lo2 + gap_mm / FT) / U.Z)
    ba, bb = view.GetSectionBox(), upper_view.GetSectionBox()
    nb = BoundingBoxXYZ()
    nb.Transform = ba.Transform
    nb.Min = XYZ(min(ba.Min.X, bb.Min.X), min(ba.Min.Y, bb.Min.Y), min(ba.Min.Z, below_mm / FT))
    nb.Max = XYZ(max(ba.Max.X, bb.Max.X), max(ba.Max.Y, bb.Max.Y), max(ba.Max.Z, bb.Max.Z))
    view.SetSectionBox(nb)
    ids = [ElementId(i) for i in upper if DisplacementElement.IsAllowedAsDisplacedElement(doc.GetElement(ElementId(i)))]
    DisplacementElement.Create(doc, _ids(ids), D, view, None)
    moved = set(_iv(i) for i in ids)
    n = 0
    for tg in list(FilteredElementCollector(doc, upper_view.Id).OfClass(IndependentTag)):
        try:
            tid = list(tg.GetTaggedLocalElementIds())[0]
        except Exception:
            continue
        if _iv(tid) not in moved:
            continue
        el = doc.GetElement(tid)
        p = _anchor(el)
        oref = list(tg.GetTaggedReferences())[0]
        k = float(upper_view.Scale) / view.Scale

        def mp(q, p=p):
            v = q - p
            return (p + D + view.RightDirection.Multiply(v.DotProduct(upper_view.RightDirection) * k)
                    + view.UpDirection.Multiply(v.DotProduct(upper_view.UpDirection) * k))
        head = mp(tg.TagHeadPosition)
        nt = IndependentTag.Create(doc, tg.GetTypeId(), view.Id, Reference(el), tg.HasLeader,
                                   tg.TagOrientation, head)
        if tg.HasLeader:
            nt.LeaderEndCondition = tg.LeaderEndCondition
            nr = list(nt.GetTaggedReferences())[0]
            try:
                if tg.LeaderEndCondition == LeaderEndCondition.Free:
                    nt.SetLeaderEnd(nr, mp(tg.GetLeaderEnd(oref)))
            except Exception:
                pass
            try:
                if tg.HasLeaderElbow(oref):
                    nt.SetLeaderElbow(nr, mp(tg.GetLeaderElbow(oref)))
            except Exception:
                pass
        nt.TagHeadPosition = head
        n += 1
    return D, len(ids), n


def _anchor(el):
    loc = el.Location
    if isinstance(loc, LocationCurve):
        return loc.Curve.Evaluate(0.5, True)
    if isinstance(loc, LocationPoint):
        return loc.Point
    bb = el.get_BoundingBox(None)
    return (bb.Min + bb.Max) / 2.0


# ------------------------------------------------------------------ марки и ведомость
def position_tag_type(doc):
    """Марка позиции: db_М_по_НК_ADSK_Позиция (как на Ко4), иначе любая многокатегорийная."""
    best = None
    for s in FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(BuiltInCategory.OST_MultiCategoryTags):
        if u"Позиция" in s.FamilyName:
            return s
        best = best or s
    return best


def tag_positions(doc, view, elements, shift, curves=False, dedupe=True):
    """Марки ADSK_Позиция. dedupe — одна марка на номер позиции (общая схема, ТМ1);
    dedupe=False и curves=True — марка у каждой детали и трубы (разнесённый узел, ТМ2)."""
    sym = position_tag_type(doc)
    if sym is None:
        return []
    seen, tags = set(), []
    for el in elements:
        if (is_curve(el) and not curves) or _p(el, POS_PARAMS) == u"":
            continue
        key = _p(el, POS_PARAMS) or str(_iv(el.Id))
        if dedupe and key in seen:   # одна марка на позицию
            continue
        seen.add(key)
        p = _anchor(el) + shift.get(_iv(el.Id), XYZ.Zero)
        head = p + view.UpDirection.Multiply(12.0 * view.Scale / FT) + view.RightDirection.Multiply(6.0 * view.Scale / FT)
        try:
            t = IndependentTag.Create(doc, sym.Id, view.Id, Reference(el), True, TagOrientation.Horizontal, head)
            tags.append(t)
        except Exception:
            pass
    return tags


def _extent(view, elements, shift):
    """Габарит видимой геометрии на плоскости вида (R, U) с учётом разноса."""
    R, U = view.RightDirection, view.UpDirection
    us, vs = [], []
    for el in elements:
        bb = el.get_BoundingBox(None)
        if bb is None:
            continue
        s = shift.get(_iv(el.Id), XYZ.Zero)
        for x in (bb.Min.X, bb.Max.X):
            for y in (bb.Min.Y, bb.Max.Y):
                for z in (bb.Min.Z, bb.Max.Z):
                    q = XYZ(x, y, z) + s
                    us.append(q.DotProduct(R))
                    vs.append(q.DotProduct(U))
    if not us:
        return None
    return min(us), max(us), min(vs), max(vs)


def _cross(a, b, c, d):
    """Пересекаются ли отрезки ab и cd (2D кортежи)."""
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (o(a, b, c) * o(a, b, d) < 0) and (o(c, d, a) * o(c, d, b) < 0)


def columns_layout(doc, view, tags, shift=None, gap_mm=12.0, step_mm=7.0, extent_elements=None):
    """Марки позиций в две колонки слева и справа от узла (как в сборочных схемах).
    Колонки ставятся за габаритом геометрии (extent_elements), порядок сверху вниз —
    по высоте детали, затем выноски распутываются перестановкой (без пересечений).
    shift — сдвиги деталей разнесённого вида {id: XYZ}."""
    if not tags:
        return
    shift = shift or {}
    R, U = view.RightDirection, view.UpDirection
    k = view.Scale / FT
    rows = []
    for t in tags:
        try:
            el = doc.GetElement(list(t.GetTaggedLocalElementIds())[0])
        except Exception:
            continue
        p = _anchor(el) + shift.get(_iv(el.Id), XYZ.Zero)
        rows.append((t, el, p, p.DotProduct(R), p.DotProduct(U)))
    if not rows:
        return
    ext = _extent(view, extent_elements, shift) if extent_elements else None
    us = sorted(r[3] for r in rows)
    vs = [r[4] for r in rows]
    if ext:
        umin, umax, vbot, vtop = ext
    else:
        umin, umax, vbot, vtop = us[0], us[-1], min(vs), max(vs)
    mid = (umin + umax) / 2.0
    for side in (-1, 1):
        col = [r for r in rows if (r[3] < mid) == (side < 0)]
        if not col:
            continue
        col.sort(key=lambda r: -r[4])
        x = (umin - gap_mm * k) if side < 0 else (umax + gap_mm * k)
        need = (len(col) - 1) * step_mm * k
        top = (vtop + vbot) / 2.0 + need / 2.0
        ys = [top - i * step_mm * k for i in range(len(col))]
        # распутать: меняем местами головы, пока выноски пересекаются
        for _ in range(30):
            changed = False
            for i in range(len(col)):
                for j in range(i + 1, len(col)):
                    a, b = col[i], col[j]
                    if _cross((x, ys[i]), (a[3], a[4]), (x, ys[j]), (b[3], b[4])):
                        col[i], col[j] = b, a
                        changed = True
            if not changed:
                break
        for i, (t, el, p, pu, pv) in enumerate(col):
            head = p + R.Multiply(x - pu) + U.Multiply(ys[i] - pv)
            try:
                t.TagHeadPosition = head
            except Exception:
                pass


def near_layout(doc, view, tags, shift=None, box_mm=(8.0, 5.0), dists_mm=(8, 12, 17, 23, 30)):
    """Марки рядом с деталями, короткие выноски (как ТМ1/ТМ2): для каждой марки
    перебираются направления (сначала — наружу от центра узла) и расстояния,
    берётся первое место, где рамка марки не налезает на уже поставленные
    марки и на точки привязки других деталей."""
    if not tags:
        return
    shift = shift or {}
    R, U = view.RightDirection, view.UpDirection
    k = view.Scale / FT
    rows = []
    for t in tags:
        try:
            el = doc.GetElement(list(t.GetTaggedLocalElementIds())[0])
        except Exception:
            continue
        p = _anchor(el) + shift.get(_iv(el.Id), XYZ.Zero)
        rows.append([t, p, p.DotProduct(R) / k, p.DotProduct(U) / k])   # в мм листа
    if not rows:
        return
    cu = sum(r[2] for r in rows) / len(rows)
    cv = sum(r[3] for r in rows) / len(rows)
    anchors = [(r[2], r[3]) for r in rows]
    w, h = box_mm[0] / 2.0 + 0.8, box_mm[1] / 2.0 + 0.8
    placed = []
    dirs0 = [(1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1),
             (2, 1), (-2, 1), (2, -1), (-2, -1)]
    rows.sort(key=lambda r: -r[3])
    for t, p, au, av in rows:
        ou, ov = au - cu, av - cv
        dirs = sorted(dirs0, key=lambda d: -(d[0] * ou + d[1] * ov) / ((d[0] ** 2 + d[1] ** 2) ** 0.5))
        best = None
        for dist in dists_mm:
            for du, dv in dirs:
                n = (du * du + dv * dv) ** 0.5
                hu, hv = au + du / n * dist, av + dv / n * dist
                if any(abs(hu - qu) < 2 * w and abs(hv - qv) < 2 * h for qu, qv in placed):
                    continue
                if any(abs(hu - qu) < w and abs(hv - qv) < h for qu, qv in anchors if (qu, qv) != (au, av)):
                    continue
                best = (hu, hv)
                break
            if best:
                break
        if best is None:
            best = (au + dists_mm[-1] * 1.3, av + dists_mm[-1] * 1.3)
        placed.append(best)
        head = p + R.Multiply((best[0] - au) * k) + U.Multiply((best[1] - av) * k)
        try:
            t.TagHeadPosition = head
        except Exception:
            pass


def type_images(doc, types, size=256):
    """Превью типа (как в «Распознать») -> картинка проекта -> параметр «Изображение типа»."""
    clr.AddReference('System.Drawing')
    from System.Drawing import Size
    from System.Drawing.Imaging import ImageFormat
    folder = os.path.join(os.environ['APPDATA'], 'ХУЙ_ikea')
    if not os.path.isdir(folder):
        os.makedirs(folder)
    n = 0
    for t in types:
        p = t.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_IMAGE)
        if p is None or p.IsReadOnly:
            continue
        if p.AsElementId() != ElementId.InvalidElementId:
            n += 1
            continue
        try:
            bmp = t.GetPreviewImage(Size(size, size))
            if bmp is None:
                continue
            path = os.path.join(folder, u'type_%d.png' % _iv(t.Id))
            bmp.Save(path, ImageFormat.Png)
            opt = ImageTypeOptions(path, False, ImageTypeSource.Import)
            it = ImageType.Create(doc, opt)
            p.Set(it.Id)
            n += 1
        except Exception:
            pass
    return n


def _parts(doc, e):
    """Элемент + все его вложенные общие семейства (рекурсивно) и родитель-контейнер."""
    keep, stack = set(), [e]
    while stack:
        x = stack.pop()
        if _iv(x.Id) in keep:
            continue
        keep.add(_iv(x.Id))
        try:
            for sid in x.GetSubComponentIds():
                stack.append(doc.GetElement(sid))
        except Exception:
            pass
    return keep


def render_images(doc, elements, px=300, scale=5, force=False):
    """Картинки деталей для ведомости (как в «Распознать», но из модели): для каждого
    типа один экземпляр отрисовывается на служебном 3D-виде «zb_img_tmp». На виде видна
    ТОЛЬКО эта деталь вместе с её вложенными семействами: все остальные элементы модели
    скрыты один раз заранее, на время снимка открывается только сама деталь (с 11.10.2026;
    раньше скрывались лишь соседи в рамке, и в картинки попадали чужие куски, а вложенные
    части самой детали пропадали). PNG -> картинка проекта -> параметр ЭКЗЕМПЛЯРА
    «Изображение» у всех экземпляров этого типа. Уже заполненные пропускаются, если не
    force=True. Возвращает число отрисованных типов. Медленно: ~1 с на тип."""
    folder = os.path.join(os.environ['APPDATA'], u'ХУЙ_ikea')
    if not os.path.isdir(folder):
        os.makedirs(folder)
    by_type = {}
    for e in elements:
        if is_curve(e):
            continue
        tid = e.GetTypeId()
        if _iv(tid) < 0:
            continue
        by_type.setdefault(_iv(tid), []).append(e)
    tmp = None
    for v in FilteredElementCollector(doc).OfClass(View3D):
        if v.Name == u"zb_img_tmp":
            tmp = v
    if tmp is None:
        tmp = View3D.CreateIsometric(doc, _iso_type(doc).Id)
        tmp.ViewTemplateId = ElementId.InvalidElementId
        tmp.Name = u"zb_img_tmp"
        tmp.DetailLevel = ViewDetailLevel.Fine
        tmp.DisplayStyle = DisplayStyle.ShadingWithEdges
        for c in doc.Settings.Categories:
            try:
                if c.CategoryType == CategoryType.Annotation and tmp.CanCategoryBeHidden(c.Id):
                    tmp.SetCategoryHidden(c.Id, True)
            except Exception:
                pass
    tmp.Scale = scale
    tmp.IsSectionBoxActive = False
    # всё видимое на служебном виде скрыть один раз (связи тоже)
    shown = [x.Id for x in FilteredElementCollector(doc, tmp.Id).WhereElementIsNotElementType()
             if x.Category is not None and x.CanBeHidden(tmp)
             and (x.Category.CategoryType == CategoryType.Model or isinstance(x, RevitLinkInstance))]
    if shown:
        tmp.HideElements(_ids(shown))
    fwd = XYZ(-1, 1, -0.8).Normalize()
    up = (XYZ.BasisZ - fwd.Multiply(XYZ.BasisZ.DotProduct(fwd))).Normalize()
    n = 0
    for tid, insts in by_type.items():
        e = insts[0]
        ip = e.get_Parameter(BuiltInParameter.ALL_MODEL_IMAGE)
        if ip is None or ip.IsReadOnly or (_iv(ip.AsElementId()) > 0 and not force):
            continue
        bb = e.get_BoundingBox(None)
        if bb is None:
            continue
        keep = [ElementId(i) if not isinstance(i, ElementId) else i for i in _parts(doc, e)]
        keep = [k for k in keep if doc.GetElement(k) is not None and doc.GetElement(k).IsHidden(tmp)]
        if keep:
            tmp.UnhideElements(_ids(keep))
        m = XYZ(0.1, 0.1, 0.1)
        box = BoundingBoxXYZ()
        box.Min, box.Max = bb.Min - m, bb.Max + m
        tmp.SetSectionBox(box)
        tmp.IsSectionBoxActive = True
        c = (bb.Min + bb.Max) / 2.0
        tmp.SetOrientation(ViewOrientation3D(c - fwd.Multiply(50.0), up, fwd))
        doc.Regenerate()
        name = u"zbimg_%d" % tid
        for f in os.listdir(folder):
            if f.startswith(name + u" ") or f == name + u".png":
                try:
                    os.remove(os.path.join(folder, f))
                except Exception:
                    pass
        o = ImageExportOptions()
        o.ExportRange = ExportRange.SetOfViews
        o.SetViewsAndSheets(_ids([tmp.Id]))
        o.FilePath = os.path.join(folder, name)
        o.HLRandWFViewsFileType = ImageFileType.PNG
        o.ShadowViewsFileType = ImageFileType.PNG
        o.ZoomType = ZoomFitType.FitToPage
        o.PixelSize = px
        o.ImageResolution = ImageResolution.DPI_150
        doc.ExportImage(o)
        if keep:
            tmp.HideElements(_ids(keep))
        files = [f for f in os.listdir(folder) if f.startswith(name) and f.lower().endswith('.png')]
        if not files:
            continue
        files.sort(key=lambda f: os.path.getmtime(os.path.join(folder, f)), reverse=True)
        clean = os.path.join(folder, name + u".png")
        src = os.path.join(folder, files[0])
        if src != clean:                         # имя экспорта «zbimg_N - 3D вид - ...» -> короткое
            if os.path.exists(clean):
                os.remove(clean)
            os.rename(src, clean)
        it = ImageType.Create(doc, ImageTypeOptions(clean, False, ImageTypeSource.Import))
        for x in insts:
            q = x.get_Parameter(BuiltInParameter.ALL_MODEL_IMAGE)
            if q is not None and not q.IsReadOnly:
                q.Set(it.Id)
        n += 1
    if shown:
        tmp.UnhideElements(_ids(shown))
    tmp.IsSectionBoxActive = False
    return n


def schedule(doc, name, with_pipes=False):
    """Ведомость узла: картинка, поз., наименование, марка, кол-во; только этот узел."""
    sch = ViewSchedule.CreateSchedule(doc, ElementId.InvalidElementId)
    taken = set(x.Name for x in FilteredElementCollector(doc).OfClass(ViewSchedule))
    nm, k = u"IKEA_Ведомость_" + name, 2
    while nm in taken:
        nm = u"IKEA_Ведомость_%s (%d)" % (name, k)
        k += 1
    sch.Name = nm
    d = sch.Definition
    fields = {}
    for sf in d.GetSchedulableFields():
        fields.setdefault(sf.GetName(doc), sf)
    img = None                               # картинка экземпляра (render_images), иначе типа
    for sf in d.GetSchedulableFields():
        # только поле самого элемента: у мультикатегорийной ведомости есть ещё
        # «Помещение: Изображение» и «Пространство: Изображение» (11.10.2026)
        if _iv(sf.ParameterId) == int(BuiltInParameter.ALL_MODEL_IMAGE) and sf.FieldType == ScheduleFieldType.Instance:
            img = sf.GetName(doc)
            break
    if img is None:
        img = u"Изображение типоразмера" if u"Изображение типоразмера" in fields else u"Изображение типа"
    want = [(img, u"Вид"), (POS_PARAMS[0], u"Поз."), (NAME_PARAMS[0], u"Наименование"),
            (MARK_PARAMS[0], u"Марка")]
    added = {}
    for fname, head in want:
        sf = fields.get(fname)
        if sf is None:
            continue
        f = d.AddField(sf)
        f.ColumnHeading = head
        added[fname] = f
    nf = fields.get(NODE_PARAM)
    if nf is not None:
        f = d.AddField(nf)
        f.IsHidden = True
        d.AddFilter(ScheduleFilter(f.FieldId, ScheduleFilterType.Equal, name))
    if POS_PARAMS[0] in added:
        d.AddFilter(ScheduleFilter(added[POS_PARAMS[0]].FieldId, ScheduleFilterType.HasValue))
        d.AddFilter(ScheduleFilter(added[POS_PARAMS[0]].FieldId, ScheduleFilterType.NotEqual, u""))
    if NAME_PARAMS[0] in added:
        nfid = added[NAME_PARAMS[0]].FieldId
        d.AddFilter(ScheduleFilter(nfid, ScheduleFilterType.HasValue))
        if not with_pipes:                   # трубы — в общей спецификации, не в ведомости узла
            d.AddFilter(ScheduleFilter(nfid, ScheduleFilterType.NotBeginsWith, u"Труба"))
    cat = [sf for sf in d.GetSchedulableFields()
           if sf.FieldType == ScheduleFieldType.Instance and (sf.GetName(doc) == u"Категория" or
              _iv(sf.ParameterId) in (int(BuiltInParameter.ELEM_CATEGORY_PARAM), int(BuiltInParameter.ELEM_CATEGORY_PARAM_MT)))]
    if cat and not with_pipes:               # соединительные детали — в общей спецификации
        try:
            cf0 = d.AddField(cat[0])
            cf0.IsHidden = True
            d.AddFilter(ScheduleFilter(cf0.FieldId, ScheduleFilterType.NotEqual,
                                       Category.GetCategory(doc, BuiltInCategory.OST_PipeFitting).Id))
        except Exception:
            pass
    try:                                     # кол-во = число экземпляров в строке
        cf = d.AddField(ScheduleFieldType.Count)
        cf.ColumnHeading = u"Кол."
    except Exception:
        pass
    if POS_PARAMS[0] in added:
        d.AddSortGroupField(ScheduleSortGroupField(added[POS_PARAMS[0]].FieldId))
    if NAME_PARAMS[0] in added:
        d.AddSortGroupField(ScheduleSortGroupField(added[NAME_PARAMS[0]].FieldId))
    d.IsItemized = False
    return sch


# ------------------------------------------------------------------ всё вместе
def node(doc, elements, name, base=None, step_mm=50, pipes='with', scale=20, orient=None,
         with_schedule=True, explode_elements=None, tag_assembled=True, layout='near'):
    """Полный цикл (внутри уже открытой транзакции): параметр узла, фильтр, вид
    «в сборе», вид «разнесённый», марки позиций, картинки деталей, ведомость.
    layout: 'near' — марки рядом с деталями, у каждой детали и трубы (как ТМ2;
    на виде «в сборе» марок нет); 'columns' — по одной марке на позицию, в две колонки."""
    explode_elements = list(explode_elements or elements)
    if orient is None:
        orient = view_orientation(elements)      # один ракурс для «в сборе» и «разнесённого»
    ensure_node_param(doc)
    mark_node(elements, name)
    flt = node_filter(doc, name)
    res = {}
    va = make_view(doc, u"IKEA_" + name + u"_сборка", elements, orient, scale)
    vb = make_view(doc, u"IKEA_" + name + u"_разнесённый", explode_elements, orient, scale)
    for v in (va, vb):
        v.AddFilter(flt.Id)
        v.SetFilterVisibility(flt.Id, False)
    shift = explode_by_connectors(doc, vb, explode_elements, base, step_mm, True, pipes)
    # рамка разнесённого вида шире: сдвинутые детали не должны обрезаться
    pts = []
    for el in explode_elements:
        bb = el.get_BoundingBox(None)
        if bb is None:
            continue
        s = shift.get(_iv(el.Id), XYZ.Zero)
        pts += [bb.Min + s, bb.Max + s, bb.Min, bb.Max]
    b = BoundingBoxXYZ()
    b.Min = XYZ(min(p.X for p in pts) - 0.5, min(p.Y for p in pts) - 0.5, min(p.Z for p in pts) - 0.5)
    b.Max = XYZ(max(p.X for p in pts) + 0.5, max(p.Y for p in pts) + 0.5, max(p.Z for p in pts) + 0.5)
    vb.SetSectionBox(b)
    doc.Regenerate()
    if len(explode_elements) < len(elements):
        hide_except(doc, vb, explode_elements)
    for v in (va, vb):
        try:
            v.SaveOrientationAndLock()
        except Exception:
            pass
    doc.Regenerate()
    if layout == 'near':
        ta = []
        tb = tag_positions(doc, vb, explode_elements, shift, curves=(pipes != 'hide'), dedupe=False)
        doc.Regenerate()
        near_layout(doc, vb, tb, shift)
    else:
        ta = tag_positions(doc, va, elements, {}) if tag_assembled else []
        tb = tag_positions(doc, vb, explode_elements, shift)
        doc.Regenerate()
        columns_layout(doc, va, ta, extent_elements=elements)
        columns_layout(doc, vb, tb, shift, extent_elements=[e for e in explode_elements if not (pipes == 'hide' and is_curve(e))])
    tags = ta + tb
    types = []
    seen = set()
    for el in elements:
        t = doc.GetElement(el.GetTypeId())
        if t is not None and _iv(t.Id) not in seen:
            seen.add(_iv(t.Id))
            types.append(t)
    try:
        res['images'] = render_images(doc, elements)
    except Exception:
        res['images'] = type_images(doc, types)   # запасной путь: превью типа
    res['schedule'] = schedule(doc, name) if with_schedule else None
    res['assembled'], res['exploded'], res['tags'] = va, vb, tags
    return res
