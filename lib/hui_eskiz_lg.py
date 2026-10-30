# -*- coding: utf-8 -*-
"""Эскиз в ЛЕГЕНДЕ из компонентов легенды (db 29.10): в модели ничего не создаётся и не ищется, эскиз — опись
типоразмера. Виды — компоненты легенды (вид компонента + поворот в плоскости листа), размеры Revit — к граням
и дугам самих компонентов (живые: правка семейства → эскиз следом). 3D — компонент «3D» (ракурс Revit).

Компонент легенды показывает ТИП с параметрами по умолчанию: длина экземпляра (КСД: 4_L) не видна →
габарит по длине подписывается «L», длины экземпляров — строкой над эскизом (как в легенде db «Эскизы КСД»).

Вид компонента → оси семейства (вправо, вверх, к зрителю) — замер 29.10 на КСД 50 (Береста):
-7 «Спереди», -6 «Сзади», -10 «Слева», -9 «Справа», -8 «План этажа», -3 «3D»."""
from __future__ import division
import math
import hui_eskiz as E
import hui_eskiz_v as V
from Autodesk.Revit.DB import (XYZ, Options, GeometryInstance, Solid, PlanarFace, Arc, Line, ElementId,
                               FilteredElementCollector, BuiltInCategory, BuiltInParameter, ElementTransformUtils,
                               CopyPasteOptions, Transform, TextNote, TextNoteOptions, FilledRegion, CurveLoop,
                               ReferenceArray, RadialDimension)
from System.Collections.Generic import List
from Autodesk.Revit.DB import View, ViewType, ViewDuplicateOption, GraphicsStyleType

FT = 304.8
DEBUG = False
CV = {-7: ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),          # «Спереди» показывает грань +Y семейства (замер 29.10)
      -6: ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
      -10: ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
      -9: ((0, -1, 0), (0, 0, 1), (-1, 0, 0)),
      -8: ((1, 0, 0), (0, 1, 0), (0, 0, 1))}


def rnd(v):
    return tuple(int(round(x)) for x in v)


SEED_LEGEND = u'db_Эскиз_образец'     # служебная легенда: один компонент — образец для копирования (не удалять)


def seed_component(doc):
    """Образец компонента легенды: сначала из служебной легенды, иначе любой (и тогда копия уходит в служебную)."""
    anyc = None
    for e in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_LegendComponents).WhereElementIsNotElementType():
        v = doc.GetElement(e.OwnerViewId)
        if v is not None and v.Name == SEED_LEGEND:
            return e
        if anyc is None:
            anyc = e
    if anyc is None:
        return None
    try:                                  # закрепить образец в служебной легенде, чтобы чистка эскизов его не унесла
        sv = None
        for v in FilteredElementCollector(doc).OfClass(View):
            if v.ViewType == ViewType.Legend and v.Name == SEED_LEGEND:
                sv = v
                break
        if sv is None:
            sv = doc.GetElement(doc.GetElement(anyc.OwnerViewId).Duplicate(ViewDuplicateOption.Duplicate))
            sv.Name = SEED_LEGEND
            ids = [x.Id for x in FilteredElementCollector(doc, sv.Id).WhereElementIsNotElementType()
                   if x.Category is not None and x.Id != sv.Id]
            if ids:
                doc.Delete(List[ElementId](ids))
        ElementTransformUtils.CopyElements(doc.GetElement(anyc.OwnerViewId), List[ElementId]([anyc.Id]), sv,
                                           Transform.Identity, CopyPasteOptions())
        doc.Regenerate()
    except Exception:
        pass
    return anyc


def place(doc, seed, lg, sym, cv):
    """Копия компонента-образца в легенду lg: типоразмер sym, вид cv."""
    ids = ElementTransformUtils.CopyElements(doc.GetElement(seed.OwnerViewId), List[ElementId]([seed.Id]), lg,
                                             Transform.Identity, CopyPasteOptions())
    c, extra = None, []
    for i in ids:                       # вместе с образцом Revit копирует привязанные к нему размеры — они станут
        e = doc.GetElement(i)           # некорректными («опорные элементы размеров») — удалить сразу (30.10, Береста)
        if c is None and e is not None and e.Category is not None and \
                e.Category.Id.IntegerValue == int(BuiltInCategory.OST_LegendComponents):
            c = e
        else:
            extra.append(i)
    if extra:
        doc.Delete(List[ElementId](extra))
    c.get_Parameter(BuiltInParameter.LEGEND_COMPONENT).Set(sym.Id)
    c.get_Parameter(BuiltInParameter.LEGEND_COMPONENT_VIEW).Set(cv)
    return c


def comp_geo(doc, comp, lg):
    """Грани / дуги / точки компонента в координатах легенды (X вправо, Y вверх) со ссылками для размеров.
    Положения — из GetInstanceGeometry (координаты легенды), ссылки — из GetSymbolGeometry в виде
    «<UniqueId компонента>:0:INSTANCE:<ссылка>» (как у размеров db): ссылки InstanceGeometry после
    регенерации теряются, и Revit удаляет размер (проверено 29.10)."""
    opt = Options()
    opt.View = lg
    opt.ComputeReferences = True
    faces, arcs, pts, solids_pts = [], [], [], []
    uid = comp.UniqueId

    def iref(r):
        try:
            from Autodesk.Revit.DB import Reference
            return Reference.ParseFromStableRepresentation(doc, uid + u':0:INSTANCE:' + r.ConvertToStableRepresentation(doc))
        except Exception:
            return None

    def solids(ge):
        return [g for g in ge if isinstance(g, Solid) and g.Edges.Size > 0]
    g = comp.get_Geometry(opt)
    if g is None:
        return faces, arcs, pts, pts
    for go in g:
        if not isinstance(go, GeometryInstance):
            continue
        # тела экземпляра и типа идут в РАЗНОМ порядке (пленум, 30.10) — попарно не сопоставлять:
        # берём геометрию типа (у неё ссылки) и переводим в координаты легенды преобразованием go.Transform
        tf = go.Transform
        for ss in solids(go.GetSymbolGeometry()):
            sp = []
            for fs in ss.Faces:
                if isinstance(fs, PlanarFace) and fs.Reference is not None:
                    faces.append({'ref': fs.Reference, 'n': tf.OfVector(fs.FaceNormal).Normalize(),
                                  'p': tf.OfPoint(fs.Origin), 'area': fs.Area, 'face': fs, 'tf': tf})
            for es in ss.Edges:
                cv = es.AsCurve().CreateTransformed(tf)
                for q in cv.Tessellate():
                    pts.append(q)
                    sp.append(q)
                if isinstance(cv, Arc) and es.Reference is not None:
                    eps = []
                    for i in (0, 1):
                        try:
                            er = es.GetEndPointReference(i)
                            if er is not None:
                                eps.append((er, cv.GetEndPoint(i)))
                        except Exception:
                            pass
                    arcs.append({'ref': es.Reference, 'eps': eps, 'c': cv.Center, 'r': cv.Radius, 'n': cv.Normal})
            if sp:
                solids_pts.append(sp)
    body = max(solids_pts, key=V.bbox_vol) if solids_pts else pts
    return faces, arcs, pts, body


def center(pts):
    return XYZ((min(p.X for p in pts) + max(p.X for p in pts)) / 2, (min(p.Y for p in pts) + max(p.Y for p in pts)) / 2, 0)


def pick_view(Rk, Uk, Nk):
    """Вид компонента, смотрящий по Nk, и угол поворота в плоскости, чтобы Rk смотрело вправо."""
    for cv, (r, u, n) in CV.items():
        if rnd(n) == rnd(Nk):
            phi = math.atan2(E.dot(Rk, u), E.dot(Rk, r))
            # после поворота на −phi «вверх» должен совпасть с Uk (иначе зеркало — такого вида нет)
            up_after = (E.dot(Uk, r), E.dot(Uk, u))
            ca, sa = math.cos(-phi), math.sin(-phi)
            yx = up_after[0] * sa + up_after[1] * ca
            if yx > 0.5:
                return cv, -phi
    return None, 0.0


def lengths_note(doc, it):
    """Длины экземпляров (параметр 4_L или INV_Длина линейного диффузора) — для подписи «L»."""
    out = {}
    for i in it.get('ids') or []:
        fi = doc.GetElement(ElementId(i))
        for nm in (u'4_L', u'INV_Длина линейного диффузора', u'Длина'):
            p = fi.LookupParameter(nm)
            if p is not None and p.HasValue:
                try:
                    L = int(round(p.AsDouble() * FT))
                    out[L] = out.get(L, 0) + 1
                    break
                except Exception:
                    pass
    return out


def build(doc, lg, items, S, st, errs):
    """items: [(it, opt)]. Эскизы друг под другом в одной легенде lg (её содержимое уже очищено)."""
    seed = seed_component(doc)
    if seed is None:
        raise Exception(u'В модели нет ни одного компонента легенды — поставьте любой (Аннотации → Компонент '
                        u'легенды) в любую легенду: API Revit не создаёт компонент легенды с нуля, только копирует.')
    from Autodesk.Revit.DB import ViewDetailLevel
    try:
        lg.ViewTemplateId = ElementId.InvalidElementId
    except Exception:
        pass
    try:
        lg.DetailLevel = ViewDetailLevel.Fine          # на «Грубом» у КСД нет тел — ни вида, ни граней для размеров
    except Exception:
        pass
    for bic in (BuiltInCategory.OST_Dimensions, BuiltInCategory.OST_LegendComponents, BuiltInCategory.OST_TextNotes,
                BuiltInCategory.OST_DetailComponents):
        try:
            lg.SetCategoryHidden(ElementId(bic), False)    # легенда-образец могла их скрывать
        except Exception:
            pass
    doc.Regenerate()
    y_top = 0.0
    k = S / FT
    for it, opt in items:
        try:
            y_top = build_one(doc, lg, seed, it, opt, S, st, errs, y_top) - 40 * k
        except Exception as ex:
            errs.append(u'%s: %s' % (it['sym'].FamilyName, ex))
    return lg


def build_one(doc, lg, seed, it, opt, S, st, errs, y_top):
    part = it['part']
    o = dict(E.DEFAULT_OPT)
    o.update(opt or {})
    face = o['face'] or E.default_face(part)
    R0, U0, N0 = E.main_basis(face, o['rot'])
    if part.get('rot180'):                                  # изделие приведено поворотом на 180° — к осям семейства
        fl = lambda v: (-v[0], -v[1], v[2])
        R0, U0, N0 = fl(R0), fl(U0), fl(N0)
    k = S / FT
    gap = 25 * k
    keys = ['main'] + [x for x in ('left', 'top', 'right', 'rear') if x in o['views']]
    placed = {}
    for key in keys:
        Rk, Uk, Nk = E.view_basis(key, R0, U0, N0)
        cv, ang = pick_view(Rk, Uk, Nk)
        if cv is None:
            errs.append(u'%s: вид «%s» компонентом легенды не показать' % (it['sym'].FamilyName, E.VIEW_TITLE[key]))
            continue
        c = place(doc, seed, lg, it['sym'], cv)
        doc.Regenerate()
        if abs(ang) > 1e-6:
            pc = center(comp_geo(doc, c, lg)[2])
            ElementTransformUtils.RotateElement(doc, c.Id, Line.CreateBound(pc, pc + XYZ.BasisZ), ang)
            doc.Regenerate()
        if DEBUG:
            errs.append(u'dbg %s cv=%d ang=%.0f' % (key, cv, math.degrees(ang)))
        placed[key] = (c, Nk)
    # раскладка: главный, справа от него — слева/сзади, под ним — сверху, правее всех — 3D
    def bb(c):
        ps = comp_geo(doc, c, lg)[2]
        return (min(p.X for p in ps), min(p.Y for p in ps), max(p.X for p in ps), max(p.Y for p in ps))

    def move(c, x0, ytop):
        b = bb(c)
        ElementTransformUtils.MoveElement(doc, c.Id, XYZ(x0 - b[0], ytop - b[3], 0))
        doc.Regenerate()
    if 'main' not in placed:
        return y_top
    head_y = y_top
    y0 = y_top - 50 * k                       # под заголовком и строкой «L —…» остаётся место для размеров сверху
    x0 = 45 * k
    move(placed['main'][0], x0, y0)
    bm = bb(placed['main'][0])
    xr = bm[2] + gap + 40 * k
    for key in ('left', 'rear'):
        if key in placed:
            c = placed[key][0]
            b = bb(c)
            ElementTransformUtils.MoveElement(doc, c.Id, XYZ(xr - b[0], (bm[1] + bm[3]) / 2 - (b[1] + b[3]) / 2, 0))
            doc.Regenerate()
            xr = bb(c)[2] + gap + 40 * k
    ybot = bm[1] - gap - 40 * k
    if 'top' in placed:
        c = placed['top'][0]
        b = bb(c)
        ElementTransformUtils.MoveElement(doc, c.Id, XYZ((bm[0] + bm[2]) / 2 - (b[0] + b[2]) / 2, ybot - b[3], 0))
        doc.Regenerate()
        ybot = bb(c)[1] - gap
    if o['axo']:
        # 30.10 (db): 3D-компонент легенды Revit не поворачивается и показывал пленум «спиной» (без патрубков).
        # Аксонометрия — как в превью: фронтальная изометрия ГОСТ 2.317 (E.build_axo) линиями детализации,
        # сторона — где видно патрубки и открытые грани; размеры Revit — по концам этих линий.
        try:
            ap = E.build_axo(part, (R0, U0, N0), o['az'], o['el'], S, o.get('axo_mode', 'free'), o.get('axo_cam'))
            ab = E.bbox(E.render(ap, S), S)
            ymid = (bm[1] + bm[3]) / 2 * FT
            E.shift(ap, xr * FT - ab[0], ymid - (ab[1] + ab[3]) / 2.0)
            errs.extend([u'аксонометрия: ' + x for x in E.emit(doc, lg, ap, st)])
            ybot = min(ybot, (ymid - (ab[3] - ab[1]) / 2.0) / FT - gap)
        except Exception as ex:
            errs.append(u'аксонометрия: %s' % ex)
    # размеры и заливка на ортогональных видах
    lens = lengths_note(doc, it)
    done_r = set()
    for key, (c, Nk) in placed.items():
        faces, arcs, pts, body = comp_geo(doc, c, lg)
        V.BODY[:] = body
        cc = center(pts)
        n_before = len(errs)
        dims_l(doc, lg, cc, faces, arcs, pts, st['dim'], S, errs, done_r, lens, it, c, Nk, R0)
        fill_open(doc, lg, part, faces, Nk, st, errs)
    # заголовок и ТТ
    try:
        o2 = TextNoteOptions(st['t3.5'])
        TextNote.Create(doc, lg.Id, XYZ(0, head_y, 0), u'Семейство: ' + it['sym'].FamilyName + u'\nТип: ' + E.nm(it['sym']), o2)
        if lens:
            o25 = TextNoteOptions(st['t2.5'])
            TextNote.Create(doc, lg.Id, XYZ(0, head_y - 8 * k, 0),
                            u'L — по экземплярам модели: ' + u'; '.join(u'%d мм — %d шт.' % (L, n) for L, n in sorted(lens.items())), o25)
        tt = E.tt_lines(part).replace(u'Отверстия (проходные) показаны заливкой и стрелками.',
                                       u'Открытые грани показаны заливкой.')
        TextNote.Create(doc, lg.Id, XYZ(0, ybot, 0), tt, TextNoteOptions(st['t2.5']))
        ybot -= (tt.count(u'\n') + 2) * 4.5 * k
    except Exception as ex:
        errs.append(u'текст: %s' % ex)
    return ybot


def dims_l(doc, lg, c, faces, arcs, pts, dt, S, errs, done_r, lens, it, comp, Nk, R0):
    """Размеры Revit к граням компонента (как V.dims_view); габарит вдоль оси длины семейства (X) — «L»,
    если длина задаётся экземпляром."""
    before = set(e.Id.IntegerValue for e in FilteredElementCollector(doc, lg.Id).OfCategory(BuiltInCategory.OST_Dimensions))
    # центры патрубков: ссылки на дуги компонента легенды Revit бракует при сохранении (30.10) — поэтому через центр
    # каждой видимой окружности — короткая вспомогательная линия стиля «невидимые линии» (способ db), размер — к ней
    arcs_c = []
    try:
        R_, U_, N_ = lg.RightDirection, lg.UpDirection, lg.ViewDirection
        sty = None
        try:
            sty = doc.Settings.Categories.get_Item(BuiltInCategory.OST_InvisibleLines).GetGraphicsStyle(GraphicsStyleType.Projection)
        except Exception:
            sty = None
        hl = 1.5 * S / 304.8                     # полудлина линии: 1,5 мм листа
        seen = []
        for a in arcs:
            if abs(a['n'].DotProduct(N_)) < 0.999:
                continue
            q = a['c']
            if any(q.DistanceTo(p0) < 0.5 / 304.8 for p0 in seen):
                continue
            seen.append(q)
            eps = []
            for d_, far in ((U_, U_), (R_, R_)):  # линия вдоль U — для размеров по R, вдоль R — для размеров по U
                dc = doc.Create.NewDetailCurve(lg, Line.CreateBound(q - d_.Multiply(hl), q + d_.Multiply(hl)))
                if sty is not None:
                    try:
                        dc.LineStyle = sty
                    except Exception:
                        pass
                eps.append((dc.GeometryCurve.Reference, q + far.Multiply(1.0)))   # сдвиг «far» уводит ссылку из чужой оси
            arcs_c.append({'ref': None, 'eps': eps, 'c': q, 'r': a['r'], 'n': a['n']})
    except Exception as ex:
        errs.append(u'вспомогательные линии: %s' % ex)
        arcs_c = []
    made = V.dims_view(doc, lg, c, faces, arcs_c, pts, dt, S, errs, radii=False, done_r=done_r)
    if DEBUG:
        from Autodesk.Revit.DB import Dimension as _D
        errs.append(u'dbg dims now in legend: %d' % len(list(FilteredElementCollector(doc, lg.Id).OfClass(_D))))
    if DEBUG:
        errs.append(u'dbg faces=%d arcs=%d dims=%d Nk=%s' % (len(faces), len(arcs), made, Nk))
    if not lens:
        return
    # габарит по длине: самый длинный новый размер, параллельный оси X семейства на этом виде
    best = None
    for d in FilteredElementCollector(doc, lg.Id).OfCategory(BuiltInCategory.OST_Dimensions):
        if d.Id.IntegerValue in before or d.NumberOfSegments > 1:
            continue
        try:
            v = d.Value
        except Exception:
            continue
        if v is not None and (best is None or v > best[0]):
            best = (v, d)
    if best is not None and Nk[0] == 0:                    # ось X семейства лежит в плоскости вида
        try:
            best[1].ValueOverride = u'L'
        except Exception as ex:
            errs.append(u'L: %s' % ex)


def fill_open(doc, lg, part, faces, Nk, st, errs):
    """Открытая целиком грань, смотрящая на зрителя в этом виде, — заливка по её контуру (грань компонента)."""
    want = [c for c in part['cuts'] if c.get('full') and c['l'] <= 0.5]
    fl = (lambda v: (-v[0], -v[1], v[2])) if part.get('rot180') else (lambda v: v)
    if not any(rnd(fl(c['n3'])) == rnd(Nk) for c in want):
        return
    fs = [f for f in faces if f['n'].Z > 0.999]
    if not fs:
        return
    top = max(f['p'].Z for f in fs)
    fs = [f for f in fs if f['p'].Z > top - 1 / FT]
    f = max(fs, key=lambda f_: f_['area'])
    try:
        loops = []
        for lp in f['face'].GetEdgesAsCurveLoops():
            cl = CurveLoop()
            for cv in lp:
                cv = cv.CreateTransformed(f['tf']) if f.get('tf') else cv
                p0, p1 = cv.GetEndPoint(0), cv.GetEndPoint(1)
                cl.Append(Line.CreateBound(XYZ(p0.X, p0.Y, 0), XYZ(p1.X, p1.Y, 0)))
            loops.append(cl)
        FilledRegion.Create(doc, st['gray'], lg.Id, List[CurveLoop](loops[:1]))
    except Exception as ex:
        errs.append(u'заливка: %s' % ex)


# ---------------------------------------------------------------- таблица врезок (30.10)
SPIG_NAME = u'db_Эскиз_Врезки пленумов'


def spigot_schedule(doc, errs, name=SPIG_NAME):
    """Одна живая спецификация на все типы пленумов: строка — тип, по врезке три столбца «размер, U, V»
    (параметры типа db_Вi_Размер / _U / _V). Компоненты легенды не дают ни размеров к круглым врезкам,
    ни марок с данными семейства (проверено 29.10) — эта таблица вместо них. Есть такая — пересобирается."""
    from Autodesk.Revit.DB import (FilteredElementCollector, ViewSchedule, ScheduleFilter, ScheduleFilterType,
                                   BuiltInParameter, ScheduleSortGroupField, ScheduleSortOrder,
                                   SectionType)
    cat = ElementId(BuiltInCategory.OST_DuctAccessory)
    vs = None
    for v in FilteredElementCollector(doc).OfClass(ViewSchedule):
        if v.Name == name and not v.IsTemplate:
            vs = v
            break
    if vs is None:
        vs = ViewSchedule.CreateSchedule(doc, cat)
        vs.Name = name
    d = vs.Definition
    while d.GetFieldCount() > 0:
        d.RemoveField(d.GetFieldId(0))
    while d.GetFilterCount() > 0:
        d.RemoveFilter(0)
    while d.GetSortGroupFieldCount() > 0:
        d.RemoveSortGroupField(0)
    sf = {}
    for f in d.GetSchedulableFields():
        try:
            sf[f.GetName(doc)] = f
        except Exception:
            pass
    n = 0
    while (u'db_В%d_Размер' % (n + 1)) in sf:
        n += 1
    if n == 0:
        raise Exception(u'нет параметров db_В1_Размер у арматуры воздуховодов — нечего сводить')
    tf = None
    for f in d.GetSchedulableFields():
        if f.ParameterId == ElementId(BuiltInParameter.ELEM_TYPE_PARAM):
            tf = f
    fld_type = d.AddField(tf) if tf is not None else d.AddField(sf[u'Тип'])
    fld_type.ColumnHeading = u'Тип'
    fld_type.GridColumnWidth = 55 / FT
    first = d.AddField(sf[u'db_В1_Грань']) if u'db_В1_Грань' in sf else None
    if first is not None:                                    # фильтр «это пленум» — у прочей арматуры нет db_В1_Грань
        first.IsHidden = True
        d.AddFilter(ScheduleFilter(first.FieldId, ScheduleFilterType.HasValue))
    d.AddSortGroupField(ScheduleSortGroupField(fld_type.FieldId, ScheduleSortOrder.Ascending))
    d.IsItemized = False
    cols = []
    for i in range(1, n + 1):
        for suf, head, w in ((u'Размер', u'Ø / размер', 16), (u'U', u'U', 10), (u'V', u'V', 10)):
            nm = u'db_В%d_%s' % (i, suf)
            if nm not in sf:
                continue
            fd = d.AddField(sf[nm])
            fd.ColumnHeading = head
            fd.GridColumnWidth = w / FT
            cols.append((i, fd))
    # заголовки групп «В1», «В2»… над тройками столбцов
    try:
        doc.Regenerate()
        body = vs.GetTableData().GetSectionData(SectionType.Body)
        top = body.FirstRowNumber
        col0 = body.FirstColumnNumber
        idx = 1                                              # 0 — «Тип» (скрытый столбец в таблицу не выводится)
        for i in range(1, n + 1):
            k = len([1 for j, fd in cols if j == i])
            if k:
                vs.GroupHeaders(top, col0 + idx, top, col0 + idx + k - 1, u'В%d' % i)
                idx += k
    except Exception as ex:
        errs.append(u'таблица врезок: заголовки групп — %s' % ex)
    return vs
