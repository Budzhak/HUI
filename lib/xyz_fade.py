# -*- coding: utf-8 -*-
"""Блёклость фильтра вида: элементы фильтра бледнеют, но остаются в цвете своей системы.

В Revit яркость полутона одна на весь проект (и через API недоступна), а фильтр задаёт один цвет
на все свои элементы. Поэтому блёклость делается вспомогательными фильтрами:
  · элементы основного фильтра на виде делятся на группы по «Типу системы» (воздуховоды, трубы,
    их фасонины, арматура, терминалы), остальное — по категории;
  · у каждой группы берётся её текущий цвет на виде (фильтры вида → цвет типа системы →
    цвет категории) и осветляется к белому с сохранением оттенка:
        цвет' = цвет + (255 − цвет) × блёклость / 100      (0 — как есть, 100 — белый);
  · на каждую группу — фильтр «zz_блёк N% · <основной> · <группа> #<id вида>» с правилами основного
    фильтра И «Тип системы = …», цветами линий/штриховки и полутоном; ставится в КОНЕЦ списка
    фильтров вида (в Revit выше в списке — главнее, поэтому служебные фильтры уступают остальным фильтрам вида, задающим цвет).
Вызывать внутри транзакции.

    import xyz_fade
    k = xyz_fade.current(doc, owner, main)             # 0 — нет
    n, notes = xyz_fade.apply(doc, view, owner, main, k)   # k = 0 — убрать
"""
from Autodesk.Revit.DB import (BuiltInParameter, Color, Element, ElementId, ElementMulticategoryFilter,
                               ElementParameterFilter, FilteredElementCollector, LogicalAndFilter,
                               NamingUtils, OverrideGraphicSettings, ParameterFilterElement,
                               ParameterFilterRuleFactory, ParameterFilterUtilities)
from System.Collections.Generic import List
import System

PREFIX = u'zz_блёк '      # «~» в именах фильтров Revit запрещён; «zz_» — в конец списков
SYS_BIPS = (BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM, BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
_BAD = {u':': u'꞉', u'\\': u'/', u'{': u'(', u'}': u')', u'[': u'(', u']': u')',
        u'|': u'/', u';': u',', u'<': u'(', u'>': u')', u'?': u'', u'`': u"'", u'~': u'-'}


def _iv(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _name(e):
    try:
        return e.Name
    except Exception:
        try:
            return Element.Name.GetValue(e)
        except Exception:
            return u'?'


def _clean(s):
    return u''.join(_BAD.get(ch, ch) for ch in (s or u''))


def _col(c):
    try:
        return (int(c.Red), int(c.Green), int(c.Blue)) if (c is not None and c.IsValid) else None
    except Exception:
        return None


def _rc(t):
    return Color(System.Byte(t[0]), System.Byte(t[1]), System.Byte(t[2]))


def fade_color(c, k):
    t = max(0, min(100, k)) / 100.0
    return tuple(int(round(v + (255 - v) * t)) for v in c)


def _suffix(owner):
    return u' #%d' % _iv(owner.Id)


def _mid(main):
    return u' · %s · ' % _clean(main.Name)


def is_helper(f):
    return f is not None and f.Name.startswith(PREFIX)


def helpers(doc, owner, main):
    """[(ElementId, k)] вспомогательных фильтров блёклости этого основного фильтра на этом виде/шаблоне."""
    res = []
    mid, suf = _mid(main), _suffix(owner)
    for fid in owner.GetFilters():
        f = doc.GetElement(fid)
        if f is None or not f.Name.startswith(PREFIX) or not f.Name.endswith(suf) or mid not in f.Name:
            continue
        try:
            k = int(f.Name[len(PREFIX):f.Name.index(u'%')])
        except Exception:
            k = 0
        res.append((fid, k))
    return res


def current(doc, owner, main):
    h = helpers(doc, owner, main)
    return max(k for _, k in h) if h else 0


def _type_color(doc, el):
    for bip in SYS_BIPS:
        p = el.get_Parameter(bip)
        if p is not None and p.HasValue:
            st = doc.GetElement(p.AsElementId())
            if st is not None:
                try:
                    c = _col(st.LineColor)
                    if c:
                        return c
                except Exception:
                    pass
    return None


def effective_colors(doc, owner, el, hf, by_system=True):
    """(линии, заливка или None, линии разреза) элемента на виде — без учёта вспомогательных фильтров."""
    line = fill = cut = None
    try:
        fids = list(owner.GetOrderedFilters())
    except Exception:
        fids = list(owner.GetFilters())
    for fid in fids:
        f = doc.GetElement(fid)
        if f is None or is_helper(f):
            continue
        try:
            if not owner.GetIsFilterEnabled(fid):
                continue
        except Exception:
            pass
        ok, _ = hf.applies(doc, f, el)
        if not ok:
            continue
        o = owner.GetFilterOverrides(fid)
        # побеждает фильтр выше в списке — берём первый найденный цвет
        line = line or _col(o.ProjectionLineColor)
        cut = cut or _col(o.CutLineColor)
        if fill is None and _iv(o.SurfaceForegroundPatternId) != -1:
            fill = _col(o.SurfaceForegroundPatternColor)
    if line is None and by_system:
        line = _type_color(doc, el)
    if line is None and el.Category is not None:
        try:
            line = _col(owner.GetCategoryOverrides(el.Category.Id).ProjectionLineColor)
        except Exception:
            pass
        if line is None:
            line = _col(el.Category.LineColor)
    line = line or (0, 0, 0)
    return line, fill, (cut or line)


def _groups(doc, view, main):
    """Элементы основного фильтра на виде → группы {ключ: dict(label, cats, bip, sid, el)}."""
    cats = List[ElementId](main.GetCategories())
    col = FilteredElementCollector(doc, view.Id).WherePasses(ElementMulticategoryFilter(cats)) \
        .WhereElementIsNotElementType()
    ef = main.GetElementFilter()
    if ef is not None:
        col = col.WherePasses(ef)
    groups = {}
    filt = {}       # (категория, параметр) -> можно ли фильтровать

    def can(cid, bip):
        k = (_iv(cid), int(bip))
        if k not in filt:
            ids = ParameterFilterUtilities.GetFilterableParametersInCommon(doc, List[ElementId]([cid]))
            filt[k] = _iv(ElementId(bip)) in set(_iv(i) for i in ids)
        return filt[k]

    for el in col:
        if el.Category is None:
            continue
        key = None
        for bip in SYS_BIPS:
            p = el.get_Parameter(bip)
            if p is not None and p.HasValue and _iv(p.AsElementId()) > 0 and can(el.Category.Id, bip):
                st = doc.GetElement(p.AsElementId())
                key = ('sys', int(bip), _iv(p.AsElementId()))
                g = groups.setdefault(key, dict(label=_name(st) if st else u'?', cats={}, bip=bip,
                                                sid=p.AsElementId(), el=el))
                break
        if key is None:
            key = ('cat', _iv(el.Category.Id))
            g = groups.setdefault(key, dict(label=el.Category.Name, cats={}, bip=None, sid=None, el=el))
        g['cats'][_iv(el.Category.Id)] = el.Category.Id
    return groups


def count(doc, view, main):
    """Сколько служебных фильтров понадобится."""
    return len(_groups(doc, view, main))


def remove_all(doc, owner):
    """Убрать все служебные фильтры блёклости с этого вида/шаблона."""
    suf = _suffix(owner)
    n = 0
    for fid in list(owner.GetFilters()):
        f = doc.GetElement(fid)
        if is_helper(f) and f.Name.endswith(suf):
            try:
                doc.Delete(fid)
                n += 1
            except Exception:
                pass
    return n


def remove(doc, owner, main):
    n = 0
    for fid, _ in helpers(doc, owner, main):
        try:
            doc.Delete(fid)
            n += 1
        except Exception:
            pass
    return n


def _ogs(line, fill, cut, k):
    o = OverrideGraphicSettings()
    o.SetProjectionLineColor(_rc(fade_color(line, k)))
    o.SetCutLineColor(_rc(fade_color(cut, k)))
    if fill:
        o.SetSurfaceForegroundPatternColor(_rc(fade_color(fill, k)))
        o.SetCutForegroundPatternColor(_rc(fade_color(fill, k)))
    o.SetHalftone(True)
    return o


def _plan(doc, view, owner, main):
    """Нужные служебные фильтры по порядку: [(хвост имени после «N%», группа)].
    В Revit при конфликте побеждает фильтр ВЫШЕ в списке — поэтому сначала группы по системам,
    за ними по категории (они ловят всю категорию и должны действовать только на то, что осталось)."""
    res, seen = [], set()
    for key, g in sorted(_groups(doc, view, main).items(), key=lambda kv: (kv[0][0] != 'sys', kv[1]['label'])):
        tail = _clean(u'%s%s%s' % (_mid(main), g['label'], _suffix(owner)))
        base, i = tail, 2
        while tail in seen:
            tail = u'%s (%d)' % (base[:-len(_suffix(owner))], i) + _suffix(owner)
            i += 1
        seen.add(tail)
        res.append((tail, g))
    return res


def _tail(name):
    return name[name.index(u'%') + 1:] if u'%' in name else name


def apply(doc, view, owner, main, k, hf):
    """Блёклость k (0 — убрать). Если состав систем не изменился — служебные фильтры не пересоздаются,
    только перекрашиваются и переименовываются (каждое добавление фильтра на вид и смена его графики
    стоят Revit ~0,3 с, поэтому пересоздание вдвое дольше). Возвращает (сколько групп, замечания)."""
    if k <= 0:
        remove(doc, owner, main)
        return 0, []
    notes = []
    plan = _plan(doc, view, owner, main)
    have = dict((_tail(doc.GetElement(fid).Name), fid) for fid, _ in helpers(doc, owner, main))
    if have and set(have) == set(t for t, _ in plan):
        for tail, g in plan:
            fid = have[tail]
            line, fill, cut = effective_colors(doc, owner, g['el'], hf, by_system=g['bip'] is not None)
            owner.SetFilterOverrides(fid, _ogs(line, fill, cut, k))
            try:
                doc.GetElement(fid).Name = u'%s%d%%%s' % (PREFIX, k, tail)
            except Exception:
                pass
        return len(plan), notes
    remove(doc, owner, main)
    main_ef = main.GetElementFilter()
    made = 0
    for tail, g in plan:
        cats = List[ElementId](g['cats'].values())
        ef = main_ef
        if g['bip'] is not None:
            rule = ElementParameterFilter(ParameterFilterRuleFactory.CreateEqualsRule(ElementId(g['bip']), g['sid']))
            ef = LogicalAndFilter(main_ef, rule) if main_ef is not None else rule
        name = u'%s%d%%%s' % (PREFIX, k, tail)
        try:
            pf = ParameterFilterElement.Create(doc, name, cats, ef) if ef is not None \
                else ParameterFilterElement.Create(doc, name, cats)
        except Exception as ex:
            notes.append(u'%s — не создан: %s' % (g['label'], ex))
            continue
        line, fill, cut = effective_colors(doc, owner, g['el'], hf, by_system=g['bip'] is not None)
        owner.AddFilter(pf.Id)
        owner.SetFilterOverrides(pf.Id, _ogs(line, fill, cut, k))
        made += 1
    return made, notes
