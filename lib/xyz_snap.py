# -*- coding: utf-8 -*-
"""xyz_snap — подложки связей «снимком» для PDF (db 22.10.2026).

Правило db: всё из нашей модели (сети, оборудование, марки, тексты, оси) — вектором,
всё, что приходит из связей (IFC АР, другие RVT) — одной картинкой-подложкой, как скан.
Тогда Revit при печати не разбирает миллионы линий мебели IFC: лист печатается быстро,
весит мало, наши линии и стрелки — чёткие.

Как:
  snapshot(doc, view, ...) — на самом виде временно скрыто всё своё (и вернуто после снимка),
      остаются только связи → ExportImage в границах обрезки → PNG в кэш.
      Кэш: %APPDATA%\\XYZ_pdfsnap\\<модель>\\<id вида>.png + .json с ключом
      (масштаб, обрезка, шаблон, файлы связей и их даты). Ключ совпал — снимок не делается.
  place(doc, view, png, ...)  — на сам вид кладёт картинку фоном (под всем) точно в рамку
      обрезки и скрывает на нём связи. Делать в откатываемой группе (кнопка «PDF вектор»).
Транзакции открывает вызывающий (внутри TransactionGroup).
"""
import os
import io
import json
import math
import time

from Autodesk.Revit.DB import (FilteredElementCollector, RevitLinkInstance, ElementId, Transaction,
                               ViewDuplicateOption, ImageExportOptions, ExportRange, ImageFileType,
                               ImageResolution, ZoomFitType, FitDirectionType, ImageType, ImageTypeOptions,
                               ImageTypeSource, ImageInstance, BuiltInParameter, ImagePlacementOptions, BoxPlacement, DrawLayer,
                               ElementTransformUtils, Line, XYZ, ExternalFileUtils, ModelPathUtils,
                               BuiltInCategory, ViewType)
from System.Collections.Generic import List

PLAN_TYPES = (ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan, ViewType.AreaPlan,
              ViewType.Section, ViewType.Elevation, ViewType.Detail)
VERSION = 4      # 4: марки связей — в снимке вида (22.10.2026)


def _iv(eid):
    """Id → обычный int (Int64 из Revit 2025 json не пишет)."""
    try:
        return int(eid.Value)
    except AttributeError:
        return int(eid.IntegerValue)


def cache_dir(doc):
    from System import Environment
    base = os.path.join(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), u'XYZ_pdfsnap')
    name = u''.join(c if (c.isalnum() or c in u'-_.') else u'_' for c in (doc.Title or u'model'))
    d = os.path.join(base, name)
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


def visible_links(doc, view, links=None):
    """Экземпляры связей, видимые на виде."""
    if links is None:
        links = list(FilteredElementCollector(doc).OfClass(RevitLinkInstance))
    try:
        if view.GetCategoryHidden(ElementId(BuiltInCategory.OST_RvtLinks)):
            return []
    except Exception:
        pass
    res = []
    for li in links:
        try:
            if not li.IsHidden(view):
                res.append(li)
        except Exception:
            res.append(li)
    return res


def link_views(doc, sheet, links=None):
    """Виды листа (планы/разрезы/фасады), на которых видна хоть одна связь и есть обрезка."""
    out = []
    for vid in sheet.GetAllPlacedViews():
        v = doc.GetElement(vid)
        if v is None or v.IsTemplate or v.ViewType not in PLAN_TYPES:
            continue
        if not v.CropBoxActive:
            continue
        if visible_links(doc, v, links):
            out.append(v)
    return out


def _link_files(doc, lis):
    res = []
    for li in lis:
        path, mt = u'', 0
        try:
            lt = doc.GetElement(li.GetTypeId())
            ref = ExternalFileUtils.GetExternalFileReference(doc, lt.Id)
            path = ModelPathUtils.ConvertModelPathToUserVisiblePath(ref.GetAbsolutePath())
            if os.path.exists(path):
                mt = int(os.path.getmtime(path))
        except Exception:
            pass
        res.append([_iv(li.Id), u'%s' % path, int(mt)])
    return sorted(res)


def _crop(view):
    cb = view.CropBox
    t = cb.Transform
    w = cb.Max.X - cb.Min.X
    h = cb.Max.Y - cb.Min.Y
    c = t.OfPoint(XYZ((cb.Min.X + cb.Max.X) / 2.0, (cb.Min.Y + cb.Max.Y) / 2.0, cb.Max.Z))
    return w, h, c, t


def key_of(doc, view, lis, dpi):
    w, h, c, t = _crop(view)
    r = lambda x: round(x, 4)
    return json.dumps(dict(v=VERSION, view=_iv(view.Id), scale=int(view.Scale), dpi=dpi,
                           crop=[r(w), r(h), r(c.X), r(c.Y), r(c.Z), r(t.BasisX.X), r(t.BasisX.Y)],
                           tpl=_iv(view.ViewTemplateId), detail=str(view.DetailLevel),
                           filters=len(list(view.GetFilters())), links=_link_files(doc, lis)),
                      sort_keys=True)


def _read(p):
    try:
        with io.open(p, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return None


def snapshot(doc, view, links=None, dpi=200, force=False, log=None):
    """PNG только со связями вида, ровно в рамке обрезки. Возвращает (путь, из_кэша) или (None, причина).
    Нужна открытая TransactionGroup у вызывающего: копия вида создаётся отдельной транзакцией
    и пропадёт при откате группы (снимок в кэше останется)."""
    lis = visible_links(doc, view, links)
    if not lis:
        return None, u'связей на виде нет'
    d = cache_dir(doc)
    png = os.path.join(d, u'%d.png' % _iv(view.Id))
    meta = os.path.join(d, u'%d.json' % _iv(view.Id))
    key = key_of(doc, view, lis, dpi)
    if not force and os.path.isfile(png) and _read(meta) == key:
        return png, True
    t0 = time.time()
    w, h, c, tr = _crop(view)
    # снимок делается с САМОГО вида (не с копии): у копии Revit теряет часть настроек показа связей
    # (на кровле проявились сети других этажей, db 22.10.2026). Своё временно скрыть, снять, вернуть.
    lids = set(_iv(x.Id) for x in lis)
    hide = []
    ap = view.get_Parameter(BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
    old_ann = ap.AsInteger() if ap is not None else 0
    old_vis = view.CropBoxVisible
    t = Transaction(doc, u'XYZ: снимок подложки — скрыть своё')
    t.Start()
    try:
        for e in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
            if _iv(e.Id) in lids or isinstance(e, RevitLinkInstance):
                continue
            try:
                if e.CanBeHidden(view) and not e.IsHidden(view):
                    hide.append(e.Id)
            except Exception:
                pass
        if hide:
            view.HideElements(List[ElementId](hide))
        view.CropBoxVisible = False
        if ap is not None and old_ann:
            ap.Set(0)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    dup = view

    def _restore():
        t2 = Transaction(doc, u'XYZ: снимок подложки — вернуть своё')
        t2.Start()
        try:
            if hide:
                view.UnhideElements(List[ElementId](hide))
            view.CropBoxVisible = old_vis
            if ap is not None and old_ann:
                ap.Set(old_ann)
            t2.Commit()
        except Exception:
            if t2.HasStarted() and not t2.HasEnded():
                t2.RollBack()
            raise
    # бумага: ширина рамки (фут модели) / масштаб → дюймы × dpi
    px = int(round(w / float(view.Scale) * 12.0 * dpi))
    px = max(500, min(px, 15000))
    base = os.path.join(d, u'_exp_%d' % _iv(view.Id))
    for f in os.listdir(d):
        if f.startswith(u'_exp_%d' % _iv(view.Id)):
            try:
                os.remove(os.path.join(d, f))
            except Exception:
                pass
    o = ImageExportOptions()
    o.ExportRange = ExportRange.SetOfViews
    o.SetViewsAndSheets(List[ElementId]([dup.Id]))
    o.FilePath = base
    o.HLRandWFViewsFileType = ImageFileType.PNG
    o.ShadowViewsFileType = ImageFileType.PNG
    o.ImageResolution = ImageResolution.DPI_300
    o.ZoomType = ZoomFitType.FitToPage
    o.FitDirection = FitDirectionType.Horizontal
    o.PixelSize = px
    try:
        doc.ExportImage(o)
    finally:
        _restore()
    made = [f for f in os.listdir(d) if f.startswith(u'_exp_%d' % _iv(view.Id)) and f.lower().endswith(u'.png')]
    if not made:
        return None, u'Revit не сохранил картинку'
    src = os.path.join(d, made[0])
    if os.path.isfile(png):
        os.remove(png)
    os.rename(src, png)
    with io.open(meta, 'w', encoding='utf-8') as f:
        f.write(unicode(key))   # noqa: F821
    if log:
        log(u'снимок «%s»: %d px, %.1f с' % (view.Name, px, time.time() - t0))
    return png, False


def place(doc, view, png, links=None, log=None):
    """Положить снимок фоном в рамку обрезки вида и скрыть на виде связи. Внутри открытой транзакции.
    Возвращает ImageInstance."""
    w, h, c, tr = _crop(view)
    it = ImageType.Create(doc, ImageTypeOptions(png, False, ImageTypeSource.Import))
    inst = ImageInstance.Create(doc, view, it.Id, ImagePlacementOptions(c, BoxPlacement.Center))
    try:
        inst.LockProportions = True
    except Exception:
        pass
    inst.Width = w
    try:
        inst.DrawLayer = DrawLayer.Background
    except Exception:
        pass
    ang = math.atan2(tr.BasisX.Y, tr.BasisX.X)
    if abs(ang) > 1e-6 and view.ViewType in (ViewType.FloorPlan, ViewType.CeilingPlan,
                                              ViewType.EngineeringPlan, ViewType.AreaPlan):
        ElementTransformUtils.RotateElement(doc, inst.Id, Line.CreateBound(c, c + XYZ.BasisZ), ang)
    lis = visible_links(doc, view, links)
    if lis:
        view.HideElements(List[ElementId]([x.Id for x in lis]))
    if log:
        try:
            log(u'подложка «%s»: ширина %.3f / %.3f фут, высота %.3f / %.3f' % (
                view.Name, inst.Width, w, inst.Height, h))
        except Exception:
            pass
    return inst


# ---------------------------------------------------------------- «как на экране»: снимок вида поверх модели
# Для листов, помеченных db «растром» (кровли и т. п.): Revit сам рисует вид со связями и перекрытием
# (разноуровневая кровля закрывает сети нижних этажей — как на экране). Снимок без надписей/марок/осей
# кладётся на вид ПЕРЕДНИМ планом (поверх модели), связи на время печати скрываются, надписи — вектором.
# db 22.10.2026.
def _link_tags(doc, view):
    """Марки на элементах связей: при скрытой связи Revit рисует их «?» — поэтому они идут в снимок,
    а при печати скрываются."""
    from Autodesk.Revit.DB import IndependentTag
    ids = []
    for tg in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
        try:
            if any(_iv(x.LinkInstanceId) > 0 for x in tg.GetTaggedElementIds()):
                ids.append(tg.Id)
        except Exception:
            pass
    return ids


def _annotation_ids(doc, view):
    from Autodesk.Revit.DB import CategoryType as _CT
    skip = set(_iv(x) for x in _link_tags(doc, view))      # марки связей остаются в снимке
    ids = []
    for e in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        if isinstance(e, RevitLinkInstance) or _iv(e.Id) in skip:
            continue
        c = e.Category
        try:
            own = _iv(e.OwnerViewId) == _iv(view.Id)
        except Exception:
            own = False
        if own or (c is not None and c.CategoryType == _CT.Annotation):
            try:
                if e.CanBeHidden(view) and not e.IsHidden(view):
                    ids.append(e.Id)
            except Exception:
                pass
    return ids


def snapshot_full(doc, view, links=None, dpi=200, force=False, log=None):
    """PNG всего вида (модель + связи, без аннотаций) в рамке обрезки. -> (путь, из_кэша) / (None, причина)."""
    lis = visible_links(doc, view, links)
    d = cache_dir(doc)
    png = os.path.join(d, u'full_%d.png' % _iv(view.Id))
    meta = os.path.join(d, u'full_%d.json' % _iv(view.Id))
    key = key_of(doc, view, lis, dpi)
    if not force and os.path.isfile(png) and _read(meta) == key:
        return png, True
    t0 = time.time()
    w, h, c, tr = _crop(view)
    hide = _annotation_ids(doc, view)
    ap = view.get_Parameter(BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
    old_ann = ap.AsInteger() if ap is not None else 0
    old_vis = view.CropBoxVisible
    t = Transaction(doc, u'XYZ: снимок вида — скрыть надписи')
    t.Start()
    try:
        if hide:
            view.HideElements(List[ElementId](hide))
        view.CropBoxVisible = False
        if ap is not None and old_ann:
            ap.Set(0)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    px = max(500, min(int(round(w / float(view.Scale) * 12.0 * dpi)), 15000))
    pref = u'_expf_%d' % _iv(view.Id)
    for f in os.listdir(d):
        if f.startswith(pref):
            try:
                os.remove(os.path.join(d, f))
            except Exception:
                pass
    o = ImageExportOptions()
    o.ExportRange = ExportRange.SetOfViews
    o.SetViewsAndSheets(List[ElementId]([view.Id]))
    o.FilePath = os.path.join(d, pref)
    o.HLRandWFViewsFileType = ImageFileType.PNG
    o.ShadowViewsFileType = ImageFileType.PNG
    o.ImageResolution = ImageResolution.DPI_300
    o.ZoomType = ZoomFitType.FitToPage
    o.FitDirection = FitDirectionType.Horizontal
    o.PixelSize = px
    try:
        doc.ExportImage(o)
    finally:
        t2 = Transaction(doc, u'XYZ: снимок вида — вернуть надписи')
        t2.Start()
        if hide:
            view.UnhideElements(List[ElementId](hide))
        view.CropBoxVisible = old_vis
        if ap is not None and old_ann:
            ap.Set(old_ann)
        t2.Commit()
    made = [f for f in os.listdir(d) if f.startswith(pref) and f.lower().endswith(u'.png')]
    if not made:
        return None, u'Revit не сохранил картинку'
    if os.path.isfile(png):
        os.remove(png)
    os.rename(os.path.join(d, made[0]), png)
    with io.open(meta, 'w', encoding='utf-8') as f:
        f.write(unicode(key))   # noqa: F821
    if log:
        log(u'снимок вида «%s»: %d px, скрыто надписей %d, %.1f с' % (view.Name, px, len(hide), time.time() - t0))
    return png, False


def place_front(doc, view, png, links=None, log=None):
    """Снимок вида — передним планом в рамку обрезки, связи скрыть. Внутри открытой транзакции."""
    w, h, c, tr = _crop(view)
    it = ImageType.Create(doc, ImageTypeOptions(png, False, ImageTypeSource.Import))
    inst = ImageInstance.Create(doc, view, it.Id, ImagePlacementOptions(c, BoxPlacement.Center))
    inst.Width = w
    inst.DrawLayer = DrawLayer.Foreground
    ang = math.atan2(tr.BasisX.Y, tr.BasisX.X)
    if abs(ang) > 1e-6:
        ElementTransformUtils.RotateElement(doc, inst.Id, Line.CreateBound(c, c + XYZ.BasisZ), ang)
    lis = visible_links(doc, view, links)
    hide = [x.Id for x in lis] + list(_link_tags(doc, view))
    if hide:
        view.HideElements(List[ElementId](hide))
    if log:
        log(u'снимок поверх «%s»: %.3f × %.3f фут (рамка %.3f × %.3f)' % (view.Name, inst.Width, inst.Height, w, h))
    return inst
