# -*- coding: utf-8 -*-

# ============================================================
# БЛОК 1. Импорты и COM-хелперы
# ============================================================
from pyrevit import revit, forms, script
from Autodesk.Revit.DB import *

import System
from System import Type, Activator
from System.Reflection import BindingFlags

doc = revit.doc


def eid_int(eid):
    """ElementId -> int (Revit 2024+ .Value, раньше .IntegerValue)."""
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


# Коды ошибок ячеек Excel, которые COM возвращает вместо значения
# (#NULL!, #DIV/0!, #VALUE!, #REF!, #NAME?, #NUM!, #N/A ...)
EXCEL_ERRORS = {
    -2146826281: u"#DIV/0!", -2146826246: u"#N/A", -2146826259: u"#NAME?",
    -2146826288: u"#NULL!", -2146826252: u"#NUM!", -2146826265: u"#REF!",
    -2146826273: u"#VALUE!",
}


def read_cell(sheet, r, c):
    """Значение ячейки как текст + признак ошибки Excel.
    Возвращает (text, error_name). Пустая ячейка -> ("", None)."""
    v = com_get(com_get(sheet, "Cells", r, c), "Value2")
    if v is None:
        return u"", None
    if isinstance(v, int) and not isinstance(v, bool) and v in EXCEL_ERRORS:
        return u"", EXCEL_ERRORS[v]
    if isinstance(v, float):
        if v == int(v):
            return u"{}".format(int(v)), None          # 46.0 -> "46"
        return u"{}".format(repr(v)), None             # без потери знаков
    return u"{}".format(v), None


def com_get(obj, name, *args):
    return obj.GetType().InvokeMember(
        name, BindingFlags.GetProperty, None, obj, System.Array[object](args)
    )


def com_set(obj, name, value):
    obj.GetType().InvokeMember(
        name, BindingFlags.SetProperty, None, obj, System.Array[object]([value])
    )


def com_call(obj, name, *args):
    return obj.GetType().InvokeMember(
        name, BindingFlags.InvokeMethod, None, obj, System.Array[object](args)
    )


# ============================================================
# БЛОК 2. Поиск параметра для колонки
# (сначала экземпляр, потом тип; приоритет у того, где реально
# есть значение)
# ============================================================
def get_param_and_type(elem, param_id, is_builtin):
    def _lookup(target):
        if target is None:
            return None
        if is_builtin:
            bip = BuiltInParameter(param_id)
            return target.get_Parameter(bip)
        else:
            param_elem = doc.GetElement(ElementId(param_id))
            if param_elem:
                param_def = param_elem.GetDefinition()
                if param_def:
                    return target.get_Parameter(param_def)
            return None

    instance_param = _lookup(elem)

    type_elem = None
    type_id = elem.GetTypeId()
    if type_id != ElementId.InvalidElementId:
        type_elem = doc.GetElement(type_id)
    type_param = _lookup(type_elem)

    # Для параметра ТИПА Revit отдаёт на экземпляре его "копию" только для
    # чтения и без значения. Раньше в этом случае выбиралась она и запись
    # молча пропускалась (так не записалась ADSK_Марка у части установок).
    # Теперь приоритет у параметра, в который МОЖНО писать.
    inst_ok = instance_param is not None and not instance_param.IsReadOnly
    type_ok = type_param is not None and not type_param.IsReadOnly
    if inst_ok and (instance_param.HasValue or not type_ok):
        return instance_param, False
    if type_ok:
        return type_param, True
    if inst_ok:
        return instance_param, False
    # записать нельзя никуда - вернём что есть (для сообщения об ошибке)
    if instance_param is not None:
        return instance_param, False
    if type_param is not None:
        return type_param, True
    return None, False


# ============================================================
# БЛОК 3. Выбор файла для импорта
# ============================================================
filepath = forms.pick_file(file_ext="xlsx", title="Выберите файл для импорта")
if not filepath:
    forms.alert("Отмена")
    raise SystemExit

# Нули из Excel в числовые параметры: параметр ОЧИЩАЕТСЯ (пустая ячейка
# в ведомости), если Revit это позволяет - т.е. у общего параметра включено
# "Скрывать, если нет значения". Иначе записывается 0, как раньше.
ZERO_AS_EMPTY = True


def is_zero(text):
    try:
        return float(text.replace(u",", u".")) == 0.0
    except Exception:
        return False


def is_yes_no(param):
    try:
        return param.Definition.GetDataType() == SpecTypeId.Boolean.YesNo
    except Exception:
        return False


# ============================================================
# БЛОК 4. Открытие файла через Excel COM (в фоновом режиме)
# ============================================================
excel_type = Type.GetTypeFromProgID("Excel.Application")
excel = Activator.CreateInstance(excel_type)
com_set(excel, "Visible", False)
com_set(excel, "DisplayAlerts", False)

workbooks = com_get(excel, "Workbooks")
workbook = com_call(workbooks, "Open", filepath)

sheets = com_get(workbook, "Sheets")
sheet = com_get(sheets, "Item", "Лист1")
meta_sheet = com_get(sheets, "Item", "_DBTOOLS_META")


# ============================================================
# БЛОК 5. Чтение служебного листа метаданных (столбец -> параметр)
# ============================================================
meta_used_range = com_get(meta_sheet, "UsedRange")
meta_rows = com_get(meta_used_range, "Rows")
meta_count = com_get(meta_rows, "Count")

columns_meta = []
for r in range(2, meta_count + 1):
    excel_col = com_get(meta_sheet, "Cells", r, 1)
    header = com_get(meta_sheet, "Cells", r, 2)
    param_id = com_get(meta_sheet, "Cells", r, 3)
    is_builtin = com_get(meta_sheet, "Cells", r, 4)

    columns_meta.append({
        "excel_col": int(com_get(excel_col, "Value2")),
        "header": com_get(header, "Value2"),
        "param_id": com_get(param_id, "Value2"),
        "is_builtin": com_get(is_builtin, "Value2"),
    })

used_range = com_get(sheet, "UsedRange")
rows_range = com_get(used_range, "Rows")
cols_range = com_get(used_range, "Columns")
row_count = com_get(rows_range, "Count")
col_count = com_get(cols_range, "Count")

uid_col = col_count  # UniqueId - последний столбец


# ============================================================
# БЛОК 6. Предварительная проверка конфликтов параметров ТИПА
# (только чтение, ничего не пишем в проект; если для одного и
# того же типа в разных строках заданы РАЗНЫЕ значения -
# останавливаем импорт целиком, ничего не записываем)
# ============================================================
# type_groups[(type_id, param_id)] = { excel_value: [(row, element_id, header), ...] }
type_groups = {}

for r in range(2, row_count + 1):
    uid_cell = com_get(sheet, "Cells", r, uid_col)
    uid = com_get(uid_cell, "Value2")
    if not uid:
        continue

    element = doc.GetElement(uid)
    if element is None:
        continue

    for cm in columns_meta:
        if cm["param_id"] is None or cm["param_id"] == "":
            continue

        excel_value, cell_err = read_cell(sheet, r, cm["excel_col"])
        if cell_err or excel_value == u"":
            continue

        pid_int = int(cm["param_id"])
        param, is_type = get_param_and_type(element, pid_int, cm["is_builtin"])

        # Конфликты нас интересуют только у реально редактируемых
        # параметров ТИПА (не ReadOnly). Параметры экземпляра
        # конфликтов не создают - у каждой строки свой независимый
        # экземпляр.
        if param is None or param.IsReadOnly or not is_type:
            continue

        type_id = eid_int(element.GetTypeId())
        key = (type_id, pid_int)

        type_groups.setdefault(key, {}).setdefault(excel_value, []).append(
            (r, eid_int(element.Id), cm["header"])
        )

conflicts = [values_map for values_map in type_groups.values() if len(values_map) > 1]

if conflicts:
    report = []
    report.append(u"ИМПОРТ ОСТАНОВЛЕН: обнаружены конфликтующие значения "
                   u"параметров ТИПА.")
    report.append(u"Один и тот же тип элемента не может одновременно иметь "
                   u"два разных значения параметра. Ничего не записано.")
    report.append(u"")

    for values_map in conflicts:
        report.append(u"--- Конфликт ---")
        for value, entries in values_map.items():
            header = entries[0][2]
            ids = u", ".join(str(e[1]) for e in entries)
            report.append(u"Параметр '{}', значение '{}': ElementId {}".format(
                header, value, ids))
        report.append(u"")

    com_call(workbook, "Close", False)
    com_call(excel, "Quit")

    forms.alert(u"\n".join(report), title=u"Конфликт данных - импорт остановлен")
    raise SystemExit


# ============================================================
# БЛОК 7. Запись значений в Revit (внутри Transaction - можно
# полностью откатить через Ctrl+Z в Revit, если что-то не так)
# ============================================================
report = []
report.append("Строк данных: {}".format(row_count - 1))
report.append("")

changed = 0
cleared = 0
zeros_ignored = 0
kept_nonzero = []
skipped_empty = 0
not_found = 0
errors = []
done_type_params = set()

t = Transaction(doc, "Импорт спецификации из Excel")
t.Start()

try:
    for r in range(2, row_count + 1):
        uid_cell = com_get(sheet, "Cells", r, uid_col)
        uid = com_get(uid_cell, "Value2")
        if not uid:
            continue

        element = doc.GetElement(uid)
        if element is None:
            not_found += 1
            errors.append({
                "element_id": None,
                "row": r,
                "header": None,
                "message": "элемент с UniqueId {} не найден".format(uid)
            })
            continue

        for cm in columns_meta:
            if cm["param_id"] is None or cm["param_id"] == "":
                continue

            excel_value, cell_err = read_cell(sheet, r, cm["excel_col"])
            pid_int = int(cm["param_id"])
            param, is_type = get_param_and_type(element, pid_int, cm["is_builtin"])

            if param is None:
                continue                       # у этого элемента такого параметра нет

            def _err(msg):
                errors.append({"element_id": eid_int(element.Id), "row": r,
                               "header": cm["header"], "message": msg})

            if cell_err:
                _err(u"в ячейке Excel ошибка {} - пропущено".format(cell_err))
                continue

            if param.IsReadOnly:
                cur = param.AsValueString() or param.AsString() or u""
                if excel_value != u"" and excel_value != cur:
                    _err(u"параметр только для чтения (формула или вычисляемый) - "
                         u"не записано '{}'".format(excel_value))
                continue

            # параметр типа пишем один раз на тип
            if is_type:
                tkey = (eid_int(element.GetTypeId()), pid_int)
                if tkey in done_type_params:
                    continue
                done_type_params.add(tkey)

            current_value = param.AsValueString()
            if current_value is None:
                current_value = param.AsString()
            if current_value is None:
                current_value = u""

            # нуль в числовом параметре: НЕ записываем.
            #  - параметр с флагом "Скрывать, если нет значения" - очищаем
            #    (пустая ячейка в ведомости);
            #  - иначе: если в модели пусто/0 - пропускаем; если было число -
            #    записываем 0 и перечисляем такие параметры в отчёте, чтобы
            #    потом перевести их кнопкой "Пустые вместо нулей".
            if (ZERO_AS_EMPTY and excel_value != u"" and is_zero(excel_value)
                    and param.StorageType in (StorageType.Double, StorageType.Integer)
                    and not is_yes_no(param)):
                if not param.HasValue:
                    continue
                try:
                    if param.ClearValue():
                        cleared += 1
                        continue
                except Exception:
                    pass
                cur = param.AsValueString() or u""
                if is_zero(cur.split(u" ")[0]):
                    zeros_ignored += 1          # в модели уже 0 - ничего не делаем
                    continue
                # в модели было число, в Excel 0 -> записываем 0 (ниже обычным путём),
                # чтобы потом его убрала кнопка "Пустые вместо нулей"
                kept_nonzero.append({"element_id": eid_int(element.Id), "row": r,
                                     "header": cm["header"], "value": cur})

            # пустая ячейка: текст - очищаем, число - сбрасываем значение
            if excel_value == u"":
                if not param.HasValue or current_value == u"":
                    continue
                try:
                    if param.StorageType == StorageType.String:
                        ok = param.Set(u"")
                    else:
                        ok = param.ClearValue() if hasattr(param, "ClearValue") else False
                        if ok is None:
                            ok = True
                    if ok:
                        cleared += 1
                    else:
                        skipped_empty += 1
                except Exception:
                    skipped_empty += 1
                continue

            if current_value == excel_value:
                continue

            try:
                if param.StorageType == StorageType.String:
                    ok = param.Set(excel_value)
                else:
                    ok = param.SetValueString(excel_value)
                    if not ok and u"." in excel_value:
                        # десятичный разделитель системы может быть запятой
                        ok = param.SetValueString(excel_value.replace(u".", u","))

                if ok:
                    changed += 1
                else:
                    _err(u"Revit не принял значение '{}'".format(excel_value))
            except Exception as ex:
                _err(u"'{}': {}".format(excel_value, ex))

    t.Commit()
except Exception as ex:
    t.RollBack()
    com_call(workbook, "Close", False)
    com_call(excel, "Quit")
    forms.alert("Импорт прерван из-за ошибки:\n\n{}".format(str(ex)), title="Ошибка импорта")
    raise SystemExit


# ============================================================
# БЛОК 8. Закрытие Excel + отчёт (всё в одном простом окне)
# ============================================================
com_call(workbook, "Close", False)
com_call(excel, "Quit")

lines = []
lines.append(u"Строк данных: {}".format(row_count - 1))
lines.append(u"")
lines.append(u"Изменено значений: {}".format(changed))
if cleared:
    lines.append(u"Очищено (пустые ячейки / нули): {}".format(cleared))
if skipped_empty:
    lines.append(u"Не удалось очистить (встроенный параметр Revit не бывает пустым): {}".format(
        skipped_empty))
if zeros_ignored:
    lines.append(u"Нулей из Excel пропущено (в модели уже пусто/0): {}".format(zeros_ignored))
if kept_nonzero:
    zp = sorted(set(k["header"] for k in kept_nonzero))
    lines.append(u"")
    lines.append(u"Записано 0 вместо прежнего значения: {} (параметров: {}).".format(
        len(kept_nonzero), len(zp)))
    lines.append(u"Чтобы эти нули стали пустыми - запустите «Пустые вместо нулей»,")
    lines.append(u"в списке будут только эти параметры. Подробности - в окне вывода.")
lines.append(u"Элементов не найдено (UniqueId): {}".format(not_found))
lines.append(u"Ошибок записи: {}".format(len(errors)))
if errors:
    lines.append(u"")
    lines.append(u"Полный список ошибок - в окне вывода pyRevit "
                 u"(ElementId там кликабельные).")

    output = script.get_output()
    output.set_title(u"Импорт из Excel - ошибки")
    output.print_md(u"## Ошибки импорта: {}".format(len(errors)))
    rows_out = []
    for e in errors:
        eid = e.get("element_id")
        link = output.linkify(ElementId(eid)) if eid is not None else u"-"
        rows_out.append([link, e.get("row"), e.get("header") or u"", e.get("message")])
    output.print_table(rows_out, columns=[u"Элемент", u"Строка", u"Параметр", u"Проблема"])

if kept_nonzero:
    out2 = script.get_output()
    out2.print_md(u"## Записано 0 вместо прежнего значения: {}".format(len(kept_nonzero)))
    out2.print_md(u"У этих параметров нет флага «Скрывать, если нет значения», поэтому "
                  u"0 виден в ведомости. Запустите «Пустые вместо нулей» - в списке "
                  u"окажутся ровно эти параметры:")
    for h in sorted(set(k["header"] for k in kept_nonzero)):
        output_n = sum(1 for k in kept_nonzero if k["header"] == h)
        out2.print_md(u"- **{}** - {} шт.".format(h, output_n))
    out2.print_table([[out2.linkify(ElementId(k["element_id"])), k["row"], k["header"],
                       k["value"]] for k in kept_nonzero],
                     columns=[u"Элемент", u"Строка", u"Параметр", u"Было в модели"])

forms.alert(u"\n".join(lines), title=u"Импорт завершён")
