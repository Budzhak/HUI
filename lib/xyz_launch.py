# -*- coding: utf-8 -*-
"""Запуск команды «По красоте» (Align.pushbutton/script.py) с готовым выбором —
для отдельных кнопок панели «Оформить» (17.10.2026).

    import xyz_launch
    xyz_launch.align(u"Колонка с угловыми выносками (сторона по точке)", globals())

Скрипт «По красоте» читает выбор из sys.xyz_align_action и не показывает меню.
Настройки (шаг, длины выносок) общие для всех кнопок: script.get_config('xyz_align')."""
import os
import sys

_EXT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TAB = os.path.join(_EXT, u'XYZ.tab')


def _find_align():
    """Папка «По красоте» — где бы ни лежала на вкладке (панели переименовываются,
    17.10.2026 кнопка переехала с «Сделать» на «Оформить»)."""
    for root, dirs, files in os.walk(_TAB):
        if os.path.basename(root) == u'Align.pushbutton' and u'script.py' in files:
            return root
    return os.path.join(_TAB, u'Annotate.panel', u'Align.pushbutton')


ALIGN_DIR = _find_align()


def align(action, g):
    global ALIGN_DIR
    if not os.path.isfile(os.path.join(ALIGN_DIR, u'script.py')):
        ALIGN_DIR = _find_align()
    path = os.path.join(ALIGN_DIR, u'script.py')
    if ALIGN_DIR not in sys.path:
        sys.path.insert(0, ALIGN_DIR)
    sys.xyz_align_action = action
    ns = dict(g)
    ns['__file__'] = path
    ns['__name__'] = '__main__'
    try:
        execfile(path, ns)
    finally:
        sys.xyz_align_action = None
