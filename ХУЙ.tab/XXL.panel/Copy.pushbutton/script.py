# -*- coding: utf-8 -*-
"""Под копирку — оформление активной ведомости на другие ведомости с теми же графами
(панель «Ёбань», db 18.10.2026). Shift+щелчок — ширины граф активной ведомости."""
import sys
sys.modules.pop('hui_schedfmt', None)
import hui_schedfmt as sf
import hui_forms as forms
from Autodesk.Revit.DB import Transaction, TransactionGroup, ViewSchedule

doc = __revit__.ActiveUIDocument.Document
src = doc.ActiveView
TITLE = u'Под копирку'


def pick_targets(cands):
    from System.Windows import Thickness, TextWrapping
    from System.Windows.Controls import CheckBox, ScrollViewer, StackPanel, ScrollBarVisibility, TextBlock
    dlg = forms._Dialog(TITLE, width=560)
    dlg._s.Text = u'образец — «%s»' % src.Name
    from System.Windows import Visibility as _V
    dlg._s.Visibility = _V.Visible
    t = dlg.add_text(u'Ведомости с теми же графами. Отмеченным достанется оформление образца: '
                     u'ширина и порядок граф, заголовки граф и их выравнивание, шрифты и стиль ячеек, '
                     u'заголовок ведомости, группы заголовков, типы текста. Высоту строк шапки и данных Revit задаёт сам — её не переносит. Данные не меняются; '
                     u'сортировка, итоги и фильтры — по флажкам ниже. Отмена — одним Ctrl+Z.', muted=True, size=12)
    t.Margin = Thickness(0, 0, 0, 10)
    sp = StackPanel()
    boxes = []
    for vs, reorder in cands:
        cb = CheckBox()
        cb.Content = vs.Name + (u'   · порядок граф другой — станет как в образце' if reorder else u'')
        cb.IsChecked = True
        cb.Margin = Thickness(0, 3, 0, 3)
        sp.Children.Add(cb)
        boxes.append((cb, vs))
    sv = ScrollViewer()
    sv.MaxHeight = 320
    sv.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
    sv.Content = sp
    dlg.body.Children.Add(sv)
    h = dlg.add_text(u'Ещё перенести:', size=13)
    h.Margin = Thickness(0, 12, 0, 4)
    extra = []
    for key, text in (('sort', u'Сортировку и группирование, общий итог, «Для каждого экземпляра»'),
                      ('calc', u'Итоги граф: «Вычислять итоги», мин./макс., указание нескольких значений'),
                      ('filt', u'Фильтры (если у ведомости есть фильтр по той же графе — её значение '
                               u'остаётся: «Имя системы = Х1.ККБ2» не станет «Х1.ККБ1»)')):
        cb = CheckBox()
        tb = TextBlock()
        tb.Text = text
        tb.TextWrapping = TextWrapping.Wrap
        cb.Content = tb
        cb.IsChecked = True
        cb.Margin = Thickness(0, 3, 0, 3)
        dlg.body.Children.Add(cb)
        extra.append((key, cb))
    dlg.add_button(u'Отмена', None, cancel=True)
    dlg.add_button(u'Применить', '__ok__', primary=True)
    dlg.ShowDialog()
    if dlg.response != '__ok__':
        return [], set()
    return [vs for cb, vs in boxes if cb.IsChecked], set(k for k, cb in extra if cb.IsChecked)


def main():
    if not isinstance(src, ViewSchedule):
        forms.alert(u'Откройте ведомость-образец (уже настроенную) и нажмите ещё раз.', title=TITLE)
        return
    cands = sf.candidates(doc, src)
    if not cands:
        forms.alert(u'В проекте нет других ведомостей с теми же графами, что у «%s».' % src.Name, title=TITLE,
                    sub_msg=u'Графы сравниваются по параметрам, а не по подписям: «№ участка» и «Номер» '
                            u'из одного параметра — одна графа.')
        return
    targets, opts = pick_targets(cands)
    if not targets:
        return
    tg = TransactionGroup(doc, u'Под копирку: ' + src.Name)
    tg.Start()
    rep = []
    try:
        for vs in targets:
            t = Transaction(doc, u'Под копирку → ' + vs.Name)
            t.Start()
            notes = sf.copy_format(doc, src, vs)
            if 'calc' in opts:
                notes += sf.copy_field_calc(doc, src, vs)
            if 'sort' in opts:
                notes += sf.copy_sorting(doc, src, vs)
            if 'filt' in opts:
                notes += sf.copy_filters(doc, src, vs)
            t.Commit()
            rep.append(u'«%s» — готово' % vs.Name + (u'\n    ' + u'\n    '.join(notes) if notes else u''))
        tg.Assimilate()
    except Exception as ex:
        tg.RollBack()
        forms.alert(u'Ошибка: %s\nНичего не изменено.' % ex, title=TITLE)
        return
    forms.alert(u'\n'.join(rep), title=TITLE, sub_msg=u'Образец: «%s». Отмена — Ctrl+Z.' % src.Name)


# без sys.exit() после изменений — pyRevit откатил бы команду (db 18.10)
main()
