# -*- coding: utf-8 -*-
"""
Умные выноски: автоматическая расстановка марок на виде (3D / план / разрез)
без пересечений текста и выносок.

Вызывается из script.py:  smart_tags.run(doc, uidoc, preselected_elements)

v2 (10.2026): взрыв-схемы (DisplacementElement), отбор по фильтру вида,
отдельные марки для стояков и для пересечений/магистралей, только тройники.
Все изменения собираются в одну группу транзакций - отменяются одним Ctrl+Z.
"""
import math
import random
import json
import time

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Collections.Generic import List
import System.Windows as SW
# WPF-контролы - только через префикс WC: "from Autodesk.Revit.DB import *" ниже
# перекрывает имя Grid (ось здания в Revit)
import System.Windows.Controls as WC
import System.Windows.Media as SWM

from Autodesk.Revit.DB import *
from pyrevit import forms, script
import xyz_forms as forms   # единый стиль окон XYZ (lib/xyz_forms.py)


FT_MM = 304.8

# ============================================================
# 1. Категории и марки
# ============================================================
_TAG_MAP_NAMES = [
    ('OST_DuctCurves', 'OST_DuctTags'),
    ('OST_FlexDuctCurves', 'OST_FlexDuctTags'),
    ('OST_PipeCurves', 'OST_PipeTags'),
    ('OST_FlexPipeCurves', 'OST_FlexPipeTags'),
    ('OST_DuctFitting', 'OST_DuctFittingTags'),
    ('OST_PipeFitting', 'OST_PipeFittingTags'),
    ('OST_DuctAccessory', 'OST_DuctAccessoryTags'),
    ('OST_PipeAccessory', 'OST_PipeAccessoryTags'),
    ('OST_DuctTerminal', 'OST_DuctTerminalTags'),
    ('OST_MechanicalEquipment', 'OST_MechanicalEquipmentTags'),
    ('OST_PlumbingFixtures', 'OST_PlumbingFixtureTags'),
    ('OST_PlumbingEquipment', 'OST_PlumbingEquipmentTags'),
    ('OST_Sprinklers', 'OST_SprinklerTags'),
    ('OST_DuctInsulations', 'OST_DuctInsulationsTags'),
    ('OST_PipeInsulations', 'OST_PipeInsulationsTags'),
    ('OST_DuctLinings', 'OST_DuctLiningsTags'),
    ('OST_CableTray', 'OST_CableTrayTags'),
    ('OST_CableTrayFitting', 'OST_CableTrayFittingTags'),
    ('OST_Conduit', 'OST_ConduitTags'),
    ('OST_ConduitFitting', 'OST_ConduitFittingTags'),
    ('OST_MechanicalControlDevices', 'OST_MechanicalControlDeviceTags'),
    ('OST_ElectricalEquipment', 'OST_ElectricalEquipmentTags'),
    ('OST_ElectricalFixtures', 'OST_ElectricalFixtureTags'),
    ('OST_LightingFixtures', 'OST_LightingFixtureTags'),
    ('OST_LightingDevices', 'OST_LightingDeviceTags'),
    ('OST_FireAlarmDevices', 'OST_FireAlarmDeviceTags'),
    ('OST_DataDevices', 'OST_DataDeviceTags'),
    ('OST_CommunicationDevices', 'OST_CommunicationDeviceTags'),
    ('OST_SecurityDevices', 'OST_SecurityDeviceTags'),
    ('OST_NurseCallDevices', 'OST_NurseCallDeviceTags'),
    ('OST_TelephoneDevices', 'OST_TelephoneDeviceTags'),
    ('OST_GenericModel', 'OST_GenericModelTags'),
    ('OST_SpecialityEquipment', 'OST_SpecialityEquipmentTags'),
    ('OST_Furniture', 'OST_FurnitureTags'),
    ('OST_StructuralFraming', 'OST_StructuralFramingTags'),
    ('OST_StructuralColumns', 'OST_StructuralColumnTags'),
    ('OST_Columns', 'OST_ColumnTags'),
    ('OST_Walls', 'OST_WallTags'),
    ('OST_Doors', 'OST_DoorTags'),
    ('OST_Windows', 'OST_WindowTags'),
]

# Сегменты сетей - объединяются в участки
_CURVE_CATS = ['OST_DuctCurves', 'OST_FlexDuctCurves', 'OST_PipeCurves',
               'OST_FlexPipeCurves', 'OST_CableTray', 'OST_Conduit']
# Через что можно "пройти", объединяя сегменты в участок (элементы с 2 соединителями)
_PASS_CATS = ['OST_DuctFitting', 'OST_PipeFitting', 'OST_DuctAccessory',
              'OST_PipeAccessory', 'OST_CableTrayFitting', 'OST_ConduitFitting']
# Не считаются препятствиями (перекрывают весь вид или не видны)
_NO_OBSTACLE_CATS = ['OST_Floors', 'OST_Ceilings', 'OST_Roofs', 'OST_Topography',
                     'OST_Toposolid', 'OST_Rooms', 'OST_MEPSpaces', 'OST_Areas',
                     'OST_HVAC_Zones', 'OST_Mass', 'OST_Site', 'OST_Levels', 'OST_Grids',
                     'OST_Cameras', 'OST_Viewers', 'OST_SectionBox', 'OST_RvtLinks',
                     'OST_DuctSystem', 'OST_PipingSystem', 'OST_ElectricalCircuit',
                     'OST_DuctInsulations', 'OST_PipeInsulations', 'OST_DuctLinings',
                     'OST_IOSModelGroups', 'OST_SketchLines', 'OST_Parts',
                     'OST_VolumeOfInterest', 'OST_ReferencePlane', 'OST_Matchline',
                     'OST_CLines', 'OST_PointClouds', 'OST_Entourage', 'OST_Planting']


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _bic_id(name):
    bic = getattr(BuiltInCategory, name, None)
    if bic is None:
        return None
    try:
        return int(bic)
    except Exception:
        try:
            return int(bic.value__)
        except Exception:
            return None


def _cat_ids(names):
    res = set()
    for n in names:
        v = _bic_id(n)
        if v is not None:
            res.add(v)
    return res


TAG_MAP = {}
for _e, _t in _TAG_MAP_NAMES:
    _ei, _ti = _bic_id(_e), _bic_id(_t)
    if _ei is not None and _ti is not None:
        TAG_MAP[_ei] = _ti
CURVE_CATS = _cat_ids(_CURVE_CATS)
PASS_CATS = _cat_ids(_PASS_CATS)
NO_OBSTACLE_CATS = _cat_ids(_NO_OBSTACLE_CATS)
MULTI_TAG_CAT = _bic_id('OST_MultiCategoryTags')
DUCT_LIKE = _cat_ids(['OST_DuctCurves', 'OST_FlexDuctCurves'])
PIPE_LIKE = _cat_ids(['OST_PipeCurves', 'OST_FlexPipeCurves'])
INSUL_CATS = _cat_ids(['OST_DuctInsulations', 'OST_PipeInsulations', 'OST_DuctLinings'])
FITTING_CATS = _cat_ids(['OST_DuctFitting', 'OST_PipeFitting', 'OST_CableTrayFitting',
                         'OST_ConduitFitting'])


def _ename(el):
    try:
        return Element.Name.__get__(el)
    except Exception:
        try:
            return el.Name
        except Exception:
            return u"?"


def _sym_label(sym):
    return u"{} : {}".format(sym.FamilyName, _ename(sym))


def _cat_int(el):
    c = el.Category
    return _iv(c.Id) if c is not None else None


# ------------------------------------------------------------
# Взрыв-схемы: смещённые элементы (DisplacementElement).
# Location элемента остаётся на месте, а рисуется он со сдвигом -
# точки привязки марок нужно сдвигать на тот же вектор.
# get_BoundingBox(view) сдвиг уже учитывает.
# ------------------------------------------------------------
DISP = {}


def _load_displacements(doc, view):
    DISP.clear()
    if not isinstance(view, View3D):
        return 0
    n = 0
    for de in FilteredElementCollector(doc, view.Id).OfClass(DisplacementElement):
        try:
            vec = de.GetAbsoluteDisplacement()
            for eid in de.GetDisplacedElementIds():
                DISP[_iv(eid)] = vec
                n += 1
        except Exception:
            pass
    return n


def _dp(el):
    """Сдвиг элемента на взрыв-схеме (XYZ.Zero, если не смещён)."""
    try:
        v = DISP.get(_iv(el.Id))
        if v is not None:
            return v
        # изоляция/вложенные - по основе
        hid = getattr(el, 'HostElementId', None)
        if hid is not None:
            v = DISP.get(_iv(hid))
            if v is not None:
                return v
        sup = getattr(el, 'SuperComponent', None)
        if sup is not None:
            v = DISP.get(_iv(sup.Id))
            if v is not None:
                return v
    except Exception:
        pass
    return XYZ.Zero


def _is_vertical_curve(c):
    d = c.GetEndPoint(1) - c.GetEndPoint(0)
    return d.GetLength() > 1e-9 and abs(d.Normalize().Z) > 0.985


# ============================================================
# 2. Геометрия в плоскости вида
# ============================================================
class ViewFrame(object):
    def __init__(self, view):
        self.view = view
        self.R = view.RightDirection
        self.U = view.UpDirection
        self.D = view.ViewDirection
        self.is3d = isinstance(view, View3D)
        self.scale = max(1, view.Scale)
        self.mm = self.scale / FT_MM            # 1 мм листа в футах модели

    def uv(self, p):
        return (p.DotProduct(self.R), p.DotProduct(self.U))

    def box_uv(self, bb):
        """Проекция всех углов BoundingBoxXYZ на плоскость вида."""
        tr = bb.Transform
        us, vs = [], []
        for x in (bb.Min.X, bb.Max.X):
            for y in (bb.Min.Y, bb.Max.Y):
                for z in (bb.Min.Z, bb.Max.Z):
                    p = tr.OfPoint(XYZ(x, y, z))
                    us.append(p.DotProduct(self.R))
                    vs.append(p.DotProduct(self.U))
        return min(us), min(vs), max(us), max(vs)

    def crop_uv(self):
        v = self.view
        try:
            if not v.CropBoxActive:
                return None
            return self.box_uv(v.CropBox)
        except Exception:
            return None


def _curve_of(el):
    loc = el.Location
    if isinstance(loc, LocationCurve):
        return loc.Curve
    return None


def _anchor_of(el, frame):
    """Точка, куда смотрит выноска, для не-линейного элемента."""
    loc = el.Location
    dp = _dp(el)
    if isinstance(loc, LocationPoint):
        return loc.Point + dp
    if isinstance(loc, LocationCurve):
        return loc.Curve.Evaluate(0.5, True) + dp
    # изоляция - берём середину основы
    try:
        host = el.Document.GetElement(el.HostElementId)
        if host is not None and isinstance(host.Location, LocationCurve):
            return host.Location.Curve.Evaluate(0.5, True) + dp
    except Exception:
        pass
    bb = el.get_BoundingBox(frame.view)
    if bb is None:
        bb = el.get_BoundingBox(None)
    if bb is not None:
        return bb.Transform.OfPoint((bb.Min + bb.Max) * 0.5)
    return None


def _param_str(el, bip):
    try:
        p = el.get_Parameter(bip)
    except Exception:
        return u""
    if p is None or not p.HasValue:
        return u""
    st = p.StorageType
    if st == StorageType.String:
        return p.AsString() or u""
    if st == StorageType.Double:
        return u"%.4f" % p.AsDouble()
    if st == StorageType.Integer:
        return u"%d" % p.AsInteger()
    if st == StorageType.ElementId:
        return u"%d" % _iv(p.AsElementId())
    return u""


def _param_d(el, bip):
    try:
        p = el.get_Parameter(bip)
        if p is not None and p.HasValue and p.StorageType == StorageType.Double:
            return p.AsDouble()
    except Exception:
        pass
    return 0.0


def _curve_half_width(el, frame):
    """Половина ширины линейного элемента на виде (ft)."""
    ci = _cat_int(el)
    r = 0.0
    if ci in PIPE_LIKE:
        r = _param_d(el, BuiltInParameter.RBS_PIPE_OUTER_DIAMETER) / 2.0
        if r <= 0:
            r = _param_d(el, BuiltInParameter.RBS_PIPE_DIAMETER_PARAM) / 2.0
    elif ci in DUCT_LIKE:
        d = _param_d(el, BuiltInParameter.RBS_CURVE_DIAMETER_PARAM)
        if d > 0:
            r = d / 2.0
        else:
            w = _param_d(el, BuiltInParameter.RBS_CURVE_WIDTH_PARAM)
            h = _param_d(el, BuiltInParameter.RBS_CURVE_HEIGHT_PARAM)
            r = (max(w, h) if frame.is3d else w) / 2.0
    else:
        w = _param_d(el, BuiltInParameter.RBS_CABLETRAY_WIDTH_PARAM)
        if w <= 0:
            w = _param_d(el, BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM)
        r = w / 2.0
    r += _param_d(el, BuiltInParameter.RBS_REFERENCE_INSULATION_THICKNESS)
    return max(r, 0.6 * frame.mm)


# ============================================================
# 3. Карта занятости (препятствия)
# ============================================================
class OccGrid(object):
    def __init__(self, u0, v0, u1, v1, cell):
        self.u0, self.v0, self.cell = u0, v0, cell
        self.nx = max(1, int((u1 - u0) / cell) + 1)
        self.ny = max(1, int((v1 - v0) / cell) + 1)
        self.occ = bytearray(self.nx * self.ny)
        self.sat = None

    def _ix(self, u):
        return int((u - self.u0) / self.cell)

    def _iy(self, v):
        return int((v - self.v0) / self.cell)

    def stamp_rect(self, u0, v0, u1, v1):
        ix0, ix1 = max(0, self._ix(u0)), min(self.nx - 1, self._ix(u1))
        iy0, iy1 = max(0, self._iy(v0)), min(self.ny - 1, self._iy(v1))
        if ix1 < ix0 or iy1 < iy0:
            return
        n = ix1 - ix0 + 1
        ones = bytearray(b'\x01' * n)
        for iy in range(iy0, iy1 + 1):
            row = iy * self.nx
            self.occ[row + ix0: row + ix1 + 1] = ones

    def stamp_capsule(self, a, b, r):
        du, dv = b[0] - a[0], b[1] - a[1]
        length = math.sqrt(du * du + dv * dv)
        step = max(self.cell * 0.5, r * 0.5)
        n = int(length / step) + 1
        for k in range(n + 1):
            t = float(k) / n
            u, v = a[0] + du * t, a[1] + dv * t
            self.stamp_rect(u - r, v - r, u + r, v + r)

    def build(self):
        nx, ny = self.nx, self.ny
        w = nx + 1
        sat = [0] * (w * (ny + 1))
        occ = self.occ
        for iy in range(ny):
            run = 0
            base_o = iy * nx
            base_s = (iy + 1) * w
            prev_s = iy * w
            for ix in range(nx):
                run += occ[base_o + ix]
                sat[base_s + ix + 1] = sat[prev_s + ix + 1] + run
        self.sat = sat

    def rect_frac(self, u0, v0, u1, v1):
        """Доля занятых ячеек в прямоугольнике (вне сетки - свободно)."""
        ix0, ix1 = max(0, self._ix(u0)), min(self.nx - 1, self._ix(u1))
        iy0, iy1 = max(0, self._iy(v0)), min(self.ny - 1, self._iy(v1))
        if ix1 < ix0 or iy1 < iy0:
            return 0.0
        w = self.nx + 1
        s = self.sat
        tot = (s[(iy1 + 1) * w + ix1 + 1] - s[iy0 * w + ix1 + 1]
               - s[(iy1 + 1) * w + ix0] + s[iy0 * w + ix0])
        area = float((ix1 - ix0 + 1) * (iy1 - iy0 + 1))
        return tot / area

    def seg_frac(self, a, b):
        du, dv = b[0] - a[0], b[1] - a[1]
        length = math.sqrt(du * du + dv * dv)
        n = int(length / self.cell) + 1
        hit = 0
        for k in range(n + 1):
            t = float(k) / n
            ix, iy = self._ix(a[0] + du * t), self._iy(a[1] + dv * t)
            if 0 <= ix < self.nx and 0 <= iy < self.ny and self.occ[iy * self.nx + ix]:
                hit += 1
        return hit / float(n + 1)


# ============================================================
# 4. Отрезки / прямоугольники
# ============================================================
def _ccw(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _seg_cross(p1, p2, p3, p4):
    d1, d2 = _ccw(p3, p4, p1), _ccw(p3, p4, p2)
    d3, d4 = _ccw(p1, p2, p3), _ccw(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def _seg_box(a, b, box, shrink):
    """Пересекает ли отрезок прямоугольник (Лианг-Барски)."""
    x0, y0, x1, y1 = box[0] + shrink, box[1] + shrink, box[2] - shrink, box[3] - shrink
    if x1 <= x0 or y1 <= y0:
        return False
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                if t > t1:
                    return False
                if t > t0:
                    t0 = t
            else:
                if t < t0:
                    return False
                if t < t1:
                    t1 = t
    return t1 - t0 > 1e-9


def _box_overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h


def _nearest_on_box(p, box):
    return (min(max(p[0], box[0]), box[2]), min(max(p[1], box[1]), box[3]))


# ============================================================
# 5. Сбор участков
# ============================================================
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


def _curve_neighbors(el):
    res = []
    for c in _connectors(el):
        if not c.IsConnected:
            continue
        for r in c.AllRefs:
            o = r.Owner
            if o is None or o.Id == el.Id:
                continue
            if isinstance(o, MEPCurve):
                res.append(o.Id)
            elif isinstance(o, FamilyInstance) and _cat_int(o) in PASS_CATS:
                fcs = _connectors(o)
                if len(fcs) != 2:          # тройник/крестовина - граница участка
                    continue
                for fc in fcs:
                    if not fc.IsConnected:
                        continue
                    for r2 in fc.AllRefs:
                        o2 = r2.Owner
                        if o2 is not None and isinstance(o2, MEPCurve) and o2.Id != el.Id:
                            res.append(o2.Id)
    return res


def _section_key(el):
    ci = _cat_int(el)
    size = _param_str(el, BuiltInParameter.RBS_CALCULATED_SIZE)
    system = _param_str(el, BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
    if ci in DUCT_LIKE:
        flow = _param_str(el, BuiltInParameter.RBS_DUCT_FLOW_PARAM)
    elif ci in PIPE_LIKE:
        flow = _param_str(el, BuiltInParameter.RBS_PIPE_FLOW_PARAM)
    else:
        flow = u""
    # вертикальные и горизонтальные куски - разные участки (у них разные марки)
    c = _curve_of(el)
    vert = bool(c is not None and _is_vertical_curve(c))
    return (ci, system, size, flow, vert)


class Label(object):
    """Одна марка: новая или существующая."""
    def __init__(self):
        self.kind = 'new'          # 'new' | 'existing'
        self.elem = None           # помечаемый элемент
        self.sym_id = None
        self.tag = None            # существующая марка
        self.anchor = None         # XYZ
        self.a = None              # (u, v)
        self.ebox = None           # контур элемента на виде
        self.rel = None            # (r0, v0, r1, v1) габарит относительно головы
        self.cands = []
        self.cur = -1
        self.problem = False
        self.new_tag = None
        self.segs = None           # [(a_uv, b_uv, r, elem, p0, p1)] - участок для ортогональных выносок
        self.extra = False         # дополнительная марка (принадлежность к системе на взрыв-схеме)


def collect(doc, frame, elems, settings, existing_tags_by_elem, tagtype_by_cat):
    """
    Возвращает (labels, skipped{причина: [ids]}, to_delete[tags], stats)
    """
    min_len = settings['min_len_mm'] / FT_MM
    risers = settings['risers']
    mode = settings['existing']
    labels = []
    skipped = {}
    to_delete = []

    def skip(reason, ids):
        skipped.setdefault(reason, []).extend(ids)

    def existing_for(ids, sym_id):
        res = []
        for eid in ids:
            for t in existing_tags_by_elem.get(_iv(eid), []):
                # при переразмещении двигаем марку любого типа (тип мог быть выбран вручную)
                if (t.GetTypeId() == sym_id or mode == 'move') and t not in res:
                    res.append(t)
        return res

    def handle(ids, host, anchor, sym_id, segs=None):
        ex = existing_for(ids, sym_id)
        if ex:
            if mode == 'skip':
                skip(u"Уже промаркирован", ids)
                return
            if mode == 'move':
                for t in ex:
                    if t in claimed:
                        continue
                    claimed.add(t)
                    lb = Label()
                    lb.kind = 'existing'
                    lb.tag = t
                    lb.elem = host
                    lb.sym_id = t.GetTypeId()
                    lb.anchor = anchor
                    lb.segs = segs
                    labels.append(lb)
                return
            to_delete.extend(ex)
        lb = Label()
        lb.elem = host
        lb.sym_id = sym_id
        lb.anchor = anchor
        lb.segs = segs
        labels.append(lb)

    def handle_extra(ids, host, anchor, sym_id, segs):
        """Вторая марка на участок (другого типа). Существующая марка этого типа
        на участке - пропустить или переразместить, иначе создать новую."""
        ex = []
        for eid in ids:
            for t in existing_tags_by_elem.get(_iv(eid), []):
                if t.GetTypeId() == sym_id and t not in ex:
                    ex.append(t)
        if ex and mode == 'skip':
            return
        if ex and mode == 'move':
            for t in ex:
                if t in claimed:
                    continue
                claimed.add(t)
                lb = Label()
                lb.kind = 'existing'
                lb.tag = t
                lb.elem = host
                lb.sym_id = sym_id
                lb.anchor = anchor
                lb.segs = segs
                lb.extra = True
                labels.append(lb)
            return
        if ex and mode == 'delete':
            to_delete.extend(ex)
        lb = Label()
        lb.elem = host
        lb.sym_id = sym_id
        lb.anchor = anchor
        lb.segs = segs
        lb.extra = True
        labels.append(lb)

    claimed = set()

    curves = [e for e in elems if isinstance(e, MEPCurve) and _cat_int(e) in CURVE_CATS]
    others = [e for e in elems if not (isinstance(e, MEPCurve) and _cat_int(e) in CURVE_CATS)]

    # --- участки линейных элементов ---
    by_id = dict((_iv(e.Id), e) for e in curves)
    parent = dict((k, k) for k in by_id)

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    keys = dict((k, _section_key(e)) for k, e in by_id.items())
    for k, e in (by_id.items() if settings.get('merge', True) else []):
        for nid in _curve_neighbors(e):
            n = _iv(nid)
            if n in by_id and keys[n] == keys[k]:
                ra, rb = find(k), find(n)
                if ra != rb:
                    parent[rb] = ra
    groups = {}
    for k in by_id:
        groups.setdefault(find(k), []).append(by_id[k])

    vsym_by_cat = settings.get('_vsym', {})     # тип для вертикальных участков
    alt_by_cat = settings.get('_alt', {})       # тип для пересечений / магистралей
    picked = []                                 # (ids, best, best_pt, ci, vertical, segs, system, area)
    for members in groups.values():
        ids = [m.Id for m in members]
        ci = _cat_int(members[0])
        if tagtype_by_cat.get(ci) is None:
            continue
        total = 0.0
        best, best_len, best_pt = None, -1.0, None
        segs = []
        seg3 = []
        all_vert = True
        for m in members:
            c = _curve_of(m)
            if c is None:
                continue
            dp = _dp(m)
            total += c.Length
            p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
            vertical = _is_vertical_curve(c)
            if not vertical:
                all_vert = False
            a, b = frame.uv(p0 + dp), frame.uv(p1 + dp)
            segs.append((a, b))
            plen = math.hypot(b[0] - a[0], b[1] - a[1])
            as_point = plen < 1.0 * frame.mm
            if not as_point:
                seg3.append((a, b, _curve_half_width(m, frame), m, p0 + dp, p1 + dp))
            if (vertical or as_point) and not risers:
                continue
            score = plen if not as_point else c.Length * 1e-6
            if score > best_len:
                best, best_len, best_pt = m, score, c.Evaluate(0.5, True) + dp
        if total < min_len:
            skip(u"Короткий участок", ids)
            continue
        if best is None:
            skip(u"Вертикальный участок", ids)
            continue
        m0 = members[0]
        area = _param_d(m0, BuiltInParameter.RBS_CURVE_WIDTH_PARAM) * \
            _param_d(m0, BuiltInParameter.RBS_CURVE_HEIGHT_PARAM)
        if area <= 0:
            dd = _param_d(m0, BuiltInParameter.RBS_CURVE_DIAMETER_PARAM) or \
                _param_d(m0, BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
            area = 0.785 * dd * dd
        if (all_vert and not risers) or not seg3:
            seg3 = None
        elif not risers:
            seg3 = [sg for sg in seg3 if not _is_vertical_curve(_curve_of(sg[3]))] or None
        picked.append([ids, best, best_pt, ci, all_vert, segs,
                       _param_str(m0, BuiltInParameter.RBS_SYSTEM_NAME_PARAM), area, seg3])

    # --- второй тип (план): пересечения систем и главный участок каждой системы ---
    alt_flag = [False] * len(picked)
    extra_flag = [False] * len(picked)
    if alt_by_cat and frame.is3d:
        # взрыв-схема: на каждом фрагменте (общий вектор смещения) для каждой системы -
        # дополнительная марка принадлежности к системе на самом крупном участке
        best_by_frag = {}
        for i, p in enumerate(picked):
            if p[3] not in alt_by_cat:
                continue
            v = _dp(p[1])
            fk = (round(v.X, 2), round(v.Y, 2), round(v.Z, 2), p[6])
            score = (0 if p[4] else 1, p[7], sum(math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1])
                                                 for s in p[5]))
            k = best_by_frag.get(fk)
            if k is None or score > k[0]:
                best_by_frag[fk] = (score, i)
        for sc, i in best_by_frag.values():
            extra_flag[i] = True
    elif alt_by_cat:
        def _ext(segs):
            us = [p[0] for s in segs for p in s]
            vs = [p[1] for s in segs for p in s]
            return min(us), min(vs), max(us), max(vs)
        exts = [_ext(p[5]) for p in picked]
        for i in range(len(picked)):
            if picked[i][3] not in alt_by_cat or picked[i][4]:
                continue
            ei = exts[i]
            for j in range(len(picked)):
                if i == j or picked[j][6] == picked[i][6]:
                    continue
                ej = exts[j]
                if ej[0] > ei[2] or ej[2] < ei[0] or ej[1] > ei[3] or ej[3] < ei[1]:
                    continue
                hit = False
                for s1 in picked[i][5]:
                    for s2 in picked[j][5]:
                        if _seg_cross(s1[0], s1[1], s2[0], s2[1]):
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    alt_flag[i] = True
                    break
        best_by_sys = {}
        for i, p in enumerate(picked):
            if p[3] not in alt_by_cat or p[4] or not p[6]:
                continue
            for sysname in p[6].split(u','):
                sysname = sysname.strip()
                k = best_by_sys.get(sysname)
                if k is None or p[7] > picked[k][7]:
                    best_by_sys[sysname] = i
        for i in best_by_sys.values():
            alt_flag[i] = True

    for i, p in enumerate(picked):
        ids, best, best_pt, ci, vert = p[0], p[1], p[2], p[3], p[4]
        sym_id = tagtype_by_cat.get(ci)
        if vert and vsym_by_cat.get(ci) is not None:
            sym_id = vsym_by_cat[ci]
        elif alt_flag[i] and alt_by_cat.get(ci) is not None:
            sym_id = alt_by_cat[ci]
        handle(ids, best, best_pt, sym_id, p[8])
        if extra_flag[i] and alt_by_cat.get(ci) is not None and alt_by_cat[ci] != sym_id:
            handle_extra(ids, best, best_pt, alt_by_cat[ci], p[8])

    # --- остальные элементы: одна марка на элемент ---
    for e in others:
        ci = _cat_int(e)
        sym_id = tagtype_by_cat.get(ci)
        if sym_id is None:
            continue
        if settings.get('branches_only') and ci in FITTING_CATS and len(_connectors(e)) < 3:
            skip(u"Отвод/переход (не тройник)", [e.Id])
            continue
        if ci in INSUL_CATS:
            try:
                host = doc.GetElement(e.HostElementId)
                hc = _curve_of(host) if host is not None else None
                if hc is not None:
                    if hc.Length < min_len:
                        skip(u"Короткий участок", [e.Id])
                        continue
                    d = (hc.GetEndPoint(1) - hc.GetEndPoint(0)).Normalize()
                    if abs(d.Z) > 0.985 and not risers:
                        skip(u"Вертикальный участок", [e.Id])
                        continue
            except Exception:
                pass
        anchor = _anchor_of(e, frame)
        if anchor is None:
            skip(u"Нет геометрии", [e.Id])
            continue
        handle([e.Id], e, anchor, sym_id)

    return labels, skipped, to_delete


# ============================================================
# 6. Замер марок
# ============================================================
def _temp_section(doc, frame, labels, m=30.0):
    vft = None
    for t in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        if t.ViewFamily == ViewFamily.Section:
            vft = t
            break
    if vft is None:
        return None
    xs = [lb.anchor.X for lb in labels]
    ys = [lb.anchor.Y for lb in labels]
    zs = [lb.anchor.Z for lb in labels]
    c = XYZ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    hx = (max(xs) - min(xs)) / 2 + m
    hy = (max(ys) - min(ys)) / 2 + m
    hz = (max(zs) - min(zs)) / 2 + m
    tr = Transform.Identity
    tr.Origin = c
    tr.BasisX = XYZ.BasisX
    tr.BasisY = XYZ.BasisZ
    tr.BasisZ = XYZ.BasisX.CrossProduct(XYZ.BasisZ)
    bb = BoundingBoxXYZ()
    bb.Transform = tr
    bb.Min = XYZ(-hx, -hz, -hy)
    bb.Max = XYZ(hx, hz, hy)
    sec = ViewSection.CreateSection(doc, vft.Id, bb)
    try:
        sec.ViewTemplateId = ElementId.InvalidElementId
    except Exception:
        pass
    sec.Scale = frame.scale
    # Служебный разрез должен перерисовываться мгновенно: Revit обновляет его
    # графику после КАЖДОЙ пробной марки. Оставляем только категории помечаемых
    # элементов и марок, связи/импорт скрываем, детализация - грубая.
    keep = set()
    for lb in labels:
        try:
            keep.add(_cat_int(lb.elem))
        except Exception:
            pass
    try:
        sec.DetailLevel = ViewDetailLevel.Coarse
    except Exception:
        pass
    try:
        for c in doc.Settings.Categories:
            try:
                ci = _iv(c.Id)
                if c.CategoryType == CategoryType.Annotation:
                    continue
                if ci in keep:
                    continue
                if sec.CanCategoryBeHidden(c.Id):
                    sec.SetCategoryHidden(c.Id, True)
            except Exception:
                pass
    except Exception:
        pass
    return sec


def measure(doc, frame, labels):
    """
    Габарит головы каждой марки (без выноски) относительно TagHeadPosition,
    в мм модели по осям вида. Для 3D-видов замер идёт на временном разрезе:
    габарит аннотаций на 3D-виде Revit отдаёт неточно.
    Транзакция откатывается - модель не меняется.
    """
    default = (-8 * frame.mm, -2.5 * frame.mm, 8 * frame.mm, 2.5 * frame.mm)
    t = Transaction(doc, u"Замер марок")
    t.Start()
    try:
        # Габарит марки Revit считает вместе с точкой на помеченном элементе,
        # поэтому ставим двух "двойников" далеко по диагонали от элемента:
        # вверх-вправо (верхний правый угол головы) и вниз-влево (нижний левый).
        far = 200.0 * frame.mm
        mview = frame.view
        R, U = frame.R, frame.U
        if frame.is3d:
            sec = _temp_section(doc, frame, labels, m=far * 1.5 + 10.0)
            if sec is not None:
                doc.Regenerate()
                mview, R, U = sec, sec.RightDirection, sec.UpDirection
        diag = R + U
        # Марки с одинаковым текстом имеют одинаковый габарит - меряем по одной
        # на группу (тип марки + размер/тип элемента). Ускоряет замер в разы.
        reps = {}
        for lb in labels:
            e = lb.elem
            try:
                if isinstance(e, MEPCurve):
                    key = (_iv(lb.sym_id), _cat_int(e),
                           _param_str(e, BuiltInParameter.RBS_CALCULATED_SIZE),
                           len(_param_str(e, BuiltInParameter.RBS_SYSTEM_NAME_PARAM)))
                else:
                    key = (_iv(lb.sym_id), _iv(e.GetTypeId()),
                           _param_str(e, BuiltInParameter.RBS_CALCULATED_SIZE))
            except Exception:
                key = (_iv(lb.sym_id), _iv(e.Id))
            reps.setdefault(key, []).append(lb)
        twins = []
        for group in reps.values():
            lb = group[0]
            try:
                ta = IndependentTag.Create(doc, lb.sym_id, mview.Id, Reference(lb.elem), False,
                                           TagOrientation.Horizontal, lb.anchor + diag * far)
                tb = IndependentTag.Create(doc, lb.sym_id, mview.Id, Reference(lb.elem), False,
                                           TagOrientation.Horizontal, lb.anchor - diag * far)
                twins.append((lb, ta, tb))
            except Exception:
                lb.rel = default
        doc.Regenerate()

        def _ranges(bb):
            us, vs = [], []
            for x in (bb.Min.X, bb.Max.X):
                for y in (bb.Min.Y, bb.Max.Y):
                    for z in (bb.Min.Z, bb.Max.Z):
                        p = bb.Transform.OfPoint(XYZ(x, y, z))
                        us.append(p.DotProduct(R))
                        vs.append(p.DotProduct(U))
            return min(us), min(vs), max(us), max(vs)

        group_of = dict((id(g[0]), g) for g in reps.values())
        for lb, ta, tb in twins:
            try:
                pa, pb = ta.TagHeadPosition, tb.TagHeadPosition
                _, _, u1, v1 = _ranges(ta.get_BoundingBox(mview))
                u0, v0, _, _ = _ranges(tb.get_BoundingBox(mview))
                rel = (u0 - pb.DotProduct(R), v0 - pb.DotProduct(U),
                       u1 - pa.DotProduct(R), v1 - pa.DotProduct(U))
                if rel[2] - rel[0] < 0.5 * frame.mm or rel[3] - rel[1] < 0.5 * frame.mm:
                    rel = default
            except Exception:
                rel = default
            for other in group_of.get(id(lb), [lb]):
                other.rel = rel
        for lb in labels:
            if lb.rel is None:
                lb.rel = default
    finally:
        t.RollBack()


# ============================================================
# 7. Раскладка
# ============================================================
W_OBS = 40.0       # марка на элементах модели (доля площади)
W_OVL = 400.0      # марки перекрывают друг друга (доля площади)
W_LBOX = 60.0      # выноска проходит через чужую марку
W_LX = 25.0        # выноски пересекаются
W_DIST = 3.0       # удаление от предпочтительного расстояния
W_LOBS = 1.5       # выноска поверх элементов модели
N_KEEP = 90        # кандидатов на марку после отбора


class Cand(object):
    __slots__ = ('box', 'lead_a', 'lead_b', 'base', 'ext', 'elem', 'free', 'p3', 'meta')

    def __init__(self, box, lead_a, lead_b, base):
        self.box = box          # габарит головы марки (u0, v0, u1, v1)
        self.lead_a = lead_a    # конец выноски на элементе
        self.lead_b = lead_b    # точка выноски у марки
        self.base = base
        self.ext = None
        self.elem = None        # ортогональный режим: к какому сегменту участка марка
        self.free = False       # ортогональный режим: свободный конец выноски в точке p3
        self.p3 = None          # XYZ конца выноски
        self.meta = None        # (seg, t, dv, gap, pen) - для выравнивания


def _cand_ext(lb, c):
    a = c.lead_a
    return (min(c.box[0], a[0]), min(c.box[1], a[1]), max(c.box[2], a[0]), max(c.box[3], a[1]))


def elem_box(lb, frame):
    """Контур помечаемого элемента на виде (u0, v0, u1, v1). Для труб и
    воздуховодов - небольшой квадрат вокруг точки на оси размером с сечение."""
    au, av = lb.a
    e = lb.elem
    small = 1.0 * frame.mm
    if isinstance(e, MEPCurve):
        r = _curve_half_width(e, frame)
        return (au - r, av - r, au + r, av + r)
    try:
        bb = e.get_BoundingBox(frame.view)
        if bb is not None:
            b = frame.box_uv(bb)
            if (b[2] - b[0]) < 150 * frame.mm and (b[3] - b[1]) < 150 * frame.mm:
                return b
    except Exception:
        pass
    return (au - small, av - small, au + small, av + small)


def _clusters(labels, thr):
    """Группы близко расположенных элементов (одиночная связь по расстоянию thr).
    Возвращает для каждой марки габарит её группы (u0, v0, u1, v1)."""
    n = len(labels)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cell = {}
    for i, lb in enumerate(labels):
        cell.setdefault((int(math.floor(lb.a[0] / thr)), int(math.floor(lb.a[1] / thr))), []).append(i)
    for (cx, cy), idx in cell.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cell.get((cx + dx, cy + dy), []):
                    for i in idx:
                        if i < j:
                            a, b = labels[i].a, labels[j].a
                            if math.hypot(a[0] - b[0], a[1] - b[1]) <= thr:
                                ri, rj = find(i), find(j)
                                if ri != rj:
                                    parent[rj] = ri
    bounds = {}
    for i, lb in enumerate(labels):
        r = find(i)
        b = bounds.get(r)
        u, v = lb.a
        bounds[r] = (u, v, u, v) if b is None else (min(b[0], u), min(b[1], v),
                                                    max(b[2], u), max(b[3], v))
    return [bounds[find(i)] for i in range(n)]


def build_candidates(frame, labels, grid, crop, pref, maxd):
    """
    Кандидаты положения марки - как оформляют вручную:
    1) вплотную к контуру элемента справа/слева/сверху/снизу на
       предпочтительном расстоянии от края, выноска короткая и по
       возможности строго горизонтальная или вертикальная;
    2) колонка/ряд у края группы элементов (для плотных узлов) - дороже;
    3) веер вокруг элемента - запасной вариант, ещё дороже.
    Расстояние считается от КОНТУРА элемента, а не от его центра.
    """
    span = max(maxd, 1e-9)
    fr = (0.0, 0.15, 0.35, 0.6, 1.0)
    dists = [pref + (maxd - pref) * f for f in fr]
    thr = max(maxd * 0.5, 5 * frame.mm)
    cl = _clusters(labels, thr)

    def add(cands, lb, E, box, extra):
        if crop is not None and (box[0] < crop[0] or box[1] < crop[1]
                                 or box[2] > crop[2] or box[3] > crop[3]):
            return
        cu, cv = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        lead_a = _nearest_on_box((cu, cv), E)          # где выноска коснётся элемента
        lead_b = _nearest_on_box(lead_a, box)          # где выйдет из марки
        du, dv = abs(lead_b[0] - lead_a[0]), abs(lead_b[1] - lead_a[1])
        llen = math.hypot(du, dv)
        if llen > maxd * 1.05:
            return
        ortho = min(du, dv) / max(du, dv, 1e-9)        # 0 - строго по горизонтали/вертикали
        base = (W_OBS * grid.rect_frac(*box)
                + W_DIST * max(0.0, llen - pref) / span
                + W_LOBS * grid.seg_frac(lead_a, lead_b) * 0.3
                + 1.2 * ortho
                + extra)
        cands.append(Cand(box, lead_a, lead_b, base))

    for i, lb in enumerate(labels):
        r0, v0, r1, v1 = lb.rel
        w, h = r1 - r0, v1 - v0
        au, av = lb.a
        E = lb.ebox
        eu, ev = (E[0] + E[2]) / 2.0, (E[1] + E[3]) / 2.0
        cands = []
        # 1) у контура элемента
        for d in dists:
            for j in range(-4, 5):
                cv = ev + j * h * 0.5
                pen = 0.15 * abs(j)
                add(cands, lb, E, (E[2] + d, cv - h / 2.0, E[2] + d + w, cv + h / 2.0), pen)
                add(cands, lb, E, (E[0] - d - w, cv - h / 2.0, E[0] - d, cv + h / 2.0), pen)
                cu = eu + j * w * 0.5
                add(cands, lb, E, (cu - w / 2.0, E[3] + d, cu + w / 2.0, E[3] + d + h), pen)
                add(cands, lb, E, (cu - w / 2.0, E[1] - d - h, cu + w / 2.0, E[1] - d), pen + 0.05)
        # 2) колонка/ряд у края группы
        gu0, gv0, gu1, gv1 = cl[i]
        for j in range(-10, 11, 2):
            cv = ev + j * h * 0.5
            add(cands, lb, E, (gu0 - pref - w, cv - h / 2.0, gu0 - pref, cv + h / 2.0), 0.6)
            add(cands, lb, E, (gu1 + pref, cv - h / 2.0, gu1 + pref + w, cv + h / 2.0), 0.6)
            cu = eu + j * w * 0.5
            add(cands, lb, E, (cu - w / 2.0, gv1 + pref, cu + w / 2.0, gv1 + pref + h), 0.6)
            add(cands, lb, E, (cu - w / 2.0, gv0 - pref - h, cu + w / 2.0, gv0 - pref), 0.65)
        # 3) веер
        for a_deg in (45, 135, 225, 315, 22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5):
            ang = math.radians(a_deg)
            ca, sa = math.cos(ang), math.sin(ang)
            for d in dists:
                cu = eu + ca * ((E[2] - E[0]) / 2.0 + d + w / 2.0)
                cv = ev + sa * ((E[3] - E[1]) / 2.0 + d + h / 2.0)
                add(cands, lb, E, (cu - w / 2.0, cv - h / 2.0, cu + w / 2.0, cv + h / 2.0), 1.0)
        if not cands:
            box = (E[2] + pref, ev - h / 2.0, E[2] + pref + w, ev + h / 2.0)
            la = _nearest_on_box(((box[0] + box[2]) / 2, ev), E)
            cands.append(Cand(box, la, _nearest_on_box(la, box), 1000.0))
        seen = set()
        uniq = []
        for c in sorted(cands, key=lambda c: c.base):
            k = (round(c.box[0] / (0.2 * frame.mm)), round(c.box[1] / (0.2 * frame.mm)))
            if k in seen:
                continue
            seen.add(k)
            uniq.append(c)
        lb.cands = uniq[:N_KEEP]
        for c in lb.cands:
            c.ext = _cand_ext(lb, c)


# ------------------------------------------------------------
# 7б. Ортогональные выноски (v3)
# Выноска строго перпендикулярна коммуникации (на плане - горизонтальна
# или вертикальна), марка может стоять в любом месте участка.
# На 3D-видах дополнительно - строго горизонтальная/вертикальная выноска
# на листе со свободным концом на оси. Пересечения выносок между собой
# штрафуются сильнее, чем в свободном режиме.
# ------------------------------------------------------------
W_LOBS_O = 2.5      # выноска поверх чужих элементов
W_OBS_O = 90.0      # марка поверх элементов модели
W_LX_O = 140.0      # выноски пересекаются
W_ALIGN = 0.35      # допустимое ухудшение ради выравнивания в ряд/колонку
N_KEEP_O = 140


def _ray_exit(dv, w, h):
    """Расстояние от центра прямоугольника w x h до его края по направлению dv."""
    ax, ay = abs(dv[0]), abs(dv[1])
    sx = (w / 2.0) / ax if ax > 1e-9 else 1e18
    sy = (h / 2.0) / ay if ay > 1e-9 else 1e18
    return min(sx, sy)


class _OSeg(object):
    __slots__ = ('a', 'd', 'n', 'L', 'r', 'elem', 'q0', 'q1', 'margin')


def _ortho_make(lb, ctx, sg, t, p, p3, dv, start, gap, free, pen):
    grid, crop, frame, pref, maxd = ctx
    r0, v0, r1, v1 = lb.rel
    w, h = r1 - r0, v1 - v0
    ext = _ray_exit(dv, w, h)
    dist = start + gap + ext
    cu, cv = p[0] + dv[0] * dist, p[1] + dv[1] * dist
    box = (cu - w / 2.0, cv - h / 2.0, cu + w / 2.0, cv + h / 2.0)
    if crop is not None and (box[0] < crop[0] or box[1] < crop[1]
                             or box[2] > crop[2] or box[3] > crop[3]):
        return None
    lead_b = (cu - dv[0] * ext, cv - dv[1] * ext)
    k = start + 0.3 * frame.mm
    out = (p[0] + dv[0] * k, p[1] + dv[1] * k)
    span = max(maxd - pref, frame.mm)
    rf = W_OBS_O * grid.rect_frac(*box)
    base = rf + W_DIST * max(0.0, gap - pref) / span + pen
    if rf < 25.0:                   # заведомо плохие - без дорогой проверки выноски
        base += W_LOBS_O * grid.seg_frac(out, lead_b)
    else:
        base += W_LOBS_O
    c = Cand(box, p, lead_b, base)
    c.elem = sg.elem if sg is not None else lb.elem
    c.free = free
    c.p3 = p3
    c.meta = (sg, t, dv, start, gap, pen)
    c.ext = _cand_ext(lb, c)
    return c


def _seg_point(sg, t):
    p = (sg.a[0] + sg.d[0] * t, sg.a[1] + sg.d[1] * t)
    f = t / sg.L if sg.L > 1e-9 else 0.5
    p3 = sg.q0 + (sg.q1 - sg.q0) * f
    return p, p3


def build_candidates_ortho(frame, labels, grid, crop, pref, maxd):
    ctx = (grid, crop, frame, pref, maxd)
    gaps = [pref + (maxd - pref) * f for f in (0.0, 0.15, 0.35, 0.6, 1.0)]
    axes = ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))
    for lb in labels:
        r0, v0, r1, v1 = lb.rel
        w, h = r1 - r0, v1 - v0
        cands = []
        osegs = []
        for raw in (lb.segs or []):
            a, b, r, el, q0, q1 = raw
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L < 1e-9:
                continue
            sg = _OSeg()
            sg.a, sg.L, sg.r, sg.elem, sg.q0, sg.q1 = a, L, r, el, q0, q1
            sg.d = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
            sg.n = (-sg.d[1], sg.d[0])
            sg.margin = min(L / 2.0, r + 1.0 * frame.mm)
            osegs.append(sg)
        lb.osegs = osegs
        # не больше ~24 точек на участок: распределяем по длине сегментов
        tot_len = sum(max(sg.L - 2 * sg.margin, 0.0) for sg in osegs) or 1.0
        for sg in osegs:
            usable = sg.L - 2 * sg.margin
            step = max(min(w, h) * 1.2, 2.0 * frame.mm)
            quota = max(1, int(round(24.0 * max(usable, 0.0) / tot_len)))
            k = max(1, min(16, quota, int(usable / step) + 1))
            ts = [sg.L / 2.0] if usable <= 0 else [sg.margin + usable * (i + 0.5) / k for i in range(k)]
            aligned = abs(sg.n[0]) > 0.97 or abs(sg.n[1]) > 0.97
            dirs = []
            pp = 0.0 if aligned else (0.9 if frame.is3d else 0.3)
            dirs.append((sg.n, False, pp))
            dirs.append(((-sg.n[0], -sg.n[1]), False, pp))
            if not aligned:
                for ax in axes:
                    if abs(ax[0] * sg.d[0] + ax[1] * sg.d[1]) > 0.8:
                        continue
                    dirs.append((ax, True, 0.05 if ax[1] != 0 else 0.1))
            for t in ts:
                p, p3 = _seg_point(sg, t)
                mid = 0.15 * abs(t - sg.L / 2.0) / sg.L
                for dv, free, pen in dirs:
                    cosn = abs(dv[0] * sg.n[0] + dv[1] * sg.n[1])
                    start = sg.r / max(cosn, 0.35)
                    for gi, g in enumerate(gaps):
                        c = _ortho_make(lb, ctx, sg, t, p, p3, dv, start, g, free,
                                        pen + mid + 0.02 * gi)
                        if c is not None:
                            cands.append(c)
        if not osegs:
            # точечный элемент (оборудование, тройник, стояк на плане)
            E = lb.ebox
            au, av = lb.a
            is_curve = isinstance(lb.elem, MEPCurve)
            for dv in axes:
                if dv[0] != 0:
                    span_p = (E[3] - E[1]) / 2.0
                    offs = [0.0] if is_curve else [0.0, 0.3 * span_p, -0.3 * span_p]
                else:
                    span_p = (E[2] - E[0]) / 2.0
                    offs = [0.0] if is_curve else [0.0, 0.3 * span_p, -0.3 * span_p]
                for o in offs:
                    if dv[0] != 0:
                        p = (au, av + o)
                        start = (E[2] - au) if dv[0] > 0 else (au - E[0])
                    else:
                        p = (au + o, av)
                        start = (E[3] - av) if dv[1] > 0 else (av - E[1])
                    start = max(start, 0.0)
                    if is_curve:
                        pe, p3, free = p, lb.anchor, False
                    else:
                        inset = max(start - 1.0 * frame.mm, 0.0)
                        pe = (p[0] + dv[0] * inset, p[1] + dv[1] * inset)
                        p3 = lb.anchor + frame.R * (pe[0] - au) + frame.U * (pe[1] - av)
                        start = start - inset
                        free = True
                    pen = (0.08 if dv[1] == 0 else 0.0) + (0.1 if o else 0.0)
                    for gi, g in enumerate(gaps):
                        c = _ortho_make(lb, ctx, None, None, pe, p3, dv, start, g, free,
                                        pen + 0.02 * gi)
                        if c is not None:
                            cands.append(c)
        if not cands:
            continue
        cands.sort(key=lambda c: c.base)
        seen = set()
        uniq = []
        q = 0.2 * frame.mm
        for c in cands:
            kk = (round(c.box[0] / q), round(c.box[1] / q))
            if kk in seen:
                continue
            seen.add(kk)
            uniq.append(c)
        lb.cands = uniq[:N_KEEP_O]


def align_pass(labels, frame, ctx, bucket, passes=2):
    """Выравнивание марок в ряды и колонки: марка сдвигается вдоль своего
    участка или меняет отступ, чтобы встать в линию с соседней маркой,
    если это не создаёт наложений и пересечений."""
    n = len(labels)
    sh = SpatialHash(bucket)
    for i, lb in enumerate(labels):
        sh.add(i, lb.cands[lb.cur].ext)
    tol = 0.05 * frame.mm
    near = 45.0 * frame.mm
    moved = 0

    def total(i, c):
        v = c.base
        for j in sh.query(c.ext):
            if j == i:
                continue
            lj = labels[j]
            v += _pair_cost(labels[i], c, lj, lj.cands[lj.cur])
        return v

    def aligned_count(i, c):
        cu, cv = (c.box[0] + c.box[2]) / 2.0, (c.box[1] + c.box[3]) / 2.0
        k = 0
        q = (c.box[0] - near, c.box[1] - near, c.box[2] + near, c.box[3] + near)
        for j in sh.query(q):
            if j == i:
                continue
            o = labels[j].cands[labels[j].cur].box
            ou, ov = (o[0] + o[2]) / 2.0, (o[1] + o[3]) / 2.0
            if abs(ou - cu) < tol or abs(o[0] - c.box[0]) < tol or abs(o[2] - c.box[2]) < tol:
                k += 1
            if abs(ov - cv) < tol:
                k += 1
        return k

    for _ in range(passes):
        for i, lb in enumerate(labels):
            c = lb.cands[lb.cur]
            if c.meta is None:
                continue
            sg, t, dv, start, gap, pen = c.meta
            cur_cost = total(i, c)
            cur_al = aligned_count(i, c)
            q = (c.box[0] - near, c.box[1] - near, c.box[2] + near, c.box[3] + near)
            tries = []
            for j in sh.query(q):
                if j == i:
                    continue
                o = labels[j].cands[labels[j].cur].box
                ou, ov = (o[0] + o[2]) / 2.0, (o[1] + o[3]) / 2.0
                cu, cv = (c.box[0] + c.box[2]) / 2.0, (c.box[1] + c.box[3]) / 2.0
                # сдвиг поперёк выноски: вдоль участка (t) или для точечных - нет
                if sg is not None:
                    along = (sg.d[0], sg.d[1])
                    for target, cur, axis_u in ((ou, cu, True), (o[0], c.box[0], True),
                                                (o[2], c.box[2], True), (ov, cv, False)):
                        delta = target - cur
                        comp = along[0] if axis_u else along[1]
                        if abs(comp) < 0.9 or abs(delta) < tol:
                            continue
                        nt = t + delta / comp
                        if sg.margin <= nt <= sg.L - sg.margin:
                            tries.append(('t', nt))
                # изменение отступа (вдоль выноски)
                for target, cur, axis_u in ((ou, cu, True), (ov, cv, False),
                                            (o[0], c.box[0], True), (o[2], c.box[2], True)):
                    comp = dv[0] if axis_u else dv[1]
                    if abs(comp) < 0.9:
                        continue
                    delta = (target - cur) / comp
                    if abs(delta) < tol:
                        continue
                    ng = gap + delta
                    if 0.5 * ctx[3] <= ng <= ctx[4] * 1.5:
                        tries.append(('g', ng))
            best, best_key = None, (cur_al, -cur_cost)
            for kind, val in tries:
                if kind == 't':
                    p, p3 = _seg_point(sg, val)
                    nc = _ortho_make(lb, ctx, sg, val, p, p3, dv, start, gap, c.free, pen)
                else:
                    nc = _ortho_make(lb, ctx, sg, t, c.lead_a, c.p3, dv, start, val, c.free, pen)
                if nc is None:
                    continue
                cost = total(i, nc)
                if cost > cur_cost + W_ALIGN:
                    continue
                al = aligned_count(i, nc)
                key = (al, -cost)
                if al > cur_al and (best is None or key > best_key):
                    best, best_key = nc, key
            if best is not None:
                sh.remove(i, c.ext)
                lb.cands.append(best)
                lb.cur = len(lb.cands) - 1
                sh.add(i, best.ext)
                moved += 1
    return moved


class SpatialHash(object):
    def __init__(self, size):
        self.size = size
        self.cells = {}

    def _keys(self, ext):
        s = self.size
        for ix in range(int(math.floor(ext[0] / s)), int(math.floor(ext[2] / s)) + 1):
            for iy in range(int(math.floor(ext[1] / s)), int(math.floor(ext[3] / s)) + 1):
                yield (ix, iy)

    def add(self, i, ext):
        for k in self._keys(ext):
            self.cells.setdefault(k, set()).add(i)

    def remove(self, i, ext):
        for k in self._keys(ext):
            s = self.cells.get(k)
            if s is not None:
                s.discard(i)

    def query(self, ext):
        res = set()
        for k in self._keys(ext):
            s = self.cells.get(k)
            if s:
                res |= s
        return res


GAP_BOX = [0.0]     # минимальный зазор между рамками марок (ft), задаётся в run()


def _pair_cost(la, ca, lb, cb):
    cost = 0.0
    g = GAP_BOX[0]
    if g > 0:
        ba = (ca.box[0] - g, ca.box[1] - g, ca.box[2] + g, ca.box[3] + g)
        ov = _box_overlap(ba, cb.box)
        if ov > 0:
            area = min((ca.box[2] - ca.box[0]) * (ca.box[3] - ca.box[1]),
                       (cb.box[2] - cb.box[0]) * (cb.box[3] - cb.box[1]))
            return W_OVL * (0.05 + ov / max(area, 1e-12)) + _lead_cost(ca, cb)
        return _lead_cost(ca, cb)
    ov = _box_overlap(ca.box, cb.box)
    if ov > 0:
        # касание рамками (как в образце) не считаем наложением
        wv = min(ca.box[2], cb.box[2]) - max(ca.box[0], cb.box[0])
        hv = min(ca.box[3], cb.box[3]) - max(ca.box[1], cb.box[1])
        tol = 0.02 * min(ca.box[3] - ca.box[1], cb.box[3] - cb.box[1])
        if wv <= tol or hv <= tol:
            ov = 0.0
    if ov > 0:
        area = min((ca.box[2] - ca.box[0]) * (ca.box[3] - ca.box[1]),
                   (cb.box[2] - cb.box[0]) * (cb.box[3] - cb.box[1]))
        cost += W_OVL * (0.05 + ov / max(area, 1e-12))
    shrink = 1e-6
    if _seg_box(ca.lead_a, ca.lead_b, cb.box, shrink):
        cost += W_LBOX
    if _seg_box(cb.lead_a, cb.lead_b, ca.box, shrink):
        cost += W_LBOX
    if _seg_cross(ca.lead_a, ca.lead_b, cb.lead_a, cb.lead_b):
        cost += W_LX
    return cost


def _count_conflicts(labels, bucket):
    sh = SpatialHash(bucket)
    for i, lb in enumerate(labels):
        sh.add(i, lb.cands[lb.cur].ext)
    hard = 0
    for i, li in enumerate(labels):
        ci = li.cands[li.cur]
        for j in sh.query(ci.ext):
            if j <= i:
                continue
            lj = labels[j]
            if _pair_cost(li, ci, lj, lj.cands[lj.cur]) > 0:
                li.problem = True
                lj.problem = True
                hard += 1
    return hard


def _lead_cost(ca, cb):
    cost = 0.0
    if _seg_box(ca.lead_a, ca.lead_b, cb.box, 1e-6):
        cost += W_LBOX
    if _seg_box(cb.lead_a, cb.lead_b, ca.box, 1e-6):
        cost += W_LBOX
    if _seg_cross(ca.lead_a, ca.lead_b, cb.lead_a, cb.lead_b):
        cost += W_LX
    return cost


def optimize(labels, passes, bucket, progress=None):
    n = len(labels)
    sh = SpatialHash(bucket)
    placed = [False] * n
    evals = [0]

    def cost_of(i, c):
        evals[0] += 1
        total = c.base
        lb = labels[i]
        for j in sh.query(c.ext):
            if j == i or not placed[j]:
                continue
            lj = labels[j]
            total += _pair_cost(lb, c, lj, lj.cands[lj.cur])
        return total

    def best_for(i):
        best_k, best_c = 0, None
        for k, c in enumerate(labels[i].cands):
            if best_c is not None and c.base >= best_c:
                break                       # кандидаты отсортированы по base
            v = cost_of(i, c)
            if best_c is None or v < best_c:
                best_k, best_c = k, v
        return best_k

    # жадно: сначала самые "стеснённые"
    order = sorted(range(n), key=lambda i: sum(1 for c in labels[i].cands if c.base < 1.0))
    for step, i in enumerate(order):
        k = best_for(i)
        labels[i].cur = k
        placed[i] = True
        sh.add(i, labels[i].cands[k].ext)
        if progress and step % 20 == 0:
            progress(step, n)

    rnd = random.Random(7)
    for p in range(passes):
        idx = list(range(n))
        rnd.shuffle(idx)
        changed = 0
        for i in idx:
            lb = labels[i]
            sh.remove(i, lb.cands[lb.cur].ext)
            placed[i] = False
            k = best_for(i)
            if k != lb.cur:
                changed += 1
                lb.cur = k
            placed[i] = True
            sh.add(i, lb.cands[k].ext)
        if changed == 0:
            break

    # итоговые конфликты
    hard = 0
    for i in range(n):
        li = labels[i]
        ci = li.cands[li.cur]
        for j in sh.query(ci.ext):
            if j <= i:
                continue
            lj = labels[j]
            if _pair_cost(li, ci, lj, lj.cands[lj.cur]) > 0:
                li.problem = True
                lj.problem = True
                hard += 1
    return hard, evals[0]


# ============================================================
# 8. Окно настроек
# ============================================================
XAML = u'''
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Умные выноски" Width="560" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" ShowInTaskbar="False">
  <StackPanel Margin="12">
    <TextBlock x:Name="tb_view" Margin="0,0,0,8" Foreground="#555555"/>
    <GroupBox Header="Что подписывать" Padding="8">
      <StackPanel>
        <StackPanel Orientation="Horizontal" Margin="0,0,0,8">
          <RadioButton x:Name="rb_scope_view" Content="Все элементы на виде" IsChecked="True" Margin="0,0,16,0"/>
          <RadioButton x:Name="rb_scope_sel" Content="Только выделенные"/>
        </StackPanel>
        <Grid Margin="0,0,0,8">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="*"/>
          </Grid.ColumnDefinitions>
          <TextBlock Text="Только фильтр вида:" VerticalAlignment="Center" Margin="0,0,6,0"/>
          <ComboBox x:Name="cb_finc" Grid.Column="1"/>
          <TextBlock Grid.Column="2" Text="кроме:" VerticalAlignment="Center" Margin="10,0,6,0"/>
          <ComboBox x:Name="cb_fexc" Grid.Column="3"/>
        </Grid>
        <Grid Margin="0,0,0,4">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="260"/>
          </Grid.ColumnDefinitions>
          <TextBlock Text="Категория (элементов на виде)" Foreground="#7A847C"/>
          <TextBlock Grid.Column="1" Text="Чем подписывать" Foreground="#7A847C"/>
        </Grid>
        <ScrollViewer MaxHeight="240" VerticalScrollBarVisibility="Auto">
          <StackPanel x:Name="sp_cats"/>
        </ScrollViewer>
        <DockPanel Margin="0,10,0,0">
          <TextBox x:Name="tx_minlen" Width="70" DockPanel.Dock="Right"/>
          <TextBlock Text="Мин. длина участка, мм (короче — пропускаются)" VerticalAlignment="Center"/>
        </DockPanel>
        <CheckBox x:Name="cb_risers" Content="Маркировать вертикальные участки (стояки)" Margin="0,6,0,0"/>
        <CheckBox x:Name="cb_branches" Content="Соединительные детали: только тройники и крестовины" Margin="0,6,0,0"/>
        <StackPanel Orientation="Horizontal" Margin="0,6,0,0">
          <RadioButton x:Name="rb_merge" Content="Одна марка на участок" Margin="0,0,16,0"/>
          <RadioButton x:Name="rb_each" Content="Марка на каждый сегмент"/>
        </StackPanel>
        <TextBlock TextWrapping="Wrap" Foreground="#7A847C" FontSize="11" Margin="0,4,0,0"
                   Text="Участок = соседние сегменты одной системы с одинаковыми размером и расходом (до тройника)."/>
      </StackPanel>
    </GroupBox>
    <GroupBox Header="Как размещать" Padding="8" Margin="0,8,0,0">
      <StackPanel>
        <TextBlock Text="Существующие марки выбранного типа:"/>
        <RadioButton x:Name="rb_ex_skip" Content="Пропустить уже промаркированные элементы" Margin="0,4,0,0"/>
        <RadioButton x:Name="rb_ex_move" Content="Переразместить существующие марки вместе с новыми"/>
        <RadioButton x:Name="rb_ex_del" Content="Удалить существующие марки и создать заново"/>
        <StackPanel Orientation="Horizontal" Margin="0,10,0,0">
          <TextBlock Text="Тип выноски:" Margin="0,0,12,0"/>
          <RadioButton x:Name="rb_attached" Content="С присоединённым концом" Margin="0,0,16,0"/>
          <RadioButton x:Name="rb_free" Content="Со свободным концом"/>
        </StackPanel>
        <StackPanel Orientation="Horizontal" Margin="0,10,0,0">
          <TextBlock Text="Форма выносок:" Margin="0,0,12,0"/>
          <RadioButton x:Name="rb_ortho" Content="Под прямым углом к коммуникации" Margin="0,0,16,0"/>
          <RadioButton x:Name="rb_diag" Content="Свободная (с наклоном)"/>
        </StackPanel>
        <CheckBox x:Name="cb_align" Content="Выравнивать марки в ряды и колонки" Margin="0,6,0,0"/>
        <DockPanel Margin="0,10,0,0">
          <TextBox x:Name="tx_pref" Width="70" DockPanel.Dock="Right"/>
          <TextBlock Text="Отступ марки от края элемента, мм листа" VerticalAlignment="Center"/>
        </DockPanel>
        <DockPanel Margin="0,4,0,0">
          <TextBox x:Name="tx_max" Width="70" DockPanel.Dock="Right"/>
          <TextBlock Text="Максимальная длина выноски, мм листа" VerticalAlignment="Center"/>
        </DockPanel>
        <DockPanel Margin="0,10,0,0">
          <TextBlock Text="Качество раскладки:" Width="150" VerticalAlignment="Top"/>
          <StackPanel>
            <Slider x:Name="sl_quality" Minimum="0" Maximum="2" TickFrequency="1"
                    IsSnapToTickEnabled="True" TickPlacement="BottomRight"/>
            <Grid>
              <TextBlock Text="Быстро" HorizontalAlignment="Left" Foreground="#777777"/>
              <TextBlock Text="Стандарт" HorizontalAlignment="Center" Foreground="#777777"/>
              <TextBlock Text="Максимум" HorizontalAlignment="Right" Foreground="#777777"/>
            </Grid>
          </StackPanel>
        </DockPanel>
      </StackPanel>
    </GroupBox>
    <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,12,0,0">
      <Button x:Name="bt_cancel" Content="Отмена" Width="110" IsCancel="True" Margin="0,0,8,0"/>
      <Button x:Name="bt_run" Content="Запустить" Width="130" IsDefault="True"/>
    </StackPanel>
  </StackPanel>
</Window>
'''


class SettingsWindow(forms.WPFWindow):
    def __init__(self, view_text, cat_rows, has_selection, saved, filter_names=None):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.filter_names = filter_names or []
        for cb_ in (self.cb_finc, self.cb_fexc):
            cb_.Items.Add(u"(не учитывать)")
            for fn in self.filter_names:
                cb_.Items.Add(fn)
        fi, fe = saved.get('f_inc', u""), saved.get('f_exc', u"")
        self.cb_finc.SelectedIndex = (self.filter_names.index(fi) + 1) if fi in self.filter_names else 0
        self.cb_fexc.SelectedIndex = (self.filter_names.index(fe) + 1) if fe in self.filter_names else 0
        self.cb_branches.IsChecked = bool(saved.get('branches_only', True))
        forms.theme(self, u"Умные выноски", view_text)
        self.result = None
        self.rows = []
        self.tb_view.Text = view_text
        self.tb_view.Visibility = SW.Visibility.Collapsed   # вид уже в шапке
        self.rb_scope_sel.IsEnabled = has_selection
        if has_selection:
            self.rb_scope_sel.IsChecked = True
            if not saved.get('scope_sel', True):
                self.rb_scope_view.IsChecked = True

        for row in cat_rows:
            g = WC.Grid()
            g.Margin = SW.Thickness(0, 2, 0, 2)
            c0 = WC.ColumnDefinition()
            c0.Width = SW.GridLength(1, SW.GridUnitType.Star)
            c1 = WC.ColumnDefinition()
            c1.Width = SW.GridLength(260)
            g.ColumnDefinitions.Add(c0)
            g.ColumnDefinitions.Add(c1)
            cb = WC.CheckBox()
            cb.Content = u"{} ({})".format(row['name'], row['count'])
            cb.VerticalAlignment = SW.VerticalAlignment.Center
            cb.IsChecked = row['checked']
            WC.Grid.SetColumn(cb, 0)
            combo = WC.ComboBox()
            for lbl in row['type_labels']:
                combo.Items.Add(lbl)
            combo.SelectedIndex = row['type_index']
            WC.Grid.SetColumn(combo, 1)
            g.Children.Add(cb)
            g.Children.Add(combo)
            self.sp_cats.Children.Add(g)
            extra = {}
            # для труб/воздуховодов - отдельные марки для стояков и для пересечений/магистралей
            for key, caption in row.get('extras', []):
                g2 = WC.Grid()
                g2.Margin = SW.Thickness(22, 0, 0, 2)
                d0 = WC.ColumnDefinition()
                d0.Width = SW.GridLength(1, SW.GridUnitType.Star)
                d1 = WC.ColumnDefinition()
                d1.Width = SW.GridLength(260)
                g2.ColumnDefinitions.Add(d0)
                g2.ColumnDefinitions.Add(d1)
                tb = WC.TextBlock()
                tb.Text = caption
                tb.FontSize = 11
                tb.Foreground = SWM.SolidColorBrush(SWM.Color.FromRgb(0x7A, 0x84, 0x7C))
                tb.VerticalAlignment = SW.VerticalAlignment.Center
                WC.Grid.SetColumn(tb, 0)
                cx = WC.ComboBox()
                cx.Items.Add(u"(как основная)")
                for lbl in row['type_labels']:
                    cx.Items.Add(lbl)
                cx.SelectedIndex = row.get(key + '_index', 0)
                WC.Grid.SetColumn(cx, 1)
                g2.Children.Add(tb)
                g2.Children.Add(cx)
                self.sp_cats.Children.Add(g2)
                extra[key] = cx
            self.rows.append((row, cb, combo, extra))

        self.tx_minlen.Text = str(saved.get('min_len_mm', 200))
        self.cb_risers.IsChecked = bool(saved.get('risers', False))
        self.rb_merge.IsChecked = bool(saved.get('merge', True))
        self.rb_each.IsChecked = not bool(saved.get('merge', True))
        ex = saved.get('existing', 'skip')
        self.rb_ex_skip.IsChecked = ex == 'skip'
        self.rb_ex_move.IsChecked = ex == 'move'
        self.rb_ex_del.IsChecked = ex == 'delete'
        self.rb_ortho.IsChecked = bool(saved.get('ortho', True))
        self.rb_diag.IsChecked = not bool(saved.get('ortho', True))
        self.cb_align.IsChecked = bool(saved.get('align', True))
        self.rb_attached.IsChecked = bool(saved.get('attached', True))
        self.rb_free.IsChecked = not bool(saved.get('attached', True))
        self.tx_pref.Text = str(saved.get('pref_mm', 4))
        self.tx_max.Text = str(saved.get('max_mm', 30))
        self.sl_quality.Value = saved.get('quality', 1)

        self.bt_run.Click += self._on_run
        self.bt_cancel.Click += self._on_cancel

    def _num(self, tb, name, minimum):
        try:
            v = float(tb.Text.replace(u",", u"."))
            if v < minimum:
                raise ValueError
            return v
        except Exception:
            forms.alert(u"Некорректное значение: {}".format(name), title=u"Умные выноски")
            return None

    def _on_run(self, sender, args):
        min_len = self._num(self.tx_minlen, u"мин. длина участка", 0)
        pref = self._num(self.tx_pref, u"предпочтительное расстояние", 0)
        maxd = self._num(self.tx_max, u"максимальное расстояние", 0.1)
        if None in (min_len, pref, maxd):
            return
        if maxd < pref:
            maxd = pref
        cats = {}
        vcats, acats = {}, {}
        for row, cb, combo, extra in self.rows:
            if cb.IsChecked and combo.SelectedIndex >= 0:
                cats[row['cat_id']] = combo.SelectedIndex
                if 'vert' in extra and extra['vert'].SelectedIndex > 0:
                    vcats[row['cat_id']] = extra['vert'].SelectedIndex - 1
                if 'alt' in extra and extra['alt'].SelectedIndex > 0:
                    acats[row['cat_id']] = extra['alt'].SelectedIndex - 1
        if not cats:
            forms.alert(u"Не выбрано ни одной категории.", title=u"Умные выноски")
            return
        if self.rb_ex_move.IsChecked:
            ex = 'move'
        elif self.rb_ex_del.IsChecked:
            ex = 'delete'
        else:
            ex = 'skip'
        fi = self.cb_finc.SelectedIndex
        fe = self.cb_fexc.SelectedIndex
        self.result = {
            'cats': cats,
            'vcats': vcats,
            'acats': acats,
            'f_inc': self.filter_names[fi - 1] if fi > 0 else u"",
            'f_exc': self.filter_names[fe - 1] if fe > 0 else u"",
            'branches_only': bool(self.cb_branches.IsChecked),
            'scope_sel': bool(self.rb_scope_sel.IsChecked),
            'min_len_mm': min_len,
            'risers': bool(self.cb_risers.IsChecked),
            'merge': bool(self.rb_merge.IsChecked),
            'attached': bool(self.rb_attached.IsChecked),
            'ortho': bool(self.rb_ortho.IsChecked),
            'align': bool(self.cb_align.IsChecked),
            'existing': ex,
            'pref_mm': pref,
            'max_mm': maxd,
            'quality': int(round(self.sl_quality.Value)),
        }
        self.Close()

    def _on_cancel(self, sender, args):
        self.result = None
        self.Close()


# ============================================================
# 9. Запуск
# ============================================================
PRESET_FILE = u"smart_tags_preset.json"


def _preset_path():
    import os
    return os.path.join(os.environ.get('TEMP', u"."), PRESET_FILE)


def _load_preset():
    """Разовый пресет из %TEMP%\\smart_tags_preset.json (пишет Claude или другой скрипт).
    Файл читается и удаляется. "autorun": true - запуск без окна настроек."""
    import os
    import io
    p = _preset_path()
    if not os.path.exists(p):
        return None
    try:
        with io.open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = None
    try:
        os.remove(p)
    except Exception:
        pass
    return data


def _load_saved():
    try:
        cfg = script.get_config()
        raw = cfg.get_option('smart_tags_settings', u"")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _save(settings, type_names):
    try:
        cfg = script.get_config()
        data = dict(settings)
        for k in ('cats', 'vcats', 'acats', '_vsym', '_alt'):
            data.pop(k, None)
        data['types'] = type_names
        data['checked_cats'] = [str(k) for k in settings['cats'].keys()]
        cfg.smart_tags_settings = json.dumps(data)
        script.save_config()
    except Exception:
        pass


def _ensure_view(doc, view):
    if isinstance(view, View3D):
        if view.IsPerspective:
            forms.alert(u"Перспективные 3D-виды не поддерживаются.", title=u"Умные выноски")
            return False
        if not view.IsLocked:
            if not forms.alert(u"Для марок на 3D-виде вид должен быть заблокирован.\n\n"
                               u"Заблокировать вид «{}»?".format(view.Name),
                               title=u"Умные выноски", yes=True, no=True):
                return False
            t = Transaction(doc, u"Блокировка 3D-вида")
            t.Start()
            try:
                view.SaveOrientationAndLock()
                t.Commit()
            except Exception as ex:
                t.RollBack()
                forms.alert(u"Не удалось заблокировать вид:\n{}\n\n"
                            u"Вид по умолчанию {{3D}} заблокировать нельзя - "
                            u"сохраните его копию под своим именем.".format(ex),
                            title=u"Умные выноски")
                return False
        return True
    if isinstance(view, (ViewPlan, ViewSection)) or view.ViewType in (
            ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan,
            ViewType.Section, ViewType.Elevation, ViewType.Detail):
        return True
    forms.alert(u"Этот тип вида не поддерживается: {}".format(view.ViewType),
                title=u"Умные выноски")
    return False


def _place_ortho(doc, view, frame, lb, c, hu, hv):
    """Размещение марки ортогонального режима. Выноска прямая: без излома,
    конец на оси (присоединённый - Revit сам опускает перпендикуляр на сегмент,
    свободный - в точке c.p3)."""
    pu, pv = c.lead_a
    head = c.p3 + frame.R * (hu - pu) + frame.U * (hv - pv)
    target = c.elem if c.elem is not None else lb.elem
    tag = lb.tag
    if lb.kind == 'new':
        tag = IndependentTag.Create(doc, lb.sym_id, view.Id, Reference(target), True,
                                    TagOrientation.Horizontal, head)
    else:
        ids = [_iv(x) for x in tag.GetTaggedLocalElementIds()]
        if _iv(target.Id) not in ids:
            old = tag
            tag = IndependentTag.Create(doc, old.GetTypeId(), view.Id, Reference(target), True,
                                        old.TagOrientation, head)
            doc.Delete(old.Id)
    tag.HasLeader = True
    ref = None
    for r in tag.GetTaggedReferences():
        ref = r
        break
    if c.free:
        tag.LeaderEndCondition = LeaderEndCondition.Free
        tag.TagHeadPosition = head
        tag.SetLeaderEnd(ref, c.p3)
    else:
        tag.LeaderEndCondition = LeaderEndCondition.Attached
        tag.TagHeadPosition = head
    try:
        if tag.HasLeaderElbow(ref):
            tag.SetLeaderElbow(ref, c.p3 + (head - c.p3) * 0.5)
    except Exception:
        pass
    return tag


def run(doc, uidoc, preselected):
    view = doc.ActiveView
    frame = ViewFrame(view)

    # --- типы марок ---
    tag_syms = {}
    multi = []
    for s_ in FilteredElementCollector(doc).OfClass(FamilySymbol):
        c = s_.Category
        if c is None:
            continue
        tci = _iv(c.Id)
        if tci == MULTI_TAG_CAT:
            multi.append(s_)
        else:
            tag_syms.setdefault(tci, []).append(s_)
    multi.sort(key=_sym_label)

    # --- выделенные марки: их тип подставляется по умолчанию,
    #     а помеченные ими элементы образуют область "Только выделенные" ---
    sel_tags = []
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if isinstance(el, IndependentTag):
            sel_tags.append(el)
    sel_tag_type = {}          # категория элемента -> FamilySymbol марки
    sel_tagged = []
    for t_ in sel_tags:
        try:
            sym_ = doc.GetElement(t_.GetTypeId())
            for eid in t_.GetTaggedLocalElementIds():
                te = doc.GetElement(eid)
                if te is None or te.Category is None:
                    continue
                sel_tagged.append(te)
                sel_tag_type.setdefault(_iv(te.Category.Id), sym_)
        except Exception:
            pass

    # --- элементы на виде по категориям ---
    # Категория попадает в список, если для неё есть своя марка, марка
    # по нескольким категориям, или на её элементы уже стоят выделенные марки
    view_elems = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType().ToElements()
    by_cat = {}
    cat_names = {}
    for e in view_elems:
        c = e.Category
        if c is None or c.CategoryType != CategoryType.Model:
            continue
        ci = _iv(c.Id)
        if ci in NO_OBSTACLE_CATS and ci not in sel_tag_type:
            continue
        if ci not in TAG_MAP and not multi and ci not in sel_tag_type:
            continue
        if isinstance(e, (RevitLinkInstance, ImportInstance, Group, AssemblyInstance)):
            continue
        by_cat.setdefault(ci, []).append(e)
        cat_names[ci] = c.Name

    preset = _load_preset()
    saved = preset if preset else _load_saved()
    saved_types = saved.get('types', {})
    saved_checked = set(saved.get('checked_cats', []))
    rows = []
    syms_for_cat = {}
    # сначала категории с собственными марками, потом остальные
    order = sorted(by_cat.keys(), key=lambda k: (k not in TAG_MAP and k not in sel_tag_type,
                                                 cat_names[k]))
    for ci in order:
        own = sorted(tag_syms.get(TAG_MAP.get(ci), []), key=_sym_label)
        syms = own + multi
        pre = sel_tag_type.get(ci)
        if pre is not None:
            found = False
            for x in syms:
                if x.Id == pre.Id:
                    found = True
                    break
            if not found:
                syms = [pre] + list(syms)
        if not syms:
            continue
        labels_ = [_sym_label(x) for x in syms]
        idx = 0
        if pre is not None:
            idx = [x.Id for x in syms].index(pre.Id)
        elif str(ci) in saved_types and saved_types[str(ci)] in labels_:
            idx = labels_.index(saved_types[str(ci)])
        syms_for_cat[ci] = syms
        if sel_tag_type:
            checked = ci in sel_tag_type
        elif saved_checked:
            checked = str(ci) in saved_checked
        else:
            checked = ci in CURVE_CATS
        row = {
            'cat_id': ci, 'name': cat_names[ci], 'count': len(by_cat[ci]),
            'type_labels': labels_, 'type_index': idx, 'checked': checked,
        }
        if ci in CURVE_CATS:
            row['extras'] = [('vert', u"вертикальные участки (стояки)"),
                             ('alt', u"пересечения систем и главный участок системы")]
            for key, sk in (('vert', 'vtypes'), ('alt', 'atypes')):
                lbl = saved.get(sk, {}).get(str(ci))
                row[key + '_index'] = (labels_.index(lbl) + 1) if lbl in labels_ else 0
        rows.append(row)

    if not rows:
        forms.alert(u"На виде нет элементов, для которых в проекте загружены марки.",
                    title=u"Умные выноски")
        return

    sel_elems = [e for e in preselected if e.Category is not None and _iv(e.Category.Id) in by_cat]
    seen_ = set(_iv(e.Id) for e in sel_elems)
    for te in sel_tagged:
        if _iv(te.Id) not in seen_ and _iv(te.Category.Id) in by_cat:
            seen_.add(_iv(te.Id))
            sel_elems.append(te)
    if sel_tags:
        saved = dict(saved)
        saved['existing'] = 'move'      # выделены марки - по умолчанию переразмещаем их
    view_text = u"Вид: {} · 1:{} · {}".format(view.Name, frame.scale, view.ViewType)
    if sel_tags:
        view_text += u"   ·   выделено марок: {}".format(len(sel_tags))
    filter_names = []
    filters_by_name = {}
    try:
        for fid in view.GetFilters():
            f_ = doc.GetElement(fid)
            if isinstance(f_, ParameterFilterElement):
                filter_names.append(_ename(f_))
                filters_by_name[_ename(f_)] = f_
    except Exception:
        pass
    win = SettingsWindow(view_text, rows, bool(sel_elems), saved, filter_names)
    if preset and preset.get('autorun'):
        win._on_run(None, None)          # пресет без окна: те же проверки, что и по кнопке
    else:
        win.ShowDialog()
    settings = win.result
    if not settings:
        return

    tagtype_by_cat = {}
    type_names = {}
    for ci, k in settings['cats'].items():
        s = syms_for_cat[ci][k]
        tagtype_by_cat[ci] = s.Id
        type_names[str(ci)] = _sym_label(s)
    vsym, alt, vnames, anames = {}, {}, {}, {}
    for ci, k in settings.get('vcats', {}).items():
        vsym[ci] = syms_for_cat[ci][k].Id
        vnames[str(ci)] = _sym_label(syms_for_cat[ci][k])
    for ci, k in settings.get('acats', {}).items():
        alt[ci] = syms_for_cat[ci][k].Id
        anames[str(ci)] = _sym_label(syms_for_cat[ci][k])
    settings['_vsym'] = vsym
    settings['_alt'] = alt
    settings['vtypes'] = vnames
    settings['atypes'] = anames
    _save(settings, type_names)

    if not _ensure_view(doc, view):
        return
    frame = ViewFrame(view)   # после блокировки направления могли уточниться

    if settings['scope_sel']:
        elems = [e for e in sel_elems if _iv(e.Category.Id) in tagtype_by_cat]
    else:
        elems = []
        for ci in tagtype_by_cat:
            elems.extend(by_cat.get(ci, []))

    # --- отбор по фильтрам вида (например, "В монтаж") ---
    def _passes(f_, e):
        try:
            if _iv(e.Category.Id) not in set(_iv(c) for c in f_.GetCategories()):
                return False
            ef = f_.GetElementFilter()
            return True if ef is None else ef.PassesFilter(e)
        except Exception:
            return False
    f_inc = filters_by_name.get(settings.get('f_inc'))
    f_exc = filters_by_name.get(settings.get('f_exc'))
    if f_inc is not None or f_exc is not None:
        elems = [e for e in elems
                 if (f_inc is None or _passes(f_inc, e)) and (f_exc is None or not _passes(f_exc, e))]

    # --- взрыв-схема: сдвиги элементов ---
    n_disp = _load_displacements(doc, view)

    # --- существующие марки на виде ---
    existing_by_elem = {}
    all_view_tags = list(FilteredElementCollector(doc, view.Id).OfClass(IndependentTag))
    for t in all_view_tags:
        try:
            for eid in t.GetTaggedLocalElementIds():
                existing_by_elem.setdefault(_iv(eid), []).append(t)
        except Exception:
            pass

    t_start = time.time()
    quality = settings['quality']
    passes = (1, 4, 12)[quality]

    labels, skipped, to_delete = collect(doc, frame, elems, settings, existing_by_elem,
                                         tagtype_by_cat)
    n_processed = len(labels) + sum(len(v) for v in skipped.values())

    if not labels and not to_delete:
        msg = [u"Нечего маркировать."]
        for k, v in skipped.items():
            msg.append(u"• {}: {}".format(k, len(v)))
        forms.alert(u"\n".join(msg), title=u"Умные выноски")
        return

    # id удаляемых марок запоминаем ДО удаления - потом обращаться к ним нельзя
    deleted_ids = set(_iv(x.Id) for x in to_delete)
    n_deleted = len(deleted_ids)

    tg = TransactionGroup(doc, u"Умные выноски")
    tg.Start()
    try:
        # удаление существующих (режим "удалить и создать заново")
        if to_delete:
            t = Transaction(doc, u"Удаление существующих марок")
            t.Start()
            ids = List[ElementId]()
            for k in deleted_ids:
                ids.Add(ElementId(k))
            doc.Delete(ids)
            t.Commit()
        if not labels:
            tg.Assimilate()
            forms.alert(u"Удалено марок: {}. Новых марок для размещения нет.".format(
                n_deleted), title=u"Умные выноски")
            return
        own_tag_ids = set(deleted_ids)
        own_tag_ids.update(_iv(lb.tag.Id) for lb in labels if lb.tag is not None)

        with forms.ProgressBar(title=u"Умные выноски: замер марок...") as pb:
            pb.update_progress(1, 5)
            for lb in labels:
                lb.a = frame.uv(lb.anchor)
                lb.ebox = elem_box(lb, frame)
            measure(doc, frame, labels)

            # --- сетка препятствий ---
            pb.title = u"Умные выноски: препятствия..."
            pb.update_progress(2, 5)
            pref = settings['pref_mm'] * frame.mm
            maxd = settings['max_mm'] * frame.mm
            max_w = max(lb.rel[2] - lb.rel[0] for lb in labels)
            max_h = max(lb.rel[3] - lb.rel[1] for lb in labels)
            margin = maxd + 2 * max(max_w, max_h)
            us = [lb.a[0] for lb in labels]
            vs = [lb.a[1] for lb in labels]
            gu0, gv0 = min(us) - margin, min(vs) - margin
            gu1, gv1 = max(us) + margin, max(vs) + margin
            crop = frame.crop_uv()
            if crop is not None:
                gu0, gv0 = max(gu0, crop[0]), max(gv0, crop[1])
                gu1, gv1 = min(gu1, crop[2]), min(gv1, crop[3])
            cell = max(1.0 * frame.mm, max(gu1 - gu0, gv1 - gv0) / 700.0)
            grid = OccGrid(gu0, gv0, gu1, gv1, cell)
            grid_area = (gu1 - gu0) * (gv1 - gv0)
            n_obst = 0
            for e in view_elems:
                try:
                    # удалённые в режиме "удалить и создать заново" - пропускаем
                    if not e.IsValidObject or _iv(e.Id) in own_tag_ids:
                        continue
                    c = e.Category
                except Exception:
                    continue
                if c is None:
                    continue
                ci = _iv(c.Id)
                if c.CategoryType == CategoryType.Annotation:
                    if isinstance(e, IndependentTag) and _iv(e.Id) in own_tag_ids:
                        continue
                    if not isinstance(e, (IndependentTag, TextNote, SpotDimension)):
                        continue
                elif c.CategoryType != CategoryType.Model or ci in NO_OBSTACLE_CATS:
                    continue
                if isinstance(e, (RevitLinkInstance, ImportInstance, Group, AssemblyInstance)):
                    continue
                crv = _curve_of(e) if isinstance(e, (MEPCurve, Wall)) else None
                try:
                    if crv is not None:
                        if isinstance(e, Wall):
                            r = max(e.Width / 2.0, 0.6 * frame.mm)
                        else:
                            r = _curve_half_width(e, frame)
                        dp = _dp(e)
                        grid.stamp_capsule(frame.uv(crv.GetEndPoint(0) + dp),
                                           frame.uv(crv.GetEndPoint(1) + dp), r)
                        n_obst += 1
                        continue
                    if isinstance(e, SpotDimension):
                        # высотная отметка: на взрыв-схеме габарит Revit отдаёт в исходном
                        # положении элемента - берём точку, где отметка нарисована
                        p0 = e.LeaderShoulderPosition if e.HasLeader else e.Origin
                        su, sv = frame.uv(p0)
                        grid.stamp_rect(su - 12 * frame.mm, sv - 4 * frame.mm,
                                        su + 12 * frame.mm, sv + 4 * frame.mm)
                        n_obst += 1
                        continue
                    bb = e.get_BoundingBox(view)
                    if bb is None:
                        continue
                    b = frame.box_uv(bb)
                    if isinstance(e, IndependentTag) and e.HasLeader:
                        # габарит марки с выноской - почти весь прямоугольник пустой:
                        # занятой считаем только область вокруг головы марки
                        hu_, hv_ = frame.uv(e.TagHeadPosition)
                        b = (max(b[0], hu_ - 16 * frame.mm), max(b[1], hv_ - 7 * frame.mm),
                             min(b[2], hu_ + 16 * frame.mm), min(b[3], hv_ + 7 * frame.mm))
                        if b[2] <= b[0] or b[3] <= b[1]:
                            continue
                    if (b[2] - b[0]) * (b[3] - b[1]) > 0.3 * grid_area:
                        continue
                    grid.stamp_rect(*b)
                    n_obst += 1
                except Exception:
                    continue
            grid.build()

            # --- кандидаты и раскладка ---
            pb.title = u"Умные выноски: раскладка..."
            pb.update_progress(3, 5)
            ortho = settings.get('ortho', True)
            global W_LX
            if ortho:
                W_LX = W_LX_O
                GAP_BOX[0] = settings.get('gap_mm', 1.0) * frame.mm
                build_candidates_ortho(frame, labels, grid, crop, pref, maxd)
                rest = [lb for lb in labels if not lb.cands]
                if rest:
                    build_candidates(frame, rest, grid, crop, pref, maxd)
            else:
                build_candidates(frame, labels, grid, crop, pref, maxd)
            bucket = max(maxd + 2 * max(max_w, max_h), 10 * frame.mm)
            t_lay = time.time()
            hard, evals = optimize(labels, passes, bucket)
            n_aligned = 0
            if ortho and settings.get('align', True):
                n_aligned = align_pass(labels, frame, (grid, crop, frame, pref, maxd), bucket)
                for lb in labels:
                    lb.problem = False
                hard = _count_conflicts(labels, bucket)
            t_lay = time.time() - t_lay

            # --- размещение ---
            pb.title = u"Умные выноски: размещение..."
            pb.update_progress(4, 5)
            t = Transaction(doc, u"Размещение марок")
            t.Start()
            n_new, n_moved, errors = 0, 0, []
            n_retarget = 0
            for lb in labels:
                c = lb.cands[lb.cur]
                cu = (c.box[0] + c.box[2]) / 2.0
                cv = (c.box[1] + c.box[3]) / 2.0
                hu = cu - (lb.rel[0] + lb.rel[2]) / 2.0
                hv = cv - (lb.rel[1] + lb.rel[3]) / 2.0
                head = lb.anchor + frame.R * (hu - lb.a[0]) + frame.U * (hv - lb.a[1])
                if c.p3 is not None:
                    try:
                        tag = _place_ortho(doc, view, frame, lb, c, hu, hv)
                        if lb.kind == 'new':
                            n_new += 1
                        else:
                            n_moved += 1
                            if tag is not lb.tag:
                                n_retarget += 1
                        lb.new_tag = tag
                    except Exception as ex:
                        errors.append(lb.elem.Id)
                    continue
                try:
                    if lb.kind == 'new':
                        tag = IndependentTag.Create(doc, lb.sym_id, view.Id, Reference(lb.elem),
                                                    False, TagOrientation.Horizontal, head)
                        n_new += 1
                    else:
                        tag = lb.tag
                        tag.TagHeadPosition = head
                        n_moved += 1
                    lb.new_tag = tag
                    tag.HasLeader = True
                    try:
                        if settings.get('attached', True):
                            # как при ручной расстановке: Revit сам ведёт выноску к элементу
                            tag.LeaderEndCondition = LeaderEndCondition.Attached
                        else:
                            la = c.lead_a
                            end = (lb.anchor + frame.R * (la[0] - lb.a[0])
                                   + frame.U * (la[1] - lb.a[1]))
                            tag.LeaderEndCondition = LeaderEndCondition.Free
                            for r in tag.GetTaggedReferences():
                                tag.SetLeaderEnd(r, end)
                                break
                        tag.TagHeadPosition = head
                    except Exception:
                        pass
                except Exception as ex:
                    errors.append(lb.elem.Id)
            t.Commit()
            pb.update_progress(5, 5)

        tg.Assimilate()
    except Exception as ex:
        try:
            tg.RollBack()
        except Exception:
            pass
        forms.alert(u"Ошибка, изменения отменены:\n\n{}".format(ex), title=u"Умные выноски")
        return

    uidoc.RefreshActiveView()

    # --- отчёт ---
    total_time = time.time() - t_start
    n_skipped = sum(len(v) for v in skipped.values())
    lines = [
        u"Вид: {}".format(view.Name),
        u"",
        u"Обработано: {}".format(n_processed),
        u"Размещено новых: {}".format(n_new),
    ]
    if n_moved:
        lines.append(u"Переразмещено существующих: {}".format(n_moved))
    if settings.get('ortho', True):
        lines.append(u"Выноски под прямым углом; выровнено в ряды/колонки: {}".format(n_aligned))
        if n_retarget:
            lines.append(u"Марок перенесено на другой сегмент участка: {}".format(n_retarget))
    if n_deleted:
        lines.append(u"Удалено старых марок: {}".format(n_deleted))
    lines.append(u"Пропущено: {}".format(n_skipped))
    for k, v in sorted(skipped.items()):
        lines.append(u"   • {}: {}".format(k, len(v)))
    lines.append(u"Конфликтов (наложения / пересечения выносок): {}".format(hard))
    if n_disp:
        lines.append(u"Взрыв-схема: учтено смещённых элементов {}".format(n_disp))
    if errors:
        lines.append(u"Ошибок создания: {}".format(len(errors)))
    lines.append(u"")
    if hard == 0 and not errors:
        lines.append(u"Все марки размещены без пересечений текста и выносок.")
    lines.append(u"Одно нажатие Ctrl+Z отменяет весь запуск.")
    lines.append(u"")
    lines.append(u"Раскладка: марок {}, препятствий {}, вариантов проверено {}, "
                 u"проходов {}, время {:.1f} c · всего {:.1f} c".format(
                     len(labels), n_obst, evals, passes, t_lay, total_time))

    problem_ids = [lb.new_tag.Id for lb in labels if lb.problem and lb.new_tag is not None]
    problem_ids += errors
    try:
        import io
        import os
        with io.open(os.path.join(os.environ.get('TEMP', u"."), u"smart_tags_report.txt"),
                     'w', encoding='utf-8') as f:
            f.write(u"\n".join(lines))
            f.write(u"\nproblem_ids: " + u",".join(str(_iv(i)) for i in problem_ids))
    except Exception:
        pass
    if preset and preset.get('quiet'):
        return
    opts = [u"Закрыть"]
    if problem_ids:
        opts = [u"Выделить проблемные", u"Закрыть"]
    choice = forms.alert(u"\n".join(lines), title=u"Умные выноски — результат", options=opts)
    if choice == u"Выделить проблемные":
        uidoc.Selection.SetElementIds(List[ElementId](problem_ids))
