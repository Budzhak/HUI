# -*- coding: utf-8 -*-
"""
Колонка марок с угловыми выносками (XYZ -> По красоте).

Выделенные марки выстраиваются друг под другом с одинаковым шагом.
Выноска каждой марки - ломаная под прямым углом: от точки на элементе
вертикально до строки своей марки, затем горизонтально до марки.
Порядок строк подбирается так, чтобы выноски не пересекались:
строка, ближняя к элементам, достаётся элементу, ближнему к колонке.

Режим 0 (по умолчанию): сторона выбирается по точке вставки.
  Указали точку правее элементов - колонка справа, марки выровнены по левому
  краю; левее - колонка слева, выровнены по правому краю. Точка - верх колонки,
  строки идут вниз; если колонка выше элементов - ближний к ней элемент
  получает нижнюю строку, если ниже - верхнюю (выноски не пересекаются).

Старые варианты (вызов с variant=1..4):
  1  вниз и налево, марки выровнены по правому краю
  2  вверх и налево, по правому краю
  3  вверх и направо, по левому краю
  4  вниз и направо, по левому краю

Вызов: stack_tags.run(doc, uidoc, tags, variant, spacing_mm=1.0, anchor=None)
  spacing_mm - зазор между рамками марок, мм листа (от масштаба не зависит);
  anchor     - точка вставки (XYZ): край колонки (правый для 1-2, левый для 3-4)
               и центр первой строки - ближней к элементам. None - автоматически.
"""
from Autodesk.Revit.DB import *

FT_MM = 304.8

VARIANTS = [
    (1, u"1. Вниз и налево (по правому краю)", -1, -1),
    (2, u"2. Вверх и налево (по правому краю)", -1, 1),
    (3, u"3. Вверх и направо (по левому краю)", 1, 1),
    (4, u"4. Вниз и направо (по левому краю)", 1, -1),
]


class _Lb(object):
    pass


def _first_ref(tag):
    for r in tag.GetTaggedReferences():
        return r
    return None


def _solve4(rows, rhs):
    """Метод наименьших квадратов для 4 неизвестных (нормальные уравнения, Гаусс)."""
    n = 4
    N = [[0.0] * (n + 1) for _ in range(n)]
    for r, b in zip(rows, rhs):
        for i in range(n):
            for j in range(n):
                N[i][j] += r[i] * r[j]
            N[i][n] += r[i] * b
    for c in range(n):
        p = max(range(c, n), key=lambda k: abs(N[k][c]))
        N[c], N[p] = N[p], N[c]
        if abs(N[c][c]) < 1e-12:
            return None
        for r in range(n):
            if r != c:
                f = N[r][c] / N[c][c]
                for j in range(n + 1):
                    N[r][j] -= f * N[c][j]
    return [N[i][n] / N[i][i] for i in range(n)]


def measure_exact(doc, view, tags, R, U, mm):
    """Точный габарит головы марки относительно TagHeadPosition в осях вида.

    Габарит марки (get_BoundingBox) у Revit захватывает точку на элементе, а на
    3D-виде это ещё и коробка по осям модели. Поэтому марку (без выноски)
    по очереди уводим далеко в +D и -D (D = вправо+вверх по виду) и по каждой
    оси модели берём ту сторону коробки, где точки элемента точно нет. Из этих
    6 чисел МНК находит центр (du, dv) и полуширину/полувысоту прямоугольника.
    Марки одного типа с одинаковым текстом меряются один раз. Транзакция откатывается."""
    D = R + U
    far = 400.0 * mm
    Rv, Uv, Dv = (R.X, R.Y, R.Z), (U.X, U.Y, U.Z), (D.X, D.Y, D.Z)
    cache, out = {}, []
    t = Transaction(doc, u"Замер марок")
    t.Start()
    try:
        for tag in tags:
            key = (tag.GetTypeId().ToString(), tag.TagText or u'')
            if key not in cache:
                rel = None
                try:
                    h = tag.TagHeadPosition
                    if tag.HasLeader:
                        tag.HasLeader = False
                    rows, rhs = [], []
                    for sg in (1.0, -1.0):
                        tag.TagHeadPosition = h + D * (sg * far)
                        doc.Regenerate()
                        hh = tag.TagHeadPosition
                        bb = tag.get_BoundingBox(view)
                        mn = (bb.Min.X, bb.Min.Y, bb.Min.Z)
                        mx = (bb.Max.X, bb.Max.Y, bb.Max.Z)
                        H = (hh.X, hh.Y, hh.Z)
                        for i in range(3):
                            if abs(Dv[i]) < 1e-6:
                                continue
                            use_max = sg * Dv[i] > 0
                            k = 1.0 if use_max else -1.0
                            rows.append((Rv[i], Uv[i], k * abs(Rv[i]), k * abs(Uv[i])))
                            rhs.append((mx[i] if use_max else mn[i]) - H[i])
                    x = _solve4(rows, rhs)
                    if x and 0 < 2 * x[2] < 150 * mm and 0 < 2 * x[3] < 30 * mm:
                        du, dv, a, b = x
                        rel = (du - a, dv - b, du + a, dv + b)
                except Exception:
                    rel = None
                cache[key] = rel
            out.append(cache[key])
    finally:
        t.RollBack()
    return out


def run(doc, uidoc, tags, variant, spacing_mm=1.0, gap_mm=3.0, anchor=None, interactive=False):
    import smart_tags
    del LAST_BAD[:]
    view = doc.ActiveView
    frame = smart_tags.ViewFrame(view)
    R, U, mm = frame.R, frame.U, frame.mm
    sx, sy = None, None
    for v in VARIANTS:
        if v[0] == variant:
            sx, sy = v[2], v[3]
    auto = (variant == 0)
    if auto:
        sx, sy = 1, -1                     # для замера; сторона уточняется по точке
    if sx is None:
        return u"Неизвестный вариант"

    tg = TransactionGroup(doc, u"Колонка марок с угловыми выносками")
    tg.Start()
    try:
        # 1. Свободные концы выносок в текущих точках на элементах
        t = Transaction(doc, u"Свободные концы выносок")
        t.Start()
        items = []
        for tag in tags:
            ref = _first_ref(tag)
            if ref is None:
                continue
            if not tag.HasLeader:
                tag.HasLeader = True
            if tag.LeaderEndCondition != LeaderEndCondition.Free:
                tag.LeaderEndCondition = LeaderEndCondition.Free
            doc.Regenerate()
            p = tag.GetLeaderEnd(ref)
            items.append((tag, ref, p))
        t.Commit()
        if not items:
            tg.RollBack()
            return u"Нет марок с выносками."

        # 2. Размеры голов марок (замер как в "Умных выносках")
        rels = measure_exact(doc, view, [it[0] for it in items], R, U, mm)
        labels = []
        for (tag, ref, p), rel in zip(items, rels):
            lb = _Lb()
            lb.rel = rel
            labels.append(lb)

        # 3. Раскладка
        rows = []
        for (tag, ref, p), lb in zip(items, labels):
            pu, pv = p.DotProduct(R), p.DotProduct(U)
            hu, hv = tag.TagHeadPosition.DotProduct(R), tag.TagHeadPosition.DotProduct(U)
            rows.append({'tag': tag, 'ref': ref, 'p': p, 'pu': pu, 'pv': pv,
                         'rel': lb.rel, 'hu': hu, 'hv': hv})
        gap = gap_mm * mm
        # Замер иногда захватывает точку на элементе (габарит в сотни мм листа) -
        # из-за этого шаг становился огромным и марки разлетались. Отбрасываем
        # нереальные габариты и берём типичный (медиану) по нормальным маркам.
        lim = 60.0 * mm
        sane = [r['rel'] for r in rows if r['rel'] and
                0 < r['rel'][2] - r['rel'][0] < 2 * lim and 0 < r['rel'][3] - r['rel'][1] < lim / 4
                and max(abs(x) for x in r['rel']) < 2 * lim]
        if sane:
            hs = sorted(q[3] - q[1] for q in sane)
            h = hs[len(hs) // 2]
            ws = sorted(q[2] - q[0] for q in sane)
            w = ws[len(ws) // 2]
        else:
            h, w = 5.0 * mm, 16.0 * mm
        for r in rows:
            q = r['rel']
            if not q or q not in sane:
                r['rel'] = (-w / 2.0, -h / 2.0, w / 2.0, h / 2.0)
        step = h + spacing_mm * mm
        # строка, ближняя к элементам, - элементу, ближнему к колонке
        rows.sort(key=lambda r: (-sx * r['pu'], -sy * r['pv']))

        # раскладка по умолчанию (без точки вставки)
        # край колонки (правый для 1-2, левый для 3-4)
        if sx < 0:
            edge = min(r['pu'] for r in rows) - gap
            cur = sorted(r['hu'] + r['rel'][2] for r in rows)[len(rows) // 2]
            edge = min(edge, cur)
        else:
            edge = max(r['pu'] for r in rows) + gap
            cur = sorted(r['hu'] + r['rel'][0] for r in rows)[len(rows) // 2]
            edge = max(edge, cur)
        # первая строка (ближняя к элементам): каждая строка - за своей точкой
        if sy < 0:
            v0 = min(r['pv'] - gap + k * step for k, r in enumerate(rows))
            cur_near = max(r['hv'] for r in rows)
            v0 = min(v0, cur_near)
        else:
            v0 = max(r['pv'] + gap - k * step for k, r in enumerate(rows))
            cur_near = min(r['hv'] for r in rows)
            v0 = max(v0, cur_near)

        def apply(edge, v0):
            placed = []
            t = Transaction(doc, u"Колонка марок")
            t.Start()
            for k, r in enumerate(rows):
                r0, q0, r1, q1 = r['rel']
                vrow = v0 + sy * k * step
                hu = (edge - r1) if sx < 0 else (edge - r0)
                hv = vrow - (q0 + q1) / 2.0
                p = r['p']
                head = p + R * (hu - r['pu']) + U * (hv - r['pv'])
                elbow = p + U * (vrow - r['pv'])
                tag = r['tag']
                tag.TagHeadPosition = head
                tag.SetLeaderEnd(r['ref'], p)
                tag.SetLeaderElbow(r['ref'], elbow)
                placed.append((tag, r['ref'], p, elbow, head))
            t.Commit()
            _verify(doc, view, placed, R, U, mm)

        def apply_auto(eu, top_v):
            """Колонка по точке: сторона по положению точки, верх колонки в точке."""
            us = sorted(r['pu'] for r in rows)
            vs = sorted(r['pv'] for r in rows)
            mu, mv = us[len(us) // 2], vs[len(vs) // 2]
            ax = 1 if eu >= mu else -1         # колонка справа -> по левому краю
            n = len(rows)
            mid_v = top_v - (n - 1) * step / 2.0
            ay = 1 if mid_v >= mv else -1      # колонка выше элементов?
            order = sorted(rows, key=lambda r: (-ax * r['pu'], -ay * r['pv']))
            place = [(r, (n - 1 - k) if ay > 0 else k) for k, r in enumerate(order)]
            # ближний к колонке - ближе к элементам. Если точки и выше, и ниже колонки
            # (рефнет сверху, труба снизу), так выноски «подныривают» друг под друга —
            # тогда подбираем порядок строк без пересечений (_order_rows).
            place = _order_rows(place, eu, top_v, step, ax)
            placed = []
            t = Transaction(doc, u"Колонка марок")
            t.Start()
            for r, row in place:
                vrow = top_v - row * step
                r0, q0, r1, q1 = r['rel']
                hu = (eu - r0) if ax > 0 else (eu - r1)
                hv = vrow - (q0 + q1) / 2.0
                p = r['p']
                head = p + R * (hu - r['pu']) + U * (hv - r['pv'])
                elbow = p + U * (vrow - r['pv'])
                tag = r['tag']
                tag.TagHeadPosition = head
                tag.SetLeaderEnd(r['ref'], p)
                tag.SetLeaderElbow(r['ref'], elbow)
                placed.append((tag, r['ref'], p, elbow, head))
            t.Commit()
            _verify(doc, view, placed, R, U, mm)

        if auto:
            # без точки: колонка с той стороны, где марки стоят сейчас, верх - по верхней марке
            hus = sorted(r['hu'] for r in rows)
            cur_u = hus[len(hus) // 2]
            us = sorted(r['pu'] for r in rows)
            if cur_u >= us[len(us) // 2]:
                def_u = max(max(r['pu'] for r in rows) + gap, cur_u)
            else:
                def_u = min(min(r['pu'] for r in rows) - gap, cur_u)
            def_v = max(r['hv'] for r in rows)
            apply = lambda e, v: apply_auto(e, v)    # noqa: E731
            edge, v0 = def_u, def_v

        if interactive:
            # указываете точку - колонка сразу переставляется,
            # можно указывать новые точки, пока результат не устроит; Esc - готово.
            # Указание точки в Revit недоступно при открытой группе транзакций,
            # поэтому подготовка фиксируется, а каждая перестановка - свой шаг отмены.
            tg.Assimilate()
            tg = None
            ptok = _prepare_plane(doc, view, [r['tag'] for r in rows])
            try:
                first = True
                while True:
                    pt = _pick(uidoc, first)
                    if pt is None:
                        if first:
                            apply(edge, v0)
                        break
                    apply(pt.DotProduct(R), pt.DotProduct(U))
                    try:
                        uidoc.RefreshActiveView()
                    except Exception:
                        pass
                    first = False
            finally:
                _restore_plane(doc, view, ptok)
        elif anchor is not None:
            apply(anchor.DotProduct(R), anchor.DotProduct(U))
        else:
            apply(edge, v0)
        if tg is not None:
            tg.Assimilate()
    except Exception:
        try:
            if tg is not None and tg.HasStarted() and not tg.HasEnded():
                tg.RollBack()
        except Exception:
            pass
        raise
    return u"Марок в колонке: {}".format(len(rows))


LAST_BAD = []   # марки, которые не удалось выстроить (для сообщения в script.py)


def _off(a, b, R, U, mm):
    d = a - b
    return abs(d.DotProduct(R)) / mm + abs(d.DotProduct(U)) / mm


def _verify(doc, view, placed, R, U, mm, tol=0.05):
    """Проверка после раскладки: конец, излом и голова каждой марки стоят там, где
    заданы (мм листа, в плоскости экрана). Не так — повторная установка в порядке
    конец -> излом -> голова; не помогло — марка в LAST_BAD, запись в журнал плоскости.
    (17.10.2026: на С.Х.VRF1 выноски «ломались» — излом уходил, голова оставалась.)"""
    import xyz_plane
    del LAST_BAD[:]
    bad = []
    for tag, ref, p, elbow, head in placed:
        try:
            if (_off(tag.GetLeaderEnd(ref), p, R, U, mm) > tol or
                    _off(tag.GetLeaderElbow(ref), elbow, R, U, mm) > tol or
                    _off(tag.TagHeadPosition, head, R, U, mm) > tol):
                bad.append((tag, ref, p, elbow, head))
        except Exception:
            bad.append((tag, ref, p, elbow, head))
    if not bad:
        return
    st = xyz_plane.state(view)
    t = Transaction(doc, u"Колонка марок: поправка выносок")
    t.Start()
    try:
        for tag, ref, p, elbow, head in bad:
            tag.SetLeaderEnd(ref, p)
            tag.SetLeaderElbow(ref, elbow)
            tag.TagHeadPosition = head
        t.Commit()
    except Exception:
        t.RollBack()
    still = []
    for tag, ref, p, elbow, head in bad:
        try:
            if (_off(tag.GetLeaderEnd(ref), p, R, U, mm) > tol or
                    _off(tag.GetLeaderElbow(ref), elbow, R, U, mm) > tol or
                    _off(tag.TagHeadPosition, head, R, U, mm) > tol):
                still.append(tag)
        except Exception:
            still.append(tag)
    xyz_plane._log(u'Колонка | вид «{}» | плоскость {} | расхождение у {} марок, после поправки {}: {}'.format(
        view.Name, xyz_plane.NAMES.get(st, st), len(bad), len(still),
        u','.join(str(x[0].Id) for x in bad)))
    for tag in still:
        LAST_BAD.append(tag.Id)


def _prepare_plane(doc, view, tags):
    """На 3D-виде - ВРЕМЕННАЯ рабочая плоскость в плоскости вида (иначе Revit не даст
    указать точку). Возвращает токен для xyz_plane.pop (вернуть прежнюю плоскость)."""
    if not isinstance(view, View3D):
        return None
    import sys
    sys.modules.pop('xyz_plane', None)
    import xyz_plane
    pts = [t.TagHeadPosition for t in tags]
    c = XYZ(sum(p.X for p in pts) / len(pts), sum(p.Y for p in pts) / len(pts),
            sum(p.Z for p in pts) / len(pts))
    try:
        return xyz_plane.push(doc, view, view.ViewDirection, c, u"Рабочая плоскость для колонки марок")
    except Exception:
        return None


def _restore_plane(doc, view, tok):
    if tok is None:
        return
    import xyz_plane
    xyz_plane.pop(doc, view, tok)


def _crossings(lay, eu):
    """lay: [(pu, pv, vrow)]. Сколько раз вертикаль одной выноски (pu: pv -> vrow)
    пересекает полку другой (строка vrow от pu до края колонки eu)."""
    c = 0
    for i, (ui, vi, ri) in enumerate(lay):
        lo, hi = (vi, ri) if vi < ri else (ri, vi)
        for j, (uj, vj, rj) in enumerate(lay):
            if i == j:
                continue
            xl, xr = (uj, eu) if uj < eu else (eu, uj)
            if xl < ui < xr and lo < rj < hi:
                c += 1
    return c


def _order_rows(place, eu, top_v, step, ax):
    """place: [(row_dict, строка)] — прежний порядок. Есть пересечения выносок —
    ищем порядок строк без них (до 7 марок — перебором, больше — делением на
    «точка выше колонки / ниже»), при равенстве — короче вертикали. Без
    пересечений прежний порядок не меняется."""
    def lay_of(pl):
        return [(r['pu'], r['pv'], top_v - row * step) for r, row in pl]

    def score(pl):
        lay = lay_of(pl)
        return (_crossings(lay, eu), sum(abs(v - q) for u, v, q in lay))
    base = score(place)
    if base[0] == 0:
        return place
    n = len(place)
    rs = [r for r, row in place]
    best, best_s = place, base
    if n <= 7:
        import itertools
        for perm in itertools.permutations(range(n)):
            pl = [(rs[i], k) for k, i in enumerate(perm)]
            sc = score(pl)
            if sc < best_s:
                best, best_s = pl, sc
        return best
    # много марок: верхние k по высоте точки — в верхние строки (ближний к колонке —
    # выше), остальные — в нижние (ближний к колонке — ниже); перебор k
    by_v = sorted(rs, key=lambda r: -r['pv'])
    for k in range(n + 1):
        ab = sorted(by_v[:k], key=lambda r: (-ax * r['pu'], r['pv']))
        be = sorted(by_v[k:], key=lambda r: (-ax * r['pu'], -r['pv']))
        pl = [(r, i) for i, r in enumerate(ab)] + [(r, n - 1 - i) for i, r in enumerate(be)]
        sc = score(pl)
        if sc < best_s:
            best, best_s = pl, sc
    # доводка: меняем местами строки двух марок, пока становится лучше
    rows_of = [row for r, row in best]
    rs2 = [r for r, row in best]
    improved = True
    while improved and best_s[0] > 0:
        improved = False
        for i in range(n):
            for j in range(i + 1, n):
                rows_of[i], rows_of[j] = rows_of[j], rows_of[i]
                pl = list(zip(rs2, rows_of))
                sc = score(pl)
                if sc < best_s:
                    best, best_s, improved = pl, sc, True
                else:
                    rows_of[i], rows_of[j] = rows_of[j], rows_of[i]
    return best


def _pick(uidoc, first):
    from Autodesk.Revit.Exceptions import OperationCanceledException
    msg = (u"Колонка марок: укажите верх колонки справа или слева от элементов (Esc - автоматически)" if first
           else u"Колонка марок: новая точка - колонка переставится, сторона по точке (Esc - готово)")
    try:
        return uidoc.Selection.PickPoint(msg)
    except OperationCanceledException:
        return None


def pick_anchor(doc, uidoc, tags):
    """Точка вставки колонки (Esc - None). На 3D-виде задаётся рабочая плоскость
    в плоскости вида, иначе Revit не даст указать точку."""
    from Autodesk.Revit.Exceptions import OperationCanceledException
    view = doc.ActiveView
    ptok = _prepare_plane(doc, view, tags)
    try:
        return uidoc.Selection.PickPoint(
            u"Колонка марок: укажите край колонки и первую строку (Esc - автоматически)")
    except OperationCanceledException:
        return None
    finally:
        _restore_plane(doc, view, ptok)
