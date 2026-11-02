# -*- coding: utf-8 -*-
"""Кто этот трек: голосование по всем лицам, увиденным за время жизни трека.

Старая система решала по одному-трём кадрам: лицо сверху, в профиль, мелкое - и ошибка.
Здесь у фигуры копятся все её лица, имя присваивается, когда доказательств достаточно,
и дальше держится, пока человек виден (даже спиной).
"""

from collections import defaultdict

import numpy as np


class Identity:
    def __init__(self, min_obs=3, threshold=0.36, margin=0.05, top_k=5):
        self.min_obs = min_obs          # столько лиц трека нужно, чтобы назвать его
        self.threshold = threshold      # среднее лучших совпадений не ниже
        self.margin = margin            # и отрыв от второго кандидата
        self.top_k = top_k
        self.obs = defaultdict(list)    # track id -> [ {имя: сходство} ]
        self.names = {}                 # track id -> имя (принятое решение)

    def add(self, track_id, scores):
        """scores - {имя: сходство} для одного лица этого трека."""
        self.obs[track_id].append(scores)
        self._decide(track_id)

    def _decide(self, tid):
        obs = self.obs[tid]
        if len(obs) < self.min_obs:
            return
        per_name = defaultdict(list)
        for o in obs:
            for n, s in o.items():
                per_name[n].append(s)
        ranked = sorted(((float(np.mean(sorted(v, reverse=True)[:self.top_k])), n)
                         for n, v in per_name.items()), reverse=True)
        best_s, best = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        # сколько раз этот человек был лучшим кандидатом для отдельного лица
        wins = sum(1 for o in obs if max(o, key=o.get) == best)
        if best_s >= self.threshold and best_s - second >= self.margin and wins >= self.min_obs:
            self.names[tid] = best

    def name(self, tid):
        return self.names.get(tid)

    def evidence(self, tid):
        return len(self.obs.get(tid, []))

    def inherit(self, new_tid, old_tid):
        """Трек разорвался (заслонили) - новый трек на том же месте получает имя старого."""
        if old_tid in self.names and new_tid not in self.names:
            self.names[new_tid] = self.names[old_tid]
            self.obs[new_tid] = list(self.obs.get(old_tid, []))[-20:]

    def forget(self, tid):
        self.obs.pop(tid, None)
        self.names.pop(tid, None)
