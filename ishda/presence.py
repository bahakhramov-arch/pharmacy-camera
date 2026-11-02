# -*- coding: utf-8 -*-
"""Приход и уход по фигурам, а не по лицам.

Старая логика: «лицо не видно 60 минут = ушёл». Камера сверху часто не видит лицо,
отсюда ложные «ушёл». Здесь человек на месте, пока на любой камере филиала есть его
фигура (трек с его именем). Ушёл - если его фигуры нигде нет leave_min минут подряд.
Время ухода - когда фигуру видели последний раз; время прихода - когда она появилась
(а не когда впервые удалось разглядеть лицо).
"""


class Presence:
    def __init__(self, leave_min=20.0):
        self.leave_sec = leave_min * 60
        self.state = {}       # имя -> {"present": bool, "since": t, "last": t}
        self.events = []      # [(t, имя, "приход"/"уход")]

    def update(self, t, named):
        """named - {имя: время появления самого раннего живого трека с этим именем} на этом кадре
        (по всем камерам филиала). Возвращает новые события."""
        new = []
        for name, first_t in named.items():
            st = self.state.get(name)
            if st is None or not st["present"]:
                self.state[name] = {"present": True, "since": first_t, "last": t}
                new.append((first_t, name, "приход"))
            else:
                st["last"] = t
        for name, st in self.state.items():
            if st["present"] and name not in named and t - st["last"] > self.leave_sec:
                st["present"] = False
                new.append((st["last"], name, "уход"))
        self.events += new
        return new

    def present(self):
        return sorted(n for n, st in self.state.items() if st["present"])
