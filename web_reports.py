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

    def check_auth(user, pwd):
        return user == "report" and pwd == password

    def requires_auth(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not password:
                return f(*args, **kwargs)
            auth = request.authorization
            if not auth or not check_auth(auth.username, auth.password):
                return Response("Нужна авторизация", 401,
                                {"WWW-Authenticate": 'Basic realm="Отчёты"'})
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

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

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
