# -*- coding: utf-8 -*-
"""
Рабочая плоскость 3D-вида схемы.

Правило (17.10.2026): у вида схемы В ПОКОЕ — горизонтальная плоскость (Z = 0).
  * Высотные отметки «db_Стрелка_Проектная_Схемы_…» строятся в той плоскости,
    что действует в момент их создания (все точки отметки лежат на одной высоте).
    Создана при наклонной плоскости — текст отметки уезжает от полки.
  * Для «Указать точку» (PickPoint) на 3D-виде скрипты XYZ ставят ВРЕМЕННУЮ
    плоскость вдоль экрана и после щелчков возвращают горизонтальную:
        tok = xyz_plane.push(doc, view, view.ViewDirection, origin)
        try:   ... PickPoint ...
        finally: xyz_plane.pop(doc, view, tok)
  * pop всегда оставляет виду горизонтальную плоскость, даже если до push стояла
    другая (так плоскость, «застрявшая» после сбоя или ручных действий, не живёт
    дальше), и удаляет временную.
  * ensure_flat — проверка в начале каждой команды «По красоте»: плоскость не
    горизонтальная — ставится горизонтальная, запись в журнал %TEMP%\\zb_plane_log.txt
    (чтобы найти команду, которая её сбивает).
"""
import io
import os
import time

from Autodesk.Revit.DB import Plane, SketchPlane, Transaction, View3D, XYZ

LOG = os.path.join(os.environ.get('TEMP', u'.'), u'zb_plane_log.txt')

NAMES = {'flat': u'горизонтальная', 'screen': u'вдоль экрана',
         'other': u'наклонная', 'none': u'не задана'}


def _log(msg):
    try:
        with io.open(LOG, 'a', encoding='utf-8') as f:
            f.write(u'{} {}\n'.format(time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass


def state(view):
    """'flat' | 'screen' | 'other' | 'none'; None — не 3D-вид (проверка не нужна)."""
    if not isinstance(view, View3D):
        return None
    sp = view.SketchPlane
    if sp is None:
        return 'none'
    try:
        n = sp.GetPlane().Normal
    except Exception:
        return 'other'
    if abs(n.Z) > 0.999:
        return 'flat'
    try:
        if abs(abs(n.DotProduct(view.ViewDirection)) - 1.0) < 1e-3:
            return 'screen'
    except Exception:
        pass
    return 'other'


def _set_flat(doc, view):
    view.SketchPlane = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero))


def ensure_flat(doc, view, where=u'', name=u"Рабочая плоскость вида — горизонтальная"):
    """Ставит виду горизонтальную плоскость, если стоит другая.
    Возвращает прежнее состояние ('screen'/'other'/'none'), если пришлось менять, иначе None."""
    st = state(view)
    if st is None or st == 'flat':
        return None
    t = Transaction(doc, name)
    t.Start()
    try:
        _set_flat(doc, view)
        t.Commit()
    except Exception as ex:
        t.RollBack()
        _log(u'{} | вид «{}»: плоскость {} — НЕ удалось вернуть горизонтальную: {}'.format(
            where, view.Name, NAMES.get(st, st), ex))
        raise
    _log(u'{} | вид «{}»: плоскость была {} — поставлена горизонтальная'.format(
        where, view.Name, NAMES.get(st, st)))
    return st


def push(doc, view, normal, origin, name=u"Временная рабочая плоскость"):
    old = view.SketchPlane
    old_id = old.Id if old is not None else None
    t = Transaction(doc, name)
    t.Start()
    try:
        sp = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(normal, origin))
        view.SketchPlane = sp
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return (old_id, sp.Id)


def pop(doc, view, token, name=u"Вернуть рабочую плоскость вида"):
    """Возвращает виду горизонтальную плоскость (прежнюю, если она была горизонтальной)
    и удаляет временную. True — получилось."""
    if not token:
        return True
    old_id, tmp_id = token
    t = Transaction(doc, name)
    t.Start()
    try:
        old = doc.GetElement(old_id) if old_id is not None else None
        flat_old = False
        if old is not None:
            try:
                flat_old = abs(old.GetPlane().Normal.Z) > 0.999
            except Exception:
                flat_old = False
        if flat_old:
            view.SketchPlane = old
        else:
            _set_flat(doc, view)
        try:
            doc.Delete(tmp_id)
        except Exception:
            pass
        t.Commit()
        return True
    except Exception as ex:
        t.RollBack()
        _log(u'pop | вид «{}»: не удалось вернуть плоскость: {}'.format(view.Name, ex))
    # запасной путь: хотя бы горизонтальная плоскость
    try:
        ensure_flat(doc, view, u'pop (запасной путь)')
        return True
    except Exception:
        return False
