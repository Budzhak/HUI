# -*- coding: utf-8 -*-
"""Настройки плагина ХУЙ (lib/hui_settings.py), db 20.10.2026.

Файл %APPDATA%\\pyRevit\\hui_settings.json. Меняются кнопкой «ХОЧУ → Обработчики».
Ключи:
  elev_marks_auto  — обработчик марок-отметок на взрыв-схемах пересчитывает их при
                     изменении модели (True/False, по умолчанию False: обработчик
                     регистрируется только когда включён).
  tab_after_insert — ставить вкладку ХУЙ сразу после «Вставить» (по умолчанию False).
"""
import io, os, json

PATH = os.path.join(os.environ.get('APPDATA', ''), 'pyRevit', 'hui_settings.json')
DEFAULTS = {'elev_marks_auto': False, 'tab_after_insert': False}
_cache = {'mtime': None, 'data': None}


def load():
    try:
        mt = os.path.getmtime(PATH)
    except Exception:
        return dict(DEFAULTS)
    if _cache['mtime'] != mt:
        try:
            with io.open(PATH, encoding='utf-8') as fh:
                d = json.loads(fh.read())
        except Exception:
            d = {}
        data = dict(DEFAULTS)
        data.update(d)
        _cache['mtime'], _cache['data'] = mt, data
    return dict(_cache['data'])


def get(key):
    return load().get(key, DEFAULTS.get(key))


def set(key, value):
    d = load()
    d[key] = value
    txt = json.dumps(d, indent=1)          # ensure_ascii — без проблем кодировки в IronPython
    with io.open(PATH, 'w', encoding='utf-8') as fh:
        fh.write(u'%s' % txt)
    _cache['mtime'] = None
