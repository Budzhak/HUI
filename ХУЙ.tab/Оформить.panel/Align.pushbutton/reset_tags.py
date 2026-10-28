# -*- coding: utf-8 -*-
"""
Сброс марок (ХУЙ -> По красоте).

Выделенные марки приводятся к одному виду за один раз:
  - тип выноски: присоединённая / со свободным концом / без выноски;
  - сторона марки от точки на элементе: справа / слева / сверху / снизу / как сейчас;
  - длина выноски в мм листа (от масштаба вида не зависит);
  - излом выноски убирается: у свободного конца выноска ровно горизонтальная
    (справа/слева) или ровно вертикальная (сверху/снизу);
  - марка горизонтальная;
  - по желанию (со свободным концом): точку конца выноски указывать щелчком —
    щелчок марки, затем щелчок точки на элементе (выделенные заранее марки —
    сразу точки, по очереди). Конец встаёт на ось трубы/воздуховода под
    щелчком или на элемент в точке щелчка (с учётом разноса).

Revit ведёт выноску в середину ближнего края марки. Поэтому марка ставится
симметрично относительно точки на элементе:
  справа/слева - центр марки на горизонтали точки, ближний край на расстоянии L;
  сверху/снизу - центр марки на вертикали точки, нижний (верхний) край на расстоянии L.
Без выноски: марка ставится рядом с точкой (центр на оси, край на расстоянии L).

Работа: вызвали команду -> окно с прошлыми настройками -> Enter -> щёлкаете марки
по одной, каждая сразу перестраивается (свой шаг Ctrl+Z) -> Esc - готово.
Если марки были выделены до запуска, сначала перестраиваются они.

Вызов: reset_tags.ask(config) -> settings;  reset_tags.pick_loop(doc, uidoc, settings, preselected)
       reset_tags.run(doc, uidoc, tags, settings) - сразу для списка марок
"""
from Autodesk.Revit.DB import *

FT_MM = 304.8

LEADERS = [('attached', u"Присоединённая"),
           ('free', u"Со свободным концом"),
           ('none', u"Без выноски")]
SIDES = [('right', u"Справа"), ('left', u"Слева"), ('up', u"Сверху"),
         ('down', u"Снизу"), ('keep', u"Как сейчас")]

DEFAULTS = {'leader': 'free', 'side': 'keep', 'length': 8.0,
            'reset_end': False, 'horizontal': True, 'pick_end': False}


def _idv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _first_ref(tag):
    for r in tag.GetTaggedReferences():
        return r
    return None


# ------------------------------------------------------------------ окно
def ask(config=None):
    """Одно окно со всеми настройками. Возвращает dict или None (Отмена)."""
    import hui_forms
    from System.Windows import Thickness
    from System.Windows.Controls import (GroupBox, StackPanel, RadioButton, TextBox, CheckBox,
                                         TextBlock, Orientation, WrapPanel)

    st = dict(DEFAULTS)
    if config is not None:
        try:
            st['leader'] = config.get_option('reset_leader', st['leader'])
            st['side'] = config.get_option('reset_side', st['side'])
            st['length'] = float(config.get_option('reset_length', str(st['length'])))
            st['reset_end'] = config.get_option('reset_end', u'0') == u'1'
            st['horizontal'] = config.get_option('reset_horizontal', u'1') == u'1'
            st['pick_end'] = config.get_option('reset_pick_end', u'0') == u'1'
        except Exception:
            pass

    dlg = hui_forms._Dialog(u"Сброс марок", width=460)
    dlg._s.Text = u"Тип выноски, сторона и длина — для всех выделенных марок"
    dlg._s.Visibility = hui_forms.Visibility.Visible

    def group(title):
        g = GroupBox()
        g.Header = title
        sp = StackPanel()
        g.Content = sp
        dlg.body.Children.Add(g)
        return sp

    sp = group(u"Выноска")
    leader_rb = {}
    for key, text in LEADERS:
        rb = RadioButton()
        rb.Content = text
        rb.GroupName = 'leader'
        rb.Margin = Thickness(0, 0, 0, 6)
        rb.IsChecked = (key == st['leader'])
        sp.Children.Add(rb)
        leader_rb[key] = rb

    sp = group(u"Где марка относительно точки на элементе")
    wp = WrapPanel()
    sp.Children.Add(wp)
    side_rb = {}
    for key, text in SIDES:
        rb = RadioButton()
        rb.Content = text
        rb.GroupName = 'side'
        rb.Margin = Thickness(0, 0, 18, 6)
        rb.IsChecked = (key == st['side'])
        wp.Children.Add(rb)
        side_rb[key] = rb
    hint = TextBlock()
    hint.Text = (u"Со свободным концом выноска будет ровно горизонтальной (справа, слева) "
                 u"или ровно вертикальной (сверху, снизу).")
    hint.TextWrapping = hui_forms.TextWrapping.Wrap
    hint.FontSize = 11
    hint.Foreground = hui_forms._brush(hui_forms.MUTE)
    sp.Children.Add(hint)

    sp = group(u"Длина выноски, мм на листе")
    tb = TextBox()
    tb.Text = (u"%g" % st['length'])
    tb.MinHeight = 32
    sp.Children.Add(tb)
    hint2 = TextBlock()
    hint2.Text = u"От точки на элементе до края марки. Без выноски — зазор между точкой и маркой."
    hint2.TextWrapping = hui_forms.TextWrapping.Wrap
    hint2.FontSize = 11
    hint2.Margin = Thickness(0, 4, 0, 0)
    hint2.Foreground = hui_forms._brush(hui_forms.MUTE)
    sp.Children.Add(hint2)

    sp = group(u"Дополнительно")
    cb_pick = CheckBox()
    cb_pick.Content = u"Конец выноски — указать щелчком"
    cb_pick.IsChecked = st['pick_end']
    cb_pick.Margin = Thickness(0, 0, 0, 4)
    sp.Children.Add(cb_pick)
    hint3 = TextBlock()
    hint3.Text = (u"Только «Со свободным концом». Щелчок по марке, затем по точке на элементе; "
                  u"выделенным заранее маркам — сразу точки, по очереди.")
    hint3.TextWrapping = hui_forms.TextWrapping.Wrap
    hint3.FontSize = 11
    hint3.Foreground = hui_forms._brush(hui_forms.MUTE)
    sp.Children.Add(hint3)

    warn = TextBlock()
    warn.Foreground = hui_forms._brush(hui_forms.RED)
    warn.FontSize = 11
    dlg.body.Children.Add(warn)

    dlg.add_button(u"Отмена", None, cancel=True)
    okb = dlg.add_button(u"Указать марки", '__ok__', primary=True)
    note = TextBlock()
    note.Text = (u"Enter — дальше щёлкайте марки по одной, каждая сразу перестраивается. "
                 u"Esc — закончить.")
    note.TextWrapping = hui_forms.TextWrapping.Wrap
    note.FontSize = 11
    note.Foreground = hui_forms._brush(hui_forms.MUTE)
    dlg.body.Children.Add(note)

    def parse():
        try:
            v = float(tb.Text.replace(u',', u'.'))
            return v if 0 <= v <= 200 else None
        except Exception:
            return None

    def changed(s, e):
        ok = parse() is not None
        okb.IsEnabled = ok
        warn.Text = u"" if ok else u"Длина — число от 0 до 200 мм"
    tb.TextChanged += changed

    def leader_changed(s, e):
        cb_pick.IsEnabled = bool(leader_rb['free'].IsChecked)
    for rb in leader_rb.values():
        rb.Checked += leader_changed
    leader_changed(None, None)

    dlg.ShowDialog()
    if dlg.response != '__ok__':
        return None
    res = {
        'leader': [k for k, rb in leader_rb.items() if rb.IsChecked][0],
        'side': [k for k, rb in side_rb.items() if rb.IsChecked][0],
        'length': parse(),
        'reset_end': st['reset_end'],
        'horizontal': st['horizontal'],
        'pick_end': bool(cb_pick.IsChecked),
    }
    if res['leader'] != 'free':
        res['pick_end'] = False
    if config is not None:
        config.reset_leader = res['leader']
        config.reset_side = res['side']
        config.reset_length = str(res['length'])
        config.reset_end = u'1' if res['reset_end'] else u'0'
        config.reset_horizontal = u'1' if res['horizontal'] else u'0'
        config.reset_pick_end = u'1' if res['pick_end'] else u'0'
    return res


# ------------------------------------------------------------------ расчёт
def head_for(side, p_u, p_v, rel, L, has_leader, cur_u=None):
    """Положение TagHeadPosition (u, v в осях вида) для марки с габаритом rel
    (u0, v0, u1, v1 относительно TagHeadPosition)."""
    u0, v0, u1, v1 = rel
    cu, cv = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    if side == 'right':
        return p_u + L - u0, p_v - cv
    if side == 'left':
        return p_u - L - u1, p_v - cv
    # сверху/снизу: марка по центру над (под) точкой - Revit ведёт выноску
    # в середину нижнего (верхнего) края марки, линия получается вертикальной
    hu = p_u - cu
    if side == 'up':
        return hu, p_v + L - v0
    return hu, p_v - L - v1


def _is_elev_mark(doc, tag):
    """Марки-отметки взрыв-схемы ведёт свой обработчик (elev_marks) - их не трогаем."""
    try:
        sym = doc.GetElement(tag.GetTypeId())
        return u'Отметка_Схемы' in (sym.FamilyName or u'')
    except Exception:
        return False


def _side_now(R, U, head, p):
    d = head - p
    du, dv = d.DotProduct(R), d.DotProduct(U)
    if abs(du) >= abs(dv):
        return 'right' if du >= 0 else 'left'
    return 'up' if dv >= 0 else 'down'


def run(doc, uidoc, tags, s, ends=None):
    """ends: {id марки: точка конца выноски} — указанные щелчком (только «свободный конец»)."""
    import stack_tags
    view = doc.ActiveView
    R, U = view.RightDirection, view.UpDirection
    mm = view.Scale / FT_MM
    L = s['length'] * mm
    mode = s['leader']

    tg = TransactionGroup(doc, u"Сброс марок")
    tg.Start()
    try:
        # 1. Точки на элементах, ориентация, выноска без излома
        t = Transaction(doc, u"Сброс марок: подготовка")
        t.Start()
        items, skipped = [], 0
        for tag in tags:
            ref = _first_ref(tag)
            if ref is None or _is_elev_mark(doc, tag):
                skipped += 1
                continue
            try:
                old_head = tag.TagHeadPosition
                if s.get('horizontal'):
                    try:
                        if tag.TagOrientation != TagOrientation.Horizontal:
                            tag.TagOrientation = TagOrientation.Horizontal
                    except Exception:
                        pass
                keep_end = (mode == 'free' and tag.HasLeader and not s.get('reset_end')
                            and tag.LeaderEndCondition == LeaderEndCondition.Free)
                p = tag.GetLeaderEnd(ref) if keep_end else None
                if ends and mode == 'free' and _idv(tag.Id) in ends:
                    p = ends[_idv(tag.Id)]
                # снять и вернуть выноску - Revit убирает излом
                if tag.HasLeader:
                    tag.HasLeader = False
                    doc.Regenerate()
                tag.HasLeader = True
                tag.LeaderEndCondition = LeaderEndCondition.Free
                doc.Regenerate()
                if p is None:
                    p = tag.GetLeaderEnd(ref)    # точка по умолчанию (как у присоединённой)
                else:
                    tag.SetLeaderEnd(ref, p)
                items.append((tag, ref, p, old_head))
            except Exception:
                skipped += 1
        t.Commit()
        if not items:
            tg.RollBack()
            return u"Нет марок, которые можно сбросить."

        # 2. Точный габарит голов марок (транзакция откатывается)
        rels = stack_tags.measure_exact(doc, view, [it[0] for it in items], R, U, mm)

        # 3. Расстановка
        t = Transaction(doc, u"Сброс марок")
        t.Start()
        for (tag, ref, p, old_head), rel in zip(items, rels):
            if rel is None:
                rel = (-6 * mm, -2.5 * mm, 6 * mm, 2.5 * mm)
            side = s['side'] if s['side'] != 'keep' else _side_now(R, U, old_head, p)
            pu, pv = p.DotProduct(R), p.DotProduct(U)
            hu, hv = head_for(side, pu, pv, rel, L, mode != 'none', old_head.DotProduct(R))
            head = p + R * (hu - pu) + U * (hv - pv)
            if mode == 'none':
                tag.HasLeader = False
            elif mode == 'attached':
                tag.LeaderEndCondition = LeaderEndCondition.Attached
            else:
                tag.SetLeaderEnd(ref, p)
            tag.TagHeadPosition = head
        t.Commit()
        tg.Assimilate()
    except Exception:
        try:
            if tg.HasStarted() and not tg.HasEnded():
                tg.RollBack()
        except Exception:
            pass
        raise
    msg = u"Сброшено марок: {}".format(len(items))
    if skipped:
        msg += u"\nПропущено (отметки взрыв-схемы, без элемента, ошибка): {}".format(skipped)
    return msg


# ------------------------------------------------------------------ прокликивание
def _make_filter():
    from Autodesk.Revit.UI.Selection import ISelectionFilter

    class TagFilter(ISelectionFilter):
        def AllowElement(self, el):
            return isinstance(el, IndependentTag)

        def AllowReference(self, ref, pt):
            return False
    return TagFilter()


def _disp(doc, view, el):
    try:
        for de in FilteredElementCollector(doc, view.Id).OfClass(DisplacementElement):
            for i in de.GetDisplacedElementIds():
                if i == el.Id:
                    return de.GetAbsoluteDisplacement()
    except Exception:
        pass
    return XYZ.Zero


def snap_to_element(doc, view, tag, pick):
    """Точка щелчка -> точка на элементе марки (на экране — там же, где щёлкнули).
    Труба/воздуховод: ось под щелчком; прочее: глубина центра элемента.
    Взрыв-схема: с учётом смещения разноса (конец выноски — в смещённом месте)."""
    ref = _first_ref(tag)
    el = doc.GetElement(ref.ElementId) if ref is not None else None
    if el is None:
        return pick
    R, U, V = view.RightDirection, view.UpDirection, view.ViewDirection
    d = _disp(doc, view, el)
    loc = el.Location
    if isinstance(loc, LocationCurve) and isinstance(loc.Curve, Line):
        p0, p1 = loc.Curve.GetEndPoint(0) + d, loc.Curve.GetEndPoint(1) + d
        a = (p0.DotProduct(R), p0.DotProduct(U))
        b = (p1.DotProduct(R), p1.DotProduct(U))
        e = (pick.DotProduct(R), pick.DotProduct(U))
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        t = 0.5 if L2 < 1e-12 else ((e[0] - a[0]) * dx + (e[1] - a[1]) * dy) / L2
        t = min(max(t, 0.0), 1.0)
        return p0 + (p1 - p0) * t
    if isinstance(loc, LocationPoint):
        c = loc.Point + d
    else:
        bb = el.get_BoundingBox(None)
        c = ((bb.Min + bb.Max) * 0.5 + d) if bb is not None else pick
    # тот же пиксель на экране, глубина — центра элемента
    return pick + V * (c - pick).DotProduct(V)


def _pick_point(uidoc, msg):
    from Autodesk.Revit.Exceptions import OperationCanceledException
    try:
        return uidoc.Selection.PickPoint(msg)
    except OperationCanceledException:
        return None


def pick_loop(doc, uidoc, s, preselected=None):
    if s.get('pick_end') and s.get('leader') == 'free':
        return pick_loop_ends(doc, uidoc, s, preselected)
    return _pick_loop_plain(doc, uidoc, s, preselected)


def pick_loop_ends(doc, uidoc, s, preselected=None):
    """Режим «Конец выноски — указать щелчком»: марка -> точка -> марка -> точка…
    Выделенным заранее маркам — сразу точки по очереди. Esc — готово."""
    from Autodesk.Revit.UI.Selection import ObjectType
    from Autodesk.Revit.Exceptions import OperationCanceledException
    import sys
    sys.modules.pop('hui_plane', None)
    import hui_plane
    view = doc.ActiveView
    tok = None
    if isinstance(view, View3D):
        try:
            tok = hui_plane.push(doc, view, view.ViewDirection, view.Origin,
                                 u"Сброс марок: временная рабочая плоскость")
        except Exception:
            tok = None
    done = 0
    try:
        queue = list(preselected or [])
        n0 = len(queue)
        for k, tag in enumerate(queue):
            try:
                uidoc.Selection.SetElementIds(List_ids([tag.Id]))
            except Exception:
                pass
            pt = _pick_point(uidoc, u"Сброс марок: точка конца выноски для выделенной марки {} из {} (Esc - готово)"
                             .format(k + 1, n0))
            if pt is None:
                return done
            msg = run(doc, uidoc, [tag], s, {_idv(tag.Id): snap_to_element(doc, view, tag, pt)})
            if msg and msg.startswith(u"Сброшено"):
                done += 1
        try:
            uidoc.Selection.SetElementIds(List_ids([]))
        except Exception:
            pass
        flt = _make_filter()
        while True:
            try:
                ref = uidoc.Selection.PickObject(
                    ObjectType.Element, flt,
                    u"Сброс марок: щёлкните марку (перестроено {}; Esc - готово)".format(done))
            except OperationCanceledException:
                break
            tag = doc.GetElement(ref.ElementId)
            if not isinstance(tag, IndependentTag):
                continue
            pt = _pick_point(uidoc, u"Сброс марок: щёлкните точку конца выноски на элементе (Esc - готово)")
            if pt is None:
                break
            msg = run(doc, uidoc, [tag], s, {_idv(tag.Id): snap_to_element(doc, view, tag, pt)})
            if msg and msg.startswith(u"Сброшено"):
                done += 1
            try:
                uidoc.RefreshActiveView()
            except Exception:
                pass
    finally:
        if tok is not None:
            hui_plane.pop(doc, view, tok)
    return done


def _pick_loop_plain(doc, uidoc, s, preselected=None):
    """Сначала выделенные заранее марки, потом марки по щелчку, пока не нажат Esc.
    Каждая марка - отдельный шаг отмены. Возвращает число перестроенных марок."""
    from Autodesk.Revit.UI.Selection import ObjectType
    from Autodesk.Revit.Exceptions import OperationCanceledException
    done = 0
    if preselected:
        run(doc, uidoc, preselected, s)
        done += len(preselected)
        try:
            uidoc.Selection.SetElementIds(List_ids([]))
        except Exception:
            pass
    flt = _make_filter()
    prompt = u"Сброс марок: щёлкните марку (Esc - готово)"
    while True:
        try:
            ref = uidoc.Selection.PickObject(ObjectType.Element, flt, prompt)
        except OperationCanceledException:
            break
        tag = doc.GetElement(ref.ElementId)
        if not isinstance(tag, IndependentTag):
            continue
        msg = run(doc, uidoc, [tag], s)
        if msg and msg.startswith(u"Сброшено"):
            done += 1
        try:
            uidoc.RefreshActiveView()
        except Exception:
            pass
        prompt = u"Сброс марок: перестроено {} — следующая марка (Esc - готово)".format(done)
    return done


def List_ids(ids):
    from System.Collections.Generic import List
    lst = List[ElementId]()
    for i in ids:
        lst.Add(i)
    return lst
