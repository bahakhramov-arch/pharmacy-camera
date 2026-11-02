# -*- coding: utf-8 -*-
"""Слежение за людьми между кадрами (упрощённый ByteTrack).

Каждая фигура получает номер (track id) и держит его, пока видна: даже если человек
стоит спиной и лица не видно. Имя (кто это) приклеивается к треку позже - по лицу.
"""

import numpy as np
from scipy.optimize import linear_sum_assignment


def iou(a, b):
    """a, b - (x, y, w, h)."""
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


class Track:
    _next = 1

    def __init__(self, box, score, t):
        self.id = Track._next
        Track._next += 1
        self.box = np.array(box, dtype=np.float32)      # x, y, w, h
        self.vel = np.zeros(2, dtype=np.float32)          # скорость центра, пикс/с
        self.score = score
        self.first_t = self.last_t = t
        self.hits = 1
        self.misses = 0
        self.confirmed = False

    def predict(self, t):
        """Где трек должен быть сейчас (простая постоянная скорость)."""
        dt = max(0.0, t - self.last_t)
        b = self.box.copy()
        b[:2] += self.vel * min(dt, 1.0)
        return b

    def update(self, box, score, t):
        box = np.array(box, dtype=np.float32)
        dt = t - self.last_t
        if dt > 0:
            c_old = self.box[:2] + self.box[2:] / 2
            c_new = box[:2] + box[2:] / 2
            self.vel = 0.6 * self.vel + 0.4 * (c_new - c_old) / dt
        self.box = 0.3 * self.box + 0.7 * box             # сглаживаем дрожание рамки
        self.score = score
        self.last_t = t
        self.hits += 1
        self.misses = 0
        if self.hits >= 3:
            self.confirmed = True

    def center(self):
        return self.box[:2] + self.box[2:] / 2


class Tracker:
    """match_iou - минимальное перекрытие для «тот же человек»;
    max_lost_sec - сколько секунд держать трек без детекции (заслонили, присел)."""

    def __init__(self, match_iou=0.25, max_lost_sec=8.0, high=0.5):
        self.tracks = []
        self.match_iou = match_iou
        self.max_lost_sec = max_lost_sec
        self.high = high

    def _match(self, tracks, dets, t, thr):
        if not tracks or not dets:
            return [], list(range(len(tracks))), list(range(len(dets)))
        cost = np.ones((len(tracks), len(dets)), dtype=np.float32)
        for i, tr in enumerate(tracks):
            p = tr.predict(t)
            for j, d in enumerate(dets):
                cost[i, j] = 1.0 - iou(p, d[:4])
        rows, cols = linear_sum_assignment(cost)
        pairs, used_t, used_d = [], set(), set()
        for i, j in zip(rows, cols):
            if 1.0 - cost[i, j] >= thr:
                pairs.append((i, j))
                used_t.add(i)
                used_d.add(j)
        return (pairs, [i for i in range(len(tracks)) if i not in used_t],
                [j for j in range(len(dets)) if j not in used_d])

    def update(self, dets, t):
        """dets - [(x, y, w, h, score)]; t - время в секундах. Возвращает живые подтверждённые треки."""
        high = [d for d in dets if d[4] >= self.high]
        low = [d for d in dets if d[4] < self.high]
        # 1) уверенные детекции ко всем трекам
        pairs, left_t, left_d = self._match(self.tracks, high, t, self.match_iou)
        for i, j in pairs:
            self.tracks[i].update(high[j][:4], high[j][4], t)
        # 2) слабые детекции - к оставшимся трекам (человек частично заслонён)
        rest = [self.tracks[i] for i in left_t]
        pairs2, left_t2, _ = self._match(rest, low, t, self.match_iou)
        for i, j in pairs2:
            rest[i].update(low[j][:4], low[j][4], t)
        for i in left_t2:
            rest[i].misses += 1
        # 3) новые треки - только из уверенных детекций
        for j in left_d:
            self.tracks.append(Track(high[j][:4], high[j][4], t))
        self.tracks = [tr for tr in self.tracks if t - tr.last_t <= self.max_lost_sec]
        return [tr for tr in self.tracks if tr.confirmed]
