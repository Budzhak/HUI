# -*- coding: utf-8 -*-
"""Видовой куб как в Revit для окон ХУЙ с 3D-превью на Canvas («Пленум»; в «Эскизе» — своя копия), db 30.10.2026.

Куб в углу Canvas крутится вместе с моделью. Активные зоны: середина грани — вид на грань, полоса у ребра — вид
под 45°, уголок — изометрия; зона подсвечивается целиком (ребро — на двух гранях, угол — на трёх). Когда смотрим
строго на грань — дуговые стрелки поворота на 90° и ▲▼◀▶ на соседнюю грань.
Элементы получают Tag ('cube', d) — взгляд из направления d (к зрителю) — или ('roll', градусы); обработку щелчка
делает окно (см. look_from, roll_screen)."""
import math
from System.Windows import Point, Size
from System.Windows.Controls import Canvas, TextBlock
from System.Windows.Shapes import Polygon, Polyline
from System.Windows.Input import Cursors
from System.Windows.Media import SolidColorBrush, Color

NAMES = {('N', 1): u'СПЕРЕДИ', ('N', -1): u'СЗАДИ', ('R', 1): u'СПРАВА', ('R', -1): u'СЛЕВА',
         ('U', 1): u'СВЕРХУ', ('U', -1): u'СНИЗУ'}


def _b(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


def _add(*vs):
    return tuple(sum(c) for c in zip(*vs))


def _mul(v, k):
    return tuple(c * k for c in v)


def _unit(v):
    l = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / l for c in v)


def project(p, yaw, pitch, roll=0.0):
    """Та же камера, что в окнах ХУЙ: (экран x, экран y вниз, глубина — больше дальше)."""
    a, b, r = math.radians(yaw), math.radians(pitch), math.radians(roll)
    x, y, z = p
    x1 = x * math.cos(a) - y * math.sin(a)
    y1 = x * math.sin(a) + y * math.cos(a)
    y2 = y1 * math.cos(b) - z * math.sin(b)
    z2 = y1 * math.sin(b) + z * math.cos(b)
    xr = x1 * math.cos(r) - z2 * math.sin(r)
    zr = x1 * math.sin(r) + z2 * math.cos(r)
    return xr, -zr, y2


def screen_axes(yaw, pitch, roll=0.0):
    """(вправо, вверх, к зрителю) экрана в координатах модели."""
    a, b, r = math.radians(yaw), math.radians(pitch), math.radians(roll)
    rs = (math.cos(a), -math.sin(a), 0.0)
    us = (math.sin(b) * math.sin(a), math.sin(b) * math.cos(a), math.cos(b))
    cr, sr = math.cos(r), math.sin(r)
    return (tuple([cr * rs[i] - sr * us[i] for i in range(3)]), tuple([sr * rs[i] + cr * us[i] for i in range(3)]),
            (-math.cos(b) * math.sin(a), -math.cos(b) * math.cos(a), math.sin(b)))


def look_from(d, yaw, front=(0, -1, 0)):
    """Взгляд из направления d → (yaw, pitch, roll). Строго сверху/снизу — «спереди» внизу, как в Revit."""
    d = _unit(d)
    pitch = max(-89.0, min(89.0, math.degrees(math.asin(max(-1.0, min(1.0, d[2]))))))
    if abs(d[0]) + abs(d[1]) > 1e-6:
        yaw = math.degrees(math.atan2(-d[0], -d[1]))
    else:
        yaw = math.degrees(math.atan2(-front[0], -front[1]))
    return yaw, pitch, 0.0


def draw(cv, yaw, pitch, roll, basis=((1, 0, 0), (0, 0, 1), (0, -1, 0)), dark=False, size=22.0, margin=50.0):
    """Нарисовать куб в правом верхнем углу cv. basis — (вправо, вверх, спереди) главного вида в координатах модели."""
    R, U, N = basis
    ax = {'R': R, 'U': U, 'N': N}
    keys = ['R', 'U', 'N']
    h = size
    ox, oy = cv.ActualWidth - margin, margin
    BAND = 0.7
    ink = _b(200, 211, 204) if dark else _b(78, 93, 84)
    hot = _b(100, 181, 246) if dark else _b(30, 136, 229)
    clear = _b(255, 255, 255, 1)

    def pr(p):
        return project(p, yaw, pitch, roll)

    def fac(n):
        return -pr(n)[2]

    def P(c):
        return _add(_mul(R, c.get('R', 0)), _mul(U, c.get('U', 0)), _mul(N, c.get('N', 0)))

    def sp(p):
        q = pr(p)
        return Point(ox + q[0] * h, oy + q[1] * h)
    groups, labels = {}, []
    for k in keys:
        o1, o2 = [x for x in keys if x != k]
        for sg in (1, -1):
            n = _mul(ax[k], sg)
            fc = fac(n)
            if fc <= 0.01:
                continue
            face = Polygon()
            for a_, b_ in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                face.Points.Add(sp(P({k: sg, o1: a_, o2: b_})))
            g = int(236 * (0.62 + 0.38 * fc))
            face.Fill = _b(g, min(255, g + 5), min(255, g + 2))
            face.Stroke, face.StrokeThickness, face.IsHitTestVisible = _b(85, 99, 90), 1.3, False
            cv.Children.Add(face)
            if fc > 0.3:
                labels.append((sp(n), NAMES[(k, sg)]))
            rng = {-1: (-1.0, -BAND), 0: (-BAND, BAND), 1: (BAND, 1.0)}
            for i in (-1, 0, 1):
                for j in (-1, 0, 1):
                    z = Polygon()
                    for a_, b_ in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        z.Points.Add(sp(P({k: sg, o1: rng[i][a_], o2: rng[j][b_]})))
                    z.Fill = clear
                    d = _add(n, _mul(ax[o1], i), _mul(ax[o2], j))
                    z.Tag = ('cube', d)
                    z.Cursor = Cursors.Hand
                    z.ToolTip = (u'Вид ' + NAMES[(k, sg)].lower()) if not (i or j) else (u'Ребро' if not (i and j) else u'Угол — изометрия')
                    groups.setdefault(tuple(int(round(x)) for x in d), []).append(z)
    hl = _b(156, 199, 238)

    def key_of(s_):
        return tuple(int(round(x)) for x in s_.Tag[1])

    def on_in(s_, e_):
        for z in groups.get(key_of(s_), []):
            z.Fill = hl

    def on_out(s_, e_):
        for z in groups.get(key_of(s_), []):
            z.Fill = clear
    for zs in groups.values():
        for z in zs:
            z.MouseEnter += on_in
            z.MouseLeave += on_out
            cv.Children.Add(z)
    for q, name in labels:
        tb = TextBlock()
        tb.Text, tb.FontSize, tb.Foreground, tb.IsHitTestVisible = name, 7.5, _b(60, 74, 66), False
        tb.Measure(Size(200, 50))
        Canvas.SetLeft(tb, q.X - tb.DesiredSize.Width / 2.0)
        Canvas.SetTop(tb, q.Y - tb.DesiredSize.Height / 2.0)
        cv.Children.Add(tb)
    if max([abs(fac(ax[k])) for k in keys]) <= 0.995:
        return
    # строго на грань: дуги поворота (справа сверху) и треугольники на соседние грани

    def roll_arrow(t0, t1, deg, tip):
        r_ = h * 2.2
        pts = [(ox + r_ * math.cos(math.radians(t0 + (t1 - t0) * q / 12.0)),
                oy - r_ * math.sin(math.radians(t0 + (t1 - t0) * q / 12.0))) for q in range(13)]
        sg = 1.0 if t1 > t0 else -1.0
        th = math.radians(t1)
        tx, ty = -math.sin(th) * sg, -math.cos(th) * sg
        ex, ey = pts[-1]
        head = Polygon()
        for px, py in ((ex + tx * 6, ey + ty * 6), (ex - ty * 4, ey + tx * 4), (ex + ty * 4, ey - tx * 4)):
            head.Points.Add(Point(px, py))
        line, hit = Polyline(), Polyline()
        for px, py in pts:
            line.Points.Add(Point(px, py))
            hit.Points.Add(Point(px, py))
        hit.Stroke, hit.StrokeThickness = clear, 10
        line.Stroke, line.StrokeThickness, line.IsHitTestVisible = ink, 1.6, False
        head.Fill, head.IsHitTestVisible = ink, False
        hit.Tag, hit.Cursor, hit.ToolTip = ('roll', deg), Cursors.Hand, tip

        def a_in(s_, e_):
            line.Stroke = head.Fill = hot

        def a_out(s_, e_):
            line.Stroke = head.Fill = ink
        hit.MouseEnter += a_in
        hit.MouseLeave += a_out
        for el in (line, head, hit):
            cv.Children.Add(el)
    roll_arrow(48, 70, 90.0, u'Повернуть вид на 90° против часовой')
    roll_arrow(42, 20, -90.0, u'Повернуть вид на 90° по часовой')
    rs, us, _ = screen_axes(yaw, pitch, roll)
    for text, x, y, d, tip in ((u'▲', ox - 5, oy - 50, us, u'Соседняя грань сверху'),
                                (u'▼', ox - 5, oy + 34, _mul(us, -1), u'Соседняя грань снизу'),
                                (u'◀', ox - 50, oy - 8, _mul(rs, -1), u'Соседняя грань слева'),
                                (u'▶', ox + 38, oy - 8, rs, u'Соседняя грань справа')):
        tb = TextBlock()
        tb.Text, tb.FontSize, tb.Foreground, tb.Cursor, tb.ToolTip = text, 11, ink, Cursors.Hand, tip
        tb.Tag = ('cube', d)

        def t_in(s_, e_):
            s_.Foreground = hot

        def t_out(s_, e_):
            s_.Foreground = ink
        tb.MouseEnter += t_in
        tb.MouseLeave += t_out
        Canvas.SetLeft(tb, x)
        Canvas.SetTop(tb, y)
        cv.Children.Add(tb)
