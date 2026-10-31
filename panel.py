# -*- coding: utf-8 -*-
"""Общая панель управления всеми филиалами: https://<сайт главного ПК>/panel

Включается в secrets.env главного ПК:
    PANEL_PASSWORD=сложный_пароль              (вход: логин admin)
    PANEL_BRANCHES=local;https://dostlik.5plyus.uz|пароль_сайта_Достлика

«local» - этот же ПК. Панель ходит на сайты филиалов под их логином report, поэтому
пароли филиалов хранятся только здесь, в secrets.env главного ПК, а не в браузере.
"""

import os
from functools import wraps

import requests
from flask import Response, jsonify, render_template, request

HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-encoding",
               "content-length", "www-authenticate", "server", "date"}


_names = {}   # url -> название филиала (берётся с его /api/health)


def _branches(config):
    """PANEL_BRANCHES=local;https://dostlik.5plyus.uz|пароль   (название можно не писать:
    «Имя=адрес|пароль» - или панель спросит его у сайта филиала)."""
    out = []
    for item in os.environ.get("PANEL_BRANCHES", "").split(";"):
        item = item.strip()
        if not item:
            continue
        name, _, target = item.rpartition("=") if "=" in item.split("|")[0] else ("", "", item)
        url, _, pwd = target.partition("|")
        url = url.strip()
        if url == "local":
            url = f"http://127.0.0.1:{os.environ.get('REPORTS_PORT', '8080')}"
            pwd = config.get("password") or ""
            name = name or config.get("branch", "")
        out.append({"name": name.strip(), "url": url.rstrip("/"), "password": pwd.strip()})
    return out


def _name(b):
    if b["name"]:
        return b["name"]
    if b["url"] not in _names:
        try:
            _names[b["url"]] = requests.get(b["url"] + "/api/health", timeout=4).json()["branch"]
        except Exception:
            return b["url"].split("//")[-1].split(".")[0].capitalize()   # dostlik.5plyus.uz -> Dostlik
    return _names[b["url"]]


def register(app, config):
    password = os.environ.get("PANEL_PASSWORD", "")

    def requires_admin(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not password:
                return Response("Панель выключена: задайте PANEL_PASSWORD в secrets.env", 404)
            auth = request.authorization
            if not auth or auth.username != "admin" or auth.password != password:
                return Response("Нужен вход администратора", 401,
                                {"WWW-Authenticate": 'Basic realm="Panel 5plus", charset="UTF-8"'})
            return f(*args, **kwargs)
        return decorated

    @app.get("/panel")
    @requires_admin
    def panel_page():
        return render_template("panel.html")

    @app.get("/panel/branches")
    @requires_admin
    def panel_branches():
        return jsonify([{"id": i, "name": _name(b)} for i, b in enumerate(_branches(config))])

    @app.route("/panel/b/<int:bid>/<path:rest>", methods=["GET", "POST", "DELETE"])
    @requires_admin
    def panel_proxy(bid, rest):
        branches = _branches(config)
        if not 0 <= bid < len(branches) or not rest.startswith("api/"):
            return jsonify({"error": "Нет такого филиала"}), 404
        b = branches[bid]
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() in ("content-type", "accept")}
        try:
            r = requests.request(request.method, f"{b['url']}/{rest}", params=request.args,
                                 data=request.get_data(), headers=headers,
                                 auth=("report", b["password"]), stream=True,
                                 timeout=(10, 300))
        except requests.RequestException as e:
            return jsonify({"error": f"Филиал «{_name(b)}» не отвечает: {e.__class__.__name__}"}), 502
        if r.status_code in (401, 530, 502, 503, 504) and "json" not in r.headers.get("content-type", ""):
            r.close()
            msg = ("неверный пароль филиала в PANEL_BRANCHES" if r.status_code == 401
                   else "сайт филиала не запущен (ПК выключен или программа остановлена)")
            return jsonify({"error": f"Филиал «{_name(b)}»: {msg}"}), 502

        def body():
            with r:
                for chunk in r.raw.stream(1 << 15, decode_content=True):
                    yield chunk
        headers = [(k, v) for k, v in r.headers.items() if k.lower() not in HOP_HEADERS]
        return Response(body(), status=r.status_code, headers=headers)
