# -*- coding: utf-8 -*-
"""
Нумерация по часовой стрелке: записывает номера в параметр элементов,
помеченных выделенными марками, обходя марки по кругу (как стрелка часов)
вокруг центра выделения.

Вызывается из script.py:  renumber.run(doc, uidoc, tags)
"""
import math
import re

from Autodesk.Revit.DB import *
from pyrevit import forms, script
import xyz_forms as forms   # единый стиль окон XYZ (lib/xyz_forms.py)


START_TOP = u"С 12 часов (строго сверху от центра)"
START_CORNER = u"С левого верхнего угла (по периметру)"


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _writable(p):
    return (p is not None and not p.IsReadOnly
            and p.StorageType in (StorageType.String, StorageType.Integer))


def _find_param(el, name):
    """Ищет параметр по имени: сначала у экземпляра, потом у типа.
    Возвращает (parameter, is_type)."""
    p = el.LookupParameter(name)
    if _writable(p):
        return p, False
    try:
        t = el.Document.GetElement(el.GetTypeId())
        if t is not None:
            pt = t.LookupParameter(name)
            if _writable(pt):
                return pt, True
    except Exception:
        pass
    return None, False


def _param_names(elems):
    """Имена записываемых текстовых/целых параметров, которые есть
    хотя бы у половины элементов (экземпляр или тип)."""
    counts = {}
    for el in elems:
        names = set()
        for src in (el, el.Document.GetElement(el.GetTypeId())):
            if src is None:
                continue
            for p in src.Parameters:
                if _writable(p):
                    names.add(p.Definition.Name)
        for n in names:
            counts[n] = counts.get(n, 0) + 1
    need = max(1, len(elems) // 2)
    return sorted([n for n, c in counts.items() if c >= need])


def _split_start(text):
    """'П1' -> ('П', 1, 1);  '1.05' -> ('1.', 5, 2);  '7' -> ('', 7, 1)"""
    m = re.match(r'^(.*?)(\d+)$', text.strip())
    if not m:
        return None
    digits = m.group(2)
    return m.group(1), int(digits), len(digits) if digits.startswith(u"0") else 1


def run(doc, uidoc, tags):
    view = doc.ActiveView
    R, U = view.RightDirection, view.UpDirection

    # --- марки -> элементы (только элементы текущей модели) ---
    items = []
    for t in tags:
        try:
            ids = list(t.GetTaggedLocalElementIds())
        except Exception:
            ids = []
        for eid in ids:
            el = doc.GetElement(eid)
            if el is not None:
                h = t.TagHeadPosition
                items.append((t, el, h.DotProduct(R), h.DotProduct(U)))
    if not items:
        forms.alert(u"Выделенные марки не помечают элементы этой модели.",
                    title=u"Нумерация по часовой")
        return

    # --- параметр ---
    cfg = script.get_config()
    names = _param_names([it[1] for it in items])
    if not names:
        forms.alert(u"У помеченных элементов нет общих записываемых параметров.",
                    title=u"Нумерация по часовой")
        return
    last = cfg.get_option('renumber_param', u"ADSK_Позиция")
    if last in names:
        names.remove(last)
        names.insert(0, last)
    pname = forms.SelectFromList.show(
        names, title=u"В какой параметр записывать номер?",
        button_name=u"Далее", multiselect=False)
    if not pname:
        return

    # --- начало обхода ---
    start = forms.CommandSwitchWindow.show(
        [START_CORNER, START_TOP],
        message=u"Откуда начинать нумерацию (по часовой стрелке)?")
    if not start:
        return

    # --- стартовый номер (можно с префиксом: П1, 1.1, 01) ---
    first = forms.ask_for_string(
        default=cfg.get_option('renumber_start', u"1"),
        prompt=u"Первый номер.\nМожно с префиксом: П1, 1.1, 01",
        title=u"Нумерация по часовой")
    if first is None:
        return
    parsed = _split_start(first)
    if parsed is None:
        forms.alert(u"Номер должен заканчиваться цифрами: 1, П1, 1.1",
                    title=u"Нумерация по часовой")
        return
    prefix, num, width = parsed

    # параметр типа меняет все экземпляры типа - тогда один номер на тип обязателен
    any_type_param = any(_find_param(it[1], pname)[1] for it in items)
    if any_type_param:
        same_type = True
        forms.alert(u"«{}» у части элементов - параметр типа.\n"
                    u"Номер будет общим для всех элементов одного типоразмера."
                    .format(pname), title=u"Нумерация по часовой")
    else:
        same_type = forms.alert(
            u"Одинаковым типоразмерам давать один номер?\n\n"
            u"Да - как позиции в спецификации (один тип = один номер).\n"
            u"Нет - каждый элемент получает свой номер.",
            title=u"Нумерация по часовой", yes=True, no=True)

    # --- порядок: угол по часовой стрелке от стартового направления ---
    us = [it[2] for it in items]
    vs = [it[3] for it in items]
    cu = (min(us) + max(us)) / 2.0
    cv = (min(vs) + max(vs)) / 2.0
    if start == START_CORNER:
        a0 = math.atan2(min(us) - cu, max(vs) - cv)     # направление на левый верхний угол
    else:
        a0 = 0.0                                          # 12 часов

    def key(it):
        du, dv = it[2] - cu, it[3] - cv
        a = (math.atan2(du, dv) - a0) % (2 * math.pi)     # 0 = старт, растёт по часовой
        return (round(a, 6), du * du + dv * dv)

    items.sort(key=key)

    # --- номера ---
    assigned = {}        # id элемента или типа -> номер
    plan = []            # (param, value)
    n = num
    done_elems = set()
    for t, el, _, _ in items:
        eid = _iv(el.Id)
        if eid in done_elems:
            continue
        done_elems.add(eid)
        p, is_type = _find_param(el, pname)
        if p is None:
            continue
        group = (u"T", _iv(el.GetTypeId())) if (same_type or is_type) else (u"E", eid)
        if group not in assigned:
            assigned[group] = u"{}{}".format(prefix, str(n).zfill(width))
            n += 1
        plan.append((p, is_type, group, assigned[group]))

    tr = Transaction(doc, u"Нумерация по часовой стрелке")
    tr.Start()
    written, errors, done_type = 0, 0, set()
    try:
        for p, is_type, group, value in plan:
            if is_type:
                if group in done_type:
                    continue
                done_type.add(group)
            try:
                if p.StorageType == StorageType.Integer:
                    digits = re.sub(r'\D', u"", value)
                    p.Set(int(digits))
                else:
                    p.Set(value)
                written += 1
            except Exception:
                errors += 1
        tr.Commit()
    except Exception as ex:
        if tr.HasStarted() and not tr.HasEnded():
            tr.RollBack()
        forms.alert(u"Ошибка, изменения отменены:\n\n{}".format(ex),
                    title=u"Нумерация по часовой")
        return

    cfg.renumber_param = pname
    cfg.renumber_start = first
    script.save_config()

    msg = [u"Параметр: {}".format(pname),
           u"Марок: {}".format(len(tags)),
           u"Номеров выдано: {} ({} - {})".format(
               len(assigned), u"{}{}".format(prefix, str(num).zfill(width)),
               u"{}{}".format(prefix, str(n - 1).zfill(width))),
           u"Записано значений: {}".format(written)]
    skipped = len(done_elems) - len(plan)
    if skipped:
        msg.append(u"Нет параметра у элементов: {}".format(skipped))
    if errors:
        msg.append(u"Ошибок записи: {}".format(errors))
    msg.append(u"")
    msg.append(u"Отмена - Ctrl+Z.")
    forms.alert(u"\n".join(msg), title=u"Нумерация по часовой")


# ============================================================
# Нумерация прокликиванием: пользователь по очереди кликает марки,
# каждой присваивается следующий номер начиная с введённого.
# Esc - завершить. Каждый клик - отдельная транзакция, поэтому
# Ctrl+Z отменяет последний клик, а номер виден сразу.
# ============================================================
def run_click(doc, uidoc):
    from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
    from Autodesk.Revit.Exceptions import OperationCanceledException

    class _TagFilter(ISelectionFilter):
        def AllowElement(self, el):
            return isinstance(el, IndependentTag)

        def AllowReference(self, ref, pt):
            return False

    cfg = script.get_config()
    first = forms.ask_for_string(
        default=cfg.get_option('click_next', u"1"),
        prompt=u"С какого номера начать?\nМожно с префиксом: П1, 1.1, 01\n\n"
               u"Дальше кликайте марки по очереди, Esc - завершить.",
        title=u"Нумерация кликами")
    if first is None:
        return
    parsed = _split_start(first)
    if parsed is None:
        forms.alert(u"Номер должен заканчиваться цифрами: 1, П1, 1.1",
                    title=u"Нумерация кликами")
        return
    prefix, n, width = parsed
    n_start = n

    pname = None
    type_warned = False
    done = 0
    last_value = None
    flt = _TagFilter()

    while True:
        value = u"{}{}".format(prefix, str(n).zfill(width))
        try:
            ref = uidoc.Selection.PickObject(
                ObjectType.Element, flt,
                u"Марка получит № {}   (Esc - завершить)".format(value))
        except OperationCanceledException:
            break
        except Exception:
            break
        tag = doc.GetElement(ref.ElementId)
        try:
            elems = [doc.GetElement(i) for i in tag.GetTaggedLocalElementIds()]
            elems = [e for e in elems if e is not None]
        except Exception:
            elems = []
        if not elems:
            forms.alert(u"Эта марка не помечает элемент текущей модели.",
                        title=u"Нумерация кликами")
            continue

        # параметр выбираем один раз - по первому кликнутому элементу
        if pname is None:
            names = _param_names(elems)
            if not names:
                forms.alert(u"У элемента нет записываемых параметров.",
                            title=u"Нумерация кликами")
                continue
            last = cfg.get_option('renumber_param', u"ADSK_Позиция")
            if last in names:
                names.remove(last)
                names.insert(0, last)
            pname = forms.SelectFromList.show(
                names, title=u"В какой параметр записывать номер?",
                button_name=u"Выбрать", multiselect=False)
            if not pname:
                break

        params = []
        for e in elems:
            p, is_type = _find_param(e, pname)
            if p is not None:
                params.append((p, is_type))
        if not params:
            forms.alert(u"У элемента нет параметра «{}».".format(pname),
                        title=u"Нумерация кликами")
            continue
        if any(t for _, t in params) and not type_warned:
            type_warned = True
            if not forms.alert(u"«{}» у этого элемента - параметр типа: номер получат "
                               u"все элементы этого типоразмера.\n\nПродолжить?".format(pname),
                               title=u"Нумерация кликами", yes=True, no=True):
                continue

        tr = Transaction(doc, u"Номер {} марке".format(value))
        tr.Start()
        try:
            for p, _ in params:
                if p.StorageType == StorageType.Integer:
                    p.Set(int(re.sub(r'\D', u"", value)))
                else:
                    p.Set(value)
            tr.Commit()
        except Exception as ex:
            if tr.HasStarted() and not tr.HasEnded():
                tr.RollBack()
            forms.alert(u"Не удалось записать номер:\n\n{}".format(ex),
                        title=u"Нумерация кликами")
            continue
        uidoc.RefreshActiveView()
        done += 1
        last_value = value
        n += 1

    # следующий свободный номер запоминаем - можно продолжить позже
    cfg.click_next = u"{}{}".format(prefix, str(n).zfill(width))
    if pname:
        cfg.renumber_param = pname
    script.save_config()
    if done:
        forms.alert(u"Пронумеровано марок: {}  ({} - {})\n"
                    u"Следующий номер: {}\n\n"
                    u"Ctrl+Z отменяет по одному клику.".format(
                        done, u"{}{}".format(prefix, str(n_start).zfill(width)), last_value,
                        cfg.click_next),
                    title=u"Нумерация кликами")
