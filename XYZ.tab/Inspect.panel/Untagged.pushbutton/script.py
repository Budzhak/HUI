# -*- coding: utf-8 -*-
"""Неподписанные участки труб на активном виде.

Участок — трубы одного диаметра, связанные через трубы и соединительные детали /
арматуру с двумя соединителями того же диаметра (колено, муфта), от точки
подключения до точки подключения (тройник, разветвитель, оборудование, переход).
Подписан — если на виде есть марка на любой его трубе (марки-отметки
«…Отметка…» не считаются). Окно: список участков; «Показать» — выделить все
трубы участка и приблизить; «◀ Назад» / «Вперёд ▶» — соседний участок.
Модель не меняется (только выделение)."""
import sys
from pyrevit import revit
import xyz_forms as forms
from Autodesk.Revit.DB import (FilteredElementCollector, IndependentTag, ElementId, ConnectorType,
                               BuiltInParameter, BuiltInCategory, Domain, MEPCurve)
from Autodesk.Revit.DB.Plumbing import Pipe
from System.Collections.Generic import List
from System.Windows import Thickness, Visibility, ResizeMode, TextWrapping
from System.Windows.Controls import ListBox, Button, TextBlock, Border, ScrollBarVisibility
from System.Windows.Forms import Screen

TITLE = u'Неподписанные участки'
FT_MM = 304.8
doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _cat(el):
    try:
        return _iv(el.Category.Id)
    except Exception:
        return None


C_PIPE = int(BuiltInCategory.OST_PipeCurves)
C_PFIT = int(BuiltInCategory.OST_PipeFitting)
C_PACC = int(BuiltInCategory.OST_PipeAccessory)
C_PINS = int(BuiltInCategory.OST_PipeInsulations)


def _conns(el):
    try:
        cm = el.ConnectorManager if isinstance(el, MEPCurve) else el.MEPModel.ConnectorManager
    except Exception:
        cm = None
    if cm is None:
        return []
    return [c for c in cm.Connectors if c.ConnectorType == ConnectorType.End and c.Domain == Domain.DomainPiping]


def _next(c):
    for r in c.AllRefs:
        o = r.Owner
        if o is None or o.Id == c.Owner.Id or o.Category is None:
            continue
        if _cat(o) == C_PINS or r.ConnectorType != ConnectorType.End:
            continue
        return r
    return None


def _tname(el):
    try:
        t = doc.GetElement(el.GetTypeId())
        from Autodesk.Revit.DB import Element
        return Element.Name.GetValue(t) if t is not None else el.Name
    except Exception:
        return u'?'


def _sysabbr(p):
    for bip in (BuiltInParameter.RBS_DUCT_PIPE_SYSTEM_ABBREVIATION_PARAM, BuiltInParameter.RBS_SYSTEM_NAME_PARAM):
        try:
            s = p.get_Parameter(bip).AsString()
            if s:
                return s
        except Exception:
            pass
    return u'—'


# ---------------------------------------------------------------- данные
pipes = [p for p in FilteredElementCollector(doc, view.Id).OfClass(Pipe)]
if not pipes:
    forms.alert(u'На активном виде нет труб.', title=TITLE, exitscript=True)
vis = dict((_iv(p.Id), p) for p in pipes)

tagged = set()
for t in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
    try:
        sym = doc.GetElement(t.GetTypeId())
        if sym is not None and u'Отметка' in (sym.FamilyName or u''):
            continue
        for i in t.GetTaggedLocalElementIds():
            tagged.add(_iv(i))
    except Exception:
        pass

done, runs = set(), []
for p0 in pipes:
    if _iv(p0.Id) in done:
        continue
    run, ends, stack, seen = [], [], [p0], set([_iv(p0.Id)])
    done.add(_iv(p0.Id))
    while stack:
        e = stack.pop()
        if isinstance(e, Pipe):
            run.append(e)
        for c in _conns(e):
            r = _next(c)
            if r is None:
                if isinstance(e, Pipe):
                    ends.append(u'свободный конец')
                continue
            o = r.Owner
            io = _iv(o.Id)
            if io in seen:
                continue
            ok = False
            if isinstance(o, Pipe):
                ok = io in vis and abs(o.Diameter - p0.Diameter) < 1e-6
            elif _cat(o) in (C_PFIT, C_PACC):
                cs = _conns(o)
                ok = len(cs) == 2 and abs(cs[0].Radius - cs[1].Radius) < 1e-6 and \
                    abs(cs[0].Radius * 2 - p0.Diameter) < 1e-3
            seen.add(io)
            if ok:
                stack.append(o)
                if isinstance(o, Pipe):
                    done.add(io)
            else:
                ends.append(u'%s #%d' % (_tname(o), io))
    if any(_iv(x.Id) in tagged for x in run):
        continue
    L = sum(x.Location.Curve.Length for x in run) * FT_MM
    runs.append({'pipes': run, 'd': p0.Diameter * FT_MM, 'L': L, 'sys': _sysabbr(p0),
                 'ends': ends})

if not runs:
    forms.alert(u'Все участки труб на виде «%s» подписаны.' % view.Name, title=TITLE, exitscript=True)

runs.sort(key=lambda r: (r['sys'], -r['d'], -r['L']))


def _label(k, r):
    ends = u'; '.join(r['ends'][:3]) or u'—'
    return u'%d.  %s  Ø%s  ·  %d мм  ·  кусков %d  ·  концы: %s' % (
        k + 1, r['sys'], (u'%.2f' % r['d']).rstrip('0').rstrip('.'), int(round(r['L'])), len(r['pipes']), ends)


# ---------------------------------------------------------------- окно
dlg = forms._Dialog(TITLE, width=860)
dlg.ResizeMode = ResizeMode.CanResize
dlg._s.Text = u'вид «%s» · участков без марки: %d из %d труб на виде' % (view.Name, len(runs), len(pipes))
dlg._s.Visibility = Visibility.Visible
hint = TextBlock()
hint.Text = (u'Участок — труба одного диаметра от точки подключения до точки подключения (колена и муфты его не делят). '
             u'«Показать» (или двойной щелчок) — выделить весь участок и приблизить. Выделение остаётся после закрытия окна.')
hint.TextWrapping = TextWrapping.Wrap
hint.Foreground = forms._brush(forms.MUTE)
hint.FontSize = 11.5
hint.Margin = Thickness(0, 0, 0, 8)
dlg.body.Children.Add(hint)
lb = ListBox()
lb.MaxHeight = max(240, Screen.PrimaryScreen.WorkingArea.Height - 360)
lb.MinHeight = 200
for k, r in enumerate(runs):
    lb.Items.Add(_label(k, r))
fr = Border()
fr.BorderBrush = forms._brush(forms.LINE)
fr.BorderThickness = Thickness(1)
fr.Child = lb
dlg.body.Children.Add(fr)


def show(k):
    if k < 0 or k >= len(runs):
        return
    ids = List[ElementId]([p.Id for p in runs[k]['pipes']])
    try:
        uidoc.Selection.SetElementIds(ids)
        uidoc.ShowElements(ids)
        uidoc.RefreshActiveView()
    except Exception as ex:
        forms.alert(u'Не удалось показать: %s' % ex, title=TITLE)
    forms.set_subtitle(dlg, u'вид «%s» · участок %d из %d выделен (%d кусков)' % (
        view.Name, k + 1, len(runs), len(runs[k]['pipes'])))


def on_show(s, e):
    show(lb.SelectedIndex if lb.SelectedIndex >= 0 else 0)
    if lb.SelectedIndex < 0:
        lb.SelectedIndex = 0


def step(d):
    k = lb.SelectedIndex + d if lb.SelectedIndex >= 0 else 0
    k = max(0, min(len(runs) - 1, k))
    lb.SelectedIndex = k
    lb.ScrollIntoView(lb.SelectedItem)
    show(k)


lb.MouseDoubleClick += lambda s, e: show(lb.SelectedIndex)
for text, h, prim, canc in ((u'◀ Назад', lambda s, e: step(-1), False, False),
                            (u'Показать', on_show, True, False),
                            (u'Вперёд ▶', lambda s, e: step(1), False, False),
                            (u'Закрыть', lambda s, e: dlg.Close(), False, True)):
    b = Button()
    b.Content = text
    b.Margin = Thickness(8, 0, 0, 0)
    b.MinWidth = 90
    b.IsDefault = prim
    b.IsCancel = canc
    b.Click += h
    dlg.footer.Children.Add(b)
forms.adaptive(dlg, 'untagged')
dlg.ShowDialog()
