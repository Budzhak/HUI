# -*- coding: utf-8 -*-
"""Прямая выноска: щёлкнуть марку, щёлкнуть, где ей стоять — выноска становится прямой
(сверху/снизу/слева/справа), конец выноски сдвигается вдоль оси трубы. По кругу,
Esc — выход. Каждая марка — отдельная отмена Ctrl+Z."""
import sys
sys.modules.pop('hui_leader', None)
sys.modules.pop('hui_plane', None)
import hui_leader
import hui_plane
import hui_forms as forms
from Autodesk.Revit.DB import IndependentTag, Transaction
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
view = doc.ActiveView
TITLE = u'Прямая выноска'


class _Tags(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, IndependentTag)

    def AllowReference(self, r, p):
        return False


hui_plane.Transaction = Transaction
tok = hui_plane.push(doc, view, view.ViewDirection, view.Origin, u'Прямая выноска: плоскость')
disp = hui_leader.displacements(doc, view)
n = 0
try:
    pre = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    pre = [e for e in pre if isinstance(e, IndependentTag)]
    while True:
        try:
            if pre:
                tag = pre.pop(0)
            else:
                r = uidoc.Selection.PickObject(ObjectType.Element, _Tags(), u'Прямая выноска: щёлкните марку (Esc — выход)')
                tag = doc.GetElement(r.ElementId)
            head = uidoc.Selection.PickPoint(u'Прямая выноска: где поставить марку (Esc — выход)')
        except OperationCanceledException:
            break
        t = Transaction(doc, u'Прямая выноска')
        t.Start()
        try:
            hui_leader.straight(doc, view, tag, head, disp)
            t.Commit()
            n += 1
        except Exception as ex:
            t.RollBack()
            forms.alert(u'Не получилось: %s' % ex, title=TITLE)
finally:
    hui_plane.pop(doc, view, tok)
