# -*- coding: utf-8 -*-
"""Отдельные 3D-виды по системам (ХУЙ.extension/lib/hui_sysviews.py), db 20.10.2026.

Кнопка «Захуярить → Система отдельно». Отдельный вид системы:
- на виде остаётся только выбранная система: фильтр «db_НЕ_система_<имя>» прячет всё,
  у чего в «Имени системы» нет этого имени отдельным словом (имена через запятую:
  установка «П.1,В.1,Х1.ККБ1» видна и на виде П.1, и на виде В.1);
- оформление — только фильтрами (правило проекта), категории не из сети скрыты;
- рамка 3D-вида — по элементам системы с запасом 300 мм;
- повторный запуск обновляет фильтр и рамку существующего вида «db_Система_<имя>».
"""
from Autodesk.Revit.DB import *
from System.Collections.Generic import List

PREFIX_VIEW = u'db_Система_'
PREFIX_FILTER = u'db_НЕ_система_'
FT = 304.8
DUCT = [BuiltInCategory.OST_DuctCurves, BuiltInCategory.OST_DuctFitting, BuiltInCategory.OST_DuctAccessory,
        BuiltInCategory.OST_DuctTerminal, BuiltInCategory.OST_FlexDuctCurves, BuiltInCategory.OST_DuctInsulations,
        BuiltInCategory.OST_MechanicalEquipment]
PIPE = [BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_PipeFitting, BuiltInCategory.OST_PipeAccessory,
        BuiltInCategory.OST_FlexPipeCurves, BuiltInCategory.OST_PipeInsulations, BuiltInCategory.OST_MechanicalEquipment,
        BuiltInCategory.OST_PlumbingFixtures, BuiltInCategory.OST_Sprinklers]
BAD = u'\\:{}[]|;<>?`~'


def _iv(i):
    try:
        return i.Value
    except Exception:
        return i.IntegerValue


def safe(name):
    for ch in BAD:
        name = name.replace(ch, u'_')
    return name


def systems(doc):
    """{имя системы: 'duct'|'pipe'} — из систем модели."""
    from Autodesk.Revit.DB.Mechanical import MechanicalSystem
    from Autodesk.Revit.DB.Plumbing import PipingSystem
    res = {}
    for cls, kind in ((PipingSystem, 'pipe'), (MechanicalSystem, 'duct')):
        for s in FilteredElementCollector(doc).OfClass(cls):
            if s.Name:
                res[s.Name] = kind
    return res


def tokens(e):
    p = e.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
    s = p.AsString() if p is not None else None
    return [t.strip() for t in s.split(u',')] if s else []


def _cats(doc, bics):
    ids = List[ElementId]()
    for b in bics:
        c = doc.Settings.Categories.get_Item(b)
        if c is not None:
            ids.Add(c.Id)
    return ids


def hide_filter(doc, name, bics):
    """Фильтр: спрятать всё, где имени системы нет отдельным словом."""
    p = ElementId(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
    rules = List[FilterRule]()
    rules.Add(ParameterFilterRuleFactory.CreateNotEqualsRule(p, name))
    rules.Add(ParameterFilterRuleFactory.CreateNotBeginsWithRule(p, name + u','))
    rules.Add(ParameterFilterRuleFactory.CreateNotContainsRule(p, u',' + name + u','))
    rules.Add(ParameterFilterRuleFactory.CreateNotEndsWithRule(p, u',' + name))
    ef = ElementParameterFilter(rules)
    fname = PREFIX_FILTER + safe(name)
    for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement):
        if f.Name == fname:
            f.SetCategories(_cats(doc, bics))
            f.SetElementFilter(ef)
            return f
    return ParameterFilterElement.Create(doc, fname, _cats(doc, bics), ef)


def bbox(doc, name, bics, margin_mm=300):
    x0 = y0 = z0 = 1e9
    x1 = y1 = z1 = -1e9
    n = 0
    for b in bics:
        for e in FilteredElementCollector(doc).OfCategory(b).WhereElementIsNotElementType():
            if name not in tokens(e):
                continue
            bb = e.get_BoundingBox(None)
            if bb is None:
                continue
            n += 1
            x0, y0, z0 = min(x0, bb.Min.X), min(y0, bb.Min.Y), min(z0, bb.Min.Z)
            x1, y1, z1 = max(x1, bb.Max.X), max(y1, bb.Max.Y), max(z1, bb.Max.Z)
    if not n:
        return None, 0
    m = margin_mm / FT
    box = BoundingBoxXYZ()
    box.Min = XYZ(x0 - m, y0 - m, z0 - m)
    box.Max = XYZ(x1 + m, y1 + m, z1 + m)
    return box, n


def make_view(doc, name, kind, template=None, extra_filters=()):
    """Создать/обновить 3D-вид «db_Система_<имя>». Возвращает (вид, число элементов, новый?)."""
    bics = DUCT if kind == 'duct' else PIPE
    f = hide_filter(doc, name, bics)
    vname = PREFIX_VIEW + safe(name)
    view = None
    for v in FilteredElementCollector(doc).OfClass(View3D):
        if not v.IsTemplate and v.Name == vname:
            view = v
            break
    new = view is None
    if new:
        vft = [t for t in FilteredElementCollector(doc).OfClass(ViewFamilyType)
               if t.ViewFamily == ViewFamily.ThreeDimensional][0]
        view = View3D.CreateIsometric(doc, vft.Id)
        view.Name = vname
        if template is not None:
            view.ViewTemplateId = template.Id
        else:
            view.ViewTemplateId = ElementId.InvalidElementId
            view.DetailLevel = ViewDetailLevel.Fine
            view.DisplayStyle = DisplayStyle.HLR
            keep = set(_iv(c) for c in _cats(doc, bics))
            for c in doc.Settings.Categories:
                if c.CategoryType == CategoryType.Model and _iv(c.Id) not in keep and view.CanCategoryBeHidden(c.Id):
                    try:
                        view.SetCategoryHidden(c.Id, True)
                    except Exception:
                        pass
            for b in (BuiltInCategory.OST_RvtLinks, BuiltInCategory.OST_Grids, BuiltInCategory.OST_Levels):
                cid = ElementId(b)
                if view.CanCategoryBeHidden(cid):
                    view.SetCategoryHidden(cid, True)
    ids = [_iv(i) for i in view.GetFilters()]
    if _iv(f.Id) not in ids:
        view.AddFilter(f.Id)
    view.SetFilterVisibility(f.Id, False)
    # db 22.10: изоляция на схемах не нужна — фильтром «db_Изоляция_скрыть» (только категории)
    ins = None
    for x in FilteredElementCollector(doc).OfClass(ParameterFilterElement):
        if x.Name == u'db_Изоляция_скрыть':
            ins = x
            break
    if ins is None:
        ins = ParameterFilterElement.Create(doc, u'db_Изоляция_скрыть',
                                            _cats(doc, [BuiltInCategory.OST_DuctInsulations, BuiltInCategory.OST_PipeInsulations]))
    if _iv(ins.Id) not in [_iv(i) for i in view.GetFilters()]:
        view.AddFilter(ins.Id)
    view.SetFilterVisibility(ins.Id, False)
    for ef in extra_filters:
        if _iv(ef.Id) not in [_iv(i) for i in view.GetFilters()]:
            view.AddFilter(ef.Id)
    box, n = bbox(doc, name, bics)
    if box is not None:
        view.SetSectionBox(box)
        view.IsSectionBoxActive = True
    return view, n, new
