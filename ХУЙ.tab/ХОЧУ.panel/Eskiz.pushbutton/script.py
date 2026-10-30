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
sys.modules.pop('hui_eskiz_v', None)
import hui_eskiz as E
import hui_eskiz_v as V
import hui_eskiz_lg as LG

from System.Windows import (Thickness, Visibility, HorizontalAlignment, VerticalAlignment, FontWeights, TextWrapping,
                            ResizeMode, SizeToContent, GridLength, GridUnitType, Point, TextTrimming)
from System.Windows.Threading import DispatcherPriority
from System.Windows.Controls import (StackPanel, WrapPanel, TextBlock, CheckBox, RadioButton, ComboBox, TextBox, Button,
                                     Border, ScrollViewer, ScrollBarVisibility, Orientation, Grid, ColumnDefinition,
                                     ListBox, Canvas, GridSplitter, SelectionMode)
from System.Windows.Media import (SolidColorBrush, Color, ScaleTransform, RotateTransform, TranslateTransform,
                                  TransformGroup, DoubleCollection, PointCollection)
from System.Windows.Shapes import Line as WLine, Ellipse, Polygon
from System.Windows.Input import Keyboard, ModifierKeys, Key, Cursors

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
FLOW_CFG = dict(CFG.get('flow') or {})          # {ключ изделия: {отверстие: 'in' | 'out' | 'none'}} — выбор db
if not CFG.get('scale_v2'):                      # 30.10 db: 1:1 — размеров в превью не видно; по умолчанию «авто»
    CFG['scale'] = 0
    CFG['scale_v2'] = True


def save_cfg():
    if hui_settings is None:
        return
    CFG['faces'] = FACES_CFG
    CFG['flow'] = FLOW_CFG
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


def cam_vecs():
    """Ракурс 3D-вида окна (раздел 2) в координатах изделия: (вправо, вверх, к зрителю) — им же строится аксонометрия."""
    v3 = globals().get('V3') or {'yaw': float(CFG.get('yaw', -35.0)), 'pitch': float(CFG.get('pitch', 25.0))}
    a, b = math.radians(v3['yaw']), math.radians(v3['pitch'])
    return ((math.cos(a), -math.sin(a), 0.0),
            (math.sin(b) * math.sin(a), math.sin(b) * math.cos(a), math.cos(b)),
            (-math.cos(b) * math.sin(a), -math.cos(b) * math.cos(a), math.sin(b)))


def opt_of(it):
    E.apply_flow(it['part'], FLOW_CFG.get(it['key']))
    f = FACES_CFG.get(it['key'])
    o = {'views': list(CFG.get('views', E.DEFAULT_OPT['views'])), 'axo': CFG.get('axo', True),
         'az': float(CFG.get('az', 35.0)), 'el': float(CFG.get('el', 30.0)), 'axo_pos': CFG.get('axo_pos', 'middle'),
         'axo_mode': CFG.get('axo_mode', 'free'), 'axo_cam': cam_vecs()}
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
# строки как в «Распознать»: Border + щелчок мышью (ListBox с CheckBox внутри глотал щелчок — выбор только стрелками)
rows_panel = StackPanel()
rows_sv = ScrollViewer()
rows_sv.MaxHeight = 220
rows_sv.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
rows_sv.Content = rows_panel
rows_bd = Border()
rows_bd.BorderBrush = B(forms.LINE)
rows_bd.BorderThickness = Thickness(1)
rows_bd.Child = rows_sv
ROWS = []          # (border, it, cb)
MARK = {}          # id(it) → TextBlock «✓ эскиз»
LAST = [None]


def paint_row(r, it, cb, hover=False):
    if W.get('cur') is it:
        r.Background = B('#C8E6C9')
    elif hover:
        r.Background = B('#F1F8F2')
    elif cb.IsChecked:
        r.Background = B('#E8F5E9')
    else:
        r.Background = B('#FFFFFF')


def repaint_rows():
    for r, it, cb in ROWS:
        paint_row(r, it, cb)


def make_row(it):
    g = Grid()
    for w in (GridLength(24), GridLength(1, GridUnitType.Star), GridLength.Auto):
        cd = ColumnDefinition()
        cd.Width = w
        g.ColumnDefinitions.Add(cd)
    cb = CheckBox()
    cb.IsChecked = is_sel(it)
    cb.VerticalAlignment = VerticalAlignment.Center
    cb.ToolTip = u'Включить в эскизы'
    g.Children.Add(cb)
    t = TextBlock()
    t.Text = it['label']
    t.VerticalAlignment = VerticalAlignment.Center
    t.TextTrimming = TextTrimming.CharacterEllipsis
    t.ToolTip = it['label']
    Grid.SetColumn(t, 1)
    g.Children.Add(t)
    mk = TextBlock()
    mk.Foreground = B(forms.GREEN)
    mk.FontSize = 11
    mk.Margin = Thickness(8, 0, 0, 0)
    mk.VerticalAlignment = VerticalAlignment.Center
    mk.ToolTip = u'В проекте уже есть легенда-эскиз этого изделия'
    Grid.SetColumn(mk, 2)
    g.Children.Add(mk)
    MARK[id(it)] = mk
    r = Border()
    r.Padding = Thickness(6, 3, 6, 3)
    r.BorderBrush = B(forms.LINE)
    r.BorderThickness = Thickness(0, 0, 0, 1)
    r.Cursor = Cursors.Hand
    r.Child = g

    def on_click(s_, e_, r=r, it=it, cb=cb):
        from System.Windows.Media import VisualTreeHelper
        o_ = e_.OriginalSource
        while o_ is not None and o_ is not r:
            if o_ is cb:
                return                               # щелчок по галочке — только включить/выключить
            try:
                o_ = VisualTreeHelper.GetParent(o_)
            except Exception:
                o_ = getattr(o_, 'Parent', None)
        cur = next(i for i, x in enumerate(ROWS) if x[0] is r)
        if (Keyboard.Modifiers & ModifierKeys.Shift) == ModifierKeys.Shift and LAST[0] is not None:
            a, b_ = sorted((LAST[0], cur))
            st_ = not cb.IsChecked
            for x in ROWS[a:b_ + 1]:
                x[2].IsChecked = st_
            repaint_rows()
            return
        LAST[0] = cur
        select_item(it)
    r.MouseLeftButtonUp += on_click
    r.MouseEnter += lambda s_, e_, r=r, it=it, cb=cb: paint_row(r, it, cb, True)
    r.MouseLeave += lambda s_, e_, r=r, it=it, cb=cb: paint_row(r, it, cb)
    cb.Click += lambda s_, e_, r=r, it=it, cb=cb: paint_row(r, it, cb)
    paint_row(r, it, cb)
    rows_panel.Children.Add(r)
    ROWS.append((r, it, cb))
    W['rows'].append((it, cb))


for it in ITEMS:
    make_row(it)
rows_bd.Focusable = True


def on_rows_key(s_, e_):
    if W.get('cur') is None or e_.Key not in (Key.Up, Key.Down, Key.Space):
        return
    i = next(i for i, x in enumerate(ROWS) if x[1] is W['cur'])
    if e_.Key == Key.Space:
        ROWS[i][2].IsChecked = not ROWS[i][2].IsChecked
        repaint_rows()
    else:
        j = max(0, min(len(ROWS) - 1, i + (1 if e_.Key == Key.Down else -1)))
        if j != i:
            select_item(ROWS[j][1])
            ROWS[j][0].BringIntoView()
    e_.Handled = True


rows_bd.PreviewKeyDown += on_rows_key
s1.Children.Add(rows_bd)
p1 = hpanel(Thickness(0, 6, 0, 0))


def set_all(v):
    for it, cb in W['rows']:
        cb.IsChecked = v(it)
    repaint_rows()


p1.Children.Add(btn(u'Все', lambda s, e: set_all(lambda it: True)))
p1.Children.Add(btn(u'Ничего', lambda s, e: set_all(lambda it: False)))
p1.Children.Add(btn(u'Выделенные', lambda s, e: set_all(is_sel),
                    tip=u'Отметить типоразмеры элементов, выделенных в модели'))
p1.Children.Add(btn(u'С эскизом', lambda s, e: set_all(has_sketch),
                    tip=u'Отметить изделия, у которых уже есть легенда-эскиз, — «Создать» пересоберёт их все'))
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
s2.Children.Add(txt(u'По умолчанию — грань с наибольшим числом ответвлений (КСД и другие по геометрии — «Спереди», '
                    u'как смонтировано). Выбор запоминается для конфигурации (db_Код конфигурации).', muted=True, size=11))
s2.Children.Add(txt(u'Открытые грани', bold=True, margin=Thickness(0, 10, 0, 2)))
cv3_border = Border()
cv3_border.BorderBrush = B(u'#C8D3CA')
cv3_border.BorderThickness = Thickness(1)
cv3_border.Height = 260
cv3 = Canvas()
cv3.Background = B(u'#FFFFFF')
cv3.ClipToBounds = True
cv3_border.Child = cv3
s2.Children.Add(cv3_border)
s2.Children.Add(txt(u'Навести на грань и щёлкнуть — открыть / закрыть. Тянуть — повернуть, колесо — приблизить.',
                    muted=True, size=11))
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
p3m = hpanel()
p3m.Children.Add(txt(u'ракурс', margin=Thickness(18, 3, 6, 0)))
AXO_MODES = [u'как 3D-вид в разделе 2', u'ГОСТ: фронтальная изометрия']
cb_mode = combo(AXO_MODES, AXO_MODES[1] if CFG.get('axo_mode') == 'gost' else AXO_MODES[0], 230)
p3m.Children.Add(cb_mode)
s3.Children.Add(p3m)
p3 = hpanel()
p3.Children.Add(txt(u'азимут °', margin=Thickness(18, 3, 6, 0)))


def set_cfg(k, v):
    CFG[k] = v
    save_cfg()                      # 30.10 db: настройки слетали — сохраняем сразу
    refresh()


p3.Children.Add(num_field(float(CFG.get('az', 35)), -180, 180, 5, lambda v: set_cfg('az', v),
                          u'Поворот взгляда вокруг вертикали от главного вида: + — справа, − — слева. ↑/↓ — по 5°'))
p3.Children.Add(txt(u'наклон °', margin=Thickness(14, 3, 6, 0)))
p3.Children.Add(num_field(float(CFG.get('el', 30)), -80, 80, 5, lambda v: set_cfg('el', v),
                          u'Взгляд сверху (+) или снизу (−). ↑/↓ — по 5°'))
s3.Children.Add(p3)
p3.Visibility = Visibility.Collapsed     # 30.10 db: подбор цифрами неудобен — ракурс задаётся 3D-видом и видовым кубом


def _cube_dir(key):
    it = W.get('cur')
    if it is None:
        return None
    f = FACES_CFG.get(it['key'])
    R, U, N = E.main_basis(f[0] if f else E.default_face(it['part']), int(f[1]) if f else 0)
    if it['part'].get('rot180'):
        fl = lambda v: (-v[0], -v[1], v[2])
        R, U, N = fl(R), fl(U), fl(N)
    m = {u'Спереди': N, u'Сзади': E.vmul(N, -1), u'Слева': E.vmul(R, -1), u'Справа': R, u'Сверху': U,
         u'Снизу': E.vmul(U, -1)}
    if key in m:
        return m[key]
    sx = {u'Изо ↗': 1, u'Изо ↖': -1, u'Изо ↘': 1, u'Изо ↙': -1}[key]
    sy = -1 if key in (u'Изо ↘', u'Изо ↙') else 1
    return E.vadd(N, E.vmul(R, sx), E.vmul(U, sy))


cube = WrapPanel()
cube.Margin = Thickness(18, 4, 0, 2)
for k_ in (u'Спереди', u'Сзади', u'Слева', u'Справа', u'Сверху', u'Снизу', u'Изо ↗', u'Изо ↖', u'Изо ↘', u'Изо ↙'):
    def _mk(k__):
        def h(s_, e_):
            d_ = _cube_dir(k__)
            if d_ is not None:
                set_cam(d_)
        return h
    b_ = btn(k_, _mk(k_), u'Видовой куб: взгляд на изделие ' + k_.lower() + u' (относительно главного вида)')
    b_.Margin = Thickness(0, 0, 4, 4)
    cube.Children.Add(b_)
s3.Children.Add(cube)
cube.Visibility = Visibility.Collapsed       # 30.10 db: видовой куб — в углу 3D-вида раздела 2
s3.Children.Add(txt(u'Ракурс аксонометрии — как 3D-вид в разделе 2: тяните модель мышью или щёлкните видовой куб '
                    u'в его углу (грань, ребро, угол). Размеры Revit на свободном ракурсе — длина на листе (рёбра под углом '
                    u'сокращены). ГОСТ: все три оси без сокращения — размеры верные.', muted=True, size=11,
                    margin=Thickness(18, 2, 0, 0)))
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
r_each.Content = u'Легенда на каждое изделие'
r_one = RadioButton()
r_one.Content = u'Одна легенда со всеми отмеченными (сводная / своя)'
r_each.IsChecked = True
r_one.Visibility = Visibility.Collapsed
s4.Children.Add(r_each)
p4m = hpanel(Thickness(18, 2, 0, 4))
p4m.Children.Add(txt(u'чем рисовать', margin=Thickness(0, 3, 6, 0)))
LG_MODES = [u'линиями — как в превью (размеры Revit по линиям)', u'компонентами легенды Revit']
cb_lgm = combo(LG_MODES, LG_MODES[1] if CFG.get('lg_mode') == 'comp' else LG_MODES[0], 300)
p4m.Children.Add(cb_lgm)
s4.Children.Add(p4m)
p4 = hpanel(Thickness(18, 2, 0, 6))
p4.Children.Add(txt(u'имя: префикс', margin=Thickness(0, 3, 6, 0)))
tb_pre = TextBox()
tb_pre.Text = (u'db_Эскиз_' if CFG.get('prefix', u'Эскиз_') in (u'Эскиз_', u'') else CFG.get('prefix'))   # 30.10: всё своё — с db_
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
p4b.Visibility = Visibility.Collapsed
s4.Children.Add(p4b)
s4.Children.Add(txt(u'Линиями — легенда в точности как превью справа (виды, подписи, стрелки, аксонометрия под своим углом), '
                    u'размеры — Revit, привязаны к линиям. Компонентами — виды из семейства (компоненты легенды), '
                    u'аксонометрия — линиями. Есть такая легенда — пересобирается («✓ эскиз»; «С эскизом» — отметить все).', muted=True, size=11))
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
                el.Fill = GRAY if itm[3] == 'gray' else (None if itm[3] == 'hatch' else INK)   # штриховка — линиями
            cv.Children.Add(el)
        elif t == 'poly':
            pg = Polygon()
            pc = PointCollection()
            for q in itm[1]:
                pc.Add(Point(X(q), Y(q)))
            pg.Points = pc
            pg.Fill = GRAY if itm[2] == 'gray' else (None if itm[2] == 'hatch' else INK)   # штриховка — линиями
            cv.Children.Insert(0, pg) if itm[2] in ('gray', 'hatch') else cv.Children.Add(pg)
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


def _log_err(what):
    try:
        import traceback, io as _io, os
        with _io.open(os.path.join(os.environ.get('TEMP', ''), u'zb_eskiz_err.txt'), 'a', encoding='utf-8') as f_:
            f_.write(u'--- %s\n%s\n' % (what, traceback.format_exc()))
    except Exception:
        pass


def refresh(*a):
    try:
        draw_preview()
    except Exception as ex:
        _log_err(u'превью')
        W['sc_note'].Text = u'превью не построено: %s (подробности — %%TEMP%%\\zb_eskiz_err.txt)' % ex
    try:
        draw3d()
    except Exception as ex:
        W['sc_note'].Text = u'3D не построено: %s' % ex


AUTO_RU = {'in': u'вход', 'out': u'выход', None: u'не определено'}
V3 = {'yaw': float(CFG.get('yaw', -35.0)), 'pitch': float(CFG.get('pitch', 25.0)), 'zoom': 1.0, 'drag': None}


def set_cam(d):
    """Видовой куб: взгляд из направления d (к зрителю, координаты изделия)."""
    d = E.unit(d)
    V3['pitch'] = max(-89.0, min(89.0, math.degrees(math.asin(max(-1.0, min(1.0, d[2]))))))
    if abs(d[0]) + abs(d[1]) > 1e-6:
        V3['yaw'] = math.degrees(math.atan2(-d[0], -d[1]))
    cam_changed()


def cam_changed():
    CFG['yaw'], CFG['pitch'] = V3['yaw'], V3['pitch']
    save_cfg()
    draw3d()
    if CFG.get('axo_mode', 'free') == 'free':
        refresh()
RGB_OPEN, RGB_BOX, RGB_HL = (70, 110, 150), (200, 214, 203), (255, 204, 128)


def _shade(rgb, lit, a=255):
    return SolidColorBrush(Color.FromArgb(a, *[int(max(0, min(255, c * lit))) for c in rgb]))


def _proj(p):
    a, b = math.radians(V3['yaw']), math.radians(V3['pitch'])
    x, y, z = p
    x1 = x * math.cos(a) - y * math.sin(a)
    y1 = x * math.sin(a) + y * math.cos(a)
    y2 = y1 * math.cos(b) - z * math.sin(b)
    z2 = y1 * math.sin(b) + z * math.cos(b)
    return x1, -z2, y2


def _facing(n):
    return -_proj(n)[2]


def set_flow(cid, v):
    it = W['cur']
    if it is None:
        return
    d = FLOW_CFG.setdefault(it['key'], {})
    if v:
        d[cid] = v
    else:
        d.pop(cid, None)
    save_cfg()
    refresh()


def flow_menu(pg, c=None, face=None):
    try:
        _flow_menu(pg, c, face)
    except Exception as ex:
        forms.alert(u'Меню входа / выхода не открылось: %s' % ex, title=u'Эскиз')


def _flow_menu(pg, c=None, face=None):
    """Правый щелчок: по отверстию — вход / выход / не указывать / авто; по грани (или открытой целиком грани) —
    «вся грань — вход / выход / закрыта»."""
    from System.Windows.Controls import ContextMenu, MenuItem
    cm = ContextMenu()
    if c is not None and c.get('user'):
        face, c = c['face'], None
    if c is not None:
        return                                   # патрубки — из семейства, менять нечего
    cid = u'face:' + face
    items = ((u'Открыта (отверстие во всю грань)', 'open'), (u'Закрыта', None))
    head = u'Грань «%s»' % face
    cur = (FLOW_CFG.get(W['cur']['key']) or {}).get(cid)
    if cur in ('in', 'out'):
        cur = 'open'
    for title, v in items:
        mi = MenuItem()
        mi.Header = title
        mi.IsCheckable = True
        mi.IsChecked = (cur == v)

        def on_click(s_, e_, v=v):
            set_flow(cid, v)
        mi.Click += on_click
        cm.Items.Add(mi)
    hd = MenuItem()
    hd.Header = head
    hd.IsEnabled = False
    cm.Items.Insert(0, hd)
    cm.PlacementTarget = pg
    cm.IsOpen = True


def draw3d():
    cv3.Children.Clear()
    W['tags'] = []
    it = W['cur']
    Wd, Hd = cv3.ActualWidth, cv3.ActualHeight
    if it is None or Wd < 30 or Hd < 30:
        return
    pt = it['part']
    Wb, Db, Hb = pt['W'], pt['D'], pt['H']
    polys = []                 # (группа, точки, rgb, яркость, срез или None)
    allp = []
    for f in E.FACES:
        o, u, v, n = E.face_frame(f, Wb, Db, Hb)
        su, sv = E.face_size(f, Wb, Db, Hb)
        pts = [o, E.vadd(o, E.vmul(u, su)), E.vadd(o, E.vmul(u, su), E.vmul(v, sv)), E.vadd(o, E.vmul(v, sv))]
        allp += pts
        fc = _facing(n)
        opened = any(c.get('full') and c['face'] == f and c['l'] <= 0.5 for c in pt['cuts'])
        if fc > 0:
            polys.append((1, pts, RGB_OPEN if opened else RGB_BOX, 0.6 + 0.4 * fc, f))
    for c in pt['cuts']:
        rgb = RGB_OPEN
        if c.get('full') and c['l'] <= 0.5:
            continue                                 # открытая грань — сама грань короба цветом отверстия
        if c.get('full'):
            o, u, v, n = E.face_frame(c['face'], Wb, Db, Hb)
            su, sv = E.face_size(c['face'], Wb, Db, Hb)
            ring = [E.vadd(E.vmul(u, x), E.vmul(v, y)) for x, y in ((-su / 2, -sv / 2), (su / 2, -sv / 2), (su / 2, sv / 2), (-su / 2, sv / 2))]
        elif c['shape'] in ('c', 'o'):
            ring = [E.vadd(E.vmul(c['u3'], x), E.vmul(c['v3'], y)) for x, y in E.stadium_pts((0, 0), c['w'] / 2.0, c['h'] / 2.0, 10)]
        else:
            hw, hh = c['w'] / 2.0, c['h'] / 2.0
            ring = [E.vadd(E.vmul(c['u3'], x), E.vmul(c['v3'], y)) for x, y in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
        n = c['n3']
        base, tip = c['o3'], c['t3']
        front = _facing(n) > 0
        grp = 2 if front else 0
        m = len(ring)
        if c['l'] > 0.5:
            for i in range(m):
                a0, a1 = ring[i], ring[(i + 1) % m]
                nn = E.unit(E.vadd(a0, a1)) if E.vlen(E.vadd(a0, a1)) > 1e-6 else n
                if _facing(nn) <= 0:
                    continue
                polys.append((grp, [E.vadd(base, a0), E.vadd(base, a1), E.vadd(tip, a1), E.vadd(tip, a0)],
                              RGB_BOX, 0.55 + 0.45 * _facing(nn), None))
        cap = [E.vadd(tip, a) for a in ring]
        allp += cap
        polys.append((grp + 0.5 if front else 0.5, cap, rgb, 1.0 if front else 0.6, c))
    pr = [_proj(q) for q in allp]
    minx, maxx = min(q[0] for q in pr), max(q[0] for q in pr)
    miny, maxy = min(q[1] for q in pr), max(q[1] for q in pr)
    k = min((Wd - 60) / max(1e-6, maxx - minx), (Hd - 60) / max(1e-6, maxy - miny)) * V3['zoom']
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0

    def scr(q):
        r = _proj(q)
        return Point(Wd / 2 + (r[0] - cx) * k, Hd / 2 + (r[1] - cy) * k)
    polys.sort(key=lambda t: (t[0], -sum(_proj(q)[2] for q in t[1]) / len(t[1])))
    edge = B(u'#55635A')
    for grp, pts, rgb, lit, c in polys:
        pg = Polygon()
        for q in pts:
            pg.Points.Add(scr(q))
        pg.Fill = _shade(rgb, lit, 235 if not isinstance(c, dict) else 255)
        pg.Stroke, pg.StrokeThickness = edge, (1.4 if isinstance(c, dict) else 0.8)
        if isinstance(c, (str, unicode)):            # грань короба
            pg.ToolTip = u'Грань «%s» — щелчок: открыть / закрыть' % c
            pg.Cursor = Cursors.Hand

            def on_in(s_, e_, pg=pg):
                W['hl'] = (pg, pg.Fill, pg.Stroke, pg.StrokeThickness)
                pg.Fill = _shade(RGB_HL, 1.0, 255)
                pg.Stroke, pg.StrokeThickness = B(u'#E65100'), 2.5

            def on_out(s_, e_, pg=pg):
                h = W.get('hl')
                if h and h[0] is pg:
                    pg.Fill, pg.Stroke, pg.StrokeThickness = h[1], h[2], h[3]
                    W['hl'] = None
            pg.MouseEnter += on_in
            pg.MouseLeave += on_out
            W['tags'].append((pg, None, c))
            pg.Tag = len(W['tags']) - 1

            def on_rcf(s_, e_, f=c, pg=pg):
                e_.Handled = True
                flow_menu(pg, face=f)
            pg.MouseRightButtonUp += on_rcf
        elif c is not None:
            pg.Cursor = Cursors.Hand
            pg.ToolTip = E.cut_title(c)
            W['tags'].append((pg, c, None))
            pg.Tag = len(W['tags']) - 1

            def on_rc(s_, e_, c=c, pg=pg):
                e_.Handled = True
                flow_menu(pg, c)
            pg.MouseRightButtonUp += on_rc
        cv3.Children.Add(pg)
    # подписи граней
    for f in E.FACES:
        o, u, v, n = E.face_frame(f, Wb, Db, Hb)
        if _facing(n) > 0.3:
            su, sv = E.face_size(f, Wb, Db, Hb)
            q = scr(E.vadd(o, E.vmul(u, su / 2), E.vmul(v, sv / 2)))
            t = TextBlock()
            t.Text, t.FontSize, t.Foreground, t.IsHitTestVisible = f, 10, B(forms.MUTE), False
            Canvas.SetLeft(t, q.X - 20)
            Canvas.SetTop(t, q.Y - 7)
            cv3.Children.Add(t)
    _draw_cube(it, Wd)


def _basis_of(it):
    f = FACES_CFG.get(it['key'])
    R, U, N = E.main_basis(f[0] if f else E.default_face(it['part']), int(f[1]) if f else 0)
    if it['part'].get('rot180'):
        fl = lambda v: (-v[0], -v[1], v[2])
        R, U, N = fl(R), fl(U), fl(N)
    return R, U, N


def _draw_cube(it, Wd):
    """Видовой куб как в Revit (db 30.10): светлый куб с подписями граней без лишних линий; активные зоны —
    середина грани, полоса у ребра, уголок — подсвечиваются голубым целиком (ребро — на обеих гранях, угол —
    на трёх); щелчок — точный вид (6 + 12 + 8). Под кубом — кольцо, рядом — «домик» (исходный вид).
    Стороны — относительно главного вида."""
    R, U, N = _basis_of(it)
    ax = {'R': R, 'U': U, 'N': N}
    names = {('N', 1): u'СПЕРЕДИ', ('N', -1): u'СЗАДИ', ('R', 1): u'СПРАВА', ('R', -1): u'СЛЕВА',
             ('U', 1): u'СВЕРХУ', ('U', -1): u'СНИЗУ'}
    keys = ['R', 'U', 'N']
    h = 22.0
    ox, oy = Wd - 50.0, 50.0
    BAND = 0.7                                    # граница зоны ребра/угла (доля полуребра)

    def P(c):
        return E.vadd(E.vmul(R, c.get('R', 0)), E.vmul(U, c.get('U', 0)), E.vmul(N, c.get('N', 0)))

    def sp(p):
        r = _proj(p)
        return Point(ox + r[0] * h, oy + r[1] * h)
    groups = {}
    labels = []
    for k in keys:
        o1, o2 = [x for x in keys if x != k]
        for sg in (1, -1):
            n = E.vmul(ax[k], sg)
            fc = _facing(n)
            if fc <= 0.01:
                continue
            face = Polygon()
            for a_, b_ in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                face.Points.Add(sp(P({k: sg, o1: a_, o2: b_})))
            face.Fill = _shade((236, 241, 238), 0.62 + 0.38 * fc, 255)   # грани заметно разной яркости (db 30.10)
            face.Stroke, face.StrokeThickness, face.IsHitTestVisible = B(u'#55635A'), 1.3, False
            cv3.Children.Add(face)
            if fc > 0.3:
                labels.append((sp(n), names[(k, sg)], fc))
            # активные зоны 3×3 (без линий): середина — грань, полосы — рёбра, уголки — углы
            rng = {-1: (-1.0, -BAND), 0: (-BAND, BAND), 1: (BAND, 1.0)}
            for i in (-1, 0, 1):
                for j in (-1, 0, 1):
                    zone = Polygon()
                    for a_, b_ in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        zone.Points.Add(sp(P({k: sg, o1: rng[i][a_], o2: rng[j][b_]})))
                    zone.Fill = _shade((255, 255, 255), 1.0, 1)        # прозрачная, но ловит мышь
                    d = E.vadd(n, E.vmul(ax[o1], i), E.vmul(ax[o2], j))
                    key_ = tuple(int(round(x)) for x in d)
                    zone.Tag = ('cube', d)
                    zone.Cursor = Cursors.Hand
                    kind = u'Вид ' + names[(k, sg)].lower() if not (i or j) else (u'Ребро' if not (i and j) else u'Угол — изометрия')
                    zone.ToolTip = kind
                    groups.setdefault(key_, []).append(zone)
    hl = B(u'#9CC7EE')

    def on_in(s_, e_, key_=None):
        for z in groups.get(s_.Tag and tuple(int(round(x)) for x in s_.Tag[1]), []):
            z.Fill = hl

    def on_out(s_, e_):
        for z in groups.get(tuple(int(round(x)) for x in s_.Tag[1]), []):
            z.Fill = _shade((255, 255, 255), 1.0, 1)
    for zs in groups.values():
        for z in zs:
            z.MouseEnter += on_in
            z.MouseLeave += on_out
            cv3.Children.Add(z)
    for q, name, fc in labels:
        tb = TextBlock()
        tb.Text, tb.FontSize, tb.Foreground, tb.IsHitTestVisible = name, 7.5, B(u'#3C4A42'), False
        tb.Measure(System.Windows.Size(200, 50))
        Canvas.SetLeft(tb, q.X - tb.DesiredSize.Width / 2.0)
        Canvas.SetTop(tb, q.Y - tb.DesiredSize.Height / 2.0)
        cv3.Children.Add(tb)



def on3_down(s_, e_):
    V3['drag'] = e_.GetPosition(cv3)
    V3['start'] = V3['drag']
    V3['src'] = e_.OriginalSource
    cv3.CaptureMouse()


def on3_move(s_, e_):
    if V3['drag'] is None:
        return
    p = e_.GetPosition(cv3)
    p0 = V3.get('start')
    if p0 is not None and abs(p.X - p0.X) + abs(p.Y - p0.Y) <= 4:
        return                                   # ещё щелчок, не поворот
    V3['yaw'] += (p.X - V3['drag'].X) * 0.5
    V3['pitch'] = max(-89.0, min(89.0, V3['pitch'] + (p.Y - V3['drag'].Y) * 0.5))
    V3['drag'] = p
    draw3d()


def on3_up(s_, e_):
    V3['drag'] = None
    cv3.ReleaseMouseCapture()
    p0, p1 = V3.get('start'), e_.GetPosition(cv3)
    if p0 is None or abs(p1.X - p0.X) + abs(p1.Y - p0.Y) > 4:
        cam_changed()                            # 30.10 db: повернули 3D — аксонометрия под тем же углом
        return                                   # тянули — поворот, не щелчок
    src = V3.get('src')
    i = getattr(src, 'Tag', None) if isinstance(src, (Polygon, TextBlock)) else None
    if isinstance(i, tuple) and i and i[0] == 'cube':
        set_cam(i[1])                            # видовой куб
        return
    tags = W.get('tags') or []
    if not isinstance(i, int) or not (0 <= i < len(tags)):
        return
    pg, c, f = tags[i]
    if c is not None and c.get('user'):
        f = c['face']
    if f is None:
        return                                   # патрубок — из семейства
    cid = u'face:' + f
    cur = (FLOW_CFG.get(W['cur']['key']) or {}).get(cid)
    set_flow(cid, None if cur else 'open')       # щелчок — открыть / закрыть грань


def on3_wheel(s_, e_):
    if Keyboard.Modifiers & ModifierKeys.Control == ModifierKeys.Control:
        return                                   # Ctrl+колесо — масштаб окна
    V3['zoom'] = max(0.4, min(4.0, V3['zoom'] * (1.1 if e_.Delta > 0 else 1 / 1.1)))
    e_.Handled = True
    draw3d()


cv3.MouseLeftButtonDown += on3_down
cv3.MouseMove += on3_move
cv3.MouseLeftButtonUp += on3_up
cv3.MouseWheel += on3_wheel
cv3.SizeChanged += lambda s_, e_: draw3d()


def pick_item(it):
    W['cur'] = it
    W['busy'] = True
    o = opt_of(it)
    cb_face.SelectedItem = o['face']
    cb_rot.SelectedItem = u'%d°' % o['rot']
    W['busy'] = False
    b2.Child.Children[0].Text = u'2. Главный вид — %s' % it['fam']
    refresh()


def select_item(it):
    """Подсветка строки — сразу, превью — следом (не держит мышь), повторный щелчок по той же строке — ничего."""
    rows_bd.Focus()
    if W.get('cur') is it:
        return
    W['cur'] = it
    repaint_rows()
    W['pending'] = it

    def later():
        if W.get('pending') is it:
            W['pending'] = None
            pick_item(it)
    dlg.Dispatcher.BeginInvoke(DispatcherPriority.Background, System.Action(later))


def on_face(s, e):
    if W.get('busy') or W['cur'] is None:
        return
    FACES_CFG[W['cur']['key']] = [unicode(cb_face.SelectedItem), int(unicode(cb_rot.SelectedItem).replace(u'°', u''))]
    save_cfg()
    refresh()


def on_views(s, e):
    CFG['views'] = [k for k, c in VIEW_CB.items() if c.IsChecked]
    CFG['axo'] = bool(cb_axo.IsChecked)
    CFG['axo_pos'] = 'top' if cb_pos.SelectedItem == AXO_POS[1] else 'middle'
    CFG['axo_mode'] = 'gost' if cb_mode.SelectedItem == AXO_MODES[1] else 'free'
    v = cb_sc.SelectedItem
    CFG['scale'] = 0 if v == u'авто' else float(v)
    CFG['lg_mode'] = 'comp' if cb_lgm.SelectedItem == LG_MODES[1] else 'lines'
    save_cfg()
    refresh()


cb_face.SelectionChanged += on_face
cb_rot.SelectionChanged += on_face
for c in list(VIEW_CB.values()) + [cb_axo]:
    c.Click += on_views
cb_pos.SelectionChanged += on_views
cb_mode.SelectionChanged += on_views
cb_lgm.SelectionChanged += on_views
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


def plan_jobs(chosen):
    """[(имя легенды, масштаб, изделия)] по настройкам раздела 4 — общее для «Создать» и «Открыть легенду»."""
    jobs = []
    if CFG['target'] == 'one':
        S = scale_for([it['part'] for it in chosen])
        jobs.append((clean(CFG['one_name']), S, chosen))
    else:
        fams = {}
        for it in chosen:
            fams[it['fam']] = fams.get(it['fam'], 0) + 1
        for it in chosen:
            fam_ = it['fam']
            if CFG['prefix'].startswith(u'db_') and fam_.startswith(u'db_'):
                fam_ = fam_[3:]                  # «db_Эскиз_Пленум_…», а не «db_Эскиз_db_Пленум_…»
            nm = CFG['prefix'] + fam_
            if fams[it['fam']] > 1 or len([x for x in ITEMS if x['fam'] == it['fam']]) > 1:
                nm += u'_' + it['tname']
            jobs.append((clean(nm), scale_for([it['part']]), [it]))
    return jobs


def inst_of(it):
    """Экземпляр изделия: выделенный (КСД и др.) или любой этого типоразмера (пленумы)."""
    if it.get('ids'):
        return doc.GetElement(DB.ElementId(it['ids'][0]))
    for fi in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance):
        if fi.Symbol is not None and fi.Symbol.Id == it['sym'].Id:
            return fi
    return None


LEG_NAMES = set()


def read_legends():
    LEG_NAMES.clear()
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if v.ViewType == DB.ViewType.Legend and not v.IsTemplate:
            LEG_NAMES.add(v.Name)


def has_sketch(it):
    try:
        return any(nm in LEG_NAMES for nm, S, its in plan_jobs([it]))
    except Exception:
        return False


def update_marks():
    CFG['target'] = 'each'
    CFG['prefix'] = tb_pre.Text or u''
    read_legends()
    for r_, it, cb in ROWS:
        MARK[id(it)].Text = u'✓ эскиз' if has_sketch(it) else u''


@guarded
def do_create(s, e):
    chosen = [it for it, cb in W['rows'] if cb.IsChecked]
    if not chosen:
        status(u'Не отмечено ни одного изделия (раздел 1).', ok=False)
        return
    CFG['target'] = 'each'
    CFG['prefix'] = tb_pre.Text or u''
    errs, made, names = [], [], []
    CFG['lg_mode'] = 'comp' if cb_lgm.SelectedItem == LG_MODES[1] else 'lines'
    with revit.Transaction(u'ХУЙ: эскиз'):
        st = E.styles(doc)
        for nm, S, its in plan_jobs(chosen):
            try:
                lg = E.legend_for(doc, nm, S)
                if CFG['lg_mode'] == 'lines':       # 30.10 db: линиями информативнее — как превью
                    errs += E.emit(doc, lg, E.build_many([(it['part'], opt_of(it)) for it in its], S), st)
                else:
                    LG.build(doc, lg, [(it, opt_of(it)) for it in its], S, st, errs)
                made.append(lg)
                names.append(nm)
            except Exception as ex:
                errs.append(u'%s: %s' % (nm, ex))
        # таблица врезок пленумов (живая спецификация по общим параметрам db_Вi_*) — если среди отмеченных есть пленум
        if any(it['sym'].LookupParameter(u'db_В1_Размер') is not None for it in chosen):
            try:
                LG.spigot_schedule(doc, errs)
                names.append(LG.SPIG_NAME)
            except Exception as ex:
                errs.append(u'таблица врезок: %s' % ex)
    W['made'] = made
    save_cfg()
    update_marks()
    msg = u'Готово: %d легенд(ы) — %s. Отмена — Ctrl+Z.' % (len(made), u', '.join(names))
    if errs:
        msg += u' Замечаний: %d (первое: %s)' % (len(errs), errs[0])
    status(msg, ok=not errs)


@guarded
def do_create_legend(s, e):
    chosen = [it for it, cb in W['rows'] if cb.IsChecked]
    if not chosen:
        status(u'Не отмечено ни одного изделия (раздел 1).', ok=False)
        return
    CFG['target'] = 'one' if r_one.IsChecked else 'each'
    CFG['prefix'] = tb_pre.Text or u''
    CFG['one_name'] = tb_one.Text or u'Эскизы_сводная'
    jobs = plan_jobs(chosen)
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
b_open = btn(u'Открыть легенду', None, tip=u'Закрыть окно и открыть легенду-эскиз: созданную только что, иначе — существующую для выбранного изделия')


@guarded
def on_open(s, e):
    """Созданная в этом окне легенда; если ещё ничего не создавали — существующая легенда выбранного изделия."""
    tgt = W['made'][-1] if W.get('made') else None
    if tgt is None:
        CFG['target'] = 'one' if r_one.IsChecked else 'each'
        CFG['prefix'] = tb_pre.Text or u''
        CFG['one_name'] = tb_one.Text or u'Эскизы_сводная'
        chosen = [it for it, cb in W['rows'] if cb.IsChecked]
        if W.get('cur') is not None and CFG['target'] == 'each':
            chosen = [W['cur']]
        for nm, S, its in (plan_jobs(chosen) if chosen else []):
            tgt = E.find_legend(doc, nm)
            if tgt is not None:
                break
    if tgt is None:
        status(u'Легенды для этого изделия ещё нет — сначала «Создать».', ok=False)
        return
    W['open_view'] = tgt
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
    try:                                         # 30.10 db: масштаб и режимы — с контролов, что бы ни было раньше
        v_ = cb_sc.SelectedItem
        CFG['scale'] = 0 if v_ == u'авто' else float(v_)
        CFG['axo_mode'] = 'gost' if cb_mode.SelectedItem == AXO_MODES[1] else 'free'
        CFG['lg_mode'] = 'comp' if cb_lgm.SelectedItem == LG_MODES[1] else 'lines'
    except Exception:
        pass
    CFG['left_w'] = c0.ActualWidth
    save_cfg()


dlg.PreviewMouseWheel += on_zoom_wheel
dlg.PreviewKeyDown += on_zoom_key
dlg.Closing += on_closing
first = [r for r in W['rows'] if r[1].IsChecked] or W['rows']
if first:
    pick_item(first[0][0])
    repaint_rows()
try:
    update_marks()
except Exception:
    pass
# окно — поверх Revit и на его мониторе (30.10: окно открывалось где-то вне глаз, Revit «серел» и ждал)
try:
    import System.Diagnostics
    from System.Windows.Interop import WindowInteropHelper
    from System.Windows import WindowStartupLocation as _WSL
    _hw = System.Diagnostics.Process.GetCurrentProcess().MainWindowHandle
    WindowInteropHelper(dlg).Owner = _hw
    dlg.WindowStartupLocation = _WSL.CenterOwner
    dlg.MaxHeight = System.Windows.SystemParameters.WorkArea.Height
except Exception:
    pass


def _bring_front(s_, e_):
    try:
        dlg.Activate()
        dlg.Topmost = True
        dlg.Topmost = False
    except Exception:
        pass
dlg.ContentRendered += _bring_front
dlg.ShowDialog()
if W.get('open_view') is not None:
    try:
        uidoc.ActiveView = W['open_view']
    except Exception as ex:
        forms.alert(u'Не удалось открыть легенду «%s»: %s' % (W['open_view'].Name, ex), title=u'Эскиз')
