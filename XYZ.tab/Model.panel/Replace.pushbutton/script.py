# -*- coding: utf-8 -*-

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType
from pyrevit import revit, forms
import xyz_forms as forms   # единый стиль окон XYZ (lib/xyz_forms.py)

doc = revit.doc
uidoc = revit.uidoc

try:
    # Выбор эталона
    src_ref = uidoc.Selection.PickObject(
        ObjectType.Element,
        "Выберите эталонный элемент"
    )
    src = doc.GetElement(src_ref)
    src_type = doc.GetElement(src.GetTypeId())
    
    # Выбор целевого
    trg_ref = uidoc.Selection.PickObject(
        ObjectType.Element,
        "Выберите элемент для замены"
    )
    trg = doc.GetElement(trg_ref)
    
    # Проверка
    if not hasattr(trg.Location, 'Point'):
        forms.alert('Элемент не имеет точки вставки!', exitscript=True)
    
    pt = trg.Location.Point
    level = doc.GetElement(trg.LevelId)
    
    # Отметка
    offset_param = trg.get_Parameter(
        BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM
    )
    offset_val = offset_param.AsDouble() if offset_param else 0.0
    
    # Транзакция
    with revit.Transaction('Match Family'):
        if not src_type.IsActive:
            src_type.Activate()
            doc.Regenerate()
        
        # 👇 ИСПОЛЬЗУЕМ ЧИСЛО (работает всегда!)
        # NonStructural = 0, Beam = 1, Column = 2, Brace = 3
        new_inst = doc.Create.NewFamilyInstance(
            pt,
            src_type,
            level,
            0  # NonStructural
        )
        
        # Копирование отметки
        new_offset = new_inst.get_Parameter(
            BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM
        )
        if new_offset:
            new_offset.Set(offset_val)
        
        doc.Delete(trg.Id)
    
    forms.alert('✅ Замена выполнена успешно!', title='Готово')

except Exception as e:
    forms.alert('❌ Ошибка: {}'.format(str(e)), title='Ошибка')