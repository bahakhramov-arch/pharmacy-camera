# -*- coding: utf-8 -*-
"""API управления филиалом для панели (/panel): фото сотрудников, запись лица с камеры,
правка журнала, настройки secrets.env, перезапуск, живое видео и просмотр записи.

Всё только при заданном пароле сайта (REPORTS_PASSWORD). Функции программы учёта
(запись лица, перезагрузка фото, перезапуск) приходят через config["control"]; если сайт
запущен отдельно (python web_reports.py), эти кнопки отвечают 501.
"""

import csv
import datetime as dt
import os
import re
import tempfile
import threading
import time

from flask import Response, jsonify, request, send_file

PHOTO_EXT = (".jpg", ".jpeg", ".png")
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ0-9-]{1,40}$")   # без «_»: он отделяет Имя_camN

# Что можно менять в secrets.env из панели. Токен бота и пароли - только руками на ПК.
CONFIG_KEYS = {
    "BRANCH": "Название филиала",
    "CHAT_ID": "Получатели уведомлений и итогов (id через запятую)",
    "STAFF_CHAT_ID": "Группа сотрудников: только «пришёл(ла)»",
    "BOT_COMMANDS": "Бот принимает команды на этом ПК (1/0)",
    "DAILY_SUMMARY_TIME": "Время итога дня (ЧЧ:ММ)",
    "STAFF_CHANNELS": "Камеры сотрудников (номера через запятую)",
    "VISITOR_CHANNELS": "Камеры двери для подсчёта клиентов",
    "MATCH_THRESHOLD": "Порог распознавания (у кого есть фото с камеры)",
    "STRICT_THRESHOLD": "Порог для тех, у кого только обычное фото (0 = как все)",
    "PERSON_THRESHOLDS": "Личные пороги: Имя:0.46,Имя2:0.44",
    "MIN_ID_FACE_SCORE": "Мин. уверенность «это лицо» (отсекает руки, 0 = выкл.)",
}
SECRET_KEYS = ("BOT_TOKEN", "NVR_PASSWORD", "REPORTS_PASSWORD", "PANEL_PASSWORD")


def register(app, config, requires_auth, base_dir):
    password = config.get("password") or ""
    ctl = config.get("control") or {}
    env_path = ctl.get("env_path") or os.path.join(base_dir, "secrets.env")
    photos_dir = ctl.get("photos_dir") or os.path.join(base_dir, "photos")
    log_lock = ctl.get("log_lock") or threading.Lock()
    job = {"running": False}
    job_lock = threading.Lock()

    def guarded(f):
        """Пароль сайта обязателен: без него управлять филиалом может любой в сети."""
        def wrapper(*a, **kw):
            if not password:
                return jsonify({"error": "Управление доступно только при заданном пароле сайта"}), 403
            return f(*a, **kw)
        wrapper.__name__ = f.__name__
        return requires_auth(wrapper)

    def need(key):
        fn = ctl.get(key)
        if fn is None:
            return None, (jsonify({"error": "Недоступно: сайт запущен без программы учёта"}), 501)
        return fn, None

    def safe_photo(fname):
        fname = os.path.basename(fname or "")
        if not fname.lower().endswith(PHOTO_EXT):
            return None
        path = os.path.join(photos_dir, fname)
        return path if os.path.isfile(path) else None

    def resync():
        fn = ctl.get("resync")
        if fn:
            fn()

    # ---------- сотрудники и фото ----------
    @app.get("/api/staff")
    @guarded
    def staff_list():
        import settings
        issues = ctl["photo_issues"]() if ctl.get("photo_issues") else {}
        cam = set(ctl["cam_enrolled"]()) if ctl.get("cam_enrolled") else set()
        people = {}
        if os.path.isdir(photos_dir):
            for fname in sorted(os.listdir(photos_dir)):
                if not fname.lower().endswith(PHOTO_EXT):
                    continue
                name = os.path.splitext(fname)[0].split("_")[0].strip()
                people.setdefault(name, []).append({
                    "file": fname, "cam": "_cam" in fname.lower(), "issue": issues.get(fname)})
        thr = {}
        for item in os.environ.get("PERSON_THRESHOLDS", "").split(","):
            if ":" in item:
                n, v = item.rsplit(":", 1)
                thr[n.strip().lower()] = v.strip()
        out = []
        for name, files in sorted(people.items(), key=lambda kv: kv[0].lower()):
            start, end = settings.shift_range(name)
            out.append({"name": name, "photos": files, "cam_enrolled": name in cam,
                        "threshold": thr.get(name.lower()), "shift": f"{start}–{end}"})
        employees = config.get("employees")
        return jsonify({"staff": out, "loaded": list(employees()) if employees else [],
                        "channels": ctl.get("channels") or {}, "enroll": dict(job)})

    @app.get("/api/photo")
    @guarded
    def photo_get():
        path = safe_photo(request.args.get("file"))
        if not path:
            return jsonify({"error": "Нет такого фото"}), 404
        return send_file(path)

    @app.post("/api/photo")
    @guarded
    def photo_add():
        import cv2
        import numpy as np
        name = (request.form.get("name") or "").strip()
        f = request.files.get("file")
        if not NAME_RE.match(name):
            return jsonify({"error": "Имя - одно слово, буквы/цифры/дефис, без пробелов и «_»"}), 400
        if not f:
            return jsonify({"error": "Не выбран файл"}), 400
        img = cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({"error": "Это не картинка (нужен JPG или PNG)"}), 400
        h, w = img.shape[:2]
        if max(h, w) > 1600:                   # телефонные фото по 4000px - уменьшаем
            k = 1600 / max(h, w)
            img = cv2.resize(img, (int(w * k), int(h * k)))
        os.makedirs(photos_dir, exist_ok=True)
        fname, k = f"{name}.jpg", 2
        while os.path.exists(os.path.join(photos_dir, fname)):
            fname, k = f"{name}_p{k}.jpg", k + 1
        cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tofile(os.path.join(photos_dir, fname))
        return jsonify({"saved": fname})

    @app.delete("/api/photo")
    @guarded
    def photo_delete():
        path = safe_photo(request.args.get("file"))
        if not path:
            return jsonify({"error": "Нет такого фото"}), 404
        os.remove(path)
        return jsonify({"deleted": os.path.basename(path)})

    @app.post("/api/enroll")
    @guarded
    def enroll_start():
        fn, err = need("enroll")
        if err:
            return err
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        try:
            ch, secs = int(data.get("ch")), int(data.get("secs") or 25)
        except (TypeError, ValueError):
            return jsonify({"error": "Нужны имя, камера и секунды"}), 400
        if not NAME_RE.match(name) or not 1 <= ch <= 32 or not 5 <= secs <= 90:
            return jsonify({"error": "Имя - одно слово; камера 1-32; 5-90 секунд"}), 400
        with job_lock:
            if job.get("running"):
                return jsonify({"error": "Уже идёт запись другого сотрудника"}), 409
            job.clear()
            job.update(running=True, name=name, ch=ch, secs=secs,
                       started=dt.datetime.now().strftime("%H:%M:%S"))

        def run():
            try:
                saved = fn(name, ch, secs)
                job.update(saved=saved, error=None if saved else
                           "Лицо не найдено: пусть подойдёт ближе и смотрит прямо в камеру")
            except Exception as e:
                job.update(saved=[], error=str(e))
            finally:
                job["running"] = False
        threading.Thread(target=run, daemon=True, name="enroll").start()
        return jsonify(dict(job))

    @app.get("/api/enroll")
    @guarded
    def enroll_status():
        return jsonify(dict(job))

    @app.post("/api/reload_faces")
    @guarded
    def reload_faces():
        fn, err = need("reload_faces")
        if err:
            return err
        return jsonify(fn())

    # ---------- журнал сотрудников ----------
    def _read_rows():
        path = config["events_file"]
        if not os.path.exists(path):
            return [], []
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        return rows[:1], rows[1:]

    def _write_rows(head, body):
        path = config["events_file"]
        head = head or [["Дата", "Время", "Сотрудник", "Филиал", "Событие", "Опоздание"]]
        body.sort(key=lambda r: (r[0], r[1]) if len(r) > 1 else ("", ""))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(head + body)
        os.replace(tmp, path)

    @app.get("/api/events")
    @guarded
    def events_list():
        day = request.args.get("day") or dt.date.today().isoformat()
        _, body = _read_rows()
        out = [{"date": r[0], "time": r[1], "name": r[2], "type": r[4],
                "late": int(r[5]) if len(r) > 5 and r[5].lstrip("-").isdigit() else 0}
               for r in body if len(r) >= 5 and r[0] == day]
        return jsonify({"day": day, "events": out})

    @app.post("/api/events")
    @guarded
    def events_add():
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        kind = data.get("type")
        try:
            day = dt.date.fromisoformat(data.get("day") or dt.date.today().isoformat())
            t = dt.time.fromisoformat(data.get("time") or "")
        except ValueError:
            return jsonify({"error": "Неверная дата или время"}), 400
        when = dt.datetime.combine(day, t)
        if not name or kind not in ("приход", "уход", "исправить приход"):
            return jsonify({"error": "Нужны сотрудник и тип: приход / уход / исправить приход"}), 400
        if when > dt.datetime.now():
            return jsonify({"error": "Это время ещё не наступило"}), 400
        if kind == "исправить приход":
            fn, err = need("fix_arrival")
            if err:
                return err
            removed, late = fn(name, when)
            resync()
            return jsonify({"ok": True, "removed": removed, "late": late})
        late = 0
        if kind == "приход" and ctl.get("late"):
            late = ctl["late"](name, when)
        branch = config.get("branch", "")
        with log_lock:
            head, body = _read_rows()
            body.append([when.date().isoformat(), when.strftime("%H:%M:%S"), name, branch, kind, late])
            _write_rows(head, body)
        resync()
        return jsonify({"ok": True, "late": late})

    @app.delete("/api/events")
    @guarded
    def events_delete():
        data = request.get_json(silent=True) or {}
        key = (data.get("date"), data.get("time"), data.get("name"), data.get("type"))
        with log_lock:
            head, body = _read_rows()
            for i, r in enumerate(body):
                if len(r) >= 5 and (r[0], r[1], r[2], r[4]) == key:
                    del body[i]
                    _write_rows(head, body)
                    break
            else:
                return jsonify({"error": "Такой записи нет (уже удалена?)"}), 404
        resync()
        return jsonify({"ok": True})

    # ---------- настройки secrets.env и перезапуск ----------
    def _env_lines():
        if not os.path.exists(env_path):
            return []
        with open(env_path, encoding="utf-8-sig") as f:
            return f.read().splitlines()

    @app.get("/api/config")
    @guarded
    def config_get():
        values = {}
        for line in _env_lines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                values[k.strip()] = v.strip()
        return jsonify({
            "keys": [{"key": k, "label": lbl, "value": values.get(k, "")} for k, lbl in CONFIG_KEYS.items()],
            "secrets": {k: bool(values.get(k)) for k in SECRET_KEYS},
            "note": "Изменения вступают в силу после перезапуска программы",
        })

    @app.post("/api/config")
    @guarded
    def config_set():
        data = request.get_json(silent=True) or {}
        changes = {k: str(v).strip() for k, v in data.items() if k in CONFIG_KEYS}
        if any("\n" in v or "\r" in v for v in changes.values()):
            return jsonify({"error": "Перенос строки в значении недопустим"}), 400
        lines, done = _env_lines(), set()
        for i, line in enumerate(lines):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k = s.split("=", 1)[0].strip()
                if k in changes:
                    lines[i] = f"{k}={changes[k]}"
                    done.add(k)
        lines += [f"{k}={v}" for k, v in changes.items() if k not in done]
        tmp = env_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, env_path)
        return jsonify({"saved": sorted(changes), "note": "Нажмите «Перезапустить программу»"})

    @app.post("/api/restart")
    @guarded
    def restart():
        fn, err = need("restart")
        if err:
            return err
        fn()
        return jsonify({"ok": True, "note": "Программа перезапустится примерно через 15 секунд"})

    # ---------- видео ----------
    def _mjpeg(frames):
        def gen():
            for jpg in frames:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-store"})

    def _encode(frame, width=960):
        import cv2
        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(frame, (width, int(h * width / w)))
        return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()

    @app.get("/api/live")
    @guarded
    def live():
        """Живое видео (MJPEG, ~4 кадра/с, до 5 минут): <img src="/api/live?ch=5">"""
        import cv2
        fn, err = need("camera_url")
        if err:
            return err
        try:
            ch = int(request.args.get("ch", ""))
        except ValueError:
            return jsonify({"error": "Пример: /api/live?ch=5"}), 400

        def frames():
            cap = cv2.VideoCapture(fn(ch, 1), cv2.CAP_FFMPEG)     # облегчённый поток
            try:
                t0, last = time.time(), 0.0
                while time.time() - t0 < 300:
                    ok, f = cap.read()
                    if not ok:
                        break
                    if time.time() - last >= 0.25:
                        last = time.time()
                        yield _encode(f)
            finally:
                cap.release()
        return _mjpeg(frames())

    @app.get("/api/replay")
    @guarded
    def replay():
        """Просмотр записи: /api/replay?ch=6&start=2026-10-30T10:26&minutes=2&speed=2"""
        import cv2
        import requests
        from requests.auth import HTTPDigestAuth
        try:
            ch = int(request.args.get("ch", ""))
            start = dt.datetime.fromisoformat(request.args.get("start", ""))
            minutes = float(request.args.get("minutes", "2"))
            speed = max(0.5, min(float(request.args.get("speed", "2")), 8))
        except ValueError:
            return jsonify({"error": "Пример: /api/replay?ch=6&start=2026-10-30T10:26&minutes=2"}), 400
        if not 0 < minutes <= 10:
            return jsonify({"error": "Не больше 10 минут за раз"}), 400
        end = start + dt.timedelta(minutes=minutes)
        ip = os.environ.get("NVR_IP", "192.168.1.99")
        url = (f"http://{ip}/cgi-bin/loadfile.cgi?action=startLoad&channel={ch}"
               f"&startTime={start:%Y-%m-%d%%20%H:%M:%S}&endTime={end:%Y-%m-%d%%20%H:%M:%S}&subtype=0")
        fd, tmp = tempfile.mkstemp(suffix=".dav")
        os.close(fd)
        try:
            with requests.get(url, auth=HTTPDigestAuth(os.environ.get("NVR_LOGIN", "admin"),
                                                       os.environ.get("NVR_PASSWORD", "")),
                              stream=True, timeout=60) as r:
                if not r.ok:
                    os.remove(tmp)
                    return jsonify({"error": f"Регистратор ответил {r.status_code} (записи нет?)"}), 404
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
        except Exception as e:
            os.remove(tmp)
            return jsonify({"error": f"Регистратор недоступен: {e}"}), 502

        def frames():
            cap = cv2.VideoCapture(tmp)
            try:
                fps = cap.get(cv2.CAP_PROP_FPS) or 25
                fps = fps if 1 <= fps <= 60 else 25
                step = max(1, round(fps * speed / 5))          # ~5 кадров/с на экране
                i, t_next = 0, time.time()
                while True:
                    ok, f = cap.read()
                    if not ok:
                        break
                    i += 1
                    if i % step:
                        continue
                    cv2.putText(f, (start + dt.timedelta(seconds=i / fps)).strftime("%H:%M:%S"),
                                (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (255, 255, 255), 4)
                    yield _encode(f)
                    t_next += 0.2
                    time.sleep(max(0.0, t_next - time.time()))
            finally:
                cap.release()
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return _mjpeg(frames())
