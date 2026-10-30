# -*- coding: utf-8 -*-
"""Эскиз изделия по ЕСКД из параметров семейства (lib/xyz_eskiz.py), db 28.10.2026.

Кнопка «Эскиз» (Model.panel/Eskiz.pushbutton). Эскиз строится НЕ из видов семейства, а из его
параметров (как у пленумов кнопки «Пленум»):
  ADSK_Размер_Ширина / Высота / Длина (габарит), ADSK_Толщина стенки, ADSK_Материал наименование,
  db_В<i>_Грань / _Роль / _Размер («Ø160» или «500×400») / _U / _V / _Длина, db_Код конфигурации.

Слой 1 (без Revit, проверяется вне Revit): part_from() → build_sketch() → список примитивов
  line / circle / fill / dim (размер Revit по ссылкам на линии) / text; render() — то же простыми
  отрезками и текстами (для превью в окне и для проверки картинкой).
Слой 2 (Revit): styles(), emit() — линии детализации, заливки, размеры, тексты в легенде.

Договорённости с db (Obsidian: REVIT/Плагин/XYZ/Разработка/Эскиз.md):
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
DEFAULT_OPT = {'face': None, 'rot': 0, 'views': ['main', 'left', 'top'], 'axo': True, 'az': 35.0, 'el': 30.0, 'axo_mode': 'free',
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
    # вход / выход воздуха: приточный — основная врезка вход, ответвления выход; вытяжной — наоборот
    md = p['mode'].lower()
    if md.startswith(u'приточ') or md.startswith(u'вытяж'):
        sup = md.startswith(u'приточ')
        for c in cuts:
            main = c['role'] == u'Основной'
            c['flow'] = c['flow_auto'] = 'in' if (main == sup) else 'out'
    for c in cuts:
        o, u, v, n = face_frame(c['face'], bw, bd, bh)
        c['o3'] = vadd(o, vmul(u, c['u']), vmul(v, c['v']))
        c['t3'] = vadd(c['o3'], vmul(n, c['l']))
        c['u3'], c['v3'], c['n3'] = u, v, n
    return p


def default_face(part):
    """Главный вид — грань с наибольшим числом ответвлений, при равенстве — «Спереди».
    Изделие по геометрии (КСД и др., система «как смонтировано») — «Спереди»: верх на эскизе = верх в модели."""
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
        d = {'t': 'arc', 'c': tuple(c), 'r': r, 'a0': a0, 'a1': a1, 'st': st}
        self.p.append(d)
        return d

    def fill(self, pts=None, c=None, r=None, kind='gray'):
        self.p.append({'t': 'fill', 'pts': [tuple(q) for q in pts] if pts else None,
                       'c': tuple(c) if c else None, 'r': r, 'kind': kind})

    def dim(self, refs, a, b, prefix=u''):
        if len(refs) >= 2:
            self.p.append({'t': 'dim', 'refs': refs, 'a': tuple(a), 'b': tuple(b), 'prefix': prefix})

    def adim(self, r1, r2, pa, pb, a, b):
        """Размер Revit между концами двух линий (r = (примитив линии, 0|1)); pa, pb — сами точки (для превью),
        a → b — положение и направление размерной линии."""
        self.p.append({'t': 'adim', 'r1': r1, 'r2': r2, 'pa': tuple(pa), 'pb': tuple(pb), 'a': tuple(a), 'b': tuple(b)})

    def rdim(self, arc, pos, kind='R', prefix=u''):
        """Размер Revit радиуса (kind='R') или диаметра ('D') дуги arc (примитив pen.arc); pos — точка надписи."""
        self.p.append({'t': 'rdim', 'arc': arc, 'pos': tuple(pos), 'kind': kind, 'prefix': prefix})

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
            if p['kind'] == 'hatch':
                out.extend(_hatch_segs(p, S))
        elif t == 'text':
            out.append(('text', p))
        elif t == 'dim':
            out.extend(render_dim(p, S))
        elif t == 'rdim':
            out.extend(render_rdim(p, S))
        elif t == 'adim':
            out.extend(render_adim(p, S))
    return out


def render_adim(p, S):
    """Превью размера Revit между двумя точками: выносные ⟂ размерной (как рисует Revit), засечки, число."""
    a, b = p['a'], p['b']
    d = unit(vsub(b, a))
    n = perp(d)
    ta, tb = dot(vsub(p['pa'], a), d), dot(vsub(p['pb'], a), d)
    out = []
    for t_, q in ((ta, p['pa']), (tb, p['pb'])):
        base = vadd(a, vmul(d, t_))
        e = dot(vsub(q, a), n)
        sg = 1 if e > 0 else -1
        out.append(('seg', vadd(base, vmul(n, e - sg * 1.0 * S)), vsub(base, vmul(n, sg * 2 * S)), 'dim'))
        tk = vmul(unit(vadd(d, n)), 1.5 * S)
        out.append(('seg', vsub(base, tk), vadd(base, tk), 'dimtick'))
    lo, hi = min(ta, tb), max(ta, tb)
    out.append(('seg', vadd(a, vmul(d, lo - 1.5 * S)), vadd(a, vmul(d, hi + 1.5 * S)), 'dim'))
    ang = math.degrees(math.atan2(d[1], d[0]))
    if ang > 90.01:
        ang -= 180
    if ang <= -89.99:
        ang += 180
    up = (-math.sin(math.radians(ang)), math.cos(math.radians(ang)))
    mid = vadd(a, vmul(d, (lo + hi) / 2))
    out.append(('text', {'pos': vadd(mid, vmul(up, 3.6 * S)), 's': fmt(round(p['val'])) if p.get('val') is not None else fmt(hi - lo), 'rot': ang, 'h': 2.5, 'al': 'c',
                         'dim': True}))
    return out


def _hatch_segs(p, S, step=1.5, ang=45.0):
    """Линии штриховки 45° (шаг 1,5 мм листа) внутри контура — для превью; в Revit — тип заливки db_Эскиз_Выход."""
    if p['pts']:
        poly = p['pts']
    else:
        c, r = p['c'], p['r']
        poly = [(c[0] + r * math.cos(2 * math.pi * i / 48), c[1] + r * math.sin(2 * math.pi * i / 48)) for i in range(48)]
    a = math.radians(ang)
    d, n = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    ts = [dot(q, n) for q in poly]
    h = step * S
    out = []
    t = math.floor(min(ts) / h) * h + h
    while t < max(ts):
        xs = []
        for i in range(len(poly)):
            A, B = poly[i], poly[(i + 1) % len(poly)]
            ta, tb = dot(A, n), dot(B, n)
            if (ta - t) * (tb - t) < 0:
                f = (t - ta) / (tb - ta)
                xs.append(dot(vadd(A, vmul(vsub(B, A), f)), d))
        xs.sort()
        for j in range(0, len(xs) - 1, 2):
            out.append(('seg', vadd(vmul(n, t), vmul(d, xs[j])), vadd(vmul(n, t), vmul(d, xs[j + 1])), 'thin'))
        t += h
    return out


def render_rdim(p, S):
    """Превью радиального / диаметрального размера: линия через центр к точке надписи, стрелка на дуге, текст."""
    a = p['arc']
    c, r = a['c'], a['r']
    u = unit(vsub(p['pos'], c))
    on = vadd(c, vmul(u, r))
    out = []
    st = vsub(c, vmul(u, r)) if p['kind'] == 'D' else c
    out.append(('seg', st, on, 'dim'))
    n = perp(u)
    for sg in ((1, -1) if p['kind'] == 'D' else (1,)):
        tip = vadd(c, vmul(u, sg * r))
        b = vsub(tip, vmul(u, sg * 2.5 * S))
        out.append(('poly', [tip, vadd(b, vmul(n, 0.8 * S)), vsub(b, vmul(n, 0.8 * S))], 'arrow'))
    val = (u'Ø' + fmt(2 * r)) if p['kind'] == 'D' else (u'R' + fmt(r))
    ang = math.degrees(math.atan2(u[1], u[0]))
    if ang > 90.01:
        ang -= 180
    if ang <= -89.99:
        ang += 180
    up = (-math.sin(math.radians(ang)), math.cos(math.radians(ang)))
    mid = vadd(c, vmul(u, r * 0.55))                 # надпись внутри дуги, на выноске (как Revit)
    out.append(('text', {'pos': vadd(mid, vmul(up, 3.6 * S)), 's': (p.get('prefix') or u'') + val, 'rot': ang,
                         'h': 2.5, 'al': 'c', 'dim': True}))
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
        out.append(('text', {'pos': vadd(mid, vmul(up, 3.6 * S)), 's': (p.get('prefix') or u'') + fmt(tb - ta),
                             'rot': ang, 'h': 2.5, 'al': 'c', 'dim': True}))
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
        for key in ('a', 'b', 'c', 'pos', 'pa', 'pb'):
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
    # справа налево: размеры одинаковых врезок (R, Ø) — у крайней правой, снаружи группы
    for c in sorted(part['cuts'], key=lambda c_: -dot(c_['o3'], R)):
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
                pen.fill(pts=[(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)], kind=fk(c))
                continue
            ov = 15 * k
            geo = None
            if c['shape'] == 'c':
                r = c['d'] / 2.0
                pen.fill(c=c2, r=r, kind=fk(c))
                geo = _circle2(pen, c2, r, 'thick')
                hw = hh = r
            elif c['shape'] == 'o':
                pen.fill(pts=stadium_pts(c2, hw, hh), kind=fk(c))
                geo = _stadium(pen, c2, hw, hh, 'thick')
            else:
                pts = [(c2[0] - hw, c2[1] - hh), (c2[0] + hw, c2[1] - hh), (c2[0] + hw, c2[1] + hh),
                       (c2[0] - hw, c2[1] + hh)]
                pen.fill(pts=pts, kind=fk(c))
                geo = _rect(pen, c2, hw, hh, 'thick')
            kx, ky = round(c2[0], 1), round(c2[1], 1)
            va = pen.line((c2[0], c2[1] - hh - ov), (c2[0], c2[1] + hh + ov), 'axis')
            ha = pen.line((c2[0] - hw - ov, c2[1]), (c2[0] + hw + ov, c2[1]), 'axis')
            vaxes.setdefault(kx, va)
            haxes.setdefault(ky, ha)
            # размеры врезки — настоящие размеры Revit (не текст): Ø / R по дуге, у овала — между центрами дуг,
            # у прямоугольника — стороны. Одинаковые врезки образмериваем один раз, Ø — с префиксом «N отв.»
            if c['label'] in done_lab:
                continue
            done_lab.add(c['label'])
            pre = u''                                   # db 29.10: без «2 отв.» — одинаковые видны и так
            q45 = (0.7071, 0.7071)
            if c['shape'] == 'c':
                pen.rdim(geo[0], vadd(c2, vmul(q45, r + 35 * k)), 'D', pre)
            elif c['shape'] == 'o' and abs(hw - hh) < 0.5:
                pen.rdim(geo['arcs'][0], vadd(c2, vmul(q45, hw + 35 * k)), 'D', pre)
            elif c['shape'] == 'o':
                lg = geo['long']
                sh = perp(lg)                                   # поперёк длины
                rr, (C1, C2) = geo['r'], geo['ctr']
                # R — на дуге, наружу под 45° от оси длины
                # дуга 0…90° правого / верхнего торца: Revit ведёт размер через её середину — под 45°
                pen.rdim(geo['arcs'][0], vadd(C1, vmul(q45, rr + 30 * k)), 'R', pre)
                # между центрами дуг — короткие осевые через центры, размер со стороны −sh
                # размер между центрами — со стороны, где до края корпуса больше места (db 29.10: под овалом)
                half_sh = abs(sh[0]) * hx + abs(sh[1]) * hy
                pos_sh = dot(c2, sh)
                sd = 1 if (half_sh - (pos_sh + rr)) > ((pos_sh - rr) + half_sh) else -1
                sh = vmul(sh, sd)
                l1 = pen.line(vsub(C1, vmul(sh, rr + ov)), vadd(C1, vmul(sh, rr + 25 * k)), 'axis')
                l2 = pen.line(vsub(C2, vmul(sh, rr + ov)), vadd(C2, vmul(sh, rr + 25 * k)), 'axis')
                y = vadd(c2, vmul(sh, rr + 15 * k))
                pen.dim([l1, l2], vsub(y, vmul(lg, 10 * k)), vadd(y, vmul(lg, 10 * k)))
            else:
                bot, rgt, top_, lft = geo
                pen.dim([lft, rgt], (c2[0] - hw, c2[1] - hh - 18 * k), (c2[0] + hw, c2[1] - hh - 18 * k), pre)
                pen.dim([bot, top_], (c2[0] + hw + 18 * k, c2[1] - hh), (c2[0] + hw + 18 * k, c2[1] + hh))
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
            g1 = pen.line(P1, T1)
            g2 = pen.line(P2, T2)
            end = pen.line(T2, T1)
            side_ends[orient].setdefault(round(dot(t2, a) * (1 if orient == 'h' else 1), 1), (end, t2))
            if not c.get('full'):
                pen.line(vsub(c2, vmul(a, 10 * k)), vadd(t2, vmul(a, 15 * k)), 'axis')
                # размер Ø / сторона патрубка — размер Revit между образующими (значение считает Revit)
                xd = vadd(t2, vmul(a, 75 * k))
                pen.dim([g1, g2], vsub(xd, vmul(p, hp)), vadd(xd, vmul(p, hp)), u'Ø' if c['shape'] == 'c' else u'')
                _flow_mark(pen, c, t2, a, p, hp, 60 * k)
            else:
                _flow_mark(pen, c, t2, a, p, hp, 45 * k)
        else:
            _flow_mark(pen, c, c2, a, p, hp, 45 * k)
            if not c.get('full'):
                # проём «с ребра» (щель КСД и т.п.): размер проёма и привязка к граням — цепочкой за стрелкой
                bw = 2.0 * S                             # короткие засечки краёв проёма — опоры размера
                e1 = pen.line(vadd(c2, vmul(p, hp)), vadd(c2, vmul(p, hp), vmul(a, bw)), 'thin')
                e2 = pen.line(vsub(c2, vmul(p, hp)), vadd(vsub(c2, vmul(p, hp)), vmul(a, bw)), 'thin')
                ed = (bL, bR) if abs(p[0]) > 0.5 else (bB, bT)
                refs = [ed[0], e1, e2, ed[1]]
                if hp * 2 >= (2 * hx if abs(p[0]) > 0.5 else 2 * hy) - 1.0:
                    refs = [ed[0], ed[1]]                # во всю длину — габарит уже есть
                if len(refs) > 2:
                    off = vadd(c2, vmul(a, 2 * k + 45 * k + 20 * k))
                    span = (hx if abs(p[0]) > 0.5 else hy)
                    pen.dim(refs, vsub(off, vmul(p, span)), vadd(off, vmul(p, span)))
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


FLOW_NAMES = [u'авто', u'вход', u'выход', u'не указывать']
FLOW_KEYS = {u'вход': 'in', u'выход': 'out', u'не указывать': 'none'}


def cut_id(c):
    """Ключ отверстия для настроек (грань и положение на ней, мм)."""
    return u'%s|%d|%d' % (c['face'], int(round(c['u'])), int(round(c['v'])))


def cut_title(c):
    """Подпись отверстия в окне: грань, форма и размер, патрубок / проём."""
    kind = u'проём' if c['l'] <= 0.5 else u'патрубок'
    return u'%s · %s %s' % (c['face'], kind, c['label'] if not c.get('full') else u'во всю грань')


def apply_flow(part, ov):
    """ov: {cut_id: 'in' | 'out' | 'none', 'face:<грань>': 'in' | 'out'} — выбор db; остальное — авто
    (соединители, _SUP/_RET, db_Режим). 'face:…' — вся грань открыта (вход / выход), задаётся в 3D окна."""
    part['cuts'] = [c for c in part['cuts'] if not c.get('user')]
    busy = set(c['face'] for c in part['cuts'] if c.get('full'))
    for key, v in sorted((ov or {}).items()):
        if not key.startswith(u'face:') or v not in ('in', 'out', 'open'):
            continue
        f = key[5:]
        if f not in FACES or f in busy:
            continue
        su, sv = face_size(f, part['W'], part['D'], part['H'])
        part['cuts'].append({'face': f, 'role': u'', 'u': su / 2.0, 'v': sv / 2.0, 'l': 0.0, 'shape': 'r', 'w': su,
                             'h': sv, 'd': su, 'label': u'%s×%s' % (fmt(su), fmt(sv)), 'user': True, 'flow': None,
                             'flow_auto': None})
    _finish(part)
    for c in part['cuts']:
        if c.get('user'):
            continue
        if 'flow_auto' not in c:
            c['flow_auto'] = c.get('flow')
        c['flow'] = None          # db 29.10: направление двухстороннее — вход / выход не показываем


def fk(c):
    """Вид заливки отверстия: выход воздуха — штриховка 45°, вход и неизвестно — серая (db 29.10)."""
    return 'hatch' if c.get('flow') == 'out' else 'gray'


def _flow_mark(pen, c, base, a, p, hp, L):
    """Отверстие «с ребра» — только стрелка потока по оси отверстия (полосу заливки db 29.10 убрать: вход / выход
    и так видны заливкой «в лоб»): вход — к отверстию, выход — от отверстия наружу, неизвестно — к отверстию."""
    k = pen.k
    bw = 0.0
    near, far = vadd(base, vmul(a, bw + 2 * k)), vadd(base, vmul(a, bw + 2 * k + L))
    if c.get('flow') == 'out':
        pen.arrow(near, far)
    else:
        pen.arrow(far, near)


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


def _circle2(pen, c, r, st):
    """Окружность четвертями. Первая — 0…90°: размер Revit (Ø / R) ставится по середине дуги, т.е. под 45°."""
    return [pen.arc(c, r, math.pi / 2 * i, math.pi / 2 * (i + 1), st) for i in range(4)]


def _stadium(pen, c, hw, hh, st):
    """Овал: {'arcs': [дуга1, дуга2], 'ctr': [центр1, центр2], 'r': радиус, 'long': ось длины (1,0)/(0,1)}."""
    if abs(hw - hh) < 0.5:
        return {'arcs': _circle2(pen, c, hw, st), 'ctr': [c, c], 'r': hw, 'long': (1, 0)}
    if hw >= hh:
        r, dx = hh, hw - hh
        pen.line((c[0] - dx, c[1] - r), (c[0] + dx, c[1] - r), st)
        pen.line((c[0] - dx, c[1] + r), (c[0] + dx, c[1] + r), st)
        a1 = pen.arc((c[0] + dx, c[1]), r, 0.0, math.pi / 2, st)            # правый торец: две четверти
        pen.arc((c[0] + dx, c[1]), r, -math.pi / 2, 0.0, st)
        a2 = pen.arc((c[0] - dx, c[1]), r, math.pi / 2, math.pi, st)
        pen.arc((c[0] - dx, c[1]), r, math.pi, 3 * math.pi / 2, st)
        return {'arcs': [a1, a2], 'ctr': [(c[0] + dx, c[1]), (c[0] - dx, c[1])], 'r': r, 'long': (1, 0)}
    r, dy = hw, hh - hw
    pen.line((c[0] - r, c[1] - dy), (c[0] - r, c[1] + dy), st)
    pen.line((c[0] + r, c[1] - dy), (c[0] + r, c[1] + dy), st)
    a1 = pen.arc((c[0], c[1] + dy), r, 0.0, math.pi / 2, st)                # верхний торец: две четверти
    pen.arc((c[0], c[1] + dy), r, math.pi / 2, math.pi, st)
    a2 = pen.arc((c[0], c[1] - dy), r, math.pi, 3 * math.pi / 2, st)
    pen.arc((c[0], c[1] - dy), r, 3 * math.pi / 2, 2 * math.pi, st)
    return {'arcs': [a1, a2], 'ctr': [(c[0], c[1] + dy), (c[0], c[1] - dy)], 'r': r, 'long': (0, 1)}


def _rect(pen, c, hw, hh, st):
    """→ (низ, право, верх, лево) — линии для размеров."""
    x0, x1, y0, y1 = c[0] - hw, c[0] + hw, c[1] - hh, c[1] + hh
    return (pen.line((x0, y0), (x1, y0), st), pen.line((x1, y0), (x1, y1), st),
            pen.line((x1, y1), (x0, y1), st), pen.line((x0, y1), (x0, y0), st))


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


# Аксонометрия без чисел (db 29.10): размеры Revit в косоугольной проекции не ставятся, а число текстом
# не обновляется и легко разойдётся с изделием. Все размеры — на ортогональных видах. True — вернуть подписи.
AXO_DIMS = True          # db 29.10: размеры на аксонометрии нужны (габариты, длины патрубков)
AXO_LABELS = False       # подписи «овал …», «Ø…» на аксонометрии — нет (текст не обновляется; размеры — на видах)


def build_axo(part, basis, az, el, S, mode='free', cam=None):
    R0, U0, N0 = basis
    R0o, U0o, N0o = R0, U0, N0
    el = max(-85.0, min(85.0, el))

    # Фронтальная изометрия (ГОСТ 2.317): ось X — горизонтально, Z — вертикально, Y — под 45°, коэффициенты 1.
    # Длины по всем трём осям на листе истинные → размеры Revit на аксонометрии показывают настоящие числа
    # (db 29.10: на эскизе только размеры Revit, никакого текста и нарисованных размерных линий).
    # Из 4 направлений оси Y (вправо/влево × вверх/вниз) — то, где видно больше: открытая грань (вес 4), проём (2),
    # торец патрубка (1). db 29.10: открытая грань пленума должна быть залита на аксонометрии.
    C45 = math.cos(math.radians(45.0))
    sx0 = 1 if math.sin(math.radians(az)) >= 0 else -1
    sy0 = 1 if el >= 0 else -1

    def eye(sx_, sy_, sn_=1):
        return unit(vadd(vmul(R0, sx_ * C45 * sn_), vmul(U0, sy_ * C45), vmul(N0, sn_)))
    best = None
    # sn = −1 — смотрим с обратной стороны (ось X тоже разворачиваем, чтобы не было зеркального изображения)
    for sn_, sx_, sy_ in [(sn, a_, b_) for sn in (1, -1) for a_, b_ in ((sx0, sy0), (-sx0, sy0), (sx0, -sy0), (-sx0, -sy0))]:
        e3_ = eye(sx_, sy_, sn_)
        sc = sum([((4 if c.get('full') else 2) if c['l'] <= 0.5 else 3) for c in part['cuts'] if dot(c['n3'], e3_) > 0.05])  # 30.10 db: патрубки видны важнее проёма сзади
        if best is None or sc > best[0]:
            best = (sc, sx_, sy_, sn_)
    SX, SY, SN = best[1], best[2], best[3]
    e3 = eye(SX, SY, SN)
    R0, N0 = vmul(R0, SN), vmul(N0, SN)
    pen = Pen(S)
    k = pen.k
    W, D, H = part['W'], part['D'], part['H']
    hw = (W / 2, D / 2, H / 2)

    def pr(p):
        t = -dot(p, N0)                              # глубина от зрителя
        return (dot(p, R0) + SX * t * C45, dot(p, U0) + SY * t * C45)

    if mode == 'free':
        # 30.10 db: свой угол — прямоугольная проекция по азимуту (вокруг вертикали от главного вида, + справа)
        # и наклону (+ сверху). Размеры Revit на ней — длина на листе (рёбра под углом сокращены).
        a_, e_ = math.radians(az), math.radians(el)
        e3 = unit(vadd(vmul(N0o, math.cos(e_) * math.cos(a_)), vmul(R0o, math.cos(e_) * math.sin(a_)), vmul(U0o, math.sin(e_))))
        XS = unit(vsub(vmul(R0o, math.cos(a_)), vmul(N0o, math.sin(a_))))
        YS = unit(vsub(vmul(U0o, math.cos(e_)), vmul(vadd(vmul(N0o, math.cos(a_)), vmul(R0o, math.sin(a_))), math.sin(e_))))
        R0, U0, N0 = R0o, U0o, N0o
        if cam:                                      # 30.10 db: ракурс = 3D-вид окна (раздел 2), видовой куб
            XS, YS, e3 = unit(cam[0]), unit(cam[1]), unit(cam[2])

        def pr(p):
            return (dot(p, XS), dot(p, YS))

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
            solids.append(_Box(tuple([min([q[i] for q in pts]) for i in range(3)]), tuple([max([q[i] for q in pts]) for i in range(3)])))
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
            return None
        pts_all.extend([A, B])
        made = []
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
                made.append(pen.line(pr(P0), pr(P1), 'thick' if st[i] else 'hidden'))
            i = j + 1
        return (made[0], made[-1]) if made else None

    # короб
    box_edges = {}
    for ax in range(3):
        o1, o2 = (ax + 1) % 3, (ax + 2) % 3
        for s1 in (-1, 1):
            for s2 in (-1, 1):
                A = [0, 0, 0]
                A[o1], A[o2] = s1 * hw[o1], s2 * hw[o2]
                B = list(A)
                A[ax], B[ax] = -hw[ax], hw[ax]
                box_edges[(tuple(A), tuple(B))] = edge(tuple(A), tuple(B))

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
    rdone = set()
    for c in sorted(part['cuts'], key=lambda c_: -dot(c_['t3'], R0)):
        nseg = max(24, min(72, int(math.pi * max(c['w'], c['h']) / (3 * S)))) if c['shape'] in ('c', 'o') else 4
        tip = ring(c['t3'], c, nseg)
        par = mode != 'free' and c['shape'] in ('c', 'o') and abs(dot(c['n3'], N0)) > 0.99 and not c.get('full')
        if par:
            # торец ∥ плоскости главного вида — во фронтальной изометрии без искажения: настоящие дуги,
            # на них — размер Revit R / Ø (у первой справа, один раз на размер)
            pts_all.extend(tip)
            hwa = (abs(dot(c['u3'], R0)) * c['w'] + abs(dot(c['v3'], R0)) * c['h']) / 2.0
            hha = (abs(dot(c['u3'], U0)) * c['w'] + abs(dot(c['v3'], U0)) * c['h']) / 2.0
            c2a = pr(c['t3'])
            sta = 'thick' if visible(c['t3']) else 'hidden'
            if c['shape'] == 'c':
                g_ = _circle2(pen, c2a, c['d'] / 2.0, sta)
                if AXO_DIMS and sta == 'thick' and c['label'] not in rdone:
                    rdone.add(c['label'])
                    pen.rdim(g_[0], vadd(c2a, vmul((0.7071, 0.7071), c['d'] / 2.0 + 30 * k)), 'D')
            else:
                g_ = _stadium(pen, c2a, hwa, hha, sta)
                if AXO_DIMS and sta == 'thick' and c['label'] not in rdone:
                    rdone.add(c['label'])
                    pen.rdim(g_['arcs'][0], vadd(g_['ctr'][0], vmul((0.7071, 0.7071), g_['r'] + 30 * k)),
                             'D' if abs(hwa - hha) < 0.5 else 'R')
        else:
            for q in range(len(tip)):
                edge(tip[q], tip[(q + 1) % len(tip)])
        if c['l'] > 0.5:
            if not c.get('full'):
                base = ring(c['o3'], c, nseg)
                for q in range(len(base)):
                    edge(base[q], base[(q + 1) % len(base)])
            gens = c['_gens'] = []
            if c['shape'] == 'r':
                for q in range(4):
                    A_, B_ = vadd(c['o3'], vsub(tip[q], c['t3'])), tip[q]
                    gens.append((A_, B_, edge(A_, B_), unit(vsub(tip[q], c['t3']))))
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
                        A_, B_ = vadd(c['o3'], off), vadd(c['t3'], off)
                        gens.append((A_, B_, edge(A_, B_), unit(off)))
        if dot(c['n3'], e3) > 0.05 and visible(c['t3']):
            fills.append(([pr(q) for q in tip], fk(c)))
    for f, kd in fills:
        pen.p.insert(0, {'t': 'fill', 'pts': f, 'c': None, 'r': None, 'kind': kd})

    # ---- размеры по ГОСТ 2.317: выносные ∥ оси, размерная ∥ ребру, засечки, текст вдоль
    hull = _hull([pr(q) for q in pts_all])
    used = []

    def outside(q):
        return not _in_hull(hull, q)

    def rdim(refs, A, B, dr, off):
        """Размер Revit между концами рёбер (A, B — 3D): размерная ∥ ребру на выносе off по dr."""
        if not AXO_DIMS or refs is None:
            return
        a, b = pr(vadd(A, vmul(dr, off))), pr(vadd(B, vmul(dr, off)))
        pen.adim((refs[0], 0), (refs[1], 1), pr(A), pr(B), a, b)
        pen.p[-1]['val'] = vlen(vsub(B, A))           # истинная длина ребра (db 30.10: на свободном ракурсе)
        used.append(vmul(vadd(a, b), 0.5))

    def idim(A, B, dr, off, s):
        if not AXO_DIMS or True:                     # нарисованные размеры (линии + текст) — не используем
            return
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
        """cands: (A, B, dir[, refs]). Наименьший вынос, при котором размерная целиком вне контура изделия."""
        best = None
        for cd in cands:
            A, B, dr = cd[0], cd[1], cd[2]
            pl = vlen(pr(dr))
            if pl < 0.35:
                continue
            for off in [x * k for x in (50, 70, 90, 120, 150, 190, 240)]:
                ok = all([outside(pr(vadd(vadd(A, vmul(vsub(B, A), f)), vmul(dr, off)))) for f in (0, 0.25, 0.5, 0.75, 1)])
                if ok:
                    mid = pr(vadd(vmul(vadd(A, B), 0.5), vmul(dr, off)))
                    near = len([1 for q in used if vlen(vsub(q, mid)) < 60 * k])
                    sc = off * pl / pl + near * 1000 + (0 if dot(dr, e3) <= 0.05 else 30 * k) - (dot(pr(dr), (0, -1)) > 0.5) * 5 * k
                    if best is None or sc < best[0]:
                        best = (sc, A, B, dr, off, cd[3] if len(cd) > 3 else None)
                    break
        if best:
            rdim(best[5], best[1], best[2], best[3], best[4])

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
                    cands.append((tuple(A), tuple(B), dr, box_edges.get((tuple(A), tuple(B)))))
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
        for A, B, refs, dr in c.get('_gens') or []:
            if refs is None:
                continue
            if vlen(pr(dr)) < 0.35:
                continue
            if True:
                off = 35 * k
                mid = pr(vadd(vmul(vadd(A, B), 0.5), vmul(dr, off)))
                hit = len([1 for c2, h2 in noz if c2 is not c and _in_hull(h2, mid)])
                near = len([1 for q in used if vlen(vsub(q, mid)) < 50 * k])
                sc = hit * 10 + near * 5 - unit(pr(dr))[1] - 0.5 * dot(unit(pr(dr)), unit(vsub(pr(c['o3']), (0, 0))))
                if best is None or sc < best[0]:
                    best = (sc, A, B, dr, off, refs)
        if best:
            rdim(best[5], best[1], best[2], best[3], best[4])
    # ---- Ø и a×b врезок: линия под 45° в плоскости торца, стрелки на контуре, вынос и полка
    ctr = vmul(vadd(*[pr(q) for q in pts_all]), 1.0 / max(1, len(pts_all)))
    vis_cuts = [c for c in part['cuts'] if not c.get('full') and dot(c['n3'], e3) > 0.1 and visible(c['t3'])]
    nlab = {}
    for c in vis_cuts:
        nlab[c['label']] = nlab.get(c['label'], 0) + 1
    done_lab = set()
    for c in vis_cuts:
        if not AXO_LABELS:
            break
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
    ov = [c for c in part['cuts'] if c['shape'] == 'o' and c['l'] > 0.5]
    if ov:
        dd = sorted(set(fmt(c['dn']) for c in ov if c.get('dn')))
        out.append(u'Патрубки овальные' + (u' под воздуховод Ø' + u', Ø'.join(dd) if dd else u'') +
                   u'; размеры — по видам.')
    holes = [c for c in part['cuts'] if not c.get('full') and c['l'] <= 0.5]
    if holes:
        out.append(u'Проёмы в стенках: ' + u'; '.join(u'%s — %s' % (FACE_LOW[c['face']], c.get('note') or c['label'])
                                                     for c in holes) + u'.')
    out.append(u'Неуказанные предельные отклонения ±2 мм.')
    fl = set(c.get('flow') for c in part['cuts'])
    if 'out' in fl or 'in' in fl:
        out.append(u'Отверстия (проходные): вход воздуха — серая заливка, стрелка к отверстию; '
                   u'выход — штриховка, стрелка от отверстия.')
    else:
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
        ap = build_axo(part, (R, U, N), o['az'], o['el'], S, o.get('axo_mode', 'free'), o.get('axo_cam'))
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
    head = u'Семейство: ' + part['family'] + (u'\nТип: ' + part['type'] if part['type'] else u'')   # db 30.10
    prims.append({'t': 'text', 'pos': (gb[0], gb[3] + (19 if part['type'] else 12) * S), 's': head, 'rot': 0.0, 'h': 3.5, 'al': 'l'})
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
        c = {'face': face, 'role': u'', 'u': cu, 'v': cv, 'l': l_, 'shape': shape, 'w': w_, 'h': h_, 'd': w_, 'dn': d}
        if shape == 'c':
            c['label'] = u'Ø' + fmt(w_)
        elif shape == 'o':
            c['label'] = u'овал %s×%s' % (fmt(w_), fmt(h_)) + (u' (Ø%s)' % fmt(d) if d else u'')
        else:
            c['label'] = u'%s×%s' % (fmt(w_), fmt(h_))
        cuts.append(c)
    _tidy_cuts(cuts, W, D, H)
    ap = []
    for a in approx:
        if a not in ap:
            ap.append(a)
    return {'W': W, 'D': D, 'H': H, 'OW': W, 'OD': D, 'OH': H, 'cuts': cuts, 'ctr': ctr, 'notes': [], 'approx': ap}


def _tidy_cuts(cuts, W, D, H, tol=2.0):
    """Погрешность обмера (поворот экземпляра, дуги) даёт 179 / 180 у одинаковых патрубков и 122 / 123 у
    симметричных. Одинаковые в пределах 2 мм — к общему (среднему, до 1 мм); почти симметричные — строго."""
    for key in ('w', 'h', 'l'):
        for c in cuts:
            grp = [x for x in cuts if x['shape'] == c['shape'] and all(abs(x[k2] - c[k2]) <= tol for k2 in ('w', 'h', 'l'))]
            c['_' + key] = round(sum(x[key] for x in grp) / float(len(grp)))
    for c in cuts:
        for key in ('w', 'h', 'l'):
            c[key] = c.pop('_' + key)
        if c['shape'] == 'c':
            c['d'] = c['w']
            c['label'] = u'Ø' + fmt(c['w'])
        elif c['shape'] == 'o':
            c['d'] = c['w']
            c['label'] = u'овал %s×%s' % (fmt(c['w']), fmt(c['h'])) + (u' (Ø%s)' % fmt(c['dn']) if c.get('dn') else u'')
        else:
            c['d'] = c['w']
            c['label'] = u'%s×%s' % (fmt(c['w']), fmt(c['h']))
    for c in cuts:
        su, sv = face_size(c['face'], W, D, H)
        for key, size in (('u', su), ('v', sv)):
            if abs(c[key] - size / 2.0) <= tol:
                c[key] = size / 2.0                      # по центру грани
                continue
            for x in cuts:
                if x is not c and x['face'] == c['face'] and abs((x[key] - size / 2.0) + (c[key] - size / 2.0)) <= tol:
                    m = round((abs(c[key] - size / 2.0) + abs(x[key] - size / 2.0)) / 2.0)
                    c[key] = size / 2.0 + (m if c[key] > size / 2.0 else -m)
                    x[key] = size / 2.0 + (m if x[key] > size / 2.0 else -m)


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
    """Тела (габариты, число цилиндрических граней) и соединители экземпляра в мм, в осях СЕМЕЙСТВА (db 29.10:
    КСД в модели вставлены на вертикальную плоскость, но изделие — как в семействе: короб 494×52×185, щель снизу).
    Габарит тела — по точкам его рёбер, дуги — с шагом 5° (габарит Solid.GetBoundingBox у повёрнутого экземпляра
    раздувается: 632×553 вместо 494×185)."""
    from Autodesk.Revit.DB import Options, ViewDetailLevel, Solid, GeometryInstance, CylindricalFace, PlanarFace, XYZ, Line
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
            pts = []
            for e in go.Edges:
                cv = e.AsCurve()
                if isinstance(cv, Line):
                    seq = (cv.GetEndPoint(0), cv.GetEndPoint(1))
                else:
                    # дуги и сплайны — 72 точки по параметру: ошибка габарита < 0,1 мм
                    # (Edge.Tessellate грубее: овал 182 давал 179–180, размеры расходились на 1–3 мм)
                    seq = [cv.Evaluate(k / 72.0, True) for k in range(73)]
                for pt in seq:
                    q = inv.OfPoint(pt)
                    pts.append((q.X * 304.8, q.Y * 304.8, q.Z * 304.8))
            if not pts:
                continue
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


def _geo_flow(fi, p):
    """Вход / выход у семейств по геометрии: патрубки — по направлению соединителей (In — воздух входит в
    изделие), щель / проёмы без соединителя — обратное. Нет данных — по имени семейства (_SUP / _RET)."""
    pat = None
    try:
        from Autodesk.Revit.DB import FlowDirectionType
        dirs = set()
        for c in fi.MEPModel.ConnectorManager.Connectors:
            try:
                if c.Direction == FlowDirectionType.In:
                    dirs.add('in')
                elif c.Direction == FlowDirectionType.Out:
                    dirs.add('out')
            except Exception:
                pass
        if len(dirs) == 1:
            pat = dirs.pop()
    except Exception:
        pass
    if pat is None:
        fam = (fi.Symbol.FamilyName or u'').upper()
        if fam.endswith(u'_SUP') or u'ПРИТОЧ' in fam:
            pat = 'in'
        elif fam.endswith(u'_RET') or fam.endswith(u'_EXH') or u'ВЫТЯЖ' in fam:
            pat = 'out'
    if pat is None:
        return
    for c in p['cuts']:
        c['flow'] = c['flow_auto'] = pat if c['l'] > 0.5 else ('out' if pat == 'in' else 'in')


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
        # поворот на 180° в плане (и зеркальный экземпляр симметричного изделия) — то же изделие:
        # из двух положений берём то, где патрубков сзади больше, при равенстве — по порядку размеров
        rot = lambda q: (-q[0], -q[1], q[2])
        boxes2 = [(rot(b[1]), rot(b[0])) + tuple(b[2:]) for b in boxes]
        boxes2 = [(tuple(min(b[0][i], b[1][i]) for i in range(3)), tuple(max(b[0][i], b[1][i]) for i in range(3))) + tuple(b[2:])
                  for b in boxes2]
        p2 = part_from_boxes(boxes2, [(rot(q), d) for q, d in conns])

        def _key(pp):
            nb = sum(1 for c in pp['cuts'] if c['face'] == u'Сзади')
            nf = sum(1 for c in pp['cuts'] if c['face'] == u'Спереди')
            return (nf - nb, sorted((c['face'], round(c['u']), round(c['v'])) for c in pp['cuts']))
        if p2 is not None and _key(p2) < _key(p):
            p = p2
            p['rot180'] = True
        slot = _pmm(fi, u'2_w')
        # щель КСД полосой не рисуем (db 29.10): выход задаётся гранью целиком в 3D окна («Снизу» — выход)
        _geo_flow(fi, p)
        p['geo'] = True
        r2 = lambda x: int(round(x / 2.0))           # допуск 2 мм: огрубление геометрии при повороте экземпляра
        sig = (s.Id.IntegerValue, r2(p['W']), r2(p['D']), r2(p['H']),
               tuple(sorted((c['face'], r2(c['u']), r2(c['v']), r2(c['w']), r2(c['h']), r2(c['l'])) for c in p['cuts'])))
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
    # 30.10 db: размеры эскиза — другим цветом, чтобы не сливались с гранями (зелёный, как в превью)
    try:
        DN = u'db_Эскиз_размер 2.5 зелёный'
        own = [d for d in dts if nm(d) == DN]
        if own:
            st['dim'] = own[0]
        elif doc.IsModifiable:
            from Autodesk.Revit.DB import BuiltInParameter
            dn = st['dim'].Duplicate(DN)
            dn.get_Parameter(BuiltInParameter.LINE_COLOR).Set(46 + 125 * 256 + 50 * 65536)
            st['dim'] = dn
    except Exception:
        pass
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
    # выход воздуха — штриховка 45° (db 29.10): тип «db_Эскиз_Выход», узор чертёжный 45°, 1,5 мм
    hat = byname.get(u'db_Эскиз_Выход')
    if hat is None:
        from Autodesk.Revit.DB import FillPattern, FillPatternHostOrientation, ElementId
        pats = [q for q in FilteredElementCollector(doc).OfClass(FillPatternElement)
                if q.GetFillPattern().Target == FillPatternTarget.Drafting and q.Name == u'db_Эскиз_Штриховка 45']
        if pats:
            pe = pats[0]
        else:
            pe = FillPatternElement.Create(doc, FillPattern(u'db_Эскиз_Штриховка 45', FillPatternTarget.Drafting,
                                                            FillPatternHostOrientation.ToView, math.radians(45),
                                                            1.5 / 304.8))
        hat = gray.Duplicate(u'db_Эскиз_Выход')
        hat.ForegroundPatternId = pe.Id
        hat.ForegroundPatternColor = Color(0, 0, 0)
        try:
            hat.BackgroundPatternId = ElementId.InvalidElementId
        except Exception:
            pass
    st['hatch'] = hat.Id
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
    lines = {}
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
                lines[id(p)] = dc
            elif t == 'circle':
                arc = Arc.Create(Plane.CreateByNormalAndOrigin(XYZ.BasisZ, P(p['c'])), p['r'] * F, 0, 2 * math.pi)
                dc = doc.Create.NewDetailCurve(view, arc)
                dc.LineStyle = st[p['st']]
            elif t == 'arc':
                arc = Arc.Create(P(p['c']), p['r'] * F, p['a0'], p['a1'], XYZ.BasisX, XYZ.BasisY)
                dc = doc.Create.NewDetailCurve(view, arc)
                dc.LineStyle = st[p['st']]
                refs[id(p)] = dc.GeometryCurve.Reference
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
                dm = doc.Create.NewDimension(view, Line.CreateBound(P(p['a']), P(p['b'])), ra, st['dim'])
                if p.get('prefix'):
                    dm.Prefix = p['prefix']
        except Exception as ex:
            errs.append(u'dim: %s' % ex)
    for p in prims:
        if p['t'] != 'adim':
            continue
        try:
            ra = ReferenceArray()
            for ln, end in (p['r1'], p['r2']):
                el = lines.get(id(ln))
                if el is None:
                    raise Exception(u'нет линии для размера')
                ra.Append(el.GeometryCurve.GetEndPointReference(end))
            dm = doc.Create.NewDimension(view, Line.CreateBound(P(p['a']), P(p['b'])), ra, st['dim'])
            # аксонометрия под свободным ракурсом: ребро на листе сокращено — размер показывает истинную длину
            # (ГОСТ 2.317: на аксонометрии — действительные размеры); на ГОСТ-ракурсе совпадает, замены нет
            v_ = p.get('val')
            if v_ is not None and dm.Value is not None and abs(dm.Value * 304.8 - v_) > 0.5:
                dm.ValueOverride = fmt(round(v_))
        except Exception as ex:
            errs.append(u'axo dim: %s' % ex)
    for p in prims:
        if p['t'] != 'rdim':
            continue
        try:
            rf = refs.get(id(p['arc']))
            if rf is None:
                continue
            dm = _radial(doc, view, rf, P(p['pos']), p['kind'], st)
            if p.get('prefix'):
                dm.Prefix = p['prefix']
            # надпись — как ставит Revit: на выноске внутри дуги (db 29.10 поправил так вручную)
        except Exception as ex:
            errs.append(u'%s: %s' % (u'Ø' if p['kind'] == 'D' else u'R', ex))
    return errs


def _radial(doc, view, ref, pos, kind, st):
    """Радиальный (R) или диаметральный (Ø) размер Revit по ссылке на дугу.
    Revit 2025: RadialDimension.Create(doc, view, arcRef, isDiameter) — размер идёт через середину дуги.
    Тип — одноимённый линейному размеру эскиза (ADSK_Основной_2.5 мм), иначе ближайший по имени, иначе по умолчанию."""
    from Autodesk.Revit.DB import RadialDimension, FilteredElementCollector, DimensionType, DimensionStyleType
    dm = RadialDimension.Create(doc, view, ref, kind == 'D')
    want = DimensionStyleType.Diameter if kind == 'D' else DimensionStyleType.Radial
    try:
        base = nm(st['dim']) if st.get('dim') is not None else u''
        best = None
        for t in FilteredElementCollector(doc).OfClass(DimensionType):
            if t.StyleType != want:
                continue
            n_ = nm(t)
            sc = 100 if n_ == base else sum(1 for w in base.replace(u'_', u' ').split() if len(w) > 2 and w in n_)
            if u'Выноска' in n_ and u'Выноска' not in base:
                sc -= 1
            if best is None or sc > best[0]:
                best = (sc, t)
        if best is not None and best[0] > 0:
            dm.DimensionType = best[1]
        # зелёный, как линейные размеры эскиза (db 30.10)
        from Autodesk.Revit.DB import BuiltInParameter
        cur = doc.GetElement(dm.GetTypeId())
        gn = nm(cur) + u' db_Эскиз зелёный'
        g = [t for t in FilteredElementCollector(doc).OfClass(DimensionType) if nm(t) == gn]
        if g:
            g = g[0]
        else:
            g = cur.Duplicate(gn)
            g.get_Parameter(BuiltInParameter.LINE_COLOR).Set(46 + 125 * 256 + 50 * 65536)
        dm.DimensionType = g
    except Exception:
        pass
    return dm
