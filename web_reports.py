# -*- coding: utf-8 -*-
"""Сайт отчётов: посетители аптеки и сотрудники.

Запуск вместе с учётом (автоматически) или отдельно:
    python web_reports.py
Откройте в браузере: http://127.0.0.1:8080
"""

import datetime as dt
import logging
import os
import socket
import threading
from collections import defaultdict
from functools import wraps

from flask import Flask, jsonify, render_template, request, Response, send_file

import settings
from reports import (build_days, make_report, read_events, read_visitors, visitor_stats,
                     workday)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _day_list(start, end):
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += dt.timedelta(days=1)
    return days


def create_app(config):
    app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
    password = config.get("password") or ""

    def _live():
        fn = config.get("live_state")
        if fn:
            try:
                return fn() or {}
            except Exception:
                return {}
        return {}

    # Пароль управления (камеры, видео, фото, журнал, настройки ПК): логин admin.
    # У report (заведующая, координатор) - только отчёты и смены.
    control_password = (os.environ.get("CONTROL_PASSWORD") or os.environ.get("PANEL_PASSWORD") or "")

    def is_admin(auth):
        return bool(control_password and auth and auth.username == "admin"
                    and auth.password == control_password)

    def check_auth(user, pwd):
        return user == "report" and pwd == password

    def requires_auth(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not password:
                return f(*args, **kwargs)
            auth = request.authorization
            if not (is_admin(auth) or (auth and check_auth(auth.username, auth.password))):
                return Response("Нужна авторизация", 401,
                                {"WWW-Authenticate": 'Basic realm="Apteka 5plus", charset="UTF-8"'})
            return f(*args, **kwargs)
        return decorated

    def requires_admin(f):
        """Камеры, видео и управление - только admin с CONTROL_PASSWORD (не пароль report)."""
        @wraps(f)
        def decorated(*args, **kwargs):
            if not control_password or not password:
                return jsonify({"error": "Управление выключено: задайте REPORTS_PASSWORD и "
                                         "CONTROL_PASSWORD в secrets.env"}), 403
            if not is_admin(request.authorization):
                return Response("Нужен вход администратора", 401,
                                {"WWW-Authenticate": 'Basic realm="Apteka 5plus admin", charset="UTF-8"'})
            return f(*args, **kwargs)
        return decorated

    def parse_range():
        today = workday(dt.datetime.now(), config["day_start"])
        raw_from = request.args.get("from")
        raw_to = request.args.get("to")
        try:
            start = dt.date.fromisoformat(raw_from) if raw_from else today
            end = dt.date.fromisoformat(raw_to) if raw_to else today
        except ValueError:
            start = end = today
        if start > end:
            start, end = end, start
        return start, end, today

    @app.get("/")
    @requires_auth
    def index():
        return render_template("index.html", branch=config["branch"])

    @app.get("/api/overview")
    @requires_auth
    def overview():
        start, end, today = parse_range()
        now = dt.datetime.now()
        events = read_events(config["events_file"])
        visits = read_visitors(config["visitors_file"])
        live = _live()
        last_seen = live.get("last_seen") or {}
        present = set(live.get("staff_present") or [])

        v_period = visitor_stats(visits, start, end, config["day_start"])
        v_today = visitor_stats(visits, today, today, config["day_start"])
        v_period["by_day"] = {d.isoformat(): n for d, n in v_period["by_day"].items()}
        v_period["out_by_day"] = {d.isoformat(): n for d, n in v_period["out_by_day"].items()}
        v_period["by_hour_day"] = {d.isoformat(): hrs for d, hrs in v_period["by_hour_day"].items()}
        v_period["out_by_hour_day"] = {d.isoformat(): hrs
                                       for d, hrs in v_period["out_by_hour_day"].items()}
        if v_period["v_busiest_day"]:
            v_period["v_busiest_day"] = v_period["v_busiest_day"].isoformat()
        if v_period["busiest_day"]:
            v_period["busiest_day"] = v_period["busiest_day"].isoformat()

        days = build_days(events, start, end, last_seen, now, config["day_start"])
        agg = defaultdict(lambda: {"days": 0, "sec": 0.0, "late_cnt": 0, "late_min": 0})
        for (_, name), info in days.items():
            a = agg[name]
            a["days"] += 1
            a["sec"] += info["worked_sec"]
            if info["late"] > 0:
                a["late_cnt"] += 1
                a["late_min"] += info["late"]
        staff = [{"name": n, "days": a["days"], "hours": round(a["sec"] / 3600, 2),
                  "avg_hours": round(a["sec"] / 3600 / a["days"], 2) if a["days"] else 0,
                  "late_cnt": a["late_cnt"], "late_min": a["late_min"]}
                 for n, a in sorted(agg.items())]
        staff_days = [{
            "date": d.isoformat(), "name": n,
            "first_in": i["first_in"].strftime("%H:%M"),
            "last_out": i["last_out"].strftime("%H:%M") if i["last_out"] and not i["active"] else None,
            "worked_sec": i["worked_sec"], "late": i["late"],
            "active": i["active"], "unclosed": i["unclosed"] and not i["active"],
        } for (d, n), i in sorted(days.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True)]

        feed = [{"when": e["when"].isoformat(), "kind": "staff", "who": e["name"], "type": e["type"],
                 "late": e["late"]}
                for e in events if start <= workday(e["when"], config["day_start"]) <= end]
        feed += [{"when": v["when"].isoformat(), "kind": "client", "who": v["camera"],
                  "type": v["type"]}
                 for v in visits if start <= workday(v["when"], config["day_start"]) <= end]
        feed = sorted(feed, key=lambda x: x["when"], reverse=True)[:60]

        today_days = build_days(events, today, today, last_seen, now, config["day_start"])
        live_rows = []
        names = sorted({n for (_, n) in today_days} | present)
        for n in names:
            info = today_days.get((today, n), {})
            live_rows.append({
                "name": n,
                "present": n in present,
                "first_in": info["first_in"].strftime("%H:%M") if info.get("first_in") else None,
                "last_out": (info["last_out"].strftime("%H:%M")
                             if info.get("last_out") and n not in present else None),
                "worked_sec": info.get("worked_sec", 0),
                "late": info.get("late", 0),
            })

        entered_live = live.get("visitors_today")
        visitors_today = (entered_live if entered_live is not None else v_today["total"])

        return jsonify({
            "branch": config["branch"],
            "from": start.isoformat(),
            "to": end.isoformat(),
            "now": now.strftime("%H:%M:%S"),
            "visitors_today": visitors_today,
            "visitors_inside": live.get("visitors_inside", 0),
            "visitors_exited": live.get("visitors_exited", v_today["exits"]),
            # посетители за период = вышедшие (выходы сверены с кассой)
            "visitors_people": v_period["visitors"],
            "visitors": v_period,
            "day_list": [d.isoformat() for d in _day_list(start, end)],
            "staff": staff,
            "staff_present": sorted(present),
            "staff_today_count": len({n for (_, n) in today_days}),
            "staff_live": live_rows,
            "staff_days": staff_days,
            "system": live.get("system"),
            "feed": feed,
        })

    @app.get("/api/excel")
    @requires_auth
    def excel():
        """Тот же Excel, что присылает бот (сотрудники + лист «Клиенты»), за выбранный период."""
        start, end, _ = parse_range()
        now = dt.datetime.now()
        folder = os.path.join(BASE_DIR, "reports")
        os.makedirs(folder, exist_ok=True)
        name = (f"Отчёт_{start:%Y-%m-%d}.xlsx" if start == end
                else f"Отчёт_{start:%Y-%m-%d}_{end:%Y-%m-%d}.xlsx")
        path = os.path.join(folder, name)
        title = (f"Отчёт за {start:%d.%m.%Y}" if start == end
                 else f"Отчёт за {start:%d.%m.%Y} – {end:%d.%m.%Y}") + f", филиал {config['branch']}"
        ok = make_report(path, read_events(config["events_file"]), start, end, title,
                         last_seen=_live().get("last_seen") or {}, now=now,
                         day_start=config["day_start"],
                         visits=read_visitors(config["visitors_file"]))
        if not ok:
            return jsonify({"error": "За этот период данных нет"}), 404
        return send_file(path, as_attachment=True, download_name=name)

    @app.get("/api/settings")
    @requires_auth
    def get_settings():
        fn = config.get("employees")
        employees = list(fn()) if fn else []
        return jsonify({"settings": settings.load(), "employees": employees,
                        "can_edit": bool(password)})

    @app.post("/api/settings")
    @requires_auth
    def post_settings():
        # без пароля сайта настройки не меняем: иначе смены поменяет любой в сети
        if not password:
            return jsonify({"error": "Сначала задайте пароль сайта (REPORTS_PASSWORD в secrets.env) - "
                                     "без пароля менять настройки нельзя"}), 403
        data = request.get_json(silent=True) or {}
        bad = [k for k in ("default_shift", "default_end")
               if k in data and not settings.norm_time(data[k])]
        shifts = {}
        for n, v in (data.get("shifts") or {}).items():
            v = v if isinstance(v, dict) else {"start": v}
            own = {k: v.get(k) for k in ("start", "end") if v.get(k)}
            if any(not settings.norm_time(t) for t in own.values()):
                bad.append(n)
            elif own:
                shifts[n] = own
        if bad:
            return jsonify({"error": "Неверное время: " + ", ".join(map(str, bad))}), 400
        data["shifts"] = shifts
        return jsonify({"settings": settings.save(data)})

    # --- Видео с регистратора: проверить, кого камера «узнала» (только с паролем сайта) ---
    def _nvr():
        return (os.environ.get("NVR_IP", "192.168.1.99"), os.environ.get("NVR_PORT", "554"),
                os.environ.get("NVR_LOGIN", "admin"), os.environ.get("NVR_PASSWORD", ""))

    def _channel():
        try:
            ch = int(request.args.get("ch", ""))
        except ValueError:
            return None
        return ch if 1 <= ch <= 32 else None

    @app.get("/api/record")
    @requires_admin
    def record():
        """Запись канала как .dav: /api/record?ch=6&start=2026-10-25T13:22&minutes=3 (до 15 мин)."""
        if not password:
            return jsonify({"error": "Видео доступно только при заданном пароле сайта"}), 403
        import requests
        from requests.auth import HTTPDigestAuth
        ch = _channel()
        try:
            start = dt.datetime.fromisoformat(request.args.get("start", ""))
            minutes = float(request.args.get("minutes", "2"))
        except ValueError:
            start, minutes = None, 0
        if ch is None or start is None or not 0 < minutes <= 15:
            return jsonify({"error": "Пример: /api/record?ch=6&start=2026-10-25T13:22&minutes=3"}), 400
        end = start + dt.timedelta(minutes=minutes)
        ip, _, login, pwd = _nvr()
        url = (f"http://{ip}/cgi-bin/loadfile.cgi?action=startLoad&channel={ch}"
               f"&startTime={start:%Y-%m-%d%%20%H:%M:%S}&endTime={end:%Y-%m-%d%%20%H:%M:%S}&subtype=0")
        try:
            r = requests.get(url, auth=HTTPDigestAuth(login, pwd), stream=True, timeout=60)
        except Exception as e:
            return jsonify({"error": f"Регистратор недоступен: {e}"}), 502
        if not r.ok:
            r.close()
            return jsonify({"error": f"Регистратор ответил {r.status_code} (записи нет?)"}), 404

        def gen():
            with r:
                for chunk in r.iter_content(1 << 16):
                    yield chunk
        name = f"cam{ch}_{start:%Y%m%d_%H%M}.dav"
        return Response(gen(), mimetype="application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.get("/api/snap")
    @requires_admin
    def snap():
        """Кадр с камеры сейчас (JPEG): /api/snap?ch=5"""
        if not password:
            return jsonify({"error": "Видео доступно только при заданном пароле сайта"}), 403
        import cv2
        from urllib.parse import quote
        ch = _channel()
        if ch is None:
            return jsonify({"error": "Пример: /api/snap?ch=5"}), 400
        ip, port, login, pwd = _nvr()
        cap = cv2.VideoCapture(f"rtsp://{quote(login, safe='')}:{quote(pwd, safe='')}@{ip}:{port}"
                               f"/cam/realmonitor?channel={ch}&subtype=0", cv2.CAP_FFMPEG)
        frame = None
        for _ in range(15):                 # первые кадры бывают серыми
            ok, f = cap.read()
            if ok:
                frame = f
        cap.release()
        if frame is None:
            return jsonify({"error": f"Камера {ch}: нет картинки"}), 404
        return Response(cv2.imencode(".jpg", frame)[1].tobytes(), mimetype="image/jpeg")

    @app.get("/api/log")
    @requires_admin
    def log_tail():
        """Последние строки logs\\run.log: /api/log?lines=200 (для проверки сбоев удалённо)."""
        if not password:
            return jsonify({"error": "Лог доступен только при заданном пароле сайта"}), 403
        try:
            n = max(10, min(int(request.args.get("lines", "200")), 5000))
        except ValueError:
            n = 200
        path = os.path.join(BASE_DIR, "logs", "run.log")
        if not os.path.exists(path):
            return jsonify({"error": "logs\\run.log нет"}), 404
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - n * 300))
            lines = f.read().decode("utf-8", "replace").splitlines()[-n:]
        return Response("\n".join(lines), mimetype="text/plain; charset=utf-8")

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "branch": config["branch"]})

    import control_api
    import panel
    control_api.register(app, config, requires_admin, BASE_DIR)
    panel.register(app, config)
    return app


def _lan_url(port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return f"http://{ip}:{port}"
    except Exception:
        return None


def start_reports_server(config, host="127.0.0.1", port=8080):
    """Запускает сайт в фоне (из программы учёта)."""
    app = create_app(config)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    try:
        from flask import cli as flask_cli
        flask_cli.show_server_banner = lambda *a, **k: None
    except Exception:
        pass

    def run():
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)

    threading.Thread(target=run, daemon=True, name="reports-web").start()
    local = f"http://127.0.0.1:{port}"
    lan = _lan_url(port)
    if lan and lan != local:
        return f"{local}  (в сети: {lan})"
    return local


def _load_env(path="secrets.env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    os.chdir(BASE_DIR)
    _load_env()
    branch = os.environ.get("BRANCH", "Достлик")
    visitors_file = os.environ.get("VISITORS_FILE", "visitors.csv")
    host = os.environ.get("REPORTS_HOST", "0.0.0.0")
    port = int(os.environ.get("REPORTS_PORT", "8080"))
    app = create_app({
        "branch": branch,
        "day_start": int(os.environ.get("DAY_START_HOUR", "0")),
        "events_file": os.environ.get("LOG_FILE", "events.csv"),
        "visitors_file": visitors_file,
        "password": os.environ.get("REPORTS_PASSWORD", ""),
        "live_state": None,
    })
    print(f"Сайт отчётов: http://127.0.0.1:{port}  (филиал {branch})")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
