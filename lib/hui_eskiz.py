# -*- coding: utf-8 -*-
"""Эскиз изделия по ЕСКД из параметров семейства (lib/hui_eskiz.py), db 28.10.2026.

Кнопка «Эскиз» (ХОЧУ.panel/Eskiz.pushbutton). Эскиз строится НЕ из видов семейства, а из его
параметров (как у пленумов кнопки «Пленум»):
  ADSK_Размер_Ширина / Высота / Длина (габарит), ADSK_Толщина стенки, ADSK_Материал наименование,
  db_В<i>_Грань / _Роль / _Размер («Ø160» или «500×400») / _U / _V / _Длина, db_Код конфигурации.

Слой 1 (без Revit, проверяется вне Revit): part_from() → build_sketch() → список примитивов
  line / circle / fill / dim (размер Revit по ссылкам на линии) / text; render() — то же простыми
  отрезками и текстами (для превью в окне и для проверки картинкой).
Слой 2 (Revit): styles(), emit() — линии детализации, заливки, размеры, тексты в легенде.

Договорённости с db (Obsidian: REVIT/Плагин/ХУЙ/Разработка/Эскиз.md):
  виды по ЕСКД (первый угол): главный, слева — справа от главного, сверху — под главным, плюс
  справа / снизу / сзади по выбору; аксонометрия — третьим столбцом, в масштабе видов, ракурс
  (азимут/наклон) задаётся; размеры на аксонометрии — линиями и текстом (ГОСТ 2.317);
  диаметры — один стиль: тонкая линия, залитые стрелки «db_Эскиз_Стрелка», текст «Ø…»;
  отверстия «в лоб» — серая заливка, «с ребра» — стрелка по оси отверстия;
  стрелка-указатель не пересекает размеры; заголовки видов — над видами;
  повторяющиеся размеры на разных видах оставляем (db 28.10).
"""
from __future__ import division
import math

try:
    unicode
except NameError:            # проверка вне Revit (Python 3)
    unicode = str

FACES = [u'Спереди', u'Сзади', u'Слева', u'Справа', u'Сверху', u'Снизу']
FACE_LOW = {u'Спереди': u'спереди', u'Сзади': u'сзади', u'Слева': u'слева', u'Справа': u'справа',
            u'Сверху': u'сверху', u'Снизу': u'снизу'}
AXIS = {u'Спереди': 'D', u'Сзади': 'D', u'Слева': 'W', u'Справа': 'W', u'Сверху': 'H', u'Снизу': 'H'}
ROTS = [0, 90, 180, 270]
SCALES = [1, 2, 2.5, 4, 5, 10, 15, 20, 25, 50]
# вид: (ключ, заголовок, столбец, строка) — ЕСКД, первый угол
VIEWS = [('main', u'Главный вид', 1, 1), ('left', u'Вид слева', 2, 1), ('top', u'Вид сверху', 1, 2),
         ('right', u'Вид справа', 0, 1), ('bottom', u'Вид снизу', 1, 0), ('rear', u'Вид сзади', 3, 1)]
VIEW_TITLE = dict((k, t) for k, t, c, r in VIEWS)
DEFAULT_OPT = {'face': None, 'rot': 0, 'views': ['main', 'left', 'top'], 'axo': True, 'az': 35.0, 'el': 30.0,
               'axo_pos': 'middle', 'scale': 0}
TXT_W = 0.62           # ширина знака / высота текста — для раскладки и превью


# ------------------------------------------------------------------ векторы (кортежи)
def vadd(*vs):
    return tuple(sum(c) for c in zip(*vs))


def vsub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def vmul(v, k):
    return tuple(c * k for c in v)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def vlen(a):
    return math.sqrt(dot(a, a))


def unit(a):
    n = vlen(a)
    return vmul(a, 1.0 / n) if n > 1e-12 else a


def perp(a):
    return (-a[1], a[0])


def fmt(x):
    s = u'%.1f' % x
    return s.rstrip(u'0').rstrip(u'.') if u'.' in s else s


def num(s, default=0.0):
    try:
        return float(unicode(s).replace(u',', u'.').replace(u'Ø', u'').strip())
    except Exception:
        return default


# ------------------------------------------------------------------ изделие из параметров
def face_frame(face, W, D, H):
    """Как в кнопке «Пленум»: начало (левый нижний угол грани снаружи), u, v, нормаль. Короб по центру."""
    if face == u'Спереди':
        return (-W / 2, -D / 2, -H / 2), (1, 0, 0), (0, 0, 1), (0, -1, 0)
    if face == u'Сзади':
        return (W / 2, D / 2, -H / 2), (-1, 0, 0), (0, 0, 1), (0, 1, 0)
    if face == u'Слева':
        return (-W / 2, D / 2, -H / 2), (0, -1, 0), (0, 0, 1), (-1, 0, 0)
    if face == u'Справа':
        return (W / 2, -D / 2, -H / 2), (0, 1, 0), (0, 0, 1), (1, 0, 0)
    if face == u'Сверху':
        return (-W / 2, -D / 2, H / 2), (1, 0, 0), (0, 1, 0), (0, 0, 1)
    return (-W / 2, D / 2, -H / 2), (1, 0, 0), (0, -1, 0), (0, 0, -1)


def face_size(face, W, D, H):
    if face in (u'Спереди', u'Сзади'):
        return W, H
    if face in (u'Слева', u'Справа'):
        return D, H
    return W, D


def part_from(get_str, get_mm, family=u'', type_name=u''):
    """Изделие из параметров. get_str(имя) → текст/None, get_mm(имя) → мм/None.
    Нет габаритов или врезок db_В1_* → None (семейство не умеем, напр. КСД)."""
    W, H, D = get_mm(u'ADSK_Размер_Ширина'), get_mm(u'ADSK_Размер_Высота'), get_mm(u'ADSK_Размер_Длина')
    cuts = []
    for i in range(1, 61):
        face = get_str(u'db_В%d_Грань' % i)
        if not face:
            break
        face = face.strip()
        if face not in FACES:
            continue
        size = (get_str(u'db_В%d_Размер' % i) or u'').strip()
        for ch in (u'x', u'X', u'х', u'Х', u'*'):
            size = size.replace(ch, u'×')
        c = {'i': i, 'face': face, 'role': (get_str(u'db_В%d_Роль' % i) or u'').strip(),
             'u': get_mm(u'db_В%d_U' % i) or 0.0, 'v': get_mm(u'db_В%d_V' % i) or 0.0,
             'l': get_mm(u'db_В%d_Длина' % i) or 0.0}
        if size.startswith(u'Ø') or u'×' not in size:
            c['shape'], c['d'] = 'c', num(size)
            c['w'] = c['h'] = c['d']
            c['label'] = u'Ø' + fmt(c['d'])
        else:
            a, b = size.split(u'×')[:2]
            c['shape'], c['w'], c['h'] = 'r', num(a), num(b)
            c['label'] = u'%s×%s' % (fmt(c['w']), fmt(c['h']))
        if c['w'] > 0 and c['h'] > 0:
            cuts.append(c)
    if not (W and H and D) or min(W, H, D) <= 0 or not cuts:
        return None
    # габарит в параметрах = короб + удлинения «во всю грань» (как в «Пленуме») → короб
    bw, bd, bh = W, D, H
    for _ in range(3):
        add = {'W': 0.0, 'D': 0.0, 'H': 0.0}
        for c in cuts:
            su, sv = face_size(c['face'], bw, bd, bh)
            c['full'] = c['shape'] == 'r' and abs(c['w'] - su) < 0.6 and abs(c['h'] - sv) < 0.6
            if c['full']:
                add[AXIS[c['face']]] += c['l']
        bw, bd, bh = W - add['W'], D - add['D'], H - add['H']
    p = {'W': bw, 'D': bd, 'H': bh, 'OW': W, 'OD': D, 'OH': H, 'cuts': cuts, 'family': family,
         'type': type_name, 'mat': get_str(u'ADSK_Материал наименование') or u'',
         'wall': get_mm(u'ADSK_Толщина стенки'), 'code': get_str(u'db_Код конфигурации') or u'',
         'mode': get_str(u'db_Режим') or u'', 'count': 0}
    for c in cuts:
        o, u, v, n = face_frame(c['face'], bw, bd, bh)
        c['o3'] = vadd(o, vmul(u, c['u']), vmul(v, c['v']))
        c['t3'] = vadd(c['o3'], vmul(n, c['l']))
        c['u3'], c['v3'], c['n3'] = u, v, n
    return p


def default_face(part):
    """Главный вид — грань с наибольшим числом ответвлений, при равенстве — «Спереди»."""
    cnt = dict((f, 0) for f in FACES)
    for c in part['cuts']:
        if c['role'] != u'Основной' and not c.get('full'):
            cnt[c['face']] += 1
    best = max(cnt.values())
    if best == 0:
        for c in part['cuts']:
            cnt[c['face']] += 1
        best = max(cnt.values())
    for f in FACES:
        if cnt[f] == best:
            return f
    return u'Спереди'


def auto_scale(parts):
    """Самый крупный масштаб, при котором наибольший габарит изделия — не больше ~100 мм листа."""
    big = 1.0
    for p in parts:
        ext = [p['W'], p['D'], p['H']]
        for c in p['cuts']:
            ext.append(max(p['W'], p['D'], p['H']) + c['l'])
        big = max(big, max(ext))
    for s in SCALES:
        if big / s <= 120.0:
            return s
    return SCALES[-1]


# ------------------------------------------------------------------ виды
def main_basis(face, rot):
    o, u, v, n = face_frame(face, 1, 1, 1)
    a = math.radians(rot)
    cs, sn = int(round(math.cos(a))), int(round(math.sin(a)))
    R = vadd(vmul(u, cs), vmul(v, sn))
    U = vadd(vmul(u, -sn), vmul(v, cs))
    return R, U, n


def view_basis(key, R, U, N):
    """(вправо, вверх, к зрителю) для вида; ЕСКД, первый угол."""
    m = vmul
    return {'main': (R, U, N), 'left': (N, U, m(R, -1)), 'right': (m(N, -1), U, R),
            'top': (R, m(N, -1), U), 'bottom': (R, N, m(U, -1)), 'rear': (m(R, -1), U, m(N, -1))}[key]


class Pen(object):
    """Сборщик примитивов одного вида (координаты листа-легенды, мм модели)."""

    def __init__(self, S):
        self.S = float(S)
        self.k = self.S / 5.0          # числа пробы 27.10 были подобраны при 1:5
        self.p = []

    def line(self, a, b, st='thick'):
        d = {'t': 'line', 'a': tuple(a), 'b': tuple(b), 'st': st}
        self.p.append(d)
        return d

    def circle(self, c, r, st='thick'):
        self.p.append({'t': 'circle', 'c': tuple(c), 'r': r, 'st': st})

    def arc(self, c, r, a0, a1, st='thick'):
        """Дуга против часовой от a0 до a1 (радианы)."""
        self.p.append({'t': 'arc', 'c': tuple(c), 'r': r, 'a0': a0, 'a1': a1, 'st': st})

    def fill(self, pts=None, c=None, r=None, kind='gray'):
        self.p.append({'t': 'fill', 'pts': [tuple(q) for q in pts] if pts else None,
                       'c': tuple(c) if c else None, 'r': r, 'kind': kind})

    def dim(self, refs, a, b):
        if len(refs) >= 2:
            self.p.append({'t': 'dim', 'refs': refs, 'a': tuple(a), 'b': tuple(b)})

    def text(self, pos, s, rot=0.0, h=2.5, al='l'):
        self.p.append({'t': 'text', 'pos': tuple(pos), 's': s, 'rot': rot, 'h': h, 'al': al})

    def tip(self, frm, to):
        """Залитое остриё стрелки в точке to, направление frm → to."""
        k = self.k
        u = unit(vsub(to, frm))
        n = perp(u)
        b = vsub(to, vmul(u, 14 * k))
        self.fill(pts=[to, vadd(b, vmul(n, 5 * k)), vsub(b, vmul(n, 5 * k))], kind='arrow')

    def arrow(self, a, b):
        self.line(a, b, 'thin')
        self.tip(a, b)

    def dot_end(self, c):
        self.fill(c=c, r=1.0 * self.S, kind='arrow')

    def text_along(self, mid, d, s, side=None, h=2.5, shift=0.0, gap=1.2):
        """Текст вдоль направления d (читаемо), над линией через mid; side — с какой стороны (вектор)."""
        ang = math.atan2(d[1], d[0])
        if ang > math.pi / 2 + 1e-6:
            ang -= math.pi
        if ang <= -math.pi / 2 + 1e-6:
            ang += math.pi
        dr = (math.cos(ang), math.sin(ang))
        up = perp(dr)
        if side is not None and dot(up, side) < 0:
            # текст обязан стоять по другую сторону — уводим его «под» линию (верх текста к линии)
            pos = vadd(mid, vmul(up, -gap * self.S), vmul(dr, shift))
        else:
            pos = vadd(mid, vmul(up, (gap + h) * self.S), vmul(dr, shift))
        self.text(pos, s, rot=math.degrees(ang), h=h, al='c')


# ------------------------------------------------------------------ отрисовка (для превью и рамок)
def text_box(t, S):
    """Углы прямоугольника текста (мм модели). Точка вставки — верх, слева / по центру (как TextNote)."""
    h = t['h'] * S
    w = max(1, max(len(x) for x in t['s'].split(u'\n'))) * h * TXT_W
    nl = t['s'].count(u'\n') + 1
    hh = h * 1.45 * nl - h * 0.45
    a = math.radians(t['rot'])
    dr, up = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    x0 = -w / 2 if t['al'] == 'c' else (-w if t['al'] == 'r' else 0.0)
    pts = []
    for lx, ly in ((x0, 0), (x0 + w, 0), (x0 + w, -hh), (x0, -hh)):
        pts.append(vadd(t['pos'], vmul(dr, lx), vmul(up, ly)))
    return pts


def render(prims, S):
    """Примитивы → простые элементы: ('seg', a, b, стиль) · ('circle', c, r, стиль) ·
    ('poly', точки, вид) · ('disc', c, r, вид) · ('text', t-словарь). Размеры Revit — как их нарисует Revit."""
    out = []
    for p in prims:
        t = p['t']
        if t == 'line':
            out.append(('seg', p['a'], p['b'], p['st']))
        elif t == 'circle':
            out.append(('circle', p['c'], p['r'], p['st']))
        elif t == 'arc':
            n = max(6, int(abs(p['a1'] - p['a0']) / 0.12))
            q = [vadd(p['c'], (p['r'] * math.cos(p['a0'] + (p['a1'] - p['a0']) * i / n),
                               p['r'] * math.sin(p['a0'] + (p['a1'] - p['a0']) * i / n))) for i in range(n + 1)]
            for i in range(n):
                out.append(('seg', q[i], q[i + 1], p['st']))
        elif t == 'fill':
            if p['pts']:
                out.append(('poly', p['pts'], p['kind']))
            else:
                out.append(('disc', p['c'], p['r'], p['kind']))
        elif t == 'text':
            out.append(('text', p))
        elif t == 'dim':
            out.extend(render_dim(p, S))
    return out


def render_dim(p, S):
    a, b = p['a'], p['b']
    d = unit(vsub(b, a))
    n = perp(d)
    pos = []
    for r in p['refs']:
        ta = dot(vsub(r['a'], a), d)
        ea, eb = dot(vsub(r['a'], a), n), dot(vsub(r['b'], a), n)
        pos.append((ta, ea if abs(ea) < abs(eb) else eb))
    pos.sort()
    out = []
    t0, t1 = pos[0][0], pos[-1][0]
    out.append(('seg', vadd(a, vmul(d, t0 - 1.5 * S)), vadd(a, vmul(d, t1 + 1.5 * S)), 'dim'))
    for t, e in pos:
        base = vadd(a, vmul(d, t))
        sg = 1 if e < 0 else -1          # от ссылки к размерной линии
        if abs(e) > 2 * S:
            out.append(('seg', vadd(base, vmul(n, e + sg * 1.0 * S)), vadd(base, vmul(n, -sg * 2 * S)), 'dim'))
        tk = vmul(unit(vadd(d, n)), 1.5 * S)
        out.append(('seg', vsub(base, tk), vadd(base, tk), 'dimtick'))
    for i in range(len(pos) - 1):
        ta, tb = pos[i][0], pos[i + 1][0]
        if tb - ta < 0.05:
            continue
        mid = vadd(a, vmul(d, (ta + tb) / 2))
        ang = math.degrees(math.atan2(d[1], d[0]))
        if ang > 90.01:
            ang -= 180
        if ang <= -89.99:
            ang += 180
        up = (-math.sin(math.radians(ang)), math.cos(math.radians(ang)))
        out.append(('text', {'pos': vadd(mid, vmul(up, 3.6 * S)), 's': fmt(tb - ta), 'rot': ang, 'h': 2.5,
                             'al': 'c', 'dim': True}))
    return out


def bbox(items, S):
    xs, ys = [], []
    for it in items:
        if it[0] == 'seg':
            xs += [it[1][0], it[2][0]]
            ys += [it[1][1], it[2][1]]
        elif it[0] in ('circle', 'disc'):
            xs += [it[1][0] - it[2], it[1][0] + it[2]]
            ys += [it[1][1] - it[2], it[1][1] + it[2]]
        elif it[0] == 'poly':
            xs += [q[0] for q in it[1]]
            ys += [q[1] for q in it[1]]
        elif it[0] == 'text':
            for q in text_box(it[1], S):
                xs.append(q[0])
                ys.append(q[1])
    if not xs:
        return [0, 0, 0, 0]
    return [min(xs), min(ys), max(xs), max(ys)]


def shift(prims, dx, dy):
    for p in prims:
        for key in ('a', 'b', 'c', 'pos'):
            if p.get(key) is not None:
                p[key] = (p[key][0] + dx, p[key][1] + dy)
        if p.get('pts'):
            p['pts'] = [(q[0] + dx, q[1] + dy) for q in p['pts']]


# ------------------------------------------------------------------ ортогональный вид
def build_ortho(part, key, basis, S):
    R, U, N = basis
    W, D, H = part['W'], part['D'], part['H']
    pen = Pen(S)
    k = pen.k
    dims3 = (W, D, H)

    def half(a):
        return sum(abs(a[i]) * dims3[i] for i in range(3)) / 2.0

    def pj(p):
        return (dot(p, R), dot(p, U))

    hx, hy = half(R), half(U)
    bL = pen.line((-hx, -hy), (-hx, hy))
    bR = pen.line((hx, -hy), (hx, hy))
    bB = pen.line((-hx, -hy), (hx, -hy))
    bT = pen.line((-hx, hy), (hx, hy))
    vaxes, haxes = {}, {}            # оси врезок «в лоб» — для цепочек: x → линия, y → линия
    side_ends = {'h': {}, 'v': {}}   # торцы врезок «с ребра»: координата → линия
    seen = set()
    nlab, done_lab = {}, set()     # одинаковые врезки «в лоб» подписываем один раз: «3 отв. Ø160»
    for c in part['cuts']:
        if dot(c['n3'], N) > 0.5 and not c.get('full'):
            nlab[c['label']] = nlab.get(c['label'], 0) + 1
    for c in part['cuts']:
        s = dot(c['n3'], N)
        c2 = pj(c['o3'])
        if s > 0.5 or s < -0.5:
            hw = (abs(dot(c['u3'], R)) * c['w'] + abs(dot(c['v3'], R)) * c['h']) / 2.0
            hh = (abs(dot(c['u3'], U)) * c['w'] + abs(dot(c['v3'], U)) * c['h']) / 2.0
            if s < 0:                                     # на дальней грани — невидимым контуром
                if not c.get('full'):
                    if c['shape'] == 'c':
                        pen.circle(c2, c['d'] / 2.0, 'hidden')
                    elif c['shape'] == 'o':
                        _stadium(pen, c2, hw, hh, 'hidden')
                    else:
                        _rect(pen, c2, hw, hh, 'hidden')
                continue
            if c.get('full'):                             # вся грань — проём
                pen.fill(pts=[(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)], kind='gray')
                continue
            ov = 15 * k
            if c['shape'] == 'c':
                r = c['d'] / 2.0
                pen.fill(c=c2, r=r, kind='gray')
                pen.circle(c2, r, 'thick')
                hw = hh = r
            elif c['shape'] == 'o':
                pen.fill(pts=stadium_pts(c2, hw, hh), kind='gray')
                _stadium(pen, c2, hw, hh, 'thick')
            else:
                pts = [(c2[0] - hw, c2[1] - hh), (c2[0] + hw, c2[1] - hh), (c2[0] + hw, c2[1] + hh),
                       (c2[0] - hw, c2[1] + hh)]
                pen.fill(pts=pts, kind='gray')
                _rect(pen, c2, hw, hh, 'thick')
            kx, ky = round(c2[0], 1), round(c2[1], 1)
            va = pen.line((c2[0], c2[1] - hh - ov), (c2[0], c2[1] + hh + ov), 'axis')
            ha = pen.line((c2[0] - hw - ov, c2[1]), (c2[0] + hw + ov, c2[1]), 'axis')
            vaxes.setdefault(kx, va)
            haxes.setdefault(ky, ha)
            # подпись размера врезки: линия под 45° через центр, стрелки на контуре, полка
            if c['label'] in done_lab:
                continue
            done_lab.add(c['label'])
            lab = (u'%d отв. ' % nlab[c['label']] if nlab.get(c['label'], 1) > 1 else u'') + c['label']
            if c['shape'] == 'c':
                q = 0.7071 * r
                A, Bp = (c2[0] - q, c2[1] - q), (c2[0] + q, c2[1] + q)
                E = (Bp[0] + 45 * k, Bp[1] + 45 * k)
                pen.line(A, E, 'thin')
                pen.tip(c2, Bp)
                pen.tip(c2, A)
            else:
                Bp = (c2[0] + hw * 0.5, c2[1] + hh * 0.5)
                E = (c2[0] + hw + 45 * k, c2[1] + hh + 45 * k)
                pen.line(Bp, E, 'thin')
                pen.dot_end(Bp)
            sw = max(60 * k, len(lab) * 2.5 * S * TXT_W + 6 * k)
            pen.line(E, (E[0] + sw, E[1]), 'thin')
            pen.text((E[0] + 3 * k, E[1] + 1.2 * S + 2.5 * S), lab)
            continue
        # «с ребра» — патрубок сбоку
        t2 = pj(c['t3'])
        a = (dot(c['n3'], R), dot(c['n3'], U))
        p = perp(a)
        p3 = vadd(vmul(R, p[0]), vmul(U, p[1]))
        hp = c['d'] / 2.0 if c['shape'] == 'c' else (abs(dot(c['u3'], p3)) * c['w'] + abs(dot(c['v3'], p3)) * c['h']) / 2.0
        key_ = (round(c2[0], 1), round(c2[1], 1), round(t2[0], 1), round(t2[1], 1), round(hp, 1))
        if key_ in seen:
            continue
        seen.add(key_)
        orient = 'h' if abs(a[0]) > 0.5 else 'v'
        if c['l'] > 0.5:
            P1, P2 = vadd(c2, vmul(p, hp)), vsub(c2, vmul(p, hp))
            T1, T2 = vadd(t2, vmul(p, hp)), vsub(t2, vmul(p, hp))
            pen.line(P1, T1)
            pen.line(P2, T2)
            end = pen.line(T2, T1)
            side_ends[orient].setdefault(round(dot(t2, a) * (1 if orient == 'h' else 1), 1), (end, t2))
            if not c.get('full'):
                pen.line(vsub(c2, vmul(a, 10 * k)), vadd(t2, vmul(a, 15 * k)), 'axis')
                # размер Ø / сторона патрубка — выносные от образующих, размерная со стрелками
                xd = vadd(t2, vmul(a, 75 * k))
                for sg in (1, -1):
                    pen.line(vadd(t2, vmul(a, 5 * k), vmul(p, sg * hp)), vadd(xd, vmul(a, 8 * k), vmul(p, sg * hp)), 'thin')
                pen.line(vsub(xd, vmul(p, hp)), vadd(xd, vmul(p, hp)), 'thin')
                pen.tip(xd, vadd(xd, vmul(p, hp)))
                pen.tip(xd, vsub(xd, vmul(p, hp)))
                lab = c['label'] if c['shape'] == 'c' else fmt(2 * hp)
                tw = len(lab) * 2.5 * S * TXT_W
                pen.text_along(xd, p, lab, side=a, shift=-(tw / 2 + 3 * k) if hp > tw / 2 + 6 * k else 0.0)
                pen.arrow(vadd(t2, vmul(a, 60 * k)), vadd(t2, vmul(a, 3 * k)))
            else:
                pen.arrow(vadd(t2, vmul(a, 45 * k)), vadd(t2, vmul(a, 3 * k)))
        else:
            pen.arrow(vadd(c2, vmul(a, 45 * k)), vadd(c2, vmul(a, 3 * k)))
    # ---- размеры: каждый следующий на своей стороне — дальше от вида на 50 (1:5)
    bb = bbox(render(pen.p, S), S)
    ext = {'top': max(bb[3], hy), 'bottom': min(bb[1], -hy), 'left': min(bb[0], -hx), 'right': max(bb[2], hx)}
    used = {'top': 0, 'bottom': 0, 'left': 0, 'right': 0}

    def slot(sd):
        used[sd] += 1
        o = 50 * k * used[sd]
        return ext[sd] + o if sd in ('top', 'right') else ext[sd] - o

    def hchain(refs, sd):
        xs = sorted(refs, key=lambda r: r['a'][0])
        y = slot(sd)
        pen.dim(xs, (xs[0]['a'][0], y), (xs[-1]['a'][0], y))

    def vchain(refs, sd):
        ys = sorted(refs, key=lambda r: r['a'][1])
        x = slot(sd)
        pen.dim(ys, (x, ys[0]['a'][1]), (x, ys[-1]['a'][1]))

    if vaxes:
        hchain([bL, bR] + [vaxes[x] for x in sorted(vaxes) if abs(abs(x) - hx) > 0.5], 'top')
    hchain([bL, bR], 'top')
    if haxes:
        vchain([bB, bT] + [haxes[y] for y in sorted(haxes) if abs(abs(y) - hy) > 0.5], 'left')
    vchain([bB, bT], 'left')
    if side_ends['h']:
        hchain([bL, bR] + [e for e, t in side_ends['h'].values()], 'bottom')
    if side_ends['v']:
        vchain([bB, bT] + [e for e, t in side_ends['v'].values()], 'right')
    return pen.p


def stadium_pts(c, hw, hh, n=12):
    """Точки овала (два полукруга + прямые) с полуосями hw, hh — для заливки."""
    if hw >= hh:
        r, dx, dy = hh, hw - hh, 0.0
        arcs = (((c[0] + dx, c[1]), -math.pi / 2), ((c[0] - dx, c[1]), math.pi / 2))
    else:
        r, dx, dy = hw, 0.0, hh - hw
        arcs = (((c[0], c[1] + dy), 0.0), ((c[0], c[1] - dy), math.pi))
    pts = []
    for cc, a0 in arcs:
        for i in range(n + 1):
            a = a0 + math.pi * i / n
            pts.append((cc[0] + r * math.cos(a), cc[1] + r * math.sin(a)))
    return pts


def _stadium(pen, c, hw, hh, st):
    if abs(hw - hh) < 0.5:
        pen.circle(c, hw, st)
        return
    if hw >= hh:
        r, dx = hh, hw - hh
        pen.line((c[0] - dx, c[1] - r), (c[0] + dx, c[1] - r), st)
        pen.line((c[0] - dx, c[1] + r), (c[0] + dx, c[1] + r), st)
        pen.arc((c[0] + dx, c[1]), r, -math.pi / 2, math.pi / 2, st)
        pen.arc((c[0] - dx, c[1]), r, math.pi / 2, 3 * math.pi / 2, st)
    else:
        r, dy = hw, hh - hw
        pen.line((c[0] - r, c[1] - dy), (c[0] - r, c[1] + dy), st)
        pen.line((c[0] + r, c[1] - dy), (c[0] + r, c[1] + dy), st)
        pen.arc((c[0], c[1] + dy), r, 0.0, math.pi, st)
        pen.arc((c[0], c[1] - dy), r, math.pi, 2 * math.pi, st)


def _rect(pen, c, hw, hh, st):
    x0, x1, y0, y1 = c[0] - hw, c[0] + hw, c[1] - hh, c[1] + hh
    pen.line((x0, y0), (x1, y0), st)
    pen.line((x1, y0), (x1, y1), st)
    pen.line((x1, y1), (x0, y1), st)
    pen.line((x0, y1), (x0, y0), st)


# ------------------------------------------------------------------ аксонометрия
class _Box(object):
    def __init__(self, lo, hi, sh=0.05):
        self.lo = tuple(x + sh for x in lo)
        self.hi = tuple(x - sh for x in hi)

    def hit(self, P, e):
        t0, t1 = 1e-6, 1e18
        for i in range(3):
            if abs(e[i]) < 1e-12:
                if P[i] < self.lo[i] or P[i] > self.hi[i]:
                    return False
            else:
                a, b = (self.lo[i] - P[i]) / e[i], (self.hi[i] - P[i]) / e[i]
                if a > b:
                    a, b = b, a
                t0, t1 = max(t0, a), min(t1, b)
                if t0 > t1:
                    return False
        return True


class _Cyl(object):
    def __init__(self, c, ax, r, a0, a1, sh=0.05):
        self.c, self.ax, self.r = c, ax, r - sh
        self.a0, self.a1 = min(a0, a1) + sh, max(a0, a1) - sh

    def hit(self, P, e):
        i = self.ax
        j, k = [q for q in range(3) if q != i]
        t0, t1 = 1e-6, 1e18
        if abs(e[i]) < 1e-12:
            if P[i] < self.a0 or P[i] > self.a1:
                return False
        else:
            a, b = (self.a0 - P[i]) / e[i], (self.a1 - P[i]) / e[i]
            if a > b:
                a, b = b, a
            t0, t1 = max(t0, a), min(t1, b)
        dx, dy = P[j] - self.c[j], P[k] - self.c[k]
        A = e[j] ** 2 + e[k] ** 2
        if A < 1e-12:
            if dx * dx + dy * dy > self.r * self.r:
                return False
        else:
            B = 2 * (dx * e[j] + dy * e[k])
            C = dx * dx + dy * dy - self.r * self.r
            disc = B * B - 4 * A * C
            if disc <= 0:
                return False
            sq = math.sqrt(disc)
            t0, t1 = max(t0, (-B - sq) / (2 * A)), min(t1, (-B + sq) / (2 * A))
        return t0 < t1


def build_axo(part, basis, az, el, S):
    R0, U0, N0 = basis
    el = max(-85.0, min(85.0, el))
    a_, e_ = math.radians(az), math.radians(el)
    e3 = unit(vadd(vmul(R0, math.cos(e_) * math.sin(a_)), vmul(N0, math.cos(e_) * math.cos(a_)), vmul(U0, math.sin(e_))))
    Rx = unit(cross(U0, e3))
    Up = cross(e3, Rx)
    pen = Pen(S)
    k = pen.k
    W, D, H = part['W'], part['D'], part['H']
    hw = (W / 2, D / 2, H / 2)

    def pr(p):
        return (dot(p, Rx), dot(p, Up))

    solids = [_Box(vmul(hw, -1), hw)]
    for c in part['cuts']:
        if c['l'] <= 0.5:
            continue
        ax = [i for i in range(3) if abs(c['n3'][i]) > 0.5][0]
        if c['shape'] == 'c':
            solids.append(_Cyl(c['o3'], ax, c['d'] / 2.0, c['o3'][ax], c['t3'][ax]))
        else:
            ext = vadd(vmul(c['u3'], c['w'] / 2.0), vmul(c['v3'], c['h'] / 2.0))
            pts = [vadd(c['o3'], ext), vsub(c['o3'], ext), vadd(c['t3'], ext), vsub(c['t3'], ext)]
            solids.append(_Box(tuple(min(q[i] for q in pts) for i in range(3)), tuple(max(q[i] for q in pts) for i in range(3))))
    eps = 0.3

    def visible(P):
        Q = vadd(P, vmul(e3, eps))
        for s in solids:
            if s.hit(Q, e3):
                return False
        return True

    step = 1.5 * S
    pts_all = []

    def edge(A, B):
        """Ребро 3D → видимые (толстые) и невидимые (штрих) куски."""
        L = vlen(vsub(B, A))
        if L < 0.5:
            return
        pts_all.extend([A, B])
        n = max(1, int(L / step))
        st = [visible(vadd(A, vmul(vsub(B, A), (i + 0.5) / n))) for i in range(n)]
        i = 0
        while i < n:
            j = i
            while j + 1 < n and st[j + 1] == st[i]:
                j += 1
            t0, t1 = i / n, (j + 1) / n
            P0, P1 = vadd(A, vmul(vsub(B, A), t0)), vadd(A, vmul(vsub(B, A), t1))
            if vlen(vsub(P1, P0)) * 1.0 > 0.9:
                pen.line(pr(P0), pr(P1), 'thick' if st[i] else 'hidden')
            i = j + 1

    # короб
    for ax in range(3):
        o1, o2 = (ax + 1) % 3, (ax + 2) % 3
        for s1 in (-1, 1):
            for s2 in (-1, 1):
                A = [0, 0, 0]
                A[o1], A[o2] = s1 * hw[o1], s2 * hw[o2]
                B = list(A)
                A[ax], B[ax] = -hw[ax], hw[ax]
                edge(tuple(A), tuple(B))

    def ring(c, cut, nseg):
        if cut['shape'] == 'c':
            r = cut['d'] / 2.0
            return [vadd(c, vmul(cut['u3'], r * math.cos(2 * math.pi * q / nseg)), vmul(cut['v3'], r * math.sin(2 * math.pi * q / nseg)))
                    for q in range(nseg)]
        a, b = cut['w'] / 2.0, cut['h'] / 2.0
        if cut['shape'] == 'o':
            return [vadd(c, vmul(cut['u3'], x), vmul(cut['v3'], y)) for x, y in stadium_pts((0, 0), a, b, max(6, nseg // 2))]
        return [vadd(c, vmul(cut['u3'], x), vmul(cut['v3'], y)) for x, y in ((-a, -b), (a, -b), (a, b), (-a, b))]

    fills = []
    for c in part['cuts']:
        nseg = max(24, min(72, int(math.pi * max(c['w'], c['h']) / (3 * S)))) if c['shape'] in ('c', 'o') else 4
        tip = ring(c['t3'], c, nseg)
        for q in range(len(tip)):
            edge(tip[q], tip[(q + 1) % len(tip)])
        if c['l'] > 0.5:
            if not c.get('full'):
                base = ring(c['o3'], c, nseg)
                for q in range(len(base)):
                    edge(base[q], base[(q + 1) % len(base)])
            if c['shape'] == 'r':
                for q in range(4):
                    edge(vadd(c['o3'], vsub(tip[q], c['t3'])), tip[q])
            else:
                axp = vsub(pr(c['t3']), pr(c['o3']))
                if vlen(axp) > 0.5:
                    pp = unit(perp(unit(axp)))
                    best = [None, None, -1e18, 1e18]
                    if c['shape'] == 'c':
                        r = c['d'] / 2.0
                        offs = [vadd(vmul(c['u3'], r * math.cos(2 * math.pi * q / 720)), vmul(c['v3'], r * math.sin(2 * math.pi * q / 720)))
                                for q in range(720)]
                    else:
                        offs = [vadd(vmul(c['u3'], x), vmul(c['v3'], y)) for x, y in stadium_pts((0, 0), c['w'] / 2.0, c['h'] / 2.0, 180)]
                    for off in offs:
                        d = dot(pr(off), pp)
                        if d > best[2]:
                            best[0], best[2] = off, d
                        if d < best[3]:
                            best[1], best[3] = off, d
                    for off in best[:2]:
                        edge(vadd(c['o3'], off), vadd(c['t3'], off))
        if dot(c['n3'], e3) > 0.05 and visible(c['t3']):
            fills.append([pr(q) for q in tip])
    for f in fills:
        pen.p.insert(0, {'t': 'fill', 'pts': f, 'c': None, 'r': None, 'kind': 'gray'})

    # ---- размеры по ГОСТ 2.317: выносные ∥ оси, размерная ∥ ребру, засечки, текст вдоль
    hull = _hull([pr(q) for q in pts_all])
    used = []

    def outside(q):
        return not _in_hull(hull, q)

    def idim(A, B, dr, off, s):
        A1, B1 = vadd(A, vmul(dr, off)), vadd(B, vmul(dr, off))
        pen.line(pr(vadd(A, vmul(dr, 8 * k))), pr(vadd(A1, vmul(dr, 12 * k))), 'thin')
        pen.line(pr(vadd(B, vmul(dr, 8 * k))), pr(vadd(B1, vmul(dr, 12 * k))), 'thin')
        a, b = pr(A1), pr(B1)
        u = unit(vsub(b, a))
        pen.line(vsub(a, vmul(u, 15 * k)), vadd(b, vmul(u, 15 * k)), 'thin')
        t = vmul(unit(vadd(u, unit(vsub(pr(vadd(A1, vmul(dr, 10))), a)))), 9 * k)
        pen.line(vsub(a, t), vadd(a, t), 'thin')
        pen.line(vsub(b, t), vadd(b, t), 'thin')
        side = vsub(pr(vadd(A1, dr)), a)            # текст — снаружи (по направлению выноса)
        pen.text_along(vmul(vadd(a, b), 0.5), u, s, side=side, gap=1.0)
        used.append(vmul(vadd(a, b), 0.5))

    def place(cands, s):
        """cands: (A, B, dir). Наименьший вынос, при котором размерная целиком вне контура изделия."""
        best = None
        for A, B, dr in cands:
            pl = vlen(pr(dr))
            if pl < 0.35:
                continue
            for off in [x * k for x in (50, 70, 90, 120, 150, 190, 240)]:
                ok = all(outside(pr(vadd(vadd(A, vmul(vsub(B, A), f)), vmul(dr, off)))) for f in (0, 0.25, 0.5, 0.75, 1))
                if ok:
                    mid = pr(vadd(vmul(vadd(A, B), 0.5), vmul(dr, off)))
                    near = sum(1 for q in used if vlen(vsub(q, mid)) < 60 * k)
                    sc = off * pl / pl + near * 1000 + (0 if dot(dr, e3) <= 0.05 else 30 * k) - (dot(pr(dr), (0, -1)) > 0.5) * 5 * k
                    if best is None or sc < best[0]:
                        best = (sc, A, B, dr, off)
                    break
        if best:
            idim(best[1], best[2], best[3], best[4], s)

    E = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    for ax in (2, 0, 1):                         # высота, ширина, глубина
        o1, o2 = (ax + 1) % 3, (ax + 2) % 3
        cands = []
        for s1 in (-1, 1):
            for s2 in (-1, 1):
                d1, d2 = vmul(E[o1], s1), vmul(E[o2], s2)
                if dot(d1, e3) <= 0 and dot(d2, e3) <= 0:
                    continue                     # ребро не видно
                A = [0, 0, 0]
                A[o1], A[o2] = s1 * hw[o1], s2 * hw[o2]
                B = list(A)
                A[ax], B[ax] = -hw[ax], hw[ax]
                for dr in (d1, d2):
                    cands.append((tuple(A), tuple(B), dr))
        place(cands, fmt(2 * hw[ax]))
    done = set()
    noz = []
    for c in part['cuts']:
        if c['l'] > 0.5:
            r_ = max(c['w'], c['h']) / 2.0
            noz.append((c, _hull([pr(vadd(q, vmul(d_, s_ * r_))) for q in (c['o3'], c['t3'])
                                  for d_ in (c['u3'], c['v3']) for s_ in (1, -1)])))
    for c in part['cuts']:
        if c['l'] <= 0.5:
            continue
        kk = (c['face'], round(c['l'], 1))
        if kk in done or (dot(c['n3'], e3) < -0.05 and not visible(c['t3'])):
            continue
        done.add(kk)
        best = None
        for vec, hs in ((c['u3'], c['w'] / 2.0), (c['v3'], c['h'] / 2.0)):
            for sg in (1, -1):
                dr = vmul(vec, sg)
                if vlen(pr(dr)) < 0.35:
                    continue
                g = vmul(dr, hs)
                A, B = vadd(c['o3'], g), vadd(c['t3'], g)
                off = 35 * k
                mid = pr(vadd(vmul(vadd(A, B), 0.5), vmul(dr, off)))
                hit = sum(1 for c2, h2 in noz if c2 is not c and _in_hull(h2, mid))
                near = sum(1 for q in used if vlen(vsub(q, mid)) < 50 * k)
                sc = hit * 10 + near * 5 - unit(pr(dr))[1] - 0.5 * dot(unit(pr(dr)), unit(vsub(pr(c['o3']), (0, 0))))
                if best is None or sc < best[0]:
                    best = (sc, A, B, dr, off)
        if best:
            idim(best[1], best[2], best[3], best[4], fmt(c['l']))
    # ---- Ø и a×b врезок: линия под 45° в плоскости торца, стрелки на контуре, вынос и полка
    ctr = vmul(vadd(*[pr(q) for q in pts_all]), 1.0 / max(1, len(pts_all)))
    vis_cuts = [c for c in part['cuts'] if not c.get('full') and dot(c['n3'], e3) > 0.1 and visible(c['t3'])]
    nlab = {}
    for c in vis_cuts:
        nlab[c['label']] = nlab.get(c['label'], 0) + 1
    done_lab = set()
    for c in vis_cuts:
        if c['label'] in done_lab:
            continue
        done_lab.add(c['label'])
        tc = pr(c['t3'])
        out_dir = unit(vsub(tc, ctr)) if vlen(vsub(tc, ctr)) > 1 else (1, 0)
        best = None
        for su in (1, -1):
            for sv in (1, -1):
                d3 = unit(vadd(vmul(c['u3'], su), vmul(c['v3'], sv)))
                d2 = pr(d3)
                sc = dot(unit(d2), out_dir) + 0.3 * (d2[1] > 0)
                if best is None or sc > best[0]:
                    best = (sc, d3)
        d3 = best[1]
        if c['shape'] == 'c':
            r = c['d'] / 2.0
            A, Bp = pr(vsub(c['t3'], vmul(d3, r))), pr(vadd(c['t3'], vmul(d3, r)))
            u = unit(vsub(Bp, A))
            Ee = vadd(Bp, vmul(u, 40 * k))
            pen.line(A, Ee, 'thin')
            pen.tip(tc, Bp)
            pen.tip(tc, A)
        else:
            Bp = pr(vadd(c['t3'], vmul(c['u3'], c['w'] / 4.0 * (1 if dot(pr(c['u3']), out_dir) >= 0 else -1)),
                         vmul(c['v3'], c['h'] / 4.0 * (1 if dot(pr(c['v3']), out_dir) >= 0 else -1))))
            u = unit(pr(d3))
            Ee = vadd(Bp, vmul(u, 40 * k + max(c['w'], c['h']) * 0.35))
            pen.line(Bp, Ee, 'thin')
            pen.dot_end(Bp)
        lab = (u'%d отв. ' % nlab[c['label']] if nlab[c['label']] > 1 else u'') + c['label']
        sw = max(60 * k, len(lab) * 2.5 * S * TXT_W + 6 * k)
        sg = 1 if u[0] >= 0 else -1
        pen.line(Ee, (Ee[0] + sg * sw, Ee[1]), 'thin')
        pen.text((Ee[0] + sg * sw / 2.0, Ee[1] + 1.2 * S + 2.5 * S), lab, al='c')
    return pen.p


def _hull(pts):
    pts = sorted(set((round(p[0], 3), round(p[1], 3)) for p in pts))
    if len(pts) < 3:
        return pts

    def cr(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cr(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def _in_hull(h, q, margin=0.0):
    if len(h) < 3:
        return False
    for i in range(len(h)):
        a, b = h[i], h[(i + 1) % len(h)]
        if (b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0]) < -margin:
            return False
    return True


# ------------------------------------------------------------------ эскиз целиком
def tt_lines(part):
    out = []
    mat = part['mat'] or u'сталь тонколистовая оцинкованная'
    wall = part.get('wall')
    out.append(u'Материал — %s%s.' % (mat, (u', t = %s мм' % fmt(wall)) if wall else u''))
    out.append(u'Соединения — фальц / заклёпки, швы загерметизировать.')
    if any(c['shape'] == 'c' and c['l'] > 0.5 for c in part['cuts']):
        out.append(u'Патрубки — ниппельные.')
    ov = sorted(set(c['label'] for c in part['cuts'] if c['shape'] == 'o' and c['l'] > 0.5))
    if ov:
        out.append(u'Патрубки овальные: ' + u', '.join(ov) + u'.')
    holes = [c for c in part['cuts'] if not c.get('full') and c['l'] <= 0.5]
    if holes:
        out.append(u'Проёмы в стенках: ' + u'; '.join(u'%s — %s' % (FACE_LOW[c['face']], c.get('note') or c['label'])
                                                     for c in holes) + u'.')
    out.append(u'Неуказанные предельные отклонения ±2 мм.')
    out.append(u'Отверстия (проходные) показаны заливкой и стрелками.')
    full = [c for c in part['cuts'] if c.get('full') and c['l'] <= 0.5]
    if full:
        out.append(u'Открыто во всю грань: ' + u'; '.join(u'%s — проём %s' % (FACE_LOW[c['face']], c['label']) for c in full) + u'.')
    for n in part.get('notes') or []:
        out.append(n)
    if part.get('count'):
        out.append(u'Количество — %d шт.' % part['count'])
    return u'Технические требования\n' + u'\n'.join(u'%d. %s' % (i + 1, s) for i, s in enumerate(out))


def build_sketch(part, opt, S):
    """Эскиз одного изделия: примитивы в координатах легенды (мм модели), левый верх блока ≈ (0, 0)."""
    o = dict(DEFAULT_OPT)
    o.update(opt or {})
    face = o['face'] or default_face(part)
    R, U, N = main_basis(face, o['rot'])
    k = S / 5.0
    G = 24.0 * S
    views = []
    for key, title, col, row in VIEWS:
        if key != 'main' and key not in o['views']:
            continue
        pr_ = build_ortho(part, key, view_basis(key, R, U, N), S)
        views.append({'key': key, 'title': title, 'col': col, 'row': row, 'p': pr_, 'bb': bbox(render(pr_, S), S)})
    # столбцы / строки: центры коробов на одной вертикали / горизонтали (проекционная связь)
    cols, rows = {}, {}
    for v in views:
        l_, b_, r_, t_ = v['bb']
        cl = cols.setdefault(v['col'], [0.0, 0.0])
        cl[0], cl[1] = max(cl[0], -l_), max(cl[1], r_)
        rw = rows.setdefault(v['row'], [0.0, 0.0])
        rw[0], rw[1] = max(rw[0], t_ + 9 * S), max(rw[1], -b_)          # сверху — место под заголовок
    cx, x = {}, 0.0
    for c in sorted(cols):
        cx[c] = x + cols[c][0]
        x = cx[c] + cols[c][1] + G
    cy, y = {}, 0.0
    for r in sorted(rows):
        cy[r] = y - rows[r][0]
        y = cy[r] - rows[r][1] - G
    prims = []
    for v in views:
        shift(v['p'], cx[v['col']], cy[v['row']])
        prims += v['p']
    # заголовки видов — над видами, у видов одного ряда — на одной линии
    for r in rows:
        top = max(v['bb'][3] + cy[r] for v in views if v['row'] == r)
        for v in views:
            if v['row'] == r:
                prims.append({'t': 'text', 'pos': (cx[v['col']], top + 9 * S), 's': v['title'], 'rot': 0.0, 'h': 2.5, 'al': 'c'})
    gb = bbox(render(prims, S), S)
    if o['axo']:
        ap = build_axo(part, (R, U, N), o['az'], o['el'], S)
        ab = bbox(render(ap, S), S)
        main_row = [v for v in views if v['row'] == 1]
        if o['axo_pos'] == 'top' and main_row:
            ty = cy[1]
            dy = ty - (ab[1] + ab[3]) / 2.0
        else:
            dy = (gb[1] + gb[3]) / 2.0 - (ab[1] + ab[3]) / 2.0
        dx = gb[2] + G - ab[0]
        shift(ap, dx, dy)
        prims += ap
        top_axo = ab[3] + dy
        prims.append({'t': 'text', 'pos': ((ab[0] + ab[2]) / 2.0 + dx, top_axo + 9 * S), 's': u'Аксонометрия',
                      'rot': 0.0, 'h': 2.5, 'al': 'c'})
    gb = bbox(render(prims, S), S)
    head = part['family'] + (u' : ' + part['type'] if part['type'] else u'')
    prims.append({'t': 'text', 'pos': (gb[0], gb[3] + 12 * S), 's': head, 'rot': 0.0, 'h': 3.5, 'al': 'l'})
    prims.append({'t': 'text', 'pos': (gb[0], gb[1] - 10 * S), 's': tt_lines(part), 'rot': 0.0, 'h': 2.5, 'al': 'l'})
    gb = bbox(render(prims, S), S)
    shift(prims, -gb[0], -gb[3])
    return prims


def build_many(items, S, gap_mm=30.0):
    """Несколько эскизов друг под другом (сводная легенда). items: [(part, opt)]."""
    out = []
    y = 0.0
    for part, opt in items:
        p = build_sketch(part, opt, S)
        shift(p, 0, y)
        out += p
        b = bbox(render(p, S), S)
        y = b[1] - gap_mm * S
    return out


# ================================================================== Revit
def nm(e):
    """Имя элемента. У типов (FamilySymbol, DimensionType …) в IronPython свойство .Name скрыто — AttributeError."""
    from Autodesk.Revit.DB import Element
    return Element.Name.GetValue(e)


def _mm(p):
    if p is None or not p.HasValue:
        return None
    try:
        return p.AsDouble() * 304.8
    except Exception:
        return None


def part_of_symbol(sym, doc=None):
    def gs(n):
        p = sym.LookupParameter(n)
        if p is None or not p.HasValue:
            return None
        try:
            return p.AsString() if p.AsString() is not None else p.AsValueString()
        except Exception:
            return None

    def gm(n):
        p = sym.LookupParameter(n)
        if p is None:
            return None
        try:
            from Autodesk.Revit.DB import StorageType
            if p.StorageType != StorageType.Double:
                return num(p.AsString() or p.AsValueString(), None)
        except Exception:
            pass
        return _mm(p)
    part = part_from(gs, gm, sym.FamilyName, nm(sym))
    if part and doc is not None:
        from Autodesk.Revit.DB import FilteredElementCollector, FamilyInstance
        part['count'] = sum(1 for e in FilteredElementCollector(doc).OfClass(FamilyInstance)
                            if e.Symbol is not None and e.Symbol.Id == sym.Id)
    return part


def _finish(p):
    """Короб W×D×H по центру + врезки: o3/t3/u3/v3/n3 и признак «во всю грань»."""
    for c in p['cuts']:
        su, sv = face_size(c['face'], p['W'], p['D'], p['H'])
        c['full'] = c['shape'] == 'r' and abs(c['w'] - su) < 0.6 and abs(c['h'] - sv) < 0.6
        o, u, v, n = face_frame(c['face'], p['W'], p['D'], p['H'])
        c['o3'] = vadd(o, vmul(u, c['u']), vmul(v, c['v']))
        c['t3'] = vadd(c['o3'], vmul(n, c['l']))
        c['u3'], c['v3'], c['n3'] = u, v, n
    return p


FACE_OF = {(0, -1): u'Слева', (0, 1): u'Справа', (1, -1): u'Спереди', (1, 1): u'Сзади', (2, 1): u'Сверху', (2, -1): u'Снизу'}


def part_from_boxes(boxes, conns, tol=2.0):
    """Изделие по геометрии семейства (КСД и др. без параметров врезок).
    boxes: [(min, max, n_цилиндрических_граней)] в мм в координатах семейства; conns: [(точка, d или None)].
    Корпус — тело с наибольшим габаритом; остальные тела, примыкающие к его граням, — патрубки."""
    if not boxes:
        return None
    vol = lambda b: (b[1][0] - b[0][0]) * (b[1][1] - b[0][1]) * (b[1][2] - b[0][2])
    boxes = [(tuple(round(x) for x in b[0]), tuple(round(x) for x in b[1])) + tuple(b[2:]) for b in boxes]   # до 1 мм
    body = max(boxes, key=vol)
    approx = []
    if (len(body) > 3 and body[3]) or body[2]:
        approx.append(u'корпус не прямоугольный — показан габаритом')
    lo, hi = body[0], body[1]
    ctr = tuple((lo[i] + hi[i]) / 2.0 for i in range(3))
    W, D, H = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
    cuts = []
    for b in boxes:
        if b is body:
            continue
        side = None
        for ax in range(3):
            if b[0][ax] >= hi[ax] - tol:
                side = (ax, 1)
            elif b[1][ax] <= lo[ax] + tol:
                side = (ax, -1)
        if side is None:
            if any(b[0][i] < lo[i] - tol or b[1][i] > hi[i] + tol for i in range(3)) and vol(b) > 1000.0:
                approx.append(u'есть детали вне корпуса, не примыкающие к граням')
            continue                                     # внутри корпуса — не патрубок
        if b[1][side[0]] - b[0][side[0]] < 5.0:
            continue                                     # пластина / накладка, не патрубок
        face = FACE_OF[side]
        bc = tuple((b[0][i] + b[1][i]) / 2.0 - ctr[i] for i in range(3))
        ext = tuple(b[1][i] - b[0][i] for i in range(3))
        o, u, v, n = face_frame(face, W, D, H)
        w_ = sum(abs(u[i]) * ext[i] for i in range(3))
        h_ = sum(abs(v[i]) * ext[i] for i in range(3))
        l_ = sum(abs(n[i]) * ext[i] for i in range(3))
        w_, h_, l_ = round(w_), round(h_), round(l_)
        base = vsub(bc, vmul(n, l_ / 2.0))
        cu, cv = round(dot(vsub(base, o), u), 1), round(dot(vsub(base, o), v), 1)
        if len(b) > 3 and b[3]:
            approx.append(u'патрубок сложной формы (конус, отвод, скос)')
        if b[2] == 0:
            shape = 'r'
        elif abs(w_ - h_) < 1.0:
            shape = 'c'
        else:
            shape = 'o'
        d = None
        tip = vadd(base, vmul(n, l_))
        for q, dd in conns:
            qq = vsub(q, ctr)
            if dd and vlen(vsub(qq, tip)) < max(w_, h_) / 2.0 + tol:
                d = dd
        c = {'face': face, 'role': u'', 'u': cu, 'v': cv, 'l': l_, 'shape': shape, 'w': w_, 'h': h_, 'd': w_}
        if shape == 'c':
            c['label'] = u'Ø' + fmt(w_)
        elif shape == 'o':
            c['label'] = u'овал %s×%s' % (fmt(w_), fmt(h_)) + (u' (Ø%s)' % fmt(d) if d else u'')
        else:
            c['label'] = u'%s×%s' % (fmt(w_), fmt(h_))
        cuts.append(c)
    ap = []
    for a in approx:
        if a not in ap:
            ap.append(a)
    return {'W': W, 'D': D, 'H': H, 'OW': W, 'OD': D, 'OH': H, 'cuts': cuts, 'ctr': ctr, 'notes': [], 'approx': ap}


def _ksd_slot(p, slot_w, gaps=0.0):
    """Щель КСД — проём во всю длину снизу корпуса шириной slot_w (параметр 2_w)."""
    if not slot_w or slot_w <= 0 or slot_w > p['D'] + 0.5:
        return
    c = {'face': u'Снизу', 'role': u'', 'u': p['W'] / 2.0, 'v': p['D'] / 2.0, 'l': 0.0, 'shape': 'r',
         'w': p['W'] - 2 * gaps, 'h': slot_w, 'd': slot_w}
    c['label'] = u'%s×%s' % (fmt(c['w']), fmt(slot_w))
    c['note'] = u'щель %s×%s под решётку' % (fmt(slot_w), fmt(c['w']))
    p['cuts'].append(c)


def geo_instance(fi):
    """Тела (габариты, число цилиндрических граней) и соединители экземпляра в мм координат семейства."""
    from Autodesk.Revit.DB import Options, ViewDetailLevel, Solid, GeometryInstance, CylindricalFace, PlanarFace, XYZ
    inv = fi.GetTransform().Inverse
    opt = Options()
    opt.DetailLevel = ViewDetailLevel.Fine
    boxes = []

    def walk(ge):
        for go in ge:
            if isinstance(go, GeometryInstance):
                walk(go.GetInstanceGeometry())
                continue
            if not isinstance(go, Solid) or go.Volume < 1e-9:
                continue
            bb = go.GetBoundingBox()
            pts = []
            for x in (bb.Min.X, bb.Max.X):
                for y in (bb.Min.Y, bb.Max.Y):
                    for z in (bb.Min.Z, bb.Max.Z):
                        q = inv.OfPoint(bb.Transform.OfPoint(XYZ(x, y, z)))
                        pts.append((q.X * 304.8, q.Y * 304.8, q.Z * 304.8))
            ncyl, bad = 0, 0
            for f in go.Faces:
                if isinstance(f, PlanarFace):
                    v = inv.OfVector(f.FaceNormal)
                elif isinstance(f, CylindricalFace):
                    ncyl += 1
                    v = inv.OfVector(f.Axis)
                else:
                    bad += 1
                    continue
                if max(abs(v.X), abs(v.Y), abs(v.Z)) < 0.999:   # грань не по осям семейства
                    bad += 1
            boxes.append((tuple(min(q[i] for q in pts) for i in range(3)), tuple(max(q[i] for q in pts) for i in range(3)), ncyl, bad))
    walk(fi.get_Geometry(opt))
    conns = []
    try:
        for c in fi.MEPModel.ConnectorManager.Connectors:
            q = inv.OfPoint(c.Origin)
            d = None
            try:
                d = round(c.Radius * 2 * 304.8, 1)
            except Exception:
                pass
            conns.append(((q.X * 304.8, q.Y * 304.8, q.Z * 304.8), d))
    except Exception:
        pass
    return boxes, conns


def _pmm(el, name):
    p = el.LookupParameter(name)
    if p is None and hasattr(el, 'Symbol') and el.Symbol is not None:
        p = el.Symbol.LookupParameter(name)
    if p is None or not p.HasValue:
        return None
    try:
        return p.AsDouble() * 304.8
    except Exception:
        return None


def geo_items(doc, ids):
    """Любые семейства (выделенные экземпляры, кроме пленумов с db_В*): корпус и патрубки по геометрии,
    у КСД (параметр 2_w) — щель снизу. Тело не «короб с патрубками» → пометка «приблизительно».
    Экземпляры с одинаковой геометрией — одна позиция. → [{'part', 'sym', 'ids', 'key', 'label'}]"""
    from Autodesk.Revit.DB import FamilyInstance, ElementId
    groups = {}
    for i in ids:
        fi = doc.GetElement(ElementId(i)) if not hasattr(i, 'IntegerValue') else doc.GetElement(i)
        if not isinstance(fi, FamilyInstance):
            continue
        s = fi.Symbol
        if s is None or s.LookupParameter(u'db_В1_Грань') is not None:
            continue
        boxes, conns = geo_instance(fi)
        p = part_from_boxes(boxes, conns)
        if p is None:
            continue
        nf = sum(1 for c in p['cuts'] if c['face'] == u'Спереди')
        if nf > sum(1 for c in p['cuts'] if c['face'] == u'Сзади'):
            # зеркальный экземпляр — то же изделие: приводим патрубки на заднюю грань
            mir = lambda q: (-q[0], -q[1], q[2])
            boxes = [(mir(b[1]), mir(b[0])) + tuple(b[2:]) for b in boxes]
            boxes = [(tuple(min(b[0][i], b[1][i]) for i in range(3)), tuple(max(b[0][i], b[1][i]) for i in range(3))) + tuple(b[2:])
                     for b in boxes]
            conns = [(mir(q), d) for q, d in conns]
            p = part_from_boxes(boxes, conns)
        slot = _pmm(fi, u'2_w')
        _ksd_slot(p, slot)
        sig = (s.Id.IntegerValue, round(p['W']), round(p['D']), round(p['H']),
               tuple(sorted((c['face'], round(c['u']), round(c['v']), round(c['w']), round(c['h']), round(c['l'])) for c in p['cuts'])))
        g = groups.get(sig)
        if g is None:
            p['family'], p['type'] = s.FamilyName, u'%s, %s×%s×%s' % (nm(s), fmt(p['W']), fmt(p['D']), fmt(p['H']))
            gs = lambda n: (s.LookupParameter(n).AsString() if s.LookupParameter(n) is not None else None)
            p['mat'] = gs(u'ADSK_Материал наименование') or u''
            p['wall'] = _pmm(fi, u'ADSK_Толщина стенки')
            p['code'] = u'%s|%s' % (s.FamilyName, p['type'])
            p['count'] = 0
            p['notes'].append(u'Корпус и патрубки — по геометрии семейства' + (u'; решётка — по каталогу производителя.' if slot else u'.'))
            if p['approx']:
                p['notes'].append(u'⚠ Эскиз приблизительный (%s) — сверить с семейством.' % u', '.join(p['approx']))
            _finish(p)
            g = groups[sig] = {'part': p, 'sym': s, 'ids': [], 'key': p['code']}
        g['ids'].append(fi.Id.IntegerValue)
        g['part']['count'] += 1
    out = list(groups.values())
    for g in out:
        g['label'] = (u'⚠ приблизительно · ' if g['part']['approx'] else u'') + \
            u'%s : %s  (%d шт.)' % (g['sym'].FamilyName, g['part']['type'], g['part']['count'])
    out.sort(key=lambda g: g['label'])
    return out


def candidates(doc):
    """Типоразмеры семейств с параметрами врезок (db_В1_Грань) — что умеем эскизировать."""
    from Autodesk.Revit.DB import FilteredElementCollector, FamilySymbol
    out = []
    for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
        if s.LookupParameter(u'db_В1_Грань') is None:
            continue
        out.append(s)
    out.sort(key=lambda s: (s.FamilyName, nm(s)))
    return out


def styles(doc):
    """Стили и типы с запасными вариантами (на другом ПК имён ADSK/db может не быть)."""
    from Autodesk.Revit.DB import (BuiltInCategory, GraphicsStyleType, FilteredElementCollector, DimensionType,
                                   TextNoteType, FilledRegionType, FillPatternElement, FillPatternTarget, Color,
                                   DimensionStyleType)
    cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
    subs = dict((c.Name, c) for c in cat.SubCategories)

    def gs(*names):
        for n in names:
            if n in subs:
                return subs[n].GetGraphicsStyle(GraphicsStyleType.Projection)
        return cat.GetGraphicsStyle(GraphicsStyleType.Projection)
    st = {'thick': gs(u'ADSK_Основная толстая', u'<Широкие линии>', u'Широкие линии'),
          'thin': gs(u'ADSK_Основная тонкая', u'<Тонкие линии>', u'Тонкие линии'),
          'axis': gs(u'<Осевая линия>', u'Осевая линия', u'<Осевые линии>', u'ADSK_Основная тонкая', u'<Тонкие линии>'),
          'hidden': gs(u'db_Скрытые линии', u'<Невидимые линии>', u'<Скрытые линии>', u'ADSK_Основная тонкая')}
    dts = [d for d in FilteredElementCollector(doc).OfClass(DimensionType)
           if d.StyleType == DimensionStyleType.Linear]
    st['dim'] = ([d for d in dts if nm(d) == u'ADSK_Основной_2.5 мм'] or [d for d in dts if u'2.5' in nm(d) and u'кругл' not in nm(d).lower()] or dts)[0]
    tts = list(FilteredElementCollector(doc).OfClass(TextNoteType))

    def tt(name, h):
        for t in tts:
            if nm(t) == name:
                return t.Id
        for t in tts:
            if u'%s' % h in nm(t):
                return t.Id
        return tts[0].Id
    st['t2.5'] = tt(u'ADSK_Основной текст_2.5', u'2.5')
    st['t3.5'] = tt(u'ADSK_Основной текст_3.5', u'3.5')
    frs = list(FilteredElementCollector(doc).OfClass(FilledRegionType))
    byname = dict((nm(f), f) for f in frs)
    gray = byname.get(u'ADSK_У_Сплошная_Серый арматура')
    if gray is None:
        gray = [f for f in frs if u'сер' in nm(f).lower() and u'сплош' in nm(f).lower()]
        gray = gray[0] if gray else frs[0]
    st['gray'] = gray.Id
    arr = byname.get(u'db_Эскиз_Стрелка')
    if arr is None:                   # чёрная сплошная — создаём (в транзакции вызывающего)
        solid = [p for p in FilteredElementCollector(doc).OfClass(FillPatternElement)
                 if p.GetFillPattern().IsSolidFill and p.GetFillPattern().Target == FillPatternTarget.Drafting]
        arr = gray.Duplicate(u'db_Эскиз_Стрелка')
        if solid:
            arr.ForegroundPatternId = solid[0].Id
        arr.ForegroundPatternColor = Color(0, 0, 0)
    st['arrow'] = arr.Id
    return st


def legend_source(doc):
    from Autodesk.Revit.DB import FilteredElementCollector, View, ViewType
    for v in FilteredElementCollector(doc).OfClass(View):
        if v.ViewType == ViewType.Legend and not v.IsTemplate:
            return v
    return None


def find_legend(doc, name):
    from Autodesk.Revit.DB import FilteredElementCollector, View, ViewType
    for v in FilteredElementCollector(doc).OfClass(View):
        if v.ViewType == ViewType.Legend and not v.IsTemplate and v.Name == name:
            return v
    return None


def legend_for(doc, name, scale):
    """Есть легенда с таким именем — очищаем; нет — копия любой легенды без содержимого (API не создаёт с нуля)."""
    from Autodesk.Revit.DB import FilteredElementCollector, ViewDuplicateOption, ElementId
    from System.Collections.Generic import List
    lg = find_legend(doc, name)
    if lg is None:
        src = legend_source(doc)
        if src is None:
            raise Exception(u'В модели нет ни одной легенды. Создайте любую (Вид → Легенды) — API Revit не умеет '
                            u'создавать легенду с нуля, только копировать.')
        lg = doc.GetElement(src.Duplicate(ViewDuplicateOption.Duplicate))
        lg.Name = name
        # копия «без детализации» может унести компоненты легенды — чистим ниже так же
    ids = [e.Id for e in FilteredElementCollector(doc, lg.Id).WhereElementIsNotElementType()
           if e.Category is not None and e.Id != lg.Id]
    if ids:
        doc.Delete(List[ElementId](ids))
    try:
        lg.Scale = int(scale) if float(scale).is_integer() else lg.Scale
    except Exception:
        pass
    return lg


def emit(doc, view, prims, st):
    """Примитивы → элементы Revit в виде (легенде). Ошибка отдельного элемента не роняет весь эскиз."""
    from Autodesk.Revit.DB import (XYZ, Line, Arc, Plane, CurveLoop, FilledRegion, ReferenceArray, TextNote,
                                   TextNoteOptions, HorizontalTextAlignment)
    from System.Collections.Generic import List
    F = 1.0 / 304.8

    def P(q):
        return XYZ(q[0] * F, q[1] * F, 0)
    refs = {}
    errs = []
    for p in prims:
        t = p['t']
        try:
            if t == 'line':
                if vlen(vsub(p['a'], p['b'])) < 1.0:
                    continue
                dc = doc.Create.NewDetailCurve(view, Line.CreateBound(P(p['a']), P(p['b'])))
                dc.LineStyle = st[p['st']]
                refs[id(p)] = dc.GeometryCurve.Reference
            elif t == 'circle':
                arc = Arc.Create(Plane.CreateByNormalAndOrigin(XYZ.BasisZ, P(p['c'])), p['r'] * F, 0, 2 * math.pi)
                dc = doc.Create.NewDetailCurve(view, arc)
                dc.LineStyle = st[p['st']]
            elif t == 'arc':
                arc = Arc.Create(P(p['c']), p['r'] * F, p['a0'], p['a1'], XYZ.BasisX, XYZ.BasisY)
                dc = doc.Create.NewDetailCurve(view, arc)
                dc.LineStyle = st[p['st']]
            elif t == 'fill':
                lp = CurveLoop()
                if p['pts']:
                    q = p['pts']
                    for i in range(len(q)):
                        a, b = q[i], q[(i + 1) % len(q)]
                        if vlen(vsub(a, b)) > 0.3:
                            lp.Append(Line.CreateBound(P(a), P(b)))
                else:
                    c, r = p['c'], p['r']
                    lp.Append(Arc.Create(P((c[0] - r, c[1])), P((c[0] + r, c[1])), P((c[0], c[1] + r))))
                    lp.Append(Arc.Create(P((c[0] + r, c[1])), P((c[0] - r, c[1])), P((c[0], c[1] - r))))
                FilledRegion.Create(doc, st[p['kind']], view.Id, List[CurveLoop]([lp]))
            elif t == 'text':
                o = TextNoteOptions(st['t3.5'] if p['h'] > 3 else st['t2.5'])
                o.Rotation = math.radians(p['rot'])
                o.HorizontalAlignment = HorizontalTextAlignment.Center if p['al'] == 'c' else (
                    HorizontalTextAlignment.Right if p['al'] == 'r' else HorizontalTextAlignment.Left)
                TextNote.Create(doc, view.Id, P(p['pos']), p['s'], o)
        except Exception as ex:
            errs.append(u'%s: %s' % (t, ex))
    for p in prims:
        if p['t'] != 'dim':
            continue
        try:
            ra = ReferenceArray()
            n = 0
            for r in p['refs']:
                if id(r) in refs:
                    ra.Append(refs[id(r)])
                    n += 1
            if n >= 2:
                doc.Create.NewDimension(view, Line.CreateBound(P(p['a']), P(p['b'])), ra, st['dim'])
        except Exception as ex:
            errs.append(u'dim: %s' % ex)
    return errs
