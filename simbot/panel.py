"""Секретная веб-панель управления СИМ-Рулеткой.

Отдаётся на том же порту, что и ``/health`` (Render/bot-hosting дают его сами), поэтому
никакого второго сервиса не нужно. Весь интерфейс живёт по «секретному» пути
(``PANEL_PATH``, по умолчанию ``/priora/panel``) и за паролем (``ADMIN_PASSWORD``).

Что умеет:
  • статистика всех игроков, динамика по дням, разрез по играм, GGR/house-hold;
  • карточка игрока: баланс, титул, инвентарь, леджер, игры, дуэли, обмены, варны,
    платежи, клан, состояние FSM, чаты, рефералы — и любые действия над ним;
  • экономика и игра целиком: настройки, разделы, техработы, операторы и шансы,
    промокоды, кланы, чаты, рынок, отмена/возврат ставок, рефанд звёзд;
  • «консоль» — произвольная админ-команда уходит прямо в роутер бота (ранг 5),
    т.е. доступны вообще все текстовые команды администрации.

Безопасность: пароль сравнивается константно, сессия — подписанная HMAC-куки
(HttpOnly, SameSite=Lax, Secure при https), POST требует CSRF-заголовок и проверка
Origin, попытки входа ограничены по частоте, панель запрещена для индексирования.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import logging
import mimetypes
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from aiohttp import web

from . import VERSION, panel_data as P
from .config import OWNER_IDS, RANK_NAMES, panel_owner_id
from .core import Ctx, UserError
from .panel_data import PanelError
from .ui import esc, parse_duration

log = logging.getLogger("panel")

WEB_DIR = Path(__file__).resolve().parent / "web" / "panel"
STATIC = {"app.css": "text/css; charset=utf-8", "app.js": "application/javascript; charset=utf-8",
          "favicon.svg": "image/svg+xml"}
COOKIE = "priora_panel"
CSRF_HEADER = "X-Panel-CSRF"
FAIL_LIMIT = 8            # неудачных входов с одного IP в окне
FAIL_WINDOW = 60          # секунд
HEADERS = {
    "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors "
                               "'none'; base-uri 'none'; form-action 'self'",
}


# ───────────────────────── мелочи ─────────────────────────
def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


def _bool(v: Any, default: bool = True) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "on", "yes", "да", "вкл")


class RecordingAPI:
    """Пишет ответы бота в буфер, а всё, что адресовано реальным чатам, отдаёт в настоящий API."""

    def __init__(self, real, panel_chat: int) -> None:
        self.real = real
        self.panel_chat = int(panel_chat)
        self.out: list[str] = []
        self._id = 900_000

    def _mine(self, chat_id: Any) -> bool:
        try:
            return int(chat_id) == self.panel_chat
        except (TypeError, ValueError):
            return False

    def _res(self, text: str) -> dict:
        self._id += 1
        self.out.append(text)
        return {"message_id": self._id, "chat": {"id": self.panel_chat}, "text": text}

    async def send(self, chat_id, text, markup=None, **kw):
        if self._mine(chat_id):
            return self._res(text)
        return await self.real.send(chat_id, text, markup, **kw)

    async def send_raw(self, chat_id, text, markup=None, **kw):
        if self._mine(chat_id):
            return self._res(text)
        return await self.real.send_raw(chat_id, text, markup, **kw)

    async def edit(self, chat_id, message_id, text, markup=None):
        if self._mine(chat_id):
            return self._res(text)
        return await self.real.edit(chat_id, message_id, text, markup)

    async def delete(self, chat_id, message_id):
        if self._mine(chat_id):
            return True
        return await self.real.delete(chat_id, message_id)

    async def dm(self, uid, text):  # не у Ctx, но на всякий случай
        return await self.send(uid, text)

    async def photo(self, chat_id, photo, caption="", markup=None, **kw):
        if self._mine(chat_id):
            return self._res(caption or "[фото]")
        return await self.real.photo(chat_id, photo, caption, markup, **kw)

    async def dice(self, chat_id, emoji="🎲", **kw):
        if self._mine(chat_id):
            return self._res(f"[🎲 {emoji}]")
        return await self.real.dice(chat_id, emoji, **kw)

    async def invoice(self, chat_id, *a, **kw):
        if self._mine(chat_id):
            return self._res("[инвойс]")
        return await self.real.invoice(chat_id, *a, **kw)

    async def refund_stars(self, user_id, charge_id):
        return await self.real.refund_stars(user_id, charge_id)

    async def answer_cb(self, *a, **kw):
        return None

    async def answer_pre_checkout(self, *a, **kw):
        return None

    async def chat_action(self, *a, **kw):
        return None

    async def call(self, method, **params):
        if method in ("getUpdates",):
            return []
        return await self.real.call(method, **params)

    @property
    def session(self):
        return self.real.session


class BotProxy:
    """Бот «как есть», но api подменён на пишущий в панель."""

    def __init__(self, bot, api: RecordingAPI) -> None:
        self._bot = bot
        self.api = api

    def __getattr__(self, name):
        return getattr(self._bot, name)


class Panel:
    def __init__(self, bot) -> None:
        self.bot = bot
        cfg = bot.cfg
        self.base = "/" + str(cfg.panel_path).strip("/")
        self.password = str(cfg.panel_password or "")
        self.hours = max(1, int(cfg.panel_hours))
        seed = f"{self.password}|{cfg.token}|{cfg.db_path}".encode()
        self.secret = hashlib.sha256(b"priora-panel-v1" + seed).digest()
        self.fails: dict[str, list[float]] = {}
        self.started = time.time()

    # ───────────────────────── регистрация ─────────────────────────
    def setup(self, app: web.Application) -> None:
        app.router.add_route("GET", self.base, self.handle)
        app.router.add_route("GET", self.base + "/", self.handle)
        app.router.add_route("*", self.base + "/{tail:.*}", self.handle)

    # ───────────────────────── сессия ─────────────────────────
    def _sign(self, payload: str) -> str:
        return hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()[:40]

    def make_cookie(self) -> str:
        payload = f"{int(time.time()) + self.hours * 3600}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    @staticmethod
    def _payload_of(cookie: str) -> str:
        parts = (cookie or "").split(".")
        return f"{parts[0]}.{parts[1]}" if len(parts) == 3 else ""

    def check_cookie(self, cookie: str) -> bool:
        payload = self._payload_of(cookie)
        if not payload:
            return False
        exp = _int(payload.split(".")[0])
        if exp < time.time():
            return False
        sig = cookie.rsplit(".", 1)[-1]
        return hmac.compare_digest(sig, self._sign(payload))

    def csrf_of(self, cookie: str) -> str:
        return self._sign("csrf:" + self._payload_of(cookie))[:32]

    # ───────────────────────── защита ─────────────────────────
    def client_ip(self, request: web.Request) -> str:
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.remote or "?"

    def throttled(self, ip: str) -> float:
        now = time.time()
        arr = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
        self.fails[ip] = arr
        if len(arr) < FAIL_LIMIT:
            return 0.0
        wait = FAIL_WINDOW - (now - arr[0])
        return max(1.0, wait)

    def note_fail(self, ip: str) -> None:
        self.fails.setdefault(ip, []).append(time.time())

    def clear_fails(self, ip: str) -> None:
        self.fails.pop(ip, None)

    def authed(self, request: web.Request) -> bool:
        return self.check_cookie(request.cookies.get(COOKIE, ""))

    def origin_ok(self, request: web.Request) -> bool:
        origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
        if not origin:
            return True                      # не-браузерные клиенты (curl) — без CSRF-риска
        host = origin.split("//")[-1].split("/")[0]
        return host == request.headers.get("Host", "")

    def secure(self, request: web.Request) -> bool:
        return request.headers.get("X-Forwarded-Proto", request.scheme).lower() == "https"

    # ───────────────────────── ответы ─────────────────────────
    def resp(self, request: web.Request, data, status: int = 200) -> web.Response:
        if isinstance(data, web.Response):
            resp = data
        elif isinstance(data, str):
            resp = web.Response(text=data, status=status, content_type="text/html", charset="utf-8")
        else:
            resp = web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False,
                                                                                      default=str))
        for k, v in HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    def err(self, request: web.Request, msg: str, status: int = 400) -> web.Response:
        return self.resp(request, {"ok": False, "error": msg}, status)

    # ───────────────────────── диспетчер ─────────────────────────
    async def handle(self, request: web.Request) -> web.Response:
        try:
            return await self._handle(request)
        except web.HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — панель не должна ронять бота
            log.exception("panel: %s", request.path)
            return self.err(request, f"Ошибка панели: {type(e).__name__}: {esc(str(e))[:300]}", 500)

    async def _handle(self, request: web.Request) -> web.Response:
        tail = request.match_info.get("tail", "").strip("/")
        if request.method not in ("GET", "POST"):
            raise web.HTTPMethodNotAllowed(method=request.method, allowed_methods=("GET", "POST"))
        # статика и вход
        if tail in ("", "index.html"):
            return self.serve_page(request)
        if tail in STATIC and request.method == "GET":
            return self.serve_static(request, tail)
        if tail == "login" and request.method == "POST":
            return await self.login(request)
        if not tail.startswith("api/"):
            raise web.HTTPNotFound()
        name = tail[4:].strip("/")
        if request.method == "GET":
            return await self.api_get(request, name)
        if not self.origin_ok(request):
            return self.err(request, "Чужой origin", 403)
        if name == "login":
            return await self.login(request)
        if not self.authed(request):
            return self.err(request, "Нужен пароль", 401)
        if request.headers.get(CSRF_HEADER, "") != self.csrf_of(request.cookies.get(COOKIE, "")):
            return self.err(request, "Сессия устарела (CSRF) — обнови страницу", 403)
        if name == "logout":
            return self.logout(request)
        if name == "action":
            return await self.action(request)
        if name == "console":
            return await self.console(request)
        return self.err(request, f"Неизвестное действие <code>{esc(name)}</code>", 404)

    # ───────────────────────── страницы/файлы ─────────────────────────
    def serve_page(self, request: web.Request) -> web.Response:
        path = WEB_DIR / "index.html"
        if not path.exists():
            return self.resp(request, "<h1>Panel assets missing</h1>"
                                      f"<p>Ожидались файлы в <code>{esc(str(path.parent))}</code></p>")
        body = path.read_text(encoding="utf-8").replace("__BASE__", self.base)
        return self.resp(request, body)

    def serve_static(self, request: web.Request, name: str) -> web.Response:
        path = (WEB_DIR / name).resolve()
        if not str(path).startswith(str(WEB_DIR.resolve())) or not path.is_file():
            raise web.HTTPNotFound()
        ctype = STATIC.get(name) or mimetypes.guess_type(name)[0] or "application/octet-stream"
        mime, _, charset = ctype.partition("; ")
        resp = web.Response(body=path.read_bytes(), content_type=mime,
                            **({"charset": charset} if charset else {}))
        resp.headers["ETag"] = f'W/"{int(path.stat().st_mtime)}-{path.stat().st_size}"'
        if request.headers.get("If-None-Match") == resp.headers["ETag"]:
            return web.Response(status=304, headers={"Cache-Control": HEADERS["Cache-Control"],
                                                      "X-Robots-Tag": HEADERS["X-Robots-Tag"]})
        return self.resp(request, resp)

    # ───────────────────────── вход ─────────────────────────
    async def login(self, request: web.Request) -> web.Response:
        ip = self.client_ip(request)
        wait = self.throttled(ip)
        if wait:
            return self.err(request, f"Слишком много попыток. Подожди {int(wait)} с.", 429)
        body = await self._body(request)
        got = str(body.get("password", body.get("pass", "")))
        if not self.password:
            return self.err(request, "Пароль не задан (ADMIN_PASSWORD пустой) — панель выключена", 503)
        if not hmac.compare_digest(got.encode("utf-8", "surrogateescape"),
                                   self.password.encode("utf-8", "surrogateescape")):
            self.note_fail(ip)
            await asyncio.sleep(0.4 + 0.15 * len(self.fails.get(ip, [])))
            log.warning("panel: неверный пароль с %s", ip)
            return self.err(request, "Неверный пароль", 401)
        self.clear_fails(ip)
        cookie = self.make_cookie()
        resp = self.resp(request, {"ok": True, "csrf": self.csrf_of(cookie), "path": self.base,
                                   "version": VERSION})
        resp.set_cookie(COOKIE, cookie, max_age=self.hours * 3600, httponly=True, samesite="Lax",
                        secure=self.secure(request), path="/")
        log.info("panel: вход разрешён (%s)", ip)
        return resp

    def logout(self, request: web.Request) -> web.Response:
        resp = self.resp(request, {"ok": True, "logged_out": True})
        resp.del_cookie(COOKIE, path="/")
        return resp

    async def _body(self, request: web.Request) -> dict:
        raw = await request.read()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            ctype = request.content_type or ""
            if "form" in ctype:
                return {k: v for k, v in await request.post()}
            raise PanelError("Ожидался JSON") from None
        if not isinstance(data, dict):
            raise PanelError("Тело запроса — объект JSON")
        return data

    # ───────────────────────── чтение ─────────────────────────
    def _actor(self) -> int:
        return panel_owner_id(self.bot.cfg.admin_ids or set(OWNER_IDS))

    async def _read(self, request: web.Request, fn: Callable[[Any], Any]) -> web.Response:
        out = await asyncio.to_thread(fn, self.bot.db)
        return self.resp(request, out)

    async def api_get(self, request: web.Request, name: str) -> web.Response:
        q = request.query
        if name == "session":
            if not self.authed(request):
                return self.resp(request, {"logged_in": False, "path": self.base, "version": VERSION})
            return self.resp(request, await asyncio.to_thread(self._session_info))
        if not self.authed(request):
            return self.err(request, "Нужен пароль", 401)
        db = self.bot.db
        if name == "overview":
            return await self._read(request, lambda d: {**P.overview(d), "bot": self._bot_info()})
        if name == "users":
            return await self._read(request, lambda d: P.list_users(
                d, q=q.get("q", ""), flt=q.get("filter", "all"), sort=q.get("sort", "balance"),
                desc=_bool(q.get("desc", "1")), page=_int(q.get("page"), 1), limit=_int(q.get("limit"), 50)))
        if name == "search":
            return await self._read(request, lambda d: {"results": P.autocomplete(d, q.get("q", ""))})
        if name.startswith("user/"):
            uid = q.get("uid") or name.split("/", 1)[1]
            if not uid.lstrip("-").isdigit():
                u = P.find_user(db, uid)
                if not u:
                    return self.err(request, "Игрок не найден", 404)
                uid = str(u["id"])
            return await self._read(request, lambda d: P.user_detail(d, int(uid)))
        if name.startswith("number/"):
            return await self._read(request, lambda d: {"number": one_number(d, name.split("/", 1)[1])})
        if name == "browse":
            return await self._read(request, lambda d: P.browse(
                d, q.get("table", "users"), q=q.get("q", ""), user=q.get("user", ""),
                page=_int(q.get("page"), 1), limit=_int(q.get("limit"), 50), order=q.get("order", ""),
                desc=_bool(q.get("desc", "1"))))
        if name == "feed":
            return await self._read(request, lambda d: {"games": P.feed(d, q.get("kind", ""), q.get("status", "finished"),
                                                                _int(q.get("limit"), 60), q.get("user", "")),
                                                "live": P.live_games(d)})
        if name == "tables":
            return await self._read(request, lambda d: {"tables": {t: int(d.val(f"SELECT COUNT(*) FROM {t}"))
                                                                    for t in P.TABLES}})
        if name in ("clans", "chats", "promos", "market", "trades", "duels", "payments", "operators",
                    "countries", "settings"):
            fn = {"clans": P.clans, "chats": P.chats, "promos": P.promos, "market": P.market,
                  "trades": P.trades, "duels": P.duels, "payments": P.payments, "operators": P.operators,
                  "countries": P.countries, "settings": P.all_settings}[name]
            extra = {"limit": _int(q.get("limit"), 100), "status": q.get("status", "")}
            return await self._read(request, lambda d, f=fn, e=extra: _call_with(f, d, e))
        if name == "audit":
            return await self._read(request, lambda d: {"rows": P.audit(d, _int(q.get("limit"), 100), q.get("action", ""))})
        if name == "export":
            return self.export_csv(request)
        if name == "actions":
            return self.resp(request, {"ops": sorted(SYNC_OPS) + sorted(ASYNC_OPS),
                                       "tables": sorted(P.TABLES), "ranks": RANK_NAMES,
                                       "settings": sorted(P.DEFAULT_SETTINGS),
                                       "sections": P.SECTION_TITLES})
        return self.err(request, f"Неизвестный раздел <code>{esc(name)}</code>", 404)

    def _kpi(self) -> dict:
        """Короткий срез статистики: панель обновляет шапку без полного /overview."""
        s = self.bot.db.stats()
        bet, pay = int(s["total_bet"]), int(s["total_payout"])
        return {"stats": s, "ggr": bet - pay,
                "hold": round((1 - pay / bet) * 100, 2) if bet else 0.0,
                "day": {"new": int(s["new_today"]), "games": int(s["games_today"])},
                "bot": self._bot_info()}

    def _session_info(self) -> dict:
        db = self.bot.db
        return {"logged_in": True, "path": self.base, "version": VERSION, "actor": self._actor(),
                "actor_name": (db.user(self._actor()) or {"first_name": "Панель"})["first_name"],
                "bot": self._bot_info()}

    def _bot_info(self) -> dict:
        b = self.bot
        info = {"username": (b.me or {}).get("username"), "id": (b.me or {}).get("id"),
                "version": VERSION, "uptime": int(time.time() - b.started) if b.started else 0,
                "updates": b.updates_handled, "api_base": b.cfg.api_base, "db_path": b.cfg.db_path,
                "db_size": 0, "python": os.sys.version.split()[0], "pid": os.getpid(),
                "panel_hours": b.cfg.panel_hours, "port": b.cfg.health_port,
                "external": b.cfg.keepalive_url, "active_crash": len(b.crash_games or {}),
                "bg_tasks": len(b.bg), "owner": self._actor()}
        try:
            info["db_size"] = os.path.getsize(b.cfg.db_path)
        except OSError:
            pass
        return info

    # ───────────────────────── выгрузка ─────────────────────────
    def export_csv(self, request: web.Request) -> web.Response:
        q = request.query
        rows = P.list_users(self.bot.db, q=q.get("q", ""), flt=q.get("filter", "all"),
                            sort=q.get("sort", "balance"), desc=_bool(q.get("desc", "1")),
                            page=1, limit=PAGE_LIMIT)["users"]
        head = ["id", "first_name", "username", "balance", "games", "wins", "total_bet", "total_won",
                "net", "spins", "biggest_win", "stars_paid", "warns", "rank", "clan_role", "banned",
                "created", "last_seen"]
        lines = [",".join(head)]
        for r in rows:
            lines.append(",".join(_csv_cell(r.get(k, "")) for k in head))
        resp = web.Response(text="\ufeff" + "\n".join(lines), content_type="text/csv", charset="utf-8")
        resp.headers["Content-Disposition"] = 'attachment; filename="priora-users.csv"'
        return self.resp(request, resp)

    # ───────────────────────── запись ─────────────────────────
    async def action(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        op = str(body.get("op", "")).strip().lower()
        if not op:
            return self.err(request, "Нужно поле <code>op</code>")
        # «неприкосновенные» id берутся из конфига, а не от клиента
        body = dict(body)
        body["_protected"] = sorted(int(x) for x in (self.bot.cfg.admin_ids or OWNER_IDS))
        actor = self._actor()
        note = str(body.get("note", ""))[:200]
        db = self.bot.db
        try:
            if op in ASYNC_OPS:
                out = await ASYNC_OPS[op](self, db, actor, body)
            elif op in SYNC_OPS:
                fn = SYNC_OPS[op]
                out = await asyncio.to_thread(lambda: fn(db, actor, body))
            else:
                return self.err(request, f"Не знаю действие <code>{esc(op)}</code>", 404)
        except PanelError as e:
            return self.err(request, str(e))
        except UserError as e:
            return self.err(request, str(e))
        except P.InsufficientFunds as e:
            return self.err(request, f"Недостаточно средств: {esc(e)}")
        except (ValueError, TypeError) as e:
            return self.err(request, f"Не понял аргументы: {esc(str(e))[:200]}")
        if note and isinstance(out, dict):
            out["note"] = note
        log.info("panel: %s → %s", op, json.dumps(out, ensure_ascii=False, default=str)[:200])
        if isinstance(out, dict) and out.get("notify"):
            out = {**out, "notify": None}
        return self.resp(request, {"ok": True, "op": op, "result": out})

    # ───────────────────────── консоль бота ─────────────────────────
    async def console(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        text = str(body.get("text", "")).strip()
        if not text:
            return self.err(request, "Пустая команда")
        uid = _int(body.get("uid"), self._actor())
        chat_id = _int(body.get("chat_id"), uid)
        chat_type = "group" if chat_id < 0 else "private"
        db = self.bot.db
        rec = RecordingAPI(self.bot.api, uid)
        ctx = Ctx(bot=BotProxy(self.bot, rec), update={}, uid=uid, chat_id=chat_id, chat_type=chat_type,
                  user=_operator_row(db, uid), message={"message_id": 1, "date": int(time.time()),
                                                         "from": {"id": uid, "is_bot": False,
                                                                   "first_name": "панель"},
                                                         "chat": {"id": chat_id, "type": chat_type},
                                                         "text": text},
                  text=text)
        ctx.rank = 5
        try:
            fn = self.bot.parse_command(ctx)
            if fn is None:
                return self.resp(request, {"ok": False, "error": "Бот не считает это командой",
                                           "replies": rec.out,
                                           "kpi": await asyncio.to_thread(self._kpi)})
            meta = self.bot.router.meta.get(fn, {})
            if int(meta.get("rank", 0)) > 5:
                return self.err(request, "Недостаточно прав")
            await fn(ctx)
            return self.resp(request, {"ok": True, "cmd": ctx.cmd, "replies": rec.out,
                                       "kpi": await asyncio.to_thread(self._kpi)})
        except (UserError, PanelError) as e:
            return self.resp(request, {"ok": False, "error": str(e), "replies": rec.out})
        except P.InsufficientFunds as e:
            return self.resp(request, {"ok": False, "error": f"Недостаточно средств: {e}", "replies": rec.out})
        except Exception as e:  # noqa: BLE001
            log.exception("console command")
            return self.resp(request, {"ok": False, "error": f"{type(e).__name__}: {esc(str(e))[:200]}",
                                       "replies": rec.out})


# ───────────────────────── вспомогательные функции ─────────────────────────
PAGE_LIMIT = 5000


def _csv_cell(v: Any) -> str:
    s = str(v if v is not None else "")
    if any(c in s for c in ',"\n'):
        return '"' + s.replace('"', '""') + '"'
    return s


def _call_with(fn: Callable, db, extra: dict):
    sig = inspect.signature(fn)
    kw = {k: v for k, v in extra.items() if k in sig.parameters}
    return fn(db, **kw)


def one_number(db, token: str) -> dict | None:
    n = db.find_number(token)
    if not n:
        raise PanelError("Номер не найден")
    d = {k: n[k] for k in n.keys()}
    owner = db.user(int(n["user_id"])) if n["user_id"] else None
    d["owner"] = {k: owner[k] for k in owner.keys()} if owner is not None else None
    d["log"] = P.rows(db, "SELECT * FROM number_log WHERE number_id=? ORDER BY id DESC LIMIT 40", (n["id"],))
    d["listing"] = one_or_none(db, "SELECT * FROM market WHERE number_id=?", (n["id"],))
    return d


def one_or_none(db, sql: str, args: tuple) -> dict | None:
    r = db.one(sql, args)
    return {k: r[k] for k in r.keys()} if r is not None else None


def _operator_row(db, uid: int):
    row = db.user(uid)
    if row is not None:
        return row
    cols = {r["name"] for r in P.rows(db, "PRAGMA table_info(users)")}
    fake = {c: 0 for c in cols}
    fake.update({"id": uid, "first_name": "🖥 Панель", "username": "panel", "created": int(time.time()),
                 "last_seen": int(time.time()), "rank": 5})
    return fake


async def stop_crash(bot, db, game_id: str) -> None:
    """Гасит живую гонку краша, чтобы цикл не начислил выплату после возврата ставки."""
    g = db.game(game_id)
    if not g or g["status"] != "active" or g["kind"] != "crash":
        return
    obj = (bot.crash_games or {}).get(int(g["user_id"]))
    if obj is None or getattr(obj, "gid", None) != g["id"]:
        return
    lock = getattr(obj, "lock", None)
    if lock is not None:
        async with lock:
            obj.done = True
    else:
        obj.done = True
    bot.crash_games.pop(int(g["user_id"]), None)


# ═══════════════════════════════════════════════════════════
#  Действия
# ═══════════════════════════════════════════════════════════
def _uid(body: dict) -> int:
    v = body.get("uid", body.get("user", body.get("id")))
    s = str(v if v is not None else "").strip()
    if not s.lstrip("-").isdigit():
        raise PanelError("Нужен числовой ID игрока (видно в карточке или командой «id»)")
    return int(s)


def op_balance(db, actor, body):
    return P.balance_op(db, actor, _uid(body), str(body.get("mode", "give")), body.get("amount", 0))


def op_ban(db, actor, body):
    return P.ban(db, actor, _uid(body), _bool(body.get("on", True)), str(body.get("reason", ""))[:200])


def op_mute(db, actor, body):
    secs = body.get("seconds")
    if secs in (None, ""):
        secs = parse_duration(str(body.get("duration", ""))) or 0
    return P.mute(db, actor, _uid(body), max(0, _int(secs)))


def op_warn(db, actor, body):
    return P.warn(db, actor, _uid(body), str(body.get("reason", ""))[:200])


def op_warn_remove(db, actor, body):
    return P.warn_remove(db, actor, _uid(body), _bool(body.get("all", False)))


def op_rank(db, actor, body):
    protected = {int(x) for x in body.get("_protected") or OWNER_IDS}
    return P.set_rank(db, actor, _uid(body), _int(body.get("rank")), protected)


def op_reset_stats(db, actor, body):
    return P.reset_stats(db, actor, _uid(body))


def op_wipe(db, actor, body):
    return P.wipe_inventory(db, actor, _uid(body), str(body.get("tier", "")))


def op_give_number(db, actor, body):
    return P.give_number(db, actor, _uid(body), str(body.get("operator", "")), str(body.get("tier", "")))


def op_take_number(db, actor, body):
    return P.take_number(db, actor, str(body.get("number", "")))


def op_move_number(db, actor, body):
    return P.move_number(db, actor, str(body.get("number", "")), body.get("to"))


def op_revalue(db, actor, body):
    return P.revalue_number(db, actor, str(body.get("number", "")), body.get("value", 0))


def op_promo_create(db, actor, body):
    return P.promo_create(db, actor, str(body.get("code", "")), body.get("amount", 0),
                          _int(body.get("uses"), 1), _int(body.get("days"), 0))


def op_promo_delete(db, actor, body):
    return P.promo_delete(db, actor, str(body.get("code", "")))


def op_promo_bump(db, actor, body):
    return P.promo_bump(db, actor, str(body.get("code", "")), _int(body.get("uses"), 1))


def op_setting(db, actor, body):
    return P.set_setting(db, actor, str(body.get("key", "")), body.get("value"))


def op_section(db, actor, body):
    return P.set_section(db, actor, str(body.get("section", "")), _bool(body.get("on", True)))


def op_maintenance(db, actor, body):
    return P.set_maintenance(db, actor, _bool(body.get("on", False)))


def op_operator(db, actor, body):
    return P.set_operator(db, actor, str(body.get("code", "")), dict(body.get("patch") or {}))


def op_country(db, actor, body):
    return P.set_country(db, actor, str(body.get("code", "")), _bool(body.get("on", True)))


def op_game_refund(db, actor, body):
    return P.game_refund(db, actor, str(body.get("game", "")), str(body.get("mark", "refunded")))


def op_duel_cancel(db, actor, body):
    return P.duel_cancel(db, actor, str(body.get("id", "")))


def op_trade_cancel(db, actor, body):
    return P.trade_cancel(db, actor, str(body.get("id", "")))


def op_market_remove(db, actor, body):
    return P.market_remove(db, actor, str(body.get("number", "")))


def op_clear_state(db, actor, body):
    return P.clear_state(db, actor, _uid(body))


def op_clan_treasury(db, actor, body):
    return P.clan_treasury(db, actor, body.get("clan"), body.get("delta", 0))


def op_clan_delete(db, actor, body):
    return P.clan_delete(db, actor, body.get("clan"))


def op_note(db, actor, body):
    return P.set_balance_note(db, actor, _uid(body), str(body.get("text", "")))


def op_browse_delete(db, actor, body):
    """Удаление строки из таблицы — только из явно разрешённого списка (users не трогаем)."""
    table = str(body.get("table", ""))
    key = str(body.get("key", ""))
    allowed = {"promos": "code", "market": "number_id", "quizzes": "id", "states": "user_id",
               "trades": "id", "duels": "id", "warns": "id", "clan_invites": "user_id"}
    if table not in allowed:
        raise PanelError("Разрешено удалять строки только из: " + ", ".join(sorted(allowed)))
    col = allowed[table]
    if table not in P.TABLES and table != "clan_invites":
        raise PanelError("Нет такой таблицы")
    cur = db.ex(f"DELETE FROM {table} WHERE CAST({col} AS TEXT)=?", (key,))
    if cur.rowcount == 0:
        raise PanelError("Нечего удалять")
    P.log(db, actor, f"delete:{table}", {"key": key, "rows": cur.rowcount})
    return {"ok": True, "deleted": cur.rowcount}


SYNC_OPS: dict[str, Callable] = {
    "balance": op_balance, "ban": op_ban, "mute": op_mute, "warn": op_warn, "warn_remove": op_warn_remove,
    "rank": op_rank, "reset_stats": op_reset_stats, "wipe_inventory": op_wipe, "give_number": op_give_number,
    "take_number": op_take_number, "move_number": op_move_number, "revalue_number": op_revalue,
    "promo_create": op_promo_create, "promo_delete": op_promo_delete, "promo_bump": op_promo_bump,
    "setting": op_setting, "section": op_section, "maintenance": op_maintenance, "operator": op_operator,
    "country": op_country, "game_refund": op_game_refund, "duel_cancel": op_duel_cancel,
    "trade_cancel": op_trade_cancel, "market_remove": op_market_remove, "clear_state": op_clear_state,
    "clan_treasury": op_clan_treasury, "clan_delete": op_clan_delete, "note": op_note,
    "browse_delete": op_browse_delete,
}


# ─── действия, которым нужен живой Bot API ───
async def op_dm(panel: Panel, db, actor: int, body: dict) -> dict:
    uid = body.get("chat_id", _uid(body))
    text = str(body.get("text", "")).strip()
    if not text:
        raise PanelError("Пустое сообщение")
    if not body.get("html"):
        text = esc(text).replace("\n", "<br>")
    res = await panel.bot.api.send(int(uid), text)
    P.log(db, actor, "dm", {"to": int(uid), "len": len(text), "sent": res is not None})
    return {"ok": True, "sent": res is not None, "to": int(uid)}


async def op_broadcast(panel: Panel, db, actor: int, body: dict) -> dict:
    text = str(body.get("text", "")).strip()
    if not text:
        raise PanelError("Пустой текст рассылки")
    if not body.get("html"):
        text = esc(text).replace("\n", "<br>")
    target = str(body.get("target", "both"))
    ids: list[int] = []
    if target in ("users", "both"):
        ids = P.user_ids_by_filter(db, str(body.get("filter", "all")))
    chats: list[int] = []
    if target in ("chats", "both"):
        chats = [int(c["id"]) for c in db.chats() if c["allowed"] and not c["banned"]]
    total = len(ids) + len(chats)
    if not total:
        raise PanelError("Никому слать нечего")
    P.log(db, actor, "broadcast", {"len": len(text), "users": len(ids), "chats": len(chats),
                                    "filter": body.get("filter", "all")})
    if body.get("dry_run"):
        return {"ok": True, "dry_run": True, "targets": total, "sample": ids[:5]}

    async def job():
        ok = 0
        for cid in chats + ids:
            if await panel.bot.api.send(cid, f"📣 {text}"):
                ok += 1
            await asyncio.sleep(0.05)
        log.info("panel: рассылка %s/%s", ok, total)

    panel.bot.spawn(job())
    return {"ok": True, "started": True, "targets": total}


async def op_chat_ban(panel: Panel, db, actor: int, body: dict) -> dict:
    cid = _int(body.get("chat"))
    banned = _bool(body.get("banned", True))
    out = P.ban_chat(db, actor, cid, banned)
    if banned:
        panel.bot.active_chats.discard(cid)
        panel.bot.left_chats.discard(cid)
        try:
            await panel.bot.api.call("leaveChat", chat_id=cid, _retries=0)
        except Exception as e:  # noqa: BLE001
            out["leave"] = f"выходи сам: {e}"
    else:
        panel.bot.left_chats.discard(cid)
    return {**out, "left": bool(banned)}


async def op_payment_refund(panel: Panel, db, actor: int, body: dict) -> dict:
    charge = str(body.get("charge", "")).strip()
    p = db.payment(charge) if charge else None
    if not p:
        raise PanelError("Платёж не найден (нужен telegram_payment_charge_id из чека)")
    if p["refunded"]:
        raise PanelError("Этот платёж уже возвращён")
    try:
        await panel.bot.api.refund_stars(int(p["user_id"]), charge)
    except Exception as e:  # noqa: BLE001
        raise PanelError(f"ShapitoGram/Telegram отклонил возврат: {e}") from None
    db.mark_refunded(charge)
    bal = db.balance(int(p["user_id"]))
    take = min(bal, int(p["rubles"]))
    if take > 0:
        db.change_balance(int(p["user_id"]), -take, f"refund:{charge}")
    P.log(db, actor, "refund", {"charge": charge, "user": int(p["user_id"]), "stars": int(p["stars"])})
    return {"ok": True, "stars": int(p["stars"]), "taken": take, "user": int(p["user_id"])}


async def op_stop_crash(panel: Panel, db, actor: int, body: dict) -> dict:
    gid = str(body.get("game", ""))
    if not db.game(gid):
        raise PanelError("Игра не найдена")
    await stop_crash(panel.bot, db, gid)
    P.log(db, actor, "stop_crash", {"game": gid})
    return {"ok": True, "stopped": True}


async def op_game_refund_safe(panel: Panel, db, actor: int, body: dict) -> dict:
    """Возврат ставки с остановкой живой гонки краша (чтобы цикл не начислил сверху)."""
    gid = str(body.get("game", ""))
    await stop_crash(panel.bot, db, gid)
    out = await asyncio.to_thread(P.game_refund, db, actor, gid, str(body.get("mark", "refunded")))
    g = db.game(gid)
    if g and body.get("notify") and g["user_id"]:
        await panel.bot.api.send(int(g["user_id"]),
                                 f"♻️ Администратор вернул ставку {out.get('refunded', 0)} ₽ по игре "
                                 f"<code>{esc(gid)}</code>")
    return out


async def op_bot_command(panel: Panel, db, actor: int, body: dict) -> dict:
    """Тот же мостик, что и «консоль»: строка уходит в роутер бота с рангом 5."""
    text = str(body.get("line", "")).strip()
    if not text:
        raise PanelError("Пустая команда")
    return await run_console_op(panel, db, actor, text)


async def run_console_op(panel: Panel, db, actor: int, text: str) -> dict:
    rec = RecordingAPI(panel.bot.api, actor)
    ctx = Ctx(bot=BotProxy(panel.bot, rec), update={}, uid=actor, chat_id=actor, chat_type="private",
              user=_operator_row(db, actor), message={"message_id": 1, "text": text, "date": int(time.time()),
                                                       "chat": {"id": actor, "type": "private"}}, text=text)
    ctx.rank = 5
    fn = panel.bot.parse_command(ctx)
    if fn is None:
        raise PanelError("Команда не распознана")
    await fn(ctx)
    return {"ok": True, "replies": rec.out}


ASYNC_OPS: dict[str, Callable] = {
    "dm": op_dm, "broadcast": op_broadcast, "chat_ban": op_chat_ban, "payment_refund": op_payment_refund,
    "stop_crash": op_stop_crash, "game_refund_live": op_game_refund_safe, "bot_command": op_bot_command,
}


def setup_panel(bot, app: web.Application) -> Panel | None:
    if not bot.cfg.panel_enabled:
        log.info("panel: выключена (PANEL_ENABLED=0)")
        return None
    panel = Panel(bot)
    panel.setup(app)
    log.info("panel: включена, сессия %s ч (путь и пароль — в PANEL_PATH/ADMIN_PASSWORD)", panel.hours)
    return panel
