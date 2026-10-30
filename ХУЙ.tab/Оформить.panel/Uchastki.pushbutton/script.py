# -*- coding: utf-8 -*-
"""Участки фреона — участки жидкостной линии, спецификация, красные марки номеров и
проверка схемы без пересчёта (панель «Пиздато»)."""
import os
import sys
for _m in ('uchastki', 'dress_scheme', 'stack_tags', 'hui_plane', 'hui_schedfmt'):
    sys.modules.pop(_m, None)
_align = os.path.join(os.path.dirname(os.path.dirname(__file__)), u'Align.pushbutton')
if _align not in sys.path:
    sys.path.append(_align)
import uchastki
import hui_forms as forms
from Autodesk.Revit.DB import ElementId, Transaction, TransactionGroup, View3D
from System.Collections.Generic import List

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
view = doc.ActiveView
TITLE = u'Участки фреона'

if not isinstance(view, View3D):
    forms.alert(u'Откройте 3D-схему фреонопровода (например, С.Х.VRF1) и запустите ещё раз.', title=TITLE, exitscript=True)

A_CHECK = u'Проверить схему — что без марки, что без номера (ничего не меняет)'
A_TAGS = u'Поставить марки номеров, где их нет — без пересчёта (быстро)'
A_MISSING = u'Пересчитать участки и спецификацию, марки номеров — только где их нет'
A_ALL = u'Пересчитать и расставить ВСЕ марки номеров заново'
A_NOTAGS = u'Пересчитать участки и спецификацию, марки не трогать'
A_FIRST = u'Посчитать участки, создать спецификацию и поставить марки номеров'
A_FIRST_NT = u'Посчитать участки и спецификацию, без марок'

# меню — по тому, что уже есть на виде (db 18.10: «почему "Пересчитать", если ещё не запускал?»)
st = uchastki.view_state(doc, view)
if not st['nums']:
    state_msg = u'Участки на этом виде ещё не считались.'
    opts = [A_FIRST, A_FIRST_NT]
elif st['tagged'] < st['nums']:
    state_msg = (u'Номера записаны: участков %d, с маркой номера %d, без марки %d.'
                 % (st['nums'], st['tagged'], st['nums'] - st['tagged']))
    opts = [A_CHECK, A_TAGS, A_MISSING, A_ALL, A_NOTAGS]
else:
    state_msg = u'Номера записаны: участков %d, марки номеров у всех.' % st['nums']
    opts = [A_CHECK, A_MISSING, A_ALL, A_NOTAGS]
if st['nums'] and st['nonum']:
    state_msg += u' Труб жидкостной линии без номера: %d — нужен пересчёт.' % st['nonum']

ans = forms.alert(u'Вид «%s».\n%s' % (view.Name, state_msg), title=TITLE, options=opts,
                  sub_msg=u'Проверка и «без пересчёта» читают уже записанные номера — быстро. Расчёт: '
                          u'номера, размеры, отводы, перепад, длина и спецификация; отмена — одним Ctrl+Z.')
if not ans:
    sys.exit()
if ans == A_FIRST:
    ans = A_MISSING
elif ans == A_FIRST_NT:
    ans = A_NOTAGS


def check_window():
    from System.Windows import Thickness, Visibility, ResizeMode, TextWrapping
    from System.Windows.Controls import ListBox, Button, TextBlock, Border
    from System.Windows.Forms import Screen
    items, n_all, n_tag = uchastki.check(doc, view)
    if not items:
        forms.alert(u'Вид «%s»: участков %d, у всех есть марка номера, все трубы жидкостной линии '
                    u'с номером.' % (view.Name, n_all), title=TITLE, exitscript=True)
    dlg = forms._Dialog(TITLE + u' — проверка', width=860)
    dlg.ResizeMode = ResizeMode.CanResize
    dlg._s.Text = u'вид «%s» · участков %d · с маркой номера %d · замечаний %d' % (
        view.Name, n_all, n_tag, len(items))
    dlg._s.Visibility = Visibility.Visible
    hint = TextBlock()
    hint.Text = (u'Данные — из уже записанных номеров участков, схема не пересчитывается. «Показать» '
                 u'(или двойной щелчок) — выделить и приблизить. Поставили марку или исправили — '
                 u'«Обновить список».')
    hint.TextWrapping = TextWrapping.Wrap
    hint.Foreground = forms._brush(forms.MUTE)
    hint.FontSize = 11.5
    hint.Margin = Thickness(0, 0, 0, 8)
    dlg.body.Children.Add(hint)
    lb = ListBox()
    lb.MaxHeight = max(240, Screen.PrimaryScreen.WorkingArea.Height - 360)
    lb.MinHeight = 200
    state = {'items': items}

    def fill():
        lb.Items.Clear()
        for it in state['items']:
            lb.Items.Add(it['title'])
    fill()
    fr = Border()
    fr.BorderBrush = forms._brush(forms.LINE)
    fr.BorderThickness = Thickness(1)
    fr.Child = lb
    dlg.body.Children.Add(fr)

    def show(k):
        its = state['items']
        if k < 0 or k >= len(its):
            return
        ids = List[ElementId](its[k]['ids'])
        try:
            uidoc.Selection.SetElementIds(ids)
            uidoc.ShowElements(ids)
            uidoc.RefreshActiveView()
        except Exception as ex:
            forms.alert(u'Не удалось показать: %s' % ex, title=TITLE)

    def step(d):
        k = lb.SelectedIndex + d if lb.SelectedIndex >= 0 else 0
        k = max(0, min(len(state['items']) - 1, k))
        lb.SelectedIndex = k
        lb.ScrollIntoView(lb.SelectedItem)
        show(k)

    def refresh(s, e):
        its, a, b = uchastki.check(doc, view)
        state['items'] = its
        fill()
        forms.set_subtitle(dlg, u'вид «%s» · участков %d · с маркой номера %d · замечаний %d' % (
            view.Name, a, b, len(its)))
    lb.MouseDoubleClick += lambda s, e: show(lb.SelectedIndex)
    for text, h, prim, canc in ((u'◀ Назад', lambda s, e: step(-1), False, False),
                                (u'Показать', lambda s, e: step(0), True, False),
                                (u'Вперёд ▶', lambda s, e: step(1), False, False),
                                (u'Обновить список', refresh, False, False),
                                (u'Закрыть', lambda s, e: dlg.Close(), False, True)):
        b = Button()
        b.Content = text
        b.Margin = Thickness(8, 0, 0, 0)
        b.MinWidth = 90
        b.IsDefault = prim
        b.IsCancel = canc
        b.Click += h
        dlg.footer.Children.Add(b)
    forms.adaptive(dlg, 'uchastki')
    dlg.ShowDialog()


if ans == A_CHECK:
    check_window()

def place_tags_only():
    import dress_scheme
    tg = TransactionGroup(doc, u'Участки фреона: марки номеров · ' + view.Name)
    tg.Start()
    try:
        n_ok, miss = uchastki.place_number_tags(doc, view, uchastki.recorded_segs(doc, view),
                                                dress_scheme, only_missing=True)
        tg.Assimilate()
    except Exception as ex:
        tg.RollBack()
        forms.alert(u'Ошибка: %s' % ex, title=TITLE)
        return
    forms.alert(u'Марок номеров поставлено: %d' % n_ok +
                (u'\nБез места: %s — поставьте вручную («Прямая выноска»)' % u', '.join(miss) if miss else u''),
                title=TITLE, sub_msg=u'Участки не пересчитывались — номера взяты из модели.')


def recalc():
    tg = TransactionGroup(doc, u'Участки фреона: ' + view.Name)
    tg.Start()
    try:
        t = Transaction(doc, u'Участки фреона: параметры')
        t.Start()
        # параметры — до пересчёта: db_Единица измерения, db_Трубы_* из ФОП, db_Участок_* на
        # охладители (арматура воздуховодов) — иначе их нет в ведомости оборудования (db 19.10)
        binds = uchastki.ensure_pipe_params(doc)
        if binds:
            doc.Regenerate()
        segs, rep = uchastki.run(doc, view, write=True)
        if binds:
            rep.append(u'Параметры: ' + u'; '.join(binds))
        made = []
        for n in uchastki.systems(segs):
            vs, new = uchastki.ensure_schedule(doc, n)
            made.append(u'«%s» — %s' % (vs.Name, new if isinstance(new, type(u'')) else (u'создана' if new else u'обновлена')))
            ve, st = uchastki.ensure_equipment_schedule(doc, n, uchastki.sys_label(doc, view, n))
            made.append(u'«%s» — %s' % (ve.Name, st))
            tubes, elbows, dia, prep = uchastki.pipe_rows(doc, view, n)   # трубы и отводы жидк. + газ. (db 18.10)
            rep.extend(prep)
            made.extend(uchastki.ensure_pipe_tables(doc, view, n, dia))
        t.Commit()
        if ans in (A_MISSING, A_ALL):
            import dress_scheme
            n_ok, miss = uchastki.place_number_tags(doc, view, segs, dress_scheme, only_missing=(ans == A_MISSING))
            rep.append(u'Марок номеров поставлено: %d' % n_ok +
                       (u'; без места: %s — поставьте вручную («Прямая выноска»)' % u', '.join(miss) if miss else u''))
        tg.Assimilate()
    except Exception as ex:
        tg.RollBack()
        forms.alert(u'Ошибка: %s' % ex, title=TITLE)
        return
    forms.alert(u'\n'.join(rep + [u'Спецификация: ' + u', '.join(made)]), title=TITLE,
                sub_msg=u'Этот отчёт всегда можно получить снова: «Участки фреона» → «Проверить схему».')


# НЕ вызывать sys.exit() после изменений модели: pyRevit тогда откатывает всю команду
# (db 18.10: марки поставились, после «ОК» исчезли).
if ans == A_TAGS:
    place_tags_only()
elif ans != A_CHECK:
    recalc()
