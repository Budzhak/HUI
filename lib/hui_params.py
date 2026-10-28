# -*- coding: utf-8 -*-
"""Общие параметры в проекте ХУЙ.

ensure_shared(...) — сделать так, чтобы в проекте был общий параметр с тем же GUID, что в связи,
и он был привязан к нужным категориям. Нужен, чтобы фильтр вида «видел» параметр элемента связи
(фильтр сопоставляет параметры по GUID, одноимённого мало).

Добавленное помечается в описании «HUI-link» — кнопка «Параметры связи» в режиме «Убрать»
находит и удаляет такие параметры.

Используют: «Фильтр по типу» (элементы без типа, например из IFC).
Вызывать ВНУТРИ открытой транзакции.
"""
import os
import tempfile

from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, ExternalDefinitionCreationOptions,
                               FilteredElementCollector, GroupTypeId, SharedParameterElement,
                               TypeBinding)

MARK = u'HUI-link'
GROUP_NAME = u'HUI_Параметры_связи'
SPF_HEADER = (u'# This is a Revit shared parameter file.\r\n'
              u'# Do not edit manually.\r\n'
              u'*META\tVERSION\tMINVERSION\r\n'
              u'META\t2\t1\r\n'
              u'*GROUP\tID\tNAME\r\n'
              u'*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\r\n')


class ParamError(Exception):
    pass


def idval(eid):
    try:
        return int(eid.Value)
    except AttributeError:
        return int(eid.IntegerValue)


def sp_guids(doc):
    """ElementId параметра → GUID (у ключа привязки в проекте .GUID нет — он в SharedParameterElement)."""
    res = {}
    for sp in FilteredElementCollector(doc).OfClass(SharedParameterElement):
        try:
            res[idval(sp.Id)] = sp.GuidValue
        except Exception:
            pass
    return res


def _key_guid(key, spg):
    try:
        return key.GUID
    except Exception:
        try:
            return spg.get(idval(key.Id))
        except Exception:
            return None


def host_binding(doc, guid=None, name=None):
    """(ключ, привязка, guid) параметра проекта по GUID или по имени; (None, None, None) — нет."""
    spg = sp_guids(doc)
    it = doc.ParameterBindings.ForwardIterator()
    it.Reset()
    while it.MoveNext():
        k = it.Key
        g = _key_guid(k, spg)
        if guid is not None and g == guid:
            return k, it.Current, g
        if name is not None and k.Name == name:
            return k, it.Current, g
    return None, None, None


def state(doc, guid, name, cat_ids):
    """Что нужно сделать в проекте: 'есть' | 'дополнить' | 'создать' | текст ошибки (начинается с '!')."""
    k, b, g = host_binding(doc, guid=guid)
    if k is not None:
        have = set(idval(c.Id) for c in b.Categories)
        return u'есть' if set(cat_ids) <= have else u'дополнить'
    k, b, g = host_binding(doc, name=name)
    if k is not None:
        return (u'!в проекте уже есть параметр «%s» с другим GUID — Revit не даст завести второй '
                u'с тем же именем, а GUID существующего не меняется' % name)
    for v in cat_ids:
        c = _cat(doc, v)
        if c is None or not c.AllowsBoundParameters:
            return u'!к категории «%s» нельзя привязать параметр' % (c.Name if c else v)
    return u'создать'


def _cat(doc, v):
    try:
        return doc.Settings.Categories.get_Item(BuiltInCategory(v))
    except Exception:
        return None


def _catset(doc, cat_ids):
    cs = doc.Application.Create.NewCategorySet()
    for v in sorted(cat_ids):
        c = _cat(doc, v)
        if c is not None:
            cs.Insert(c)
    return cs


def _open_temp_spf(app):
    """Временный файл общих параметров (байтами, UTF-16 с BOM — иначе Revit не читает)."""
    last = None
    for with_header in (True, False):
        path = os.path.join(tempfile.gettempdir(), u'hui_params_%d.txt' % os.getpid())
        with open(path, 'wb') as f:
            if with_header:
                f.write(SPF_HEADER.encode('utf-16'))
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
    raise ParamError(u'Revit не смог открыть временный файл общих параметров: %s' % last)


def ensure_shared(doc, guid, name, dtype, cat_ids, instance=True, group=None, source=u''):
    """Общий параметр guid/name в проекте, привязанный к cat_ids (int BuiltInCategory).
    Возвращает (ElementId параметра, 'есть' | 'дополнен' | 'создан'). Ошибка — ParamError."""
    app = doc.Application
    st = state(doc, guid, name, cat_ids)
    if st.startswith(u'!'):
        raise ParamError(st[1:])
    if st == u'есть':
        return SharedParameterElement.Lookup(doc, guid).Id, u'есть'
    if st == u'дополнить':
        k, b, g = host_binding(doc, guid=guid)
        have = set(idval(c.Id) for c in b.Categories)
        cs = _catset(doc, have | set(cat_ids))
        nb = app.Create.NewTypeBinding(cs) if isinstance(b, TypeBinding) else app.Create.NewInstanceBinding(cs)
        try:
            grp = k.GetGroupTypeId()
        except Exception:
            grp = GroupTypeId.Ifc
        if not doc.ParameterBindings.ReInsert(k, nb, grp):
            raise ParamError(u'не удалось добавить категории параметру «%s»' % name)
        return SharedParameterElement.Lookup(doc, guid).Id, u'дополнен'
    old = app.SharedParametersFilename
    tmp = None
    try:
        df, tmp = _open_temp_spf(app)
        grp_def = df.Groups.Create(GROUP_NAME)
        opts = ExternalDefinitionCreationOptions(name, dtype)
        opts.GUID = guid
        opts.Visible = True
        opts.UserModifiable = True
        opts.Description = u'%s · из связи «%s»' % (MARK, source) if source else MARK
        d = grp_def.Definitions.Create(opts)
        cs = _catset(doc, cat_ids)
        nb = app.Create.NewInstanceBinding(cs) if instance else app.Create.NewTypeBinding(cs)
        ok = doc.ParameterBindings.Insert(d, nb, group or GroupTypeId.Ifc)
        if not ok:
            ok = doc.ParameterBindings.Insert(d, nb, GroupTypeId.Ifc)
        if not ok:
            raise ParamError(u'Revit не добавил параметр «%s» в проект' % name)
    finally:
        try:
            app.SharedParametersFilename = old
        except Exception:
            pass
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass
    sp = SharedParameterElement.Lookup(doc, guid)
    if sp is None:
        raise ParamError(u'параметр «%s» добавлен, но не найден по GUID' % name)
    return sp.Id, u'создан'
