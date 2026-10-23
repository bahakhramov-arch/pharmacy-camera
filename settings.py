# -*- coding: utf-8 -*-
"""Настройки смен, которые меняются без кода: сайт (вкладка «Настройки») и бот (/smena ...).

Хранятся в settings.json рядом с программой (в git не попадает - у каждого филиала свои).
Программа перечитывает файл, когда он меняется, - перезапуск не нужен.
"""

import json
import os
import re
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE_DIR, "settings.json")

# Значения по умолчанию, пока settings.json ещё нет (аптека работает 08:00-23:00)
DEFAULTS = {
    "default_shift": "08:00",       # начало смены для всех, у кого нет своей
    "grace_minutes": 10,            # допуск: опоздание, если позже на > N минут
    "leave_timeout_min": 60,        # не виден дольше N минут = ушёл
    "shifts": {"nurbonu": "08:00"},  # своё начало смены: имя (как у фото, без регистра) -> ЧЧ:ММ
}

_lock = threading.Lock()
_cache = {"mtime": None, "data": None}
_TIME = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")


def norm_time(s):
    """«8:5»/«08.05»/«0805» -> «08:05»; None, если это не время."""
    s = str(s).strip()
    if re.fullmatch(r"\d{3,4}", s):
        s = s[:-2] + ":" + s[-2:]
    m = _TIME.match(s)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _clean(data):
    out = dict(DEFAULTS)
    out["shifts"] = {}
    if not isinstance(data, dict):
        data = {}
    t = norm_time(data.get("default_shift", DEFAULTS["default_shift"]))
    out["default_shift"] = t or DEFAULTS["default_shift"]
    for key, lo, hi in (("grace_minutes", 0, 120), ("leave_timeout_min", 10, 600)):
        try:
            v = int(data.get(key, DEFAULTS[key]))
            out[key] = v if lo <= v <= hi else DEFAULTS[key]
        except (TypeError, ValueError):
            out[key] = DEFAULTS[key]
    for name, val in (data.get("shifts") or {}).items():
        t = norm_time(val)
        if t and str(name).strip():
            out["shifts"][str(name).strip().lower()] = t
    return out


def load():
    """Текущие настройки (с кэшем по времени изменения файла)."""
    with _lock:
        try:
            mtime = os.path.getmtime(PATH)
        except OSError:
            mtime = None
        if _cache["data"] is not None and mtime == _cache["mtime"]:
            return _cache["data"]
        data = DEFAULTS
        if mtime is not None:
            try:
                with open(PATH, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = DEFAULTS
        _cache.update(mtime=mtime, data=_clean(data))
        return _cache["data"]


def save(data):
    """Проверяет и сохраняет; возвращает то, что реально записано."""
    clean = _clean(data)
    with _lock:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)
        _cache.update(mtime=None, data=None)
    return clean


def update(**changes):
    cur = load()
    new = {**cur, "shifts": dict(cur["shifts"])}
    new.update({k: v for k, v in changes.items() if k != "shifts"})
    if "shifts" in changes:
        new["shifts"] = changes["shifts"]
    return save(new)


def shift_for(name):
    s = load()
    return s["shifts"].get(name.lower(), s["default_shift"])
