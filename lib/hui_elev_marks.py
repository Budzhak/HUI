# -*- coding: utf-8 -*-
"""
Марки-отметки на взрыв-схемах (ХУЙ.extension/lib/hui_elev_marks.py).

Штатная высотная отметка Revit на смещённом элементе (DisplacementElement)
рисуется в ИСХОДНОМ положении элемента. Поэтому вместо неё ставится марка
семейства "db_М_Отметка_Схемы" (стрелка + полка + значение), которая рисуется
там, где элемент показан на схеме.

- значение (от ±0.000, базовая точка проекта) пишется в параметр элемента
  db_Отметка_Схемы (слот 1) или db_Отметка_Схемы_2 (слот 2 - вторая отметка
  на том же элементе); типы марки: "Полка вправо/влево" и "... 2";
- в марке (Extensible Storage) хранится ссылка на грань/ребро/ось элемента и
  точка на ней - по ним отметка пересчитывается;
- ElevUpdater (подключается в startup.py) при изменении геометрии элемента
  или смещения на взрыв-схеме пересчитывает значение и переносит марку.
"""
import clr
from System import Guid
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.ExtensibleStorage import (Schema, SchemaBuilder, Entity,
                                                 AccessLevel)

FAMILY = u"db_М_Отметка_Схемы"
PARAMS = (u"db_Отметка_Схемы", u"db_Отметка_Схемы_2")
# старые имена типов (для справки; типы теперь узнаются по имени через type_key)
TYPES = {('R', 1): u"Полка вправо", ('L', 1): u"Полка влево",
         ('R', 2): u"Полка вправо 2", ('L', 2): u"Полка влево 2",
         ('UR', 1): u"Полка вверху вправо", ('UL', 1): u"Полка вверху влево",
         ('UR', 2): u"Полка вверху вправо 2", ('UL', 2): u"Полка вверху влево 2"}
# стороны: R/L - полка внизу, значение под полкой (как "...Схемы_Вниз");
#          UR/UL - полка вверху, значение над полкой (как "...Вверх")
SIDES = [('R', u"Полка внизу, вправо"), ('L', u"Полка внизу, влево"),
         ('UR', u"Полка вверху, вправо"), ('UL', u"Полка вверху, влево")]
SCHEMA_GUID = Guid("6f0f3b52-2b8e-4f3a-9a41-5c2d7e9b1a11")
HEAD_GUID = Guid("6f0f3b52-2b8e-4f3a-9a41-5c2d7e9b1a21")   # где была точка привязки при последнем пересчёте
UPDATER_GUID = Guid("6f0f3b52-2b8e-4f3a-9a41-5c2d7e9b1a02")
FT_MM = 304.8


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


# ------------------------------------------------------------------ storage
# Точка отметки хранится относительно самого элемента, а не в абсолютных
# координатах - поэтому при переносе/изменении элемента она едет вместе с ним:
#   MEPCurve: T - параметр на оси (0..1), O - смещение от оси;
#             Role "bottom" - низ прямоугольного (смещение = -высота/2 по Z),
#             "axis" - ось, "free" - произвольная точка (смещение хранится);
#   FamilyInstance: Role "fi", O - точка в системе координат экземпляра.
def schema():
    s = Schema.Lookup(SCHEMA_GUID)
    if s is not None:
        return s
    b = SchemaBuilder(SCHEMA_GUID)
    b.SetSchemaName("db_ElevMark2")
    b.SetReadAccessLevel(AccessLevel.Public)
    b.SetWriteAccessLevel(AccessLevel.Public)
    b.AddSimpleField("Elem", ElementId)
    b.AddSimpleField("Role", str)
    b.AddSimpleField("T", float).SetSpec(SpecTypeId.Number)
    for n in ("OX", "OY", "OZ"):
        b.AddSimpleField(n, float).SetSpec(SpecTypeId.Length)
    b.AddSimpleField("Slot", int)
    return b.Finish()


def write_entity(tag, el, role, t, o, slot):
    e = Entity(schema())
    e.Set[ElementId]("Elem", el.Id)
    e.Set[str]("Role", role)
    e.Set[float]("T", float(t), UnitTypeId.General)
    e.Set[float]("OX", o.X, UnitTypeId.Feet)
    e.Set[float]("OY", o.Y, UnitTypeId.Feet)
    e.Set[float]("OZ", o.Z, UnitTypeId.Feet)
    e.Set[int]("Slot", slot)
    tag.SetEntity(e)


def head_schema():
    s = Schema.Lookup(HEAD_GUID)
    if s is not None:
        return s
    b = SchemaBuilder(HEAD_GUID)
    b.SetSchemaName("db_ElevMarkHead")
    b.SetReadAccessLevel(AccessLevel.Public)
    b.SetWriteAccessLevel(AccessLevel.Public)
    for n in ("HX", "HY", "HZ"):
        b.AddSimpleField(n, float).SetSpec(SpecTypeId.Length)
    return b.Finish()


def last_anchor(tag):
    """Точка привязки (с учётом разноса), которая была при прошлом пересчёте, или None."""
    s = Schema.Lookup(HEAD_GUID)
    if s is None:
        return None
    e = tag.GetEntity(s)
    if e is None or not e.IsValid():
        return None
    return XYZ(e.Get[float]("HX", UnitTypeId.Feet), e.Get[float]("HY", UnitTypeId.Feet),
               e.Get[float]("HZ", UnitTypeId.Feet))


def set_last_anchor(tag, p):
    e = Entity(head_schema())
    e.Set[float]("HX", p.X, UnitTypeId.Feet)
    e.Set[float]("HY", p.Y, UnitTypeId.Feet)
    e.Set[float]("HZ", p.Z, UnitTypeId.Feet)
    tag.SetEntity(e)


def read_entity(tag):
    s = Schema.Lookup(SCHEMA_GUID)
    if s is None:
        return None
    e = tag.GetEntity(s)
    if e is None or not e.IsValid():
        return None
    o = XYZ(e.Get[float]("OX", UnitTypeId.Feet), e.Get[float]("OY", UnitTypeId.Feet),
            e.Get[float]("OZ", UnitTypeId.Feet))
    return (e.Get[ElementId]("Elem"), e.Get[str]("Role"), e.Get[float]("T", UnitTypeId.General),
            o, e.Get[int]("Slot"))


def _curve(el):
    if isinstance(el, MEPCurve) and isinstance(el.Location, LocationCurve):
        return el.Location.Curve
    return None


def _half_h(el):
    for bip in (BuiltInParameter.RBS_CURVE_HEIGHT_PARAM,):
        try:
            p = el.get_Parameter(bip)
            if p is not None and p.HasValue and p.AsDouble() > 0:
                return p.AsDouble() / 2.0
        except Exception:
            pass
    return 0.0


def anchor_of(el, p, role=None):
    """(role, t, o) для точки p на элементе."""
    c = _curve(el)
    if c is not None:
        r = c.Project(p)
        t = c.ComputeNormalizedParameter(r.Parameter)
        o = p - r.XYZPoint
        if role is None:
            role = 'free'
        if role == 'axis':
            o = XYZ.Zero
        return role, t, o
    if isinstance(el, FamilyInstance):
        return 'fi', 0.0, el.GetTotalTransform().Inverse.OfPoint(p)
    return 'abs', 0.0, p


def point_of(el, role, t, o):
    c = _curve(el)
    if c is not None and role in ('free', 'axis', 'bottom'):
        base = c.Evaluate(min(max(t, 0.0), 1.0), True)
        if role == 'axis':
            return base
        if role == 'bottom':
            # низ прямоугольного: по горизонтали - как при постановке (ребро),
            # по высоте - всегда низ сечения (меняется вместе с размером)
            return base + XYZ(o.X, o.Y, -_half_h(el))
        return base + o
    if role == 'fi' and isinstance(el, FamilyInstance):
        return el.GetTotalTransform().OfPoint(o)
    return o


# ------------------------------------------------------------------ helpers
ROUND_MM = 5      # шаг округления отметки, мм (как формат Revit: метры, округление 0.005)


def fmt(z_ft):
    """Отметка в метрах: знак, 3 знака после точки, шаг ROUND_MM (+6.842 -> +6.840)."""
    mm = z_ft * FT_MM
    mm = int(round(mm / float(ROUND_MM))) * ROUND_MM if ROUND_MM else int(round(mm))
    if mm == 0:
        return u"±0.000"
    sign = u"+" if mm > 0 else u"-"
    mm = abs(mm)
    return u"{}{}.{:03d}".format(sign, mm // 1000, mm % 1000)


def base_z(doc):
    try:
        return BasePoint.GetProjectBasePoint(doc).Position.Z
    except Exception:
        return 0.0


def disp_map(doc, view):
    m = {}
    if not isinstance(view, View3D):
        return m
    for de in FilteredElementCollector(doc, view.Id).OfClass(DisplacementElement):
        try:
            v = de.GetAbsoluteDisplacement()
            for eid in de.GetDisplacedElementIds():
                m[_iv(eid)] = v
        except Exception:
            pass
    return m


def disp_of(dmap, el):
    v = dmap.get(_iv(el.Id))
    if v is None:
        sup = getattr(el, 'SuperComponent', None)
        if sup is not None:
            v = dmap.get(_iv(sup.Id))
    return v if v is not None else XYZ.Zero


def type_key(nm):
    """Сторона и слот по имени типа, без привязки к точному написанию:
    «вверху» -> U, «влево» -> L, иначе R; « 2» -> слот 2. Хвост после «_»
    (например «_10» — другая длина подключения) — вариант того же типа.
    Подходят и старые имена («Полка вправо»), и новые («Полка внизу вправо_10»)."""
    n = (nm or u'').lower().replace(u'ё', u'е')
    base, _, var = n.partition(u'_')
    side = (u'U' if u'вверх' in base else u'') + (u'L' if u'влев' in base else u'R')
    words = base.replace(u'-', u' ').split()
    slot = 2 if words and words[-1] == u'2' else 1
    return (side, slot), var.strip()


def family_symbols(doc):
    return [s for s in FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(
            BuiltInCategory.OST_MultiCategoryTags) if s.FamilyName == FAMILY]


def mark_types(doc):
    """{(сторона, слот): тип}. Основной тип — без хвоста «_…»; если есть только
    варианты — самый короткий по имени."""
    res, rank = {}, {}
    for s in family_symbols(doc):
        nm = s.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
        key, var = type_key(nm)
        r = (1 if var else 0, len(nm or u''))
        if key not in rank or r < rank[key]:
            res[key], rank[key] = s, r
    return res


def mark_tags(doc, view=None):
    """Все марки семейства отметок, любого типа (в т.ч. «_10» и переименованные)."""
    ids = set(_iv(s.Id) for s in family_symbols(doc))
    col = FilteredElementCollector(doc, view.Id) if view is not None else FilteredElementCollector(doc)
    return [t for t in col.OfClass(IndependentTag) if _iv(t.GetTypeId()) in ids]


def _project(doc, el, refstr, p):
    """Точка на грани/ребре/оси элемента, ближайшая к p (исходное положение)."""
    try:
        ref = Reference.ParseFromStableRepresentation(doc, refstr)
        g = el.GetGeometryObjectFromReference(ref)
    except Exception:
        return None
    if g is None and isinstance(el, MEPCurve) and isinstance(el.Location, LocationCurve):
        g = el.Location.Curve            # ось трубы/воздуховода (":0:LINEAR")
    if g is None:
        return None
    trs = [Transform.Identity]
    if isinstance(el, FamilyInstance):
        trs.append(el.GetTotalTransform())
    best = None
    for tr in trs:
        q = tr.Inverse.OfPoint(p)
        r = None
        try:
            if isinstance(g, Edge):
                r = g.AsCurve().Project(q)
            elif isinstance(g, Curve):
                r = g.Project(q)
            elif isinstance(g, Face):
                r = g.Project(q)
        except Exception:
            r = None
        if r is None:
            continue
        x = tr.OfPoint(r.XYZPoint)
        d = x.DistanceTo(p)
        if best is None or d < best[0]:
            best = (d, x)
    return best[1] if best else None


# ------------------------------------------------------------------ placing
def used_slots(doc, el, view=None):
    used = {}
    for t in mark_tags(doc, view):
        info = read_entity(t)
        if info and info[0] == el.Id:
            used[info[4]] = t
    return used


def place(doc, view, el, ref, p, side='R', slot=None, dmap=None, role=None):
    """Марка-отметка на элементе el в точке p (исходное положение, на ref)."""
    types = mark_types(doc)
    if (side, 1) not in types:
        names = [x.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString() for x in family_symbols(doc)]
        if not names:
            raise Exception(u"В проект не загружено семейство марки «{}»".format(FAMILY))
        raise Exception(u"У семейства «{}» нет типа для стороны «{}». Есть типы: {}".format(
            FAMILY, dict(SIDES).get(side, side), u", ".join(sorted(names))))
    if dmap is None:
        dmap = disp_map(doc, view)
    if slot is None:
        used = used_slots(doc, el, view)
        slot = 1 if 1 not in used else (2 if 2 not in used else 2)
        if slot in used:
            doc.Delete(used[slot].Id)
    prm = el.LookupParameter(PARAMS[slot - 1])
    if prm is None:
        raise Exception(u"У элемента нет параметра {}".format(PARAMS[slot - 1]))
    # правило отметок: прямоугольный - низ, круглый - ось (см. auto_role)
    r_, t_, o_ = anchor_of(el, p, role)
    q = point_of(el, r_, t_, o_)
    prm.Set(fmt(q.Z - base_z(doc)))
    head = q + disp_of(dmap, el)
    tag = IndependentTag.Create(doc, types[(side, slot)].Id, view.Id, Reference(el), False,
                                TagOrientation.Horizontal, head)
    tag.TagHeadPosition = head
    write_entity(tag, el, r_, t_, o_, slot)
    set_last_anchor(tag, head)
    return tag


def is_round(el):
    for bip in (BuiltInParameter.RBS_CURVE_DIAMETER_PARAM, BuiltInParameter.RBS_PIPE_DIAMETER_PARAM):
        try:
            p = el.get_Parameter(bip)
            if p is not None and p.HasValue and p.AsDouble() > 0:
                return True
        except Exception:
            pass
    return False


def auto_role(el):
    """Правило отметок на схемах: у горизонтальных (и наклонных до ~11°)
    прямоугольных воздуховодов - низ, у круглых воздуховодов и труб - ось.
    Вертикальные участки и прочие элементы - точка, куда указали."""
    c = _curve(el)
    if c is None:
        return None
    d = c.GetEndPoint(1) - c.GetEndPoint(0)
    if d.GetLength() < 1e-9 or abs(d.Normalize().Z) > 0.2:
        return None
    return 'axis' if is_round(el) else 'bottom'


def t_from_head(el, tag, view, disp, t_old):
    """Где на оси элемента стоит стрелка марки: параметр t (0..1) по проекции
    головы марки (за вычетом разноса) на ось в плоскости вида. Рядом с концом
    (ближе 2 мм листа, не больше четверти длины) — ровно конец. Если ось видна торцом — старое t."""
    c = _curve(el)
    if c is None or view is None:
        return t_old
    R, U = view.RightDirection, view.UpDirection
    a, b = c.GetEndPoint(0), c.GetEndPoint(1)
    p = tag.TagHeadPosition - disp
    au, av, bu, bv = a.DotProduct(R), a.DotProduct(U), b.DotProduct(R), b.DotProduct(U)
    pu, pv = p.DotProduct(R), p.DotProduct(U)
    L2 = (bu - au) ** 2 + (bv - av) ** 2
    if L2 < 1e-6:
        return t_old
    t = ((pu - au) * (bu - au) + (pv - av) * (bv - av)) / L2
    t = max(0.0, min(1.0, t))
    # прилипание к концу: 2 мм листа, но не больше четверти длины элемента на виде
    snap = min(0.25, 2.0 * view.Scale / FT_MM / (L2 ** 0.5))
    if t < snap:
        t = 0.0
    elif t > 1.0 - snap:
        t = 1.0
    return t


def refresh(doc, tags=None, dmaps=None, moved=None):
    """Пересчёт значений и положения марок-отметок. Возвращает число изменённых.
    moved - id марок, которые сдвинул пользователь: у них отметка берётся там,
    куда теперь показывает стрелка (вдоль оси трубы/воздуховода), сама марка
    не двигается."""
    moved = moved or set()
    if tags is None:
        tags = mark_tags(doc)
    dmaps = dmaps if dmaps is not None else {}
    bz = base_z(doc)
    n = 0
    for tg in tags:
        info = read_entity(tg)
        if not info:
            continue
        eid, role, t, o, slot = info
        el = doc.GetElement(eid)
        if el is None:
            continue
        vid = tg.OwnerViewId
        v = doc.GetElement(vid)
        if _iv(vid) not in dmaps:
            dmaps[_iv(vid)] = disp_map(doc, v)
        follow = _iv(tg.Id) in moved and role in ('free', 'axis', 'bottom')
        if follow:
            t2 = t_from_head(el, tg, v, disp_of(dmaps[_iv(vid)], el), t)
            if abs(t2 - t) > 1e-6:
                try:
                    write_entity(tg, el, role, t2, o, slot)
                    t = t2
                except Exception:
                    pass
        q = point_of(el, role, t, o)
        val = fmt(q.Z - bz)
        prm = el.LookupParameter(PARAMS[slot - 1])
        if prm is not None and prm.AsString() != val:
            prm.Set(val)
            n += 1
        anchor = q + disp_of(dmaps[_iv(vid)], el)
        # Марку, сдвинутую вручную, не возвращаем к точке привязки: двигаем её
        # только на столько, на сколько сдвинулась сама точка привязки.
        last = last_anchor(tg)
        try:
            if last is None or follow:
                # старые и сдвинутые вручную: марку не двигаем, только запоминаем точку
                # (пишем лишь при изменении - чтобы не вызывать обработчик повторно)
                if last is None or (anchor - last).GetLength() > 1e-6:
                    set_last_anchor(tg, anchor)
                continue
            dd = anchor - last
            if abs(dd.DotProduct(v.RightDirection)) > 1e-4 or abs(dd.DotProduct(v.UpDirection)) > 1e-4:
                tg.TagHeadPosition = tg.TagHeadPosition + dd
                n += 1
            if dd.GetLength() > 1e-6:
                set_last_anchor(tg, anchor)
        except Exception:
            pass                                     # марка занята другим пользователем
    return n


# ------------------------------------------------------------------ updater
def _log_error(where):
    """Ошибки обработчика — в %TEMP%\\hui_errors.log (не глушим молча)."""
    try:
        import io as _io, os as _os, traceback as _tb, datetime as _dt
        p = _os.path.join(_os.environ.get('TEMP', ''), 'hui_errors.log')
        with _io.open(p, 'a', encoding='utf-8') as fh:
            fh.write(u'%s hui_elev_marks %s\n%s\n' % (_dt.datetime.now(), where, _tb.format_exc()))
    except Exception:
        pass


class ElevUpdater(IUpdater):
    def __init__(self, addin_id):
        self._uid = UpdaterId(addin_id, UPDATER_GUID)

    def GetUpdaterId(self):
        return self._uid

    def GetUpdaterName(self):
        return u"ХУЙ: марки-отметки на взрыв-схемах"

    def GetAdditionalInformation(self):
        return u"Пересчитывает марки db_М_Отметка_Схемы при изменении элементов"

    def GetChangePriority(self):
        return ChangePriority.Annotations

    def Execute(self, data):
        try:
            # выключатель: кнопка «ХОЧУ → Обработчики» (lib/hui_settings.py)
            try:
                import hui_settings
                if not hui_settings.get('elev_marks_auto'):
                    return
            except Exception:
                _log_error('settings')
            doc = data.GetDocument()
            if Schema.Lookup(SCHEMA_GUID) is None:
                return
            changed = set(_iv(i) for i in data.GetModifiedElementIds())
            disp_changed = False
            for i in data.GetModifiedElementIds():
                if isinstance(doc.GetElement(i), DisplacementElement):
                    disp_changed = True
                    break
            tags = mark_tags(doc)
            if not tags:
                return
            todo, moved = [], set()
            for t in tags:
                info = read_entity(t)
                if not info:
                    continue
                if disp_changed or _iv(info[0]) in changed:
                    todo.append(t)                 # изменился элемент или разнос - марка едет за ним
                elif _iv(t.Id) in changed:
                    moved.add(_iv(t.Id))           # сдвинули только марку - отметка за стрелкой
                    todo.append(t)
            if todo:
                refresh(doc, todo, moved=moved)
        except Exception:
            _log_error('updater')


def register(addin_id):
    up = ElevUpdater(addin_id)
    if UpdaterRegistry.IsUpdaterRegistered(up.GetUpdaterId()):
        # повторный запуск (Reload pyRevit): старый обработчик живёт в старом движке
        UpdaterRegistry.UnregisterUpdater(up.GetUpdaterId())
    UpdaterRegistry.RegisterUpdater(up, True)
    flt = ElementMulticategoryFilter(_cat_list([
            BuiltInCategory.OST_DuctCurves, BuiltInCategory.OST_DuctFitting,
            BuiltInCategory.OST_DuctAccessory, BuiltInCategory.OST_MechanicalEquipment,
            BuiltInCategory.OST_FlexDuctCurves, BuiltInCategory.OST_PipeCurves,
            BuiltInCategory.OST_PipeFitting, BuiltInCategory.OST_PipeAccessory,
            BuiltInCategory.OST_DuctTerminal]))
    UpdaterRegistry.AddTrigger(up.GetUpdaterId(), flt, Element.GetChangeTypeGeometry())
    UpdaterRegistry.AddTrigger(up.GetUpdaterId(), ElementClassFilter(DisplacementElement),
                               Element.GetChangeTypeAny())
    # сдвиг марки-отметки пользователем: отметка следует за стрелкой
    UpdaterRegistry.AddTrigger(up.GetUpdaterId(), ElementClassFilter(IndependentTag),
                               Element.GetChangeTypeAny())
    return up


def unregister(addin_id):
    """Снять обработчик (выключатель «Автопилот»)."""
    uid = UpdaterId(addin_id, UPDATER_GUID)
    if UpdaterRegistry.IsUpdaterRegistered(uid):
        UpdaterRegistry.UnregisterUpdater(uid)

def _cat_list(bics):
    from System.Collections.Generic import List as _L
    lst = _L[BuiltInCategory]()
    for b in bics:
        lst.Add(b)
    return lst


# ------------------------------------------------------------------ picking
def original_point(el, g, dp):
    """Точка клика в исходных координатах элемента: Revit может вернуть точку
    там, где элемент нарисован на взрыв-схеме (со смещением)."""
    if dp is None or dp.GetLength() < 1e-9:
        return g
    c = _curve(el)
    if c is not None:
        def dist(q):
            try:
                return c.Project(q).XYZPoint.DistanceTo(q)
            except Exception:
                return 1e9
        return g if dist(g) <= dist(g - dp) else g - dp
    bb = el.get_BoundingBox(None)
    if bb is None:
        return g

    def gap(p):
        dx = max(bb.Min.X - p.X, 0, p.X - bb.Max.X)
        dy = max(bb.Min.Y - p.Y, 0, p.Y - bb.Max.Y)
        dz = max(bb.Min.Z - p.Z, 0, p.Z - bb.Max.Z)
        return dx + dy + dz
    return g if gap(g) <= gap(g - dp) else g - dp


def point_on_axis(el, g, dp, view):
    """Для трубы/воздуховода: точка на оси (исходное положение), ближайшая к
    клику НА ЛИСТЕ. Клик на взрыв-схеме Revit может вернуть и в исходных,
    и в смещённых координатах - сравниваются оба варианта в плоскости вида."""
    c = _curve(el)
    if c is None or g is None:
        return None
    R, U = view.RightDirection, view.UpDirection
    a, b = c.GetEndPoint(0), c.GetEndPoint(1)
    best = None
    for shift in (XYZ.Zero, dp):
        au, av = (a + shift).DotProduct(R), (a + shift).DotProduct(U)
        bu, bv = (b + shift).DotProduct(R), (b + shift).DotProduct(U)
        gu, gv = g.DotProduct(R), g.DotProduct(U)
        du, dv = bu - au, bv - av
        L2 = du * du + dv * dv
        if L2 < 1e-12:
            t = 0.5          # ось смотрит на зрителя - точка
        else:
            t = max(0.0, min(1.0, ((gu - au) * du + (gv - av) * dv) / L2))
        d = ((au + du * t - gu) ** 2 + (av + dv * t - gv) ** 2) ** 0.5
        if best is None or d < best[0]:
            best = (d, t)
    t = best[1]
    # у концов участка - точно в конец (стыки)
    L = c.Length
    if L > 1e-9:
        if t * L < 0.15:
            t = 0.0
        elif (1 - t) * L < 0.15:
            t = 1.0
    return a + (b - a) * t


def pick_loop(doc, uidoc, side, alert=None):
    """Интерактивно: клик по грани/ребру элемента на взрыв-схеме -> марка-отметка."""
    from Autodesk.Revit.UI.Selection import ObjectType
    from Autodesk.Revit.Exceptions import OperationCanceledException
    view = doc.ActiveView
    dmap = disp_map(doc, view)
    n = 0
    while True:
        try:
            ref = uidoc.Selection.PickObject(
                ObjectType.PointOnElement,
                u"Отметка: укажите точку на элементе (Esc - завершить)")
        except OperationCanceledException:
            break
        el = doc.GetElement(ref)
        if el is None:
            continue
        g = ref.GlobalPoint
        dp = disp_of(dmap, el)
        p = point_on_axis(el, g, dp, view)
        if p is None:
            p = original_point(el, g, dp)
        t = Transaction(doc, u"Марка-отметка на взрыв-схеме")
        t.Start()
        try:
            place(doc, view, el, ref, p, side, None, dmap, auto_role(el))
            t.Commit()
            n += 1
        except Exception as ex:
            t.RollBack()
            if alert:
                alert(u"Не удалось поставить отметку:\n{}".format(ex))
            break
    return n


def _sketch_plane(doc, view, origin):
    if not isinstance(view, View3D):
        return
    t = Transaction(doc, u"Рабочая плоскость для отметки")
    t.Start()
    try:
        view.SketchPlane = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(view.ViewDirection, origin))
        t.Commit()
    except Exception:
        t.RollBack()


def _bottom_point_fi(el):
    """Нижняя точка семейства (центр нижней горизонтальной грани), исходное положение."""
    opt = Options()
    best = None
    geo = el.get_Geometry(opt)
    if geo is None:
        return None
    for g in geo:
        solids = []
        if isinstance(g, GeometryInstance):
            solids = [x for x in g.GetInstanceGeometry() if isinstance(x, Solid)]
        elif isinstance(g, Solid):
            solids = [g]
        for so in solids:
            for f in so.Faces:
                if not isinstance(f, PlanarFace) or f.FaceNormal.Z > -0.99:
                    continue
                try:
                    m = f.Triangulate()
                    q = (m.Vertices[0] + m.Vertices[1] + m.Vertices[2]) / 3.0
                except Exception:
                    q = f.Origin
                if best is None or q.Z < best.Z - 1e-3:
                    best = q
    if best is None:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            best = XYZ((bb.Min.X + bb.Max.X) / 2, (bb.Min.Y + bb.Max.Y) / 2, bb.Min.Z)
    return best


def place_selected(doc, uidoc, elems, side, alert=None):
    """Отметка на ВЫБРАННЫХ элементах - без указания точки (на взрыв-схеме Revit
    не даёт указать смещённый элемент). Сначала выделить элементы, потом запустить.
      горизонтальный прямоугольный воздуховод - низ, середина участка;
      круглый воздуховод / труба - ось, середина участка;
      вертикальный участок - ось в нижнем конце;
      оборудование, фасонные части и пр. - нижняя точка элемента."""
    view = doc.ActiveView
    dmap = disp_map(doc, view)
    n, skipped, errs = 0, [], []
    t = Transaction(doc, u"Отметка на взрыв-схеме (выбранного элемента)")
    t.Start()
    try:
        for el in elems:
            c = _curve(el)
            role = None
            if c is not None:
                role = auto_role(el)
                if role is None:          # вертикальный/крутой участок - нижний конец
                    a, b = c.GetEndPoint(0), c.GetEndPoint(1)
                    p = a if a.Z <= b.Z else b
                    role = 'axis'
                else:
                    p = c.Evaluate(0.5, True)
            else:
                p = _bottom_point_fi(el)
            if p is None:
                skipped.append(el)
                continue
            st = SubTransaction(doc)
            st.Start()
            try:
                place(doc, view, el, None, p, side, None, dmap, role)
                st.Commit()
                n += 1
            except Exception as ex:
                st.RollBack()
                errs.append(u"{}: {}".format(_iv(el.Id), ex))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    if alert and (errs or skipped):
        msg = u"Поставлено отметок: {}".format(n)
        if skipped:
            msg += u"\nНе найдена нижняя точка: " + u", ".join(str(_iv(e.Id)) for e in skipped)
        if errs:
            msg += u"\n\n" + u"\n".join(errs[:10])
        alert(msg)
    return n
