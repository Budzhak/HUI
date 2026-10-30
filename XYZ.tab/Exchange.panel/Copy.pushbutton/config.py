# -*- coding: utf-8 -*-
"""Под копирку, Shift+щелчок: ширины всех граф активной ведомости (мм) — вписать числами (db 18.10.2026)."""
import sys
sys.modules.pop('xyz_schedfmt', None)
import xyz_schedfmt as sf
import xyz_forms as forms
from Autodesk.Revit.DB import Transaction, ViewSchedule

doc = __revit__.ActiveUIDocument.Document
vs = doc.ActiveView
TITLE = u'Под копирку — ширины граф'


def num(s):
    try:
        return float(s.replace(u',', u'.').strip())
    except Exception:
        return None


def main():
    if not isinstance(vs, ViewSchedule):
        forms.alert(u'Откройте ведомость и нажмите Shift+щелчок ещё раз.', title=TITLE)
        return
    from System.Windows import Thickness, GridLength, GridUnitType, VerticalAlignment
    from System.Windows.Controls import Grid, ColumnDefinition, RowDefinition, TextBlock, TextBox, ScrollViewer, \
        ScrollBarVisibility
    rows = sf.widths(vs)
    dlg = forms._Dialog(TITLE, width=560)
    dlg._s.Text = u'«%s»' % vs.Name
    from System.Windows import Visibility as _V
    dlg._s.Visibility = _V.Visible
    g = Grid()
    for w in (GridLength(1, GridUnitType.Star), GridLength(90)):
        cd = ColumnDefinition()
        cd.Width = w
        g.ColumnDefinitions.Add(cd)
    boxes = []
    items = [(u'%d. %s' % (i + 1, (h or u'').replace(u'\n', u' ')), w, f) for i, (f, h, w) in enumerate(rows)]
    total = TextBlock()

    def upd(*a):
        s = 0.0
        for tb, f in boxes:
            if f != '__hh__':
                v = num(tb.Text)
                s += v or 0.0
        total.Text = u'Общая ширина: %.1f мм' % s
    for i, (label, val, f) in enumerate(items):
        g.RowDefinitions.Add(RowDefinition())
        lb = TextBlock()
        lb.Text = label
        lb.Margin = Thickness(0, 4, 8, 4)
        lb.VerticalAlignment = VerticalAlignment.Center
        Grid.SetRow(lb, i)
        g.Children.Add(lb)
        tb = TextBox()
        tb.Text = (u'%.1f' % val).rstrip(u'0').rstrip(u'.')
        tb.Margin = Thickness(0, 3, 0, 3)
        tb.TextChanged += upd
        Grid.SetRow(tb, i)
        Grid.SetColumn(tb, 1)
        g.Children.Add(tb)
        boxes.append((tb, f))
    sv = ScrollViewer()
    sv.MaxHeight = 460
    sv.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
    sv.Content = g
    t = dlg.add_text(u'Ширина граф, мм (как на листе). Дальше «Под копирку» разнесёт их на остальные ведомости.',
                     muted=True, size=12)
    t.Margin = Thickness(0, 0, 0, 8)
    dlg.body.Children.Add(sv)
    total.Margin = Thickness(0, 8, 0, 0)
    dlg.body.Children.Add(total)
    upd()
    dlg.add_button(u'Отмена', None, cancel=True)
    dlg.add_button(u'Применить', '__ok__', primary=True)
    dlg.ShowDialog()
    if dlg.response != '__ok__':
        return
    bad = [tb.Text for tb, f in boxes if not num(tb.Text) or num(tb.Text) <= 0]
    if bad:
        forms.alert(u'Не число или не больше нуля: %s. Ничего не изменено.' % u', '.join(bad), title=TITLE)
        return
    t = Transaction(doc, u'Ширины граф: ' + vs.Name)
    t.Start()
    try:
        for tb, f in boxes:
            v = num(tb.Text)
            if abs(f.GridColumnWidth * sf.FT_MM - v) > 0.05:
                f.GridColumnWidth = v / sf.FT_MM
                try:
                    f.SheetColumnWidth = v / sf.FT_MM
                except Exception:
                    pass
        t.Commit()
    except Exception as ex:
        t.RollBack()
        forms.alert(u'Ошибка: %s' % ex, title=TITLE)


main()
