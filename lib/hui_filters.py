# -*- coding: utf-8 -*-
"""Фильтры вида: какие из них действуют на элемент, их правила и переопределения графики.
Используется кнопкой «Чей фильтр» (панель Залупу).

    import hui_filters as hf
    hf.applies(doc, view_filter, el, ldoc)   -> (True | False | None, пояснение)
    hf.filter_rules_text(doc, pfe)           -> «Перекрытия: IfcPresentationLayer содержит «Пол» ИЛИ …»
    hf.overrides_text(ogs)                   -> [«линии: цвет 255,0,0», «полутон», …]
Элемент связи проверяется в документе связи: параметры правил сопоставляются
(встроенные — как есть, общие — по GUID, параметры проекта — по имени).
"""
from Autodesk.Revit.DB import (
    BuiltInParameter, ElementFilter, ElementId, ElementParameterFilter, FilterDoubleRule,
    FilterElementIdRule, FilterIntegerRule, FilterInverseRule, FilterStringRule, FilteredElementCollector,
    HasNoValueFilterRule, HasValueFilterRule, LabelUtils, LogicalAndFilter, LogicalOrFilter,
    ParameterElement, ParameterFilterElement, ParameterValueProvider, SelectionFilterElement,
    SharedParameterApplicableRule, SharedParameterElement, UnitFormatUtils,
)
from System.Collections.Generic import List


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _eid(v):
    try:
        return ElementId(v)
    except Exception:
        return ElementId(int(v))


# ------------------------------------------------------------------ тексты
EVAL_RU = {
    'FilterStringContains': u'содержит', 'FilterStringBeginsWith': u'начинается с',
    'FilterStringEndsWith': u'заканчивается на', 'FilterStringEquals': u'равно',
    'FilterStringGreater': u'больше', 'FilterStringGreaterOrEqual': u'больше или равно',
    'FilterStringLess': u'меньше', 'FilterStringLessOrEqual': u'меньше или равно',
    'FilterNumericEquals': u'равно', 'FilterNumericGreater': u'больше',
    'FilterNumericGreaterOrEqual': u'больше или равно', 'FilterNumericLess': u'меньше',
    'FilterNumericLessOrEqual': u'меньше или равно',
}
EVAL_NOT = {u'содержит': u'не содержит', u'начинается с': u'не начинается с',
            u'заканчивается на': u'не заканчивается на', u'равно': u'не равно'}


def param_name(doc, pid):
    v = _iv(pid)
    if v < 0:
        try:
            return LabelUtils.GetLabelFor(BuiltInParameter(v))
        except Exception:
            return u'параметр %d' % v
    el = doc.GetElement(pid)
    return el.Name if el is not None else u'параметр %d' % v


def _spec(doc, pid):
    try:
        el = doc.GetElement(pid)
        if isinstance(el, ParameterElement):
            return el.GetDefinition().GetDataType()
    except Exception:
        pass
    return None


def _num(doc, pid, value):
    sp = _spec(doc, pid)
    if sp is not None:
        try:
            return UnitFormatUtils.Format(doc.GetUnits(), sp, value, False)
        except Exception:
            pass
    try:
        return (u'%.3f' % value).rstrip(u'0').rstrip(u'.')
    except Exception:
        return u'%s' % value


def rule_text(doc, rule, neg=False):
    if isinstance(rule, FilterInverseRule):
        return rule_text(doc, rule.GetInnerRule(), not neg)
    try:
        pid = rule.GetRuleParameter()
        pn = param_name(doc, pid)
    except Exception:
        pid, pn = None, u'?'
    if isinstance(rule, HasValueFilterRule):
        return u'%s %s' % (pn, u'не имеет значения' if neg else u'имеет значение')
    if isinstance(rule, HasNoValueFilterRule):
        return u'%s %s' % (pn, u'имеет значение' if neg else u'не имеет значения')
    if isinstance(rule, SharedParameterApplicableRule):
        return u'параметр «%s» %s' % (rule.GetParameterName(), u'отсутствует' if neg else u'существует')
    op, val = u'?', u''
    try:
        op = EVAL_RU.get(type(rule.GetEvaluator()).__name__, type(rule.GetEvaluator()).__name__)
    except Exception:
        pass
    if neg:
        op = EVAL_NOT.get(op, u'НЕ ' + op)
    if isinstance(rule, FilterStringRule):
        val = u'«%s»' % rule.RuleString
    elif isinstance(rule, FilterElementIdRule):
        e = doc.GetElement(rule.RuleValue)
        val = u'«%s»' % (e.Name if e is not None else _iv(rule.RuleValue))
    elif isinstance(rule, FilterIntegerRule):
        val = u'%d' % rule.RuleValue
    elif isinstance(rule, FilterDoubleRule):
        val = _num(doc, pid, rule.RuleValue)
    return u'%s %s %s' % (pn, op, val)


def element_filter_text(doc, ef):
    if ef is None:
        return u'(без правил — все элементы категорий)'
    if isinstance(ef, LogicalAndFilter) or isinstance(ef, LogicalOrFilter):
        word = u' И ' if isinstance(ef, LogicalAndFilter) else u' ИЛИ '
        parts = [element_filter_text(doc, f) for f in ef.GetFilters()]
        parts = [(u'(%s)' % p) if (u' И ' in p or u' ИЛИ ' in p) else p for p in parts]
        return word.join(parts)
    if isinstance(ef, ElementParameterFilter):
        t = u' И '.join(rule_text(doc, r) for r in ef.GetRules())
        return (u'НЕ (%s)' % t) if ef.Inverted else t
    return type(ef).__name__


def category_names(doc, pfe):
    out = []
    for cid in pfe.GetCategories():
        try:
            from Autodesk.Revit.DB import Category
            c = Category.GetCategory(doc, cid)
            out.append(c.Name if c is not None else u'%d' % _iv(cid))
        except Exception:
            out.append(u'%d' % _iv(cid))
    return sorted(out)


def filter_rules_text(doc, f):
    if isinstance(f, SelectionFilterElement):
        return u'набор выбора: %d элементов' % f.GetElementIds().Count
    if isinstance(f, ParameterFilterElement):
        return element_filter_text(doc, f.GetElementFilter())
    return u''


# ------------------------------------------------------------------ проверка элемента
def _map_param(doc, ldoc, pid, cache):
    v = _iv(pid)
    if v < 0 or ldoc is None:
        return pid
    if v in cache:
        return cache[v]
    res = None
    el = doc.GetElement(pid)
    if isinstance(el, SharedParameterElement):
        sp = SharedParameterElement.Lookup(ldoc, el.GuidValue)
        res = sp.Id if sp is not None else None
    elif el is not None:
        for pe in FilteredElementCollector(ldoc).OfClass(ParameterElement):
            if pe.Name == el.Name:
                res = pe.Id
                break
    cache[v] = res
    return res


class _NoParam(Exception):
    pass


def _clone_rule(doc, ldoc, rule, cache):
    if isinstance(rule, FilterInverseRule):
        return FilterInverseRule(_clone_rule(doc, ldoc, rule.GetInnerRule(), cache))
    if isinstance(rule, SharedParameterApplicableRule):
        return SharedParameterApplicableRule(rule.GetParameterName())
    np = _map_param(doc, ldoc, rule.GetRuleParameter(), cache)
    if np is None:
        raise _NoParam(param_name(doc, rule.GetRuleParameter()))
    if isinstance(rule, HasValueFilterRule):
        return HasValueFilterRule(np)
    if isinstance(rule, HasNoValueFilterRule):
        return HasNoValueFilterRule(np)
    pvp = ParameterValueProvider(np)
    if isinstance(rule, FilterStringRule):
        return FilterStringRule(pvp, rule.GetEvaluator(), rule.RuleString)
    if isinstance(rule, FilterIntegerRule):
        return FilterIntegerRule(pvp, rule.GetEvaluator(), rule.RuleValue)
    if isinstance(rule, FilterDoubleRule):
        return FilterDoubleRule(pvp, rule.GetEvaluator(), rule.RuleValue, rule.Epsilon)
    if isinstance(rule, FilterElementIdRule):
        return FilterElementIdRule(pvp, rule.GetEvaluator(), rule.RuleValue)
    raise Exception(type(rule).__name__)


def _clone(doc, ldoc, ef, cache):
    if isinstance(ef, LogicalAndFilter) or isinstance(ef, LogicalOrFilter):
        subs = List[ElementFilter]([_clone(doc, ldoc, f, cache) for f in ef.GetFilters()])
        return LogicalAndFilter(subs) if isinstance(ef, LogicalAndFilter) else LogicalOrFilter(subs)
    if isinstance(ef, ElementParameterFilter):
        from Autodesk.Revit.DB import FilterRule
        rules = List[FilterRule]([_clone_rule(doc, ldoc, r, cache) for r in ef.GetRules()])
        return ElementParameterFilter(rules, ef.Inverted)
    raise Exception(type(ef).__name__)


def applies(doc, f, el, ldoc=None, cache=None):
    """(True|False|None, пояснение). None — проверить не удалось."""
    if cache is None:
        cache = {}
    if isinstance(f, SelectionFilterElement):
        if ldoc is not None:
            return None, u'набор выбора на элементы связи не действует'
        return (f.Contains(el.Id), u'')
    if not isinstance(f, ParameterFilterElement):
        return None, u''
    cat = el.Category
    if cat is None or not any(_iv(c) == _iv(cat.Id) for c in f.GetCategories()):
        return False, u'другая категория'
    ef = f.GetElementFilter()
    if ef is None:
        return True, u'фильтр только по категории'
    try:
        if ldoc is None:
            return ef.PassesFilter(doc, el.Id), u''
        return _clone(doc, ldoc, ef, cache).PassesFilter(ldoc, el.Id), u''
    except _NoParam as ex:
        return False, u'в связи нет параметра «%s»' % ex.args[0]
    except Exception as ex:
        return None, u'не удалось проверить: %s' % ex


# ------------------------------------------------------------------ графика
def _c(col):
    try:
        if col is not None and col.IsValid:
            return (col.Red, col.Green, col.Blue)
    except Exception:
        pass
    return None


def overrides_text(ogs):
    out = []
    try:
        c = _c(ogs.ProjectionLineColor)
        if c:
            out.append(u'линии %d,%d,%d' % c)
        if ogs.ProjectionLineWeight > 0:
            out.append(u'вес линий %d' % ogs.ProjectionLineWeight)
        c = _c(ogs.SurfaceForegroundPatternColor)
        if c and _iv(ogs.SurfaceForegroundPatternId) > 0:
            out.append(u'заливка %d,%d,%d' % c)
        c = _c(ogs.CutLineColor)
        if c:
            out.append(u'линии разреза %d,%d,%d' % c)
        if ogs.Halftone:
            out.append(u'полутон')
        if ogs.Transparency > 0:
            out.append(u'прозрачность %d%%' % ogs.Transparency)
        if _iv(ogs.ProjectionLinePatternId) > 0:
            out.append(u'тип линий')
    except Exception:
        pass
    return out


def swatch_color(ogs):
    for c in (ogs.SurfaceForegroundPatternColor if _iv(ogs.SurfaceForegroundPatternId) > 0 else None,
              ogs.ProjectionLineColor, ogs.CutLineColor):
        r = _c(c)
        if r:
            return r
    return None
