# -*- coding: utf-8 -*-
"""
«Конец выноски — в центр элемента» — команда кнопки «По красоте».

У выделенных марок с выноской «Со свободным концом» конец выноски
переносится в центр элемента, на который указывает марка:
  - труба, воздуховод, лоток, короб — в середину участка (на оси);
  - фитинг, арматура и прочие семейства с точкой вставки — в эту точку;
  - остальное — в центр габарита элемента.
Работает и на взрыв-схемах: учитывается смещение набора разноса.
Голова марки и излом выноски не двигаются.

Вызов из script.py:   leader_snap.run(doc, uidoc, tags)
Ядро (нужна открытая транзакция): snap_tags(doc, view, tags) -> (n, skipped)
"""
from Autodesk.Revit.DB import (
    DisplacementElement, ElementId, FilteredElementCollector, LeaderEndCondition,
    LocationCurve, LocationPoint, Line, Transaction, XYZ,
)



def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _disp_map(doc, view):
    out = {}
    try:
        for de in FilteredElementCollector(doc, view.Id).OfClass(DisplacementElement):
            d = de.GetAbsoluteDisplacement()
            for i in de.GetDisplacedElementIds():
                out[_iv(i)] = d
    except Exception:
        pass
    return out


def _center(view, el, end, d):
    """Точка центра элемента (в положении на виде, т.е. со смещением d)."""
    R, U = view.RightDirection, view.UpDirection

    def uv(p):
        return (p.DotProduct(R), p.DotProduct(U))
    loc = el.Location
    if isinstance(loc, LocationCurve):
        # середина участка (не ближайшая точка: конец выноски у тройника так и
        # оставался у тройника — инженер ждёт центр участка)
        return loc.Curve.Evaluate(0.5, True) + d
    if isinstance(loc, LocationPoint):
        return loc.Point + d
    bb = el.get_BoundingBox(None)
    if bb is None:
        return None
    return (bb.Min + bb.Max) * 0.5 + d


def snap_tags(doc, view, tags):
    dmap = _disp_map(doc, view)
    n, skipped = 0, []
    for t in tags:
        try:
            if not t.HasLeader or t.LeaderEndCondition != LeaderEndCondition.Free:
                skipped.append((t, u"выноска не «Со свободным концом»"))
                continue
        except Exception:
            skipped.append((t, u"нет выноски"))
            continue
        moved = False
        for r in t.GetTaggedReferences():
            try:
                if _iv(r.LinkedElementId) > 0:
                    continue                      # элемент связи — не трогаем
            except Exception:
                pass
            el = doc.GetElement(r.ElementId)
            if el is None:
                continue
            try:
                end = t.GetLeaderEnd(r)
            except Exception:
                continue
            p = _center(view, el, end, dmap.get(_iv(el.Id), XYZ.Zero))
            if p is None:
                continue
            t.SetLeaderEnd(r, p)
            moved = True
        if moved:
            n += 1
        else:
            skipped.append((t, u"не удалось найти центр элемента"))
    return n, skipped


def run(doc, uidoc, tags):
    import xyz_forms as forms
    view = doc.ActiveView
    if not tags:
        forms.alert(u"Выделите марки с выноской «Со свободным концом».",
                    title=u"Конец выноски — в центр элемента")
        return
    t = Transaction(doc, u"Конец выноски — в центр элемента")
    t.Start()
    try:
        n, skipped = snap_tags(doc, view, tags)
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    msg = u"Перенесено концов выносок: {} из {}.".format(n, len(tags))
    if skipped:
        why = {}
        for _, w in skipped:
            why[w] = why.get(w, 0) + 1
        msg += u"\n\nПропущено:\n" + u"\n".join(u"  {} — {}".format(k, v) for k, v in why.items())
        try:
            from System.Collections.Generic import List
            uidoc.Selection.SetElementIds(List[ElementId]([x.Id for x, _ in skipped]))
            msg += u"\n(пропущенные марки выделены)"
        except Exception:
            pass
    forms.alert(msg + u"\n\nОтменить — Ctrl+Z.", title=u"Конец выноски — в центр элемента")
