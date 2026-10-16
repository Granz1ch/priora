"""Ядро v2: текстовые команды (личка и групповые чаты), титулы, разделы, подкрутка, белый список чатов."""
from __future__ import annotations

import asyncio
import logging
import re
import signal
import time
import traceback
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import aiohttp

from . import VERSION
from .api import API, APIError
from .config import Config, RANK_NAMES, SECTION_TITLES
from .db import DB
from .ui import esc, mention, money

log = logging.getLogger("core")

Handler = Callable[["Ctx"], Awaitable[None]]

FORCE_RE = re.compile(r"\(\s*(вин|win|в|луз|lose|loose|луза|л)\s*\)\s*$", re.IGNORECASE)
WIN_WORDS = {"вин", "win", "в"}


class UserError(Exception):
    """Ошибка, текст которой показывается пользователю."""


class Router:
    def __init__(self) -> None:
        self.commands: dict[str, Handler] = {}
        self.meta: dict[Handler, dict] = {}
        self.payments: list[Handler] = []
        self.pre_checkout: list[Handler] = []
        self.texts: list = []          # обработчики произвольного текста: async fn(ctx) -> bool
        self.joins: list = []          # бот/кто-то добавлен в чат: async fn(ctx, members)

    def cmd(self, *names: str, section: str | None = None, rank: int = 0, private_only: bool = False):
        """Регистрирует текстовую команду. Имена без «/» — работают и как «/имя», и как слово в чате."""
        def deco(fn: Handler) -> Handler:
            for n in names:
                self.commands[n.lower()] = fn
            self.meta[fn] = {"section": section, "rank": rank, "private_only": private_only}
            return fn
        return deco

    # совместимость со старым именем
    command = cmd

    def on_payment(self, fn: Handler) -> Handler:
        self.payments.append(fn)
        return fn

    def on_pre_checkout(self, fn: Handler) -> Handler:
        self.pre_checkout.append(fn)
        return fn

    def on_text(self, fn):
        self.texts.append(fn)
        return fn

    def on_join(self, fn):
        self.joins.append(fn)
        return fn


router = Router()


def norm_word(w: str) -> str:
    """Нормализация слова команды: нижний регистр, ё→е, без завершающих знаков препинания."""
    return w.lower().replace("ё", "е").strip(".,!?:;")


@dataclass
class Ctx:
    bot: "Bot"
    update: dict
    uid: int
    chat_id: int
    chat_type: str = "private"
    user: object = None
    message: dict | None = None
    text: str = ""                     # исходный текст (без пометки подкрутки)
    cmd: str = ""                      # распознанное слово команды
    args: list[str] = field(default_factory=list)   # аргументы после команды
    msg_id: int | None = None          # id входящего сообщения
    reply_user: dict | None = None     # from из сообщения, на которое ответили
    reply_msg: dict | None = None
    rank: int = 0
    force: str | None = None           # 'win' | 'lose' | None (только для админов)
    state_data: dict = field(default_factory=dict)

    # ── удобства ──
    @property
    def api(self) -> API:
        return self.bot.api

    @property
    def db(self) -> DB:
        return self.bot.db

    @property
    def is_group(self) -> bool:
        return self.chat_type in ("group", "supergroup")

    @property
    def is_admin(self) -> bool:
        return self.rank >= 3

    @property
    def who(self) -> str:
        """Упоминание автора с тегом клана: [TAG] Имя."""
        return self.bot.display(self.user)

    def arg(self, i: int, default: str = "") -> str:
        return self.args[i] if i < len(self.args) else default

    def rest(self, i: int) -> str:
        return " ".join(self.args[i:])

    def setting(self, key: str, default=None):
        return self.db.setting(key, default)

    def refresh_user(self):
        self.user = self.db.user(self.uid)
        return self.user

    # ── отправка ──
    async def reply(self, text: str, **kw) -> dict | None:
        """Ответ на сообщение пользователя (в группах — с цитатой)."""
        if self.msg_id and self.is_group:
            kw.setdefault("reply_to_message_id", self.msg_id)
            kw.setdefault("allow_sending_without_reply", True)
        return await self.api.send(self.chat_id, text, None, **kw)

    async def send(self, text: str, **kw) -> dict | None:
        return await self.api.send(self.chat_id, text, None, **kw)

    async def edit(self, msg_id: int | None, text: str) -> dict | None:
        if not msg_id:
            return await self.send(text)
        res = await self.api.edit(self.chat_id, msg_id, text)
        return res

    async def dm(self, uid: int, text: str) -> dict | None:
        """Личное сообщение игроку (молча игнорирует ошибки — он мог не писать боту)."""
        return await self.api.send(uid, text)

    async def delete_incoming(self) -> None:
        if self.msg_id:
            await self.api.delete(self.chat_id, self.msg_id)

    # ── цели (кого касается команда) ──
    def target(self, token: str | None = None, allow_self: bool = False):
        """Пользователь по ответу на сообщение, @нику или ID. Бросает UserError, если не найден."""
        row = None
        if self.reply_user and not self.reply_user.get("is_bot"):
            row, _ = self.db.ensure_user(self.reply_user)
        elif token:
            t = token.strip()
            if t.startswith("@") or t.lstrip("-").isdigit():
                row = self.db.find_user(t)
            if row is None:
                raise UserError(f"Игрок {esc(t)} не найден — он должен хотя бы раз написать боту")
        if row is None:
            raise UserError("Укажи игрока: ответь на его сообщение или напиши @ник / ID")
        if row["id"] == self.uid and not allow_self:
            raise UserError("Нельзя применить к самому себе")
        return row

    def has_target_token(self, token: str | None) -> bool:
        return bool(self.reply_user and not self.reply_user.get("is_bot")) or bool(
            token and (token.startswith("@") or token.lstrip("-").isdigit()))


class Bot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.api = API(cfg.token, cfg.api_base)
        self.db = DB(cfg.db_path)
        self.router = router
        self.locks: dict[int, asyncio.Lock] = {}
        self.bg: set[asyncio.Task] = set()
        self.running = True
        self.started = time.time()
        self.me: dict = {}
        self.crash_games: dict = {}          # uid -> CrashGame
        self.banner_file_id: str | None = None
        self.updates_handled = 0
        self.left_chats: set[int] = set()       # чаты, где уже отправили «заблокирован» (не спамить)
        self.active_chats: set[int] = set()     # кэш чатов, где бот работает
        self.last_hint: dict[int, float] = {}

    # ───────────────────────── служебное ─────────────────────────
    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.bg.add(task)
        task.add_done_callback(self.bg.discard)
        return task

    def rank(self, uid: int) -> int:
        if uid in self.cfg.admin_ids:
            return 5
        row = self.db.user(uid)
        if not row:
            return 0
        return max(int(row["rank"] or 0), 3 if row["is_admin"] else 0)

    def is_admin(self, uid: int) -> bool:
        return self.rank(uid) >= 3

    def admin_ids(self) -> set[int]:
        ids = set(self.cfg.admin_ids)
        ids.update(r["id"] for r in self.db.all("SELECT id FROM users WHERE rank>=3 OR is_admin=1"))
        return ids

    async def notify_admins(self, text: str) -> None:
        for aid in self.admin_ids():
            await self.api.send(aid, text)

    def lock_for(self, uid: int) -> asyncio.Lock:
        lock = self.locks.get(uid)
        if lock is None:
            lock = self.locks[uid] = asyncio.Lock()
        return lock

    def display(self, row, link: bool = True, tag: bool = True) -> str:
        """[TAG] Имя (со ссылкой на профиль)."""
        if row is None:
            return "?"
        tag = self.db.tag_of(int(row["id"])) if (tag and row["clan_id"]) else ""
        name = esc(row["first_name"])
        s = mention(int(row["id"]), row["first_name"]) if link else name
        return f"[{esc(tag)}] {s}" if tag else s

    def section_enabled(self, section: str | None) -> bool:
        if not section:
            return True
        return bool(int(self.db.setting(f"section:{section}", 1)))

    async def try_quick(self, ctx: "Ctx") -> bool:
        for fn in self.router.texts:
            if await fn(ctx):
                return True
        return False

    # ───────────────────────── запуск ─────────────────────────
    async def run(self) -> None:
        logging.basicConfig(level=self.cfg.log_level,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        await self.api.start()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.stop)
            except NotImplementedError:
                pass
        try:
            self.me = await self.api.call("getMe")
        except APIError as e:
            log.error("getMe failed: %s", e)
            raise SystemExit(1)
        log.info("Запущен @%s (id %s), API %s, v%s", self.me.get("username"), self.me.get("id"),
                 self.cfg.api_base, VERSION)
        await self._setup_bot_profile()
        await self._recover()
        self.active_chats = self.db.active_chat_ids()
        self.spawn(self._housekeeping())
        if self.cfg.health_port:
            self.spawn(self._health_server())
        await self.notify_admins(
            f"🟢 <b>СИМ-Рулетка v{VERSION} запущена</b>\n"
            f"🤖 @{esc(self.me.get('username', '?'))} · 👥 {self.db.val('SELECT COUNT(*) FROM users')} · "
            f"💰 {money(self.db.val('SELECT COALESCE(SUM(balance),0) FROM users'))}\n"
            f"<i>«админка» — список команд управления</i>"
        )
        try:
            await self._poll()
        finally:
            for t in list(self.bg):
                t.cancel()
            await self.api.close()
            log.info("Остановлен")

    def stop(self) -> None:
        log.info("Получен сигнал остановки")
        self.running = False

    async def _setup_bot_profile(self) -> None:
        try:
            await self.api.call("deleteWebhook", _retries=0)
        except APIError:
            pass
        commands = [
            {"command": "start", "description": "🏠 Старт и краткая справка"},
            {"command": "help", "description": "📖 Все команды"},
            {"command": "balance", "description": "💰 Баланс"},
            {"command": "profile", "description": "👤 Профиль"},
            {"command": "spin", "description": "📱 Крутить номер: /spin тесто 3"},
            {"command": "numbers", "description": "📂 Мои номера"},
            {"command": "crash", "description": "🚀 Краш: /crash 500 [авто]"},
            {"command": "mines", "description": "💣 Мины: /mines 500 3"},
            {"command": "slots", "description": "🎰 Слоты: /slots 100"},
            {"command": "dice", "description": "🎲 Кубик: /dice 100 чёт"},
            {"command": "up", "description": "🔼 Апгрейд: /up 100 2"},
            {"command": "duel", "description": "⚔️ Дуэль: /duel 1000 [@ник]"},
            {"command": "cashout", "description": "💸 Забрать (краш/мины)"},
            {"command": "clan", "description": "🏰 Клан"},
            {"command": "top", "description": "🏆 Топ"},
            {"command": "bonus", "description": "🎁 Ежедневный бонус"},
            {"command": "promo", "description": "🎟 Промокод: /promo КОД"},
            {"command": "deposit", "description": "⭐ Пополнить (в личке): /deposit 5"},
            {"command": "rules", "description": "📜 Правила"},
        ]
        try:
            await self.api.call("setMyCommands", commands=commands, _retries=1)
        except APIError as e:
            log.warning("setMyCommands: %s", e)

    async def _recover(self) -> None:
        """После рестарта: вернуть ставки незавершённых краш/апгрейд/слот/кубик-игр."""
        for g in self.db.active_games():
            if g["kind"] in ("crash", "upgrader", "slots", "dice"):
                try:
                    self.db.change_balance(g["user_id"], g["bet"], f"refund:{g['kind']}:{g['id']}",
                                           ref=f"refund:{g['id']}")
                except Exception:
                    pass
                self.db.finish_game(g["id"], "refunded", g["bet"], "перезапуск бота")
                u = self.db.user(g["user_id"])
                await self.api.send(g["chat_id"], f"♻️ {self.display(u)}, бот перезапускался — ставка "
                                                  f"{money(g['bet'])} возвращена")

    async def _housekeeping(self) -> None:
        from .handlers_duel import expire_duels  # локальный импорт, чтобы избежать цикла
        from .handlers_quiz import expire_quizzes
        from .handlers_market import expire_trades
        last_ping = 0.0
        if self.cfg.keepalive_url:
            log.info("Keep-alive: самопинг %s/health каждые 5 мин", self.cfg.keepalive_url)
        while self.running:
            try:
                await expire_duels(self)
                await expire_quizzes(self)
                await expire_trades(self)
                self.db.ex("DELETE FROM states WHERE updated<?", (int(time.time()) - 86400,))
                if self.cfg.keepalive_url and time.time() - last_ping >= 300:
                    last_ping = time.time()
                    await self._keepalive()
            except Exception:
                log.exception("housekeeping")
            await asyncio.sleep(30)

    async def _keepalive(self) -> None:
        """Render Free усыпляет сервис без входящего трафика 15 мин — пингуем свой публичный URL."""
        try:
            async with self.api.session.get(self.cfg.keepalive_url + "/health",
                                            timeout=aiohttp.ClientTimeout(total=20)) as r:
                await r.read()
        except Exception as e:  # noqa: BLE001 — пинг не должен ронять housekeeping
            log.debug("keepalive: %s", e)

    async def _health_server(self) -> None:
        """HTTP на порту хостинга: /health для проверок + секретная панель управления."""
        from aiohttp import web

        from .panel import setup_panel

        async def health(_req):
            return web.json_response({
                "ok": True, "bot": self.me.get("username"), "version": VERSION,
                "uptime": int(time.time() - self.started), "updates": self.updates_handled,
                "users": self.db.val("SELECT COUNT(*) FROM users"),
            })

        app = web.Application()
        app.router.add_get("/health", health)
        app.router.add_get("/", health)          # как раньше: панель на свой секретный путь не выдаёт
        setup_panel(self, app)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.cfg.health_port)
        await site.start()
        # путь панели в лог не пишем: он и есть первый ключ секретки
        log.info("Health endpoint на порту %s%s", self.cfg.health_port,
                 ", панель включена (путь — PANEL_PATH)" if getattr(self.cfg, "panel_enabled", False) else "")

    # ───────────────────────── polling ─────────────────────────
    async def _poll(self) -> None:
        offset = None
        allowed = ["message", "pre_checkout_query", "my_chat_member"]
        while self.running:
            try:
                updates = await self.api.call("getUpdates", offset=offset, timeout=self.cfg.poll_timeout,
                                              allowed_updates=allowed, _timeout=self.cfg.poll_timeout + 20,
                                              _retries=0)
            except APIError as e:
                if e.code == 409:
                    log.error("Конфликт: уже запущен другой экземпляр бота с этим токеном. Жду 10 с.")
                    await asyncio.sleep(10)
                else:
                    log.warning("getUpdates: %s", e)
                    await asyncio.sleep(3)
                continue
            except Exception as e:  # noqa: BLE001
                log.warning("getUpdates unexpected: %s", e)
                await asyncio.sleep(3)
                continue
            if not updates:
                continue
            for upd in updates:
                try:
                    offset = max(offset or 0, int(upd.get("update_id", 0)) + 1)
                except (TypeError, ValueError):
                    pass
                self.spawn(self._handle(upd))

    # ───────────────────────── диспетчер ─────────────────────────
    def _extract(self, upd: dict) -> Ctx | None:
        if "message" in upd:
            m = upd["message"] or {}
            who = m.get("from") or {}
            chat = m.get("chat") or {}
            if not who.get("id") or who.get("is_bot"):
                return None
            ctype = chat.get("type") or "private"
            if ctype == "channel":
                return None
            text = m.get("text") or m.get("caption") or ""
            rm = m.get("reply_to_message") or None
            ctx = Ctx(bot=self, update=upd, uid=int(who["id"]), chat_id=int(chat.get("id") or who["id"]),
                      chat_type=ctype, message=m, text=text, msg_id=m.get("message_id"),
                      reply_user=(rm or {}).get("from") if rm else None, reply_msg=rm)
            return ctx
        if "pre_checkout_query" in upd:
            q = upd["pre_checkout_query"] or {}
            who = q.get("from") or {}
            if not who.get("id"):
                return None
            return Ctx(bot=self, update=upd, uid=int(who["id"]), chat_id=int(who["id"]))
        if "my_chat_member" in upd:
            q = upd["my_chat_member"] or {}
            who = q.get("from") or {}
            chat = q.get("chat") or {}
            if not who.get("id") or not chat.get("id"):
                return None
            return Ctx(bot=self, update=upd, uid=int(who["id"]), chat_id=int(chat["id"]),
                       chat_type=chat.get("type") or "group")
        return None

    async def _handle(self, upd: dict) -> None:
        ctx = self._extract(upd)
        if ctx is None:
            return
        self.updates_handled += 1
        who = (upd.get("message") or upd.get("pre_checkout_query") or upd.get("my_chat_member") or {}).get("from") or {}
        if "pre_checkout_query" in upd:
            ctx.user, _ = self.db.ensure_user(who)
            ctx.rank = self.rank(ctx.uid)
            for fn in self.router.pre_checkout:
                try:
                    await fn(ctx)
                except Exception:
                    log.exception("pre_checkout handler")
            return
        if "my_chat_member" in upd:
            try:
                await self._on_my_chat_member(ctx, upd["my_chat_member"])
            except Exception:
                log.exception("my_chat_member")
            return
        async with self.lock_for(ctx.uid):
            try:
                ctx.user, created = self.db.ensure_user(who)
                ctx.rank = self.rank(ctx.uid)
                if created:
                    ctx.state_data["__new__"] = True
                await self._route(ctx)
            except UserError as e:
                await ctx.reply(f"⚠️ {e}")
            except APIError as e:
                log.warning("API error in handler: %s", e)
            except Exception:
                log.error("Handler crashed:\n%s", traceback.format_exc())
                try:
                    await ctx.reply("⚠️ Что-то пошло не так. Попробуй ещё раз.")
                except Exception:
                    pass

    # ───────────────────────── чаты: открыты для всех, есть чёрный список ─────────────────────────
    def chat_ok(self, ctx: Ctx) -> bool:
        if not ctx.is_group:
            return True
        if ctx.chat_id in self.active_chats:
            return True
        if self.db.chat_banned(ctx.chat_id):
            return False
        # бот уже в чате (добавили до обновления или сервисное сообщение потерялось) — регистрируем
        title = ((ctx.message or {}).get("chat") or {}).get("title") or ""
        self.db.save_chat(ctx.chat_id, title, None, True)
        self.active_chats.add(ctx.chat_id)
        return True

    async def _on_my_chat_member(self, ctx: Ctx, q: dict) -> None:
        new = (q.get("new_chat_member") or {}).get("status")
        chat = q.get("chat") or {}
        if ctx.chat_type not in ("group", "supergroup"):
            return
        if new in ("member", "administrator", "restricted"):
            await self.on_added(ctx.chat_id, chat.get("title") or "", int(ctx.uid))
        elif new in ("left", "kicked"):
            self.db.save_chat(ctx.chat_id, chat.get("title") or "", None, False)
            self.active_chats.discard(ctx.chat_id)

    async def on_added(self, chat_id: int, title: str, by: int) -> None:
        """Бота добавили в чат. Добавлять может любой пользователь; исключение — чёрный список владельца."""
        if self.db.chat_banned(chat_id):
            self.left_chats.discard(chat_id)   # снова добавили — снова предупреждаем и выходим
            await self.reject_chat(chat_id)
            return
        if chat_id in self.active_chats or self.db.chat_allowed(chat_id):
            return  # уже работаем здесь (пришло и сервисное сообщение, и my_chat_member)
        self.db.save_chat(chat_id, title, by, True)
        self.active_chats.add(chat_id)
        self.left_chats.discard(chat_id)
        await self.api.send(chat_id, "🎰 <b>СИМ-Рулетка в чате!</b>\nНапиши «помощь» — все команды, «правила» — правила. "
                                     "Играть: <code>краш 500</code>, <code>мины 500 3</code>, "
                                     "<code>крутить тесто</code>.")

    async def reject_chat(self, chat_id: int) -> None:
        """Чат из чёрного списка: один раз сообщаем и выходим."""
        self.active_chats.discard(chat_id)
        if chat_id in self.left_chats:
            return
        self.left_chats.add(chat_id)
        await self.api.send(chat_id, "⛔ Этот чат заблокирован владельцем бота.")
        try:
            await self.api.call("leaveChat", chat_id=chat_id, _retries=0)
        except APIError as e:
            log.info("leaveChat %s: %s", chat_id, e)

    # ───────────────────────── маршрутизация ─────────────────────────
    def parse_command(self, ctx: Ctx) -> Handler | None:
        """Определяет команду по тексту. Заполняет ctx.cmd/ctx.args. None, если это не команда."""
        text = ctx.text.strip()
        if not text:
            return None
        m = FORCE_RE.search(text)
        if m:
            if ctx.rank >= 3:
                ctx.force = "win" if m.group(1).lower() in WIN_WORDS else "lose"
            text = text[:m.start()].rstrip()
        ctx.text = text
        tokens = text.split()
        first = tokens[0]
        if first.startswith("/"):
            name, _, at = first[1:].partition("@")
            if at and self.me.get("username") and at.lower() != str(self.me["username"]).lower():
                return None
            key = norm_word(name)
            if not key:
                return None
            # двухсловные команды: /clan создать …
            fn = self.router.commands.get(key)
            if fn is None:
                return None
            ctx.cmd, ctx.args = key, tokens[1:]
            return fn
        w1 = norm_word(first)
        if len(tokens) > 1:
            w2 = w1 + " " + norm_word(tokens[1])
            fn = self.router.commands.get(w2)
            if fn is not None:
                ctx.cmd, ctx.args = w2, tokens[2:]
                return fn
        fn = self.router.commands.get(w1)
        if fn is not None:
            ctx.cmd, ctx.args = w1, tokens[1:]
            return fn
        return None

    async def _route(self, ctx: Ctx) -> None:
        user = ctx.user
        m = ctx.message or {}
        # бота/людей добавили в чат (сервисное сообщение)
        if m.get("new_chat_members"):
            members = m["new_chat_members"]
            if any(int(x.get("id", 0)) == int(self.me.get("id", -1)) for x in members):
                await self.on_added(ctx.chat_id, (m.get("chat") or {}).get("title") or "", ctx.uid)
                return
            if self.chat_ok(ctx):
                for fn in self.router.joins:
                    await fn(ctx, members)
            return
        if not m.get("text") and not m.get("caption") and not m.get("successful_payment"):
            return
        # чат из чёрного списка: реагируем только на «чат разрешить» от администрации
        if not self.chat_ok(ctx):
            fn = self.parse_command(ctx)
            if fn is not None and ctx.cmd in ("чат", "chat", "chatallow") and ctx.rank >= 4:
                await fn(ctx)
                return
            await self.reject_chat(ctx.chat_id)
            return
        # мут — молча игнорируем
        if int(user["muted_until"] or 0) > time.time() and ctx.rank < 1:
            return
        # платёж
        if m.get("successful_payment"):
            for fn in self.router.payments:
                await fn(ctx)
            return
        fn = self.parse_command(ctx)
        # бан
        if user["banned"] and ctx.rank < 3:
            if fn is not None or not ctx.is_group:
                reason = f" Причина: <i>{esc(user['ban_reason'])}</i>" if user["ban_reason"] else ""
                await ctx.reply(f"🚫 Ты заблокирован в боте.{reason}")
            return
        # техработы
        if int(self.db.setting("maintenance", 0)) and ctx.rank < 3:
            if fn is not None or not ctx.is_group:
                await ctx.reply("🛠 Технические работы — бот скоро вернётся.")
            return
        if fn is not None:
            meta = self.router.meta.get(fn, {})
            if meta.get("rank", 0) > ctx.rank:
                # обычным игрокам в группе не отвечаем (слова вроде «бан» часто пишут просто так)
                if not ctx.is_group or ctx.rank >= 1:
                    await ctx.reply("⛔ Недостаточно прав")
                return
            sec = meta.get("section")
            if sec and not self.section_enabled(sec):
                await ctx.reply(f"⛔ Раздел «{SECTION_TITLES.get(sec, sec)}» временно отключён")
                return
            if meta.get("private_only") and ctx.is_group:
                await ctx.reply(f"🔒 Эта команда работает только в личке с ботом: @{esc(self.me.get('username', ''))}")
                return
            await fn(ctx)
            return
        # произвольный текст: ответы на викторину, клетки мин и т.п.
        for tfn in self.router.texts:
            if await tfn(ctx):
                return
        if not ctx.is_group:
            now = time.time()
            if now - self.last_hint.get(ctx.uid, 0) > 20:
                self.last_hint[ctx.uid] = now
                await ctx.reply("🤔 Не понял. «помощь» — список команд, «игры» — как играть")


def rank_name(rank: int) -> str:
    return RANK_NAMES.get(int(rank), "Игрок")
