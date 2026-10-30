# -*- coding: utf-8 -*-
"""
«Выноски — равный шаг вдоль трубы» — команда кнопки «По красоте».

Марки остаются на месте; двигаются только концы выносок — ВДОЛЬ оси своего
элемента (трубы, воздуховода), так что вертикальные участки выносок
(от излома вниз к элементу) встают с одинаковым шагом и остаются
вертикальными: излом сдвигается по горизонтали вслед за концом.
  - «Равный шаг между крайними»: крайние выноски на месте, средние — поровну;
  - «Шаг N мм на листе»: выноски разносятся (или сводятся) вокруг их середины.
Порядок выносок слева направо сохраняется (новых перехлёстов не появляется).
После первой группы — рамкой следующие группы с тем же режимом, пока не Esc.
У разветвителя/фитинга конец едет вдоль прямого прохода (вход — основной выход);
у оборудования двигать некуда — конец остаётся на месте.
Работает на взрыв-схеме (с учётом смещения разноса).

Вызов из script.py:   leader_spread.run(doc, uidoc, tags, config)
Ядро (нужна открытая транзакция): spread(doc, view, tags, step_mm=None)
"""
from Autodesk.Revit.DB import (
    DisplacementElement, ElementId, FilteredElementCollector, IndependentTag, LeaderEndCondition,
    LocationCurve, Line, Transaction, XYZ,
)

TITLE = u"Выноски — равный шаг вдоль трубы"


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


def _axis(el):
    """Отрезок, вдоль которого можно двигать конец выноски: ось трубы/воздуховода;
    у фитинга/арматуры (разветвитель, муфта) — прямой проход между двумя
    соосными соединителями (вход — основной выход)."""
    loc = el.Location
    if isinstance(loc, LocationCurve) and isinstance(loc.Curve, Line):
        return loc.Curve.GetEndPoint(0), loc.Curve.GetEndPoint(1)
    try:
        cs = [c for c in el.MEPModel.ConnectorManager.Connectors]
    except Exception:
        return None
    best = None
    for i in range(len(cs)):
        for j in range(i + 1, len(cs)):
            a, b = cs[i], cs[j]
            try:
                if a.CoordinateSystem.BasisZ.DotProduct(b.CoordinateSystem.BasisZ) > -0.95:
                    continue
                L = a.Origin.DistanceTo(b.Origin)
            except Exception:
                continue
            if best is None or L > best[0]:
                best = (L, a.Origin, b.Origin)
    return (best[1], best[2]) if best is not None and best[0] > 1e-3 else None


def _items(doc, view, tags):
    R = view.RightDirection
    dmap = _disp_map(doc, view)
    items, skipped = [], []
    for t in tags:
        try:
            if not t.HasLeader or t.LeaderEndCondition != LeaderEndCondition.Free:
                skipped.append((t, u"выноска не «Со свободным концом»"))
                continue
        except Exception:
            skipped.append((t, u"нет выноски"))
            continue
        ref = None
        for r in t.GetTaggedReferences():
            ref = r
            break
        el = doc.GetElement(ref.ElementId) if ref is not None else None
        if el is None:
            skipped.append((t, u"нет элемента"))
            continue
        end = t.GetLeaderEnd(ref)
        it = {'tag': t, 'ref': ref, 'end': end, 'u': end.DotProduct(R), 'p0': None, 'p1': None}
        d = dmap.get(_iv(el.Id), XYZ.Zero)
        seg = _axis(el)
        if seg is not None:
            p0, p1 = seg[0] + d, seg[1] + d
            if abs((p1 - p0).DotProduct(R)) * 304.8 / view.Scale > 0.05:   # не «в торец» на экране
                it['p0'], it['p1'] = p0, p1
        try:
            it['elbow'] = t.GetLeaderElbow(ref) if t.HasLeaderElbow(ref) else None
        except Exception:
            it['elbow'] = None
        items.append(it)
    return items, skipped


def spread(doc, view, tags, step_mm=None):
    """step_mm=None — равный шаг между крайними; иначе шаг на листе вокруг середины.
    Возвращает (перемещено, [(марка, причина)])."""
    R = view.RightDirection
    mm = view.Scale / 304.8
    items, skipped = _items(doc, view, tags)
    if len(items) < 2:
        return 0, skipped + [(it['tag'], u"нужно хотя бы две марки") for it in items]
    items.sort(key=lambda x: x['u'])
    n = len(items)
    if step_mm is None:
        u0, u1 = items[0]['u'], items[-1]['u']
        targets = [u0 + (u1 - u0) * k / (n - 1.0) for k in range(n)]
    else:
        c = sum(x['u'] for x in items) / float(n)
        s = step_mm * mm
        targets = [c + (k - (n - 1) / 2.0) * s for k in range(n)]
    moved = 0
    for it, ut in zip(items, targets):
        t, ref = it['tag'], it['ref']
        if it['p0'] is None:
            if abs(ut - it['u']) > 0.05 * mm:
                skipped.append((t, u"конец не на трубе/фитинге — остался на месте"))
            continue
        p0, p1 = it['p0'], it['p1']
        a, b = p0.DotProduct(R), p1.DotProduct(R)
        k = (ut - a) / (b - a)
        if k < 0.0 or k > 1.0:
            skipped.append((t, u"элемент короче нужного сдвига — конец у его края"))
            k = min(max(k, 0.0), 1.0)
        new_end = p0 + (p1 - p0) * k
        t.SetLeaderEnd(ref, new_end)
        if it['elbow'] is not None:
            e = it['elbow']
            try:
                t.SetLeaderElbow(ref, e + R * (new_end.DotProduct(R) - e.DotProduct(R)))
            except Exception:
                pass
        moved += 1
    return moved, skipped


def _tag_filter():
    from Autodesk.Revit.UI.Selection import ISelectionFilter

    class TagFilter(ISelectionFilter):
        def AllowElement(self, el):
            return isinstance(el, IndependentTag)

        def AllowReference(self, ref, pt):
            return False
    return TagFilter()


def run(doc, uidoc, tags, config=None):
    import xyz_forms as forms
    view = doc.ActiveView
    if not tags or len(tags) < 2:
        forms.alert(u"Выделите две и больше марок с выноской «Со свободным концом».", title=TITLE)
        return
    A_EQ = u"Равный шаг между крайними (крайние на месте)"
    A_STEP = u"Задать шаг, мм на листе (разнести или свести)…"
    ch = forms.CommandSwitchWindow.show([A_EQ, A_STEP], message=TITLE)
    if not ch:
        return
    step = None
    if ch == A_STEP:
        last = u"4"
        try:
            last = config.get_option('spread_step', u"4") if config is not None else u"4"
        except Exception:
            pass
        s = forms.ask_for_string(default=last, prompt=u"Шаг между вертикальными выносками, мм на листе",
                                 title=TITLE)
        if s is None:
            return
        try:
            step = float(s.replace(u",", u"."))
            if step <= 0:
                raise ValueError
        except ValueError:
            forms.alert(u"Нужно положительное число, например 3 или 4.5.", title=TITLE)
            return
        if config is not None:
            config.spread_step = s
    mode = u"шаг {} мм".format(s) if step is not None else u"равный шаг между крайними"
    rounds, total, moved_all, skipped_all = 0, 0, 0, []

    def apply(group):
        t = Transaction(doc, TITLE)
        t.Start()
        try:
            n, sk = spread(doc, view, group, step)
            t.Commit()
        except Exception:
            t.RollBack()
            raise
        return n, sk

    # 1) выделенные заранее
    n, sk = apply(tags)
    rounds, total, moved_all = 1, len(tags), n
    skipped_all.extend(sk)
    # 2) дальше — рамкой следующие группы с тем же режимом, пока не Esc
    from Autodesk.Revit.Exceptions import OperationCanceledException
    flt = _tag_filter()
    while True:
        try:
            uidoc.RefreshActiveView()
        except Exception:
            pass
        try:
            picked = uidoc.Selection.PickElementsByRectangle(
                flt, u"{}: рамкой — следующие выноски ({}; групп {}). Esc — готово".format(TITLE, mode, rounds))
        except OperationCanceledException:
            break
        group = [e for e in picked if isinstance(e, IndependentTag)]
        if len(group) < 2:
            continue
        n, sk = apply(group)
        rounds += 1
        total += len(group)
        moved_all += n
        skipped_all.extend(sk)
    msg = u"Групп: {}. Сдвинуто концов выносок: {} из {}.".format(rounds, moved_all, total)
    if skipped_all:
        why = {}
        for _, w in skipped_all:
            why[w] = why.get(w, 0) + 1
        msg += u"\n\n" + u"\n".join(u"  {} — {}".format(k, v) for k, v in why.items())
        try:
            from System.Collections.Generic import List
            uidoc.Selection.SetElementIds(List[ElementId]([x.Id for x, _ in skipped_all]))
            msg += u"\n(эти марки выделены)"
        except Exception:
            pass
    forms.alert(msg + u"\n\nКаждая группа — свой шаг Ctrl+Z.", title=TITLE)
