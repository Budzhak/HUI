# -*- coding: utf-8 -*-
"""Распознать: группы выделенного (Категория | Семейство | Тип) с мини-картинками.
Отметь нужные группы флажками -> «Выделить в модели» (как «Фильтровать»).
Ничего не выделено — распознаётся всё видимое на активном виде;
есть выделение — в окне переключатель «Выделение / Весь вид».
Shift+щелчок по строке — отметить/снять диапазон от предыдущего щелчка.
«Инвертировать выбор» — отмеченные снимаются, неотмеченные отмечаются (для найденных строк).
Поиск — также по классификации систем и сокращению для системы (третья строка в группе).
Окно растягивается — список растёт вместе с ним.
v2.2 · 21.10.2026"""

# ============================================================
# БЛОК 1. Импорты
# ============================================================
from pyrevit import revit, forms
import hui_forms as forms   # единый стиль окон ХУЙ (lib/hui_forms.py)
from Autodesk.Revit.DB import *
from System.Collections.Generic import List

import clr
clr.AddReference('System.Drawing')
clr.AddReference('System.Windows.Forms')
from System.Drawing import Size as DSize
from System.Drawing.Imaging import ImageFormat
from System.IO import MemoryStream
from System.Windows.Forms import Screen
from System.Windows import (Thickness, TextWrapping, TextTrimming, Visibility,
                            VerticalAlignment, HorizontalAlignment, FontWeights,
                            GridLength, GridUnitType, SizeToContent, ResizeMode)
from System.Windows.Controls import (Border, StackPanel, TextBlock, TextBox, CheckBox, Button,
                                     ScrollViewer, ScrollBarVisibility, Orientation, Grid,
                                     ColumnDefinition, Image, DockPanel, Dock)
from System.Windows.Media import Stretch
from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
from System.Windows.Input import Cursors, Keyboard, ModifierKeys
import System

doc = revit.doc
uidoc = revit.uidoc

IMG = 48   # размер мини-картинки, px


# ============================================================
# БЛОК 2. Источник: выделение или всё видимое на активном виде
# (ничего не выделено -> сразу «Весь вид»)
# ============================================================
SKIP_CATS = set()
for _n in ('OST_Cameras', 'OST_Viewers', 'OST_SectionBox', 'OST_IOSSketchGrid',
           'OST_ProjectBasePoint', 'OST_SharedBasePoint', 'OST_IOS_GeoSite'):
    try:
        SKIP_CATS.add(int(getattr(BuiltInCategory, _n)))
    except Exception:
        pass
view = doc.ActiveView
selected_ids = list(uidoc.Selection.GetElementIds())


def view_ids():
    """Только то, что видно на виде: видимость категорий, фильтры, скрытые элементы,
    подрезка — учитывает сам коллектор; временное скрытие/изоляцию — проверяем отдельно."""
    out = []
    try:
        temp = view.IsTemporaryHideIsolateActive()
    except Exception:
        temp = False
    for el in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        cat = el.Category
        if cat is None or cat.Id.IntegerValue in SKIP_CATS:
            continue
        if cat.CategoryType not in (CategoryType.Model, CategoryType.Annotation):
            continue
        if temp:
            try:
                if not view.IsElementVisibleInTemporaryViewMode(
                        TemporaryViewMode.TemporaryHideIsolate, el.Id):
                    continue
            except Exception:
                pass
        out.append(el.Id)
    return out


# ============================================================
# БЛОК 3. Группировка по (Категория, Семейство, Тип) — как в «Фильтровать»
# ============================================================
groups, group_ids, group_type, order = {}, {}, {}, []
group_sys = {}   # key -> {'abbr': set, 'cls': set}


def sys_vals(el):
    """(сокращение для системы, классификация систем) элемента или пустые строки."""
    out = []
    for bip in (BuiltInParameter.RBS_DUCT_PIPE_SYSTEM_ABBREVIATION_PARAM,
                BuiltInParameter.RBS_SYSTEM_CLASSIFICATION_PARAM):
        v = u''
        try:
            p = el.get_Parameter(bip)
            if p is not None and p.HasValue:
                v = p.AsString() or p.AsValueString() or u''
        except Exception:
            pass
        out.append(v.strip())
    return out


def build_groups(ids):
    groups.clear(); group_ids.clear(); group_type.clear(); group_sys.clear(); del order[:]
    for eid in ids:
        el = doc.GetElement(eid)
        if el is None:
            continue
        category_name = el.Category.Name if el.Category else u"(без категории)"
        family_name, type_name, el_type = u"-", u"-", None
        type_id = el.GetTypeId()
        if type_id != ElementId.InvalidElementId:
            el_type = doc.GetElement(type_id)
            if el_type:
                try:
                    family_name = el_type.FamilyName
                except Exception:
                    family_name = u"-"
                try:
                    type_name = Element.Name.GetValue(el_type)
                except Exception:
                    try:
                        type_name = el_type.Name
                    except Exception:
                        p = el_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
                        if p:
                            type_name = p.AsString()
        key = (category_name, family_name, type_name)
        if key not in groups:
            groups[key] = 0
            group_ids[key] = []
            group_type[key] = el_type
            group_sys[key] = {'abbr': set(), 'cls': set()}
            order.append(key)
        ab, cl = sys_vals(el)
        for part in ab.split(u','):
            if part.strip():
                group_sys[key]['abbr'].add(part.strip())
        for part in cl.split(u','):
            if part.strip():
                group_sys[key]['cls'].add(part.strip())
        groups[key] += 1
        group_ids[key].append(eid)


mode = ['sel' if selected_ids else 'view']
build_groups(selected_ids if mode[0] == 'sel' else view_ids())
if not order:
    forms.alert(u"На виде «{}» нечего распознавать.".format(view.Name))
    raise SystemExit


# ============================================================
# БЛОК 4. Мини-картинки (превью типа -> WPF), кэш по типу
# ============================================================
_img_cache = {}


def type_image(el_type):
    if el_type is None:
        return None
    k = el_type.Id.IntegerValue
    if k in _img_cache:
        return _img_cache[k]
    src = None
    try:
        bmp = el_type.GetPreviewImage(DSize(IMG, IMG))
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
    _img_cache[k] = src
    return src


# ============================================================
# БЛОК 5. Окно: поиск + строки (флажок | картинка | тип / категория · семейство | шт.)
# ============================================================
B = forms._brush
dlg = forms._Dialog(u"Распознать", width=720)
dlg.ResizeMode = ResizeMode.CanResize


def set_sub():
    src = u"выделение" if mode[0] == 'sel' else u"весь вид «{}»".format(view.Name)
    dlg._s.Text = u"{} · {} эл. · {} групп · щелчок — флажок, Shift+щелчок — диапазон".format(
        src, sum(groups.values()), len(order))


set_sub()
dlg._s.Visibility = Visibility.Visible

search = TextBox()
search.Margin = Thickness(0, 0, 0, 8)
search.ToolTip = u"Поиск по категории, семейству, типу, сокращению для системы и классификации систем"
dlg.body.Children.Add(search)

tools = StackPanel()
tools.Orientation = Orientation.Horizontal
tools.Margin = Thickness(0, 0, 0, 8)
dlg.body.Children.Add(tools)

rows_panel = StackPanel()
sv = ScrollViewer()
sv.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
sv.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
sv.Height = max(120, min(560, Screen.PrimaryScreen.WorkingArea.Height - 260, len(order) * 57 + 2))
sv.Content = rows_panel
frame = Border()
frame.BorderBrush = B(forms.LINE)
frame.BorderThickness = Thickness(1)
frame.Background = B('#FFFFFF')
frame.Child = sv
dlg.body.Children.Add(frame)

rows = []   # (border, checkbox, key, search_text)
fit = [None]   # запас по высоте окна вокруг списка (после загрузки); список растёт вместе с окном
last = [None]   # индекс последней щёлкнутой строки (для Shift)
painters = {}
btn_ok = [None]


def refresh_count():
    n_g = sum(1 for r in rows if r[1].IsChecked)
    n_e = sum(groups[r[2]] for r in rows if r[1].IsChecked)
    if btn_ok[0] is not None:
        btn_ok[0].Content = (u"Выделить в модели ({} гр., {} шт.)".format(n_g, n_e)
                             if n_g else u"Выделить в модели")
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
    g.Children.Add(cb)

    src = type_image(group_type[key])
    if src is not None:
        im = Image()
        im.Source = src
        im.Width = IMG
        im.Height = IMG
        im.Stretch = Stretch.Uniform
        pic = im
    else:
        ph = Border()
        ph.Width = IMG
        ph.Height = IMG
        ph.Background = B(forms.BG)
        ph.BorderBrush = B(forms.LINE)
        ph.BorderThickness = Thickness(1)
        t = TextBlock()
        t.Text = u"нет\nпревью"
        t.FontSize = 9
        t.Foreground = B(forms.MUTE)
        t.TextAlignment = System.Windows.TextAlignment.Center
        t.VerticalAlignment = VerticalAlignment.Center
        ph.Child = t
        pic = ph
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
    sy = group_sys.get(key) or {'abbr': set(), 'cls': set()}
    sys_txt = u"  ·  ".join(x for x in (u", ".join(sorted(sy['abbr'])), u", ".join(sorted(sy['cls']))) if x)
    if sys_txt:
        t3 = TextBlock()
        t3.Text = sys_txt
        t3.FontSize = 11
        t3.Foreground = B(forms.GREEN)
        t3.TextTrimming = TextTrimming.CharacterEllipsis
        txt.Children.Add(t3)
    txt.ToolTip = u"{}\n{}\n{}{}".format(cat, fam, typ, (u"\n" + sys_txt) if sys_txt else u"")
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
    row.Background = B('#FFFFFF')
    row.Cursor = Cursors.Hand
    row.Child = g

    def paint(r=row, c=cb, hover=False):
        if c.IsChecked:
            r.Background = B('#E8F5E9')
        elif hover:
            r.Background = B('#F1F8F2')
        else:
            r.Background = B('#FFFFFF')

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
        refresh_count()

    row.MouseLeftButtonUp += click
    row.MouseEnter += lambda s, e: paint(hover=True)
    row.MouseLeave += lambda s, e: paint()
    painters[row] = paint
    rows_panel.Children.Add(row)
    rows.append((row, cb, key, (u" ".join(key) + u" " + sys_txt).lower()))


def fill_rows():
    rows_panel.Children.Clear()
    del rows[:]
    last[0] = None
    painters.clear()
    for i, k in enumerate(order, start=1):
        make_row(i, k)
    search.Text = u""
    if fit[0] is None:
        sv.Height = max(120, min(560, Screen.PrimaryScreen.WorkingArea.Height - 260, len(order) * 57 + 2))
    sv.ScrollToTop()
    set_sub()
    refresh_count()


fill_rows()


def set_all(state):
    for r, c, k, s in rows:
        if r.Visibility == Visibility.Visible:
            c.IsChecked = (not c.IsChecked) if state is None else state
            painters[r]()
    refresh_count()


def switch(new_mode):
    if new_mode == mode[0]:
        return
    ids = selected_ids if new_mode == 'sel' else view_ids()
    if not ids:
        return
    mode[0] = new_mode
    build_groups(ids)
    fill_rows()
    paint_switch()


sw_btns = {}


def paint_switch():
    for m, b in sw_btns.items():
        b.IsDefault = False
        b.FontWeight = FontWeights.Bold if m == mode[0] else FontWeights.Normal
        b.Foreground = B(forms.GREEN) if m == mode[0] else B(forms.INK)


if selected_ids:
    for m, txt_ in (('sel', u"Выделение ({})".format(len(selected_ids))), ('view', u"Весь вид")):
        b = Button()
        b.Content = txt_
        b.Margin = Thickness(0, 0, 8, 0)
        b.ToolTip = u"Что распознавать: выделенное или всё видимое на активном виде"
        b.Click += (lambda v: (lambda s, e: switch(v)))(m)
        tools.Children.Add(b)
        sw_btns[m] = b
    sep = TextBlock()
    sep.Text = u"|"
    sep.Foreground = B(forms.LINE)
    sep.Margin = Thickness(2, 0, 10, 0)
    sep.VerticalAlignment = VerticalAlignment.Center
    tools.Children.Add(sep)
    paint_switch()

for txt_, st_ in ((u"Отметить все", True), (u"Снять все", False), (u"Инвертировать выбор", None)):
    b = Button()
    b.Content = txt_
    b.ToolTip = u"Если в поиске что-то введено — только для найденных строк"
    b.Margin = Thickness(0, 0, 8, 0)
    b.Click += (lambda v: (lambda s, e: set_all(v)))(st_)
    tools.Children.Add(b)


def on_search(s, e):
    q = (search.Text or u"").lower().strip()
    words = q.split()
    for r, c, k, st in rows:
        ok = all(w in st for w in words)
        r.Visibility = Visibility.Visible if ok else Visibility.Collapsed


search.TextChanged += on_search

dlg.add_button(u"Отмена", None, cancel=True)
btn_ok[0] = dlg.add_button(u"Выделить в модели", '__ok__', primary=True)
refresh_count()
def on_loaded(s, e):
    search.Focus()
    fit[0] = dlg.ActualHeight - sv.ActualHeight


def on_size(s, e):
    if fit[0] is None:
        return
    sv.Height = max(120, dlg.ActualHeight - fit[0])


dlg.Loaded += on_loaded
dlg.SizeChanged += on_size

dlg.ShowDialog()


# ============================================================
# БЛОК 6. Выделение отмеченных групп в модели
# ============================================================
if dlg.response == '__ok__':
    combined = []
    for r, c, k, s in rows:
        if c.IsChecked:
            combined.extend(group_ids[k])
    if combined:
        uidoc.Selection.SetElementIds(List[ElementId](combined))
