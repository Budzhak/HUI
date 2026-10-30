# -*- coding: utf-8 -*-
"""Параметры связи → в проект, чтобы по ним можно было делать фильтры видимости.

Проблема: фильтр вида видит у элементов связи только те параметры, которые есть в
ОСНОВНОМ проекте — причём как ОБЩИЕ параметры с тем же GUID (одноимённого мало).
Параметры IFC-связи (IfcPresentationLayer, ArchiCADProperties.* и т. п.) в проекте
отсутствуют, поэтому в списке «Критерии фильтрации» их нет.

Безопасность: ничего не добавляется само. Ты отмечаешь ТОЛЬКО нужные параметры (заранее не
отмечено ничего, в окне работает поиск), затем ТОЛЬКО нужные категории (например, «Перекрытия»),
затем видишь итог и подтверждаешь. Всё одной транзакцией — Ctrl+Z. Добавленное кнопкой помечено
в описании параметра («XYZ-link»), поэтому режим «Убрать» находит и удаляет именно его.

Что делает кнопка (то же, что руками «Экспорт параметра из связи → Добавить в проект»,
но сразу пачкой):
  1. выбираешь связь (IFC или RVT) — читается её загруженный документ;
  2. показывается список общих параметров связи, которых нет в проекте
     (или есть, но не для всех нужных категорий);
  3. отмеченные параметры создаются в проекте с ТЕМ ЖЕ GUID, именем и типом данных
     и привязываются к тем же категориям (экземпляр / тип — как в связи);
  4. по желанию печатается список значений параметров в связи — удобно собирать правила.

После этого параметр появляется в «Фильтры → Критерии фильтрации», а фильтр действует на
элементы связи, если у связи в «Видимость/Графика → Revit-связи» стоит «По основному виду».

Ограничения:
  · переносятся только ОБЩИЕ параметры (у них есть GUID). Обычные параметры проекта связи
    без GUID сопоставить нечем — они выводятся в отчёте как «не переносятся»;
  · если в проекте уже есть параметр с таким именем, но другим GUID, он пропускается
    (Revit не допускает два параметра с одним именем) — это тоже в отчёте;
  · в проекте у нового параметра значений нет — значения живут только в связи.
Shift+клик — только подробная диагностика связи (какие параметры и как устроены), без изменений.
"""
import os
import io
import tempfile

import clr
from System import Guid
from System.Collections.Generic import List
from pyrevit import revit, DB, script
import xyz_forms as forms   # единый стиль окон XYZ (lib/xyz_forms.py)

doc = revit.doc
app = doc.Application
out = script.get_output()
forms.style_output(out)

GROUP_NAME = u'XYZ_Параметры_связи'
MARK = u'XYZ-link'
MARKS = (MARK, u'HUI-link')   # параметры, добавленные до переименования (30.10.2026)          # пометка в описании параметра: по ней находим добавленное этой кнопкой
SPF_HEADER = (u'# This is a Revit shared parameter file.\r\n'
              u'# Do not edit manually.\r\n'
              u'*META\tVERSION\tMINVERSION\r\n'
              u'META\t2\t1\r\n'
              u'*GROUP\tID\tNAME\r\n'
              u'*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\r\n')


# ======================================================================
#  Мелочи
# ======================================================================
def idval(eid):
    try:
        return int(eid.Value)
    except AttributeError:
        return int(eid.IntegerValue)


def text(x):
    try:
        return x if isinstance(x, type(u'')) else x.decode('utf-8')
    except Exception:
        return u'%s' % (x,)


class Item(object):
    """Строка в списке выбора."""
    def __init__(self, label, data):
        self.label = label
        self.data = data


# ======================================================================
#  Связи
# ======================================================================
def link_is_ifc(inst):
    try:
        lt = doc.GetElement(inst.GetTypeId())
        ref = lt.GetExternalFileReference()
        if ref is not None:
            if str(ref.ExternalFileReferenceType) == 'IFC':
                return True
            p = DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(ref.GetAbsolutePath())
            return p.lower().endswith('.ifc') or p.lower().endswith('.ifc.rvt')
    except Exception:
        pass
    return False


def pick_link():
    insts = list(DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance))
    items, seen = [], set()
    for li in sorted(insts, key=lambda x: x.Name):
        ld = li.GetLinkDocument()
        if ld is None:
            continue
        key = ld.Title
        if key in seen:
            continue
        seen.add(key)
        kind = u'IFC' if link_is_ifc(li) else u'RVT'
        items.append(Item(u'%s   ·   %s' % (text(li.Name).split(u' : ')[0], kind), ld))
    if not items:
        forms.alert(u'В проекте нет загруженных связей.', title=u'Параметры связи')
        return None
    if len(items) == 1:
        return items[0].data
    sel = forms.SelectFromList.show(items, name_attr='label', title=u'Из какой связи взять параметры',
                                    button_name=u'Дальше')
    return sel.data if sel else None


# ======================================================================
#  Чтение параметров
# ======================================================================
def sp_guids(d):
    """ElementId параметра → GUID. У параметров проекта ключ привязки — InternalDefinition без GUID,
    GUID лежит в SharedParameterElement с тем же Id."""
    res = {}
    for sp in DB.FilteredElementCollector(d).OfClass(DB.SharedParameterElement):
        try:
            res[idval(sp.Id)] = sp.GuidValue
        except Exception:
            pass
    return res


def read_bindings(d, tokens=None, with_cats=True):
    """Привязки параметров документа: [dict(name, guid|None, kind, cats, definition)].
    tokens — слова (в нижнем регистре), которые должны входить в имя; категории читаем только у подходящих —
    в IFC-связи десятки тысяч параметров, перебирать им категории незачем."""
    spg = sp_guids(d)
    res = []
    it = d.ParameterBindings.ForwardIterator()
    it.Reset()
    while it.MoveNext():
        key = it.Key
        name = text(key.Name)
        if tokens:
            low = name.lower()
            if not all(t in low for t in tokens):
                continue
        b = it.Current
        guid = None
        try:
            guid = key.GUID
        except Exception:
            try:
                guid = spg.get(idval(key.Id))
            except Exception:
                guid = None
        cats = set()
        if with_cats:
            try:
                for c in b.Categories:
                    v = idval(c.Id)
                    if v < 0:
                        cats.add(v)
            except Exception:
                pass
        res.append(dict(name=name, guid=guid, kind=u'type' if isinstance(b, DB.TypeBinding) else u'inst',
                        cats=cats, definition=key))
    return res


def host_state():
    """Что уже есть в проекте: guid → привязанные категории; имя → guid."""
    by_guid, by_name = {}, {}
    for b in read_bindings(doc):
        by_name[b['name']] = b['guid']
        if b['guid'] is not None:
            by_guid.setdefault(b['guid'], set()).update(b['cats'])
    existing = set()
    for sp in DB.FilteredElementCollector(doc).OfClass(DB.SharedParameterElement):
        existing.add(sp.GuidValue)
    return by_guid, by_name, existing


def host_bindable_cats(cat_ids):
    """Категории связи, к которым в проекте можно привязать параметр."""
    ok = set()
    for v in cat_ids:
        try:
            cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory(v))
            if cat is not None and cat.AllowsBoundParameters:
                ok.add(v)
        except Exception:
            pass
    return ok


def analyse(ldoc, tokens=None):
    """Разбор параметров связи → (кандидаты, отчёт о том, что не переносится)."""
    h_guid, h_name, h_existing = host_state()
    cands, skipped = [], []
    lb = read_bindings(ldoc, tokens)
    twins = {}
    for b in lb:
        if b['guid'] is not None:
            twins.setdefault(b['name'], set()).add(b['guid'])
    for b in lb:
        if b['guid'] is None:
            skipped.append((b['name'], u'не общий параметр (нет GUID) — сопоставить нечем'))
            continue
        want = host_bindable_cats(b['cats'])
        if not want:
            skipped.append((b['name'], u'нет категорий, к которым в проекте можно привязать параметр'))
            continue
        have = h_guid.get(b['guid'], set())
        if b['guid'] in h_guid and want <= have:
            continue                                  # уже всё есть
        if b['guid'] not in h_guid and b['name'] in h_name and h_name[b['name']] != b['guid']:
            cn = u', '.join(sorted(cat_name(v) for v in want)[:6])
            if h_name[b['name']] in twins.get(b['name'], set()):
                # в связи два одноимённых параметра (у IFC так бывает: IfcPresentationLayer
                # общий + отдельный для осей), в проекте уже есть ДРУГОЙ из них — это нормально
                skipped.append((b['name'], u'второй одноимённый параметр связи (категории: %s); '
                                           u'в проекте уже есть основной с тем же GUID, что в связи — '
                                           u'фильтры работают по нему' % cn))
            else:
                skipped.append((b['name'], u'в проекте уже есть параметр с таким именем, но другим GUID '
                                           u'(категории в связи: %s). Revit не меняет GUID существующего '
                                           u'параметра' % cn))
            continue
        try:
            dt = b['definition'].GetDataType()
        except Exception:
            dt = None
        if dt is None:
            skipped.append((b['name'], u'не удалось определить тип данных'))
            continue
        try:
            grp = b['definition'].GetGroupTypeId()
        except Exception:
            grp = None
        status = (u'дополнить категории (+%d)' % len(want - have)) if b['guid'] in h_guid else u'новый'
        b.update(want=want, have=have, dtype=dt, group=grp, status=status,
                 in_host=b['guid'] in h_guid)
        cands.append(b)
    cands.sort(key=lambda x: x['name'].lower())
    return cands, skipped


def print_rows(title, rows, columns, cap=300):
    """Таблица в окно вывода — не больше cap строк (десятки тысяч строк подвешивают Revit);
    полный список сохраняем в файл рядом."""
    out.print_md(title)
    if not rows:
        return
    out.print_table(table_data=rows[:cap], columns=columns)
    if len(rows) > cap:
        path = os.path.join(tempfile.gettempdir(), u'xyz_link_params_report.txt')
        try:
            with io.open(path, 'w', encoding='utf-8-sig') as f:
                f.write(u'\t'.join(columns) + u'\n')
                for r in rows:
                    f.write(u'\t'.join(u'%s' % c for c in r) + u'\n')
            tail = u'Полный список (%d строк): `%s`' % (len(rows), path)
        except Exception:
            tail = u''
        out.print_md(u'…показано %d из %d. %s' % (cap, len(rows), tail))


# ======================================================================
#  Ограничение категорий, подтверждение, откат
# ======================================================================
def cat_name(v):
    try:
        return text(doc.Settings.Categories.get_Item(DB.BuiltInCategory(v)).Name)
    except Exception:
        return u'категория %d' % v


def pick_categories(chosen):
    """К каким категориям привязывать (по умолчанию ничего не отмечено — отмечаешь нужные)."""
    allc = set()
    for c in chosen:
        allc |= c['want']
    items = [Item(u'%s   ·   параметров: %d' % (cat_name(v), len([c for c in chosen if v in c['want']])), v)
             for v in sorted(allc, key=cat_name)]
    sel = forms.SelectFromList.show(
        items, name_attr='label', multiselect=True, button_name=u'Дальше',
        title=u'К каким категориям привязать (отметь только нужные, например «Перекрытия»)')
    return set(i.data for i in sel) if sel else None


def remove_added():
    """Убрать из проекта параметры, добавленные этой кнопкой (по пометке в описании)."""
    found = []
    it = doc.ParameterBindings.ForwardIterator()
    it.Reset()
    while it.MoveNext():
        key = it.Key
        try:
            if (key.Description or u'').startswith(MARKS):
                found.append(key)
        except Exception:
            pass
    if not found:
        forms.alert(u'Параметров, добавленных этой кнопкой, в проекте нет.', title=u'Параметры связи')
        return
    items = [Item(u'%s   ·   %s' % (text(k.Name), text(k.Description)), k) for k in sorted(found, key=lambda k: text(k.Name))]
    sel = forms.SelectFromList.show(items, name_attr='label', multiselect=True, button_name=u'Убрать из проекта',
                                    title=u'Какие параметры убрать из проекта')
    if not sel:
        return
    if not forms.alert(u'Убрать из проекта параметров: %d?\n\nФильтры и спецификации, которые их используют, '
                       u'перестанут работать. Отменить можно Ctrl+Z.' % len(sel),
                       title=u'Параметры связи', yes=True, no=True):
        return
    removed = []
    with revit.Transaction(u'Убрать параметры связи'):
        for i in sel:
            key = i.data
            guid = None
            try:
                guid = key.GUID
            except Exception:
                pass
            name = text(key.Name)
            if doc.ParameterBindings.Remove(key):
                removed.append(name)
                if guid is not None:
                    for sp in DB.FilteredElementCollector(doc).OfClass(DB.SharedParameterElement):
                        if sp.GuidValue == guid:
                            try:
                                doc.Delete(sp.Id)
                            except Exception:
                                pass
    forms.alert(u'Убрано параметров: %d из %d.' % (len(removed), len(sel)), title=u'Параметры связи')


# ======================================================================
#  Запись в проект
# ======================================================================
def make_definition_file(with_header=True):
    """Пустой файл общих параметров во временной папке. Пишем БАЙТАМИ (UTF-16 с BOM, CRLF как есть):
    в текстовом режиме Windows превратил бы \\r\\n в \\r\\r\\n и Revit не смог бы прочитать файл
    («Error in readParamDatabase»)."""
    path = os.path.join(tempfile.gettempdir(), u'xyz_link_params_%d.txt' % os.getpid())
    with open(path, 'wb') as f:
        if with_header:
            f.write(SPF_HEADER.encode('utf-16'))
    return path


def open_definition_file():
    """Открыть наш временный файл; если с заголовком не вышло — пробуем пустой файл (Revit допишет заголовок сам)."""
    last = None
    for with_header in (True, False):
        path = make_definition_file(with_header)
        app.SharedParametersFilename = path
        try:
            df = app.OpenSharedParameterFile()
            if df is not None:
                return df, path
        except Exception as ex:
            last = ex
        try:
            os.remove(path)
        except Exception:
            pass
    raise Exception(u'Revit не смог открыть временный файл общих параметров: %s' % text(last))


def category_set(cat_ids):
    cs = app.Create.NewCategorySet()
    for v in sorted(cat_ids):
        cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory(v))
        if cat is not None:
            cs.Insert(cat)
    return cs


def import_params(cands, source):
    """Создаёт параметры в проекте. Возвращает (создано, дополнено, ошибки)."""
    created, extended, errors = [], [], []
    old_file = app.SharedParametersFilename
    tmp = None
    try:
        df, tmp = open_definition_file()
        grp = df.Groups.Create(GROUP_NAME)
        with revit.Transaction(u'Параметры связи → проект'):
            with forms.ProgressBar(title=u'Параметры связи: {value} из {max_value}', cancellable=True) as pb:
                for i, c in enumerate(cands):
                    if pb.cancelled:
                        break
                    pb.update_progress(i, len(cands))
                    try:
                        want_all = set(c['want']) | set(c['have'])
                        cs = category_set(want_all)
                        binding = (app.Create.NewTypeBinding(cs) if c['kind'] == u'type'
                                   else app.Create.NewInstanceBinding(cs))
                        if c['in_host']:
                            # ключ привязки в проекте — InternalDefinition без .GUID: GUID берём
                            # из SharedParameterElement с тем же Id (раньше поиск не находил
                            # параметр, шёл в Insert и получал «Insert вернул False»)
                            definition = None
                            spg = sp_guids(doc)
                            it = doc.ParameterBindings.ForwardIterator()
                            it.Reset()
                            while it.MoveNext():
                                g = None
                                try:
                                    g = it.Key.GUID
                                except Exception:
                                    g = spg.get(idval(it.Key.Id))
                                if g == c['guid']:
                                    definition = it.Key
                                    break
                            if definition is not None:
                                ok = doc.ParameterBindings.ReInsert(definition, binding, c['group'] or DB.GroupTypeId.Ifc)
                                (extended if ok else errors).append((c['name'], u'' if ok else u'ReInsert вернул False'))
                                continue
                        opts = DB.ExternalDefinitionCreationOptions(c['name'], c['dtype'])
                        opts.GUID = c['guid']
                        opts.Visible = True
                        opts.UserModifiable = True
                        opts.Description = u'%s · из связи «%s»' % (MARK, source)
                        definition = grp.Definitions.Create(opts)
                        ok = doc.ParameterBindings.Insert(definition, binding, c['group'] or DB.GroupTypeId.Ifc)
                        if not ok:
                            ok = doc.ParameterBindings.Insert(definition, binding, DB.GroupTypeId.Ifc)
                        (created if ok else errors).append((c['name'], u'' if ok else u'Insert вернул False'))
                    except Exception as ex:
                        errors.append((c['name'], text(ex)))
    finally:
        app.SharedParametersFilename = old_file
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass
    return created, extended, errors


# ======================================================================
#  Значения параметров в связи
# ======================================================================
def scan_values(ldoc, params, cap=300):
    """{имя: {значение: сколько}} по элементам связи в категориях параметров."""
    result = dict((c['name'], {}) for c in params)
    for kind in (u'inst', u'type'):
        group = [c for c in params if c['kind'] == kind]
        if not group:
            continue
        cat_ids = set()
        for c in group:
            cat_ids |= c['want'] | c['have']
        ids = List[DB.ElementId]([DB.ElementId(DB.BuiltInCategory(v)) for v in cat_ids])
        col = DB.FilteredElementCollector(ldoc).WherePasses(DB.ElementMulticategoryFilter(ids))
        col = col.WhereElementIsElementType() if kind == u'type' else col.WhereElementIsNotElementType()
        for el in col:
            cat = el.Category
            if cat is None:
                continue
            cv = idval(cat.Id)
            for c in group:
                if cv not in c['want'] and cv not in c['have']:
                    continue
                p = el.get_Parameter(c['guid'])
                if p is None or not p.HasValue:
                    continue
                v = p.AsValueString() or p.AsString() or u''
                d = result[c['name']]
                d[v] = d.get(v, 0) + 1
    return result


# ======================================================================
#  Диагностика (Shift+клик)
# ======================================================================
def ask_tokens(prompt_extra=u''):
    q = forms.ask_for_string(
        default=u'Ifc',
        prompt=u'Часть имени параметра. Несколько слов через пробел — должны входить все.\n'
               u'Пусто — все параметры связи (в IFC их десятки тысяч).' + prompt_extra,
        title=u'Параметры связи')
    if q is None:
        return None
    return [t for t in q.lower().split() if t]


def diagnose(ldoc):
    tokens = ask_tokens()
    if tokens is None:
        return
    out.print_md(u'## Диагностика связи «%s»' % text(ldoc.Title))
    sp = list(DB.FilteredElementCollector(ldoc).OfClass(DB.SharedParameterElement))
    allb = read_bindings(ldoc, None, with_cats=False)
    out.print_md(u'Общих параметров (SharedParameterElement): **%d** · привязок параметров проекта: **%d** '
                 u'(с GUID: **%d**)' % (len(sp), len(allb), len([b for b in allb if b['guid']])))
    sel = [b for b in allb if all(t in b['name'].lower() for t in tokens)] if tokens else allb
    rows = [[b['name'], u'%s' % b['guid'] if b['guid'] else u'—', u'тип' if b['kind'] == u'type' else u'экз.']
            for b in sorted(sel, key=lambda x: x['name'].lower())]
    print_rows(u'### Параметры по фильтру «%s»: %d' % (u' '.join(tokens), len(rows)), rows,
               [u'Параметр', u'GUID', u'Привязка'])


# ======================================================================
#  Запуск
# ======================================================================
def main():
    if not __shiftclick__:  # noqa: F821
        mode = forms.CommandSwitchWindow.show(
            [u'Добавить параметры из связи', u'Убрать параметры, добавленные этой кнопкой'],
            message=u'Параметры связи')
        if not mode:
            return
        if mode.startswith(u'Убрать'):
            remove_added()
            return
    ldoc = pick_link()
    if ldoc is None:
        return
    if __shiftclick__:  # noqa: F821
        diagnose(ldoc)
        return
    tokens = ask_tokens()
    if tokens is None:
        return
    cands, skipped = analyse(ldoc, tokens)
    if not cands:
        msg = u'Новых параметров, которых не хватает проекту, в связи нет.'
        if skipped:
            msg += u'\n\nНе переносятся: %d (подробности — Shift+клик по кнопке).' % len(skipped)
        forms.alert(msg, title=u'Параметры связи')
        if skipped:
            print_rows(u'## Не переносятся', [[a, b] for a, b in skipped], [u'Параметр', u'Почему'])
        return

    # 1. какие параметры (ничего не отмечено заранее; в окне есть поиск — просто начни печатать)
    if len(cands) > 1500:
        forms.alert(u'Найдено параметров: %d. Показываю первые 1500 — уточни фильтр по имени, если нужного нет.'
                    % len(cands), title=u'Параметры связи')
        cands = cands[:1500]
    items = [Item(u'%s   ·   %s   ·   %s   ·   категорий: %d' % (
        c['name'], u'тип' if c['kind'] == u'type' else u'экземпляр', c['status'], len(c['want'])), c)
        for c in cands]
    src = text(ldoc.Title).replace(u'.ifc.RVT', u'').replace(u'.rvt', u'')
    picked = forms.SelectFromList.show(
        items, name_attr='label', multiselect=True, button_name=u'Дальше',
        title=u'Параметры «%s», которых нет в проекте: %d. Отметь только нужные' % (src, len(items)))
    if not picked:
        return
    chosen = [p.data for p in picked]

    # 2. к каким категориям (чтобы не засорять проект)
    cats = pick_categories(chosen)
    if not cats:
        return
    for c in chosen:
        c['want'] = c['want'] & cats
    chosen = [c for c in chosen if c['want']]
    if not chosen:
        forms.alert(u'У выбранных параметров нет отмеченных категорий.', title=u'Параметры связи')
        return

    # 3. подтверждение: что именно изменится
    names = u'\n'.join(u'  • %s' % c['name'] for c in chosen[:12]) + (u'\n  … и ещё %d' % (len(chosen) - 12) if len(chosen) > 12 else u'')
    cnames = u', '.join(cat_name(v) for v in sorted(cats, key=cat_name)[:8]) + (u' …' if len(cats) > 8 else u'')
    if not forms.alert(u'В проект будет добавлено параметров: %d\n\n%s\n\nКатегории: %s\n\n'
                       u'Параметры общие, значения в проекте пустые. Отменить — Ctrl+Z, '
                       u'убрать позже — эта же кнопка, режим «Убрать».' % (len(chosen), names, cnames),
                       title=u'Подтверждение', yes=True, no=True):
        return

    created, extended, errors = import_params(chosen, src)

    out.print_md(u'## Параметры связи → проект')
    out.print_md(u'Создано: **%d** · дополнено категориями: **%d** · ошибок: **%d** · не переносятся: **%d**'
                 % (len(created), len(extended), len(errors), len(skipped)))
    print_rows(u'**Создано**', [[n] for n, _ in created], [u'Параметр'])
    print_rows(u'**Дополнены категории**', [[n] for n, _ in extended], [u'Параметр'])
    print_rows(u'**Ошибки**', [[n, e] for n, e in errors], [u'Параметр', u'Причина'])
    print_rows(u'**Не переносятся (по этому фильтру)**', [[a, b] for a, b in skipped], [u'Параметр', u'Почему'])
    out.print_md(u'Теперь параметры есть в **Фильтры → Критерии фильтрации**. Фильтр действует на элементы связи, '
                 u'если у неё в Видимость/Графика → Revit-связи → Параметры отображения стоит «По основному виду».')

    done = set(n for n, _ in created) | set(n for n, _ in extended)
    shown = [c for c in chosen if c['name'] in done]
    if shown and forms.alert(u'Показать значения этих параметров в связи?\n(поможет собрать правила фильтра)',
                             title=u'Параметры связи', yes=True, no=True):
        values = scan_values(ldoc, shown)
        for c in shown:
            vals = sorted(values[c['name']].items(), key=lambda kv: (-kv[1], kv[0]))
            out.print_md(u'### %s  ·  разных значений: %d' % (c['name'], len(vals)))
            if vals:
                out.print_table(table_data=[[v if v else u'(пусто)', n] for v, n in vals[:200]],
                                columns=[u'Значение', u'Элементов'])


if __name__ == '__main__':
    main()
