# -*- coding: utf-8 -*-
"""Прозрачность связи: слайдер 0–100 % в активном виде, через фильтры вида.

Скрипт читает элементы выбранной связи, собирает их категории и ставит фильтр
db_Связи_Прозрачность (эти категории, прозрачность N %). Фильтры вида действуют на
элементы связей с отображением «По основному виду», в том числе в «Скрытых линиях».
Чтобы не просвечивала сама ОВ2, выше стоит db_ОВ2_Непрозрачно: правило «Имя типа =
типы ОВ2 в этих категориях» (собирается из модели при каждом запуске), прозрачность 1 %.
Оба фильтра ставятся первыми в списке фильтров вида, остальные возвращаются на места.
Ограничение Revit: у элементов связи нет параметра «имя связи», поэтому фильтр действует
на все загруженные связи в этих категориях, а не только на выбранную.
0 — фильтры снимаются с вида; 100 — выбранные связи скрываются.
«Применить» (и крестик) — оставить, «Отмена» — вернуть вид как было до открытия окна.
"""
__title__ = u'Прозрачность\nсвязи'
__persistentengine__ = True

import clr
import System
import os
import io
import json
from pyrevit import revit, DB, UI, forms
from System.Collections.Generic import List
from System.Windows.Media import Brushes
from System.Windows.Threading import DispatcherTimer
from System import TimeSpan

uidoc = revit.uidoc
doc = revit.doc

F_LINK = u'db_Связи_Прозрачность'
F_HOST = u'db_ОВ2_Непрозрачно'


def links_in(d):
    res = list(DB.FilteredElementCollector(d).OfClass(DB.RevitLinkInstance))
    res.sort(key=lambda x: x.Name)
    return res


def find_filter(d, name):
    for f in DB.FilteredElementCollector(d).OfClass(DB.ParameterFilterElement):
        if f.Name == name:
            return f
    return None


def applied(view, f):
    return f is not None and view.IsFilterApplied(f.Id)


def current_value(view, eid):
    d = view.Document
    el = d.GetElement(eid)
    if el is not None and el.IsHidden(view):
        return 100
    f = find_filter(d, F_LINK)
    if applied(view, f):
        return view.GetFilterOverrides(f.Id).Transparency
    return 0


def link_categories(d, ids):
    fc = DB.ParameterFilterUtilities.GetAllFilterableCategories()
    cats = {}
    for i in ids:
        ld = d.GetElement(i).GetLinkDocument()
        if ld is None:
            continue
        for e in DB.FilteredElementCollector(ld).WhereElementIsNotElementType():
            c = e.Category
            if c is not None and c.CategoryType == DB.CategoryType.Model and fc.Contains(c.Id):
                cats[c.Id.Value] = c.Id
    return cats


LINE_MAX = 225      # предел посветления контура: чисто белый (255) закрывал бы то, что за ним


def link_overrides(value):
    """Прозрачность поверхностей = value; контуры (проекция и разрез) светлеют вместе с ней:
    0 % — чёрные, 100 % — светло-серые RGB 225, не белые."""
    o = DB.OverrideGraphicSettings()
    o.SetSurfaceTransparency(int(value))
    g = int(round(LINE_MAX * value / 100.0))
    c = DB.Color(g, g, g)
    o.SetProjectionLineColor(c)
    o.SetCutLineColor(c)
    return o


def host_overrides():
    """ОВ2 стоит выше: прозрачность 1 % и чёрный контур, иначе ей достались бы
    прозрачность и светлые контуры фильтра связи."""
    o = DB.OverrideGraphicSettings()
    o.SetSurfaceTransparency(1)
    o.SetProjectionLineColor(DB.Color(0, 0, 0))
    o.SetCutLineColor(DB.Color(0, 0, 0))
    return o


class Applier(UI.IExternalEventHandler):
    """Выполняется в контексте Revit. Из значений слайдера берётся только последнее."""
    def __init__(self):
        self.win = None
        self.value = None
        self.ids = []
        self.clear_sel = False
        self.restore = None
        self.initial = {}          # id вида -> снимок до первого изменения
        self.cats_cache = {}

    def snapshot(self, d, view, ids):
        if view.Id.Value in self.initial:
            return
        fl, fh = find_filter(d, F_LINK), find_filter(d, F_HOST)
        self.initial[view.Id.Value] = dict(
            link=view.GetFilterOverrides(fl.Id).Transparency if applied(view, fl) else None,
            host=applied(view, fh),
            hidden=dict((i.Value, d.GetElement(i).IsHidden(view)) for i in ids))

    def ensure_filters(self, d, view, ids):
        key = tuple(sorted(i.Value for i in ids))
        if key not in self.cats_cache:
            self.cats_cache[key] = link_categories(d, ids)
        cats = dict(self.cats_cache[key])
        fl, fh = find_filter(d, F_LINK), find_filter(d, F_HOST)
        if fl is not None:                       # категории копятся: старые не теряем
            for c in fl.GetCategories():
                cats[c.Value] = c
        if not cats:
            raise Exception(u'у выбранных связей нет категорий для фильтра (связь не загружена?)')
        cl = List[DB.ElementId](cats.values())
        if fl is None:
            fl = DB.ParameterFilterElement.Create(d, F_LINK, cl)
        else:
            fl.SetCategories(cl)
        # Защита ОВ2: категории связи, где у элементов есть «Имя типа», правило —
        # «Имя типа = любое из типов ОВ2 в этих категориях». Список собирается заново
        # из модели при каждом запуске, поэтому новые типы ОВ2 тоже защищены.
        p_type = DB.ElementId(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        hcats = [c for c in cats.values()
                 if DB.ParameterFilterUtilities.GetFilterableParametersInCommon(d, List[DB.ElementId]([c])).Contains(p_type)]
        names = set()
        if hcats:
            for e in DB.FilteredElementCollector(d).WherePasses(
                    DB.ElementMulticategoryFilter(List[DB.ElementId](hcats))).WhereElementIsNotElementType():
                t = d.GetElement(e.GetTypeId())     # «Имя типа» — параметр типа, не экземпляра
                p = t.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME) if t is not None else None
                n = p.AsString() if p is not None else None
                if n:
                    names.add(n)
        if names:
            rules = List[DB.ElementFilter]([DB.ElementParameterFilter(
                DB.ParameterFilterRuleFactory.CreateEqualsRule(p_type, n)) for n in sorted(names)])
            ef = rules[0] if rules.Count == 1 else DB.LogicalOrFilter(rules)
            hl = List[DB.ElementId](hcats)
            if fh is None:
                fh = DB.ParameterFilterElement.Create(d, F_HOST, hl, ef)
            else:
                fh.SetCategories(hl)
                fh.SetElementFilter(ef)
        elif fh is not None and applied(view, fh):
            view.RemoveFilter(fh.Id)
            fh = None
        if fh is None:
            if not applied(view, fl):
                view.AddFilter(fl.Id)
            view.SetIsFilterEnabled(fl.Id, True)
            view.SetFilterVisibility(fl.Id, True)
            return fl, None
        order = list(view.GetOrderedFilters())
        if [x.Value for x in order[:2]] != [fh.Id.Value, fl.Id.Value]:
            # Наши два фильтра должны стоять первыми: в Revit верхний фильтр главнее, и
            # твои db_Стены / db_Перекрытия (у них своя прозрачность) иначе перебивают слайдер.
            # API не умеет менять порядок — снимаем остальные и возвращаем их с теми же настройками.
            keep = []
            for fid in order:
                if fid.Value in (fh.Id.Value, fl.Id.Value):
                    continue
                keep.append((fid, view.GetFilterOverrides(fid), view.GetFilterVisibility(fid), view.GetIsFilterEnabled(fid)))
            for fid in order:
                view.RemoveFilter(fid)
            view.AddFilter(fh.Id)
            view.AddFilter(fl.Id)
            for fid, ogs, vis, en in keep:
                view.AddFilter(fid)
                view.SetFilterOverrides(fid, ogs)
                view.SetFilterVisibility(fid, vis)
                view.SetIsFilterEnabled(fid, en)
        for f in (fh, fl):
            view.SetIsFilterEnabled(f.Id, True)
            view.SetFilterVisibility(f.Id, True)
        view.SetFilterOverrides(fh.Id, host_overrides())
        return fl, fh

    def remove_filters(self, d, view):
        for name in (F_LINK, F_HOST):
            f = find_filter(d, name)
            if applied(view, f):
                view.RemoveFilter(f.Id)

    def set_hidden(self, d, view, ids, flags):
        hide, show = List[DB.ElementId](), List[DB.ElementId]()
        for i in ids:
            el = d.GetElement(i)
            want = flags[i.Value]
            if want and not el.IsHidden(view) and el.CanBeHidden(view):
                hide.Add(i)
            elif not want and el.IsHidden(view):
                show.Add(i)
        if hide.Count:
            view.HideElements(hide)
        if show.Count:
            view.UnhideElements(show)

    def Execute(self, uiapp):
        try:
            ud = uiapp.ActiveUIDocument
            d = ud.Document
            if self.clear_sel:
                self.clear_sel = False
                ud.Selection.SetElementIds(List[DB.ElementId]())
            view = d.ActiveView
            if view is None or view.IsTemplate:
                return
            if self.restore is not None:
                self.restore = None
                snap = self.initial.get(view.Id.Value)
                if snap:
                    t = DB.Transaction(d, u'Прозрачность связи: отмена')
                    t.Start()
                    ids = [DB.ElementId(k) for k in snap['hidden']]
                    self.set_hidden(d, view, ids, snap['hidden'])
                    if snap['link'] is None:
                        self.remove_filters(d, view)
                    else:
                        fl = find_filter(d, F_LINK)
                        if applied(view, fl):
                            view.SetFilterOverrides(fl.Id, link_overrides(snap['link']))
                    t.Commit()
                    ud.RefreshActiveView()
                if self.win:
                    self.win.done()
                return
            if self.value is None or not self.ids:
                return
            value, ids = self.value, list(self.ids)
            self.value = None
            ok = [i for i in ids if isinstance(d.GetElement(i), DB.RevitLinkInstance)]
            if not ok:
                self.win.status(u'В этом документе нет выбранных связей.', True)
                return
            tid = view.ViewTemplateId
            if tid != DB.ElementId.InvalidElementId:
                tpl = d.GetElement(tid)
                free = [p.Value for p in tpl.GetNonControlledTemplateParameterIds()]
                if int(DB.BuiltInParameter.VIS_GRAPHICS_FILTERS) not in free:
                    self.win.status(u'Фильтрами вида «%s» управляет шаблон «%s» — сними у шаблона галку «Фильтры» или открепи шаблон.' % (view.Name, tpl.Name), True)
                    return
            self.snapshot(d, view, ok)
            t = DB.Transaction(d, u'Прозрачность связи: %d %%' % value)
            t.Start()
            for i in ok:                         # убрать остатки «Графики элемента» от версий 0.1–0.2
                ogs = view.GetElementOverrides(i)
                if ogs.Transparency:
                    ogs.SetSurfaceTransparency(0)
                    view.SetElementOverrides(i, ogs)
            if value >= 100:
                self.set_hidden(d, view, ok, dict((i.Value, True) for i in ok))
            else:
                self.set_hidden(d, view, ok, dict((i.Value, False) for i in ok))
                if value <= 0:
                    self.remove_filters(d, view)
                else:
                    fl, fh = self.ensure_filters(d, view, ok)
                    view.SetFilterOverrides(fl.Id, link_overrides(value))
            d.Regenerate()
            t.Commit()
            ud.RefreshActiveView()           # без этого вид иногда не перерисовывается («через раз»)
            # проверка: в виде действительно стоит нужное значение
            fl_chk = find_filter(d, F_LINK)
            if 0 < value < 100 and fl_chk is not None and applied(view, fl_chk):
                got = view.GetFilterOverrides(fl_chk.Id).Transparency
                if got != int(value):
                    self.win.status(u'Revit не принял значение (%d вместо %d) — повтори.' % (got, value), True)
                    return
            other = [l for l in links_in(d) if l.Id not in ok and l.GetLinkDocument() is not None and not l.IsHidden(view)]
            note = u' Та же прозрачность ложится и на другие видимые связи (%d).' % len(other) if other and 0 < value < 100 else u''
            self.win.status(u'Вид «%s»: %s.%s' % (view.Name, u'скрыто' if value >= 100 else (u'как было' if value <= 0 else u'прозрачность %d %%' % value), note), False)
        except Exception as ex:
            if self.win:
                self.win.status(u'Ошибка: %s' % ex, True)

    def GetName(self):
        return 'db_link_transparency'


XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Прозрачность связи" Width="470" Height="600" MinWidth="400" MinHeight="480"
        WindowStartupLocation="CenterScreen" Topmost="True" FontFamily="Segoe UI" FontSize="13"
        Background="#F4F6F4">
  <Window.Resources>
    <SolidColorBrush x:Key="G" Color="#2E7D32"/>
    <SolidColorBrush x:Key="GL" Color="#E8F5E9"/>
    <SolidColorBrush x:Key="Ink" Color="#1B1F1C"/>
    <SolidColorBrush x:Key="Mute" Color="#7A847C"/>

    <Style x:Key="Pill" TargetType="Button">
      <Setter Property="Foreground" Value="{StaticResource Ink}"/>
      <Setter Property="Background" Value="White"/>
      <Setter Property="BorderBrush" Value="#D5DBD6"/>
      <Setter Property="Padding" Value="0,5"/>
      <Setter Property="Margin" Value="0,0,6,0"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="1" CornerRadius="14" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="#E8F5E9"/>
                <Setter TargetName="b" Property="BorderBrush" Value="#2E7D32"/>
              </Trigger>
              <Trigger Property="IsPressed" Value="True">
                <Setter TargetName="b" Property="Background" Value="#C8E6C9"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="Primary" TargetType="Button" BasedOn="{StaticResource Pill}">
      <Setter Property="Background" Value="#2E7D32"/>
      <Setter Property="BorderBrush" Value="#2E7D32"/>
      <Setter Property="Foreground" Value="White"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Padding" Value="18,7"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="{TemplateBinding Background}" CornerRadius="16" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="b" Property="Background" Value="#388E3C"/></Trigger>
              <Trigger Property="IsPressed" Value="True"><Setter TargetName="b" Property="Background" Value="#1B5E20"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="Ghost" TargetType="Button" BasedOn="{StaticResource Pill}">
      <Setter Property="Padding" Value="14,7"/>
    </Style>

    <Style x:Key="FillBtn" TargetType="RepeatButton">
      <Setter Property="Focusable" Value="False"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="RepeatButton">
            <Border Height="10" CornerRadius="5" Background="{TemplateBinding Background}"/>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="Fancy" TargetType="Slider">
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Slider">
            <Grid Height="34">
              <Border Height="10" CornerRadius="5" Background="#DDE3DE" VerticalAlignment="Center"/>
              <Track x:Name="PART_Track">
                <Track.DecreaseRepeatButton>
                  <RepeatButton Style="{StaticResource FillBtn}" Command="Slider.DecreaseLarge">
                    <RepeatButton.Background>
                      <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                        <GradientStop Color="#1B5E20" Offset="0"/>
                        <GradientStop Color="#81C784" Offset="1"/>
                      </LinearGradientBrush>
                    </RepeatButton.Background>
                  </RepeatButton>
                </Track.DecreaseRepeatButton>
                <Track.IncreaseRepeatButton>
                  <RepeatButton Style="{StaticResource FillBtn}" Background="Transparent" Command="Slider.IncreaseLarge"/>
                </Track.IncreaseRepeatButton>
                <Track.Thumb>
                  <Thumb Cursor="Hand">
                    <Thumb.Template>
                      <ControlTemplate TargetType="Thumb">
                        <Grid Width="26" Height="26">
                          <Ellipse Fill="#33000000" Margin="1,3,-1,-1"/>
                          <Ellipse Fill="White" Stroke="#2E7D32" StrokeThickness="3"/>
                          <Ellipse Fill="#2E7D32" Width="8" Height="8"/>
                        </Grid>
                      </ControlTemplate>
                    </Thumb.Template>
                  </Thumb>
                </Track.Thumb>
              </Track>
            </Grid>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style TargetType="ListBoxItem">
      <Setter Property="Padding" Value="10,6"/>
      <Setter Property="Margin" Value="0,1"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="ListBoxItem">
            <Border x:Name="b" CornerRadius="6" Background="Transparent" Padding="{TemplateBinding Padding}">
              <ContentPresenter/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="b" Property="Background" Value="#EEF3EF"/></Trigger>
              <Trigger Property="IsSelected" Value="True">
                <Setter TargetName="b" Property="Background" Value="#2E7D32"/>
                <Setter Property="Foreground" Value="White"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <Border Background="#1E2A22" Padding="18,14">
      <StackPanel>
        <TextBlock Text="Прозрачность связи" Foreground="White" FontSize="18" FontWeight="SemiBold"/>
        <TextBlock x:Name="sub" Foreground="#A5D6A7" FontSize="12" Margin="0,2,0,0"/>
      </StackPanel>
    </Border>

    <Grid Grid.Row="1" Margin="16,14,16,0">
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>
      <DockPanel>
        <TextBlock Text="Связи" FontWeight="SemiBold" Foreground="{StaticResource Ink}"/>
        <TextBlock Text="  Shift / Ctrl — несколько" Foreground="{StaticResource Mute}" FontSize="11" VerticalAlignment="Bottom"/>
        <Button x:Name="bSel" DockPanel.Dock="Right" HorizontalAlignment="Right" Style="{StaticResource Pill}" Padding="10,3" Margin="0"
                Content="↺ Взять выделенные"/>
      </DockPanel>
      <Border Grid.Row="1" Background="White" CornerRadius="10" BorderBrush="#DDE3DE" BorderThickness="1" Margin="0,8,0,12" Padding="4">
        <ListBox x:Name="lbLinks" SelectionMode="Extended" BorderThickness="0" Background="Transparent"
                 ScrollViewer.HorizontalScrollBarVisibility="Disabled"/>
      </Border>

      <Border Grid.Row="2" Background="White" CornerRadius="12" BorderBrush="#DDE3DE" BorderThickness="1" Padding="16,12">
        <StackPanel>
          <DockPanel>
            <TextBlock Text="Прозрачность" FontWeight="SemiBold" VerticalAlignment="Bottom" Margin="0,0,0,6"/>
            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" DockPanel.Dock="Right">
              <TextBox x:Name="tb" Text="0" FontSize="30" FontWeight="SemiBold" Foreground="#2E7D32" BorderThickness="0"
                       Background="Transparent" Width="70" HorizontalContentAlignment="Right" VerticalAlignment="Center"/>
              <TextBlock Text="%" FontSize="20" Foreground="#2E7D32" VerticalAlignment="Center" Margin="2,6,0,0"/>
            </StackPanel>
          </DockPanel>
          <Slider x:Name="sl" Style="{StaticResource Fancy}" Minimum="0" Maximum="100" SmallChange="1" LargeChange="10" Margin="0,4,0,0"/>
          <Grid Margin="2,0,2,8">
            <TextBlock Text="как есть" Foreground="{StaticResource Mute}" FontSize="11"/>
            <TextBlock Text="скрыта" Foreground="{StaticResource Mute}" FontSize="11" HorizontalAlignment="Right"/>
          </Grid>
          <UniformGrid Rows="1" Columns="6">
            <Button x:Name="b0" Style="{StaticResource Pill}" Content="0"/>
            <Button x:Name="b25" Style="{StaticResource Pill}" Content="25"/>
            <Button x:Name="b50" Style="{StaticResource Pill}" Content="50"/>
            <Button x:Name="b75" Style="{StaticResource Pill}" Content="75"/>
            <Button x:Name="b90" Style="{StaticResource Pill}" Content="90"/>
            <Button x:Name="b100" Style="{StaticResource Pill}" Content="100" Margin="0"/>
          </UniformGrid>
        </StackPanel>
      </Border>

      <TextBlock x:Name="st" Grid.Row="3" Margin="2,10,2,0" TextWrapping="Wrap" FontSize="12"/>
    </Grid>

    <Border Grid.Row="2" Background="White" BorderBrush="#DDE3DE" BorderThickness="0,1,0,0" Padding="16,12" Margin="0,12,0,0">
      <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
        <Button x:Name="bCancel" Style="{StaticResource Ghost}" Content="Отмена" Margin="0,0,8,0"
                ToolTip="Вернуть вид как было до открытия окна"/>
        <Button x:Name="bOk" Style="{StaticResource Primary}" Content="Применить" Margin="0"
                ToolTip="Оставить и закрыть. Сбросить — снова запустить и нажать 0"/>
      </StackPanel>
    </Border>
  </Grid>
</Window>
"""


def short_name(li):
    n = li.Name
    k = n.find(u' : ')
    return n[:k] if k > 0 else n


class OpacityWindow(forms.WPFWindow):
    def __init__(self, handler, event):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        try:
            import xyz_forms as _hf
            _hf.adaptive(self, 'linkopacity')
        except Exception:
            pass
        self.handler, self.event = handler, event
        handler.win = self
        self.busy = False
        sel = set(i.Value for i in uidoc.Selection.GetElementIds())
        links = links_in(doc)
        # выделенные — первыми, затем загруженные, затем выгруженные
        links.sort(key=lambda li: (0 if li.Id.Value in sel else 1,
                                   0 if li.GetLinkDocument() is not None else 1, short_name(li)))
        self.links = links
        for li in links:
            txt = short_name(li)
            if li.GetLinkDocument() is None:
                txt += u'   · не загружена'
            self.lbLinks.Items.Add(txt)
        self.sub.Text = u'Вид «%s»' % uidoc.ActiveView.Name
        self.pick(lambda li: li.Id.Value in sel)
        if self.lbLinks.SelectedItems.Count:
            self.lbLinks.ScrollIntoView(self.lbLinks.Items[0])
        self.lbLinks.SelectionChanged += self.on_links
        self.sl.ValueChanged += self.on_slider
        self.tb.TextChanged += self.on_text
        for b, v in ((self.b0, 0), (self.b25, 25), (self.b50, 50), (self.b75, 75), (self.b90, 90), (self.b100, 100)):
            b.Click += (lambda vv: (lambda s, e: self.set_value(vv)))(v)
        self.bSel.Click += self.on_take_selection
        self.bOk.Click += lambda s, e: self.Close()
        self.bCancel.Click += self.on_cancel
        # слайдер шлёт значения не чаще чем раз в 120 мс — Revit не захлёбывается
        self.timer = DispatcherTimer()
        self.timer.Interval = TimeSpan.FromMilliseconds(120)
        self.timer.Tick += self.on_tick
        self.pending = None
        self.handler.clear_sel = True        # снять выделение сразу, иначе розовая подсветка закрывает прозрачность
        self.event.Raise()
        self.read_current()
        if not self.links:
            self.status(u'В проекте нет связанных моделей RVT/IFC.', True)
        elif self.lbLinks.SelectedItems.Count == 0:
            self.status(u'Выбери связь в списке или выдели её в модели и нажми «Взять выделенные».', True)

    def status(self, txt, warn):
        self.st.Text = txt
        self.st.Foreground = Brushes.Firebrick if warn else Brushes.ForestGreen

    def pick(self, pred):
        self.busy = True
        self.lbLinks.SelectedItems.Clear()
        for i, li in enumerate(self.links):
            if pred(li):
                self.lbLinks.SelectedItems.Add(self.lbLinks.Items[i])
        self.busy = False

    def chosen(self):
        items = self.lbLinks.SelectedItems
        return [li.Id for i, li in enumerate(self.links) if items.Contains(self.lbLinks.Items[i])]

    def read_current(self):
        ids = self.chosen()
        if not ids:
            return
        try:
            v = current_value(uidoc.ActiveView, ids[0])
        except Exception:
            v = 0
        self.busy = True
        self.sl.Value = v
        self.tb.Text = str(int(v))
        self.busy = False

    def on_links(self, s, e):
        if not self.busy:
            self.read_current()

    def on_take_selection(self, s, e):
        sel = set(i.Value for i in uidoc.Selection.GetElementIds())
        self.pick(lambda li: li.Id.Value in sel)
        if self.lbLinks.SelectedItems.Count == 0:
            self.status(u'Среди выделенного нет связей.', True)
        else:
            self.read_current()
            self.handler.clear_sel = True
            self.event.Raise()

    def on_cancel(self, s, e):
        self.timer.Stop()
        self.handler.value = None
        self.handler.restore = True
        self.event.Raise()

    def done(self):
        self.Close()

    def set_value(self, v):
        self.busy = True
        self.sl.Value = v
        self.tb.Text = str(int(v))
        self.busy = False
        self.push(v, now=True)

    def on_slider(self, s, e):
        if self.busy:
            return
        v = int(round(self.sl.Value))
        self.busy = True
        self.tb.Text = str(v)
        self.busy = False
        self.push(v)

    def on_text(self, s, e):
        if self.busy:
            return
        try:
            v = int(float(self.tb.Text.replace(',', '.').replace('%', '').strip()))
        except Exception:
            return
        v = max(0, min(100, v))
        self.busy = True
        self.sl.Value = v
        self.busy = False
        self.push(v)

    def push(self, v, now=False):
        ids = self.chosen()
        if not ids:
            self.status(u'Не выбрана ни одна связь.', True)
            return
        self.pending = (ids, v)
        self.timer.Stop()
        if now:
            self.on_tick(None, None)
        else:
            self.timer.Start()

    def on_tick(self, s, e):
        self.timer.Stop()
        if self.pending is None:
            return
        ids, v = self.pending
        self.pending = None
        self.handler.ids = ids
        self.handler.value = v
        self.event.Raise()


handler = Applier()
event = UI.ExternalEvent.Create(handler)
OpacityWindow(handler, event).show(modal=False)
