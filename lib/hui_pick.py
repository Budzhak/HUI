# -*- coding: utf-8 -*-
"""Окно выбора групп элементов (Категория | Семейство | Тип) с мини-картинками и
флажками — как в кнопке «Распознать». Для скриптов, где нужно спросить инженера,
к каким объектам применять команду.

    import hui_pick
    ids = hui_pick.pick_groups(doc, ids, u"Высотные отметки",
                               ok_text=u"Поставить отметки",
                               checked=lambda key, el: key[0] == u"Трубы")
    # None — «Отмена», иначе список ElementId отмеченных групп

Щелчок по строке — флажок, Shift+щелчок — диапазон, поиск по тексту,
«Отметить все» / «Снять все» / «Инвертировать выбор» (для найденных строк).
"""
import clr
clr.AddReference('System.Drawing')
clr.AddReference('System.Windows.Forms')
for _a in ('PresentationFramework', 'PresentationCore', 'WindowsBase'):
    try:
        clr.AddReference(_a)
    except Exception:
        pass
import System
from System.Drawing import Size as DSize
from System.Drawing.Imaging import ImageFormat
from System.IO import MemoryStream
from System.Windows.Forms import Screen
from System.Windows import (Thickness, TextTrimming, Visibility, VerticalAlignment,
                            HorizontalAlignment, FontWeights, GridLength, GridUnitType, ResizeMode)
from System.Windows.Controls import (Border, StackPanel, TextBlock, TextBox, CheckBox, Button,
                                     ScrollViewer, ScrollBarVisibility, Orientation, Grid,
                                     ColumnDefinition, Image)
from System.Windows.Media import Stretch
from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
from System.Windows.Input import Cursors, Keyboard, ModifierKeys
from Autodesk.Revit.DB import ElementId, Element, BuiltInParameter


IMG = 48


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def group_key(doc, el):
    """(Категория, Семейство, Тип) — как в «Распознать» и «Фильтровать»."""
    cat = el.Category.Name if el.Category else u"(без категории)"
    fam, typ, et = u"-", u"-", None
    tid = el.GetTypeId()
    if tid is not None and _iv(tid) > 0:
        et = doc.GetElement(tid)
        if et is not None:
            try:
                fam = et.FamilyName
            except Exception:
                pass
            try:
                typ = Element.Name.GetValue(et)
            except Exception:
                p = et.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
                if p is not None:
                    typ = p.AsString() or u"-"
    return (cat, fam, typ), et


def _type_image(et, cache):
    if et is None:
        return None
    k = _iv(et.Id)
    if k in cache:
        return cache[k]
    src = None
    try:
        bmp = et.GetPreviewImage(DSize(IMG, IMG))
        if bmp is not None:
            ms = MemoryStream()
            bmp.Save(ms, ImageFormat.Png)
            bmp.Dispose()
            ms.Position = 0
            src = BitmapImage()
            src.BeginInit()
            src.CacheOption = BitmapCacheOption.OnLoad
            src.StreamSource = ms
            src.EndInit()
            src.Freeze()
            ms.Dispose()
    except Exception:
        src = None
    cache[k] = src
    return src


def pick_groups(doc, ids, title, subtitle=None, ok_text=u"Готово", checked=None, note=None):
    """ids — кандидаты (ElementId). checked(key, el) -> bool — какие группы отмечены
    сразу (key = (категория, семейство, тип)). Возвращает список ElementId отмеченных
    групп или None, если нажали «Отмена»."""
    import hui_forms as forms
    groups, gids, gtype, order = {}, {}, {}, []
    for eid in ids:
        el = doc.GetElement(eid)
        if el is None:
            continue
        key, et = group_key(doc, el)
        if key not in groups:
            groups[key] = 0
            gids[key] = []
            gtype[key] = (et, el)
            order.append(key)
        groups[key] += 1
        gids[key].append(eid)
    if not order:
        return []
    order.sort(key=lambda k: (k[0], k[1], k[2]))

    B = forms._brush
    dlg = forms._Dialog(title, width=720)
    dlg.ResizeMode = ResizeMode.CanResize
    dlg._s.Text = subtitle or u"{} эл. · {} групп · щелчок — флажок, Shift+щелчок — диапазон".format(
        sum(groups.values()), len(order))
    dlg._s.Visibility = Visibility.Visible
    if note:
        nt = TextBlock()
        nt.Text = note
        nt.TextWrapping = System.Windows.TextWrapping.Wrap
        nt.Foreground = B(forms.MUTE)
        nt.Margin = Thickness(0, 0, 0, 8)
        dlg.body.Children.Add(nt)

    search = TextBox()
    search.Margin = Thickness(0, 0, 0, 8)
    search.ToolTip = u"Поиск по категории, семейству, типу"
    dlg.body.Children.Add(search)
    tools = StackPanel()
    tools.Orientation = Orientation.Horizontal
    tools.Margin = Thickness(0, 0, 0, 8)
    dlg.body.Children.Add(tools)

    rows_panel = StackPanel()
    sv = ScrollViewer()
    sv.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
    sv.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
    sv.Height = max(120, min(520, Screen.PrimaryScreen.WorkingArea.Height - 300, len(order) * 57 + 2))
    sv.Content = rows_panel
    frame = Border()
    frame.BorderBrush = B(forms.LINE)
    frame.BorderThickness = Thickness(1)
    frame.Background = B('#FFFFFF')
    frame.Child = sv
    dlg.body.Children.Add(frame)

    rows, painters, last, btn_ok, cache = [], {}, [None], [None], {}

    def refresh():
        n_g = sum(1 for r in rows if r[1].IsChecked)
        n_e = sum(groups[r[2]] for r in rows if r[1].IsChecked)
        if btn_ok[0] is not None:
            btn_ok[0].Content = u"{} ({} гр., {} шт.)".format(ok_text, n_g, n_e) if n_g else ok_text
            btn_ok[0].IsEnabled = n_g > 0

    def make_row(idx, key):
        cat, fam, typ = key
        g = Grid()
        for w in (GridLength(26), GridLength(IMG + 12), GridLength(1, GridUnitType.Star), GridLength.Auto):
            cd = ColumnDefinition()
            cd.Width = w
            g.ColumnDefinitions.Add(cd)
        cb = CheckBox()
        cb.VerticalAlignment = VerticalAlignment.Center
        cb.IsHitTestVisible = False
        try:
            cb.IsChecked = bool(checked(key, gtype[key][1])) if checked else False
        except Exception:
            cb.IsChecked = False
        g.Children.Add(cb)
        src = _type_image(gtype[key][0], cache)
        if src is not None:
            pic = Image()
            pic.Source = src
            pic.Width = IMG
            pic.Height = IMG
            pic.Stretch = Stretch.Uniform
        else:
            pic = Border()
            pic.Width = IMG
            pic.Height = IMG
            pic.Background = B(forms.BG)
            pic.BorderBrush = B(forms.LINE)
            pic.BorderThickness = Thickness(1)
            t = TextBlock()
            t.Text = u"нет\nпревью"
            t.FontSize = 9
            t.Foreground = B(forms.MUTE)
            t.TextAlignment = System.Windows.TextAlignment.Center
            t.VerticalAlignment = VerticalAlignment.Center
            pic.Child = t
        pic.HorizontalAlignment = HorizontalAlignment.Left
        pic.VerticalAlignment = VerticalAlignment.Center
        Grid.SetColumn(pic, 1)
        g.Children.Add(pic)
        txt = StackPanel()
        txt.VerticalAlignment = VerticalAlignment.Center
        t1 = TextBlock()
        t1.Text = u"{}. {}".format(idx, typ)
        t1.FontWeight = FontWeights.SemiBold
        t1.TextTrimming = TextTrimming.CharacterEllipsis
        t2 = TextBlock()
        t2.Text = u"{}  ·  {}".format(cat, fam)
        t2.FontSize = 11.5
        t2.Foreground = B(forms.MUTE)
        t2.TextTrimming = TextTrimming.CharacterEllipsis
        txt.Children.Add(t1)
        txt.Children.Add(t2)
        txt.ToolTip = u"{}\n{}\n{}".format(cat, fam, typ)
        Grid.SetColumn(txt, 2)
        g.Children.Add(txt)
        cnt = TextBlock()
        cnt.Text = u"{} шт.".format(groups[key])
        cnt.FontWeight = FontWeights.SemiBold
        cnt.Foreground = B(forms.GREEN)
        cnt.Margin = Thickness(10, 0, 4, 0)
        cnt.VerticalAlignment = VerticalAlignment.Center
        Grid.SetColumn(cnt, 3)
        g.Children.Add(cnt)
        row = Border()
        row.Padding = Thickness(8, 4, 8, 4)
        row.BorderBrush = B(forms.LINE)
        row.BorderThickness = Thickness(0, 0, 0, 1)
        row.Cursor = Cursors.Hand
        row.Child = g

        def paint(r=row, c=cb, hover=False):
            r.Background = B('#E8F5E9') if c.IsChecked else (B('#F1F8F2') if hover else B('#FFFFFF'))

        def click(s, e, r=row, c=cb):
            cur = next(i for i, x in enumerate(rows) if x[0] is r)
            state = not c.IsChecked
            shift = (Keyboard.Modifiers & ModifierKeys.Shift) == ModifierKeys.Shift
            if shift and last[0] is not None and last[0] < len(rows):
                a, b = sorted((last[0], cur))
                for x in rows[a:b + 1]:
                    if x[0].Visibility == Visibility.Visible:
                        x[1].IsChecked = state
                        painters[x[0]]()
            else:
                c.IsChecked = state
                paint()
            last[0] = cur
            refresh()

        row.MouseLeftButtonUp += click
        row.MouseEnter += lambda s, e: paint(hover=True)
        row.MouseLeave += lambda s, e: paint()
        painters[row] = paint
        paint()
        rows_panel.Children.Add(row)
        rows.append((row, cb, key, u" ".join(key).lower()))

    for i, k in enumerate(order, start=1):
        make_row(i, k)

    def set_all(state):
        for r, c, k, s in rows:
            if r.Visibility == Visibility.Visible:
                c.IsChecked = (not c.IsChecked) if state is None else state
                painters[r]()
        refresh()

    for txt_, st_ in ((u"Отметить все", True), (u"Снять все", False), (u"Инвертировать выбор", None)):
        b = Button()
        b.Content = txt_
        b.ToolTip = u"Если в поиске что-то введено — только для найденных строк"
        b.Margin = Thickness(0, 0, 8, 0)
        b.Click += (lambda v: (lambda s, e: set_all(v)))(st_)
        tools.Children.Add(b)

    def on_search(s, e):
        words = (search.Text or u"").lower().split()
        for r, c, k, st in rows:
            r.Visibility = Visibility.Visible if all(w in st for w in words) else Visibility.Collapsed

    search.TextChanged += on_search
    dlg.add_button(u"Отмена", None, cancel=True)
    btn_ok[0] = dlg.add_button(ok_text, '__ok__', primary=True)
    refresh()
    dlg.Loaded += lambda s, e: search.Focus()
    forms.adaptive(dlg, 'pick')
    dlg.ShowDialog()
    if dlg.response != '__ok__':
        return None
    out = []
    for r, c, k, s in rows:
        if c.IsChecked:
            out.extend(gids[k])
    return out
