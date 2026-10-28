# -*- coding: utf-8 -*-

# ============================================================
# БЛОК 1. Импорты
# ============================================================
from pyrevit import revit, forms
import hui_forms as forms   # единый стиль окон ХУЙ (lib/hui_forms.py)
from Autodesk.Revit.DB import *
import random
import time

doc = revit.doc
uidoc = revit.uidoc

COMMENTS_BIP = BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS


def resolve_param(el, key):
    """key - int: отрицательный = BuiltInParameter, положительный =
    ElementId проектного/общего параметра."""
    if key < 0:
        return el.get_Parameter(BuiltInParameter(key))
    else:
        param_elem = doc.GetElement(ElementId(key))
        if param_elem:
            param_def = param_elem.GetDefinition()
            if param_def:
                return el.get_Parameter(param_def)
        return None


def get_schedulable_field(definition, key):
    for sf in definition.GetSchedulableFields():
        try:
            if sf.ParameterId.IntegerValue == key:
                return sf
        except Exception:
            continue
    return None


def ensure_field_and_get_field_id(definition, key, hide_if_new=False):
    """Возвращает ScheduleFieldId для параметра key - либо уже
    существующего в спеке поля, либо только что добавленного."""
    for i in range(definition.GetFieldCount()):
        f = definition.GetField(i)
        try:
            if f.ParameterId.IntegerValue == key:
                return f.FieldId
        except Exception:
            continue

    sf = get_schedulable_field(definition, key)
    if sf is None:
        return None

    field = definition.AddField(sf)
    if hide_if_new:
        field.IsHidden = True
    return field.FieldId


# ============================================================
# БЛОК 2. Получение выделенных элементов
# ============================================================
selected_ids = uidoc.Selection.GetElementIds()

elements = [doc.GetElement(eid) for eid in selected_ids]
elements = [el for el in elements if el is not None]

if len(elements) < 2:
    forms.alert(u"Выделите минимум 2 элемента.")
    raise SystemExit


# ============================================================
# БЛОК 3. Поиск ОБЩИХ параметров экземпляра, доступных для записи
# ============================================================
def get_instance_writable_params(el):
    result = {}
    for p in el.Parameters:
        try:
            if p.IsReadOnly:
                continue
            if p.StorageType == StorageType.ElementId:
                continue
            key = p.Id.IntegerValue
            name = p.Definition.Name
            result[key] = (name, p.StorageType)
        except Exception:
            continue
    return result


common = get_instance_writable_params(elements[0])

for el in elements[1:]:
    this_params = get_instance_writable_params(el)
    common = {k: v for k, v in common.items() if k in this_params}
    if not common:
        break


# ============================================================
# БЛОК 4. Выбор параметра для метки
# ============================================================
label_to_key = {}
labels = []

for key, (name, storage_type) in common.items():
    label = u"{} ({})".format(name, storage_type)
    label_to_key[label] = key
    labels.append(label)

labels.sort()

comments_label = u"Комментарии (запасной вариант)"
if comments_label not in labels:
    label_to_key[comments_label] = int(COMMENTS_BIP)
    labels.append(comments_label)

chosen_label = forms.SelectFromList.show(
    labels,
    title=u"Выберите параметр для записи уникальной метки",
    button_name=u"Выбрать"
)

if not chosen_label:
    raise SystemExit

marker_key = label_to_key[chosen_label]


# ============================================================
# БЛОК 5. Генерация уникальной метки и запись во все элементы
# ============================================================
first_param = resolve_param(elements[0], marker_key)
storage_type = first_param.StorageType if first_param else StorageType.String

marker_value_str = u"MARK-{}-{}".format(int(time.time()), random.randint(1000, 9999))
marker_value_int = int(time.time()) % 1000000
marker_value_double = float(marker_value_int)

t1 = Transaction(doc, u"Запись метки для группировки")
t1.Start()

write_errors = []
for el in elements:
    param = resolve_param(el, marker_key)
    if param is None:
        write_errors.append(el.Id.IntegerValue)
        continue
    try:
        if storage_type == StorageType.String:
            param.Set(marker_value_str)
        elif storage_type == StorageType.Integer:
            param.Set(marker_value_int)
        elif storage_type == StorageType.Double:
            param.Set(marker_value_double)
    except Exception:
        write_errors.append(el.Id.IntegerValue)

t1.Commit()

if write_errors:
    forms.alert(u"Не удалось записать метку в {} элемент(ов):\n{}".format(
        len(write_errors), u", ".join(str(x) for x in write_errors)))


# ============================================================
# БЛОК 6. Клонировать существующую спеку или создать новую?
# ============================================================
mode_choice = forms.CommandSwitchWindow.show(
    [u"Клонировать существующую спеку", u"Создать новую спеку"],
    message=u"Что делаем со спецификацией?"
)

if not mode_choice:
    raise SystemExit

new_name = forms.ask_for_string(
    default=u"Обосанные",
    prompt=u"Имя новой спецификации:",
    title=u"Создание спецификации"
)

if not new_name:
    raise SystemExit


# ============================================================
# БЛОК 7А. Режим "Клонировать" - только среди <Несколько категорий>
# ============================================================
if mode_choice == u"Клонировать существующую спеку":
    all_schedules = FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements()
    multi_cat_schedules = [
        s for s in all_schedules
        if not s.IsTemplate
        and not s.IsTitleblockRevisionSchedule
        and s.Definition.CategoryId == ElementId.InvalidElementId
    ]

    if not multi_cat_schedules:
        forms.alert(u"В проекте нет ни одной спецификации типа "
                    u"<Несколько категорий> для клонирования.")
        raise SystemExit

    name_to_schedule = {s.Name: s for s in multi_cat_schedules}
    chosen_source_name = forms.SelectFromList.show(
        sorted(name_to_schedule.keys()),
        title=u"Выберите спецификацию-образец (только <Несколько категорий>)",
        button_name=u"Клонировать"
    )

    if not chosen_source_name:
        raise SystemExit

    source_schedule = name_to_schedule[chosen_source_name]

    t2 = Transaction(doc, u"Клонирование спецификации")
    t2.Start()
    try:
        new_view_id = source_schedule.Duplicate(ViewDuplicateOption.Duplicate)
        new_view = doc.GetElement(new_view_id)
        new_view.Name = new_name

        definition = new_view.Definition
        field_id = ensure_field_and_get_field_id(definition, marker_key, hide_if_new=True)

        if field_id is None:
            raise Exception(u"Параметр метки недоступен как поле спецификации "
                             u"(не входит в схематизируемые поля для этой категории/спеки).")

        if storage_type == StorageType.String:
            sched_filter = ScheduleFilter(field_id, ScheduleFilterType.Equal, marker_value_str)
        elif storage_type == StorageType.Integer:
            sched_filter = ScheduleFilter(field_id, ScheduleFilterType.Equal, marker_value_int)
        else:
            sched_filter = ScheduleFilter(field_id, ScheduleFilterType.Equal, marker_value_double)

        definition.AddFilter(sched_filter)
        t2.Commit()
    except Exception as ex:
        t2.RollBack()
        forms.alert(u"Ошибка при клонировании:\n\n{}".format(str(ex)))
        raise SystemExit

    uidoc.RequestViewChange(new_view)
    forms.alert(u"Готово! Создан клон «{}».".format(new_name))


# ============================================================
# БЛОК 7Б. Режим "Создать новую" - <Несколько категорий>
# Поля: сохранённый набор (просмотр/удаление/редактирование
# с предвыбором чекбоксов) или ручной выбор, порядок столбцов -
# через перетаскивание мышкой
# ============================================================
else:
    from pyrevit import script
    from pyrevit.forms import TemplateListItem

    config = script.get_config()
    presets = config.get_option('field_presets', {})

    import clr
    clr.AddReference('System.Windows.Forms')
    clr.AddReference('System.Drawing')
    import System
    from System.Windows.Forms import (Form, ListBox, Button, DragDropEffects,
                                        FormStartPosition, DialogResult)
    from System.Drawing import Point, Size

    def manual_select_fields(field_labels_full, preselected=None):
        if preselected:
            preselected_set = set(preselected)
            # Отмеченные - в начало списка (в исходном порядке набора),
            # остальные - по алфавиту следом. Так видно сразу, что выбрано,
            # не прокручивая весь список.
            top_items = [name for name in preselected if name in field_label_to_sf]
            rest_items = sorted(n for n in field_labels_full if n not in preselected_set)
            ordered = top_items + rest_items
            items = [TemplateListItem(name, checked=(name in preselected_set))
                     for name in ordered]
        else:
            items = field_labels_full

        picked = forms.SelectFromList.show(
            items,
            title=u"Выберите поля (столбцы) для спецификации",
            button_name=u"Далее",
            multiselect=True
        )
        if not picked:
            return None

        result = []
        for p in picked:
            if hasattr(p, "name"):
                result.append(p.name)
            else:
                result.append(str(p))
        return result

    def reorder_fields_updown(field_list):
        """Окно «Вверх/Вниз» для порядка столбцов (общий стиль ХУЙ)."""
        if not field_list or len(field_list) < 2:
            return field_list
        return [str(x) for x in forms.reorder_list(
            field_list, title=u"Порядок столбцов",
            prompt=u"Выдели поле и двигай кнопками. «Готово» — сохранить порядок.")]

    t3 = Transaction(doc, u"Создание спецификации")
    t3.Start()
    try:
        new_schedule = ViewSchedule.CreateSchedule(doc, ElementId.InvalidElementId)
        new_schedule.Name = new_name
        definition = new_schedule.Definition

        all_schedulable_fields = list(definition.GetSchedulableFields())

        field_label_to_sf = {}
        field_labels_full = []
        for sf in all_schedulable_fields:
            try:
                name = sf.GetName(doc)
            except Exception:
                continue
            field_label_to_sf[name] = sf
            field_labels_full.append(name)

        field_labels_full.sort()

        chosen_fields = None
        save_as_new_preset = False

        while chosen_fields is None:
            preset_options = list(sorted(presets.keys()))
            preset_options.append(u"+ Выбрать поля вручную (новый набор)")
            if presets:
                preset_options.append(u"\u2699 Управлять наборами (удалить/редактировать)")

            preset_choice = forms.SelectFromList.show(
                preset_options,
                title=u"Выберите набор полей (или посмотрите другой)",
                button_name=u"Просмотреть"
            )
            if not preset_choice:
                t3.RollBack()
                raise SystemExit

            # --- Ручной выбор ---
            if preset_choice == u"+ Выбрать поля вручную (новый набор)":
                manual_fields = manual_select_fields(field_labels_full)
                if manual_fields:
                    manual_fields = reorder_fields_updown(manual_fields)
                    chosen_fields = manual_fields
                    save_as_new_preset = True
                continue

            # --- Управление наборами ---
            if preset_choice == u"\u2699 Управлять наборами (удалить/редактировать)":
                manage_choice = forms.SelectFromList.show(
                    sorted(presets.keys()),
                    title=u"Какой набор редактировать/удалить?",
                    button_name=u"Выбрать"
                )
                if not manage_choice:
                    continue

                action = forms.CommandSwitchWindow.show(
                    [u"Удалить набор", u"Редактировать состав полей"],
                    message=u"Что сделать с набором «{}»?".format(manage_choice)
                )

                if action == u"Удалить набор":
                    confirm = forms.alert(
                        u"Точно удалить набор «{}»? Отменить нельзя.".format(manage_choice),
                        yes=True, no=True
                    )
                    if confirm:
                        del presets[manage_choice]
                        config.field_presets = presets
                        script.save_config()
                        forms.alert(u"Набор «{}» удалён.".format(manage_choice))

                elif action == u"Редактировать состав полей":
                    new_fields = manual_select_fields(
                        field_labels_full, preselected=presets[manage_choice])
                    if new_fields:
                        new_fields = reorder_fields_updown(new_fields)
                        presets[manage_choice] = new_fields
                        config.field_presets = presets
                        script.save_config()
                        forms.alert(u"Набор «{}» обновлён.".format(manage_choice))

                continue  # возвращаемся к списку наборов

            # --- Выбран конкретный сохранённый набор ---
            saved_names = presets[preset_choice]
            available = [n for n in saved_names if n in field_label_to_sf]
            missing = [n for n in saved_names if n not in field_label_to_sf]

            preview_lines = [u"Набор «{}» - {} параметр(ов), по порядку:".format(
                preset_choice, len(saved_names)), u""]
            preview_lines.extend(u"{}. {}".format(i + 1, n) for i, n in enumerate(saved_names))
            if missing:
                preview_lines.append(u"")
                preview_lines.append(u"Недоступны для текущего выбора элементов "
                                     u"(будут пропущены):")
                preview_lines.extend(u"- {}".format(n) for n in missing)

            use_this = forms.alert(
                u"\n".join(preview_lines),
                yes=True, no=True,
                title=u"Использовать набор «{}»?".format(preset_choice)
            )

            if use_this:
                chosen_fields = available

        if not chosen_fields:
            t3.RollBack()
            raise SystemExit

        for label in chosen_fields:
            sf = field_label_to_sf[label]
            definition.AddField(sf)

        field_id = ensure_field_and_get_field_id(definition, marker_key, hide_if_new=True)

        if field_id is None:
            raise Exception(u"Параметр метки недоступен как поле спецификации.")

        if storage_type == StorageType.String:
            sched_filter = ScheduleFilter(field_id, ScheduleFilterType.Equal, marker_value_str)
        elif storage_type == StorageType.Integer:
            sched_filter = ScheduleFilter(field_id, ScheduleFilterType.Equal, marker_value_int)
        else:
            sched_filter = ScheduleFilter(field_id, ScheduleFilterType.Equal, marker_value_double)

        definition.AddFilter(sched_filter)
        t3.Commit()
    except SystemExit:
        raise
    except Exception as ex:
        t3.RollBack()
        forms.alert(u"Ошибка при создании спецификации:\n\n{}".format(str(ex)))
        raise SystemExit

    uidoc.RequestViewChange(new_schedule)

    if save_as_new_preset:
        want_save = forms.alert(
            u"Сохранить этот набор полей для будущего использования?",
            yes=True, no=True
        )
        if want_save:
            preset_name = forms.ask_for_string(
                default=u"Мой набор",
                prompt=u"Имя набора полей:",
                title=u"Сохранение набора"
            )
            if preset_name:
                presets[preset_name] = chosen_fields
                config.field_presets = presets
                script.save_config()

    forms.alert(u"Готово! Создана спецификация «{}».".format(new_name))