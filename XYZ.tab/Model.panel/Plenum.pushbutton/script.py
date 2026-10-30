# -*- coding: utf-8 -*-
"""Пленум: короб с врезками → семейство «Арматура воздуховодов».

Окно: размеры, список врезок. Соединители — двунаправленные: вход и выход Revit берёт из системы,
одно семейство годится и на приток, и на вытяжку (проверено 28.10.2026, режим убран).
Окно по стандарту «Объёмов»: тянется, разделитель, Ctrl+колесо — масштаб, размер запоминается.
Проверка: врезка влезает в грань (с отступом «От края»), врезки на одной
грани не пересекаются (зазор «Между врезками»), основная врезка ровно одна.
«Создать»: семейство из шаблона «типовая модель» → категория «Арматура
воздуховодов», короб + патрубки + соединители, параметры ADSK из ФОП,
свои параметры врезок db_В*, загрузка в проект и размещение.
Имя семейства — db_Пленум_ШxВxД_Nвр (N — врезок-ответвлений, основной присоединительный размер не считается); другая конфигурация с теми же габаритами → _2, _3. Одинаковая конфигурация (db_Код конфигурации) повторно не создаётся.
"""
import os
import math
import hashlib
import copy
import clr

from pyrevit import revit, DB, forms, script
import xyz_forms as forms   # единый стиль окон XYZ (lib/xyz_forms.py)
import sys
sys.modules.pop('xyz_viewcube', None)
import xyz_viewcube as VC   # видовой куб как в Revit (30.10.2026)
from Autodesk.Revit.DB.Mechanical import DuctSystemType
from System.Windows import Thickness, Point, Size
from System.Windows.Controls import Canvas, TextBlock, Button
from System.Windows.Input import Cursors, Keyboard, ModifierKeys, Key
from System.Windows.Media import ScaleTransform
from System.Windows import GridLength
from System.Windows.Threading import DispatcherTimer
from System.Windows.Controls import ScrollBarVisibility
from System import TimeSpan

try:
    import xyz_settings
    CFG = dict(xyz_settings.get('plenum') or {})
except Exception:
    xyz_settings = None
    CFG = {}


def save_cfg():
    if xyz_settings is not None:
        try:
            xyz_settings.set('plenum', CFG)
        except Exception:
            pass


def guarded(fn):
    """Ошибка в обработчике — окном с текстом, а не «нажал — ничего»."""
    def wrap(*a):
        try:
            return fn(*a)
        except Exception:
            import traceback
            forms.alert(u'Ошибка — действие не выполнено:\n\n' + traceback.format_exc()[-1500:], title=u'Пленум')
    return wrap
from System.Windows.Shapes import Rectangle, Ellipse, Polygon, Line as WLine
from System.Windows.Media import SolidColorBrush, Color

doc = revit.doc
uidoc = revit.uidoc
app = doc.Application
MM = 1.0 / 304.8
# 30.10.2026 db: свой ФОП плагина (те же GUID, что ФОП2021(КС), только нужные параметры) — лежит в расширении
FOP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), u'ФОП', u'db_ФОП.txt')
OUT_DIR = os.path.join(os.environ.get('TEMP', ''), u'db_Пленум')

FACES = [u'Спереди', u'Сзади', u'Слева', u'Справа', u'Сверху', u'Снизу']
SHAPES = [u'Круглый', u'Прямоугольный']
ROLES = [u'Ответвление', u'Основной']


def face_geom(face, W, D, H):
    """origin (левый нижний угол грани снаружи), u, v, нормаль, размер u, размер v."""
    X, Y, Z = DB.XYZ.BasisX, DB.XYZ.BasisY, DB.XYZ.BasisZ
    P = DB.XYZ
    if face == u'Спереди':
        return P(-W / 2, -D / 2, -H / 2), X, Z, -Y, W, H
    if face == u'Сзади':
        return P(W / 2, D / 2, -H / 2), -X, Z, Y, W, H
    if face == u'Слева':
        return P(-W / 2, D / 2, -H / 2), -Y, Z, -X, D, H
    if face == u'Справа':
        return P(W / 2, -D / 2, -H / 2), Y, Z, X, D, H
    if face == u'Сверху':
        return P(-W / 2, -D / 2, H / 2), X, Y, Z, W, D
    return P(-W / 2, D / 2, -H / 2), X, -Y, -Z, W, D   # Снизу


def face_size(face, W, D, H):
    if face in (u'Спереди', u'Сзади'):
        return W, H
    if face in (u'Слева', u'Справа'):
        return D, H
    return W, D


def num(tb, default=0.0):
    try:
        return float(tb.Text.replace(',', '.').strip())
    except Exception:
        return default


def fmt(x):
    return (u'%.1f' % x).rstrip('0').rstrip('.')


def cut_label(c):
    size = u'Ø%s' % fmt(c['d']) if c['shape'] == SHAPES[0] else u'%s×%s' % (fmt(c['w']), fmt(c['h']))
    return size


def full_face(c, W, D, H):
    """Прямоугольная врезка во всю грань — продолжение короба (переход без уступа)."""
    if c['shape'] != SHAPES[1]:
        return False
    su, sv = face_size(c['face'], W, D, H)
    return abs(c['w'] - su) < 0.5 and abs(c['h'] - sv) < 0.5


AXIS = {u'Спереди': 'D', u'Сзади': 'D', u'Слева': 'W', u'Справа': 'W', u'Сверху': 'H', u'Снизу': 'H'}


def overall(W, D, H, cuts):
    """Габарит: короб + длины врезок во всю грань (они — продолжение короба)."""
    add = {'W': 0.0, 'D': 0.0, 'H': 0.0}
    for c in cuts:
        if full_face(c, W, D, H):
            add[AXIS[c['face']]] += c['l']
    return W + add['W'], D + add['D'], H + add['H']


def half(c):
    if c['shape'] == SHAPES[0]:
        return c['d'] / 2.0, c['d'] / 2.0
    return c['w'] / 2.0, c['h'] / 2.0




LAYOUTS = [u'В ряд', u'2 ряда', u'3 ряда', u'Шахматка']
# Технологические минимумы (db 28.10.2026): жестянщику нужно место под крепёж патрубка
# (заклёпки, саморезы, отбортовка) — между стенками соседних патрубков и до края грани (фальц, угол).
MIN_GAP = 50.0
MIN_EDGE = 25.0


def grid(same, su, sv, gap, edge, nrows):
    """Сеткой в nrows рядов по порядку в списке: верхние ряды полные, каждый ряд по центру грани,
    между краями соседних — «Между врезками», ряды по центру грани по высоте."""
    k = len(same)
    nrows = min(nrows, k)
    per = int(math.ceil(k / float(nrows)))
    rows = [same[i:i + per] for i in range(0, k, per)]
    heights = [max(half(c)[1] * 2 for c in r) for r in rows]
    total_h = sum(heights) + gap * (len(rows) - 1)
    top = sv - max(edge, (sv - total_h) / 2.0)
    for r, h in zip(rows, heights):
        widths = [half(c)[0] * 2 for c in r]
        total = sum(widths) + gap * (len(r) - 1)
        pos = max(edge, (su - total) / 2.0)
        for c, w in zip(r, widths):
            c['u'] = round(pos + w / 2.0, 1)
            c['v'] = round(top - h / 2.0, 1)
            pos += w + gap
        top -= h + gap


def chess(same, su, sv, gap, edge):
    """Шахматка в 2 ряда (по порядку в списке): верхний ряд — ceil(n/2), нижний — между ними.
    Занимает грань с отступом «От края» по ширине и высоте; шаг не меньше «Между врезками»,
    ряды не ближе, чем нужно для зазора по диагонали (иначе проверка покажет красным)."""
    k = len(same)
    ntop = (k + 1) // 2
    top, bot = same[:ntop], same[ntop:]
    wm = max(half(c)[0] * 2 for c in same)
    hm = max(half(c)[1] * 2 for c in same)
    units = (ntop - 1) + (0.5 if len(bot) == ntop else 0.0)
    if len(bot) and ntop == 1:
        units = 0.5
    p = max(wm + gap, (su - 2 * edge - wm) / units) if units > 0 else 0.0
    round_ = all(c['shape'] == SHAPES[0] for c in same)
    if round_:
        dmin = math.sqrt(max(0.0, (wm + gap) ** 2 - (p / 2.0) ** 2)) + 0.3   # запас на округление до 0,1 мм
    else:
        dmin = 0.0 if p / 2.0 >= wm + gap else hm + gap
    dv = max(dmin, sv - 2 * edge - hm)
    x0 = (su - (units * p + wm)) / 2.0 + wm / 2.0
    for i, c in enumerate(top):
        c['u'], c['v'] = round(x0 + i * p, 1), round(sv / 2.0 + dv / 2.0, 1)
    for j, c in enumerate(bot):
        c['u'], c['v'] = round(x0 + p / 2.0 + j * p, 1), round(sv / 2.0 - dv / 2.0, 1)


# ---------------------------------------------------------------- геометрия для окна (мм, кортежи)
def face_frame(face, W, D, H):
    """То же, что face_geom, но в мм и кортежами: origin, u, v, n."""
    if face == u'Спереди':
        return (-W / 2, -D / 2, -H / 2), (1, 0, 0), (0, 0, 1), (0, -1, 0)
    if face == u'Сзади':
        return (W / 2, D / 2, -H / 2), (-1, 0, 0), (0, 0, 1), (0, 1, 0)
    if face == u'Слева':
        return (-W / 2, D / 2, -H / 2), (0, -1, 0), (0, 0, 1), (-1, 0, 0)
    if face == u'Справа':
        return (W / 2, -D / 2, -H / 2), (0, 1, 0), (0, 0, 1), (1, 0, 0)
    if face == u'Сверху':
        return (-W / 2, -D / 2, H / 2), (1, 0, 0), (0, 1, 0), (0, 0, 1)
    return (-W / 2, D / 2, -H / 2), (1, 0, 0), (0, -1, 0), (0, 0, -1)


def vadd(*vs):
    return tuple(sum(c) for c in zip(*vs))


def vmul(v, k):
    return tuple(c * k for c in v)


VIEWS = [(u'Спереди', 0, 0), (u'Сзади', 180, 0), (u'Слева', 90, 0), (u'Справа', -90, 0),
         (u'Сверху', 0, 89.9), (u'Снизу', 0, -89.9), (u'3D', -35, 28)]


# ---------------------------------------------------------------- окно
XAML = u'''
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="ПЛЕНУМ" Width="1180" Height="760" MinWidth="980" MinHeight="640"
        WindowStartupLocation="CenterScreen" FontFamily="Segoe UI" FontSize="13">
  <Window.Resources>
    <ResourceDictionary>
      <ResourceDictionary.MergedDictionaries>
        <ResourceDictionary Source="XYZ_THEME_URI"/>
      </ResourceDictionary.MergedDictionaries>
      <Style TargetType="TextBox" BasedOn="{StaticResource {x:Type TextBox}}"><Setter Property="Margin" Value="0,2,8,6"/></Style>
      <Style TargetType="TextBlock"><Setter Property="Foreground" Value="#7A847C"/></Style>
      <Style TargetType="ComboBox" BasedOn="{StaticResource {x:Type ComboBox}}"><Setter Property="Margin" Value="0,2,8,6"/></Style>
      <Style TargetType="Button" BasedOn="{StaticResource {x:Type Button}}"><Setter Property="Margin" Value="0,4,6,0"/><Setter Property="Padding" Value="12,5"/></Style>
    </ResourceDictionary>
  </Window.Resources>
  <Grid Margin="12">
    <Grid.ColumnDefinitions><ColumnDefinition x:Name="colLeft" Width="420" MinWidth="320"/><ColumnDefinition Width="Auto"/><ColumnDefinition Width="*" MinWidth="320"/></Grid.ColumnDefinitions>
    <ScrollViewer x:Name="svLeft" Grid.Column="0" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
    <StackPanel x:Name="leftRoot" Margin="0,0,8,0">
      <GroupBox Header="Короб, мм">
        <Grid>
          <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition/><ColumnDefinition/></Grid.ColumnDefinitions>
          <Grid.RowDefinitions><RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/></Grid.RowDefinitions>
          <TextBlock Text="Ширина (Ш)" Grid.Row="0" Grid.Column="0"/><TextBox x:Name="tbW" Text="600" Grid.Row="1" Grid.Column="0"/>
          <TextBlock Text="Высота (В)" Grid.Row="0" Grid.Column="1"/><TextBox x:Name="tbH" Text="250" Grid.Row="1" Grid.Column="1"/>
          <TextBlock Text="Длина (Д)" Grid.Row="0" Grid.Column="2"/><TextBox x:Name="tbD" Text="400" Grid.Row="1" Grid.Column="2"/>
          <TextBlock Text="Длина врезки" Grid.Row="2" Grid.Column="0"/><TextBox x:Name="tbLen" Text="50" Grid.Row="3" Grid.Column="0"/>
          <TextBlock Text="Между врезками" Grid.Row="2" Grid.Column="1" ToolTip="Не меньше 50 мм — место под крепёж патрубка"/><TextBox x:Name="tbGap" Text="50" Grid.Row="3" Grid.Column="1"/>
          <TextBlock Text="От края" Grid.Row="2" Grid.Column="2" ToolTip="Не меньше 25 мм — фальц, угол короба"/><TextBox x:Name="tbEdge" Text="25" Grid.Row="3" Grid.Column="2"/>
          <TextBlock Text="Наименование" Grid.Row="4" Grid.Column="0" Grid.ColumnSpan="2"/><TextBlock Text="Стенка, мм" Grid.Row="4" Grid.Column="2"/>
          <TextBox x:Name="tbName" Grid.Row="5" Grid.Column="0" Grid.ColumnSpan="2"/><TextBox x:Name="tbWall" Text="0.5" Grid.Row="5" Grid.Column="2"/>
        </Grid>
      </GroupBox>
      <GroupBox x:Name="gbCuts" Header="Врезки">
        <StackPanel>
          <WrapPanel Margin="0,0,0,6">
            <TextBlock Text="Шаблон:" VerticalAlignment="Center" Margin="0,4,6,0"/>
            <Button x:Name="btnP1" Content="1 врезка"/>
            <Button x:Name="btnP2" Content="2 врезки"/>
            <Button x:Name="btnP3" Content="3 врезки"/>
            <Button x:Name="btnPA" Content="Адаптер"/>
          </WrapPanel>
          <ListBox x:Name="lbCuts" Height="130" SelectionMode="Extended"/>
          <Grid Margin="0,8,0,0">
            <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition/><ColumnDefinition/></Grid.ColumnDefinitions>
            <Grid.RowDefinitions><RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/></Grid.RowDefinitions>
            <TextBlock Text="Грань" Grid.Row="0" Grid.Column="0"/><ComboBox x:Name="cbFace" Grid.Row="1" Grid.Column="0"/>
            <TextBlock Text="Форма" Grid.Row="0" Grid.Column="1"/><ComboBox x:Name="cbShape" Grid.Row="1" Grid.Column="1"/>
            <TextBlock Text="Роль" Grid.Row="0" Grid.Column="2"/><ComboBox x:Name="cbRole" Grid.Row="1" Grid.Column="2"/>
            <TextBlock x:Name="lblA" Text="Диаметр" Grid.Row="2" Grid.Column="0"/><TextBox x:Name="tbA" Grid.Row="3" Grid.Column="0"/>
            <TextBlock x:Name="lblB" Text="Высота" Grid.Row="2" Grid.Column="1"/><TextBox x:Name="tbB" Grid.Row="3" Grid.Column="1"/>
            <TextBlock Text="Длина" Grid.Row="2" Grid.Column="2"/><TextBox x:Name="tbL" Grid.Row="3" Grid.Column="2"/>
            <TextBlock Text="Слева, U" Grid.Row="4" Grid.Column="0"/><TextBox x:Name="tbU" Grid.Row="5" Grid.Column="0"/>
            <TextBlock Text="Снизу, V" Grid.Row="4" Grid.Column="1"/><TextBox x:Name="tbV" Grid.Row="5" Grid.Column="1"/>
          </Grid>
          <WrapPanel>
            <Button x:Name="btnAdd" Content="Добавить"/>
            <Button x:Name="btnDel" Content="Удалить"/>
            <Button x:Name="btnCenter" Content="По центру"/>
            <ComboBox x:Name="cbRows" Width="120" VerticalAlignment="Bottom" ToolTip="Как авто расставлять врезки грани выбранной врезки: в один ряд, сеткой в 2–3 ряда или шахматкой (верхний ряд на одну больше, нижний — посередине между ними; шахматка занимает грань с отступом «От края»)"/>
            <Button x:Name="btnRow" Content="Расставить (авто)"/>
            <Button x:Name="btnFull" Content="По сечению короба" ToolTip="Прямоугольная во всю грань, по центру — без перехода"/>
          </WrapPanel>
        </StackPanel>
      </GroupBox>
    </StackPanel>
    </ScrollViewer>
    <GridSplitter Grid.Column="1" Width="6" HorizontalAlignment="Stretch" Background="#D5DDD7" ToolTip="Тяните: шире настройки или превью"/>
    <Grid Grid.Column="2" Margin="8,0,0,0">
      <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
      <WrapPanel x:Name="wpViews" Grid.Row="0" Margin="0,0,0,6"/>
      <Border Grid.Row="1" Background="#172022" CornerRadius="4">
        <Canvas x:Name="cv" ClipToBounds="True" Background="Transparent" Cursor="SizeAll"/>
      </Border>
      <TextBlock Grid.Row="2" Foreground="#888" FontSize="11" Margin="0,4,0,0" TextWrapping="Wrap"
                 Text="ЛКМ по фону — вращать, колесо — масштаб, ЛКМ по врезке — выбрать, с Ctrl/Shift — добавить к выбору (в списке так же). Бирюзовая — основная, оранжевая — ответвление, жёлтая рамка — выбранная, красная — не влезает. Цифра — номер в списке. Вход и выход Revit определит сам по системе. Ctrl+колесо — масштаб окна, Ctrl+0 — 100 %; Ctrl+Z / Ctrl+Y — назад / вперёд (кнопки внизу); в числовых полях ↑/↓ — ±1, PgUp/PgDn — ±5. «Между врезками» и «От края» сами расставляют врезки грани по центру — в ряд, в 2–3 ряда или шахматкой (список рядом с «Расставить (авто)»); если сдвинуть врезку вручную (U/V, «По центру»), эта грань перестаёт расставляться сама — вернуть кнопкой «Расставить (авто)»."/>
      <TextBlock x:Name="tbStatus" Grid.Row="4" TextWrapping="Wrap" Margin="0,8,0,0"/>
      <StackPanel Grid.Row="3" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,10,0,0">
        <Button x:Name="btnUndo" Content="↶ Назад" ToolTip="Отменить последнее изменение окна (Ctrl+Z)"/>
        <Button x:Name="btnRedo" Content="Вперёд ↷" ToolTip="Вернуть отменённое (Ctrl+Y или Ctrl+Shift+Z)" Margin="0,4,24,0"/>
        <Button x:Name="btnCreate" Content="Создать семейство" IsDefault="True"/>
        <Button x:Name="btnClose" Content="Закрыть"/>
      </StackPanel>
    </Grid>
  </Grid>
</Window>
'''


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


def shade(rgb, k, a=255):
    return brush(*[int(max(0, min(255, c * k))) for c in rgb] + [a])


BOX_RGB = (150, 196, 204)
MAIN_RGB = (38, 166, 154)
BR_RGB = (245, 145, 32)
BAD_RGB = (229, 57, 53)
C_SEL = brush(255, 214, 0)
C_EDGE = brush(220, 235, 238, 160)
C_TXT = brush(236, 239, 241)
C_DIM = brush(160, 180, 185)


class PlenumWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML.replace(u'XYZ_THEME_URI', forms.theme_uri()), literal_string=True)
        forms.theme(self, u'Пленум', u'Короб с врезками → семейство «Арматура воздуховодов» с соединителями', merge=False)
        self.cuts = []
        self.restoring = False      # история назад/вперёд создаётся в конце __init__
        self.bad = set()
        self.busy = False
        self.result = None
        self.yaw, self.pitch, self.zoom = -35.0, 28.0, 1.0
        self.roll = 0.0
        self.drag = None
        self.auto = set(FACES)      # грани, где врезки расставляются автоматически (ряд, зазор «Между врезками»)
        self.mode = {}              # грань → расстановка: ряд / 2 ряда / 3 ряда / шахматка (по умолчанию ряд)
        for it in LAYOUTS:
            self.cbRows.Items.Add(it)
        self.cbRows.SelectedIndex = 0
        self.snap = None            # снимок положений врезок до правки размеров короба
        for cb, items in ((self.cbFace, FACES), (self.cbShape, SHAPES), (self.cbRole, ROLES)):
            for it in items:
                cb.Items.Add(it)
        # 30.10.2026: кнопки видов заменены видовым кубом в углу превью (как в Revit)
        b = Button(); b.Content = u'Вписать'; b.Click += self.on_fit
        self.wpViews.Children.Add(b)
        self.preset(2)
        for tb in (self.tbW, self.tbH, self.tbD):
            tb.TextChanged += self.on_dims
        for tb in (self.tbGap, self.tbEdge):
            tb.TextChanged += self.on_spacing
        for tb in (self.tbW, self.tbH, self.tbD):
            tb.GotKeyboardFocus += self.take_snap
        self.tbLen.TextChanged += self.on_len
        for tb in (self.tbA, self.tbB, self.tbL, self.tbU, self.tbV):
            tb.TextChanged += self.on_cut_edit
        for cb in (self.cbShape, self.cbRole):
            cb.SelectionChanged += self.on_cut_edit
        self.cbFace.SelectionChanged += self.on_face
        self.lbCuts.SelectionChanged += self.on_select
        self.btnAdd.Click += self.on_add
        self.btnDel.Click += self.on_del
        self.btnCenter.Click += self.on_center
        self.btnRow.Click += self.on_row
        self.cbRows.SelectionChanged += self.on_rows
        self.btnFull.Click += self.on_full
        self.btnP1.Click += lambda s, e: self.preset(1)
        self.btnP2.Click += lambda s, e: self.preset(2)
        self.btnP3.Click += lambda s, e: self.preset(3)
        self.btnPA.Click += lambda s, e: self.preset(0)
        self.btnCreate.Click += self.on_create
        self.btnClose.Click += lambda s, e: self.Close()
        self.cv.SizeChanged += lambda s, e: self.draw()
        self.cv.MouseLeftButtonDown += self.on_down
        self.cv.MouseMove += self.on_move
        self.cv.MouseLeftButtonUp += self.on_up
        self.cv.MouseWheel += self.on_wheel
        self.set_auto_name()
        self.tbName.TextChanged += self.on_name
        # стандарт окон «Объёмов»: размер, разделитель и масштаб запоминаются
        try:
            if CFG.get('w'):
                self.Width, self.Height = max(980.0, float(CFG['w'])), max(640.0, float(CFG['h']))
            if CFG.get('left'):
                self.colLeft.Width = GridLength(max(320.0, float(CFG['left'])))
        except Exception:
            pass
        self.zoomk = 1.0
        self.set_zoomk(float(CFG.get('zoom', 1.0)))
        self.PreviewMouseWheel += self.on_zoom_wheel
        self.PreviewKeyDown += self.on_zoom_key
        self.Closing += self.on_closing
        for tb in (self.tbW, self.tbH, self.tbD, self.tbLen, self.tbGap, self.tbEdge,
                   self.tbA, self.tbB, self.tbL, self.tbU, self.tbV):
            tb.PreviewKeyDown += self.on_num_key
        # история: назад / вперёд (снимок окна после того, как правка «улеглась», и перед каждой кнопкой)
        self.hist, self.hpos, self.restoring = [self.state()], 0, False
        self.htimer = DispatcherTimer()
        self.htimer.Interval = TimeSpan.FromMilliseconds(600)
        self.htimer.Tick += self.on_htick
        self.btnUndo.Click += self.on_undo
        self.btnRedo.Click += self.on_redo
        self.update_hist_buttons()

    # ------------------------------------------------------------ история
    HIST_TB = ('tbW', 'tbH', 'tbD', 'tbLen', 'tbGap', 'tbEdge', 'tbName', 'tbWall')

    def state(self):
        return dict(texts=dict((n, getattr(self, n).Text) for n in self.HIST_TB),
                    name_auto=self.tbName.Tag == 'auto',
                    cuts=copy.deepcopy(self.cuts), auto=set(self.auto), mode=dict(self.mode),
                    sel=self.sel_idx())

    @staticmethod
    def same(a, b):
        return a['texts'] == b['texts'] and a['cuts'] == b['cuts'] and a['auto'] == b['auto'] and a['mode'] == b['mode']

    def record(self):
        """Запомнить текущее состояние, если оно отличается от последнего запомненного."""
        if self.restoring or 'hist' not in self.__dict__:
            return
        self.htimer.Stop()
        st = self.state()
        if self.same(st, self.hist[self.hpos]):
            self.hist[self.hpos]['sel'] = st['sel']
            return
        del self.hist[self.hpos + 1:]
        self.hist.append(st)
        if len(self.hist) > 200:
            self.hist.pop(0)
        self.hpos = len(self.hist) - 1
        self.update_hist_buttons()

    def kick_hist(self):
        if self.restoring or 'htimer' not in self.__dict__:
            return
        self.htimer.Stop()
        self.htimer.Start()

    def on_htick(self, sender, e):
        self.record()

    def restore(self, st):
        self.restoring = True
        self.busy = True
        try:
            for n, t in st['texts'].items():
                getattr(self, n).Text = t
            self.tbName.Tag = 'auto' if st['name_auto'] else None
            self.cuts = copy.deepcopy(st['cuts'])
            self.auto, self.mode, self.snap = set(st['auto']), dict(st['mode']), None
        finally:
            self.busy = False
        self.refresh_list(st['sel'] or 0)
        self.draw()
        self.restoring = False
        self.update_hist_buttons()

    def on_undo(self, sender, e):
        self.record()
        if self.hpos > 0:
            self.hpos -= 1
            self.restore(self.hist[self.hpos])
            self.tbStatus.Text = u'Назад: шаг %d из %d. Вперёд — Ctrl+Y. ' % (self.hpos + 1, len(self.hist)) + self.tbStatus.Text

    def on_redo(self, sender, e):
        self.record()
        if self.hpos < len(self.hist) - 1:
            self.hpos += 1
            self.restore(self.hist[self.hpos])

    def update_hist_buttons(self):
        self.btnUndo.IsEnabled = self.hpos > 0
        self.btnRedo.IsEnabled = self.hpos < len(self.hist) - 1

    # ------------------------------------------------------------ масштаб, клавиши, запоминание
    def set_zoomk(self, z):
        self.zoomk = round(max(0.6, min(1.6, z)), 2)
        self.leftRoot.LayoutTransform = ScaleTransform(self.zoomk, self.zoomk)
        self.svLeft.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto if self.zoomk > 1.001 else ScrollBarVisibility.Disabled
        CFG['zoom'] = self.zoomk

    def on_zoom_wheel(self, sender, e):
        if Keyboard.Modifiers & ModifierKeys.Control == ModifierKeys.Control:
            self.set_zoomk(self.zoomk + (0.05 if e.Delta > 0 else -0.05))
            self.tbStatus.Text = u'Масштаб окна %d %% (Ctrl+колесо, Ctrl+0 — 100 %%)' % int(self.zoomk * 100)
            e.Handled = True

    def on_zoom_key(self, sender, e):
        ctrl = Keyboard.Modifiers & ModifierKeys.Control == ModifierKeys.Control
        shift = Keyboard.Modifiers & ModifierKeys.Shift == ModifierKeys.Shift
        if ctrl and e.Key in (Key.D0, Key.NumPad0):
            self.set_zoomk(1.0)
            e.Handled = True
        elif ctrl and (e.Key == Key.Y or (e.Key == Key.Z and shift)):
            self.on_redo(None, None)
            e.Handled = True
        elif ctrl and e.Key == Key.Z:
            self.on_undo(None, None)
            e.Handled = True

    def on_num_key(self, sender, e):
        step = {Key.Up: 1, Key.Down: -1, Key.PageUp: 5, Key.Next: -5}.get(e.Key)
        if step is None or int(Keyboard.Modifiers) != 0:
            return
        try:
            val = float((sender.Text or u'0').replace(u',', u'.'))
        except ValueError:
            return
        sender.Text = fmt(max(0.0, val + step))
        sender.CaretIndex = len(sender.Text)
        e.Handled = True

    def on_closing(self, sender, e):
        try:
            CFG['w'], CFG['h'] = self.ActualWidth, self.ActualHeight
            CFG['left'] = self.colLeft.ActualWidth
        except Exception:
            pass
        save_cfg()

    # ------------------------------------------------------------ данные
    def dims(self):
        return num(self.tbW, 600), num(self.tbD, 400), num(self.tbH, 200)

    def sel(self):
        i = self.lbCuts.SelectedIndex
        return self.cuts[i] if 0 <= i < len(self.cuts) else None

    def sel_idx(self):
        """Номера выбранных строк (Shift / Ctrl — несколько)."""
        items = self.lbCuts.SelectedItems
        return [i for i in range(self.lbCuts.Items.Count) if items.Contains(self.lbCuts.Items[i])]

    def sels(self):
        return [self.cuts[i] for i in self.sel_idx() if i < len(self.cuts)]

    def row_text(self, i):
        c = self.cuts[i]
        return u'№%d · %s · %s · %s' % (i + 1, c['face'], cut_label(c), c['role'].lower())

    def select(self, idx):
        """Выделить строки idx (первая — основная для редактора)."""
        was = self.busy
        self.busy = True
        self.lbCuts.SelectedItems.Clear()
        idx = [i for i in idx if 0 <= i < len(self.cuts)]
        if not idx and self.cuts:
            idx = [0]
        if idx:
            self.lbCuts.SelectedIndex = idx[0]
            for i in idx[1:]:
                self.lbCuts.SelectedItems.Add(self.lbCuts.Items[i])
        self.busy = was
        self.update_header()

    def update_header(self):
        k = len(self.sel_idx())
        self.gbCuts.Header = u'Врезки: %d + основной' % len([c for c in self.cuts if c['role'] != ROLES[1]]) + (u'   ·   выбрано: %d (правка размеров и длины — для всех выбранных)' % k if k > 1 else u'')

    def new_cut(self, face, role, d, u, v):
        return dict(face=face, shape=SHAPES[0], role=role, d=float(d), w=200.0, h=100.0,
                    u=float(u), v=float(v), l=num(self.tbLen, 50))

    def preset(self, n):
        """n = 1..3: основная Ø160 сзади + n ответвлений Ø125 спереди в ряд; 0 — адаптер (прямоугольная основная)."""
        W, D, H = self.dims()
        su, sv = face_size(u'Сзади', W, D, H)
        main = self.new_cut(u'Сзади', ROLES[1], min(160, sv - 20), su / 2, sv / 2)
        if n == 0:
            main['shape'] = SHAPES[1]
            main['w'], main['h'] = max(50, su - 100), max(50, sv - 50)
            n = 1
        self.cuts = [main]
        fu, fv = face_size(u'Спереди', W, D, H)
        d = min(125, fv - 20)
        for i in range(n):
            self.cuts.append(self.new_cut(u'Спереди', ROLES[0], d, 0, 0))
        self.auto = set(FACES)
        self.layout_all()
        self.refresh_list(0)

    # ------------------------------------------------------------ расстановка
    def layout(self, face):
        """Авторасстановка врезок грани по выбранному способу (self.mode)."""
        same = [c for c in self.cuts if c['face'] == face]
        if not same:
            return
        su, sv = face_size(face, *self.dims())
        gap, edge = max(num(self.tbGap, 0), MIN_GAP), max(num(self.tbEdge, 0), MIN_EDGE)
        mode = self.mode.get(face, LAYOUTS[0])
        if mode == LAYOUTS[3] and len(same) >= 2:
            chess(same, su, sv, gap, edge)
            return
        if mode in (LAYOUTS[1], LAYOUTS[2]) and len(same) >= 2:
            grid(same, su, sv, gap, edge, 2 if mode == LAYOUTS[1] else 3)
            return
        self.layout_row(same, su, sv, gap, edge)

    def layout_row(self, same, su, sv, gap, edge):
        """Врезки грани — в ряд вдоль U: между краями соседних ровно «Между врезками»,
        ряд по центру грани; если не помещается по центру — прижат к «От края» слева."""
        same = sorted(same, key=lambda c: c['u'])
        widths = [half(c)[0] * 2 for c in same]
        total = sum(widths) + gap * (len(same) - 1)
        pos = max(edge, (su - total) / 2.0)
        for c, w in zip(same, widths):
            c['u'] = round(pos + w / 2.0, 1)
            c['v'] = round(sv / 2.0, 1)
            pos += w + gap

    def layout_all(self):
        for f in FACES:
            if f in self.auto:
                self.layout(f)

    def take_snap(self, sender, e):
        d = self.dims()
        self.snap = (d, [(c['u'], c['v'], full_face(c, *d)) for c in self.cuts]) if min(d) > 0 else None

    def on_spacing(self, sender, e):
        if self.busy:
            return
        self.layout_all()
        self.refresh_list()

    def refresh_list(self, select=None):
        self.busy = True
        if select is None:
            cur = self.sel_idx()
        elif isinstance(select, int):
            cur = [max(0, min(select, len(self.cuts) - 1))]
        else:
            cur = list(select)
        self.lbCuts.Items.Clear()
        for i in range(len(self.cuts)):
            self.lbCuts.Items.Add(self.row_text(i))
        self.select(cur)
        self.busy = False
        self.load_editor()
        self.validate()

    def load_editor(self):
        c = self.sel()
        self.busy = True
        if c:
            self.cbFace.SelectedItem = c['face']
            self.cbShape.SelectedItem = c['shape']
            self.cbRole.SelectedItem = c['role']
            rnd = c['shape'] == SHAPES[0]
            self.lblA.Text = u'Диаметр' if rnd else u'Ширина'
            self.lblB.Text = u'—' if rnd else u'Высота'
            self.tbB.IsEnabled = not rnd
            self.tbA.Text = fmt(c['d'] if rnd else c['w'])
            self.tbB.Text = u'' if rnd else fmt(c['h'])
            self.tbL.Text = fmt(c['l'])
            self.tbU.Text = fmt(c['u'])
            self.tbV.Text = fmt(c['v'])
            one = len(self.sel_idx()) <= 1
            self.tbU.IsEnabled = self.tbV.IsEnabled = self.cbRole.IsEnabled = one
        self.busy = False

    # ------------------------------------------------------------ события
    def set_auto_name(self):
        W, D, H = overall(*(self.dims() + (self.cuts,)))
        was = self.busy
        self.busy = True
        self.tbName.Text = u'Пленум %sx%sx%s' % (fmt(W), fmt(H), fmt(D))
        self.tbName.Tag = 'auto'
        self.busy = was

    def on_name(self, sender, e):
        if not self.busy:
            self.tbName.Tag = None

    def on_dims(self, sender, e):
        """Размеры короба: на авто-гранях ряд пересобирается, на ручных — врезки сдвигаются
        пропорционально от снимка (без накопления ошибки при наборе «5» → «50» → «500»)."""
        if self.busy:
            return
        new = self.dims()
        if min(new) > 0:
            if self.snap and len(self.snap[1]) == len(self.cuts):
                old, pos = self.snap
                for c, (u, v, ff) in zip(self.cuts, pos):
                    su0, sv0 = face_size(c['face'], *old)
                    su1, sv1 = face_size(c['face'], *new)
                    c['u'], c['v'] = u * su1 / su0, v * sv1 / sv0
                    if ff:                       # во всю грань — остаётся во всю грань
                        c['w'], c['h'] = su1, sv1
            self.layout_all()
        if self.tbName.Tag == 'auto':
            self.set_auto_name()
        self.refresh_list()

    def on_len(self, sender, e):
        if self.busy:
            return
        for c in self.cuts:
            c['l'] = num(self.tbLen, 50)
        self.load_editor()
        self.validate()

    def on_face(self, sender, e):
        """Новая грань — врезка встаёт в центр этой грани."""
        if self.busy:
            return
        idx = self.sel_idx()
        face = self.cbFace.SelectedItem
        if not idx or not face:
            return
        su, sv = face_size(face, *self.dims())
        for k, i in enumerate(idx):
            c = self.cuts[i]
            c['face'] = face
            c['u'], c['v'] = su * (2.0 + k), sv / 2.0     # в конец ряда, в порядке выбора
        self.layout_all()
        if face not in self.auto:
            for i in idx:
                self.cuts[i]['u'], self.cuts[i]['v'] = su / 2.0, sv / 2.0
        self.snap = None
        self.refresh_list(idx)

    def on_cut_edit(self, sender, e):
        """Правится только то поле, которое тронули, — сразу у всех выбранных врезок
        (диаметр — у круглых, ширина/высота — у прямоугольных, длина — у всех)."""
        if self.busy:
            return
        idx = self.sel_idx()
        c = self.sel()
        if not c or not idx:
            return
        group = [self.cuts[i] for i in idx]
        if sender == self.cbShape:
            shape = self.cbShape.SelectedItem
            if not shape:
                return
            faces = set()
            for x in group:
                if x['shape'] != shape:
                    x['shape'] = shape
                    faces.add(x['face'])
            for f in faces:
                if f in self.auto:
                    self.layout(f)
            self.refresh_list(idx)
            return
        if sender == self.cbRole:
            if len(group) == 1 and self.cbRole.SelectedItem:
                c['role'] = self.cbRole.SelectedItem
        elif sender == self.tbA:
            for x in group:
                if x['shape'] != c['shape']:
                    continue
                if x['shape'] == SHAPES[0]:
                    x['d'] = num(self.tbA, x['d'])
                else:
                    x['w'] = num(self.tbA, x['w'])
        elif sender == self.tbB:
            for x in group:
                if x['shape'] == SHAPES[1] and c['shape'] == SHAPES[1]:
                    x['h'] = num(self.tbB, x['h'])
        elif sender == self.tbL:
            for x in group:
                x['l'] = num(self.tbL, x['l'])
        elif sender in (self.tbU, self.tbV) and len(group) == 1:
            nu, nv = num(self.tbU, c['u']), num(self.tbV, c['v'])
            if abs(nu - c['u']) > 0.11 or abs(nv - c['v']) > 0.11:
                self.auto.discard(c['face'])     # двигали руками — грань больше не расставляется сама
            c['u'], c['v'] = nu, nv
        if sender in (self.tbA, self.tbB):
            for f in set(x['face'] for x in group):
                if f in self.auto:
                    self.layout(f)
            self.busy = True
            self.tbU.Text, self.tbV.Text = fmt(c['u']), fmt(c['v'])
            self.busy = False
        self.busy = True
        for i in idx:
            self.lbCuts.Items[i] = self.row_text(i)
        self.select(idx)
        self.busy = False
        self.validate()

    def on_select(self, sender, e):
        if not self.busy:
            self.update_header()
            self.load_editor()
            self.draw()

    def on_add(self, sender, e):
        base = self.sel()
        face = base['face'] if base else u'Спереди'
        su, sv = face_size(face, *self.dims())
        self.cuts.append(self.new_cut(face, ROLES[0], min(125, sv - 20), su * 2.0, sv / 2.0))
        if face in self.auto:
            self.layout(face)
        else:
            self.cuts[-1]['u'] = su / 2.0
        self.snap = None
        self.refresh_list(len(self.cuts) - 1)

    def on_del(self, sender, e):
        idx = self.sel_idx()
        if idx:
            for i in sorted(idx, reverse=True):
                del self.cuts[i]
            self.layout_all()
            self.snap = None
            self.refresh_list(max(0, min(idx) - 1))

    def on_center(self, sender, e):
        idx = self.sel_idx()
        for i in idx:
            c = self.cuts[i]
            su, sv = face_size(c['face'], *self.dims())
            c['u'], c['v'] = su / 2.0, sv / 2.0
            self.auto.discard(c['face'])
        if idx:
            self.refresh_list(idx)

    def on_row(self, sender, e):
        idx = self.sel_idx()
        mode = self.cbRows.SelectedItem or LAYOUTS[0]
        for f in set(self.cuts[i]['face'] for i in idx):
            self.auto.add(f)
            self.mode[f] = mode
            self.layout(f)
        if idx:
            self.refresh_list(idx)

    def on_rows(self, sender, e):
        """Сменили способ расстановки — сразу расставить грани выбранных врезок."""
        if not self.busy and self.sel_idx():
            self.on_row(sender, e)

    def on_full(self, sender, e):
        """Сечение врезки = сечение грани короба (без перехода), по центру грани."""
        idx = self.sel_idx()
        W, D, H = self.dims()
        for i in idx:
            c = self.cuts[i]
            su, sv = face_size(c['face'], W, D, H)
            c['shape'] = SHAPES[1]
            c['w'], c['h'] = su, sv
            c['u'], c['v'] = su / 2.0, sv / 2.0
        if idx:
            self.snap = None
            if self.tbName.Tag == 'auto':
                self.set_auto_name()
            self.refresh_list(idx)

    def on_view(self, sender, e):
        self.yaw, self.pitch = sender.Tag
        self.roll = 0.0
        self.zoom = 1.0
        self.draw()

    def on_fit(self, sender, e):
        self.zoom = 1.0
        self.draw()

    # ------------------------------------------------------------ мышь
    def on_down(self, sender, e):
        self.drag = (e.GetPosition(self.cv), self.yaw, self.pitch, e.OriginalSource)
        self.cv.CaptureMouse()

    def on_move(self, sender, e):
        if not self.drag:
            return
        p0, yw, pt, src = self.drag
        p = e.GetPosition(self.cv)
        dx, dy = p.X - p0.X, p.Y - p0.Y
        if abs(dx) + abs(dy) < 3:
            return
        self.yaw = yw - dx * 0.5
        self.pitch = max(-89.9, min(89.9, pt + dy * 0.5))
        self.draw()

    def on_up(self, sender, e):
        if not self.drag:
            return
        p0, yw, pt, src = self.drag
        self.drag = None
        self.cv.ReleaseMouseCapture()
        p = e.GetPosition(self.cv)
        if abs(p.X - p0.X) + abs(p.Y - p0.Y) < 3:
            tag = getattr(src, 'Tag', None)
            if isinstance(tag, tuple) and tag and tag[0] == 'cube':      # видовой куб
                self.yaw, self.pitch, self.roll = VC.look_from(tag[1], self.yaw)
                self.draw()
                return
            if isinstance(tag, tuple) and tag and tag[0] == 'roll':      # стрелки поворота на 90°
                self.roll = (self.roll + tag[1]) % 360.0
                self.draw()
                return
            if isinstance(tag, int) and 0 <= tag < len(self.cuts):
                if int(Keyboard.Modifiers) & 6:      # Ctrl (2) или Shift (4)
                    idx = self.sel_idx()
                    idx = [i for i in idx if i != tag] if tag in idx else idx + [tag]
                    self.select(idx)
                else:
                    self.select([tag])
                self.load_editor()
                self.draw()

    def on_wheel(self, sender, e):
        self.zoom = max(0.3, min(5.0, self.zoom * (1.15 if e.Delta > 0 else 1 / 1.15)))
        self.draw()

    # ------------------------------------------------------------ проверка
    def problems(self):
        W, D, H = self.dims()
        edge, gap = max(num(self.tbEdge, 0), MIN_EDGE), max(num(self.tbGap, 0), MIN_GAP)
        bad, msgs = set(), []
        if num(self.tbGap, 0) < MIN_GAP - 1e-6 or num(self.tbEdge, 0) < MIN_EDGE - 1e-6:
            msgs.append(u'Меньше технологического минимума нельзя: между патрубками — %s мм, от края грани — %s мм '
                        u'(жестянщику нужно место под крепёж). Расстановка и проверка идут по минимуму.' % (fmt(MIN_GAP), fmt(MIN_EDGE)))
        if min(W, D, H) <= 0:
            msgs.append(u'Размеры короба должны быть больше нуля.')
            return set(range(len(self.cuts))), msgs
        mains = [i for i, c in enumerate(self.cuts) if c['role'] == ROLES[1]]
        if len(mains) != 1:
            msgs.append(u'Основная врезка должна быть ровно одна (сейчас %d).' % len(mains))
        for i, c in enumerate(self.cuts):
            su, sv = face_size(c['face'], W, D, H)
            hu, hv = half(c)
            if hu <= 0 or hv <= 0 or c['l'] < 0:
                bad.add(i); msgs.append(u'№%d: размеры должны быть больше нуля.' % (i + 1))
            elif c['l'] <= 0 and not full_face(c, W, D, H):
                bad.add(i); msgs.append(u'№%d: длина 0 можно только у прямоугольной врезки во всю грань (%s×%s), у остальных — больше нуля.' % (i + 1, fmt(su), fmt(sv)))
            elif ((abs(2 * hu - su) > 0.5 and (c['u'] - hu < edge - 1e-6 or c['u'] + hu > su - edge + 1e-6)) or
                  (abs(2 * hv - sv) > 0.5 and (c['v'] - hv < edge - 1e-6 or c['v'] + hv > sv - edge + 1e-6))):
                # врезка во всю ширину/высоту грани — продолжение короба, отступ от края не нужен
                bad.add(i); msgs.append(u'№%d не влезает в грань «%s» (%s×%s мм, от края %s).' % (i + 1, c['face'], fmt(su), fmt(sv), fmt(edge)))
        for i in range(len(self.cuts)):
            for j in range(i + 1, len(self.cuts)):
                a, b = self.cuts[i], self.cuts[j]
                if a['face'] != b['face']:
                    continue
                if a['shape'] == SHAPES[0] and b['shape'] == SHAPES[0]:
                    clash = math.hypot(a['u'] - b['u'], a['v'] - b['v']) < a['d'] / 2 + b['d'] / 2 + gap - 1e-6
                else:
                    (ahu, ahv), (bhu, bhv) = half(a), half(b)
                    clash = abs(a['u'] - b['u']) < ahu + bhu + gap - 1e-6 and abs(a['v'] - b['v']) < ahv + bhv + gap - 1e-6
                if clash:
                    bad.update((i, j)); msgs.append(u'№%d и №%d ближе %s мм друг к другу.' % (i + 1, j + 1, fmt(gap)))
        return bad, msgs

    def validate(self):
        self.kick_hist()
        bad, msgs = self.problems()
        self.bad = bad
        W, D, H = self.dims()
        if msgs:
            self.tbStatus.Foreground = brush(198, 40, 40)
            self.tbStatus.Text = u'\n'.join(msgs)
        else:
            oW, oD, oH = overall(W, D, H, self.cuts)
            area = 2 * (oW * oD + oW * oH + oD * oH) / 1e6
            self.tbStatus.Foreground = brush(27, 94, 32)
            txt = u'Всё влезает. Габарит Ш %s × В %s × Д %s, площадь %.3f м², врезок %d.' % (fmt(oW), fmt(oH), fmt(oD), area, len([c for c in self.cuts if c['role'] != ROLES[1]]))
            if (oW, oD, oH) != (W, D, H):
                txt += u'\nВрезки во всю грань считаются продолжением короба: короб %s×%s×%s + их длина.' % (fmt(W), fmt(H), fmt(D))
            self.tbStatus.Text = txt
        if self.tbName.Tag == 'auto':
            self.set_auto_name()
        self.btnCreate.IsEnabled = not msgs
        self.draw()

    # ------------------------------------------------------------ 3D
    def project(self, p):
        return VC.project(p, self.yaw, self.pitch, self.roll)   # экран x, экран y, глубина (больше — дальше)

    def facing(self, n):
        return -self.project(n)[2]  # >0 — грань смотрит на зрителя

    def draw(self):
        cv = self.cv
        cv.Children.Clear()
        Wd, Hd = cv.ActualWidth, cv.ActualHeight
        W, D, H = self.dims()
        if Wd < 20 or Hd < 20 or min(W, D, H) <= 0:
            return
        polys = []    # (группа, глубина, точки 3D, rgb, яркость, тег, обводка)
        box_faces = []
        for face in FACES:
            o, u, v, n = face_frame(face, W, D, H)
            su, sv = face_size(face, W, D, H)
            pts = [o, vadd(o, vmul(u, su)), vadd(o, vmul(u, su), vmul(v, sv)), vadd(o, vmul(v, sv))]
            box_faces.append((face, pts, n))
        selset = [id(x) for x in self.sels()]
        ends = []
        for i, c in enumerate(self.cuts):
            o, u, v, n = face_frame(c['face'], W, D, H)
            ctr = vadd(o, vmul(u, c['u']), vmul(v, c['v']))
            tip = vadd(ctr, vmul(n, c['l']))
            rgb = BAD_RGB if i in self.bad else (MAIN_RGB if c['role'] == ROLES[1] else BR_RGB)
            front = self.facing(n) > 0
            grp = 2 if front else 0
            if c['shape'] == SHAPES[0]:
                r, seg = c['d'] / 2.0, 24
                ring = [vadd(vmul(u, r * math.cos(2 * math.pi * k / seg)), vmul(v, r * math.sin(2 * math.pi * k / seg))) for k in range(seg)]
            else:
                hw, hh = c['w'] / 2.0, c['h'] / 2.0
                ring = [vadd(vmul(u, -hw), vmul(v, -hh)), vadd(vmul(u, hw), vmul(v, -hh)), vadd(vmul(u, hw), vmul(v, hh)), vadd(vmul(u, -hw), vmul(v, hh))]
            m = len(ring)
            for k in range(m):
                a0, a1 = ring[k], ring[(k + 1) % m]
                nn = vadd(a0, a1)
                ln = math.sqrt(sum(q * q for q in nn)) or 1
                nn = vmul(nn, 1 / ln)
                if self.facing(nn) <= 0:
                    continue
                quad = [vadd(ctr, a0), vadd(ctr, a1), vadd(tip, a1), vadd(tip, a0)]
                polys.append((grp, quad, rgb, 0.55 + 0.45 * self.facing(nn), i, None))
            if self.facing(n) > 0:
                cap = [vadd(tip, a) for a in ring]
                polys.append((grp + 0.5, cap, rgb, 1.0, i, C_SEL if id(c) in selset else None))
            ends.append((i, tip, c, rgb))
        # короб
        for face, pts, n in box_faces:
            f = self.facing(n)
            if f > 0:
                polys.append((1, pts, BOX_RGB, 0.55 + 0.45 * f, None, C_EDGE))
        # проекция и масштаб
        allp = [p for pl in polys for p in pl[1]] + [p for _, pts, _ in box_faces for p in pts]
        pr = [self.project(p) for p in allp]
        minx, maxx = min(q[0] for q in pr), max(q[0] for q in pr)
        miny, maxy = min(q[1] for q in pr), max(q[1] for q in pr)
        k = min((Wd - 120) / max(1e-6, maxx - minx), (Hd - 120) / max(1e-6, maxy - miny)) * self.zoom
        cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0

        def scr(p):
            q = self.project(p)
            return Point(Wd / 2 + (q[0] - cx) * k, Hd / 2 + (q[1] - cy) * k), q[2]

        polys.sort(key=lambda t: (t[0], -sum(self.project(p)[2] for p in t[1]) / len(t[1])))
        for grp, pts, rgb, lit, tag, stroke in polys:
            pg = Polygon()
            for p in pts:
                pg.Points.Add(scr(p)[0])
            pg.Fill = shade(rgb, lit, 235 if tag is None else 255)
            if stroke is not None:
                pg.Stroke, pg.StrokeThickness = stroke, (3 if stroke is C_SEL else 1)
            if tag is not None:
                pg.Tag = tag
                pg.Cursor = Cursors.Hand
            cv.Children.Add(pg)
        # подписи граней
        for face, pts, n in box_faces:
            if self.facing(n) > 0.25:
                ctr = vmul(vadd(*pts), 0.25)
                p, _ = scr(ctr)
                self.label(face, p.X, p.Y, C_DIM, 11, center=True)
        # номера врезок (направление воздуха не задаём — Revit берёт его из системы)
        for i, tip, c, rgb in ends:
            o, u, v, n = face_frame(c['face'], W, D, H)
            p, _ = scr(vadd(tip, vmul(n, 30)))
            self.badge(str(i + 1), p.X, p.Y, shade(rgb, 0.8), id(c) in selset)
        # габариты
        corner = (-W / 2, -D / 2, -H / 2)
        for txt, q in ((u'Ш %s' % fmt(W), (0, -D / 2, -H / 2)), (u'Д %s' % fmt(D), (W / 2, 0, -H / 2)), (u'В %s' % fmt(H), (-W / 2, -D / 2, 0))):
            p, _ = scr(q)
            self.label(txt, p.X + 4, p.Y + 4, C_TXT, 11)
        VC.draw(cv, self.yaw, self.pitch, self.roll, dark=True)

    def label(self, txt, x, y, br, size, center=False):
        t = TextBlock()
        t.Text, t.Foreground, t.FontSize = txt, br, size
        t.IsHitTestVisible = False
        if center:
            t.Measure(Size(1e4, 1e4))
            x -= t.DesiredSize.Width / 2; y -= t.DesiredSize.Height / 2
        Canvas.SetLeft(t, x); Canvas.SetTop(t, y)
        self.cv.Children.Add(t)

    def badge(self, txt, x, y, br, selected):
        e = Ellipse()
        e.Width = e.Height = 20
        e.Fill = br
        e.Stroke = C_SEL if selected else C_TXT
        e.StrokeThickness = 2 if selected else 1
        e.IsHitTestVisible = False
        Canvas.SetLeft(e, x - 10); Canvas.SetTop(e, y - 10)
        self.cv.Children.Add(e)
        self.label(txt, x, y, C_TXT, 11, center=True)

    def arrow(self, a, b, br):
        ln = WLine()
        ln.X1, ln.Y1, ln.X2, ln.Y2 = a.X, a.Y, b.X, b.Y
        ln.Stroke, ln.StrokeThickness = br, 2
        ln.IsHitTestVisible = False
        self.cv.Children.Add(ln)
        dx, dy = b.X - a.X, b.Y - a.Y
        L = math.hypot(dx, dy) or 1
        ux, uy = dx / L, dy / L
        head = Polygon()
        for px, py in ((b.X, b.Y), (b.X - 9 * ux + 5 * uy, b.Y - 9 * uy - 5 * ux), (b.X - 9 * ux - 5 * uy, b.Y - 9 * uy + 5 * ux)):
            head.Points.Add(Point(px, py))
        head.Fill = br
        head.IsHitTestVisible = False
        self.cv.Children.Add(head)

    # ------------------------------------------------------------ создать
    def on_create(self, sender, e):
        W, D, H = self.dims()
        self.result = dict(W=W, D=D, H=H, name=self.tbName.Text.strip() or u'Пленум',
                           wall=num(self.tbWall, 0.5),
                           cuts=[dict(c) for c in self.cuts])
        self.Close()


def checkpoint(fn):
    """Перед действием кнопкой — запомнить то, что было (вдруг оно ещё не «улеглось» в историю)."""
    def wrap(self, *a):
        self.record()
        r = fn(self, *a)
        self.record()
        return r
    return wrap


for _n in ('on_add', 'on_del', 'on_center', 'on_row', 'on_rows', 'on_full'):
    setattr(PlenumWindow, _n, checkpoint(getattr(PlenumWindow, _n)))
PlenumWindow.preset_raw = PlenumWindow.preset


def _preset(self, n):
    if 'hist' in self.__dict__:
        self.record()
    PlenumWindow.preset_raw(self, n)
    if 'hist' in self.__dict__:
        self.record()
        self.tbStatus.Text = u'Шаблон заменил врезки. Вернуть как было — «↶ Назад» или Ctrl+Z. ' + self.tbStatus.Text


PlenumWindow.preset = _preset

for _n in ('on_create', 'on_undo', 'on_redo', 'on_add', 'on_del', 'on_center', 'on_row', 'on_rows', 'on_full', 'on_dims', 'on_spacing',
           'on_len', 'on_cut_edit', 'on_face', 'on_select', 'on_view', 'on_fit', 'on_down', 'on_move', 'on_up',
           'on_wheel', 'on_name', 'preset', 'take_snap'):
    if hasattr(PlenumWindow, _n):
        setattr(PlenumWindow, _n, guarded(getattr(PlenumWindow, _n)))


# ---------------------------------------------------------------- семейство
def config_hash(cfg):
    """v6 — без режима приток/вытяжка и только из того, что лежит в параметрах типа (db_В*_Грань/Роль/Размер/U/V/Длина),
    чтобы код можно было пересчитать у готового семейства."""
    parts = ['v6', fmt(cfg['W']), fmt(cfg['D']), fmt(cfg['H'])]
    for c in cfg['cuts']:
        parts.append(u'|'.join([c['face'], c['role'], cut_label(c), fmt(c['u']), fmt(c['v']), fmt(c['l'])]))
    return hashlib.md5(u';'.join(parts).encode('utf-8')).hexdigest()[:6]


def find_family(name):
    for f in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        if f.Name == name:
            return f
    return None


CODE_PARAM = u'db_Код конфигурации'


def family_code(fam):
    for sid in fam.GetFamilySymbolIds():
        p = doc.GetElement(sid).LookupParameter(CODE_PARAM)
        if p is not None:
            return p.AsString()
    return None


def pick_family(base, code):
    """Имя без «кода» в хвосте: db_Пленум_500x400x100_3вр. Та же конфигурация → то же семейство
    (сверяем по скрытому в параметрах «db_Код конфигурации»); другая с теми же габаритами →
    db_Пленум_500x400x100_3вр_2, _3 …"""
    n = 1
    while True:
        name = base if n == 1 else u'%s_%d' % (base, n)
        fam = find_family(name)
        if fam is None:
            return None, name
        if family_code(fam) == code:
            return fam, name
        n += 1


def load_defs(names):
    orig = app.SharedParametersFilename
    fop = FOP if os.path.isfile(FOP) else orig   # db_ФОП из расширения; нет — ФОП, заданный в Revit
    if not fop or not os.path.isfile(fop):
        raise Exception(u'Нет файла общих параметров (ФОП). Задайте его в Revit: Управление → Общие параметры.')
    app.SharedParametersFilename = fop
    f = app.OpenSharedParameterFile()
    if f is None:
        app.SharedParametersFilename = orig
        raise Exception(u'Не открылся ФОП: ' + fop)
    defs = {}
    for g in f.Groups:
        for d in g.Definitions:
            if d.Name in names:
                defs[d.Name] = d
    return defs, orig


def extrude_rect(fd, center, u, v, n, a, b, depth):
    pl = DB.Plane.CreateByNormalAndOrigin(n, center)
    sp = DB.SketchPlane.Create(fd, pl)
    pts = [center - u * (a / 2) - v * (b / 2), center + u * (a / 2) - v * (b / 2),
           center + u * (a / 2) + v * (b / 2), center - u * (a / 2) + v * (b / 2)]
    ca = DB.CurveArray()
    for i in range(4):
        ca.Append(DB.Line.CreateBound(pts[i], pts[(i + 1) % 4]))
    caa = DB.CurveArrArray(); caa.Append(ca)
    return fd.FamilyCreate.NewExtrusion(True, caa, sp, depth)


def extrude_circle(fd, center, n, r, depth):
    pl = DB.Plane.CreateByNormalAndOrigin(n, center)
    sp = DB.SketchPlane.Create(fd, pl)
    ca = DB.CurveArray()
    ca.Append(DB.Arc.Create(center, r, 0.0, math.pi, pl.XVec, pl.YVec))
    ca.Append(DB.Arc.Create(center, r, math.pi, 2 * math.pi, pl.XVec, pl.YVec))
    caa = DB.CurveArrArray(); caa.Append(ca)
    return fd.FamilyCreate.NewExtrusion(True, caa, sp, depth)


def end_face(fd, ext, origin, n, depth):
    opt = DB.Options(); opt.ComputeReferences = True
    for go in ext.get_Geometry(opt):
        if isinstance(go, DB.Solid):
            for f in go.Faces:
                if isinstance(f, DB.PlanarFace) and f.FaceNormal.IsAlmostEqualTo(n) and abs((f.Origin - origin).DotProduct(n) - depth) < 1e-6:
                    return f
    return None


SHARED_TYPE = [u'ADSK_Наименование', u'ADSK_Марка', u'ADSK_Код изделия', u'ADSK_Завод-изготовитель',
               u'ADSK_Единица измерения', u'ADSK_Количество', u'ADSK_Масса', u'ADSK_Материал наименование',
               u'ADSK_Толщина стенки', u'db_Наименование краткое', u'db_Размер']
SHARED_SIZE = [u'ADSK_Размер_Ширина', u'ADSK_Размер_Высота', u'ADSK_Размер_Длина']
CUT_SHARED = [u'db_В%d_%s' % (i, s_) for i in range(1, 17) for s_ in (u'Грань', u'Роль', u'Размер', u'U', u'V', u'Длина')]


def build_family(cfg, fam_name, type_name, path):
    W, D, H = cfg['W'] * MM, cfg['D'] * MM, cfg['H'] * MM
    tpl = os.path.join(app.FamilyTemplatePath, u'Метрическая система, типовая модель.rft')
    fd = app.NewFamilyDocument(tpl)
    defs, orig = load_defs(set(SHARED_TYPE + SHARED_SIZE + [u'ADSK_Размер_Площадь', u'ADSK_Примечание'] + CUT_SHARED))
    try:
        t = DB.Transaction(fd, u'Пленум')
        t.Start()
        fd.OwnerFamily.FamilyCategory = DB.Category.GetCategory(fd, DB.BuiltInCategory.OST_DuctAccessory)
        # «Разрывает» — как у остальной арматуры с 3–5 соединителями в проекте. При «Присоединяется»
        # (по умолчанию после смены категории) в проекте не видно соединителей и к ним не подключиться.
        fd.OwnerFamily.get_Parameter(DB.BuiltInParameter.FAMILY_CONTENT_PART_TYPE).Set(int(DB.PartType.BreaksInto))
        box = extrude_rect(fd, DB.XYZ(0, 0, -H / 2), DB.XYZ.BasisX, DB.XYZ.BasisY, DB.XYZ.BasisZ, W, D, H)
        cuts = sorted(cfg['cuts'], key=lambda c: 0 if c['role'] == ROLES[1] else 1)  # основная — первой (первичный соединитель)
        made = []
        for c in cuts:
            o, u, v, n, su, sv = face_geom(c['face'], W, D, H)
            center = o + u * (c['u'] * MM) + v * (c['v'] * MM)
            L = c['l'] * MM
            if L < 1e-6:                    # во всю грань без удлинения — соединитель прямо на грани короба
                made.append((c, box, center, n, 0.0, u))
                continue
            if c['shape'] == SHAPES[0]:
                ext = extrude_circle(fd, center, n, c['d'] * MM / 2, L)
            else:
                ext = extrude_rect(fd, center, u, v, n, c['w'] * MM, c['h'] * MM, L)
            made.append((c, ext, center, n, L, u))
        fd.Regenerate()
        for c, ext, center, n, L, u in made:
            f = end_face(fd, ext, center, n, L)
            if f is None:
                raise Exception(u'Не найдена торцевая грань врезки ' + cut_label(c))
            prof = DB.ConnectorProfileType.Round if c['shape'] == SHAPES[0] else DB.ConnectorProfileType.Rectangular
            ce = DB.ConnectorElement.CreateDuctConnector(fd, DuctSystemType.Global, prof, f.Reference)
            if c['shape'] == SHAPES[0]:
                ce.get_Parameter(DB.BuiltInParameter.CONNECTOR_RADIUS).Set(c['d'] * MM / 2)
            else:
                cw, ch = c['w'] * MM, c['h'] * MM
                if abs(ce.CoordinateSystem.BasisX.DotProduct(u)) < 0.5:   # оси соединителя повёрнуты относительно грани
                    cw, ch = ch, cw
                ce.get_Parameter(DB.BuiltInParameter.CONNECTOR_WIDTH).Set(cw)
                ce.get_Parameter(DB.BuiltInParameter.CONNECTOR_HEIGHT).Set(ch)
            p = ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_DIRECTION_PARAM)   # вход/выход — по системе
            if p and not p.IsReadOnly:
                p.Set(int(DB.FlowDirectionType.Bidirectional))
        # параметры
        fm = fd.FamilyManager
        if fm.CurrentType is None or not (fm.CurrentType.Name or u'').strip():
            fm.NewType(type_name)
        else:
            fm.RenameCurrentType(type_name)
        for nm in SHARED_SIZE:
            fm.AddParameter(defs[nm], DB.GroupTypeId.Geometry, False)
        oW, oD, oH = overall(cfg['W'], cfg['D'], cfg['H'], cfg['cuts'])
        fm.Set(fm.get_Parameter(u'ADSK_Размер_Ширина'), oW * MM)
        fm.Set(fm.get_Parameter(u'ADSK_Размер_Высота'), oH * MM)
        fm.Set(fm.get_Parameter(u'ADSK_Размер_Длина'), oD * MM)
        ap = fm.AddParameter(defs[u'ADSK_Размер_Площадь'], DB.GroupTypeId.Data, False)
        fm.SetFormula(ap, u'2 * (ADSK_Размер_Ширина * ADSK_Размер_Длина + ADSK_Размер_Ширина * ADSK_Размер_Высота + ADSK_Размер_Длина * ADSK_Размер_Высота)')
        for nm in SHARED_TYPE:
            if nm in defs:
                fm.AddParameter(defs[nm], DB.GroupTypeId.Data, False)
        fm.AddParameter(defs[u'ADSK_Примечание'], DB.GroupTypeId.Data, True)
        fm.Set(fm.get_Parameter(u'ADSK_Наименование'), cfg['name'])
        fm.Set(fm.get_Parameter(u'ADSK_Единица измерения'), u'шт')
        fm.Set(fm.get_Parameter(u'ADSK_Количество'), 1.0)
        fm.Set(fm.get_Parameter(u'ADSK_Толщина стенки'), cfg['wall'] * MM)
        fm.Set(fm.get_Parameter(u'db_Наименование краткое'), u'Пленум')
        fm.Set(fm.get_Parameter(u'db_Размер'), u'%s×%s×%s' % (fmt(oW), fmt(oH), fmt(oD)))
        fm.Set(fm.AddParameter(CODE_PARAM, DB.GroupTypeId.Data, DB.SpecTypeId.String.Text, False), cfg.get('code', u''))
        # врезки — ОБЩИЕ параметры db_Вi_* из ФОП (группа «db_Пленум», 29.10.2026): их видит спецификация
        # «Врезки пленумов» («Эскиз»); нет в ФОП — как раньше, параметр семейства
        def cut_par(name, grp, spec):
            d = defs.get(name)
            if d is not None:
                return fm.AddParameter(d, grp, False)
            return fm.AddParameter(name, grp, spec, False)
        for i, c in enumerate(cfg['cuts']):
            pre = u'db_В%d_' % (i + 1)
            for suf, val in ((u'Грань', c['face']), (u'Роль', c['role']), (u'Размер', cut_label(c))):
                fm.Set(cut_par(pre + suf, DB.GroupTypeId.Data, DB.SpecTypeId.String.Text), val)
            for suf, val in ((u'U', c['u']), (u'V', c['v']), (u'Длина', c['l'])):
                fm.Set(cut_par(pre + suf, DB.GroupTypeId.Geometry, DB.SpecTypeId.Length), val * MM)
        t.Commit()
        app.SharedParametersFilename = orig
        if not os.path.isdir(OUT_DIR):
            os.makedirs(OUT_DIR)
        opts = DB.SaveAsOptions(); opts.OverwriteExistingFile = True
        fd.SaveAs(path, opts)
        fam = fd.LoadFamily(doc)
        return fam
    finally:
        app.SharedParametersFilename = orig
        fd.Close(False)


def main():
    if doc.IsFamilyDocument:
        forms.alert(u'Откройте проект, а не семейство.', exitscript=True)
    w = PlenumWindow()
    w.ShowDialog()
    cfg = w.result
    if not cfg:
        return
    oW, oD, oH = overall(cfg['W'], cfg['D'], cfg['H'], cfg['cuts'])
    type_name = u'%sx%sx%s' % (fmt(oW), fmt(oH), fmt(oD))
    code = config_hash(cfg)
    n_br = len([c for c in cfg['cuts'] if c['role'] != ROLES[1]])   # основной присоединительный размер врезкой не считается
    fam, fam_name = pick_family(u'db_Пленум_%s_%dвр' % (type_name, n_br), code)
    cfg['code'] = code
    path = os.path.join(OUT_DIR, fam_name + u'.rfa')
    if fam is None:
        try:
            fam = build_family(cfg, fam_name, type_name, path)
        except Exception as ex:
            forms.alert(u'Не удалось создать семейство:\n%s' % ex)
            return
    sym = None
    for sid in fam.GetFamilySymbolIds():
        sym = doc.GetElement(sid)
        break
    if sym is None:
        forms.alert(u'В семействе нет типоразмера.')
        return
    with revit.Transaction(u'Пленум: активировать тип'):
        if not sym.IsActive:
            sym.Activate()
    try:
        uidoc.PromptForFamilyInstancePlacement(sym)
    except Exception:
        pass   # Esc — размещение закончено


main()
