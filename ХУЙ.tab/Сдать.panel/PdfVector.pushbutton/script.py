# -*- coding: utf-8 -*-
"""PDF вектор — листы в лёгкий векторный PDF без растровых «плиток».

Почему обычный векторный экспорт тяжёлый: Revit молча превращает в картинку ВЕСЬ лист,
если на нём есть вид со стилем «Тонированный»/«Реалистичный», с «Глубиной», тенями,
прозрачностью модели или фоном (градиент, небо). Такой лист весит 0,7–1,5 МБ и печатается
медленно, а линии на нём размыты.

Что делает кнопка:
  1. На время экспорта (в откатываемой группе — модель не меняется) у видов на выбранных
     листах выключает всё, что включает растр: тени, «Глубину», фон, прозрачность модели,
     эскизные линии, освещение; «Тонированный» заменяет на «Одноцветный» (цвета остаются,
     лист — вектором).
  2. Экспортирует листы в PDF по одному (быстрее и надёжнее одного большого файла).
     Подложки связей (АР/IFC и др.) на планах и разрезах — снимком (db 22.10.2026, lib/hui_snap.py):
     всё своё (сети, оборудование, марки, тексты) — вектором, связи — одной картинкой фоном.
     Снимки кешируются (%APPDATA%\ХУЙ_pdfsnap) и пересоздаются, только если изменились вид или файл связи.
     Если снимок не получился — этот лист растром 150 dpi (запасной путь).
     Остановить выгрузку: положить в папку выгрузки файл «СТОП» (любой, например СТОП.txt) —
     кнопка закончит текущий лист, откатит модель и остановится.
  3. В фоне (Python) доводит PDF: убирает повторно нарисованные линии, линии и заливки,
     полностью закрытые другими, «пылинки», лишнюю точность координат; склеивает листы
     в один файл, логотип штампа хранится один раз. Revit в это время свободен.
     По окончании откроется папка и появится сообщение с размером.
"""
import os
import io
import re
import json
import time
import datetime
import clr
# Revit 2025 (.NET 8): Process живёт в отдельной сборке — подключить явно
for _asm in ('System.Diagnostics.Process', 'System.ComponentModel.Primitives'):
    try:
        clr.AddReference(_asm)
    except Exception:
        pass
from System.Diagnostics import Process, ProcessStartInfo

from pyrevit import revit, DB
from System.Collections.Generic import List
import hui_forms as forms
import sys
sys.modules.pop('hui_snap', None)
import hui_snap

doc = revit.doc
uidoc = revit.uidoc
TITLE = u'PDF вектор'

# параметры «Графика» (Параметры отображения графики) — всё, что может включить растр
# «Модель» (стиль, рёбра, силуэты…) НЕ берём из шаблона: в ней хранится, что видно на схемах;
# стиль и прозрачность модели правим у самого вида (или у его шаблона).
GDO = (DB.BuiltInParameter.GRAPHIC_DISPLAY_OPTIONS_SHADOWS,
       DB.BuiltInParameter.GRAPHIC_DISPLAY_OPTIONS_SKETCHY_LINES,
       DB.BuiltInParameter.GRAPHIC_DISPLAY_OPTIONS_FOG,
       DB.BuiltInParameter.GRAPHIC_DISPLAY_OPTIONS_LIGHTING,
       DB.BuiltInParameter.GRAPHIC_DISPLAY_OPTIONS_PHOTO_EXPOSURE,
       DB.BuiltInParameter.GRAPHIC_DISPLAY_OPTIONS_BACKGROUND)
SHADED = (DB.DisplayStyle.Shading, DB.DisplayStyle.ShadingWithEdges,
          DB.DisplayStyle.Realistic, DB.DisplayStyle.RealisticWithEdges)
LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), u'..', u'..', u'..', u'lib'))


def iv(eid):
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


def nat_key(text):
    return [(0, int(p), u'') if p.isdigit() else (1, 0, p.lower()) for p in re.split(u'(\\d+)', text or u'') if p]


def safe(name):
    return re.sub(u'[\\\\/:*?"<>|]+', u'_', name).strip() or u'лист'


def collection(sheet):
    try:
        el = doc.GetElement(sheet.SheetCollectionId)
        return el.Name if el is not None else u''
    except Exception:
        return u''


# ---------------------------------------------------------------- выбор листов
def pick_sheets():
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    sel = [s for s in sel if isinstance(s, DB.ViewSheet)]
    allsh = [s for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet) if not s.IsPlaceholder]
    allsh.sort(key=lambda s: (nat_key(collection(s)), nat_key(s.SheetNumber)))
    if sel:
        sel.sort(key=lambda s: (nat_key(collection(s)), nat_key(s.SheetNumber)))
        res = forms.CommandSwitchWindow.show([u'Выделенные листы (%d)' % len(sel), u'Выбрать из списка'],
                                             title=TITLE, message=u'Какие листы выгрузить?')
        if not res:
            return None
        if res.startswith(u'Выделенные'):
            return sel
    labels = []
    for s in allsh:
        c = collection(s)
        labels.append(u'%s%s - %s' % (u'[%s]  ' % c if c else u'', s.SheetNumber, s.Name))
    picked = forms.SelectFromList.show(labels, multiselect=True, title=u'Листы для PDF', button_name=u'Дальше')
    if not picked:
        return None
    ps = set(picked)
    return [s for s, l in zip(allsh, labels) if l in ps]


# ---------------------------------------------------------------- листы «растром» (помечает db)
# Листы, где вектор + снимок подложки не совпадает с экраном (кровли: связь АР на экране закрывает сети
# нижних этажей, снимок — нет), db помечает при выгрузке; пометки запоминаются для модели
# (%APPDATA%\pyRevit\hui_settings.json, ключ pdf_raster: {модель: [UniqueId листов]}). db 22.10.2026.
def pick_raster(sheets):
    import hui_settings
    from pyrevit import forms as _pf
    key = doc.Title
    allm = hui_settings.get('pdf_raster') or {}
    marked = set(allm.get(key, []))
    items = []
    for s in sheets:
        c = collection(s)
        it = _pf.TemplateListItem(u'%s%s - %s' % (u'[%s]  ' % c if c else u'', s.SheetNumber, s.Name),
                                  checked=s.UniqueId in marked)
        it.sheet = s
        items.append(it)
    res = forms.SelectFromList.show(items, multiselect=True, title=u'Какие листы — растром',
                                    button_name=u'Дальше')
    labels = set(res or [])          # ничего не отмечено (или окно закрыто) — растром ничего
    ras = set()
    for it in items:
        if it.item in labels:
            ras.add(iv(it.sheet.Id))
            marked.add(it.sheet.UniqueId)
        else:
            marked.discard(it.sheet.UniqueId)
    allm[key] = sorted(marked)
    try:
        hui_settings.set('pdf_raster', allm)
    except Exception:
        pass
    return ras


# ---------------------------------------------------------------- тяжёлые листы
PLAN_TYPES = (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.EngineeringPlan, DB.ViewType.AreaPlan,
              DB.ViewType.Section, DB.ViewType.Elevation, DB.ViewType.Detail)


def heavy_sheet(sheet, links):
    """Лист тяжёлый, если на нём план/разрез, где видна связь (АР, IFC-подложка): вектором такой лист —
    до 50 МБ и минуты доводки, растром — пара МБ и секунды (db 22.10.2026)."""
    lid = DB.ElementId(DB.BuiltInCategory.OST_RvtLinks)
    for vid in sheet.GetAllPlacedViews():
        v = doc.GetElement(vid)
        if v is None or v.ViewType not in PLAN_TYPES:
            continue
        try:
            if v.GetCategoryHidden(lid):
                continue
        except Exception:
            pass
        for li in links:
            try:
                if not li.IsHidden(v):
                    return True
            except Exception:
                return True
    return False


# ---------------------------------------------------------------- подготовка видов
def clean_template():
    """Временный шаблон, управляющий только «Графикой»: всё выключено (берётся у нового 3D-вида)."""
    vft = [t for t in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
           if t.ViewFamily == DB.ViewFamily.ThreeDimensional][0]
    v = DB.View3D.CreateIsometric(doc, vft.Id)
    v.ViewTemplateId = DB.ElementId.InvalidElementId
    v.DisplayStyle = DB.DisplayStyle.HLR
    m = v.GetViewDisplayModel()
    m.Transparency = 0
    m.SmoothEdges = False
    v.SetViewDisplayModel(m)
    t = v.CreateViewTemplate()
    keep = set(int(g) for g in GDO)
    t.SetNonControlledTemplateParameterIds(List[DB.ElementId]([i for i in t.GetTemplateParameterIds() if iv(i) not in keep]))
    return t


def prepare_views(sheets, flat):
    tmpl = clean_template()
    keep = [DB.ElementId(g) for g in GDO]
    freed = set(); done = set(); n = 0; nshade = 0
    for sh in sheets:
        for vid in sh.GetAllPlacedViews():
            if iv(vid) in done:
                continue
            done.add(iv(vid))
            v = doc.GetElement(vid)
            if v is None or isinstance(v, DB.ViewSchedule) or v.ViewType in (DB.ViewType.Legend, DB.ViewType.DraftingView):
                continue
            ds = v.DisplayStyle      # как сейчас видно на листе (с учётом шаблона)
            tid = v.ViewTemplateId
            if isinstance(v, DB.View3D):
                # 3D-схемы: шаблон «Графики» не применять — после него на схемах проявляется
                # скрытая архитектура (стены, перекрытия). Достаточно «Одноцветного» стиля.
                owner = v
                if iv(tid) > 0:
                    T = doc.GetElement(tid)
                    mid = int(DB.BuiltInParameter.GRAPHIC_DISPLAY_OPTIONS_MODEL)
                    if not any(iv(i) == mid for i in T.GetNonControlledTemplateParameterIds()):
                        owner = T
                try:
                    if ds in SHADED:
                        if flat:
                            owner.DisplayStyle = DB.DisplayStyle.FlatColors
                        nshade += 1
                except Exception:
                    pass
                n += 1
                continue
            if iv(tid) > 0 and iv(tid) not in freed:
                T = doc.GetElement(tid)
                nc = list(T.GetNonControlledTemplateParameterIds())
                have = set(iv(i) for i in nc)
                for k in keep:
                    if iv(k) not in have:
                        nc.append(k)
                T.SetNonControlledTemplateParameterIds(List[DB.ElementId](nc))
                freed.add(iv(tid))
            try:
                v.ApplyViewTemplateParameters(tmpl)
            except Exception:
                continue
            try:
                dc = v.GetDepthCueing()
                if dc.EnableDepthCueing:
                    dc.EnableDepthCueing = False
                    v.SetDepthCueing(dc)
            except Exception:
                pass
            # стиль и прозрачность модели — там, где они заданы (шаблон или сам вид)
            owner = v
            if iv(tid) > 0:
                T = doc.GetElement(tid)
                mid = int(DB.BuiltInParameter.GRAPHIC_DISPLAY_OPTIONS_MODEL)
                if not any(iv(i) == mid for i in T.GetNonControlledTemplateParameterIds()):
                    owner = T
            try:
                if ds in SHADED:
                    if flat:
                        owner.DisplayStyle = DB.DisplayStyle.FlatColors
                    nshade += 1
                m = owner.GetViewDisplayModel()
                if m.Transparency or m.SmoothEdges:
                    m.Transparency = 0
                    m.SmoothEdges = False
                    owner.SetViewDisplayModel(m)
            except Exception:
                pass
            n += 1
    return n, nshade


def raster_visible(sheets):
    """Категория «Растровые изображения» видна на видах листов (иначе снимок подложки не печатается)."""
    rc = DB.ElementId(DB.BuiltInCategory.OST_RasterImages)
    mp = int(DB.BuiltInParameter.VIS_GRAPHICS_MODEL)
    for sh in sheets:
        for vid in sh.GetAllPlacedViews():
            v = doc.GetElement(vid)
            if v is None or v.ViewType not in PLAN_TYPES:
                continue
            o = v
            if iv(v.ViewTemplateId) > 0:
                T = doc.GetElement(v.ViewTemplateId)
                if not any(iv(i) == mp for i in T.GetNonControlledTemplateParameterIds()):
                    o = T
            try:
                if o.GetCategoryHidden(rc):
                    o.SetCategoryHidden(rc, False)
            except Exception:
                pass


def export_options():
    o = DB.PDFExportOptions()
    o.Combine = True
    o.HideCropBoundaries = True
    o.HideReferencePlane = True
    o.HideScopeBoxes = True
    o.ZoomType = DB.ZoomType.Zoom
    o.ZoomPercentage = 100
    o.ColorDepth = DB.ColorDepthType.Color
    o.RasterQuality = DB.RasterQualityType.High
    o.AlwaysUseRaster = False
    return o


def mmss(sec):
    sec = int(round(sec))
    m, s_ = divmod(sec, 60)
    return (u'%d мин %d с' % (m, s_)) if m else (u'%d с' % s_)


def run(exe, args, wait=True):
    psi = ProcessStartInfo(exe, args)
    psi.UseShellExecute = False
    psi.CreateNoWindow = True
    if wait:
        psi.RedirectStandardOutput = True
        psi.RedirectStandardError = True
    p = Process.Start(psi)
    if not wait:
        return p, None
    out = p.StandardOutput.ReadToEnd()
    p.WaitForExit(60000)
    return p, out


def find_py():
    for c in (u'py', u'python'):
        try:
            p, out = run(c, u'-c "import pikepdf; print(1)"')
            if p.ExitCode == 0 and u'1' in out:
                return c
        except Exception:
            pass
    return None


# ---------------------------------------------------------------- главное
def main():
    sheets = pick_sheets()
    if not sheets:
        return
    links = list(DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance))
    ras = pick_raster(sheets)          # помеченные db — растром, как на экране
    if ras is None:
        return
    heavy = set(iv(s.Id) for s in sheets if iv(s.Id) not in ras and heavy_sheet(s, links))
    # окно без галок (db 22.10.2026): всё нужное включено всегда — подложки связей снимком,
    # тонированные виды одноцветными, облегчение, один файл. Выбор только: снимки из кэша или заново.
    GO = u'Выгрузить'
    FRESH = u'Выгрузить, снимки подложек сделать заново'
    res = forms.CommandSwitchWindow.show(
        [GO, FRESH], title=TITLE,
        message=(u'Листов: %d, растром (помечены): %d, с подложкой связей: %d.\n'
                 u'Наши сети, марки, тексты — вектором; связи (АР, IFC) — снимком.\n'
                 u'Снимки берутся из кэша; «заново» — если меняли вид или связь, а снимок старый.\n'
                 u'Модель не изменится — всё откатывается.') % (len(sheets), len(ras), len(heavy)))
    if not res:
        return
    rast = True
    fresh = (res == FRESH)
    flat = True
    slim = True
    one = True
    if not rast:
        heavy = set()

    title = safe(doc.Title)
    stamp = datetime.datetime.now().strftime('%Y-%m-%d_%H%M')
    from System import Environment
    outdir = os.path.join(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), u'%s_PDF_%s' % (title, stamp))
    rawdir = os.path.join(outdir, u'_raw')
    if not os.path.isdir(rawdir):
        os.makedirs(rawdir)
    py = find_py()            # доводка нужна всегда: текст ведомостей (db 19.10)

    t0 = time.time(); raw = []; text = {}; rasters = []
    logp = os.path.join(outdir, u'%s_лог_Revit.txt' % title)     # рядом с PDF: _raw доводка удаляет (27.10.2026)

    def log(msg):
        try:
            with io.open(logp, 'a', encoding='utf-8') as f:
                f.write(u'%s  %s\n' % (datetime.datetime.now().strftime('%H:%M:%S'), msg))
        except Exception:
            pass

    def stop_asked():
        try:
            return any(f.upper().startswith(u'СТОП') or f.upper().startswith(u'STOP') for f in os.listdir(outdir))
        except Exception:
            return False
    nsnap = [0, 0]      # сделано, из кэша
    fallback = set()    # листы, где снимок не вышел → растр
    nviews = nshade = 0
    err = None
    tg = DB.TransactionGroup(doc, u'ХУЙ: PDF вектор (откат)')
    tg.Start()
    try:
        t = DB.Transaction(doc, u'Подготовка видов к векторному PDF')
        t.Start()
        try:
            nviews, nshade = prepare_views(sheets, flat)
            t.Commit()
        except Exception as ex:
            # транзакцию закрыть ДО отката группы — иначе Revit падает
            if t.GetStatus() == DB.TransactionStatus.Started:
                t.RollBack()
            raise
        # подложки связей — снимком (до экспорта, в той же откатываемой группе)
        if heavy:
            log(u'снимки подложек: листов %d' % len(heavy))
            plan = []
            for s in sheets:
                if iv(s.Id) not in heavy:
                    continue
                for v in hui_snap.link_views(doc, s, links):
                    plan.append((s, v))
            done_v = {}
            for s, v in plan:
                if stop_asked():
                    raise Exception(u'остановлено файлом СТОП')
                if iv(v.Id) in done_v:
                    continue
                try:
                    png, cached = hui_snap.snapshot(doc, v, links, dpi=200, force=fresh, log=log)
                except Exception as ex:
                    png, cached = None, u'%s' % ex
                if png is None:
                    log(u'снимок «%s» не вышел: %s — лист %s растром' % (v.Name, cached, s.SheetNumber))
                    fallback.add(iv(s.Id))
                    continue
                nsnap[1 if cached is True else 0] += 1
                done_v[iv(v.Id)] = png
            t = DB.Transaction(doc, u'Подложки связей снимком')
            t.Start()
            try:
                raster_visible([s for s, _ in plan])
                for vid, png in done_v.items():
                    v = [x for _, x in plan if iv(x.Id) == vid][0]
                    hui_snap.place(doc, v, png, links, log=log)
                t.Commit()
            except Exception:
                if t.GetStatus() == DB.TransactionStatus.Started:
                    t.RollBack()
                raise
            log(u'снимков: новых %d, из кэша %d, %.1f с' % (nsnap[0], nsnap[1], time.time() - t0))
        # помеченные db листы — «как на экране»: снимок всего вида (модель + связи) передним планом,
        # надписи вектором. Не вышло — лист растром (запасной путь).
        rfail = set()
        if ras:
            fplan = []
            for s in sheets:
                if iv(s.Id) not in ras:
                    continue
                for vid in s.GetAllPlacedViews():
                    v = doc.GetElement(vid)
                    if v is None or v.ViewType not in PLAN_TYPES:
                        continue
                    if not v.CropBoxActive:
                        rfail.add(iv(s.Id))
                        log(u'«%s» без обрезки — лист %s растром' % (v.Name, s.SheetNumber))
                        continue
                    fplan.append((s, v))
            fdone = {}
            for s, v in fplan:
                if stop_asked():
                    raise Exception(u'остановлено файлом СТОП')
                if iv(v.Id) in fdone or iv(s.Id) in rfail:
                    continue
                try:
                    png, cached = hui_snap.snapshot_full(doc, v, links, dpi=200, force=fresh, log=log)
                except Exception as ex:
                    png, cached = None, u'%s' % ex
                if png is None:
                    log(u'снимок вида «%s» не вышел: %s — лист %s растром' % (v.Name, cached, s.SheetNumber))
                    rfail.add(iv(s.Id))
                    continue
                fdone[iv(v.Id)] = (s, v, png)
            t = DB.Transaction(doc, u'Снимок вида поверх')
            t.Start()
            try:
                raster_visible([s for s, _ in fplan])
                for s, v, png in fdone.values():
                    if iv(s.Id) not in rfail:
                        hui_snap.place_front(doc, v, png, links, log=log)
                t.Commit()
            except Exception:
                if t.GetStatus() == DB.TransactionStatus.Started:
                    t.RollBack()
                raise
        heavy = fallback | rfail  # растром — только где снимок не получился
        opts = export_options()
        if py is None and one and not heavy:
            opts.FileName = title
            doc.Export(outdir, List[DB.ElementId]([s.Id for s in sheets]), opts)
        else:
            ropts = export_options()
            ropts.AlwaysUseRaster = True
            ropts.RasterQuality = DB.RasterQualityType.Medium      # 150 dpi: читается, лист A1 — пара МБ
            for i, s in enumerate(sheets):
                if stop_asked():
                    raise Exception(u'остановлено файлом СТОП (готово листов: %d)' % i)
                name = u'%02d_%s' % (i + 1, safe(s.SheetNumber))
                o = ropts if iv(s.Id) in heavy else opts
                o.FileName = name
                ts = time.time()
                doc.Export(rawdir, List[DB.ElementId]([s.Id]), o)
                try:
                    log(u'лист %s «%s»: %s, %.1f с, %.2f МБ' % (s.SheetNumber, s.Name, u'растр' if o is ropts else u'вектор',
                        time.time() - ts, os.path.getsize(os.path.join(rawdir, name + u'.pdf')) / 1048576.0))
                except Exception:
                    pass
                raw.append(os.path.join(rawdir, name + u'.pdf'))
                if iv(s.Id) in heavy:
                    rasters.append(raw[-1])
                    continue
                # ведомости листа: их текст в PDF шире, чем в Revit, — доводка сожмёт (db 19.10)
                text[raw[-1]] = [int(iv(x.Id)) for x in DB.FilteredElementCollector(doc, s.Id)   # Int64 → int: иначе json не пишет
                                 .OfClass(DB.ScheduleSheetInstance)]
    except Exception as ex:
        err = ex
    finally:
        if tg.GetStatus() == DB.TransactionStatus.Started:
            tg.RollBack()
    if err is not None:
        forms.alert(u'Экспорт прерван, модель возвращена как была.\n\n%s' % err, title=TITLE)
        return
    t_exp = time.time() - t0

    if py is None:
        if one:
            try:
                os.rmdir(rawdir)
            except Exception:
                pass
        forms.alert(u'PDF выгружен (%d листов, %s), но без доводки: не найден Python с библиотеками.\n'
                    u'Текст ведомостей может вылезать за рамки ячеек.\n'
                    u'Поставить: py -m pip install --user pikepdf shapely numpy' % (len(sheets), mmss(t_exp)),
                    title=TITLE)
        run(u'explorer.exe', u'"%s"' % outdir, wait=False)
        return
    raw = [f for f in raw if os.path.isfile(f)]
    job = {'raw': raw, 'out': os.path.join(outdir, title + u'.pdf'), 'slim': slim, 'combine': one,
           'raster': [f for f in rasters if os.path.isfile(f)], 'max_mb': 30,
           'text': dict((k, v) for k, v in text.items() if v), 'text_fx': 0.96,
           'tol_mm': 0.02, 'speck_mm': 0.05, 'title': TITLE, 'keep_raw': False, 't_export': t_exp}
    jp = os.path.join(outdir, u'_job.json')
    with io.open(jp, 'w', encoding='utf-8') as f:
        f.write(json.dumps(job, ensure_ascii=False))
    run(py, u'"%s" "%s"' % (os.path.join(LIB, u'hui_pdfslim.py'), jp), wait=False)
    forms.alert(u'Revit закончил: %d листов за %s (растром: %d; видов подготовлено: %d, из них тонированных: %d).\n\n'
                u'Доводка PDF идёт в фоне — Revit свободен. Когда закончится, откроется папка:\n%s'
                % (len(sheets), mmss(t_exp), len(rasters), nviews, nshade, outdir), title=TITLE)


main()
