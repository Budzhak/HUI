# -*- coding: utf-8 -*-
"""Участки фреонопровода (жидкостная линия): номер, размер, отводы, перепад, длина.

Правила db (17.10.2026):
- участок — от разветвителя (или муфты наружных блоков) до разветвителя или блока;
- только жидкостная линия (система «Х3…» у VRF, «Х1…» у ККБ); газовую не трогаем;
- длина = трубы + длина изгиба отводов (как «Сумма длин» ModPlus), округление ВВЕРХ до 0,1 м;
- отводы — только отводы (переходы не считаются);
- перепад — наибольший перепад высот на участке, без знака;
- номера 1, 2, 3… от наружного блока: сначала магистраль до конца, потом ответвления
  по порядку от наружного блока;
- у разветвителя в № — номера всех примыкающих участков через «-» (по возрастанию).

    import uchastki
    rep = uchastki.run(doc, view)        # в открытой транзакции
"""
import math
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, ConnectorType, Domain, Element,
                               FamilyInstance, FilteredElementCollector, MEPCurve, PartType)

FT_MM = 304.8
P_NUM, P_SIZE, P_ELB, P_DZ, P_LEN = (u'db_Участок_№', u'db_Участок_Размер', u'db_Участок_Отводы',
                                     u'db_Участок_Перепад', u'db_Участок_Длина')
P_GAS = u'db_Участок_Размер_газ'    # размер газовой трубы того же участка (db, 18.10)
P_SYS = u'db_Участок_Система'       # имя системы — для фильтра ведомости по нескольким категориям (db, 18.10)
P_GRP = u'db_Участок_Группа'        # группа в ведомости оборудования: Наружный блок / Внутренний блок / ЭРВ / Разветвитель
P_ORD = u'db_Участок_Порядок'       # порядок группы: 1 наружный, 2 разветвители, 3 ЭРВ, 4 внутренние (db, 18.10)
GRP_ORDER = {u'Наружный блок': 1, u'Разветвитель': 2, u'ЭРВ': 3, u'Внутренний блок': 4}
C_EQ = int(BuiltInCategory.OST_MechanicalEquipment)
C_ACC = int(BuiltInCategory.OST_PipeAccessory)
C_INS = int(BuiltInCategory.OST_PipeInsulations)
C_DACC = int(BuiltInCategory.OST_DuctAccessory)   # фреоновые охладители в воздуховоде (KOMFOVENT DCF)


def _iv(eid):
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


def _cat(e):
    return _iv(e.Category.Id) if e.Category is not None else 0


def _cm(e):
    if isinstance(e, MEPCurve):
        return e.ConnectorManager
    m = getattr(e, 'MEPModel', None)
    return m.ConnectorManager if m is not None else None


def _ends(e):
    cm = _cm(e)
    if cm is None:
        return []
    return [c for c in cm.Connectors if c.ConnectorType == ConnectorType.End and c.Domain == Domain.DomainPiping]


def _next(c):
    for r in c.AllRefs:
        o = r.Owner
        if _iv(o.Id) == _iv(c.Owner.Id) or o.Category is None or _cat(o) == C_INS:
            continue
        if r.ConnectorType != ConnectorType.End:
            continue
        return r
    return None


def _sysname(e):
    p = e.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
    return (p.AsString() or u'') if p is not None else u''


def is_liquid(e):
    s = _sysname(e).strip()
    return s[:2] in (u'Х3', u'Х1', u'X3', u'X1')


def is_gas(e):
    s = _sysname(e).strip()
    return s[:2] in (u'Х5', u'Х2', u'X5', u'X2')


def _fam(e):
    try:
        return e.Symbol.FamilyName or u''
    except Exception:
        return u''


def _tname(e):
    try:
        return Element.Name.GetValue(e.Symbol) if isinstance(e, FamilyInstance) else Element.Name.GetValue(e)
    except Exception:
        return u''


def is_node(e):
    c = _cat(e)
    if c == C_EQ:
        return True
    if c == C_ACC:
        f = _fam(e)
        return u'Branch' in f or u'Coupler' in f or u'разветв' in f.lower()
    return False


def is_outdoor(e):
    if _cat(e) != C_EQ:
        return False
    t = (_tname(e) + u' ' + _fam(e)).upper()
    return t.startswith(u'ARUM') or u'OUTDOOR' in t or u'НАРУЖ' in t


def is_eev(e):
    """Комплект ЭРВ (EEV kit) для ККБ: на нём участок не кончается — N.1 / N.2."""
    if _cat(e) != C_EQ:
        return False
    t = (_tname(e) + u' ' + _fam(e)).upper()
    return t.startswith(u'PRLK') or u'EEV' in t or u'ЭРВ' in t


def _piece(e):
    """(длина мм, вид: 'pipe' | 'elbow' | 'other')."""
    if isinstance(e, MEPCurve):
        return e.Location.Curve.Length * FT_MM, 'pipe'
    cs = _ends(e)
    if len(cs) != 2:
        return 0.0, 'other'
    mf = getattr(e, 'MEPModel', None)
    a, b = cs[0].Origin, cs[1].Origin
    if mf is not None and getattr(mf, 'PartType', None) == PartType.Elbow:
        d1, d2 = cs[0].CoordinateSystem.BasisZ, cs[1].CoordinateSystem.BasisZ
        th = math.pi - d1.AngleTo(d2)
        chord = a.DistanceTo(b) * FT_MM
        if th < 1e-3:
            return chord, 'elbow'
        R = chord / (2.0 * math.sin(th / 2.0))
        return R * th, 'elbow'
    return a.DistanceTo(b) * FT_MM, 'other'


def _walk(n, c0, line=None):
    """Участок от узла n через его соединитель c0. None — не та линия (по умолчанию жидкостная)."""
    line = line or is_liquid
    r = _next(c0)
    if r is None or not line(r.Owner):
        return None
    seg = {'a': n, 'b': None, 'els': [], 'pl': [], 'L': 0.0, 'Lo': 0.0, 'elb': 0, 'z0': 1e9, 'z1': -1e9, 'dia': []}
    cin, cur, guard = r, r.Owner, 0
    while cur is not None and guard < 5000:
        guard += 1
        if is_node(cur):
            seg['b'] = cur
            break
        seg['els'].append(cur)
        L, kind = _piece(cur)
        seg['pl'].append((0.0 if kind == 'other' else L, kind))     # своя доля элемента в длине участка
        if kind == 'other':
            seg['Lo'] += L
        else:
            seg['L'] += L
        if kind == 'elbow':
            seg['elb'] += 1
        for cc in _ends(cur):
            z = cc.Origin.Z
            seg['z0'] = min(seg['z0'], z)
            seg['z1'] = max(seg['z1'], z)
        if isinstance(cur, MEPCurve):
            d = round(cur.Diameter * FT_MM, 2)
            if d not in seg['dia']:
                seg['dia'].append(d)
        ends = _ends(cur)
        if len(ends) != 2:
            seg['b'] = cur     # тройник/заглушка — граница
            break
        outc = None
        for cc in ends:
            if not cc.Origin.IsAlmostEqualTo(cin.Origin):
                outc = cc
        if outc is None:
            break
        nx = _next(outc)
        if nx is None:
            break
        cin, cur = nx, nx.Owner
    return seg if seg['els'] else None


def segments(doc, view, line=None):
    """Все участки линии на виде (по умолчанию жидкостной)."""
    els = [e for e in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType() if e.Category is not None]
    segs, seen = [], set()
    for n in els:
        if not is_node(n):
            continue
        for c0 in _ends(n):
            s = _walk(n, c0, line)
            if s is None:
                continue
            k = min(_iv(e.Id) for e in s['els'])
            if k in seen:
                continue
            seen.add(k)
            segs.append(s)
    return segs


def number(segs):
    """Номера от наружного блока: на каждом разветвителе сначала ответвление целиком,
    потом дальше по магистрали (db, 18.10.2026)."""
    adj = {}
    for s in segs:
        for x in (s['a'], s['b']):
            if x is not None:
                adj.setdefault(_iv(x.Id), []).append(s)

    def other(s, nid):
        a = _iv(s['a'].Id)
        b = _iv(s['b'].Id) if s['b'] is not None else None
        return b if a == nid else a

    def weight(s, nid, seen_s):
        """Сколько участков дальше по этой ветке (магистраль — самая разветвлённая)."""
        st, tot, vis = [(s, nid)], 0, set()
        while st:
            x, fr = st.pop()
            if id(x) in vis or id(x) in seen_s:
                continue
            vis.add(id(x))
            tot += 1
            nx = other(x, fr)
            for y in adj.get(nx, []):
                if y is not x:
                    st.append((y, nx))
        return tot

    node_el = {}
    for s in segs:
        for x in (s['a'], s['b']):
            if x is not None:
                node_el[_iv(x.Id)] = x
    roots = [s for s in segs if is_outdoor(s['a']) or (s['b'] is not None and is_outdoor(s['b']))]

    def root_key(r):
        # несколько наружных блоков на муфтах: начинать с конца цепочки муфт (самые тонкие трубы)
        far = other(r, _iv(r['a'].Id) if is_outdoor(r['a']) else _iv(r['b'].Id))
        return max([max(y['dia'] or [0]) for y in adj.get(far, [])] or [0])
    # на одной муфте первым — самый дальний наружный блок: самая длинная подводка (db, 18.10)
    roots.sort(key=lambda r: (root_key(r), -(r['L'] + r.get('Lo', 0.0))))
    order, done = [], set()

    def visit(s, frm):
        """Обход «ветка целиком, потом дальше» (db, 18.10): на каждом узле сначала
        ответвления до конца, потом магистраль (самая толстая / разветвлённая)."""
        while s is not None and id(s) not in done:
            done.add(id(s))
            s['frm'] = frm
            order.append(s)
            nid = other(s, frm)
            x = node_el.get(nid)
            if x is not None and _cat(x) == C_EQ and not is_eev(x):
                return      # блок (внутренний, приточка, наружный) — конец ветки
            nxt = [y for y in adj.get(nid, []) if id(y) not in done]
            if not nxt:
                return
            # у муфты наружных блоков — сначала короткие подводки к блокам
            stubs = [y for y in nxt if any(z is not None and is_outdoor(z) for z in (y['a'], y['b']))]
            stubs.sort(key=lambda y: -(y['L'] + y.get('Lo', 0.0)))      # дальний блок — раньше
            for y in stubs:
                done.add(id(y))
                y['frm'] = nid
                order.append(y)
            nxt = [y for y in nxt if id(y) not in done]
            if not nxt:
                return
            nxt.sort(key=lambda y: (-max(y['dia'] or [0]), -weight(y, nid, done)))
            for y in nxt[1:]:
                visit(y, nid)               # ответвление целиком
            s, frm = nxt[0], nid            # потом дальше по магистрали

    for r in roots:
        frm = _iv(r['a'].Id) if is_outdoor(r['a']) else _iv(r['b'].Id)
        visit(r, frm)
    for s in segs:              # не связанные с наружным блоком — в конец
        if id(s) not in done:
            done.add(id(s))
            s['frm'] = _iv(s['a'].Id)
            order.append(s)
    # номера; участок через комплект ЭРВ (PRLK) — одна запись в две строки: N.1, N.2 (db, 17.10)
    k, labeled = 0, set()
    for s in order:
        if id(s) in labeled:
            continue
        grp, cur = [s], s
        while True:
            nd = other(cur, cur['frm'])
            x = node_el.get(nd)
            if x is None or not is_eev(x):
                break
            nx = [y for y in adj.get(nd, []) if y is not cur and id(y) not in labeled and y not in grp]
            if len(nx) != 1:
                break
            nx[0]['frm'] = nd
            grp.append(nx[0])
            cur = nx[0]
        k += 1
        for m, y in enumerate(grp):
            y['n'] = (u'%d.%d' % (k, m + 1)) if len(grp) > 1 else u'%d' % k
            labeled.add(id(y))
    out = list(order)            # порядок вывода — по номерам
    out.sort(key=lambda y: [int(p) for p in y['n'].split(u'.')])
    return out


def _pos(e):
    lp = e.Location
    p = getattr(lp, 'Point', None)
    if p is not None:
        return p
    bb = e.get_BoundingBox(None)
    return (bb.Min + bb.Max) * 0.5


def _group_ends(segs):
    """Участки с одним номером (N, N.1+N.2…) -> (концевые узлы, участки)."""
    grp = {}
    for s in segs:
        grp.setdefault(s['n'].split(u'.')[0], []).append(s)
    out = []
    for k, ss in grp.items():
        nodes = []
        for s in ss:
            for x in (s['a'], s['b']):
                if x is not None and not is_eev(x):
                    nodes.append(x)
        if len(nodes) == 2:
            out.append((nodes, ss))
    return out


def gas_sizes(doc, view, segs):
    """Размер газовой трубы для каждого жидкостного участка: газовый участок с теми же
    концами (блок — тот же элемент, разветвитель — газовая пара рядом). Возвращает номера
    участков без пары; s['gas'] — строка размера."""
    gsegs = segments(doc, view, is_gas)
    gends = []
    for g in gsegs:
        if g['b'] is None:
            continue
        gends.append((_pos(g['a']), _pos(g['b']), u'/'.join(u'Ø%g' % d for d in sorted(g['dia']))))
    miss = []
    for nodes, ss in _group_ends(segs):
        pa, pb = _pos(nodes[0]), _pos(nodes[1])
        best = None
        for ga, gb, sz in gends:
            d = min(pa.DistanceTo(ga) + pb.DistanceTo(gb), pa.DistanceTo(gb) + pb.DistanceTo(ga)) * FT_MM
            if best is None or d < best[0]:
                best = (d, sz)
        if best is not None and best[0] < 1500.0:
            for s in ss:
                s['gas'] = best[1]
        else:
            miss.append(ss[0]['n'].split(u'.')[0])
    return miss


def _set(e, name, val):
    p = e.LookupParameter(name)
    if p is None or p.IsReadOnly:
        return False
    if val is None:
        try:
            if p.StorageType.ToString() == 'String':
                p.Set(u'')
            else:
                p.ClearValue() if hasattr(p, 'ClearValue') else None
        except Exception:
            pass
        return True
    p.Set(val)
    return True


def _liquid_els(doc, view):
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementMulticategoryFilter
    cats = List[BuiltInCategory]([BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_PipeFitting,
                                  BuiltInCategory.OST_PipeAccessory, BuiltInCategory.OST_MechanicalEquipment])
    return [e for e in FilteredElementCollector(doc, view.Id).WherePasses(ElementMulticategoryFilter(cats))
            .WhereElementIsNotElementType() if is_liquid(e) or is_node(e)]


def run(doc, view, write=True):
    """Посчитать и (write) записать параметры. Возвращает (участки по номерам, отчёт).
    Перед записью у всех элементов жидкостной линии вида параметры участка очищаются."""
    segs = number(segments(doc, view))
    if not segs:
        return segs, [u'На виде нет жидкостной линии фреонопровода (системы Х1…/Х3…)']
    if write:
        for e in _liquid_els(doc, view):
            for nm in (P_NUM, P_SIZE, P_GAS, P_SYS, P_GRP):
                _set(e, nm, u'')
            for nm in (P_ELB, P_DZ, P_LEN, P_ORD):
                _set(e, nm, None)
    gas = gas_sizes(doc, view, segs)
    node_nums = {}
    nmiss = []
    for s in segs:
        Lm = math.ceil(round(s['L'] / 100.0, 6)) / 10.0       # м, вверх до 0,1
        dz = (s['z1'] - s['z0']) * FT_MM / 1000.0 if s['z1'] > s['z0'] else 0.0
        size = u'/'.join(u'Ø%s' % (u'%g' % d) for d in sorted(s['dia']))
        s['Lm'], s['dz'], s['size'] = Lm, dz, size
        for x in (s['a'], s['b']):
            if x is not None and _cat(x) == C_ACC:
                node_nums.setdefault(_iv(x.Id), (x, []))[1].append(s['n'])
        if write:
            # db 18.10: у каждого элемента — своя длина (труба — длина, отвод — дуга, переход — 0)
            # и свой отвод (1/0): ведомость «Вычислять итоги» складывает их сама. Округление
            # участка вверх до 0,1 м — добавка к самой длинной трубе, сумма = Lm ровно.
            pl = s.get('pl') or [(0.0, 'other')] * len(s['els'])
            own = [x[0] for x in pl]
            pipes = [i for i, e in enumerate(s['els']) if isinstance(e, MEPCurve)]
            if pipes:
                k = max(pipes, key=lambda i: own[i])
                own[k] += Lm * 1000.0 - sum(own)
            for i, e in enumerate(s['els']):
                ok = _set(e, P_NUM, s['n'])
                if not ok:
                    nmiss.append(e)
                    continue
                _set(e, P_SIZE, size)
                _set(e, P_SYS, _sysname(e) or u'')
                _set(e, P_GAS, s.get('gas') or u'')
                _set(e, P_ELB, 1 if pl[i][1] == 'elbow' else 0)
                _set(e, P_DZ, round(dz * 1000.0, 0) / FT_MM)
                _set(e, P_LEN, max(0.0, own[i]) / FT_MM)
    if write:
        _write_nodes(segs)
        for nid, (x, ns) in node_nums.items():
            ns = sorted(set(ns), key=lambda n: [int(p) for p in n.split(u'.')])
            _set(x, P_NUM, u'-'.join(ns))
    rep = [u'Участков: %d, разветвителей: %d' % (len(segs), len(node_nums))]
    if gas:
        rep.append(u'Без пары на газовой линии: %s' % u', '.join(gas))
    nmiss = [e for e in nmiss if _cat(e) != int(BuiltInCategory.OST_DuctAccessory)]
    if nmiss:
        cats = sorted(set(e.Category.Name for e in nmiss))
        rep.append(u'Без параметра %s (категория не подключена): %d шт. — %s' % (P_NUM, len(nmiss), u', '.join(cats)))
    sy = [x for x in systems(segs, all_=True) if u',' not in x[0]]
    if len(sy) > 1:
        rep.append(u'ВНИМАНИЕ: на виде несколько жидкостных систем: %s — проверьте назначение систем' %
                   u', '.join(u'%s (%d эл.)' % (n, c) for n, c in sy))
    return segs, rep


SCHED_PREFIX = u'db_Участки_'
HEADS = ((P_NUM, u'№ участка'), (P_SIZE, u'Размер трубы (жидк.)'), (P_GAS, u'Размер трубы (газ)'),
         (P_ELB, u'Кол. отводов (жидк.)'), (P_DZ, u'Перепад высот (жидк.), м'), (P_LEN, u'Длина участка (жидк.), м'))


def systems(segs, all_=False):
    """Основная жидкостная система вида (по числу элементов); all_ — все с количеством."""
    cnt = {}
    for s in segs:
        for e in s['els']:
            n = _sysname(e)
            if n:
                cnt[n] = cnt.get(n, 0) + 1
    srt = sorted(cnt.items(), key=lambda kv: -kv[1])
    if all_:
        return srt
    return [srt[0][0]] if srt else []


def _fmt_m(f, acc):
    from Autodesk.Revit.DB import FormatOptions, UnitTypeId
    fo = FormatOptions()
    fo.UseDefault = False
    fo.SetUnitTypeId(UnitTypeId.Meters)
    fo.Accuracy = acc
    f.SetFormatOptions(fo)


def _sync_fields(doc, vs):
    """Столбцы и заголовки по HEADS (у старой спецификации — добавить недостающие)."""
    sd = vs.Definition
    by_name = {}
    for sf in sd.GetSchedulableFields():
        by_name[sf.GetName(doc)] = sf
    have = {}
    for i in range(sd.GetFieldCount()):
        f = sd.GetField(i)
        have[f.GetName()] = f
    fields = []
    for pos, (nm, h) in enumerate(HEADS):
        f = have.get(nm)
        if f is None:
            f = sd.InsertField(by_name[nm], pos)
        f.ColumnHeading = h
        fields.append(f)
    _fmt_m(fields[4], 0.01)
    _fmt_m(fields[5], 0.1)
    return fields


def _setup_schedule(doc, vs, sysname):
    """Графы, фильтры, сортировка, итоги новой ведомости участков (несколько категорий)."""
    from Autodesk.Revit.DB import (ElementId, ScheduleFilter, ScheduleFilterType, ScheduleSortGroupField,
                                   ScheduleFieldDisplayType)
    sd = vs.Definition
    fsys = fcat = None
    for sf in sd.GetSchedulableFields():
        # «Имя системы» в ведомости по нескольким категориям недоступно — своё поле db_Участок_Система
        if sf.GetName(doc) == P_SYS:
            fsys = sf
        elif sf.ParameterId == ElementId(BuiltInParameter.ELEM_CATEGORY_PARAM):
            fcat = sf
    if fsys is None:
        raise Exception(u'нет общего параметра %s (ФОП, группа db_Параметры)' % P_SYS)
    added = _sync_fields(doc, vs)
    fs = sd.AddField(fsys)
    fs.IsHidden = True
    sd.AddFilter(ScheduleFilter(fs.FieldId, ScheduleFilterType.Equal, sysname))
    sd.AddFilter(ScheduleFilter(added[0].FieldId, ScheduleFilterType.HasValue))
    if fcat is not None:        # разветвители (арматура) несут «a-b-c» — не строки ведомости
        fc = sd.AddField(fcat)
        fc.IsHidden = True
        sd.AddFilter(ScheduleFilter(fc.FieldId, ScheduleFilterType.NotEqual,
                                    ElementId(BuiltInCategory.OST_PipeAccessory)))
    sd.AddSortGroupField(ScheduleSortGroupField(added[0].FieldId))
    sd.IsItemized = False
    for f in (added[3], added[5]):           # отводы и длина — сумма по участку
        f.DisplayType = ScheduleFieldDisplayType.Totals
    sd.ShowGrandTotal = True
    sd.ShowGrandTotalTitle = False
    sd.ShowGrandTotalCount = False
    return vs


def _is_multi(vs):
    return _iv(vs.Definition.CategoryId) == -1


def _migrate(doc, old, sysname):
    """Старая ведомость по трубам → по нескольким категориям (трубы + соединительные детали),
    с тем же именем, оформлением инженера и местами на листах (db 18.10)."""
    from Autodesk.Revit.DB import ElementId, ScheduleSheetInstance, ViewSchedule
    import hui_schedfmt as sfm
    name = old.Name
    places = []
    for si in FilteredElementCollector(doc).OfClass(ScheduleSheetInstance):
        if si.ScheduleId == old.Id:
            places.append((si.OwnerViewId, si.Point, si.Rotation))
    vs = ViewSchedule.CreateSchedule(doc, ElementId.InvalidElementId)
    vs.Name = name + u' (нов)'
    _setup_schedule(doc, vs, sysname)
    doc.Regenerate()
    notes = sfm.copy_format(doc, old, vs)
    sfm.copy_field_calc(doc, old, vs)
    for f in sfm.fields(vs):                  # итоги длины и отводов — всегда
        if f.GetName() in (P_LEN, P_ELB):
            from Autodesk.Revit.DB import ScheduleFieldDisplayType
            f.DisplayType = ScheduleFieldDisplayType.Totals
    od, nd = old.Definition, vs.Definition
    for nm in ('ShowGrandTotal', 'ShowGrandTotalTitle', 'ShowGrandTotalCount', 'GrandTotalTitle'):
        try:
            setattr(nd, nm, getattr(od, nm))
        except Exception:
            pass
    nd.ShowGrandTotal = True
    try:
        vs.ViewTemplateId = old.ViewTemplateId
    except Exception:
        pass
    from Autodesk.Revit.DB import DocumentValidation
    for si in [x for x in FilteredElementCollector(doc).OfClass(ScheduleSheetInstance) if x.ScheduleId == old.Id]:
        doc.Delete(si.Id)
    if DocumentValidation.CanDeleteElement(doc, old.Id):
        doc.Delete(old.Id)
    else:       # открыта во вкладке или занята другим пользователем — удалить нельзя
        old.Name = name + u' (старая — удалить)'
        notes.append(u'старую ведомость удалить нельзя (открыта во вкладке?) — переименована в «%s», '
                     u'удалите её вручную' % old.Name)
    vs.Name = name
    for sheet_id, pt, rot in places:
        si = ScheduleSheetInstance.Create(doc, sheet_id, vs.Id, pt)
        try:
            si.Rotation = rot
        except Exception:
            pass
    return vs, notes


def ensure_schedule(doc, sysname):
    """Ведомость участков системы (одна строка на участок, ведомость по нескольким категориям:
    трубы + отводы, итоги длины и отводов считает Revit). Старая по трубам — переделывается."""
    from Autodesk.Revit.DB import ElementId, ViewSchedule
    name = SCHED_PREFIX + sysname
    for v in FilteredElementCollector(doc).OfClass(ViewSchedule):
        if v.Name == name:
            if not _is_multi(v):
                vs, notes = _migrate(doc, v, sysname)
                return vs, u'переделана: по нескольким категориям' + (u' (' + u'; '.join(notes) + u')' if notes else u'')
            _sync_fields(doc, v)
            return v, False
    vs = ViewSchedule.CreateSchedule(doc, ElementId.InvalidElementId)
    vs.Name = name
    _setup_schedule(doc, vs, sysname)
    try:        # заголовок — текстом, не именем вида (как у db_Участки_Х1.ККБ1)
        from Autodesk.Revit.DB import SectionType, CellType
        hs = vs.GetTableData().GetSectionData(SectionType.Header)
        hs.SetCellType(hs.FirstRowNumber, hs.FirstColumnNumber, CellType.Text)
        hs.SetCellText(hs.FirstRowNumber, hs.FirstColumnNumber, u'Участки фреонопроводов ' + sysname)
    except Exception:
        pass
    return vs, True


# ================================================================== ведомость оборудования системы
EQ_PREFIX = u'db_Оборудование_'
EQ_SAMPLE = u'db_характеристики систем холодоснабжения'      # образец оформления (db)


def node_group(x):
    """Группа узла жидкостной линии для ведомости оборудования (db, 18.10)."""
    if is_outdoor(x):
        return u'Наружный блок'
    if is_eev(x):
        return u'ЭРВ'
    if _cat(x) == C_ACC:
        return u'Разветвитель'
    if _cat(x) == C_EQ:
        return u'Внутренний блок'
    if _cat(x) == C_DACC and _ends(x):      # охладитель в воздуховоде с фреоновыми патрубками (db 19.10)
        return u'Внутренний блок'
    return None


def _tparam(e, name):
    """Параметр экземпляра, а если его нет — типа."""
    p = e.LookupParameter(name)
    if p is None:
        t = e.Document.GetElement(e.GetTypeId())
        p = t.LookupParameter(name) if t is not None else None
    return p


def _fill(e, name, val):
    """Записать, только если пусто (экземпляр или тип)."""
    p = _tparam(e, name)
    if p is not None and not p.IsReadOnly and not (p.AsString() or u'').strip():
        p.Set(val)


def _write_nodes(segs):
    """Узлам жидкостной линии (наружный блок, обслуживаемые блоки, ЭРВ, разветвители) — система и
    группа для ведомости оборудования. У разветвителей LG без марки — марка из типа
    («ARBLN07121_Liquid» → «ARBLN07121»), ед. изм. и завод, только если пусто."""
    sy = systems(segs)
    sysname = sy[0] if sy else u''
    seen = set()
    for s in segs:
        for x in (s['a'], s['b']):
            if x is None or _iv(x.Id) in seen:
                continue
            seen.add(_iv(x.Id))
            g = node_group(x)
            if g is None:
                continue
            _set(x, P_SYS, sysname)
            _set(x, P_GRP, g)
            _set(x, P_ORD, GRP_ORDER.get(g, 9))
            if g == u'Внутренний блок':
                # «Тип» в ведомости — «Комментарии к типоразмеру»; пусто — имя системы блока (db_Имя системы)
                ps = x.LookupParameter(u'db_Имя системы')
                if ps is not None and (ps.AsString() or u'').strip():
                    _fill(x, u'Комментарии к типоразмеру', ps.AsString().strip())
            if g == u'Разветвитель' and u'LG' in _fam(x):
                tn = _tname(x)
                base = tn.split(u'_')[0] if u'_' in tn else tn
                _fill(x, u'ADSK_Марка', base)
                # разветвитель LG — комплект из пары жидк. + газ. (db 18.10): «компл.» вместо пустого
                # или «шт.», которое вписывалось раньше
                pu = _tparam(x, u'ADSK_Единица измерения')
                if pu is not None and not pu.IsReadOnly and (pu.AsString() or u'').strip() in (u'', u'шт.', u'шт'):
                    pu.Set(u'компл.')
                _fill(x, u'ADSK_Завод-изготовитель', u'LG Electronics')


def _iv_fid(fid):
    try:
        return fid.IntegerValue
    except Exception:
        return str(fid)


def _eq_sorting(doc, vs):
    """Группы по порядку: наружный блок, разветвители, ЭРВ, внутренние блоки (db, 18.10): сначала
    по скрытому db_Участок_Порядок, затем по db_Участок_Группа с заголовком и итогом, затем марка, Qх."""
    from Autodesk.Revit.DB import ScheduleSortGroupField
    sd = vs.Definition
    have = dict((sd.GetField(i).GetName(), sd.GetField(i)) for i in range(sd.GetFieldCount()))
    if P_ORD not in have:
        for sf in sd.GetSchedulableFields():
            if sf.GetName(doc) == P_ORD:
                f = sd.AddField(sf)
                f.IsHidden = True
                have[P_ORD] = f
                break
    sd.ClearSortGroupFields()
    if P_ORD in have:
        have[P_ORD].IsHidden = True
        sd.AddSortGroupField(ScheduleSortGroupField(have[P_ORD].FieldId))
    g = ScheduleSortGroupField(have[P_GRP].FieldId)
    g.ShowHeader = True
    g.ShowFooter = True
    g.ShowFooterTitle = True
    g.ShowFooterCount = True
    sd.AddSortGroupField(g)
    for nm in (u'ADSK_Марка', u'ADSK_Холодильная мощность'):    # одна марка с разной Qх — разные строки
        if nm in have:
            sd.AddSortGroupField(ScheduleSortGroupField(have[nm].FieldId))


EQ_HEADS = ((u'ADSK_Марка', u'Марка'), (u'ADSK_Холодильная мощность', u'Qх, кВт'),
            (u'ADSK_Единица измерения', u'Ед. изм.'), (u'__count__', u'Кол.'),
            (u'Комментарии к типоразмеру', u'Тип'), (u'ADSK_Завод-изготовитель', u'Завод'))


def ensure_equipment_schedule(doc, sysname, label=None):
    """Ведомость оборудования фреоновой системы (несколько категорий: оборудование + арматура):
    наружный блок, обслуживаемые блоки, ЭРВ, разветвители — по группам, кол. — числом элементов
    жидкостной линии. Оформление — с «db_характеристики систем холодоснабжения». Новая —
    на лист рядом с ведомостью участков. -> (ведомость, 'создана'|'обновлена'|..)."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import (ElementId, ScheduleFieldId, ScheduleFieldType, ScheduleFilter,
                                   ScheduleFilterType, ScheduleSheetInstance, ScheduleSortGroupField,
                                   SectionType, CellType, ViewSchedule, XYZ)
    name = EQ_PREFIX + sysname
    for v in FilteredElementCollector(doc).OfClass(ViewSchedule):
        if v.Name == name:
            _eq_sorting(doc, v)
            return v, u'обновлена'
    vs = ViewSchedule.CreateSchedule(doc, ElementId.InvalidElementId)
    vs.Name = name
    sd = vs.Definition
    by = {}
    cnt = None
    for sf in sd.GetSchedulableFields():
        if sf.FieldType == ScheduleFieldType.Count:
            cnt = sf
        else:
            by.setdefault(sf.GetName(doc), sf)
    order = []
    fsys = sd.AddField(by[P_SYS])
    fsys.IsHidden = True
    fgrp = sd.AddField(by[P_GRP])
    fgrp.IsHidden = True
    vis = {}
    for nm, h in EQ_HEADS:
        sf = cnt if nm == u'__count__' else by.get(nm)
        if sf is None:
            continue
        f = sd.AddField(sf)
        f.ColumnHeading = h
        vis[nm] = f
    sd.AddFilter(ScheduleFilter(fsys.FieldId, ScheduleFilterType.Equal, sysname))
    sd.AddFilter(ScheduleFilter(fgrp.FieldId, ScheduleFilterType.HasValue))
    sd.IsItemized = False
    _eq_sorting(doc, vs)
    # оформление с образца инженера
    try:
        import hui_schedfmt as sfm
        smp = [v for v in FilteredElementCollector(doc).OfClass(ViewSchedule) if v.Name == EQ_SAMPLE]
        if smp:
            sfm.copy_format(doc, smp[0], vs)
    except Exception:
        pass
    shown = set(_iv_fid(f.FieldId) for f in vis.values())
    ids = [f.FieldId for nm, h in EQ_HEADS for f in [vis.get(nm)] if f is not None] + \
          [sd.GetFieldId(i) for i in range(sd.GetFieldCount()) if _iv_fid(sd.GetFieldId(i)) not in shown]
    sd.SetFieldOrder(List[ScheduleFieldId](ids))
    for nm, h in EQ_HEADS:          # подписи — свои (образец мог переписать)
        if nm in vis:
            vis[nm].ColumnHeading = h
            vis[nm].IsHidden = False
    fsys.IsHidden = True
    fgrp.IsHidden = True
    # заголовок — текстом, не именем вида
    try:
        hs = vs.GetTableData().GetSectionData(SectionType.Header)
        hs.SetCellType(hs.FirstRowNumber, hs.FirstColumnNumber, CellType.Text)
        hs.SetCellText(hs.FirstRowNumber, hs.FirstColumnNumber,
                       u'Технические характеристики оборудования системы ' + (label or sysname))   # перенос — сам Revit; ширину в PDF правит «PDF вектор» (db 19.10)
    except Exception:
        pass
    # на лист — слева от ведомости участков
    try:
        doc.Regenerate()
        us = [v for v in FilteredElementCollector(doc).OfClass(ViewSchedule) if v.Name == SCHED_PREFIX + sysname]
        if us:
            for si in FilteredElementCollector(doc).OfClass(ScheduleSheetInstance):
                if si.ScheduleId == us[0].Id:
                    w = sum(f.SheetColumnWidth for f in vis.values())
                    pt = si.Point + XYZ(-(w + 15.0 / FT_MM), 0, 0)
                    ScheduleSheetInstance.Create(doc, si.OwnerViewId, vs.Id, pt)
                    return vs, u'создана и поставлена на лист'
    except Exception:
        pass
    return vs, u'создана'


# ================================================================== ведомость труб и отводов системы
# db 18.10: сводная ведомость меди на листе схемы, данные — для итоговой спецификации объекта.
# Наименование и количество — ADSK (экземпляр), ед. изм. — db_Единица измерения (экземпляр:
# ADSK_Единица измерения в проекте — параметр типа, а у одного типа отвода бывает и «м», и «шт.»).
P_NAME, P_QTY = u'ADSK_Наименование', u'ADSK_Количество'
P_UNIT = u'db_Единица измерения'
P_TSYS = u'db_Трубы_Система'        # система (жидк. + газ.) — фильтр таблиц труб и отводов
P_PSIZE = u'db_Трубы_Размер'        # «Ø25.4» — графа «Размер трубы» / «Размер»
P_ANG = u'db_Трубы_Угол'            # «90°» — у отводов под пайку
BEND_MAX = 23.0                     # отводы до 23 мм — гнутые участки трубы, от 23 мм — под пайку
# 30.10.2026 db: свой ФОП плагина (те же GUID, что ФОП2021(КС), только нужные параметры) — лежит в расширении,
# поэтому работает на любом ПК и из GitHub
import os
FOP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), u'ФОП', u'db_ФОП.txt')
C_PIPE = int(BuiltInCategory.OST_PipeCurves)


def _wall(e):
    p = e.get_Parameter(BuiltInParameter.RBS_PIPE_WALL_THICKNESS)
    try:
        return round(p.AsDouble() * FT_MM, 2) if p is not None else 0.0
    except Exception:
        return 0.0


def _fd(d):
    return u'%g' % round(d, 2)


def _ft(t):
    s = (u'%.2f' % t).rstrip(u'0')
    if s.endswith(u'.'):
        s += u'0'
    return s.replace(u'.', u',')


def pipe_name(d, t):
    return u'Труба медная ⌀%sх%s мм' % (_fd(d), _ft(t)) if t > 0 else u'Труба медная ⌀%s мм' % _fd(d)


def _elbow(e):
    """(диаметр мм, угол °, длина дуги мм) или None, если не отвод."""
    mf = getattr(e, 'MEPModel', None)
    if mf is None or getattr(mf, 'PartType', None) != PartType.Elbow:
        return None
    cs = _ends(e)
    if len(cs) != 2:
        return None
    d = round(cs[0].Radius * 2.0 * FT_MM, 2)
    th = math.pi - cs[0].CoordinateSystem.BasisZ.AngleTo(cs[1].CoordinateSystem.BasisZ)
    L, _k = _piece(e)
    return d, int(round(math.degrees(th))), L


def _bind_cats(doc, names_cats):
    """Параметры экземпляра на категории: нет в проекте — из ФОП; есть, но не на всех
    категориях — добавить категории. -> список изменений."""
    from Autodesk.Revit.DB import Category, InstanceBinding, GroupTypeId
    app = doc.Application
    bm = doc.ParameterBindings
    have = {}
    it = bm.ForwardIterator()
    while it.MoveNext():
        have[it.Key.Name] = (it.Key, it.Current)
    out, need_fop = [], []
    for nm, cats in names_cats:
        if nm in have:
            dfn, b = have[nm]
            if not isinstance(b, InstanceBinding):
                out.append(u'%s — параметр типа, не трогаю' % nm)
                continue
            cs = b.Categories
            add = [c for c in cats if not cs.Contains(Category.GetCategory(doc, c))]
            if add:
                for c in add:
                    cs.Insert(Category.GetCategory(doc, c))
                try:
                    grp = dfn.GetGroupTypeId()
                except Exception:
                    grp = GroupTypeId.Data
                bm.ReInsert(dfn, InstanceBinding(cs), grp)
                out.append(u'%s — добавлены категории' % nm)
        else:
            need_fop.append((nm, cats))
    if need_fop:
        old = app.SharedParametersFilename
        try:
            import os
            if os.path.exists(FOP):
                app.SharedParametersFilename = FOP
            df = app.OpenSharedParameterFile()
            defs = {}
            for g in df.Groups:
                for d in g.Definitions:
                    defs.setdefault(d.Name, d)
            for nm, cats in need_fop:
                d = defs.get(nm)
                if d is None:
                    out.append(u'%s — нет в ФОП' % nm)
                    continue
                cs = app.Create.NewCategorySet()
                for c in cats:
                    cs.Insert(Category.GetCategory(doc, c))
                bm.Insert(d, InstanceBinding(cs), GroupTypeId.Data)
                out.append(u'%s — добавлен из ФОП' % nm)
        finally:
            try:
                app.SharedParametersFilename = old
            except Exception:
                pass
    return out


def ensure_pipe_params(doc):
    pf = [BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_PipeFitting]
    da = [BuiltInCategory.OST_DuctAccessory]
    return _bind_cats(doc, [(P_UNIT, pf), (P_TSYS, pf), (P_PSIZE, pf), (P_ANG, pf), (P_ORD, pf + da),
                            (P_SYS, da), (P_GRP, da)])


def pipe_rows(doc, view, sysname, write=True):
    """Трубы и отводы жидкостной и газовой линий вида (db 18.10). Каждому элементу:
    ADSK_Наименование (полное, с толщиной стенки — для итоговой спецификации), db_Единица измерения,
    ADSK_Количество, db_Трубы_Размер «Ø25.4», db_Трубы_Угол «90°» (только отводы под пайку).
    Отводы до 23 мм — гиб трубы: идут в длину трубы своего диаметра (длина дуги). Длина по
    диаметру — вверх до 0,1 м, добавка к самой длинной трубе.
    -> (трубы [(размер, м)], отводы [(размер, угол, шт)], диаметры {'bend': [..], 'solder': [..]}, отчёт)."""
    from Autodesk.Revit.DB import ElementMulticategoryFilter
    from System.Collections.Generic import List
    cats = List[BuiltInCategory]([BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_PipeFitting])
    els = [e for e in FilteredElementCollector(doc, view.Id).WherePasses(ElementMulticategoryFilter(cats))
           .WhereElementIsNotElementType() if is_liquid(e) or is_gas(e)]
    walls = {}
    for e in els:
        if _cat(e) == C_PIPE:
            t = _wall(e)
            if t > 0:
                d = round(e.Diameter * FT_MM, 2)
                walls.setdefault(d, {})
                walls[d][t] = walls[d].get(t, 0) + 1

    def wall_of(d):
        w = walls.get(d)
        return max(w.items(), key=lambda kv: kv[1])[0] if w else 0.0
    # [элемент, наим., ед., кол., порядок, труба?, размер, угол]
    items, other, nowall = [], 0, []
    for e in els:
        if _cat(e) == C_PIPE:
            d = round(e.Diameter * FT_MM, 2)
            t = _wall(e)
            if t <= 0:
                t = wall_of(d)
                nowall.append(e)
            items.append([e, pipe_name(d, t), u'м', e.Location.Curve.Length * FT_MM / 1000.0,
                          100000 + int(round(d * 100)), True, u'Ø' + _fd(d), u''])
            continue
        el = _elbow(e)
        if el is None:
            other += 1
            continue
        d, ang, L = el
        if d > BEND_MAX:
            items.append([e, u'Отвод медный под пайку %d° ⌀%s мм' % (ang, _fd(d)), u'шт.', 1.0,
                          200000 + int(round(d * 100)), False, u'Ø' + _fd(d), u'%d°' % ang])
        else:
            items.append([e, pipe_name(d, wall_of(d)), u'м', L / 1000.0, 100000 + int(round(d * 100)), False,
                          u'Ø' + _fd(d), u''])
    # длина по размеру — вверх до 0,1 м (строка таблицы = размер; толщина у одного размера одна)
    grp = {}
    for it in items:
        if it[2] == u'м':
            grp.setdefault(it[6], []).append(it)
    for sz, its in grp.items():
        tot = sum(x[3] for x in its)
        tgt = math.ceil(round(tot * 10.0, 6)) / 10.0
        pipes = [x for x in its if x[5]] or its
        k = max(pipes, key=lambda x: x[3])
        k[3] += tgt - tot
    tp, te, dia = {}, {}, {'bend': set(), 'solder': set()}
    for it in items:
        d = (it[4] % 100000) / 100.0
        if it[2] == u'м':
            r = tp.setdefault(it[6], [it[4], it[6], 0.0])
            r[2] += it[3]
            dia['bend'].add(d)
        else:
            r = te.setdefault((it[6], it[7]), [it[4], it[6], it[7], 0])
            r[3] += 1
            dia['solder'].add(d)
    tubes = [tuple(r[1:]) for r in sorted(tp.values())]
    elbows = [tuple(r[1:]) for r in sorted(te.values())]
    if write:
        for e in els:
            _set(e, P_TSYS, u'')
        for e, nm, u, q, o, _p, sz, ang in items:
            _set(e, P_NAME, nm)
            _set(e, P_UNIT, u)
            _set(e, P_QTY, round(q, 6))
            _set(e, P_TSYS, sysname)
            _set(e, P_ORD, o)
            _set(e, P_PSIZE, sz)
            _set(e, P_ANG, ang)
    rep = [u'Фреонопроводы: %d размеров, отводы под пайку: %d шт.' % (len(tubes), sum(r[2] for r in elbows))]
    if other:
        rep.append(u'переходы в таблицы не входят: %d шт.' % other)
    if nowall:
        rep.append(u'у труб без толщины стенки взята обычная для диаметра: %s' %
                   u', '.join(str(_iv(e.Id)) for e in nowall[:10]))
    return tubes, elbows, dia, rep


def sys_label(doc, view, sysname):
    """«Х1.ККБ1» + газовая «Х2.ККБ1 (1)» -> «Х(1,2).ККБ1»."""
    import re
    m = re.match(u'^([ХX])(\\d+)\\.(.+)$', sysname.strip(), re.U)
    if m is None:
        return sysname
    nums = [m.group(2)]
    for e in FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_PipeCurves).WhereElementIsNotElementType():
        if not is_gas(e):
            continue
        g = re.match(u'^[ХX](\\d+)\\.(\\S+)', _sysname(e).strip(), re.U)
        if g is not None and g.group(2) == m.group(3).split(u' ')[0] and g.group(1) not in nums:
            nums.append(g.group(1))
    if len(nums) == 1:
        return sysname
    return u'%s(%s).%s' % (m.group(1), u','.join(sorted(nums, key=int)), m.group(3))


def _fixed01(f):
    from Autodesk.Revit.DB import FormatOptions, UnitTypeId
    try:
        fo = FormatOptions()
        fo.UseDefault = False
        fo.SetUnitTypeId(UnitTypeId.Fixed)      # у «Общего» нельзя ни точность, ни «без нулей»
        fo.Accuracy = 0.1
        fo.SuppressTrailingZeros = True
        f.SetFormatOptions(fo)
    except Exception:
        pass


def _new_table(doc, name, title, sysname, unit, cols):
    """Ведомость по нескольким категориям: фильтр по системе и ед. изм., cols — [(параметр,
    заголовок, ширина мм)], последняя графа — сумма ADSK_Количество."""
    from Autodesk.Revit.DB import (ElementId, ScheduleFieldDisplayType, ScheduleFilter, ScheduleFilterType,
                                   ScheduleSortGroupField, SectionType, CellType, ViewSchedule)
    vs = ViewSchedule.CreateSchedule(doc, ElementId.InvalidElementId)
    vs.Name = name
    sd = vs.Definition
    by = {}
    for sf in sd.GetSchedulableFields():
        by.setdefault(sf.GetName(doc), sf)
    for nm in [P_TSYS, P_UNIT, P_ORD] + [c[0] for c in cols]:
        if nm not in by:
            raise Exception(u'нет параметра %s у труб и соединительных деталей' % nm)
    fsys = sd.AddField(by[P_TSYS])
    fun = sd.AddField(by[P_UNIT])
    ford = sd.AddField(by[P_ORD])
    vis = []
    for nm, h, wmm in cols:
        f = sd.AddField(by[nm])
        f.ColumnHeading = h
        try:
            f.SheetColumnWidth = wmm / FT_MM
        except Exception:
            pass
        vis.append(f)
    for f in (fsys, fun, ford):
        f.IsHidden = True
    vis[-1].DisplayType = ScheduleFieldDisplayType.Totals
    _fixed01(vis[-1])
    sd.AddFilter(ScheduleFilter(fsys.FieldId, ScheduleFilterType.Equal, sysname))
    sd.AddFilter(ScheduleFilter(fun.FieldId, ScheduleFilterType.Equal, unit))
    sd.AddSortGroupField(ScheduleSortGroupField(ford.FieldId))
    for f in vis[:-1]:
        sd.AddSortGroupField(ScheduleSortGroupField(f.FieldId))
    sd.IsItemized = False
    sd.ShowGrandTotal = False
    try:
        hs = vs.GetTableData().GetSectionData(SectionType.Header)
        hs.SetCellType(hs.FirstRowNumber, hs.FirstColumnNumber, CellType.Text)
        hs.SetCellText(hs.FirstRowNumber, hs.FirstColumnNumber, title)
    except Exception:
        pass
    return vs, sum(c[2] for c in cols)


EEV_NOTE = (u'Длина фреонопровода от ЭРВ до охладителя — не более 3 м (требование LG Electronics): '
            u'ЭРВ устанавливать у охладителя, независимо от условного положения на схеме.')


def bend_note(dia, eev=False):
    """Примечание под таблицами: до какого диаметра повороты — гибом, с какого — отводами."""
    bend = sorted(d for d in dia['bend'] if d <= BEND_MAX)
    sold = sorted(dia['solder'])
    fmt = lambda d: (u'%g' % d).replace(u'.', u',')
    parts = []
    if bend:
        parts.append(u'Повороты фреонопроводов наружным диаметром до %s мм включительно выполняются '
                     u'изгибом трубы с помощью трубогиба, без изломов, гофр и сплющивания сечения; длина гибов '
                     u'учтена в длине фреонопроводов.' % fmt(bend[-1]))
    if sold:
        parts.append(u'Повороты фреонопроводов наружным диаметром %s мм и более выполняются с применением '
                     u'медных отводов под пайку.' % fmt(sold[0]))
    parts.append(u'Длина фреонопроводов указана по схеме, без запаса (для расчёта системы LG Electronics); '
                 u'при заказе труб предусмотреть запас на монтаж.')        # db 19.10
    if eev:         # db 19.10: ЭРВ — не дальше 3 м от охладителя (инженер LG)
        parts.append(EEV_NOTE)
    return (u'Примечание. ' + u' '.join(parts)) if parts else u''


TUBE_PREFIX, ELB_PREFIX = u'db_Фреонопроводы_', u'db_Отводы_'
NOTE_TYPE = u'ADSK_Основной текст_2.5'


def ensure_pipe_tables(doc, view, sysname, dia):
    """Две таблицы системы: «Фреонопроводы системы Х(1,2).ККБ1» (Размер трубы · Длина, м) и
    «Отводы системы Х(1,2).ККБ1» (Размер · Угол · Кол., шт.). Новые — на лист рядом друг с другом
    под ведомость оборудования (или участков), под ними — примечание о гибах. -> отчёт."""
    from Autodesk.Revit.DB import (ScheduleSheetInstance, TextNote, TextNoteOptions, TextNoteType,
                                   ElementTypeGroup, ViewSchedule, XYZ)
    lab = sys_label(doc, view, sysname)
    have = dict((v.Name, v) for v in FilteredElementCollector(doc).OfClass(ViewSchedule))
    tn, en = TUBE_PREFIX + sysname, ELB_PREFIX + sysname
    if tn in have and en in have:
        return [u'«%s», «%s» — обновлены' % (tn, en)]
    out, made = [], []
    if tn not in have:
        vt, wt = _new_table(doc, tn, u'Фреонопроводы системы ' + lab, sysname, u'м',
                            [(P_PSIZE, u'Размер трубы', 22.0), (P_QTY, u'Длина, м', 18.0)])
        made.append((vt, wt))
        out.append(u'«%s» — создана' % tn)
    if en not in have:
        ve, we = _new_table(doc, en, u'Отводы системы ' + lab, sysname, u'шт.',
                            [(P_PSIZE, u'Размер', 18.0), (P_ANG, u'Угол', 14.0), (P_QTY, u'Кол., шт.', 18.0)])
        made.append((ve, we))
        out.append(u'«%s» — создана' % en)
    if len(made) != 2:
        return out
    # на лист: рядом друг с другом под ведомостью оборудования (или участков), под ними примечание
    try:
        doc.Regenerate()
        anchor = None
        for pref in (EQ_PREFIX, SCHED_PREFIX):
            v = have.get(pref + sysname)
            if v is None:
                continue
            for si in FilteredElementCollector(doc).OfClass(ScheduleSheetInstance):
                if si.ScheduleId == v.Id:
                    anchor = si
                    break
            if anchor is not None:
                break
        if anchor is None:
            return out
        sheet = doc.GetElement(anchor.OwnerViewId)
        bb = anchor.get_BoundingBox(sheet)
        x0, y0 = anchor.Point.X, bb.Min.Y - 10.0 / FT_MM
        gap = 8.0 / FT_MM
        x, low = x0, y0
        for vs, wmm in made:
            si = ScheduleSheetInstance.Create(doc, sheet.Id, vs.Id, XYZ(x, y0, 0))
            doc.Regenerate()
            b2 = si.get_BoundingBox(sheet)
            low = min(low, b2.Min.Y)
            x = b2.Max.X + gap
        eev = any(is_eev(e) for e in FilteredElementCollector(doc, view.Id).OfCategory(
            BuiltInCategory.OST_MechanicalEquipment).WhereElementIsNotElementType())
        txt = bend_note(dia, eev)
        if txt:
            lv, st = ensure_note_legend(doc, sysname, txt, x - gap - x0, eev)
            place_legend(doc, sheet, lv, x0, low - 5.0 / FT_MM)
            out.append(u'примечание — легенда «%s» (%s)' % (lv.Name, st))
        out.append(u'поставлены на лист %s' % sheet.SheetNumber)
    except Exception as ex:
        out.append(u'на лист не поставлены: %s' % ex)
    return out


# ---------------------------------------------------------------- примечание о гибах — легенда
# db 18.10: примечание — в легенде, не текстом на листе: тогда его видит «Опись хуйни».
NOTE_PREFIX = u'db_Примечание_'


def _legends(doc):
    from Autodesk.Revit.DB import View, ViewType
    return [v for v in FilteredElementCollector(doc).OfClass(View) if v.ViewType == ViewType.Legend and not v.IsTemplate]


def _note_nums(text):
    """('22,2', '25,4') из текста примечания (до … включительно / … и более)."""
    import re
    a = re.search(u'до ([0-9]+(?:,[0-9]+)?) мм', text, re.U)
    b = re.search(u'([0-9]+(?:,[0-9]+)?) мм и более', text, re.U)
    return (a.group(1) if a else None, b.group(1) if b else None)


def ensure_note_legend(doc, sysname, txt, width_ft, eev=False):
    """Легенда «db_Примечание_<система>» с примечанием о гибах. Образец — другая легенда
    db_Примечание_* (оформление инженера: копия, в тексте меняются только диаметры); нет образца —
    пустая копия любой легенды и текст ADSK_Основной текст_2.5. -> (легенда, 'есть'|'создана')."""
    from Autodesk.Revit.DB import (TextNote, TextNoteOptions, TextNoteType, ElementTypeGroup, ViewDuplicateOption, XYZ)
    legs = _legends(doc)
    name = NOTE_PREFIX + sysname
    for v in legs:
        if v.Name == name:
            return v, u'есть'
    smp = [v for v in legs if v.Name.startswith(NOTE_PREFIX)]

    def has_eev(v):
        return any(u'ЭРВ' in tn.Text for tn in FilteredElementCollector(doc, v.Id).OfClass(TextNote))
    smp = [v for v in smp if has_eev(v) == eev]       # образец с пунктом про ЭРВ — только для систем с ЭРВ
    if smp:
        lv = doc.GetElement(smp[0].Duplicate(ViewDuplicateOption.WithDetailing))
        lv.Name = name
        new = _note_nums(txt)
        for tn in FilteredElementCollector(doc, lv.Id).OfClass(TextNote):
            old = _note_nums(tn.Text)
            ft = tn.GetFormattedText()
            ok = True
            for o, n in zip(old, new):
                if o is None or n is None:
                    ok = ok and (o is None) == (n is None)
                    continue
                if o == n:
                    continue
                r = ft.Find(o + u' мм', 0, True, False)
                if r is None or r.Length == 0:
                    ok = False
                    continue
                ft.SetPlainText(r, n + u' мм')
            if ok:
                tn.SetFormattedText(ft)
            else:
                tn.Text = txt
        return lv, u'создана по образцу «%s»' % smp[0].Name
    lv = doc.GetElement(legs[0].Duplicate(ViewDuplicateOption.Duplicate))
    lv.Name = name
    tt = [t for t in FilteredElementCollector(doc).OfClass(TextNoteType) if Element.Name.GetValue(t) == NOTE_TYPE]
    tid = tt[0].Id if tt else doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType)
    TextNote.Create(doc, lv.Id, XYZ(0, 0, 0), width_ft * lv.Scale, txt, TextNoteOptions(tid))
    return lv, u'создана'


def place_legend(doc, sheet, lv, x_left, y_top):
    """Легенда на лист: левый верхний угол рамки — (x_left, y_top); вид видового экрана — «Без названия»."""
    from Autodesk.Revit.DB import Viewport, XYZ
    for vp in FilteredElementCollector(doc, sheet.Id).OfClass(Viewport):
        if vp.ViewId == lv.Id:
            return vp
    vp = Viewport.Create(doc, sheet.Id, lv.Id, XYZ(x_left, y_top, 0))
    try:
        for tid in vp.GetValidTypes():
            if Element.Name.GetValue(doc.GetElement(tid)) == u'Без названия':
                vp.ChangeTypeId(tid)
                break
    except Exception:
        pass
    doc.Regenerate()
    o = vp.GetBoxOutline()
    vp.SetBoxCenter(vp.GetBoxCenter() + XYZ(x_left - o.MinimumPoint.X, y_top - o.MaximumPoint.Y, 0))
    return vp



# ================================================================== марки номеров на схеме
TAG_FAMILY = u'db_М_Участок'


def _tag_symbol(doc):
    from Autodesk.Revit.DB import FamilySymbol
    for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            if s.FamilyName in (TAG_FAMILY, u'db_M_Участок'):
                return s
        except Exception:
            pass
    return None


def _view_tags(doc, view):
    """Все марки вида. НЕ FilteredElementCollector(doc, view.Id): на взрыв-схеме он не видит
    часть марок (С.Х.ККБ1, 18.10: из 69 марок видно 33 — марки «только где нет»
    поставились второй раз). Берём по виду-владельцу."""
    from Autodesk.Revit.DB import IndependentTag
    vid = _iv(view.Id)
    return [t for t in FilteredElementCollector(doc).OfClass(IndependentTag) if _iv(t.OwnerViewId) == vid]


def _tagged_numbers(doc, view, sym_family):
    """Номера участков, у которых на виде уже есть марка номера."""
    from Autodesk.Revit.DB import IndependentTag
    out = set()
    for t in _view_tags(doc, view):
        if _fam_of_tag(doc, t) != sym_family:
            continue
        for e in t.GetTaggedLocalElements():
            p = e.LookupParameter(P_NUM)
            if p is not None and p.AsString():
                out.add(p.AsString())
    return out


# видимая выноска марки номера, мм листа (правки db на С.Х.ККБ1, 18.10: медиана 5,7)
NUM_LEAD = (5.5, 4.0, 7.0, 9.0, 12.0, 16.0)


def _straight_place(ctx, s, rel, ds):
    """Прямая выноска (db, С.Х.ККБ1: 25 из 35 марок): точка привязки — вдоль ЛЮБОЙ трубы
    участка, марка строго над/под ней (у вертикальной на листе трубы — сбоку), видимая
    выноска ~5,5 мм, тесно — длиннее. -> (стоимость, труба, точка 3D, (hu, hv), box, lead)."""
    r0, q0, r1, q1 = rel
    cu, cv = (r0 + r1) / 2.0, (q0 + q1) / 2.0
    pipes = sorted([e for e in s['els'] if isinstance(e, MEPCurve)],
                   key=lambda e: -e.Location.Curve.Length)[:6]
    best = None
    for rank, p in enumerate(pipes):
        c = p.Location.Curve
        d = ctx.dp(p)
        A, B = ctx.uv(c.GetEndPoint(0) + d), ctx.uv(c.GetEndPoint(1) + d)
        Lsh = ((B[0] - A[0]) ** 2 + (B[1] - A[1]) ** 2) ** 0.5
        if Lsh < 3.0:
            continue
        vert = abs(B[0] - A[0]) < 0.3 * abs(B[1] - A[1])
        for t in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.12, 0.88):
            pt = c.Evaluate(t, True)
            if not ctx.visible_pt(pt):
                continue
            a3 = pt + d
            au, av = ctx.uv(a3)
            for L in NUM_LEAD:
                if vert:
                    opts = [(au + L - r0, av - cv, (au + L, av)), (au - L - r1, av - cv, (au - L, av))]
                else:
                    opts = [(au - cu, av + L - q0, (au, av + L)), (au - cu, av - L - q1, (au, av - L))]
                for hu, hv, tip in opts:
                    box = (hu + r0, hv + q0, hu + r1, hv + q1)
                    cst = ctx.occ.box_cost(box)
                    if cst >= ds.HARD:
                        continue
                    lead = [(au, av), tip]
                    cst += ctx.occ.lead_cost(lead) + (L - NUM_LEAD[0]) * 1.5 + abs(t - 0.5) * 4.0 + rank * 2.0
                    if best is None or cst < best[0]:
                        best = (cst, p, a3, (hu, hv), box, lead)
    return best


def place_number_tags(doc, view, segs, ds, only_missing=False):
    """Красная марка номера на каждом участке (db, 17–18.10): прямая выноска к любой трубе
    участка (см. _straight_place); не вышло — угловая, как у марок труб (dress_scheme._single).
    only_missing — только участкам без марки, остальные марки не трогать; иначе старые
    марки db_М_Участок на виде заменяются. ds — модуль dress_scheme.
    Возвращает (поставлено, не нашлось места)."""
    from Autodesk.Revit.DB import (IndependentTag, LeaderEndCondition, Reference, TagOrientation,
                                   Transaction)
    import stack_tags
    import hui_plane
    sym = _tag_symbol(doc)
    if sym is None:
        return 0, [u'нет семейства марки «%s»' % TAG_FAMILY]
    have = _tagged_numbers(doc, view, sym.FamilyName) if only_missing else set()
    todo = [s for s in segs if s['n'] not in have]
    if not todo:
        return 0, []
    t = Transaction(doc, u'Участки: марки номеров (подготовка)')
    t.Start()
    if not only_missing:
        old = [x.Id for x in _view_tags(doc, view)
               if _fam_of_tag(doc, x) == sym.FamilyName]
        for i in old:
            doc.Delete(i)
    if not sym.IsActive:
        sym.Activate()
    t.Commit()
    ctx = ds.Ctx(doc, view, None, True)
    ctx.angled = True
    ctx.tV, ctx.tH = ds.PIPE_LEAD.get(u'vrf', (10.0, 7.5))
    ctx.occ.band_w = 25.0
    for p in FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_PipeCurves) \
            .WhereElementIsNotElementType():
        try:
            c = p.Location.Curve
        except Exception:
            continue
        d = ctx.dp(p)
        r = max(0.6, p.Diameter * FT_MM / view.Scale / 2.0 + 0.3)
        ctx.occ.add_band(ctx.uv(c.GetEndPoint(0) + d), ctx.uv(c.GetEndPoint(1) + d), r)
    ds._eq_boxes(ctx)
    try:
        ds._path_bands(ctx)
    except Exception:
        pass
    hui_plane.Transaction = Transaction
    ptok = hui_plane.push(doc, view, view.ViewDirection, view.Origin, u'Участки: плоскость')
    made, miss, n_ok = [], [], 0
    try:
        t = Transaction(doc, u'Участки: марки номеров')
        t.Start()
        for s in todo:
            pipes = [e for e in s['els'] if isinstance(e, MEPCurve)]
            if not pipes:
                miss.append(s['n'])
                continue
            host = max(pipes, key=lambda e: e.Location.Curve.Length)
            anchors = ds.anchors_of(ctx, host)
            if not anchors:
                miss.append(s['n'])
                continue
            tg = IndependentTag.Create(doc, sym.Id, view.Id, Reference(host), True,
                                       TagOrientation.Horizontal, anchors[0])
            tg.LeaderEndCondition = LeaderEndCondition.Free
            made.append((tg, host, anchors, s['n'], s))
        t.Commit()
        if made:
            rels = stack_tags.measure_exact(doc, view, [m[0] for m in made], ctx.R, ctx.U, ctx.mm)
            t = Transaction(doc, u'Участки: раскладка марок')
            t.Start()
            for item, rel in zip(made, rels):
                tg, host, anchors, num, s = item
                if rel is None:
                    rel = (-3 * ctx.mm, -2.5 * ctx.mm, 3 * ctx.mm, 2.5 * ctx.mm)
                rel_mm = tuple(x / ctx.mm for x in rel)
                best = _straight_place(ctx, s, rel_mm, ds)
                if best is not None and best[0] < 2000.0:
                    c_, p, a3, huv, box, lead = best
                    if _iv(p.Id) != _iv(host.Id):
                        nt = IndependentTag.Create(doc, sym.Id, view.Id, Reference(p), True,
                                                   TagOrientation.Horizontal, a3)
                        doc.Delete(tg.Id)
                        tg = nt
                        tg.LeaderEndCondition = LeaderEndCondition.Free
                    ds._set_tag(ctx, tg, a3, huv)
                    ctx.occ.add_box(box)
                    ctx.occ.add_seg(lead[0], lead[1])
                    n_ok += 1
                elif ds._single(ctx, (tg, host, anchors, num), rel_mm):
                    n_ok += 1
                else:
                    miss.append(num)
                    doc.Delete(tg.Id)
            t.Commit()
    finally:
        hui_plane.pop(doc, view, ptok)
    return n_ok, miss


# ================================================================== состояние вида
def _recorded(doc, view):
    """Записанные номера участков на виде: {номер: [элементы]} (трубы и фитинги жидкостной
    линии) и счёт труб жидкостной линии без номера. Модель не меняется."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementMulticategoryFilter
    cats = List[BuiltInCategory]([BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_PipeFitting])
    by_num, nonum = {}, 0
    for e in FilteredElementCollector(doc, view.Id).WherePasses(ElementMulticategoryFilter(cats)) \
            .WhereElementIsNotElementType():
        if not is_liquid(e):
            continue
        p = e.LookupParameter(P_NUM)
        v = p.AsString() if p is not None else None
        if v:
            by_num.setdefault(v, []).append(e)
        elif isinstance(e, MEPCurve):
            nonum += 1
    return by_num, nonum


def view_state(doc, view):
    """Что уже есть на виде (для меню кнопки, db 18.10): участков с номером, из них с маркой
    номера, труб жидкостной линии без номера."""
    by_num, nonum = _recorded(doc, view)
    sym = _tag_symbol(doc)
    fam = sym.FamilyName if sym is not None else TAG_FAMILY
    tagged = _tagged_numbers(doc, view, fam) if by_num else set()
    return {'nums': len(by_num), 'tagged': len([n for n in by_num if n in tagged]), 'nonum': nonum}


def recorded_segs(doc, view):
    """Участки из уже записанных номеров — для расстановки марок без пересчёта
    (place_number_tags берёт у участка только 'n' и 'els')."""
    by_num, _ = _recorded(doc, view)
    return [{'n': n, 'els': els} for n, els in by_num.items()]


# ================================================================== проверка без пересчёта
def _ref_kind(e):
    """('Liquid'|'Gas'|None, марка без суффикса) по имени типа разветвителя/муфты LG."""
    tn = _tname(e)
    for suf in (u'_Liquid', u'_Gas'):
        if tn.endswith(suf):
            return suf[1:], tn[:-len(suf)]
    return None, tn


def refnet_pairs(doc, view, stats=None):
    """Комплектность разветвителей LG (db, 18.10): у каждого жидкостного — газовый той же марки рядом;
    тип по линии (на жидкостной — _Liquid, на газовой — _Gas). view=None — весь проект.
    stats (dict) — заполняется: 'liq', 'gas', 'pairs', 'by_mark' {марка: комплектов}.
    -> [{'title', 'ids', 'kind'}]."""
    liq, gas = [], []
    col = FilteredElementCollector(doc, view.Id) if view is not None else FilteredElementCollector(doc)
    for e in col.OfCategory(BuiltInCategory.OST_PipeAccessory).WhereElementIsNotElementType():
        if u'LG' not in _fam(e):
            continue
        kind, base = _ref_kind(e)
        if is_liquid(e):
            liq.append((e, kind, base))
        elif is_gas(e):
            gas.append((e, kind, base))
    out = []
    for e, kind, base in liq:
        if kind == u'Gas':
            out.append({'kind': 'pair', 'ids': [e.Id], 'title': u'%s — газовый тип на жидкостной линии' % _tname(e)})
    for e, kind, base in gas:
        if kind == u'Liquid':
            out.append({'kind': 'pair', 'ids': [e.Id], 'title': u'%s — жидкостный тип на газовой линии' % _tname(e)})
    # пары по расстоянию: ближайшие сначала, каждый — в одну пару
    cand = []
    for i, (a, ka, ba) in enumerate(liq):
        pa = _pos(a)
        for j, (b, kb, bb) in enumerate(gas):
            d = pa.DistanceTo(_pos(b)) * FT_MM
            if d < 1500.0:
                cand.append((d, i, j))
    cand.sort()
    used_l, used_g, pairs = set(), set(), []
    for d, i, j in cand:
        if i in used_l or j in used_g:
            continue
        used_l.add(i)
        used_g.add(j)
        pairs.append((i, j))
    for i, j in pairs:
        a, ka, ba = liq[i]
        b, kb, bb = gas[j]
        if ba != bb:
            out.append({'kind': 'pair', 'ids': [a.Id, b.Id],
                        'title': u'Разветвители не одной марки: жидк. %s, газ. %s' % (_tname(a), _tname(b))})
    if stats is not None:
        stats['liq'], stats['gas'], stats['pairs'] = len(liq), len(gas), len(pairs)
        bm = {}
        for i, j in pairs:
            m = liq[i][2] if liq[i][2] == gas[j][2] else u'%s / %s' % (liq[i][2], gas[j][2])
            bm[m] = bm.get(m, 0) + 1
        stats['by_mark'] = bm
    for i, (a, ka, ba) in enumerate(liq):
        if i not in used_l:
            out.append({'kind': 'pair', 'ids': [a.Id], 'title': u'%s — нет газовой пары рядом' % _tname(a)})
    for j, (b, kb, bb) in enumerate(gas):
        if j not in used_g:
            out.append({'kind': 'pair', 'ids': [b.Id], 'title': u'%s — нет жидкостной пары рядом' % _tname(b)})
    return out


def check(doc, view):
    """Состояние схемы по уже записанным параметрам (быстро, модель не меняется):
    список пунктов {'title', 'ids', 'kind'}: участки без марки номера, элементы
    жидкостной линии без номера, элементы чужой системы."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementMulticategoryFilter
    sym = _tag_symbol(doc)
    fam = sym.FamilyName if sym is not None else TAG_FAMILY
    cats = List[BuiltInCategory]([BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_PipeFitting])
    by_num, nonum, sysc = {}, [], {}
    for e in FilteredElementCollector(doc, view.Id).WherePasses(ElementMulticategoryFilter(cats)) \
            .WhereElementIsNotElementType():
        if not is_liquid(e):
            continue
        sn = _sysname(e)
        sysc.setdefault(sn, []).append(e.Id)
        p = e.LookupParameter(P_NUM)
        v = p.AsString() if p is not None else None
        if v:
            by_num.setdefault(v, []).append(e)
        elif isinstance(e, MEPCurve):
            nonum.append(e.Id)
    tagged = _tagged_numbers(doc, view, fam)

    def key(n):
        try:
            return [int(x) for x in n.split(u'.')]
        except Exception:
            return [9999]
    out = []
    for n in sorted(by_num, key=key):
        if n in tagged:
            continue
        els = by_num[n]
        L = 0.0
        sz = u''
        for e in els:
            if isinstance(e, MEPCurve):
                L += e.Location.Curve.Length * FT_MM
            if not sz:
                q = e.LookupParameter(P_SIZE)
                sz = (q.AsString() or u'') if q is not None else u''
        out.append({'kind': 'notag', 'n': n, 'ids': [e.Id for e in els],
                    'title': u'Участок %s — нет марки номера  ·  %s  ·  %.1f м' % (n, sz, L / 1000.0)})
    if nonum:
        out.append({'kind': 'nonum', 'ids': nonum,
                    'title': u'Трубы жидкостной линии без номера участка: %d шт. (появились после '
                             u'расчёта — запустите «Пересчитать»)' % len(nonum)})
    if len(sysc) > 1:
        main = max(sysc.items(), key=lambda kv: len(kv[1]))[0]
        for sn, ids in sysc.items():
            if sn != main and u',' not in sn:
                out.append({'kind': 'sys', 'ids': ids,
                            'title': u'Система «%s» вместо «%s»: %d эл. — эти участки не попадут в '
                                     u'спецификацию' % (sn, main, len(ids))})
    try:
        out.extend(refnet_pairs(doc, view))
    except Exception:
        pass
    return out, len(by_num), len(tagged)


def _fam_of_tag(doc, tag):
    try:
        return doc.GetElement(tag.GetTypeId()).FamilyName
    except Exception:
        return u''
