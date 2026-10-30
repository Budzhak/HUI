# -*- coding: utf-8 -*-
"""Эскиз из видов САМОГО экземпляра (db 29.10): ортогональные виды — разрезы Revit по экземпляру (соседи скрыты),
аксонометрия — заблокированный 3D-вид в любом ракурсе. Размеры — только размеры Revit к граням экземпляра
(живые: меняется семейство — меняется эскиз). Поверх геометрии — только заливка открытых граней.
Всё — на лист «<префикс><семейство>[_<тип>]».

Ссылки на геометрию экземпляра: грани / рёбра из символьной геометрии + «<UniqueId экземпляра>:0:INSTANCE:<ссылка>»
(так их принимает и RadialDimension; ссылки GetInstanceGeometry годятся только для линейных размеров)."""
from __future__ import division
import math
import hui_eskiz as E
from Autodesk.Revit.DB import (XYZ, Transform, BoundingBoxXYZ, ViewSection, View3D, ViewFamilyType, ViewFamily,
                               FilteredElementCollector, ViewOrientation3D, Options, ViewDetailLevel, GeometryInstance,
                               Solid, PlanarFace, Arc, Reference, ReferenceArray, Line, RadialDimension, ViewSheet,
                               Viewport, ElementId, TextNote, TextNoteOptions, FilledRegion, CurveLoop,
                               BuiltInParameter, View, FamilyInstance, HorizontalTextAlignment)
from System.Collections.Generic import List


def work_plane(doc, v, normal, origin):
    """В 3D-виде размер ложится на рабочую плоскость вида — ставим её по месту размера."""
    from Autodesk.Revit.DB import SketchPlane, Plane
    try:
        v.SketchPlane = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(normal, origin))
    except Exception:
        pass

FT = 304.8
DEBUG = False
SOFT = []
TOL = 2.0 / FT                    # 2 мм — одна плоскость (стенки 1 мм, погрешность)


def X(v):
    return XYZ(v[0], v[1], v[2])


def T(x):
    return (x.X, x.Y, x.Z)


# ------------------------------------------------------------------ геометрия экземпляра со ссылками
def inst_geo(doc, fi):
    """Плоские грани и дуги экземпляра в модели: [{'ref', 'n', 'p', 'area'}], [{'ref', 'eps':[(ref, точка)],
    'c', 'r', 'n'}], габарит (min, max) в модели."""
    opt = Options()
    opt.ComputeReferences = True
    opt.DetailLevel = ViewDetailLevel.Fine
    faces, arcs, pts = [], [], []
    solids_pts = []
    uid = fi.UniqueId

    def iref(r):
        try:
            return Reference.ParseFromStableRepresentation(doc, uid + u':0:INSTANCE:' + r.ConvertToStableRepresentation(doc))
        except Exception:
            return None
    for go in fi.get_Geometry(opt):
        if not isinstance(go, GeometryInstance):
            continue
        tr = go.Transform
        for so in go.GetSymbolGeometry():
            if not isinstance(so, Solid) or so.Volume < 1e-9:
                continue
            spts = []
            for f in so.Faces:
                if isinstance(f, PlanarFace) and f.Reference is not None:
                    r = iref(f.Reference)
                    if r is not None:
                        faces.append({'ref': r, 'n': tr.OfVector(f.FaceNormal), 'p': tr.OfPoint(f.Origin), 'area': f.Area})
            for ed in so.Edges:
                cv = ed.AsCurve()
                for q in cv.Tessellate():
                    pts.append(tr.OfPoint(q))
                    spts.append(pts[-1])
                if isinstance(cv, Arc) and ed.Reference is not None:
                    r = iref(ed.Reference)
                    eps = []
                    for i in (0, 1):
                        try:
                            er = iref(ed.GetEndPointReference(i))
                            if er is not None:
                                eps.append((er, tr.OfPoint(cv.GetEndPoint(i))))
                        except Exception:
                            pass
                    if r is not None:
                        arcs.append({'ref': r, 'eps': eps, 'c': tr.OfPoint(cv.Center), 'r': cv.Radius,
                                     'n': tr.OfVector(cv.Normal)})
            if spts:
                solids_pts.append(spts)
    BODY[:] = max(solids_pts, key=bbox_vol) if solids_pts else pts
    lo = XYZ(min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts)) if pts else None
    hi = XYZ(max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts)) if pts else None
    return faces, arcs, (lo, hi), pts


BODY = []                         # точки корпуса (тело с наибольшим габаритом) — база размеров аксонометрии


def bbox_vol(ps):
    return ((max(p.X for p in ps) - min(p.X for p in ps)) * (max(p.Y for p in ps) - min(p.Y for p in ps)) *
            (max(p.Z for p in ps) - min(p.Z for p in ps)))


# ------------------------------------------------------------------ виды
def vtype(doc, fam):
    return [t for t in FilteredElementCollector(doc).OfClass(ViewFamilyType) if t.ViewFamily == fam][0]


def extent(pts, axis, c):
    vals = [(p - c).DotProduct(axis) for p in pts]
    return min(vals), max(vals)


def make_section(doc, name, c, n, up, pts, S, margin):
    """Разрез: зритель снаружи по n (к зрителю), «вверх» — up. Rule Revit: Right = -BasisX, View = -BasisZ."""
    t = Transform.Identity
    t.Origin = c
    t.BasisZ = n.Negate()
    t.BasisY = up
    t.BasisX = up.CrossProduct(n.Negate())
    right = up.CrossProduct(n)
    x0, x1 = extent(pts, right, c)
    y0, y1 = extent(pts, up, c)
    z0, z1 = extent(pts, n, c)
    b = BoundingBoxXYZ()
    b.Transform = t
    # BasisX = −right → по X коробки зеркально
    b.Min = XYZ(-x1 - margin, y0 - margin, -z1 - 50 / FT)
    b.Max = XYZ(-x0 + margin, y1 + margin, -z0 + 50 / FT)
    v = ViewSection.CreateSection(doc, vtype(doc, ViewFamily.Section).Id, b)
    hide_marker(v)
    v.Scale = int(S)
    v.DetailLevel = ViewDetailLevel.Fine
    try:
        v.CropBoxVisible = False
    except Exception:
        pass
    v.Name = unique_name(doc, name)
    return v


def make_ortho(doc, name, c, n, up, pts, S, margin):
    """Ортогональный вид: разрез, если Revit оставил заданный «верх» (у разреза с горизонтальным взглядом верх
    всегда — верх модели); иначе — заблокированный 3D-вид с тем же взглядом (ортогональный, любой «верх»)."""
    # проверено 29.10: разрез с горизонтальным взглядом рисуется с верхом по оси Z модели, даже если UpDirection
    # сообщает заданный «верх» → такой вид — 3D. Взгляд по вертикали (как план) — любой «верх» честно.
    if abs(n.Z) < 0.999 and abs(up.Z) < 0.999:
        return make_3d(doc, name, c, n, up, pts, S)
    return make_section(doc, name, c, n, up, pts, S, margin)


def make_3d(doc, name, c, d, up, pts, S):
    """Заблокированный 3D-вид: d — к зрителю."""
    v = View3D.CreateIsometric(doc, vtype(doc, ViewFamily.ThreeDimensional).Id)
    upp = (up - d.Multiply(up.DotProduct(d))).Normalize()
    v.SetOrientation(ViewOrientation3D(c + d.Multiply(30), upp, d.Negate()))
    lo = XYZ(min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts))
    hi = XYZ(max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts))
    m = XYZ(30 / FT, 30 / FT, 30 / FT)
    bb = BoundingBoxXYZ()
    bb.Min, bb.Max = lo - m, hi + m
    v.IsSectionBoxActive = True
    v.SetSectionBox(bb)
    v.Scale = int(S)
    v.DetailLevel = ViewDetailLevel.Fine
    v.Name = unique_name(doc, name)
    v.SaveOrientationAndLock()
    # подрезка по изделию (+ 30 мм листа под размеры): иначе видовой экран на листе — во всю модель
    try:
        cb = v.CropBox
        inv = cb.Transform.Inverse
        q = [inv.OfPoint(p) for p in pts]
        mg = 22 * S / FT
        cb.Min = XYZ(min(a.X for a in q) - mg, min(a.Y for a in q) - mg, cb.Min.Z)
        cb.Max = XYZ(max(a.X for a in q) + mg, max(a.Y for a in q) + mg, cb.Max.Z)
        v.CropBox = cb
        v.CropBoxActive = True
        v.CropBoxVisible = False
    except Exception as ex:
        SOFT.append(u'подрезка 3D %s: %s' % (name, ex))
    try:
        from Autodesk.Revit.DB import BuiltInCategory
        v.SetCategoryHidden(ElementId(BuiltInCategory.OST_SectionBox), True)
    except Exception:
        pass
    return v


def ann_crop(v):
    """Подрезка аннотаций по рамке: размеры, ушедшие далеко (ошибка привязки), не раздувают видовой экран."""
    p = v.get_Parameter(BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
    if p is not None and not p.IsReadOnly:
        try:
            p.Set(1)
        except Exception:
            pass


def isolate(v, fi):
    """Только изделие: соседи скрыты; марки других разрезов, рамки — скрыты; цвет системы — чёрным
    (переопределение КАТЕГОРИИ в этом виде, не элемента)."""
    from Autodesk.Revit.DB import BuiltInCategory, OverrideGraphicSettings, Color
    v.IsolateElementTemporary(fi.Id)
    v.ConvertTemporaryHideIsolateToPermanent()
    ann_crop(v)
    for nm_ in ('OST_Viewers', 'OST_SectionBox', 'OST_Levels', 'OST_Grids', 'OST_Elev', 'OST_CLines'):
        bic = getattr(BuiltInCategory, nm_, None)
        if bic is None:
            continue
        try:
            v.SetCategoryHidden(ElementId(bic), True)
        except Exception:
            pass
    try:
        fid = black_filter(v.Document, fi.Category.Id)
        ogs = OverrideGraphicSettings()
        blk = Color(0, 0, 0)
        ogs.SetProjectionLineColor(blk)
        ogs.SetCutLineColor(blk)
        v.AddFilter(fid)
        v.SetFilterOverrides(fid, ogs)
    except Exception:
        pass


def black_filter(doc, cat_id):
    """Фильтр «zb_Эскиз_контур_<категория>» без правил — все элементы категории: линии чёрным (цвет системы
    перебивает переопределение категории, фильтр — нет). Правило db: оформление — фильтрами."""
    from Autodesk.Revit.DB import ParameterFilterElement
    nm = u'zb_Эскиз_контур_%d' % cat_id.IntegerValue
    for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement):
        if f.Name == nm:
            return f.Id
    return ParameterFilterElement.Create(doc, nm, List[ElementId]([cat_id])).Id


def hide_marker(v):
    """Марка этого разреза не нужна ни на планах, ни на других видах эскиза: «скрыть при масштабе мельче» 1:2."""
    for nm_ in ('SECTION_COARSER_SCALE_PULLDOWN_METRIC', 'SECTION_COARSER_SCALE_PULLDOWN_IMPERIAL'):
        bip = getattr(BuiltInParameter, nm_, None)
        if bip is None:
            continue
        p = v.get_Parameter(bip)
        if p is not None and not p.IsReadOnly:
            for val in (1, 2):
                try:
                    p.Set(val)
                    break
                except Exception:
                    pass


def unique_name(doc, name):
    names = set(x.Name for x in FilteredElementCollector(doc).OfClass(View))
    if name not in names:
        return name
    i = 2
    while u'%s (%d)' % (name, i) in names:
        i += 1
    return u'%s (%d)' % (name, i)


def set_title(v, title):
    p = v.get_Parameter(BuiltInParameter.VIEW_DESCRIPTION)
    if p is not None and not p.IsReadOnly:
        p.Set(title)


# ------------------------------------------------------------------ размеры
def groups(items, key):
    """[(позиция, элемент)] → по одной на позицию (допуск 2 мм), у грани — с наибольшей площадью."""
    items = sorted(items, key=key)
    out = []
    for it in items:
        if out and abs(key(it) - key(out[-1][-1])) < TOL:
            out[-1].append(it)
        else:
            out.append([it])
    return out


def dims_view(doc, v, c, faces, arcs, pts, dt, S, errs, chains=True, radii=True, done_r=None):
    """Габариты, цепочки привязок (грани + центры дуг через концы дуг), R / Ø — в плоскости вида v."""
    R, U, N = v.RightDirection, v.UpDirection, v.ViewDirection
    k = S / FT                                        # 1 мм листа в футах модели
    x0, x1 = extent(pts, R, c)
    y0, y1 = extent(pts, U, c)
    made = 0

    def P(x, y):
        return c + R.Multiply(x) + U.Multiply(y)

    def dim(refs, a, b):
        if isinstance(v, View3D):
            work_plane(doc, v, N, a)
        ra = ReferenceArray()
        for r in refs:
            ra.Append(r)
        try:
            doc.Create.NewDimension(v, Line.CreateBound(a, b), ra, dt)
            return 1
        except Exception as ex:
            errs.append(u'размер %s: %s' % (v.Name, ex))
            return 0
    for ax, oth, lo_o, side in ((R, U, y1, 1), (U, R, x0, -1)):
        fs = [f for f in faces if abs(f['n'].DotProduct(ax)) > 0.999]
        pos = lambda f: (f['p'] - c).DotProduct(ax)
        gs = groups(fs, pos)
        if len(gs) < 2:
            continue
        lo_f = max(gs[0], key=lambda f: f['area'])
        hi_f = max(gs[-1], key=lambda f: f['area'])
        # центры дуг (дуги, видимые в истинную величину): конец дуги на оси центра
        cen = []
        for a in arcs:
            if abs(a['n'].DotProduct(N)) < 0.999:
                continue
            for r, q in a['eps']:
                if abs((q - a['c']).DotProduct(ax)) < 0.5 / FT:
                    cen.append({'ref': r, 'x': (q - c).DotProduct(ax)})
        mids = [{'ref': max(g, key=lambda f: f['area'])['ref'], 'x': pos(g[0])} for g in gs[1:-1]]
        xlo, xhi = pos(lo_f), pos(hi_f)
        chain = [g[0] for g in groups(cen + mids, lambda e: e['x'])
                 if xlo + TOL < g[0]['x'] < xhi - TOL]
        row = 1
        if chains and chain:
            refs = [lo_f['ref']] + [e['ref'] for e in chain] + [hi_f['ref']]
            o = lo_o + side * 12 * k * row
            a_, b_ = (P(pos(lo_f), o), P(pos(hi_f), o)) if ax is R else (P(o, pos(lo_f)), P(o, pos(hi_f)))
            made += dim(refs, a_, b_)
            row += 1
        o = lo_o + side * 12 * k * row
        a_, b_ = (P(pos(lo_f), o), P(pos(hi_f), o)) if ax is R else (P(o, pos(lo_f)), P(o, pos(hi_f)))
        made += dim([lo_f['ref'], hi_f['ref']], a_, b_)
    if radii:
        done = done_r if done_r is not None else set()
        vis = [a for a in arcs if abs(a['n'].DotProduct(N)) > 0.999]
        vis.sort(key=lambda a: -(a['c'] - c).DotProduct(R))           # справа налево — R у крайней правой
        for a in vis:
            full = any(b is not a and (b['c'] - a['c']).GetLength() < TOL and abs(b['r'] - a['r']) < TOL for b in vis)
            key = (round(a['r'] * FT, 0), full)
            if key in done:
                continue
            # только «наружные» дуги: середина дуги в сторону от центра изделия по R или вверх
            if isinstance(v, View3D):
                work_plane(doc, v, a['n'], a['c'])
            try:
                RadialDimension.Create(doc, v, a['ref'], full)
                done.add(key)
                made += 1
            except Exception as ex:
                errs.append(u'R %s: %s' % (v.Name, ex))
    return made


def fill_open(doc, v, part, tr_p, frt, errs):
    """Заливка граней, открытых целиком, и проёмов, которые в этом виде видны «в лоб»."""
    N = v.ViewDirection
    o = v.Origin
    for cut in part['cuts']:
        if cut['l'] > 0.5:
            continue
        n3 = tr_p.OfVector(X(cut['n3']))
        if n3.DotProduct(N) < 0.99:
            continue
        a, b = cut['w'] / 2.0, cut['h'] / 2.0
        ring = [E.vadd(cut['o3'], E.vmul(cut['u3'], x), E.vmul(cut['v3'], y)) for x, y in
                ((-a, -b), (a, -b), (a, b), (-a, b))] if cut['shape'] == 'r' else \
            [E.vadd(cut['o3'], E.vmul(cut['u3'], x), E.vmul(cut['v3'], y)) for x, y in
             E.stadium_pts((0, 0), a, b, 16)]
        P = []
        for q in ring:
            m = tr_p.OfPoint(X(tuple(z / FT for z in q)))
            P.append(m - N.Multiply((m - o).DotProduct(N)))
        try:
            lp = CurveLoop()
            for i in range(len(P)):
                if P[i].DistanceTo(P[(i + 1) % len(P)]) > 0.3 / FT:
                    lp.Append(Line.CreateBound(P[i], P[(i + 1) % len(P)]))
            FilledRegion.Create(doc, frt, v.Id, List[CurveLoop]([lp]))
        except Exception as ex:
            errs.append(u'заливка %s: %s' % (v.Name, ex))


# ------------------------------------------------------------------ изделие → виды + лист
def part_transform(fi, part):
    """Координаты изделия (мм, центр короба) → модель (футы)."""
    tr = fi.GetTransform()
    ctr = part.get('ctr')
    if ctr is None:
        boxes, conns = E.geo_instance(fi)
        pb = E.part_from_boxes(boxes, conns)
        ctr = pb['ctr'] if pb else (0, 0, 0)
    t = Transform.Identity
    if part.get('rot180'):                       # изделие приведено поворотом на 180° вокруг Z семейства
        t.BasisX, t.BasisY, t.BasisZ = tr.BasisX.Negate(), tr.BasisY.Negate(), tr.BasisZ
        ctr = (-ctr[0], -ctr[1], ctr[2])
    else:
        t.BasisX, t.BasisY, t.BasisZ = tr.BasisX, tr.BasisY, tr.BasisZ
    t.Origin = tr.OfPoint(X(tuple(z / FT for z in ctr)))
    return t


def find_sheet(doc, name):
    for s_ in FilteredElementCollector(doc).OfClass(ViewSheet):
        if s_.Name == name:
            return s_
    return None


def sheet_for(doc, name):
    for s in FilteredElementCollector(doc).OfClass(ViewSheet):
        if s.Name == name:
            return s
    nums = set(s.SheetNumber for s in FilteredElementCollector(doc).OfClass(ViewSheet))
    i = 1
    while u'ЭСК-%d' % i in nums:
        i += 1
    s = ViewSheet.Create(doc, ElementId.InvalidElementId)
    s.Name = name
    s.SheetNumber = u'ЭСК-%d' % i
    return s


def clear_old(doc, sheet, prefix):
    """Повторный запуск: убрать прежние виды этого эскиза (по имени) и всё с листа."""
    ids = [vp.ViewId for vp in [doc.GetElement(i) for i in sheet.GetAllViewports()]]
    for e in FilteredElementCollector(doc, sheet.Id).WhereElementIsNotElementType().ToElements():
        if isinstance(e, (Viewport, TextNote)):
            try:
                doc.Delete(e.Id)
            except Exception:
                pass
    for i in ids:
        try:
            doc.Delete(i)
        except Exception:
            pass
    for v in FilteredElementCollector(doc).OfClass(View).ToElements():
        if not v.IsTemplate and v.Name.startswith(prefix):
            try:
                doc.Delete(v.Id)
            except Exception:
                pass


def build_item(doc, fi, part, opt, S, name, st, errs):
    """→ лист. opt — как у старого эскиза (face, rot, views, axo, az, el)."""
    o = dict(E.DEFAULT_OPT)
    o.update(opt or {})
    face = o['face'] or E.default_face(part)
    R0, U0, N0 = E.main_basis(face, o['rot'])
    tp = part_transform(fi, part)
    faces, arcs, (lo, hi), pts = inst_geo(doc, fi)
    c = tp.Origin
    sheet = sheet_for(doc, name)
    clear_old(doc, sheet, name + u' · ')
    dt = st['dim']
    frt = doc.GetElement(st['gray'])
    views = []
    margin = 22 * S / FT                         # 22 мм листа — место под размеры (2 ряда по 8–12 мм)
    keys = ['main'] + [k for k in ('left', 'top', 'right', 'bottom', 'rear') if k in o['views']]
    views_fill_later = []
    done_r = set()
    for key in keys:
        Rk, Uk, Nk = E.view_basis(key, R0, U0, N0)
        n = tp.OfVector(X(Nk)).Normalize()
        up = tp.OfVector(X(Uk)).Normalize()
        try:
            v = make_ortho(doc, u'%s · %s' % (name, E.VIEW_TITLE[key]), c, n, up, pts, S, margin)
            isolate(v, fi)
            if DEBUG:
                errs.append(u'dbg %s %s U=%s want %s D=%s want %s' % (key, type(v).__name__, T(v.UpDirection), T(up), T(v.ViewDirection), T(n)))
            set_title(v, E.VIEW_TITLE[key])
            doc.Regenerate()
            if isinstance(v, ViewSection):
                fill_open(doc, v, part, tp, frt.Id if frt else st['gray'], errs)
            else:
                views_fill_later.append((v, part, tp))
            dims_view(doc, v, c, faces, arcs, pts, dt, S, errs, done_r=done_r)
            views.append((key, v))
        except Exception as ex:
            errs.append(u'вид %s: %s' % (key, ex))
    if o['axo']:
        try:
            d = axo_dir(part, R0, U0, N0, o['az'], o['el'])
            dm = tp.OfVector(X(d)).Normalize()
            v = make_3d(doc, u'%s · Аксонометрия' % name, c, dm, tp.OfVector(X(U0)).Normalize(), pts, S)
            isolate(v, fi)
            set_title(v, u'Аксонометрия')
            doc.Regenerate()
            dims_3d(doc, v, c, faces, arcs, pts, dt, S, tp, errs)
            views.append(('axo', v))
            views_fill_later.append((v, part, tp))
        except Exception as ex:
            errs.append(u'аксонометрия: %s' % ex)
    doc.Regenerate()
    errs.extend(SOFT)
    del SOFT[:]
    vps = layout(doc, sheet, views, part, S, errs)
    # открытые грани на 3D-видах: заливка на листе поверх видового экрана (Revit не даёт заливку в 3D-вид)
    frid = frt.Id if frt else st['gray']
    for v, pr, tpp in views_fill_later:
        vp = vps.get(v.Id.IntegerValue) if vps else None
        if vp is not None:
            fill_on_sheet(doc, sheet, vp, v, pr, tpp, frid, errs)
    return sheet


def fill_on_sheet(doc, sheet, vp, v, part, tp, frt_id, errs):
    try:
        tl = v.GetModelToProjectionTransforms()
        mp = tl[0].GetModelToProjectionTransform()
        ps = vp.GetProjectionToSheetTransform()
    except Exception as ex:
        errs.append(u'заливка на листе: %s' % ex)
        return
    N = v.ViewDirection
    for cut in part['cuts']:
        if cut['l'] > 0.5:
            continue
        if tp.OfVector(X(cut['n3'])).DotProduct(N) < 0.05:          # грань к зрителю (в аксонометрии — наискось)
            continue
        a, b = cut['w'] / 2.0, cut['h'] / 2.0
        ring = [E.vadd(cut['o3'], E.vmul(cut['u3'], x), E.vmul(cut['v3'], y)) for x, y in
                (((-a, -b), (a, -b), (a, b), (-a, b)) if cut['shape'] == 'r' else E.stadium_pts((0, 0), a, b, 16))]
        P = []
        for q in ring:
            m = tp.OfPoint(X(tuple(z / FT for z in q)))
            sp = ps.OfPoint(mp.OfPoint(m))
            P.append(XYZ(sp.X, sp.Y, 0))
        try:
            lp = CurveLoop()
            for i in range(len(P)):
                if P[i].DistanceTo(P[(i + 1) % len(P)]) > 0.1 / FT:
                    lp.Append(Line.CreateBound(P[i], P[(i + 1) % len(P)]))
            FilledRegion.Create(doc, frt_id, sheet.Id, List[CurveLoop]([lp]))
        except Exception:
            return                  # на лист заливку Revit не даёт — в 3D-видах открытая грань без заливки


def axo_dir(part, R0, U0, N0, az, el):
    """Ракурс аксонометрии: из 8 (± азимут, ± наклон, спереди / сзади) — где видно больше открытых граней (4),
    проёмов (2), торцов патрубков (1); при равенстве — заданный в окне."""
    best = None
    for sn in (1, -1):
        for sa in (1, -1):
            for se in (1, -1):
                a_, e_ = math.radians(sa * az), math.radians(se * el)
                d = E.unit(E.vadd(E.vmul(R0, math.cos(e_) * math.sin(a_) * sn), E.vmul(N0, math.cos(e_) * math.cos(a_) * sn),
                                  E.vmul(U0, math.sin(e_))))
                sc = sum(((4 if c.get('full') else 2) if c['l'] <= 0.5 else 1) for c in part['cuts'] if E.dot(c['n3'], d) > 0.05)
                sc -= 0.01 * ((sn < 0) + (sa < 0) + (se < 0))
                if best is None or sc > best[0]:
                    best = (sc, d)
    return best[1]


def dims_3d(doc, v, c, faces, arcs, pts, dt, S, tp, errs):
    """Габариты по трём осям изделия и R / Ø на дугах, обращённых к зрителю."""
    D = v.ViewDirection                                   # к зрителю (проверено 29.10)
    k = S / FT
    axes = [tp.BasisX, tp.BasisY, tp.BasisZ]
    # база выносных — рёбра КОРПУСА (db 29.10: «от плоскости основания врезок, а не от самих врезок»)
    ext = [extent(BODY or pts, a, c) for a in axes]
    for i, ax in enumerate(axes):
        fs = [f for f in faces if abs(f['n'].DotProduct(ax)) > 0.999]
        gs = groups(fs, lambda f: (f['p'] - c).DotProduct(ax))
        if len(gs) < 2:
            continue
        lo_f, hi_f = max(gs[0], key=lambda f: f['area']), max(gs[-1], key=lambda f: f['area'])
        # ребро габарита — на углу, обращённом к зрителю по одной оси и «вниз/вбок» по другой
        j, m = [q for q in range(3) if q != i]
        sj = 1 if axes[j].DotProduct(D) > 0 else -1
        sm = -1 if axes[m].DotProduct(D) > 0 else 1
        base = c + axes[j].Multiply(ext[j][1] if sj > 0 else ext[j][0]) + axes[m].Multiply(ext[m][1] if sm > 0 else ext[m][0])
        off = axes[m].Multiply(sm * 12 * k)
        a_ = base + off + ax.Multiply((lo_f['p'] - c).DotProduct(ax) - (base - c).DotProduct(ax))
        b_ = a_ + ax.Multiply((hi_f['p'] - lo_f['p']).DotProduct(ax))
        ra = ReferenceArray()
        ra.Append(lo_f['ref'])
        ra.Append(hi_f['ref'])
        work_plane(doc, v, ax.CrossProduct(axes[m]).Normalize(), a_)
        try:
            doc.Create.NewDimension(v, Line.CreateBound(a_, b_), ra, dt)
        except Exception as ex:
            errs.append(u'размер 3D: %s' % ex)
    done = set()
    # R — на дуге ТОРЦА патрубка (плоскость, дальше всех от корпуса по оси патрубка), у крайнего правого
    # (db 29.10: «радиус можно взять как раз от той плоскости»)
    bc = c
    if BODY:
        bc = XYZ(sum(p.X for p in BODY) / len(BODY), sum(p.Y for p in BODY) / len(BODY), sum(p.Z for p in BODY) / len(BODY))

    def outv(a_):
        nv = a_['n'] if a_['n'].DotProduct(D) >= 0 else a_['n'].Negate()
        return (a_['c'] - bc).DotProduct(nv)
    cand = [a for a in arcs if abs(a['n'].DotProduct(D)) > 0.05]
    if cand:
        far = max(outv(a) for a in cand)
        cand = [a for a in cand if outv(a) > far - TOL]
    for a in sorted(cand, key=lambda a_: -(a_['c'] - c).DotProduct(v.RightDirection)):
        key = round(a['r'] * FT, 0)
        if key in done:
            continue
        # Радиальный размер в 3D-виде Revit кладёт на отметку 0 (проверено 29.10: TextPosition/рабочая плоскость
        # не помогают) — вне подрезки. Поэтому на торце — линейный размер Revit между концами торцевой дуги
        # (= 2R, ширина овала / Ø круга) в плоскости торца; R — на главном виде.
        if len(a['eps']) < 2:
            continue
        (r1, q1), (r2, q2) = a['eps'][0], a['eps'][1]
        dv = q2 - q1
        if dv.GetLength() < 1 / FT:
            continue
        dvu = dv.Normalize()
        off = a['n'].CrossProduct(dvu).Normalize()
        if (a['c'] - bc).DotProduct(off) < 0:
            off = off.Negate()
        off = off.Multiply(a['r'] + 10 * S / FT)
        work_plane(doc, v, a['n'], a['c'])
        ra = ReferenceArray()
        ra.Append(r1)
        ra.Append(r2)
        try:
            doc.Create.NewDimension(v, Line.CreateBound(q1 + off, q2 + off), ra, dt)
            done.add(key)
        except Exception as ex:
            errs.append(u'торец 3D: %s' % ex)


def layout(doc, sheet, views, part, S, errs):
    """ЕСКД, первый угол: главный | слева | аксонометрия; сверху — под главным. Зазор 20 мм листа."""
    gap = 8 / FT
    vps = {}
    for key, v in views:
        try:
            vps[key] = Viewport.Create(doc, sheet.Id, v.Id, XYZ(0, 0, 0))
        except Exception as ex:
            errs.append(u'на лист %s: %s' % (key, ex))
    doc.Regenerate()
    if DEBUG:
        for k_, vp_ in vps.items():
            o_ = vp_.GetBoxOutline()
            errs.append(u'dbg vp %s %.0fx%.0f мм' % (k_, (o_.MaximumPoint.X - o_.MinimumPoint.X) * FT, (o_.MaximumPoint.Y - o_.MinimumPoint.Y) * FT))

    def box(vp):
        o = vp.GetBoxOutline()
        return o.MinimumPoint, o.MaximumPoint

    def move_to(vp, x, y):                                # левый верх рамки → (x, y)
        mn, mx = box(vp)
        vp.SetBoxCenter(XYZ(x + (mx.X - mn.X) / 2, y - (mx.Y - mn.Y) / 2, 0))
    if 'main' not in vps:
        return dict((vp.ViewId.IntegerValue, vp) for vp in vps.values())
    mn, mx = box(vps['main'])
    mw, mh = mx.X - mn.X, mx.Y - mn.Y
    x0, y0 = 20 / FT, 270 / FT
    move_to(vps['main'], x0, y0)
    xr = x0 + mw + gap
    if 'left' in vps:
        move_to(vps['left'], xr, y0)
        a, b = box(vps['left'])
        xr = b.X + gap
    if 'rear' in vps:
        move_to(vps['rear'], xr, y0)
        a, b = box(vps['rear'])
        xr = b.X + gap
    yb = y0 - mh - gap
    if 'top' in vps:
        move_to(vps['top'], x0, yb)
        a, b = box(vps['top'])
        yb = a.Y - gap
    if 'axo' in vps:
        move_to(vps['axo'], xr, y0)
        a, b = box(vps['axo'])
        yb = min(yb, a.Y - gap)
    # выравнивание по проекционной связи: центры главного / сверху на одной вертикали, главного / слева — горизонтали
    cm = vps['main'].GetBoxCenter()
    for key, axis in (('top', 'x'), ('left', 'y'), ('rear', 'y')):
        if key in vps:
            cc = vps[key].GetBoxCenter()
            vps[key].SetBoxCenter(XYZ(cm.X, cc.Y, 0) if axis == 'x' else XYZ(cc.X, cm.Y, 0))
    # заголовок и ТТ
    try:
        st2 = E.styles(doc)
        o2 = TextNoteOptions(st2['t3.5'])
        TextNote.Create(doc, sheet.Id, XYZ(x0, y0 + 15 / FT, 0), part['family'] + (u' : ' + part['type'] if part.get('type') else u''), o2)
        o3 = TextNoteOptions(st2['t2.5'])
        tt = E.tt_lines(part).replace(u'Отверстия (проходные) показаны заливкой и стрелками.',
                                       u'Открытые грани и проёмы показаны заливкой на видах «в лоб».')
        TextNote.Create(doc, sheet.Id, XYZ(x0, yb, 0), tt, o3)
    except Exception as ex:
        errs.append(u'текст листа: %s' % ex)
    return dict((vp.ViewId.IntegerValue, vp) for vp in vps.values())
