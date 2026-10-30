# -*- coding: utf-8 -*-
__title__ = u"Масштаб\n3D"
__author__ = u"db"
__doc__ = u"""Дописывает масштаб в заголовок 3D-видов на листах.

Работает только с видовыми экранами, у которых тип из списка TYPE_NAMES
(по умолчанию «Заголовок на листе_3D_Масштаб»). Остальные 3D-виды не трогает.

В параметр вида «Заголовок на листе» пишется: <заголовок> ( 1 : <масштаб>).
Старая приписка масштаба заменяется, поэтому кнопку можно нажимать повторно
после смены масштаба. Если заголовок пустой, берётся имя вида.
Перспективные виды пропускаются (у них нет масштаба).

Shift+клик — выбрать типы видовых экранов вручную на один запуск."""

import re
from pyrevit import revit, DB, script, forms
import xyz_forms as forms   # единый стиль окон XYZ (lib/xyz_forms.py)

# Типы видовых экранов, для которых работает кнопка
TYPE_NAMES = [u"Заголовок на листе_3D_Масштаб"]

doc = revit.doc
out = script.get_output()
forms.style_output(out)
SCALE_RE = re.compile(u"\\s*\\(\\s*1\\s*:\\s*[\\d\\s.,]+\\)\\s*$")


def type_name(el_type):
    p = el_type.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    return p.AsString() if p else u""


viewports = list(DB.FilteredElementCollector(doc).OfClass(DB.Viewport))

names = TYPE_NAMES
if __shiftclick__:  # noqa: F821
    all_types = sorted(set(type_name(doc.GetElement(vp.GetTypeId())) for vp in viewports))
    picked = forms.SelectFromList.show(all_types, title=u"Типы видовых экранов для масштаба",
                                       multiselect=True, button_name=u"Применить")
    if not picked:
        script.exit()
    names = picked

changed, same, skipped = [], 0, []
with revit.Transaction(u"Масштаб в заголовки 3D-видов"):
    for vp in viewports:
        vtype = doc.GetElement(vp.GetTypeId())
        if type_name(vtype) not in names:
            continue
        view = doc.GetElement(vp.ViewId)
        if not isinstance(view, DB.View3D):
            continue
        sheet = doc.GetElement(vp.SheetId)
        where = u"{} — {}".format(sheet.SheetNumber, view.Name)
        if view.IsPerspective:
            skipped.append(where + u" (перспектива)")
            continue
        p = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
        if p is None or p.IsReadOnly:
            skipped.append(where + u" (параметр недоступен)")
            continue
        base = SCALE_RE.sub(u"", p.AsString() or u"").strip() or view.Name
        new = u"{} ( 1 : {})".format(base, view.Scale)
        if new == (p.AsString() or u""):
            same += 1
            continue
        p.Set(new)
        changed.append(u"{}: {}".format(where, new))

out.print_md(u"**Типы:** " + u", ".join(names))
out.print_md(u"**Обновлено:** {}  ·  **без изменений:** {}".format(len(changed), same))
for c in changed:
    out.print_md(u"- " + c)
if skipped:
    out.print_md(u"**Пропущено:**")
    for s in skipped:
        out.print_md(u"- " + s)
if not changed and not same and not skipped:
    forms.alert(u"Нет 3D-видов на листах с типом видового экрана:\n" + u"\n".join(names))
