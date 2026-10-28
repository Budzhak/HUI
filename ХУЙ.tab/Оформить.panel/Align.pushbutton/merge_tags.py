# -*- coding: utf-8 -*-
"""
Объединение одинаковых марок: несколько марок одного типа с одинаковым
текстом заменяются одной маркой с несколькими основами (как команда
Revit "Добавить/удалить основу"), лишние марки удаляются.

Вызывается из script.py:  merge_tags.run(doc, uidoc, tags)
"""
from System.Collections.Generic import List
from Autodesk.Revit.DB import *
from pyrevit import forms
import hui_forms as forms   # единый стиль окон ХУЙ (lib/hui_forms.py)


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _ref_key(r):
    """Ключ основы: элемент модели (+ элемент связи, если марка по связи)."""
    try:
        return (_iv(r.ElementId), _iv(r.LinkedElementId))
    except Exception:
        return (_iv(r.ElementId), -1)


def _leader_end(tag, r):
    try:
        if tag.HasLeader and tag.LeaderEndCondition == LeaderEndCondition.Free:
            return tag.GetLeaderEnd(r)
    except Exception:
        pass
    return None


def run(doc, uidoc, tags):
    view = doc.ActiveView
    R, U = view.RightDirection, view.UpDirection

    # --- группы: тип марки + текст марки + вид ---
    groups = {}
    order = []
    for t in tags:
        try:
            text = (t.TagText or u"").strip()
        except Exception:
            text = u""
        k = (_iv(t.GetTypeId()), text, _iv(t.OwnerViewId))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(t)

    mergeable = [k for k in order if len(groups[k]) > 1]
    if not mergeable:
        forms.alert(u"Среди выделенных нет одинаковых марок.\n\n"
                    u"Одинаковые = один тип марки и одинаковый текст.",
                    title=u"Объединить марки")
        return

    preview = []
    for k in mergeable[:12]:
        preview.append(u"   «{}» × {}".format(k[1] or u"(пусто)", len(groups[k])))
    if len(mergeable) > 12:
        preview.append(u"   ... и ещё {}".format(len(mergeable) - 12))
    OPT_MERGE = u"Объединить, выноски сходятся в общую полку"
    OPT_SEPARATE = u"Объединить, каждая выноска отдельно"
    choice = forms.CommandSwitchWindow.show(
        [OPT_MERGE, OPT_SEPARATE],
        message=u"Групп для объединения: {}. {}".format(
            len(mergeable), u"; ".join(p.strip() for p in preview[:4])))
    if not choice:
        return
    merge_elbows = choice == OPT_MERGE

    merged_groups, removed, errors = 0, 0, []
    tr = Transaction(doc, u"Объединить одинаковые марки")
    tr.Start()
    try:
        for k in mergeable:
            grp = groups[k]
            # главная марка - ближайшая к центру группы (по положению голов)
            us = [t.TagHeadPosition.DotProduct(R) for t in grp]
            vs = [t.TagHeadPosition.DotProduct(U) for t in grp]
            cu, cv = sum(us) / len(us), sum(vs) / len(vs)
            main = min(grp, key=lambda t: (t.TagHeadPosition.DotProduct(R) - cu) ** 2 +
                                          (t.TagHeadPosition.DotProduct(U) - cv) ** 2)
            others = [t for t in grp if t.Id != main.Id]

            have = set(_ref_key(r) for r in main.GetTaggedReferences())
            add_refs = []
            ends = []
            for o in others:
                for r in o.GetTaggedReferences():
                    key = _ref_key(r)
                    if key in have:
                        continue
                    have.add(key)
                    add_refs.append(r)
                    ends.append(_leader_end(o, r))

            sub = SubTransaction(doc)
            sub.Start()
            try:
                if add_refs:
                    main.AddReferences(List[Reference](add_refs))
                    doc.Regenerate()
                    # свободные концы выносок - туда же, где были у старых марок
                    if main.LeaderEndCondition == LeaderEndCondition.Free:
                        new_refs = list(main.GetTaggedReferences())
                        for r, p in zip(add_refs, ends):
                            if p is None:
                                continue
                            for nr in new_refs:
                                if _ref_key(nr) == _ref_key(r):
                                    try:
                                        main.SetLeaderEnd(nr, p)
                                    except Exception:
                                        pass
                                    break
                ids = List[ElementId]([o.Id for o in others])
                doc.Delete(ids)
                if merge_elbows:
                    try:
                        main.MergeElbows = True
                    except Exception:
                        pass
                sub.Commit()
                merged_groups += 1
                removed += len(others)
            except Exception as ex:
                sub.RollBack()
                errors.append(u"«{}»: {}".format(k[1] or u"(пусто)", ex))
        tr.Commit()
    except Exception as ex:
        if tr.HasStarted() and not tr.HasEnded():
            tr.RollBack()
        forms.alert(u"Ошибка, изменения отменены:\n\n{}".format(ex), title=u"Объединить марки")
        return

    msg = [u"Объединено групп: {}".format(merged_groups),
           u"Удалено лишних марок: {}".format(removed)]
    if errors:
        msg.append(u"")
        msg.append(u"Не удалось объединить ({}):".format(len(errors)))
        msg.extend(u"   " + e for e in errors[:10])
        msg.append(u"")
        msg.append(u"Обычно причина - марка не поддерживает несколько основ "
                   u"или элементы разных категорий.")
    msg.append(u"")
    msg.append(u"Отмена - Ctrl+Z.")
    forms.alert(u"\n".join(msg), title=u"Объединить марки")
