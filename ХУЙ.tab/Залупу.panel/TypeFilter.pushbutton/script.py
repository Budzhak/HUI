# -*- coding: utf-8 -*-
"""Фильтр по типу (ХУЙ -> ХОЧУ).

Выделили элементы (своей модели или связи - Tab) -> для каждого типоразмера создаётся фильтр видимости
«db_<Категория>_Тип꞉ <Тип>» (категория элемента, правила: Имя типа = …
и Имя семейства = …) и сразу добавляется на текущий вид (или в его шаблон).
Затем открывается окно «Идентификация фильтра» (с 22.10.2026; раньше — «Видимость/Графика») - настраивайте.
Shift+клик - без открытия окна.

Элементы БЕЗ типа (например, из IFC: DirectShape) - вместо типа выбирается параметр с данными
(IfcName и т. п.), фильтр «db_<Категория>_<Параметр>꞉ <Значение>» с правилом «Параметр = значение».
Если это общий параметр связи, которого нет в проекте, - он сначала добавляется в проект с тем же
GUID (как кнопкой «Параметры связи», с пометкой HUI-link), иначе фильтр не увидит элемент связи.

Двоеточие в именах Revit запрещено, поэтому в имени стоит похожий знак «꞉» (U+A789).
Если фильтр с таким именем уже есть - используется он, новый не создаётся.
"""
import sys
from pyrevit import revit, script
import hui_forms as forms
sys.modules.pop('hui_params', None)
import hui_params
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import RevitCommandId, PostableCommand
from System.Collections.Generic import List

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
TITLE = u"Фильтр по типу"

BAD = {u':': u'꞉', u'\\': u'/', u'{': u'(', u'}': u')', u'[': u'(', u']': u')',
       u'|': u'/', u';': u',', u'<': u'(', u'>': u')', u'?': u'', u'`': u"'", u'~': u'-'}


def clean(s):
    return u''.join(BAD.get(ch, ch) for ch in (s or u''))


def pval(el, bip):
    p = el.get_Parameter(bip)
    if p is None or not p.HasValue:
        return None
    return p.AsString() or p.AsValueString()


def host_cat_id(cat):
    """Id категории в этой модели (у встроенных категорий Id одинаковые и в связях)."""
    if cat.Id.ToString().startswith(u'-'):
        return cat.Id
    for c in doc.Settings.Categories:
        if c.Name == cat.Name:
            return c.Id
    return None


def eq_rule(bip, value):
    pid = ElementId(bip)
    try:
        return ParameterFilterRuleFactory.CreateEqualsRule(pid, value)
    except TypeError:
        return ParameterFilterRuleFactory.CreateEqualsRule(pid, value, True)


# ------------------------------------------------------------------ что выделено
def selected_elements():
    """Элементы своей модели и элементы связей (выбранные Tab-ом в связи)."""
    out = []
    seen = set()
    try:
        refs = list(uidoc.Selection.GetReferences())
    except Exception:
        refs = []
    for r in refs:
        lid = r.LinkedElementId
        if lid is not None and lid.ToString() != u'-1':
            li = doc.GetElement(r.ElementId)
            ldoc = li.GetLinkDocument() if isinstance(li, RevitLinkInstance) else None
            if ldoc is None:
                continue
            el = ldoc.GetElement(lid)
            if el is not None:
                out.append((el, ldoc))
                seen.add(r.ElementId.ToString())
    for i in uidoc.Selection.GetElementIds():
        el = doc.GetElement(i)
        if el is None or isinstance(el, RevitLinkInstance):
            continue
        out.append((el, doc))
    return out


groups = {}     # (cat_id, тип, семейство) -> категория
untyped = []    # (el, edoc) - у элемента нет типа (IFC DirectShape и т. п.)
from_link = 0
for el, edoc in selected_elements():
    if el.Category is None or isinstance(el, ElementType):
        continue
    tid = el.GetTypeId()
    tp = edoc.GetElement(tid) if (tid is not None and tid.ToString() != u'-1') else None
    # имя типа и семейства - с типа (у элементов IFC-связей на экземпляре они пустые)
    tname = (pval(tp, BuiltInParameter.ALL_MODEL_TYPE_NAME) if tp is not None else None) \
        or pval(el, BuiltInParameter.ALL_MODEL_TYPE_NAME) or (tp.Name if tp is not None else None)
    if not tname:
        untyped.append((el, edoc))
        continue
    fname = (pval(tp, BuiltInParameter.ALL_MODEL_FAMILY_NAME) if tp is not None else None) \
        or pval(el, BuiltInParameter.ALL_MODEL_FAMILY_NAME)
    key = (el.Category.Id.ToString(), tname, fname)
    if key not in groups:
        groups[key] = el.Category
        if edoc is not doc:
            from_link += 1


# ------------------------------------------------------------------ элементы без типа: фильтр по параметру
SKIP_BIP = set(['IFC_GUID', 'IFC_TYPE_GUID', 'IFC_EXPORT_ELEMENT', 'IFC_EXPORT_ELEMENT_AS',
                'IFC_EXPORT_ELEMENT_TYPE', 'IFC_EXPORT_ELEMENT_TYPE_AS', 'IFC_EXPORT_PREDEFINEDTYPE',
                'IFC_EXPORT_PREDEFINEDTYPE_TYPE', 'ELEM_CATEGORY_PARAM', 'ELEM_CATEGORY_PARAM_MT'])
FIRST = [u'IfcName', u'IfcObjectType', u'IfcDescription', u'IfcPresentationLayer', u'IfcTag']


class Item(object):
    def __init__(self, label, data):
        self.label = label
        self.data = data


def param_value(p):
    """(значение для правила, текст для имени) или None."""
    st = p.StorageType
    if st == StorageType.String:
        v = p.AsString()
        return (v, v) if v and v.strip() else None
    if st == StorageType.Integer:
        return p.AsInteger(), (p.AsValueString() or u'%d' % p.AsInteger())
    if st == StorageType.Double:
        return p.AsDouble(), (p.AsValueString() or u'%g' % p.AsDouble())
    return None


def param_candidates(items):
    """Параметры с данными у элементов без типа: key -> dict."""
    cands = {}
    for el, edoc in items:
        for p in el.Parameters:
            if not p.HasValue:
                continue
            pv = param_value(p)
            if pv is None:
                continue
            d = p.Definition
            bip = None
            try:
                if d.BuiltInParameter != BuiltInParameter.INVALID:
                    bip = d.BuiltInParameter
            except Exception:
                pass
            if bip is not None and str(bip) in SKIP_BIP:
                continue
            guid = p.GUID if (bip is None and p.IsShared) else None
            if bip is None and guid is None and edoc is not doc:
                continue        # параметр проекта связи без GUID - в проекте сопоставить нечем
            if bip is not None:
                key = ('b', str(bip))
            elif guid is not None:
                key = ('g', guid.ToString())
            else:
                key = ('p', hui_params.idval(d.Id))
            c = cands.get(key)
            if c is None:
                try:
                    dtype = d.GetDataType()
                except Exception:
                    dtype = None
                try:
                    grp = d.GetGroupTypeId()
                except Exception:
                    grp = None
                c = cands[key] = dict(name=d.Name, bip=bip, guid=guid, pid=d.Id if key[0] == 'p' else None,
                                      storage=p.StorageType, dtype=dtype, group=grp, link=False,
                                      source=u'', hits=[])
            if edoc is not doc:
                c['link'] = True
                c['source'] = edoc.Title.replace(u'.ifc.RVT', u'').replace(u'.rvt', u'')
            c['hits'].append((el, pv))
    return cands


def cand_order(c):
    n = c['name']
    return (FIRST.index(n) if n in FIRST else len(FIRST), 0 if c['guid'] else 1, n.lower())


def pick_param(items):
    cands = param_candidates(items)
    if not cands:
        forms.alert(u"У выделенных элементов нет типа, и нет параметров с данными, по которым можно сделать "
                    u"фильтр.", title=TITLE)
        return None
    rows = []
    for c in sorted(cands.values(), key=cand_order):
        vals = []
        for el, (v, txt) in c['hits']:
            if txt not in vals:
                vals.append(txt)
        if len(vals) == 1:
            label = u"%s  =  %s" % (c['name'], vals[0])
        else:
            label = u"%s  ·  значений: %d (%s%s)" % (c['name'], len(vals), u', '.join(vals[:3]),
                                                     u', …' if len(vals) > 3 else u'')
        if len(c['hits']) < len(items):
            label += u"  ·  есть у %d из %d" % (len(c['hits']), len(items))
        if c['guid'] is not None and c['link']:
            cat_ids = set(hui_params.idval(el.Category.Id) for el, _ in c['hits'])
            st = hui_params.state(doc, c['guid'], c['name'], cat_ids)
            c['state'] = st
            if st.startswith(u'!'):
                label += u"  ·  нельзя: " + st[1:]
            elif st != u'есть':
                label += u"  ·  добавится в проект"
        rows.append(Item(label, c))
    sel = forms.SelectFromList.show(
        rows, name_attr='label', button_name=u'Создать фильтр',
        title=u"У элемента нет типа — по какому параметру сделать фильтр (выделено без типа: %d)" % len(items))
    if not sel:
        return None
    c = sel.data
    if c.get('state', u'').startswith(u'!'):
        forms.alert(u"Параметр «%s»: %s." % (c['name'], c['state'][1:]), title=TITLE)
        return None
    if c.get('state') in (u'создать', u'дополнить'):
        cn = u', '.join(sorted(set(el.Category.Name for el, _ in c['hits'])))
        what = u"добавлен в проект" if c['state'] == u'создать' else u"привязан ещё и к категории"
        if not forms.alert(u"Параметра «%s» из связи нет в проекте для категории «%s» — без него фильтр не "
                           u"увидит элементы связи.\n\nОн будет %s (общий, тот же GUID, что в связи; значения "
                           u"в проекте пустые). Убрать потом — «Параметры связи» → «Убрать».\n\nПродолжить?"
                           % (c['name'], cn, what), title=TITLE, yes=True, no=True):
            return None
    return c


def make_rule(pid, storage, value):
    if storage == StorageType.String:
        try:
            return ParameterFilterRuleFactory.CreateEqualsRule(pid, value)
        except TypeError:
            return ParameterFilterRuleFactory.CreateEqualsRule(pid, value, True)
    if storage == StorageType.Integer:
        return ParameterFilterRuleFactory.CreateEqualsRule(pid, int(value))
    return ParameterFilterRuleFactory.CreateEqualsRule(pid, float(value), 1e-6)


pcand = pick_param(untyped) if untyped else None
if untyped and pcand is None and not groups:
    script.exit()

if not groups and not pcand:
    forms.alert(u"Выделите один или несколько элементов модели или связи (элемент связи — через Tab; "
                u"не марки и не виды).\n\n"
                u"Для каждого их типоразмера будет создан фильтр видимости и добавлен на текущий вид.",
                title=TITLE, exitscript=True)

# ------------------------------------------------------------------ куда добавлять
target = view
if view.ViewTemplateId.ToString() != u'-1':
    tmpl = doc.GetElement(view.ViewTemplateId)
    ctrl = True
    try:
        nc = tmpl.GetNonControlledTemplateParameterIds()
        ctrl = not any(i.ToString() == ElementId(BuiltInParameter.VIS_GRAPHICS_FILTERS).ToString() for i in nc)
    except Exception:
        pass
    if ctrl:
        opt_t = u"Добавить в шаблон «{}» (изменятся все виды с этим шаблоном)".format(tmpl.Name)
        opt_f = u"Только создать фильтры, на вид не добавлять"
        ch = forms.alert(u"Фильтрами этого вида управляет шаблон «{}».".format(tmpl.Name),
                         title=TITLE, options=[opt_t, opt_f])
        if not ch:
            script.exit()
        target = tmpl if ch == opt_t else None

# ------------------------------------------------------------------ создание
existing = dict((f.Name, f) for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement))
made, reused, added, problems, params_note = [], [], [], [], []

t = Transaction(doc, TITLE)
t.Start()
try:
    for (cid, tname, fname), cat in sorted(groups.items(), key=lambda kv: (kv[1].Name, kv[0][1])):
        name = clean(u"db_{}_Тип: {}".format(cat.Name, tname))
        if not NamingUtils.IsValidName(name):
            problems.append(u"{} — недопустимое имя".format(name))
            continue
        pf = existing.get(name)
        if pf is None:
            hid = host_cat_id(cat)
            if hid is None:
                problems.append(u"{} — категории «{}» нет в этой модели".format(name, cat.Name))
                continue
            cats = List[ElementId]([hid])
            common = ParameterFilterUtilities.GetFilterableParametersInCommon(doc, cats)
            ok_ids = set(i.ToString() for i in common)
            rules = List[FilterRule]()
            if ElementId(BuiltInParameter.ALL_MODEL_TYPE_NAME).ToString() in ok_ids:
                rules.Add(eq_rule(BuiltInParameter.ALL_MODEL_TYPE_NAME, tname))
            elif ElementId(BuiltInParameter.SYMBOL_NAME_PARAM).ToString() in ok_ids:
                rules.Add(eq_rule(BuiltInParameter.SYMBOL_NAME_PARAM, tname))
            else:
                problems.append(u"{} — у категории «{}» нельзя фильтровать по имени типа".format(name, cat.Name))
                continue
            if fname and ElementId(BuiltInParameter.ALL_MODEL_FAMILY_NAME).ToString() in ok_ids:
                rules.Add(eq_rule(BuiltInParameter.ALL_MODEL_FAMILY_NAME, fname))
            pf = ParameterFilterElement.Create(doc, name, cats, ElementParameterFilter(rules))
            existing[name] = pf
            made.append(name)
        else:
            reused.append(name)
        if target is not None:
            if not target.IsFilterApplied(pf.Id):
                target.AddFilter(pf.Id)
                target.SetFilterVisibility(pf.Id, True)
                added.append(name)

    # ---- элементы без типа: фильтр по выбранному параметру
    if pcand is not None:
        c = pcand
        pvals = {}      # (cat_id, текст) -> (категория, значение)
        for el, (v, txt) in c['hits']:
            pvals.setdefault((el.Category.Id.ToString(), txt), (el.Category, v))
        pid = None
        if c['bip'] is not None:
            pid = ElementId(c['bip'])
        elif c['pid'] is not None:
            pid = c['pid']
        else:
            cat_ids = set(hui_params.idval(el.Category.Id) for el, _ in c['hits'])
            try:
                pid, how = hui_params.ensure_shared(doc, c['guid'], c['name'], c['dtype'], cat_ids,
                                                    instance=True, group=c['group'], source=c['source'])
                if how != u'есть':
                    params_note.append(u"Параметр «{}» {} в проект.".format(
                        c['name'], u"добавлен" if how == u'создан' else u"привязан к категории"))
                doc.Regenerate()
            except hui_params.ParamError as ex:
                problems.append(u"{} — {}".format(c['name'], ex.args[0] if ex.args else u''))
        if pid is not None:
            for (cid, txt), (cat, v) in sorted(pvals.items(), key=lambda kv: (kv[1][0].Name, kv[0][1])):
                name = clean(u"db_{}_{}: {}".format(cat.Name, c['name'], txt))
                if not NamingUtils.IsValidName(name):
                    problems.append(u"{} — недопустимое имя".format(name))
                    continue
                pf = existing.get(name)
                if pf is None:
                    hid = host_cat_id(cat)
                    if hid is None:
                        problems.append(u"{} — категории «{}» нет в этой модели".format(name, cat.Name))
                        continue
                    cats = List[ElementId]([hid])
                    ok_ids = set(i.ToString() for i in
                                 ParameterFilterUtilities.GetFilterableParametersInCommon(doc, cats))
                    if pid.ToString() not in ok_ids:
                        problems.append(u"{} — по параметру «{}» у категории «{}» фильтровать нельзя".format(
                            name, c['name'], cat.Name))
                        continue
                    rules = List[FilterRule]()
                    rules.Add(make_rule(pid, c['storage'], v))
                    pf = ParameterFilterElement.Create(doc, name, cats, ElementParameterFilter(rules))
                    existing[name] = pf
                    made.append(name)
                else:
                    reused.append(name)
                if target is not None and not target.IsFilterApplied(pf.Id):
                    target.AddFilter(pf.Id)
                    target.SetFilterVisibility(pf.Id, True)
                    added.append(name)
    t.Commit()
except Exception:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    import traceback
    forms.alert(u"Ошибка:\n\n" + traceback.format_exc(), title=TITLE, exitscript=True)

# ------------------------------------------------------------------ итог
if problems or params_note or (not made and not added):
    lines = list(params_note)
    if made:
        lines.append(u"Создано: " + u", ".join(made))
    if reused:
        lines.append(u"Уже были: " + u", ".join(reused))
    if added:
        lines.append(u"Добавлено на {}: {}".format(u"вид" if target is view else u"шаблон", len(added)))
    elif reused and target is not None:
        lines.append(u"Эти фильтры уже есть на виде.")
    if problems:
        lines.append(u"Не получилось:\n" + u"\n".join(problems))
    forms.alert(u"\n\n".join(lines), title=TITLE)

if (made or added or reused) and target is not None and not __shiftclick__:   # noqa: F821
    # v22.10.2026: вместо стандартного «Видимость/Графика» — наше окно «Идентификация фильтра»
    # (те же выделенные элементы, новый фильтр в списке; правки — в вид или в шаблон).
    import os
    import io
    _wf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # noqa: F821
                       'WhoFilter.pushbutton', 'script.py')
    if os.path.exists(_wf):
        _ns = {'__name__': '__main__', '__file__': _wf}
        try:
            with io.open(_wf, encoding='utf-8') as _fh:
                _src = _fh.read()
            # unicode-текст без строки coding (иначе кириллица в окне — кракозябры)
            _src = _src.replace(u'# -*- coding: utf-8 -*-', u'#', 1)
            exec(compile(_src, _wf, "exec"), _ns)
        except SystemExit:
            pass
        except Exception:
            import traceback
            forms.alert(u"Окно «Идентификация фильтра» не открылось:\n\n" + traceback.format_exc(), title=TITLE)
    else:
        try:
            __revit__.PostCommand(RevitCommandId.LookupPostableCommandId(PostableCommand.VisibilityOrGraphics))  # noqa: F821
        except Exception:
            pass
