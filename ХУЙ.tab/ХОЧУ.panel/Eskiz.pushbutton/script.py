# -*- coding: utf-8 -*-
__title__ = u"Эскиз"
__author__ = u"db"
__doc__ = u"""Эскиз нестандартного изделия по ЕСКД в легенде — из параметров семейства (пленумы кнопки «Пленум»).

1) Изделия: пленумы (параметры врезок db_В1_…) — из модели; КСД и любые другие семейства — из выделенного (корпус и патрубки по геометрии, у КСД щель снизу; не «короб с патрубками» — «⚠ приблизительно»).
2) Главный вид — грань и поворот (запоминается для конфигурации); виды слева / сверху / справа / снизу / сзади,
   аксонометрия с азимутом и наклоном, масштаб.
3) Куда: легенда на каждый типоразмер или одна легенда со всеми отмеченными (сводная / своя).
Превью — сразу в окне. Легенда с тем же именем перерисовывается. Отмена — Ctrl+Z. Код: lib/hui_eskiz.py."""

import sys
import math
import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
import System
from pyrevit import revit, DB
import hui_forms as forms
sys.modules.pop('hui_eskiz', None)
import hui_eskiz as E

from System.Windows import (Thickness, Visibility, HorizontalAlignment, VerticalAlignment, FontWeights, TextWrapping,
                            ResizeMode, SizeToContent, GridLength, GridUnitType, Point)
from System.Windows.Controls import (StackPanel, WrapPanel, TextBlock, CheckBox, RadioButton, ComboBox, TextBox, Button,
                                     Border, ScrollViewer, ScrollBarVisibility, Orientation, Grid, ColumnDefinition,
                                     ListBox, Canvas, GridSplitter, SelectionMode)
from System.Windows.Media import (SolidColorBrush, Color, ScaleTransform, RotateTransform, TranslateTransform,
                                  TransformGroup, DoubleCollection, PointCollection)
from System.Windows.Shapes import Line as WLine, Ellipse, Polygon
from System.Windows.Input import Keyboard, ModifierKeys, Key

doc = revit.doc
uidoc = revit.uidoc
B = forms._brush

try:
    import hui_settings
    CFG = dict(hui_settings.get('eskiz') or {})
except Exception:
    hui_settings = None
    CFG = {}
FACES_CFG = dict(CFG.get('faces') or {})


def save_cfg():
    if hui_settings is None:
        return
    CFG['faces'] = FACES_CFG
    try:
        hui_settings.set('eskiz', CFG)
    except Exception:
        pass


def guarded(fn):
    """Ошибка в обработчике — окном с текстом, а не «нажал — ничего»."""
    def wrap(s, e):
        try:
            return fn(s, e)
        except Exception:
            import traceback
            forms.alert(u'Ошибка — действие не выполнено:\n\n' + traceback.format_exc()[-1500:], title=u'Эскиз')
    return wrap


# ---------------------------------------------------------------- изделия
ITEMS = []
for s_ in E.candidates(doc):                        # пленумы: параметры врезок db_В*
    p_ = E.part_of_symbol(s_, doc)
    if p_:
        ITEMS.append({'sym': s_, 'part': p_, 'ids': None, 'key': p_['code'] or (s_.FamilyName + u' : ' + E.nm(s_)),
                      'fam': s_.FamilyName, 'tname': E.nm(s_),
                      'label': u'%s : %s  (%d шт.)' % (s_.FamilyName, E.nm(s_), p_['count'])})
SEL_ALL = [i_.IntegerValue for i_ in uidoc.Selection.GetElementIds()]
for g_ in E.geo_items(doc, SEL_ALL):                # любые выделенные семейства: корпус и патрубки по геометрии
    g_['fam'], g_['tname'] = g_['sym'].FamilyName, g_['part']['type']
    ITEMS.append(g_)
if not ITEMS:
    forms.alert(u'Нечего эскизировать.\n\nПленумы (параметры врезок db_В1_Грань …) берутся из модели сами; '
                u'КСД и любые другие семейства — выделите их в модели и запустите «Эскиз» снова.',
                title=u'Эскиз', exitscript=True)
SEL_SYM, SEL_IDS = set(), set()
for i_ in uidoc.Selection.GetElementIds():
    el_ = doc.GetElement(i_)
    if isinstance(el_, DB.FamilyInstance) and el_.Symbol is not None:
        SEL_SYM.add(el_.Symbol.Id.IntegerValue)
        SEL_IDS.add(i_.IntegerValue)
    elif isinstance(el_, DB.FamilySymbol):
        SEL_SYM.add(el_.Id.IntegerValue)


def is_sel(it):
    if it.get('ids'):
        return any(i in SEL_IDS for i in it['ids'])
    return it['sym'].Id.IntegerValue in SEL_SYM


def opt_of(it):
    f = FACES_CFG.get(it['key'])
    o = {'views': list(CFG.get('views', E.DEFAULT_OPT['views'])), 'axo': CFG.get('axo', True),
         'az': float(CFG.get('az', 35.0)), 'el': float(CFG.get('el', 30.0)), 'axo_pos': CFG.get('axo_pos', 'middle')}
    o['face'] = f[0] if f else E.default_face(it['part'])
    o['rot'] = int(f[1]) if f else 0
    return o


def scale_for(parts):
    s = CFG.get('scale', 0)
    return float(s) if s else E.auto_scale(parts)


# ---------------------------------------------------------------- элементы окна
def txt(t, bold=False, muted=False, size=None, margin=None):
    tb = TextBlock()
    tb.Text = t
    tb.TextWrapping = TextWrapping.Wrap
    if bold:
        tb.FontWeight = FontWeights.SemiBold
        tb.Foreground = B(forms.GREEN)
    if muted:
        tb.Foreground = B(forms.MUTE)
    if size:
        tb.FontSize = size
    tb.Margin = margin or Thickness(0, 0, 0, 4)
    return tb


def btn(t, handler, tip=None):
    b = Button()
    b.Content = t
    b.Padding = Thickness(10, 3, 10, 3)
    b.Margin = Thickness(0, 0, 8, 0)
    if tip:
        b.ToolTip = tip
    if handler is not None:
        b.Click += handler
    return b


def hpanel(m=None):
    sp = StackPanel()
    sp.Orientation = Orientation.Horizontal
    sp.Margin = m or Thickness(0, 2, 0, 6)
    return sp


def section(title):
    bd = Border()
    bd.BorderBrush = B(forms.LINE)
    bd.BorderThickness = Thickness(1)
    bd.Background = B('#FFFFFF')
    bd.Padding = Thickness(10, 8, 10, 8)
    bd.Margin = Thickness(0, 0, 0, 10)
    sp = StackPanel()
    sp.Children.Add(txt(title, bold=True))
    bd.Child = sp
    return bd, sp


def combo(values, cur, width=None):
    cb = ComboBox()
    for v in values:
        cb.Items.Add(v)
    cb.SelectedItem = cur if cur in values else values[0]
    if width:
        cb.Width = width
    cb.Margin = Thickness(0, 0, 10, 0)
    return cb


def num_field(val, lo, hi, step, on_change, tip):
    """Узкое поле: ввод с клавиатуры + Enter, ↑/↓ — шаг, PgUp/PgDn — 3 шага."""
    box = TextBox()
    box.MinWidth = 0
    box.Width = 44
    box.Padding = Thickness(2, 0, 2, 0)
    box.HorizontalContentAlignment = HorizontalAlignment.Right
    box.Text = E.fmt(val)
    box.ToolTip = tip

    def commit(*a):
        try:
            v = max(lo, min(hi, float((box.Text or u'0').replace(u',', u'.').replace(u'°', u''))))
        except Exception:
            v = lo
        box.Text = E.fmt(v)
        on_change(v)

    def key(s_, e_):
        d = {Key.Up: step, Key.Down: -step, Key.PageUp: 3 * step, Key.PageDown: -3 * step}.get(e_.Key)
        if e_.Key in (Key.Enter, Key.Return):
            commit()
            box.SelectAll()
            e_.Handled = True
        elif d:
            try:
                v = float(box.Text.replace(u',', u'.'))
            except Exception:
                v = 0.0
            box.Text = E.fmt(max(lo, min(hi, v + d)))
            commit()
            box.SelectAll()
            e_.Handled = True
    box.PreviewKeyDown += key
    box.LostFocus += commit
    box.GotKeyboardFocus += lambda s_, e_: box.SelectAll()
    return box


dlg = forms._Dialog(u'Эскиз', width=float(CFG.get('w', 1180)))
dlg.ResizeMode = ResizeMode.CanResize
dlg.SizeToContent = SizeToContent.Manual
dlg.Height = float(CFG.get('h', 760))
dlg.MinWidth, dlg.MinHeight = 760, 520
dlg._s.Text = u'эскиз по ЕСКД из параметров семейства · превью справа · Ctrl+колесо — масштаб окна'
dlg._s.Visibility = Visibility.Visible

grid = Grid()
c0, c1, c2 = ColumnDefinition(), ColumnDefinition(), ColumnDefinition()
c0.Width = GridLength(float(CFG.get('left_w', 400)))
c0.MinWidth = 280
c1.Width = GridLength(6)
c2.Width = GridLength(1, GridUnitType.Star)
c2.MinWidth = 200
for c_ in (c0, c1, c2):
    grid.ColumnDefinitions.Add(c_)
root = StackPanel()
sv = ScrollViewer()
sv.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
sv.Content = root
Grid.SetColumn(sv, 0)
grid.Children.Add(sv)
spl = GridSplitter()
spl.Width = 6
spl.HorizontalAlignment = HorizontalAlignment.Stretch
spl.Background = B(forms.LINE)
spl.ToolTip = u'Тяните: шире настройки или превью'
Grid.SetColumn(spl, 1)
grid.Children.Add(spl)
pv_border = Border()
pv_border.BorderBrush = B(forms.LINE)
pv_border.BorderThickness = Thickness(1)
pv_border.Background = B('#FFFFFF')
pv_border.Margin = Thickness(8, 0, 0, 0)
cv = Canvas()
cv.ClipToBounds = True
pv_border.Child = cv
Grid.SetColumn(pv_border, 2)
grid.Children.Add(pv_border)
# тело окна растёт с окном: DockPanel-последний ребёнок — body (StackPanel) → заменяем на Grid
_dock = dlg.Content
_dock.Children.Remove(dlg.body)
body = Grid()
body.Margin = Thickness(18, 14, 18, 14)
body.Children.Add(grid)
_dock.Children.Add(body)
W = {'cur': None, 'rows': []}

# 1. изделия
b1, s1 = section(u'1. Изделия (пленумы + выделенное)')
lb = ListBox()
lb.MaxHeight = 220
lb.SelectionMode = SelectionMode.Single
for it in ITEMS:
    cb = CheckBox()
    n_ = it['part']['count']
    cb.Content = it['label']
    cb.IsChecked = is_sel(it)
    cb.Tag = it
    cb.Margin = Thickness(0, 1, 0, 1)
    lb.Items.Add(cb)
    W['rows'].append((it, cb))
s1.Children.Add(lb)
p1 = hpanel(Thickness(0, 6, 0, 0))


def set_all(v):
    for it, cb in W['rows']:
        cb.IsChecked = v(it)


p1.Children.Add(btn(u'Все', lambda s, e: set_all(lambda it: True)))
p1.Children.Add(btn(u'Ничего', lambda s, e: set_all(lambda it: False)))
p1.Children.Add(btn(u'Выделенные', lambda s, e: set_all(is_sel),
                    tip=u'Отметить типоразмеры элементов, выделенных в модели'))
s1.Children.Add(p1)
s1.Children.Add(txt(u'Щелчок по строке — показать в превью и настроить главный вид. Пленумы — из модели; КСД и другие '
                    u'семейства — из выделенного перед запуском (корпус и патрубки по геометрии; «⚠ приблизительно» — '
                    u'тело не «короб с патрубками», сверить с семейством).', muted=True, size=11))
root.Children.Add(b1)

# 2. главный вид
b2, s2 = section(u'2. Главный вид (для выбранного изделия)')
p2 = hpanel()
p2.Children.Add(txt(u'Грань', margin=Thickness(0, 3, 6, 0)))
cb_face = combo(E.FACES, E.FACES[0], 110)
p2.Children.Add(cb_face)
p2.Children.Add(txt(u'Повернуть', margin=Thickness(0, 3, 6, 0)))
cb_rot = combo([u'0°', u'90°', u'180°', u'270°'], u'0°', 70)
p2.Children.Add(cb_rot)
s2.Children.Add(p2)
s2.Children.Add(txt(u'По умолчанию — грань с наибольшим числом ответвлений. Выбор запоминается для конфигурации '
                    u'(db_Код конфигурации).', muted=True, size=11))
root.Children.Add(b2)

# 3. виды
b3, s3 = section(u'3. Виды и масштаб')
wp = WrapPanel()
VIEW_CB = {}
for key_, title_, col_, row_ in E.VIEWS[1:]:
    c = CheckBox()
    c.Content = title_
    c.IsChecked = key_ in CFG.get('views', E.DEFAULT_OPT['views'])
    c.Margin = Thickness(0, 2, 14, 2)
    VIEW_CB[key_] = c
    wp.Children.Add(c)
s3.Children.Add(wp)
cb_axo = CheckBox()
cb_axo.Content = u'Аксонометрия'
cb_axo.IsChecked = CFG.get('axo', True)
cb_axo.Margin = Thickness(0, 6, 0, 2)
s3.Children.Add(cb_axo)
p3 = hpanel()
p3.Children.Add(txt(u'азимут °', margin=Thickness(18, 3, 6, 0)))


def set_cfg(k, v):
    CFG[k] = v
    refresh()


p3.Children.Add(num_field(float(CFG.get('az', 35)), -180, 180, 5, lambda v: set_cfg('az', v),
                          u'Поворот взгляда вокруг вертикали от главного вида: + — справа, − — слева. ↑/↓ — по 5°'))
p3.Children.Add(txt(u'наклон °', margin=Thickness(14, 3, 6, 0)))
p3.Children.Add(num_field(float(CFG.get('el', 30)), -80, 80, 5, lambda v: set_cfg('el', v),
                          u'Взгляд сверху (+) или снизу (−). ↑/↓ — по 5°'))
s3.Children.Add(p3)
p3b = hpanel()
p3b.Children.Add(txt(u'место', margin=Thickness(18, 3, 6, 0)))
AXO_POS = [u'посередине группы видов', u'на уровне главного вида']
cb_pos = combo(AXO_POS, AXO_POS[1] if CFG.get('axo_pos') == 'top' else AXO_POS[0], 210)
p3b.Children.Add(cb_pos)
s3.Children.Add(p3b)
p3c = hpanel(Thickness(0, 6, 0, 2))
p3c.Children.Add(txt(u'Масштаб 1:', margin=Thickness(0, 3, 6, 0)))
SC_VALS = [u'авто'] + [E.fmt(x) for x in E.SCALES]
cb_sc = combo(SC_VALS, E.fmt(CFG['scale']) if CFG.get('scale') else u'авто', 70)
p3c.Children.Add(cb_sc)
W['sc_note'] = txt(u'', muted=True, size=11, margin=Thickness(4, 4, 0, 0))
p3c.Children.Add(W['sc_note'])
s3.Children.Add(p3c)
root.Children.Add(b3)

# 4. куда
b4, s4 = section(u'4. Куда')
r_each = RadioButton()
r_each.Content = u'Легенда на каждый типоразмер'
r_one = RadioButton()
r_one.Content = u'Одна легенда со всеми отмеченными (сводная / своя)'
(r_one if CFG.get('target') == 'one' else r_each).IsChecked = True
s4.Children.Add(r_each)
p4 = hpanel(Thickness(18, 2, 0, 6))
p4.Children.Add(txt(u'имя: префикс', margin=Thickness(0, 3, 6, 0)))
tb_pre = TextBox()
tb_pre.Text = CFG.get('prefix', u'Эскиз_')
tb_pre.MinWidth = 0
tb_pre.Width = 90
p4.Children.Add(tb_pre)
p4.Children.Add(txt(u'+ имя семейства', margin=Thickness(6, 3, 0, 0)))
s4.Children.Add(p4)
s4.Children.Add(r_one)
p4b = hpanel(Thickness(18, 2, 0, 6))
p4b.Children.Add(txt(u'имя', margin=Thickness(0, 3, 6, 0)))
tb_one = TextBox()
tb_one.Text = CFG.get('one_name', u'Эскизы_сводная')
tb_one.MinWidth = 0
tb_one.Width = 220
p4b.Children.Add(tb_one)
s4.Children.Add(p4b)
s4.Children.Add(txt(u'Легенда с таким именем уже есть — она очищается и рисуется заново. Новая легенда — копия любой '
                    u'легенды проекта без содержимого (API не создаёт легенду с нуля).', muted=True, size=11))
root.Children.Add(b4)


# ---------------------------------------------------------------- превью
INK = SolidColorBrush(Color.FromRgb(0x22, 0x22, 0x22))
DIMC = B(forms.GREEN)
GRAY = SolidColorBrush(Color.FromRgb(0xC8, 0xC8, 0xC8))
STY = {'thick': (INK, 1.6, None), 'thin': (INK, 0.7, None), 'axis': (INK, 0.6, [10.0, 3.0, 2.0, 3.0]),
       'hidden': (INK, 0.8, [4.0, 3.0]), 'dim': (DIMC, 0.6, None), 'dimtick': (DIMC, 1.1, None)}


def draw_preview():
    cv.Children.Clear()
    it = W['cur']
    if it is None or cv.ActualWidth < 30 or cv.ActualHeight < 30:
        return
    S = scale_for([it['part']])
    prims = E.build_sketch(it['part'], opt_of(it), S)
    items = E.render(prims, S)
    bb = E.bbox(items, S)
    pad = 14
    k = min((cv.ActualWidth - 2 * pad) / max(1, bb[2] - bb[0]), (cv.ActualHeight - 2 * pad) / max(1, bb[3] - bb[1]))
    ox = pad + ((cv.ActualWidth - 2 * pad) - (bb[2] - bb[0]) * k) / 2.0

    def X(p):
        return ox + (p[0] - bb[0]) * k

    def Y(p):
        return pad + (bb[3] - p[1]) * k
    for itm in items:
        t = itm[0]
        if t == 'seg':
            br, w_, dash = STY.get(itm[3], STY['thin'])
            ln = WLine()
            ln.X1, ln.Y1, ln.X2, ln.Y2 = X(itm[1]), Y(itm[1]), X(itm[2]), Y(itm[2])
            ln.Stroke = br
            ln.StrokeThickness = w_
            if dash:
                ln.StrokeDashArray = DoubleCollection([float(x) for x in dash])
            cv.Children.Add(ln)
        elif t in ('circle', 'disc'):
            el = Ellipse()
            r = itm[2] * k
            el.Width = el.Height = 2 * r
            Canvas.SetLeft(el, X(itm[1]) - r)
            Canvas.SetTop(el, Y(itm[1]) - r)
            if t == 'circle':
                br, w_, dash = STY.get(itm[3], STY['thin'])
                el.Stroke = br
                el.StrokeThickness = w_
                if dash:
                    el.StrokeDashArray = DoubleCollection([float(x) for x in dash])
            else:
                el.Fill = GRAY if itm[3] == 'gray' else INK
            cv.Children.Add(el)
        elif t == 'poly':
            pg = Polygon()
            pc = PointCollection()
            for q in itm[1]:
                pc.Add(Point(X(q), Y(q)))
            pg.Points = pc
            pg.Fill = GRAY if itm[2] == 'gray' else INK
            cv.Children.Insert(0, pg) if itm[2] == 'gray' else cv.Children.Add(pg)
        elif t == 'text':
            d = itm[1]
            tb = TextBlock()
            tb.Text = d['s']
            fs = d['h'] * S * k
            if fs < 3:
                continue
            tb.FontSize = fs
            tb.LineHeight = fs * 1.45
            tb.Foreground = DIMC if d.get('dim') else INK
            tb.Measure(System.Windows.Size(1e6, 1e6))
            w_ = tb.DesiredSize.Width
            dx = -w_ / 2.0 if d['al'] == 'c' else (-w_ if d['al'] == 'r' else 0.0)
            tg = TransformGroup()
            tg.Children.Add(TranslateTransform(dx, -fs * 0.15))
            tg.Children.Add(RotateTransform(-d['rot']))
            tb.RenderTransform = tg
            Canvas.SetLeft(tb, X(d['pos']))
            Canvas.SetTop(tb, Y(d['pos']))
            cv.Children.Add(tb)
    W['sc_note'].Text = u'превью: 1:%s, %s' % (E.fmt(S), it['fam'])


def refresh(*a):
    try:
        draw_preview()
    except Exception as ex:
        W['sc_note'].Text = u'превью не построено: %s' % ex


def pick_item(it):
    W['cur'] = it
    W['busy'] = True
    o = opt_of(it)
    cb_face.SelectedItem = o['face']
    cb_rot.SelectedItem = u'%d°' % o['rot']
    W['busy'] = False
    b2.Child.Children[0].Text = u'2. Главный вид — %s' % it['fam']
    refresh()


def on_sel(s, e):
    c = lb.SelectedItem
    if c is not None:
        pick_item(c.Tag)


def on_face(s, e):
    if W.get('busy') or W['cur'] is None:
        return
    FACES_CFG[W['cur']['key']] = [cb_face.SelectedItem, int(unicode(cb_rot.SelectedItem).replace(u'°', u''))]
    refresh()


def on_views(s, e):
    CFG['views'] = [k for k, c in VIEW_CB.items() if c.IsChecked]
    CFG['axo'] = bool(cb_axo.IsChecked)
    CFG['axo_pos'] = 'top' if cb_pos.SelectedItem == AXO_POS[1] else 'middle'
    v = cb_sc.SelectedItem
    CFG['scale'] = 0 if v == u'авто' else float(v)
    refresh()


lb.SelectionChanged += on_sel
cb_face.SelectionChanged += on_face
cb_rot.SelectionChanged += on_face
for c in list(VIEW_CB.values()) + [cb_axo]:
    c.Click += on_views
cb_pos.SelectionChanged += on_views
cb_sc.SelectionChanged += on_views
cv.SizeChanged += refresh


# ---------------------------------------------------------------- создать
def status(t, ok=True):
    W['status'].Text = t
    W['status'].Foreground = B(forms.GREEN if ok else forms.RED)
    if not ok:
        forms.alert(t, title=u'Эскиз')


BAD = u'\\:{}[]|;<>?`~'


def clean(name):
    for ch in BAD:
        name = name.replace(ch, u'_')
    return name.strip()


@guarded
def do_create(s, e):
    chosen = [it for it, cb in W['rows'] if cb.IsChecked]
    if not chosen:
        status(u'Не отмечено ни одного изделия (раздел 1).', ok=False)
        return
    CFG['target'] = 'one' if r_one.IsChecked else 'each'
    CFG['prefix'] = tb_pre.Text or u''
    CFG['one_name'] = tb_one.Text or u'Эскизы_сводная'
    jobs = []
    if CFG['target'] == 'one':
        S = scale_for([it['part'] for it in chosen])
        jobs.append((clean(CFG['one_name']), S, chosen))
    else:
        fams = {}
        for it in chosen:
            fams[it['fam']] = fams.get(it['fam'], 0) + 1
        for it in chosen:
            nm = CFG['prefix'] + it['fam']
            if fams[it['fam']] > 1 or len([x for x in ITEMS if x['fam'] == it['fam']]) > 1:
                nm += u'_' + it['tname']
            jobs.append((clean(nm), scale_for([it['part']]), [it]))
    lines = []
    for nm, S, its in jobs:
        ex = E.find_legend(doc, nm)
        lines.append(u'• %s — 1:%s, %d изд.%s' % (nm, E.fmt(S), len(its), u' (есть — перерисуется)' if ex else u''))
    if not forms.alert(u'Легенды:\n' + u'\n'.join(lines) + u'\n\nСоздать? Отмена потом — Ctrl+Z.',
                       title=u'Эскиз — проба', yes=True, no=True):
        return
    errs, made, names = [], [], []
    with revit.Transaction(u'ХУЙ: эскиз'):
        st = E.styles(doc)
        for nm, S, its in jobs:
            lg = E.legend_for(doc, nm, S)
            prims = E.build_many([(it['part'], opt_of(it)) for it in its], S)
            errs += E.emit(doc, lg, prims, st)
            made.append(lg)
            names.append(nm)
    W['made'] = made
    save_cfg()
    msg = u'Готово: %d легенд(ы) — %s. Отмена — Ctrl+Z.' % (len(made), u', '.join(names))
    if errs:
        msg += u' Не создалось элементов: %d (первая ошибка: %s)' % (len(errs), errs[0])
    status(msg, ok=not errs)


W['status'] = txt(u'', size=12, margin=Thickness(4, 4, 4, 0))
_fb = dlg._footer_border
_old = _fb.Child
_fb.Child = None
_fp = StackPanel()
_fp.Children.Add(_old)
_fp.Children.Add(W['status'])
_fb.Child = _fp
b_open = btn(u'Открыть легенду', None, tip=u'Закрыть окно и открыть последнюю созданную легенду')


def on_open(s, e):
    W['open'] = True
    dlg.Close()


b_open.Click += on_open
dlg.footer.Children.Add(b_open)
# «Создать» не закрывает окно — можно поправить и создать ещё
b_mk = Button()
b_mk.Content = u'Создать'
b_mk.MinWidth = 90
b_mk.Margin = Thickness(8, 0, 0, 0)
b_mk.IsDefault = True
b_mk.Click += do_create
dlg.footer.Children.Add(b_mk)
dlg.add_button(u'Закрыть', None, cancel=True)

# Ctrl + колесо — масштаб настроек, Ctrl+0 — 100 %
ZOOM = [max(0.6, min(1.6, float(CFG.get('zoom', 1.0))))]
root.LayoutTransform = ScaleTransform(ZOOM[0], ZOOM[0])


def set_zoom(z):
    ZOOM[0] = round(max(0.6, min(1.6, z)), 2)
    root.LayoutTransform = ScaleTransform(ZOOM[0], ZOOM[0])
    CFG['zoom'] = ZOOM[0]
    dlg._s.Text = u'масштаб окна %d %% (Ctrl+колесо, Ctrl+0 — 100 %%) · превью справа' % int(ZOOM[0] * 100)


def on_zoom_wheel(s_, e_):
    if Keyboard.Modifiers & ModifierKeys.Control == ModifierKeys.Control:
        set_zoom(ZOOM[0] + (0.05 if e_.Delta > 0 else -0.05))
        e_.Handled = True


def on_zoom_key(s_, e_):
    if Keyboard.Modifiers & ModifierKeys.Control == ModifierKeys.Control and e_.Key in (Key.D0, Key.NumPad0):
        set_zoom(1.0)
        e_.Handled = True


def on_closing(s_, e_):
    CFG['w'], CFG['h'] = dlg.ActualWidth, dlg.ActualHeight
    CFG['left_w'] = c0.ActualWidth
    save_cfg()


dlg.PreviewMouseWheel += on_zoom_wheel
dlg.PreviewKeyDown += on_zoom_key
dlg.Closing += on_closing
first = [r for r in W['rows'] if r[1].IsChecked] or W['rows']
lb.SelectedItem = first[0][1]
dlg.ShowDialog()
if W.get('open') and W.get('made'):
    try:
        uidoc.ActiveView = W['made'][-1]
    except Exception:
        pass
