# -*- coding: utf-8 -*-
"""Ishda AI в программе учёта: «теневой режим».

Работает параллельно со старым распознаванием в своём потоке и пишет приходы/уходы
в ishda_events.csv (без уведомлений) - чтобы сравнить со старой системой и реальностью.

secrets.env (старые имена HOZIR_* тоже работают):
    ISHDA=1
    ISHDA_ZONES=6:0.42,0,1,0.7;5:0,0,1,1      зона поиска людей на камере (доли кадра)
    ISHDA_MIN_BOTTOM=5:0.55                   низ фигуры ниже этой линии (камера за прилавком:
                                              клиенты по ту сторону прилавка видны только по пояс)
    ISHDA_FPS=4
"""

import csv
import datetime as dt
import os
import threading
import time

from ishda.pipeline import CameraPipeline
from ishda.presence import Presence

EVENTS_FILE = "ishda_events.csv"


def env(key, default=None):
    """ISHDA_<key>, для совместимости - HOZIR_<key> (рабочее имя проекта)."""
    return os.environ.get("ISHDA_" + key, os.environ.get("HOZIR_" + key, default))


def _parse(raw, cast):
    out = {}
    for part in (raw or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                out[int(k)] = cast(v)
            except ValueError:
                pass
    return out


class IshdaLive:
    def __init__(self, db, branch, channels, label):
        zones = _parse(env("ZONES"), lambda v: tuple(float(x) for x in v.split(",")))
        self.min_bottom = {label(ch): v for ch, v in
                           _parse(env("MIN_BOTTOM"), float).items()}
        self.fps = float(env("FPS", "4"))
        self.branch = branch
        self.cams = {label(ch): CameraPipeline(db, person_score=0.25, roi=zones.get(ch)) for ch in channels}
        self.presence = Presence(float(env("LEAVE_MIN", "20")))
        self.day = dt.date.today()
        self._recover()
        self.latest = {}
        self.lock = threading.Lock()
        self.ms = 0.0
        threading.Thread(target=self._run, daemon=True, name="ishda").start()
        print(f"[Ishda] теневой режим: камеры {', '.join(self.cams)}")

    def _recover(self):
        """После перезапуска программы: кто по журналу Ishda сейчас на работе - тот на месте
        (иначе каждый перезапуск давал всем новый «приход»). Таймаут ухода - от момента запуска."""
        if not os.path.exists(EVENTS_FILE):
            return
        today, last = self.day.isoformat(), {}
        with open(EVENTS_FILE, encoding="utf-8-sig", newline="") as f:
            for r in csv.reader(f):
                if len(r) >= 5 and r[0] == today:
                    last[r[2]] = r
        now = time.time()
        for name, r in last.items():
            if r[4] == "приход":
                since = dt.datetime.fromisoformat(f"{r[0]} {r[1]}").timestamp()
                self.presence.state[name] = {"present": True, "since": since, "last": now}
        if self.presence.present():
            print("[Ishda] после перезапуска на месте: " + ", ".join(self.presence.present()))

    def _new_day(self, now):
        """Полночь: кто ещё «на месте» - уход в момент, когда его видели последний раз
        (но не позже 23:59:59), новый день начинается с чистого листа."""
        end = dt.datetime.combine(self.day, dt.time(23, 59, 59)).timestamp()
        for name in self.presence.present():
            t_last = min(self.presence.state[name]["last"], end)
            when = dt.datetime.fromtimestamp(t_last)
            print(f"[Ishda] {name}: уход в {when:%H:%M:%S} (конец дня)")
            self._log(when, name, "уход")
        self.presence.state.clear()
        self.day = dt.date.fromtimestamp(now)

    def set_db(self, db):
        for p in self.cams.values():
            p.db = db

    def feed(self, cam, frame):
        """Из основного цикла: последний кадр камеры (копия не нужна - кадр больше не меняется)."""
        if cam in self.cams:
            with self.lock:
                self.latest[cam] = frame

    def _run(self):
        period = 1.0 / self.fps
        while True:
            t0 = time.time()
            if dt.date.fromtimestamp(t0) != self.day:
                self._new_day(t0)
            with self.lock:
                frames, self.latest = self.latest, {}
            named = {}
            for cam, frame in frames.items():
                pipe = self.cams[cam]
                _, res = pipe.step(frame, t0)
                h = frame.shape[0]
                for tr, name, _ in res:
                    if not name:
                        continue
                    mb = self.min_bottom.get(cam)
                    if mb is not None and (tr.box[1] + tr.box[3]) / h < mb:
                        continue
                    named[name] = min(named.get(name, tr.first_t), tr.first_t)
            day_start = dt.datetime.combine(self.day, dt.time()).timestamp()
            named = {n: max(t_first, day_start) for n, t_first in named.items()}   # трек со вчера
            # треки, которые сейчас не в кадре, но не «умерли», не учитываются - уход по таймауту
            for t_ev, name, kind in self.presence.update(t0, named):
                when = dt.datetime.fromtimestamp(t_ev)
                print(f"[Ishda] {name}: {kind} в {when:%H:%M:%S}")
                self._log(when, name, kind)
            spent = time.time() - t0
            if frames:                      # среднее время обработки кадров одного круга
                self.ms = 0.9 * self.ms + 0.1 * spent * 1000 if self.ms else spent * 1000
            time.sleep(max(0.0, period - spent))

    def _log(self, when, name, kind):
        new = not os.path.exists(EVENTS_FILE)
        with open(EVENTS_FILE, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["Дата", "Время", "Сотрудник", "Филиал", "Событие"])
            w.writerow([when.date().isoformat(), when.strftime("%H:%M:%S"), name, self.branch, kind])

    def status(self):
        return {"present": self.presence.present(), "ms_per_round": round(self.ms),
                "events": [(dt.datetime.fromtimestamp(t).strftime("%H:%M:%S"), n, k)
                           for t, n, k in self.presence.events[-30:]]}
