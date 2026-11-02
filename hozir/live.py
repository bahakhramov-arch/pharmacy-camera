# -*- coding: utf-8 -*-
"""Hozir в программе учёта: «теневой режим».

Работает параллельно со старым распознаванием в своём потоке и пишет приходы/уходы
в hozir_events.csv (без уведомлений) - чтобы сравнить со старой системой и реальностью.

secrets.env:
    HOZIR=1
    HOZIR_ZONES=6:0.42,0,1,0.7;5:0,0,1,1      зона поиска людей на камере (доли кадра)
    HOZIR_MIN_BOTTOM=5:0.55                   низ фигуры ниже этой линии (камера за прилавком:
                                              клиенты по ту сторону прилавка видны только по пояс)
    HOZIR_FPS=4
"""

import csv
import datetime as dt
import os
import threading
import time

from hozir.pipeline import CameraPipeline
from hozir.presence import Presence

EVENTS_FILE = "hozir_events.csv"


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


class HozirLive:
    def __init__(self, db, branch, channels, label):
        zones = _parse(os.environ.get("HOZIR_ZONES"), lambda v: tuple(float(x) for x in v.split(",")))
        self.min_bottom = {label(ch): v for ch, v in
                           _parse(os.environ.get("HOZIR_MIN_BOTTOM"), float).items()}
        self.fps = float(os.environ.get("HOZIR_FPS", "4"))
        self.branch = branch
        self.cams = {label(ch): CameraPipeline(db, person_score=0.25, roi=zones.get(ch)) for ch in channels}
        self.presence = Presence(float(os.environ.get("HOZIR_LEAVE_MIN", "20")))
        self.latest = {}
        self.lock = threading.Lock()
        self.ms = 0.0
        threading.Thread(target=self._run, daemon=True, name="hozir").start()
        print(f"[Hozir] теневой режим: камеры {', '.join(self.cams)}")

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
            # треки, которые сейчас не в кадре, но не «умерли», не учитываются - уход по таймауту
            for t_ev, name, kind in self.presence.update(t0, named):
                when = dt.datetime.fromtimestamp(t_ev)
                print(f"[Hozir] {name}: {kind} в {when:%H:%M:%S}")
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
