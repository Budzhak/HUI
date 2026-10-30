# -*- coding: utf-8 -*-
"""XYZ · «PDF вектор» — доводка PDF после экспорта из Revit (обычный Python 3, не pyRevit).

Запускается кнопкой «PDF вектор» в фоне:   py xyz_pdfslim.py <job.json>
job.json: {"raw": [пути PDF по листам по порядку], "out": итоговый PDF,
           "slim": true/false, "combine": true/false, "tol_mm": 0.02, "speck_mm": 0.05,
           "title": "...", "keep_raw": false}

Что делает с каждым листом (без видимых изменений):
  1) одинаковые штрихи, нарисованные повторно, — остаётся последний;
  2) штрихи и заливки, полностью закрытые заливками поверх, — удаляются;
  3) заливки-«пылинки» меньше speck_mm — удаляются;
  4) штрихи склеиваются в полилинии, точки на прямой (в пределах tol_mm) убираются;
  5) координаты — с точностью 0,01 pt (0,0035 мм).
Текст ведомостей (job "text": {сырой PDF: [номера элементов-ведомостей]}): Revit раскладывает его по
«экранным» ширинам букв, а в PDF кладёт настоящие — строка на 3–5 % шире и вылезает за рамку ячейки.
Каждая строка ведомости сжимается к своему началу в text_fx раз (0,96; db 19.10.2026).
Потом листы склеиваются в один файл, одинаковые картинки (логотип в штампе) хранятся один раз.
Нужно: pip install pikepdf shapely numpy
"""
import sys, os, json, math, time, hashlib, traceback

PT = 72/25.4
PATH = ('m', 'l', 'c', 'v', 'y', 're', 'h')
PAINT = ('S', 'f', 'f*', 'F', 'B', 'B*', 'b', 'b*', 's', 'n')


def mul(a, b):
    return [a[0]*b[0]+a[1]*b[2], a[0]*b[1]+a[1]*b[3], a[2]*b[0]+a[3]*b[2], a[2]*b[1]+a[3]*b[3],
            a[4]*b[0]+a[5]*b[2]+b[4], a[4]*b[1]+a[5]*b[3]+b[5]]


def scl(m):
    return math.sqrt(abs(m[0]*m[3]-m[1]*m[2])) or 1.0


def fmt(v, nd):
    r = round(v, nd)
    if r == int(r):
        return str(int(r))
    return ('%.*f' % (nd, r)).rstrip('0').rstrip('.')


def ndig(m):
    step = 0.01/scl(m)
    return max(0, min(5, int(math.ceil(-math.log10(step))))) if step < 1 else 0


def dp(pts, tol):
    """Дуглас — Пекер: убрать точки, лежащие на прямой с точностью tol."""
    if len(pts) < 3 or tol <= 0:
        return pts
    keep = [False]*len(pts)
    keep[0] = keep[-1] = True
    st = [(0, len(pts)-1)]
    while st:
        a, b = st.pop()
        ax, ay = pts[a]; bx, by = pts[b]; dx, dy = bx-ax, by-ay; L = math.hypot(dx, dy)
        best, bi = -1, -1
        for i in range(a+1, b):
            px, py = pts[i]
            d = abs(dy*(px-ax)-dx*(py-ay))/L if L > 1e-12 else math.hypot(px-ax, py-ay)
            if d > best:
                best, bi = d, i
        if best > tol:
            keep[bi] = True
            st += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def walk(pikepdf, page):
    out = []; ctm = [1, 0, 0, 1, 0, 0]; st = []; lw = 1.0; sc = None; clip = 0; clipn = 0
    path = []; cur = None; cplx = False; pid = 0
    for operands, op in pikepdf.parse_content_stream(page):
        o = str(op)
        if o in PATH:
            a = [float(x) for x in operands]
            if o == 'm':
                cur = [(a[0], a[1])]; path.append(cur)
            elif o == 'l' and cur is not None:
                cur.append((a[0], a[1]))
            elif o == 'h':
                if cur:
                    cur.append(cur[0])
            else:
                cplx = True; path.append((o, a)); cur = None
            continue
        if o in PAINT or o in ('W', 'W*'):
            if o in ('W', 'W*'):
                clipn += 1; clip = clipn
            out.append(('path', pid, path, o, dict(ctm=list(ctm), lw=lw, sc=sc, clip=clip, cplx=cplx)))
            pid += 1; path = []; cur = None; cplx = False
            continue
        if o == 'q':
            st.append((list(ctm), lw, sc, clip))
        elif o == 'Q' and st:
            ctm, lw, sc, clip = st.pop()
        elif o == 'cm':
            ctm = mul([float(x) for x in operands], ctm)
        elif o == 'w':
            lw = float(operands[0])
        elif o == 'RG':
            sc = tuple(round(float(x), 4) for x in operands)
        elif o in ('G', 'K', 'SC', 'SCN', 'CS'):
            sc = (o,) + tuple(str(x) for x in operands)
        elif o in ('gs', 'd', 'J', 'j', 'M'):
            sc = (sc, o, tuple(str(x) for x in operands))     # смена стиля линии — другой ключ
        out.append(('op', operands, op))
    return out


def _area2(pts):
    return sum(pts[i][0]*pts[i+1][1]-pts[i+1][0]*pts[i][1] for i in range(len(pts)-1)) + \
        (pts[-1][0]*pts[0][1]-pts[0][0]*pts[-1][1])


def analyse(items, speck_pt, shapely_ok):
    """Какие подпути убрать: множество (pid, sid). Заливка из нескольких контуров (с отверстиями)
    рассматривается целиком — удаляется только вся сразу."""
    drop = set(); strokes = []; fills = []; order = 0
    for it in items:
        if it[0] != 'path':
            continue
        _, pid, path, paint, s = it
        if s['cplx'] or paint not in ('S', 'f', 'f*'):
            order += 1
            continue
        m = s['ctm']; k = scl(m)
        if paint == 'S':
            for sid, pl in enumerate(path):
                if len(pl) < 2:
                    continue
                d = [(m[0]*x+m[2]*y+m[4], m[1]*x+m[3]*y+m[5]) for x, y in pl]
                strokes.append((order, pid, sid, d, s['lw']*k, s['sc'], s['clip']))
                order += 1
        else:
            rings = []; sids = []
            for sid, pl in enumerate(path):
                if len(pl) >= 3:
                    rings.append([(m[0]*x+m[2]*y+m[4], m[1]*x+m[3]*y+m[5]) for x, y in pl]); sids.append(sid)
            if rings:
                xs = [p[0] for r in rings for p in r]; ys = [p[1] for r in rings for p in r]
                if max(max(xs)-min(xs), max(ys)-min(ys)) < speck_pt:
                    for sid in sids:
                        drop.add((pid, sid))
                else:
                    fills.append((order, pid, sids, rings, paint, s['clip']))
                order += 1
        order += 1
    last = {}
    for o, pid, sid, d, w, c, cl in strokes:
        pts = [tuple(round(v, 2) for v in p) for p in d]
        if len(pts) == 2:
            pts = sorted(pts)
        key = (round(w, 2), c, cl, tuple(pts))
        if key in last:
            drop.add(last[key])
        last[key] = (pid, sid)
    ndup = len(drop)
    if not shapely_ok or not fills:
        return drop, ndup, 0
    from shapely.geometry import Polygon, LineString
    from shapely.validation import make_valid
    from shapely import STRtree
    from shapely.prepared import prep
    from shapely.ops import unary_union

    def geom(rings, paint):
        gs = []
        for r in rings:
            try:
                g = make_valid(Polygon(r))
            except Exception:
                return None
            if not g.is_empty:
                gs.append(g)
        if not gs:
            return None
        if len(gs) == 1:
            return gs[0]
        if paint == 'f':      # ненулевое правило: все контуры в одну сторону — объединение
            signs = set(_area2(r) > 0 for r in rings)
            if len(signs) == 1:
                return unary_union(gs)
        g = gs[0]             # чёт-нечет (или разнонаправленные контуры) — отверстия
        for h in gs[1:]:
            g = g.symmetric_difference(h)
        return g
    polys = []; meta = []; fgeo = {}
    for o, pid, sids, rings, paint, cl in fills:
        try:
            g = geom(rings, paint)
        except Exception:
            g = None
        if g is None or g.is_empty or g.area <= 0:
            continue
        fgeo[pid] = g
        polys.append(g); meta.append((o, cl))
    if not polys:
        return drop, ndup, 0
    tree = STRtree(polys); prepared = {}

    def covered(g, o, cl):
        cand = [j for j in tree.query(g) if meta[j][0] > o and meta[j][1] == cl]
        if not cand:
            return False
        for j in cand:
            p = prepared.get(j)
            if p is None:
                p = prepared[j] = prep(polys[j])
            if p.contains(g):
                return True
        if len(cand) > 12:
            env = g.envelope
            cand = sorted(cand, key=lambda j: -polys[j].intersection(env).area)[:12]
        try:
            return unary_union([polys[j] for j in cand]).contains(g)
        except Exception:
            return False
    nocc = 0
    for o, pid, sid, d, w, c, cl in strokes:
        if (pid, sid) in drop:
            continue
        try:
            g = LineString(d).buffer(max(w, 0.1)/2, cap_style=2)
        except Exception:
            continue
        if covered(g, o, cl):
            drop.add((pid, sid)); nocc += 1
    for o, pid, sids, rings, paint, cl in fills:
        g = fgeo.get(pid)
        if g is None:
            continue
        g = g.buffer(-0.02)
        if g.is_empty:
            continue
        if covered(g, o, cl):
            for sid in sids:
                drop.add((pid, sid))
            nocc += 1
    return drop, ndup, nocc


def emit(pikepdf, items, drop, tol_pt):
    out = []; pend = [None]

    def flush():
        if not pend[0]:
            return
        m, polys = pend[0]; pend[0] = None
        merged = []
        for pl in polys:
            if merged and merged[-1][-1] == pl[0]:
                merged[-1].extend(pl[1:])
            else:
                merged.append(list(pl))
        t = tol_pt/scl(m); d = ndig(m)
        for pl in merged:
            pl = dp(pl, t)
            q = [(round(pl[0][0], d), round(pl[0][1], d))]
            for x, y in pl[1:]:
                r = (round(x, d), round(y, d))
                if r != q[-1]:
                    q.append(r)
            if len(q) < 2:
                continue
            out.append('%s %s m' % (fmt(q[0][0], d), fmt(q[0][1], d)))
            out.extend('%s %s l' % (fmt(x, d), fmt(y, d)) for x, y in q[1:])
        out.append('S')
    for it in items:
        if it[0] == 'op':
            flush()
            out.append(pikepdf.unparse_content_stream([(it[1], it[2])]).decode('latin1'))
            continue
        _, pid, path, paint, s = it
        m = s['ctm']
        if s['cplx'] or paint not in ('S', 'f', 'f*'):
            flush()
            for p in path:
                if isinstance(p, tuple):
                    out.append(' '.join(fmt(v, 5) for v in p[1]) + ' ' + p[0])
                elif p:
                    out.append('%s %s m' % (fmt(p[0][0], 5), fmt(p[0][1], 5)))
                    out.extend('%s %s l' % (fmt(x, 5), fmt(y, 5)) for x, y in p[1:])
            out.append(paint)
            continue
        keep = [pl for sid, pl in enumerate(path) if len(pl) > 1 and (pid, sid) not in drop]
        if paint == 'S':
            if keep:
                if pend[0] is None:
                    pend[0] = (m, keep)
                else:
                    pend[0][1].extend(keep)
            continue
        flush()
        keep = [pl for pl in keep if len(pl) > 2]
        if not keep:
            continue
        d = ndig(m); t = tol_pt/scl(m)
        for pl in keep:
            pl = dp(pl, t) if len(pl) > 6 else pl
            out.append('%s %s m' % (fmt(pl[0][0], d), fmt(pl[0][1], d)))
            out.extend('%s %s l' % (fmt(x, d), fmt(y, d)) for x, y in pl[1:])
        out.append(paint)
    flush()
    return '\n'.join(out).encode('latin1')


def _font_widths(pikepdf, fobj):
    """-> (функция байты->[(код, ширина/1000)], двухбайтный?) или None."""
    st = str(fobj.get('/Subtype'))
    if st == '/Type0':
        df = fobj.DescendantFonts[0]
        dw = float(df.get('/DW', 1000)); w = {}
        arr = list(df.get('/W', [])); i = 0
        while i < len(arr):
            c = int(arr[i])
            if isinstance(arr[i+1], pikepdf.Array):
                for j, v in enumerate(arr[i+1]):
                    w[c+j] = float(v)
                i += 2
            else:
                for j in range(c, int(arr[i+1])+1):
                    w[j] = float(arr[i+2])
                i += 3
        return lambda b: [w.get(b[k]*256+b[k+1], dw)/1000.0 for k in range(0, len(b)-1, 2)], True
    if '/Widths' in fobj:
        fc = int(fobj.get('/FirstChar', 0)); ws = [float(x) for x in fobj.Widths]
        return lambda b: [(ws[c-fc] if 0 <= c-fc < len(ws) else 0.0)/1000.0 for c in b], False
    return None


def _fonts(pikepdf, page):
    fonts = {}
    res = page.Resources.get('/Font') or {}
    for k in list(res.keys()):
        try:
            fonts[str(k)] = _font_widths(pikepdf, res[k])
        except Exception:
            fonts[str(k)] = None
    return fonts


def _fix_ops(pikepdf, ops, fonts, ids, fx, st):
    """Ядро сжатия строк ведомостей. st — состояние, переходящее между кусками потока:
    font, fs, tc, tw, th, stack. -> (новые операторы, строк сжато)."""
    out = []; stack = st['stack']; changed = 0
    tm = tlm = None; font = st['font']; fs = st['fs']; tc = st['tc']; tw = st['tw']; th = st['th']
    anchor = None; adv_line = 0.0; groups = 0

    def inside():
        return any(s in ids for s in stack)

    def new_tm(m):
        """Матрица для вывода: строка сжата к anchor вдоль базовой линии."""
        ax, ay = anchor
        L = math.hypot(m[0], m[1]) or 1.0
        dx, dy = m[0]/L, m[1]/L
        along = (m[4]-ax)*dx + (m[5]-ay)*dy
        return [m[0]*fx, m[1]*fx, m[2], m[3], m[4]-(1-fx)*along*dx, m[5]-(1-fx)*along*dy]

    for operands, op in ops:
        o = str(op)
        if o in ('BMC', 'BDC'):
            stack.append(str(operands[0]))
        elif o == 'EMC' and stack:
            stack.pop()
        elif o == 'BT':
            tm = [1, 0, 0, 1, 0, 0]; tlm = list(tm); anchor = None; adv_line = 0.0
        elif o == 'Tf':
            font = str(operands[0]); fs = float(operands[1])
        elif o == 'Tc':
            tc = float(operands[0])
        elif o == 'Tw':
            tw = float(operands[0])
        elif o == 'Tz':
            th = float(operands[0])/100.0
        if tm is not None and inside() and fonts.get(font) is not None:
            if o == 'Tm':
                tm = [float(x) for x in operands]; tlm = list(tm); anchor = None; adv_line = 0.0
                continue                              # выведем перед словом
            if o in ('Td', 'TD'):
                tx, ty = float(operands[0]), float(operands[1])
                cont = abs(ty) < 1e-6 and -1e-6 <= tx <= adv_line*1.06 + 0.05*fs
                tlm = mul([1, 0, 0, 1, tx, ty], tlm); tm = list(tlm)
                if not cont:
                    anchor = None
                adv_line = 0.0
                continue
            if o == 'T*':
                anchor = None
            if o in ('Tj', 'TJ', "'", '"'):
                if o in ("'", '"'):
                    out.append(([], pikepdf.Operator('T*')))
                    anchor = None
                if anchor is None:
                    anchor = (tm[4], tm[5]); groups += 1
                m = new_tm(tm)
                out.append(([round(v, 5) for v in m], pikepdf.Operator('Tm')))
                out.append((operands, op))
                wf, two = fonts[font]
                adv = 0.0
                items = operands[-1]
                seq = list(items) if o == 'TJ' else [items]
                for it in seq:
                    try:
                        b = bytes(it)
                    except Exception:                 # число в TJ — сдвиг в тысячных
                        adv -= float(it)/1000.0*fs
                        continue
                    ws = wf(b)
                    nsp = b.count(b' ') if not two else 0
                    adv += sum(ws)*fs + tc*len(ws) + tw*nsp
                adv *= th
                tm = mul([1, 0, 0, 1, adv, 0], tm)
                adv_line += adv
                changed += 1
                continue
        if o == 'ET':
            tm = None
        out.append((operands, op))
    st.update(font=font, fs=fs, tc=tc, tw=tw, th=th)
    return out, (groups if changed else 0)


def _state():
    return dict(font=None, fs=1.0, tc=0.0, tw=0.0, th=1.0, stack=[])


def fix_text(pikepdf, page, ids, fx):
    """ids — номера элементов-ведомостей ('/Element123'). -> (новые байты потока или None, строк сжато)."""
    if not ids or fx >= 0.9999:
        return None, 0
    out, groups = _fix_ops(pikepdf, pikepdf.parse_content_stream(page), _fonts(pikepdf, page), ids, fx, _state())
    if not groups:
        return None, 0
    return pikepdf.unparse_content_stream(out), groups


def fix_text_big(pikepdf, pdf, page, data, ids, fx):
    """То же для огромного потока — построчно, разбираются только блоки BT…ET (память — как размер потока).
    -> (новые байты или None, строк сжато)."""
    import io as _io
    if not ids or fx >= 0.9999:
        return None, 0
    fonts = _fonts(pikepdf, page); st = _state(); stack = st['stack']
    out = _io.BytesIO(); w = out.write; block = None; total = 0
    for ln in _io.BytesIO(data):
        s = ln.strip()
        if block is not None:
            block.append(ln)
            if s == b'ET':
                raw = b''.join(block); block = None
                if any(x in ids for x in stack):
                    ops = pikepdf.parse_content_stream(pdf.make_stream(raw))
                    new, g = _fix_ops(pikepdf, ops, fonts, ids, fx, st)
                    if g:
                        total += g
                        w(pikepdf.unparse_content_stream(new) + b'\n')
                        continue
                else:                               # чужой текст — только запомнить шрифт/интервалы
                    for x in raw.split(b'\n'):
                        x = x.strip(); p = x.split()
                        try:
                            if x.endswith(b' Tf'):
                                st['font'] = p[-3].decode('latin1'); st['fs'] = float(p[-2])
                            elif x.endswith(b' Tc'):
                                st['tc'] = float(p[-2])
                            elif x.endswith(b' Tw'):
                                st['tw'] = float(p[-2])
                            elif x.endswith(b' Tz'):
                                st['th'] = float(p[-2])/100.0
                        except Exception:
                            pass
                w(raw)
            continue
        if s == b'BT':
            block = [ln]
            continue
        p = s.split()
        if s.endswith(b' BMC') or s.endswith(b' BDC'):
            stack.append(p[0].decode('latin1'))
        elif s == b'EMC' and stack:
            stack.pop()
        elif s.endswith(b' Tf') and len(p) >= 3:
            try:
                st['font'] = p[-3].decode('latin1'); st['fs'] = float(p[-2])
            except Exception:
                pass
        w(ln)
    if block:
        w(b''.join(block))
    return (out.getvalue(), total) if total else (None, 0)


def _filled_elements(data):
    """Номера элементов (/ElementN), у которых есть заливки: их белые штрихи — штриховка поверх заливки
    (стены), её не трогаем."""
    import io as _io
    res = set(); stack = []
    for ln in _io.BytesIO(data):
        s = ln.strip()
        if s.endswith(b' BMC') or s.endswith(b' BDC'):
            stack.append(s.split()[0])
        elif s == b'EMC':
            if stack:
                stack.pop()
        elif s in (b'f', b'f*', b'F', b'B', b'B*', b'b', b'b*') and stack:
            res.update(stack)
    return res


def drop_white(data, lo=0.95, hi=0.999):
    """Быстрый проход по байтам потока построчно (без разбора в объекты — мало памяти):
    убрать штрихи почти белого цвета (lo ≤ R,G,B < hi) у элементов БЕЗ заливок — так Revit рисует полутон
    IFC-подложки (мебель, оборудование АР) миллионами отрезков, на бумаге их не видно. Белая штриховка
    по заливке (стены) остаётся. Чисто белые (1,1,1) не трогаем. Заодно — пустые пары «/ElementN BMC … EMC».
    -> (байты, убрано штрихов)."""
    import io as _io
    filled = _filled_elements(data)
    out = _io.BytesIO(); buf = []; rg = None; st = []; n = 0; pend = None; stack = []
    w = out.write

    def put(ln):
        nonlocal pend
        if pend is not None:
            w(pend); pend = None
        w(ln)
    for ln in _io.BytesIO(data):
        s = ln.strip()
        if s.endswith(b' m') or s.endswith(b' l'):
            buf.append(ln)
            continue
        if s == b'S' and buf:
            if rg is not None and lo <= rg[0] < hi and lo <= rg[1] < hi and lo <= rg[2] < hi \
                    and stack and not any(x in filled for x in stack):
                n += 1; buf = []
                continue
            for x in buf:
                put(x)
            buf = []; put(ln)
            continue
        if buf:
            for x in buf:
                put(x)
            buf = []
        if s.endswith(b' RG'):
            try:
                rg = tuple(float(x) for x in s.split()[-4:-1])
            except Exception:
                rg = None
        elif s.endswith((b' G', b' K', b' SC', b' SCN', b' CS')):
            rg = None
        elif s == b'q':
            st.append(rg)
        elif s == b'Q':
            rg = st.pop() if st else None
        if s.endswith(b' BMC') or s.endswith(b' BDC'):
            stack.append(s.split()[0])
            if s.endswith(b' BMC'):
                if pend is not None:
                    w(pend)
                pend = ln
                continue
        elif s == b'EMC':
            if stack:
                stack.pop()
            if pend is not None:
                pend = None                    # пустая пара — выбросить
                continue
        put(ln)
    for x in buf:
        put(x)
    if pend is not None:
        w(pend)
    return out.getvalue(), n


def slim_file(args):
    src, dst, tol_mm, speck_mm = args[:4]
    ids = set(args[4]) if len(args) > 4 and args[4] else set()
    fx = args[5] if len(args) > 5 else 1.0
    do_slim = args[6] if len(args) > 6 else True
    white = args[7] if len(args) > 7 else False
    import pikepdf
    try:
        import shapely  # noqa
        shapely_ok = True
    except Exception:
        shapely_ok = False
    t0 = time.time(); pdf = pikepdf.open(src); info = []
    for page in pdf.pages:
        cs = page.obj.Contents
        cs = list(cs) if isinstance(cs, pikepdf.Array) else [cs]
        data = b'\n'.join(x.read_bytes() for x in cs); cs = None
        before = len(data)
        nwhite = 0
        if white:                       # сначала дешёвый проход: огромные листы худеют в 2–3 раза
            data, nwhite = drop_white(data)
            if nwhite:
                page.obj.Contents = pdf.make_stream(data)
        size = len(data)
        huge = size > HUGE
        ntext = 0
        if ids:
            try:
                if huge:
                    fixed, ntext = fix_text_big(pikepdf, pdf, page, data, ids, fx)
                else:
                    fixed, ntext = fix_text(pikepdf, page, ids, fx)
                if fixed is not None:
                    page.obj.Contents = pdf.make_stream(fixed)
                fixed = None
            except Exception:
                ntext = -1
        data = None
        if not do_slim or huge:         # огромный лист: полный разбор съедает десятки ГБ памяти — только белое
            info.append((before, size, 0, 0, ntext, nwhite))
            continue
        items = walk(pikepdf, page)
        drop, ndup, nocc = analyse(items, speck_mm*PT, shapely_ok and size < 20e6)
        data = emit(pikepdf, items, drop, tol_mm*PT)
        items = None
        if len(data) < size or ntext:
            page.obj.Contents = pdf.make_stream(data)
        info.append((before, len(data), ndup, nocc, ntext, nwhite))
        data = None
    pdf.save(dst, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate, recompress_flate=True)
    return src, dst, info, time.time()-t0


def combine(pikepdf, files, out):
    """Склеить листы; одинаковые картинки — один объект."""
    res = pikepdf.new()
    for f in files:
        with pikepdf.open(f) as src:
            res.pages.extend(src.pages)
    seen = {}; dup = 0
    for p in res.pages:
        xo = p.Resources.get('/XObject')
        if not xo:
            continue
        for k in list(xo.keys()):
            im = xo[k]
            if im.get('/Subtype') != '/Image':
                continue
            raw = im.read_raw_bytes()
            sm = im.get('/SMask')
            h = hashlib.md5(raw + (sm.read_raw_bytes() if sm is not None else b'')).hexdigest()
            if h in seen and seen[h].objgen != im.objgen:
                xo[k] = seen[h]; dup += 1
            else:
                seen[h] = im
    res.remove_unreferenced_resources()
    res.save(out, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    return dup


def shrink_images(pikepdf, path, quality=70):
    """Пережать крупные картинки (растровые листы) в JPEG указанного качества, если так меньше. -> экономия, байт."""
    try:
        from PIL import Image  # noqa
    except Exception:
        return 0
    import io as _io
    saved = 0
    pdf = pikepdf.open(path, allow_overwriting_input=True)
    seen = set()
    for page in pdf.pages:
        xo = page.Resources.get('/XObject') or {}
        for k in list(xo.keys()):
            im = xo[k]
            if im.get('/Subtype') != '/Image' or im.objgen in seen:
                continue
            seen.add(im.objgen)
            old = len(im.read_raw_bytes())
            if old < 200000 or im.get('/SMask') is not None:
                continue
            try:
                pil = pikepdf.PdfImage(im).as_pil_image()
                if pil.mode not in ('RGB', 'L'):
                    pil = pil.convert('RGB')
                b = _io.BytesIO(); pil.save(b, 'JPEG', quality=quality, optimize=True)
                data = b.getvalue()
                if len(data) < old * 0.9:
                    im.write(data, filter=pikepdf.Name.DCTDecode)
                    im.ColorSpace = pikepdf.Name.DeviceRGB if pil.mode == 'RGB' else pikepdf.Name.DeviceGray
                    im.BitsPerComponent = 8
                    for kk in ('/DecodeParms', '/Decode'):
                        if kk in im:
                            del im[kk]
                    saved += old - len(data)
            except Exception:
                continue
    if saved:
        pdf.save(path, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    pdf.close()
    return saved


def mmss(sec):
    sec = int(round(sec))
    m, s = divmod(sec, 60)
    return ('%d мин %d с' % (m, s)) if m else ('%d с' % s)


def message(text, title):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40 | 0x40000)
    except Exception:
        pass


class Progress(object):
    """Окно с полосой загрузки поверх всех окон (db 22.10.2026: «не понимаю, сколько ещё ждать»).
    Нет tkinter — молча без окна."""
    def __init__(self, title, total):
        self.root = None
        self.total = max(1, total)
        self.t0 = time.time()
        try:
            import tkinter as tk
            from tkinter import ttk
            r = tk.Tk()
            r.title(title)
            r.attributes('-topmost', True)
            r.resizable(False, False)
            r.configure(bg='#F4F6F4')
            w, h = 460, 130
            r.geometry('%dx%d+%d+%d' % (w, h, r.winfo_screenwidth() - w - 40, r.winfo_screenheight() - h - 90))
            tk.Label(r, text='PDF вектор — доводка альбома (Revit свободен)', bg='#1E2A22', fg='white',
                     font=('Segoe UI', 10, 'bold'), anchor='w', padx=10, pady=6).pack(fill='x')
            self.var = tk.StringVar(value='Запуск…')
            tk.Label(r, textvariable=self.var, bg='#F4F6F4', font=('Segoe UI', 9), anchor='w',
                     padx=10, justify='left').pack(fill='x', pady=(8, 4))
            st = ttk.Style(r)
            try:
                st.theme_use('clam')
            except Exception:
                pass
            st.configure('G.Horizontal.TProgressbar', troughcolor='#DDE3DE', background='#2E7D32',
                         bordercolor='#DDE3DE', lightcolor='#2E7D32', darkcolor='#2E7D32')
            self.bar = ttk.Progressbar(r, style='G.Horizontal.TProgressbar', length=w - 20,
                                       maximum=self.total + 1, mode='determinate')
            self.bar.pack(padx=10, pady=(0, 10))
            r.protocol('WM_DELETE_WINDOW', lambda: None)       # не закрывать посреди работы
            r.update()
            self.root = r
        except Exception:
            self.root = None

    def set(self, done, text):
        if self.root is None:
            return
        try:
            el = time.time() - self.t0
            eta = ''
            if 0 < done < self.total:
                eta = ', осталось ≈ %s' % mmss(el / done * (self.total - done))
            self.var.set('%s\nГотово листов: %d из %d · прошло %s%s' % (text, done, self.total, mmss(el), eta))
            self.bar['value'] = done
            self.root.update()
        except Exception:
            self.root = None

    def tick(self):
        if self.root is not None:
            try:
                self.root.update()
            except Exception:
                self.root = None

    def close(self):
        if self.root is not None:
            try:
                self.root.destroy()
            except Exception:
                pass
            self.root = None


HUGE = 40e6           # байт потока после «белого» прохода: больше — без полного разбора
BIG_MB = 15.0           # лист тяжелее — доводится один, без соседей (память: до ~12 ГБ на лист 50 МБ)


def run_tasks(tasks, job, log, failed, prog=None):
    """Каждый лист — отдельным процессом с лимитом времени. Лёгкие — по несколько сразу, тяжёлые — по одному.
    Не уложился или упал — в альбом идёт исходный лист (failed). -> {src: dst}"""
    import subprocess
    # лимит на лист: дольше — второй заход только с правкой текста ведомостей (без облегчения),
    # не уложился и он — исходный лист. Раньше было 15 мин, один лист съедал 9 мин (db 22.10.2026).
    limit = float(job.get('sheet_limit_s', 90))
    limit_text = float(job.get('text_limit_s', 120))
    lite = []          # листы, доведённые только по тексту
    cpu = max(1, min(4, (os.cpu_count() or 2) - 1))
    queue = sorted(tasks, key=lambda t: os.path.getsize(t[0]))          # лёгкие вперёд
    # тяжёлые листы (> slim_max_mb) облегчение не успевают за лимит: 27.10.2026 из 7 таких все 7 ушли
    # в таймаут (≈10 мин работы впустую) — сразу только текст ведомостей
    smax = float(job.get('slim_max_mb', 2.5))
    for i, t in enumerate(queue):
        if t[6] and os.path.getsize(t[0]) / 1e6 > smax:
            queue[i] = tuple(t[:6]) + (False,) + tuple(t[7:])
            lite.append(os.path.basename(t[0]))
            log('%s: %.1f МБ — тяжелее %.1f МБ, без облегчения (только текст ведомостей)' % (os.path.basename(t[0]), os.path.getsize(t[0]) / 1e6, smax))
    running = []; done = {}
    me = os.path.abspath(__file__)

    def mb(t):
        return os.path.getsize(t[0]) / 1e6

    def finish(p, t, t0, timeout=False):
        name = os.path.basename(t[0])
        res = t[1] + '.json'
        if timeout:
            try:
                p.kill()
            except Exception:
                pass
            if t[6] and t[4]:          # было облегчение и есть ведомости — второй заход: только текст
                t2 = tuple(t[:6]) + (False,) + tuple(t[7:])
                queue.insert(0, t2)
                lite.append(name)
                log('%s: облегчение дольше %s — второй заход только с текстом ведомостей' % (name, mmss(limit)))
            elif not t[4]:             # ведомостей нет — исходный лист ничем не хуже, только тяжелее
                log('%s: %.1f МБ — облегчение дольше %s, взят исходный лист (ведомостей нет)' % (name, mb(t), mmss(limit)))
            else:
                failed.append(name)
                log('%s: %.1f МБ — не уложилась в %s, взят исходный лист' % (
                    name, mb(t), mmss(limit if t[6] else limit_text)))
        elif p.returncode == 0 and os.path.exists(t[1]) and os.path.exists(res):
            info, sec = json.load(open(res, encoding='utf-8'))
            done[t[0]] = t[1]
            b = sum(i[0] for i in info); a = sum(i[1] for i in info)
            nt = sum(i[4] for i in info if i[4] > 0); nw = sum(i[5] for i in info if len(i) > 5)
            log('%s: %.1f → %.1f МБ; графика %d → %d КБ, повторов %d, закрытых %d, почти белых %d, строк ведомостей %d%s, %s' % (
                name, mb(t), os.path.getsize(t[1]) / 1e6, b//1024, a//1024, sum(i[2] for i in info),
                sum(i[3] for i in info), nw, nt, ' (ОШИБКА сжатия текста)' if any(i[4] < 0 for i in info) else '', mmss(sec)))
        else:
            failed.append(name)
            log('%s: доводка упала (код %s), взят исходный лист' % (name, p.returncode))
        for x in (res,) + ((t[1],) if t[0] not in done else ()):
            try:
                if os.path.exists(x):
                    os.remove(x)
            except Exception:
                pass

    while queue or running:
        for r in list(running):
            p, t, t0 = r
            if p.poll() is not None:
                running.remove(r); finish(p, t, t0)
            elif time.time() - t0 > (limit if t[6] else limit_text):
                running.remove(r); finish(p, t, t0, timeout=True)
        while queue:
            t = queue[0]
            heavy_running = any(mb(r[1]) >= BIG_MB for r in running)
            if mb(t) >= BIG_MB and running:
                break
            if heavy_running or len(running) >= cpu:
                break
            queue.pop(0)
            arg = os.path.join(os.path.dirname(t[1]), os.path.basename(t[1]) + '.task.json')
            with open(arg, 'w', encoding='utf-8') as f:
                json.dump(list(t), f, ensure_ascii=False)
            flags = 0x08000000 if os.name == 'nt' else 0          # без окна консоли
            p = subprocess.Popen([sys.executable, me, '--one', arg], creationflags=flags)
            running.append((p, t, time.time()))
        if prog is not None:
            nd = len(done) + len(failed)
            prog.set(nd, 'Облегчаю листы: в работе %s' % (', '.join(os.path.basename(r[1][0]) for r in running) or '—'))
        for _ in range(5):
            time.sleep(0.1)
            if prog is not None:
                prog.tick()
    return done


def one(task_path):
    """Дочерний процесс: доводка одного листа, результат — <dst>.json."""
    t = json.load(open(task_path, encoding='utf-8'))
    try:
        os.remove(task_path)
    except Exception:
        pass
    src, dst, info, sec = slim_file(t)
    with open(dst + '.json', 'w', encoding='utf-8') as f:
        json.dump([info, sec], f)


def main(job_path):
    job = json.load(open(job_path, encoding='utf-8'))
    log_path = os.path.splitext(job['out'])[0] + '_лог.txt'
    lines = []

    def log(s):
        lines.append(s)
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    t0 = time.time()
    raw = job['raw']; title = job.get('title', 'PDF вектор')
    try:
        import pikepdf
    except Exception:
        message('Нет библиотеки pikepdf: выполните в командной строке\n  py -m pip install --user pikepdf shapely numpy\n\n'
                'Листы без доводки лежат в папке:\n' + os.path.dirname(raw[0]), title)
        return
    before = sum(os.path.getsize(f) for f in raw)
    files = raw
    text = job.get('text') or {}
    fx = float(job.get('text_fx', 0.96))
    failed = []
    if job.get('slim', True) or text:
        tmpdir = os.path.join(os.path.dirname(raw[0]), '_slim')
        os.makedirs(tmpdir, exist_ok=True)
        rset = set(job.get('raster') or [])       # растровые листы не разбираем — сразу в альбом
        tasks = [(f, os.path.join(tmpdir, os.path.basename(f)), job.get('tol_mm', 0.02), job.get('speck_mm', 0.05),
                  ['/Element%d' % i for i in text.get(f, [])], fx, job.get('slim', True), job.get('white', True))
                 for f in raw if f not in rset]
        prog = Progress(title, len(tasks))
        done = run_tasks(tasks, job, log, failed, prog)
        prog.set(len(tasks), 'Склеиваю альбом…')
    else:
        prog = None
        files = [done.get(f, f) for f in raw]      # не удалось — исходный лист
    if job.get('combine', True):
        try:
            dup = combine(pikepdf, files, job['out'])
        except Exception:
            log('Склейка облегчённых листов не удалась — склеиваю исходные:\n' + traceback.format_exc()[-800:])
            failed.append('склейка')
            dup = combine(pikepdf, raw, job['out'])
        after = os.path.getsize(job['out'])
        log('Склеено листов: %d, общих картинок: %d' % (len(files), dup))
        lim = float(job.get('max_mb', 0)) * 1e6
        if lim and after > lim:          # растровые листы и снимки подложек (xyz_snap) — пережать картинки
            for q in (70, 50, 35):
                sv = shrink_images(pikepdf, job['out'], q)
                after = os.path.getsize(job['out'])
                log('Картинки (растр, подложки) пережаты (JPEG %d): −%.1f МБ → %.1f МБ' % (q, sv/1e6, after/1e6))
                if after <= lim or not sv:
                    break
        result = job['out']
    else:
        outdir = os.path.dirname(job['out'])
        import shutil
        after = 0
        for f, r in zip(files, raw):
            dst = os.path.join(outdir, os.path.basename(r))
            if os.path.abspath(f) != os.path.abspath(dst):
                shutil.copyfile(f, dst)
            after += os.path.getsize(dst)
        result = outdir
    if prog is not None:
        prog.close()
    t_opt = time.time()-t0; t_exp = float(job.get('t_export', 0))
    log('Было (экспорт Revit по листам): %.1f МБ → стало: %.1f МБ' % (before/1e6, after/1e6))
    log('Время: экспорт в PDF (Revit) — %s, оптимизация — %s, всего — %s' % (mmss(t_exp), mmss(t_opt), mmss(t_exp+t_opt)))
    if not job.get('keep_raw', False):
        import shutil
        for d in {os.path.dirname(raw[0])}:
            shutil.rmtree(d, ignore_errors=True)
    try:
        os.startfile(os.path.dirname(job['out']))
    except Exception:
        pass
    warn = ''
    if failed:
        warn = ('\n\nВНИМАНИЕ: доводка не удалась на %d лист(ах) — в альбом взяты исходные (тяжелее, текст ведомостей '
                'может вылезать за ячейки):\n%s' % (len(failed), ', '.join(failed)))
    message('Готово: %s\n\n%.1f МБ (сырой экспорт был %.1f МБ)\nЛистов: %d\nВремя: экспорт %s + оптимизация %s = %s%s' % (
        result, after/1e6, before/1e6, len(files), mmss(t_exp), mmss(t_opt), mmss(t_exp+t_opt), warn), title)


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--one':
        one(sys.argv[2])
        sys.exit(0)
    try:
        main(sys.argv[1])
    except Exception:
        err = traceback.format_exc()
        try:
            with open(os.path.join(os.environ.get('TEMP', '.'), 'xyz_pdfslim_error.txt'), 'w', encoding='utf-8') as f:
                f.write(err)
        except Exception:
            pass
        message('Ошибка доводки PDF:\n' + err[-1500:], 'PDF вектор')
