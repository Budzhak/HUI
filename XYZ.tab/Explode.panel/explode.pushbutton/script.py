# -*- coding: utf-8 -*-
import sys
import Autodesk.Revit.DB as RDB
from pyrevit import revit, forms
import xyz_forms as forms   # единый стиль окон XYZ (lib/xyz_forms.py)

doc = revit.doc
uidoc = revit.uidoc
active_view = doc.ActiveView

# 1. Получаем выборку элементов
selection_ids = uidoc.Selection.GetElementIds()
if not selection_ids:
    forms.alert(u"Выделите элементы!", title=u"Ошибка")
    sys.exit()

# 2. Находим базу (самый низ)
elements = [doc.GetElement(eid) for eid in selection_ids]
base_element = min(elements, key=lambda e: e.get_BoundingBox(None).Min.Z if e.get_BoundingBox(None) else 0)

# 3. Ввод шага
user_input = forms.ask_for_string(default='500', title=u"Шаг", prompt=u"Шаг (мм):")
if not user_input: sys.exit()
step_feet = float(user_input) * 0.00328084

t = RDB.Transaction(doc, u"Индивидуальный взрыв")
t.Start()

# Собираем словарь: ID элемента -> Вектор смещения
# Используем ту же логику цепочки, но для каждого элемента делаем свой набор
processed = {base_element.Id: RDB.XYZ(0, 0, 0)}
queue = [base_element]

while queue:
    current = queue.pop(0)
    curr_disp = processed[current.Id]
    
    # Ищем соседей
    mep_model = getattr(current, 'MEPModel', None)
    conn_mgr = mep_model.ConnectorManager if mep_model else getattr(current, 'ConnectorManager', None)
    if not conn_mgr: continue
    
    for conn in conn_mgr.Connectors:
        if conn.IsConnected:
            for ref in conn.AllRefs:
                next_el = ref.Owner
                if next_el.Id in selection_ids and next_el.Id not in processed:
                    
                    # Рассчитываем вектор смещения для ЭТОГО конкретного элемента
                    direction = conn.CoordinateSystem.BasisZ
                    new_disp = curr_disp + (direction * step_feet)
                    
                    # --- ГЛАВНОЕ ИЗМЕНЕНИЕ: Индивидуальный DisplacementElement ---
                    from System.Collections.Generic import List
                    id_list = List[RDB.ElementId]()
                    id_list.Add(next_el.Id)
                    
                    try:
                        # Создаем набор смещения ТОЛЬКО для одного текущего элемента
                        RDB.DisplacementElement.Create(doc, id_list, new_disp, active_view, None)
                        
                        processed[next_el.Id] = new_disp
                        queue.append(next_el)
                    except Exception as e:
                        # Если этот конкретный элемент не хочет сдвигаться, 
                        # мы не ломаем всю операцию, а идем дальше
                        continue

t.Commit()
forms.toast(u"Готово! Каждый элемент обработан индивидуально.", title=u"Результат")