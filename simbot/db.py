"""SQLite-хранилище: пользователи, леджер, номера, операторы, игры, дуэли, платежи, промо, настройки."""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from .config import DEFAULT_SETTINGS
from .numbers import DEFAULT_ODDS, new_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
    banned INTEGER NOT NULL DEFAULT 0,
    ban_reason TEXT NOT NULL DEFAULT '',
    is_admin INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    last_daily INTEGER NOT NULL DEFAULT 0,
    daily_streak INTEGER NOT NULL DEFAULT 0,
    ref_by INTEGER,
    refs INTEGER NOT NULL DEFAULT 0,
    games INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    total_bet INTEGER NOT NULL DEFAULT 0,
    total_won INTEGER NOT NULL DEFAULT 0,
    biggest_win INTEGER NOT NULL DEFAULT 0,
    spins INTEGER NOT NULL DEFAULT 0,
    stars_paid INTEGER NOT NULL DEFAULT 0,
    last_bet INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger(
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    delta INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ref TEXT UNIQUE,
    created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_user ON ledger(user_id, id);
CREATE TABLE IF NOT EXISTS countries(
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    flag TEXT NOT NULL,
    dial TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS operators(
    code TEXT PRIMARY KEY,
    country TEXT NOT NULL,
    name TEXT NOT NULL,
    emoji TEXT NOT NULL,
    prefix TEXT NOT NULL,
    price INTEGER NOT NULL,
    odds TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS numbers(
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    country TEXT NOT NULL,
    operator TEXT NOT NULL,
    full TEXT UNIQUE NOT NULL,
    digits TEXT NOT NULL,
    tier TEXT NOT NULL,
    value INTEGER NOT NULL,
    origin TEXT NOT NULL DEFAULT 'spin',
    created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS numbers_user ON numbers(user_id, created);
CREATE TABLE IF NOT EXISTS number_log(
    id INTEGER PRIMARY KEY,
    number_id TEXT NOT NULL,
    full TEXT NOT NULL,
    from_user INTEGER,
    to_user INTEGER,
    action TEXT NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS games(
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    bet INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    payout INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT '',
    msg_id INTEGER,
    created INTEGER NOT NULL,
    finished INTEGER
);
CREATE INDEX IF NOT EXISTS games_user ON games(user_id, created);
CREATE INDEX IF NOT EXISTS games_active ON games(status, kind);
CREATE TABLE IF NOT EXISTS duels(
    id TEXT PRIMARY KEY,
    creator INTEGER NOT NULL,
    opponent INTEGER,
    target INTEGER,
    bet INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    winner INTEGER,
    rounds TEXT NOT NULL DEFAULT '[]',
    created INTEGER NOT NULL,
    finished INTEGER
);
CREATE INDEX IF NOT EXISTS duels_status ON duels(status, created);
CREATE TABLE IF NOT EXISTS invoices(
    payload TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    stars INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments(
    charge_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    stars INTEGER NOT NULL,
    rubles INTEGER NOT NULL,
    payload TEXT NOT NULL,
    refunded INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS promos(
    code TEXT PRIMARY KEY,
    amount INTEGER NOT NULL,
    uses_left INTEGER NOT NULL,
    total_uses INTEGER NOT NULL,
    expires INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_uses(
    code TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    created INTEGER NOT NULL,
    PRIMARY KEY(code, user_id)
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
    id INTEGER PRIMARY KEY,
    admin_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS states(
    user_id INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    updated INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS clans(
    id INTEGER PRIMARY KEY,
    tag TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    owner INTEGER NOT NULL,
    treasury INTEGER NOT NULL DEFAULT 0 CHECK(treasury >= 0),
    open INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS clan_invites(
    clan_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    by INTEGER NOT NULL,
    created INTEGER NOT NULL,
    PRIMARY KEY(clan_id, user_id)
);
CREATE TABLE IF NOT EXISTS clan_log(
    id INTEGER PRIMARY KEY,
    clan_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    delta INTEGER NOT NULL,
    action TEXT NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chats(
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    added_by INTEGER,
    allowed INTEGER NOT NULL DEFAULT 0,
    banned INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS quizzes(
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    creator INTEGER NOT NULL,
    level INTEGER NOT NULL,
    reward INTEGER NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    winner INTEGER,
    msg_id INTEGER,
    created INTEGER NOT NULL,
    finished INTEGER
);
CREATE INDEX IF NOT EXISTS quizzes_chat ON quizzes(chat_id, status);
CREATE TABLE IF NOT EXISTS market(
    number_id TEXT PRIMARY KEY,
    seller INTEGER NOT NULL,
    price INTEGER NOT NULL,
    buyer INTEGER,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trades(
    id TEXT PRIMARY KEY,
    from_user INTEGER NOT NULL,
    to_user INTEGER NOT NULL,
    give_id TEXT NOT NULL,
    take_id TEXT NOT NULL,
    extra INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    created INTEGER NOT NULL,
    finished INTEGER
);
CREATE TABLE IF NOT EXISTS warns(
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    by INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created INTEGER NOT NULL
);
"""

# Колонки, добавленные в новых версиях (для уже существующих баз — ALTER TABLE)
MIGRATIONS = [
    ("users", "rank", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "warns", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "muted_until", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "clan_id", "INTEGER"),
    ("users", "clan_role", "TEXT NOT NULL DEFAULT ''"),
    ("duels", "chat_id", "INTEGER"),
    ("chats", "banned", "INTEGER NOT NULL DEFAULT 0"),
]

SEED_COUNTRIES = [("ru", "Россия", "🇷🇺", "+7", 1, 0)]
SEED_OPERATORS = [
    # code, country, name, emoji, prefix, price, odds, sort
    ("pivkoff", "ru", "Пивкофф", "🍺", "902", 500,
     {"common": 76, "uncommon": 17, "rare": 5.2, "epic": 1.4, "legendary": 0.35, "mythic": 0.05}, 0),
    ("testo", "ru", "Тесто", "🍞", "501", 1500,
     {"common": 55, "uncommon": 27, "rare": 12, "epic": 4.5, "legendary": 1.2, "mythic": 0.3}, 1),
    ("sberishche", "ru", "Сберище", "🏦", "404", 5000,
     {"common": 30, "uncommon": 30, "rare": 22, "epic": 11.5, "legendary": 5, "mythic": 1.5}, 2),
]


class InsufficientFunds(Exception):
    pass


def now() -> int:
    return int(time.time())


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._depth = 0
        self.conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self._seed()

    # ───────────────────────── базовые операции ─────────────────────────
    @contextlib.contextmanager
    def tx(self):
        with self.lock:
            if self._depth > 0:
                self._depth += 1
                try:
                    yield self.conn
                finally:
                    self._depth -= 1
                return
            self.conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            finally:
                self._depth = 0

    def ex(self, sql: str, args: Iterable = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, tuple(args))

    def one(self, sql: str, args: Iterable = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, tuple(args)).fetchone()

    def all(self, sql: str, args: Iterable = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, tuple(args)).fetchall()

    def val(self, sql: str, args: Iterable = (), default=0):
        r = self.one(sql, args)
        if r is None or r[0] is None:
            return default
        return r[0]

    def _migrate(self) -> None:
        for table, column, decl in MIGRATIONS:
            cols = {r["name"] for r in self.all(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.ex(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def _seed(self) -> None:
        with self.tx():
            for c in SEED_COUNTRIES:
                self.ex("INSERT OR IGNORE INTO countries(code,name,flag,dial,enabled,sort) VALUES(?,?,?,?,?,?)", c)
            for code, country, name, emoji, prefix, price, odds, sort in SEED_OPERATORS:
                self.ex(
                    "INSERT OR IGNORE INTO operators(code,country,name,emoji,prefix,price,odds,enabled,sort,created)"
                    " VALUES(?,?,?,?,?,?,?,1,?,?)",
                    (code, country, name, emoji, prefix, price, dumps(odds), sort, now()),
                )
            for k, v in DEFAULT_SETTINGS.items():
                self.ex("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, str(v)))

    # ───────────────────────── настройки ─────────────────────────
    def setting(self, key: str, default=None):
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        if r is None:
            return DEFAULT_SETTINGS.get(key, default)
        v = r["value"]
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v

    def set_setting(self, key: str, value) -> None:
        self.ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))

    # ───────────────────────── пользователи ─────────────────────────
    def ensure_user(self, who: dict) -> tuple[sqlite3.Row, bool]:
        """Создаёт/обновляет пользователя. Возвращает (row, created)."""
        uid = int(who["id"])
        first = (who.get("first_name") or who.get("username") or str(uid))[:64]
        uname = (who.get("username") or "")[:64]
        t = now()
        with self.tx():
            row = self.one("SELECT * FROM users WHERE id=?", (uid,))
            if row is None:
                self.ex("INSERT INTO users(id,first_name,username,created,last_seen) VALUES(?,?,?,?,?)",
                        (uid, first, uname, t, t))
                return self.one("SELECT * FROM users WHERE id=?", (uid,)), True
            self.ex("UPDATE users SET first_name=?, username=?, last_seen=? WHERE id=?", (first, uname, t, uid))
            return self.one("SELECT * FROM users WHERE id=?", (uid,)), False

    def user(self, uid: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    def user_by_username(self, uname: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM users WHERE lower(username)=lower(?)", (uname.lstrip("@"),))

    def find_user(self, token: str) -> sqlite3.Row | None:
        token = token.strip()
        if token.lstrip("-").isdigit():
            return self.user(int(token))
        return self.user_by_username(token)

    def balance(self, uid: int) -> int:
        return int(self.val("SELECT balance FROM users WHERE id=?", (uid,), 0))

    def change_balance(self, uid: int, delta: int, reason: str, ref: str | None = None) -> int:
        """Атомарно меняет баланс. Возвращает новый баланс. Повтор по ref — no-op (идемпотентность)."""
        delta = int(delta)
        with self.tx():
            if ref and self.one("SELECT 1 FROM ledger WHERE ref=?", (ref,)):
                return self.balance(uid)
            cur = self.ex("UPDATE users SET balance=balance+? WHERE id=? AND balance+?>=0", (delta, uid, delta))
            if cur.rowcount != 1:
                if self.user(uid) is None:
                    raise InsufficientFunds("Пользователь не найден")
                raise InsufficientFunds("Недостаточно средств")
            bal = self.balance(uid)
            self.ex("INSERT INTO ledger(user_id,delta,balance_after,reason,ref,created) VALUES(?,?,?,?,?,?)",
                    (uid, delta, bal, reason, ref, now()))
            return bal

    def set_balance(self, uid: int, amount: int, reason: str) -> int:
        with self.tx():
            cur = self.balance(uid)
            return self.change_balance(uid, int(amount) - cur, reason)

    def record_game(self, uid: int, bet: int, payout: int) -> None:
        """Обновляет статистику игрока после игры (payout — сумма возврата, 0 если проигрыш)."""
        win = 1 if payout > bet else 0
        self.ex(
            "UPDATE users SET games=games+1, wins=wins+?, total_bet=total_bet+?, total_won=total_won+?,"
            " biggest_win=MAX(biggest_win, ?), last_bet=? WHERE id=?",
            (win, bet, payout, payout - bet if payout > bet else 0, bet, uid),
        )

    def set_ban(self, uid: int, banned: bool, reason: str = "") -> None:
        self.ex("UPDATE users SET banned=?, ban_reason=? WHERE id=?", (1 if banned else 0, reason[:200], uid))

    # ───────────────────────── титулы, варны, муты ─────────────────────────
    def set_rank(self, uid: int, rank: int) -> None:
        self.ex("UPDATE users SET rank=? WHERE id=?", (int(rank), uid))

    def ranked_users(self) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM users WHERE rank>0 ORDER BY rank DESC, first_name")

    def add_warn(self, uid: int, by: int, reason: str) -> int:
        with self.tx():
            self.ex("INSERT INTO warns(user_id,by,reason,created) VALUES(?,?,?,?)", (uid, by, reason[:200], now()))
            self.ex("UPDATE users SET warns=warns+1 WHERE id=?", (uid,))
            return int(self.val("SELECT warns FROM users WHERE id=?", (uid,)))

    def remove_warn(self, uid: int) -> int:
        with self.tx():
            self.ex("UPDATE users SET warns=MAX(0, warns-1) WHERE id=?", (uid,))
            return int(self.val("SELECT warns FROM users WHERE id=?", (uid,)))

    def clear_warns(self, uid: int) -> None:
        self.ex("UPDATE users SET warns=0 WHERE id=?", (uid,))

    def set_mute(self, uid: int, until: int) -> None:
        self.ex("UPDATE users SET muted_until=? WHERE id=?", (int(until), uid))

    # ───────────────────────── чаты ─────────────────────────
    def chat(self, chat_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM chats WHERE id=?", (chat_id,))

    def chat_allowed(self, chat_id: int) -> bool:
        """Бот сейчас активен в этом чате (добавлен и не вышел)."""
        return bool(self.val("SELECT allowed FROM chats WHERE id=?", (chat_id,), 0))

    def chat_banned(self, chat_id: int) -> bool:
        """Чат в чёрном списке владельца: бот выходит и не отвечает."""
        return bool(self.val("SELECT banned FROM chats WHERE id=?", (chat_id,), 0))

    def ban_chat(self, chat_id: int, banned: bool) -> None:
        self.ex("INSERT INTO chats(id,title,added_by,allowed,banned,created) VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET banned=excluded.banned,"
                " allowed=CASE WHEN excluded.banned=1 THEN 0 ELSE chats.allowed END",
                (chat_id, "", None, 0, 1 if banned else 0, now()))

    def active_chat_ids(self) -> set[int]:
        return {int(r["id"]) for r in self.all("SELECT id FROM chats WHERE allowed=1 AND banned=0")}

    def save_chat(self, chat_id: int, title: str, added_by: int | None, allowed: bool) -> None:
        self.ex("INSERT INTO chats(id,title,added_by,allowed,created) VALUES(?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET title=excluded.title, allowed=excluded.allowed,"
                " added_by=COALESCE(excluded.added_by, chats.added_by)",
                (chat_id, (title or "")[:80], added_by, 1 if allowed else 0, now()))

    def chats(self) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM chats ORDER BY banned DESC, allowed DESC, created DESC LIMIT 60")

    def user_ids(self, only_active: bool = False) -> list[int]:
        sql = "SELECT id FROM users WHERE banned=0"
        return [r["id"] for r in self.all(sql + " ORDER BY id")]

    # ───────────────────────── состояния (FSM) ─────────────────────────
    def get_state(self, uid: int) -> tuple[str | None, dict]:
        r = self.one("SELECT state, data FROM states WHERE user_id=?", (uid,))
        if not r:
            return None, {}
        try:
            data = json.loads(r["data"])
        except json.JSONDecodeError:
            data = {}
        return r["state"], data

    def set_state(self, uid: int, state: str | None, data: dict | None = None) -> None:
        if state is None:
            self.ex("DELETE FROM states WHERE user_id=?", (uid,))
            return
        self.ex("INSERT INTO states(user_id,state,data,updated) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET"
                " state=excluded.state, data=excluded.data, updated=excluded.updated",
                (uid, state, dumps(data or {}), now()))

    # ───────────────────────── страны и операторы ─────────────────────────
    def countries(self, only_enabled: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM countries" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY sort, name"
        return self.all(sql)

    def country(self, code: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM countries WHERE code=?", (code,))

    def operators(self, country: str | None = None, only_enabled: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM operators WHERE 1=1"
        args: list = []
        if country:
            sql += " AND country=?"
            args.append(country)
        if only_enabled:
            sql += " AND enabled=1"
        sql += " ORDER BY sort, price"
        return self.all(sql, args)

    def operator(self, code: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM operators WHERE code=?", (code,))

    def operator_odds(self, op: sqlite3.Row) -> dict:
        try:
            return json.loads(op["odds"])
        except (json.JSONDecodeError, TypeError):
            return dict(DEFAULT_ODDS)

    # ───────────────────────── номера ─────────────────────────
    def number_taken(self, full: str) -> bool:
        return self.one("SELECT 1 FROM numbers WHERE full=?", (full,)) is not None

    def add_number(self, uid: int, country: str, operator: str, full: str, digits: str, tier: str,
                   value: int, origin: str = "spin") -> sqlite3.Row:
        with self.tx():
            for _ in range(20):
                nid = new_id()
                if not self.one("SELECT 1 FROM numbers WHERE id=?", (nid,)):
                    break
            self.ex("INSERT INTO numbers(id,user_id,country,operator,full,digits,tier,value,origin,created)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)", (nid, uid, country, operator, full, digits, tier, value, origin, now()))
            self.ex("INSERT INTO number_log(number_id,full,from_user,to_user,action,created) VALUES(?,?,?,?,?,?)",
                    (nid, full, None, uid, origin, now()))
            return self.one("SELECT * FROM numbers WHERE id=?", (nid,))

    def number(self, nid: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM numbers WHERE id=?", (nid.upper().lstrip("#"),))

    def number_by_full(self, full: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM numbers WHERE full=?", (full,))

    def find_number(self, token: str) -> sqlite3.Row | None:
        """Номер по ID (#ABC123) или по цифрам (+79021234567 / 9021234567 / хвост от 7 цифр)."""
        t = (token or "").strip()
        if not t:
            return None
        n = self.number(t)
        if n:
            return n
        digits = "".join(ch for ch in t if ch.isdigit())
        if len(digits) >= 7:
            return self.one("SELECT * FROM numbers WHERE REPLACE(REPLACE(REPLACE(full,' ',''),'-',''),'+','') LIKE ?"
                            " ORDER BY created DESC LIMIT 1", ("%" + digits,))
        return None

    def user_numbers(self, uid: int, limit: int = 1000, offset: int = 0) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM numbers WHERE user_id=? ORDER BY value DESC, created DESC LIMIT ? OFFSET ?",
                        (uid, limit, offset))

    def numbers_count(self, uid: int) -> int:
        return int(self.val("SELECT COUNT(*) FROM numbers WHERE user_id=?", (uid,)))

    def collection_value(self, uid: int) -> int:
        return int(self.val("SELECT COALESCE(SUM(value),0) FROM numbers WHERE user_id=?", (uid,)))

    def transfer_number(self, nid: str, from_uid: int, to_uid: int, action: str = "gift") -> bool:
        with self.tx():
            cur = self.ex("UPDATE numbers SET user_id=? WHERE id=? AND user_id=?", (to_uid, nid, from_uid))
            if cur.rowcount != 1:
                return False
            self.ex("DELETE FROM market WHERE number_id=?", (nid,))
            n = self.number(nid)
            self.ex("INSERT INTO number_log(number_id,full,from_user,to_user,action,created) VALUES(?,?,?,?,?,?)",
                    (nid, n["full"], from_uid, to_uid, action, now()))
            return True

    def remove_number(self, nid: str, action: str, by: int | None = None) -> sqlite3.Row | None:
        with self.tx():
            n = self.number(nid)
            if not n:
                return None
            self.ex("DELETE FROM numbers WHERE id=?", (nid,))
            self.ex("DELETE FROM market WHERE number_id=?", (nid,))
            self.ex("INSERT INTO number_log(number_id,full,from_user,to_user,action,created) VALUES(?,?,?,?,?,?)",
                    (nid, n["full"], n["user_id"], by, action, now()))
            return n

    # ───────────────────────── игры ─────────────────────────
    def create_game(self, uid: int, chat_id: int, kind: str, bet: int, state: dict | None = None,
                    msg_id: int | None = None) -> str:
        gid = new_id(8)
        self.ex("INSERT INTO games(id,user_id,chat_id,kind,bet,state,status,msg_id,created) VALUES(?,?,?,?,?,?,?,?,?)",
                (gid, uid, chat_id, kind, bet, dumps(state or {}), "active", msg_id, now()))
        return gid

    def game(self, gid: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM games WHERE id=?", (gid,))

    def game_state(self, row: sqlite3.Row) -> dict:
        try:
            return json.loads(row["state"])
        except (json.JSONDecodeError, TypeError):
            return {}

    def update_game(self, gid: str, state: dict | None = None, msg_id: int | None = None) -> None:
        if state is not None:
            self.ex("UPDATE games SET state=? WHERE id=?", (dumps(state), gid))
        if msg_id is not None:
            self.ex("UPDATE games SET msg_id=? WHERE id=?", (msg_id, gid))

    def finish_game(self, gid: str, status: str, payout: int, result: str = "", state: dict | None = None) -> None:
        self.ex("UPDATE games SET status=?, payout=?, result=?, finished=?, state=COALESCE(?, state) WHERE id=?",
                (status, payout, result[:200], now(), dumps(state) if state is not None else None, gid))

    def active_game(self, uid: int, kind: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM games WHERE user_id=? AND kind=? AND status='active' ORDER BY created DESC",
                        (uid, kind))

    def active_games(self, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return self.all("SELECT * FROM games WHERE status='active' AND kind=?", (kind,))
        return self.all("SELECT * FROM games WHERE status='active'")

    def history(self, uid: int, limit: int = 10) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM games WHERE user_id=? AND status!='active' ORDER BY finished DESC LIMIT ?",
                        (uid, limit))

    # ───────────────────────── дуэли ─────────────────────────
    def create_duel(self, creator: int, bet: int, target: int | None = None, chat_id: int | None = None) -> str:
        did = new_id(6)
        status = "invited" if target else "open"
        self.ex("INSERT INTO duels(id,creator,target,bet,status,created,chat_id) VALUES(?,?,?,?,?,?,?)",
                (did, creator, target, bet, status, now(), chat_id))
        return did

    def duel(self, did: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM duels WHERE id=?", (did.upper(),))

    def open_duels(self, limit: int = 10, chat_id: int | None = None) -> list[sqlite3.Row]:
        if chat_id is not None and chat_id < 0:
            return self.all("SELECT d.*, u.first_name AS cname FROM duels d JOIN users u ON u.id=d.creator"
                            " WHERE d.status='open' AND d.chat_id=? ORDER BY d.created DESC LIMIT ?", (chat_id, limit))
        return self.all("SELECT d.*, u.first_name AS cname FROM duels d JOIN users u ON u.id=d.creator"
                        " WHERE d.status='open' AND (d.chat_id IS NULL OR d.chat_id>0) ORDER BY d.created DESC LIMIT ?",
                        (limit,))

    def pending_for(self, uid: int, chat_id: int | None = None) -> list[sqlite3.Row]:
        """Дуэли, которые пользователь может принять: адресованные ему вызовы + открытые в этом чате."""
        rows = self.all("SELECT * FROM duels WHERE status='invited' AND target=? ORDER BY created DESC", (uid,))
        if chat_id is not None and chat_id < 0:
            rows += self.all("SELECT * FROM duels WHERE status='open' AND chat_id=? AND creator!=? ORDER BY created DESC",
                             (chat_id, uid))
        else:
            rows += self.all("SELECT * FROM duels WHERE status='open' AND (chat_id IS NULL OR chat_id>0) AND creator!=?"
                             " ORDER BY created DESC", (uid,))
        return rows

    def user_pending_duels(self, uid: int) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM duels WHERE creator=? AND status IN ('open','invited') ORDER BY created DESC",
                        (uid,))

    def expired_duels(self, ttl: int) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM duels WHERE status IN ('open','invited') AND created<?", (now() - ttl,))

    def claim_duel(self, did: str, opponent: int, from_status: tuple[str, ...] = ("open", "invited")) -> bool:
        """Атомарно переводит дуэль в статус playing. True, если удалось."""
        marks = ",".join("?" * len(from_status))
        cur = self.ex(f"UPDATE duels SET status='playing', opponent=? WHERE id=? AND status IN ({marks})",
                      (opponent, did, *from_status))
        return cur.rowcount == 1

    def set_duel_status(self, did: str, status: str, from_status: tuple[str, ...] | None = None) -> bool:
        if from_status:
            marks = ",".join("?" * len(from_status))
            cur = self.ex(f"UPDATE duels SET status=? WHERE id=? AND status IN ({marks})", (status, did, *from_status))
        else:
            cur = self.ex("UPDATE duels SET status=? WHERE id=?", (status, did))
        return cur.rowcount == 1

    def finish_duel(self, did: str, status: str, winner: int | None, rounds: list) -> None:
        self.ex("UPDATE duels SET status=?, winner=?, rounds=?, finished=? WHERE id=?",
                (status, winner, dumps(rounds), now(), did))

    # ───────────────────────── платежи ─────────────────────────
    def create_invoice(self, payload: str, uid: int, stars: int) -> None:
        self.ex("INSERT INTO invoices(payload,user_id,stars,status,created) VALUES(?,?,?,?,?)",
                (payload, uid, stars, "sent", now()))

    def invoice(self, payload: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM invoices WHERE payload=?", (payload,))

    def payment(self, charge_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM payments WHERE charge_id=?", (charge_id,))

    def apply_payment(self, charge_id: str, uid: int, stars: int, rubles: int, payload: str) -> bool:
        """Начисляет рубли за оплату. False, если платёж уже учтён."""
        with self.tx():
            if self.payment(charge_id):
                return False
            self.ex("INSERT INTO payments(charge_id,user_id,stars,rubles,payload,created) VALUES(?,?,?,?,?,?)",
                    (charge_id, uid, stars, rubles, payload, now()))
            self.ex("UPDATE invoices SET status='paid' WHERE payload=?", (payload,))
            self.ex("UPDATE users SET stars_paid=stars_paid+? WHERE id=?", (stars, uid))
            self.change_balance(uid, rubles, f"deposit:{stars}⭐", ref=f"pay:{charge_id}")
            return True

    def mark_refunded(self, charge_id: str) -> None:
        self.ex("UPDATE payments SET refunded=1 WHERE charge_id=?", (charge_id,))

    # ───────────────────────── промокоды ─────────────────────────
    def promo(self, code: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM promos WHERE code=?", (code.upper(),))

    def create_promo(self, code: str, amount: int, uses: int, days: int, admin: int) -> None:
        exp = now() + days * 86400 if days > 0 else 0
        self.ex("INSERT INTO promos(code,amount,uses_left,total_uses,expires,created_by,created) VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(code) DO UPDATE SET amount=excluded.amount, uses_left=excluded.uses_left,"
                " total_uses=excluded.total_uses, expires=excluded.expires",
                (code.upper(), amount, uses, uses, exp, admin, now()))

    def delete_promo(self, code: str) -> bool:
        return self.ex("DELETE FROM promos WHERE code=?", (code.upper(),)).rowcount == 1

    def promos(self) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM promos ORDER BY created DESC LIMIT 30")

    def redeem_promo(self, code: str, uid: int) -> int:
        """Возвращает сумму начисления. Бросает ValueError с текстом причины."""
        code = code.upper()
        with self.tx():
            p = self.promo(code)
            if not p:
                raise ValueError("Такого промокода нет 🤷")
            if p["expires"] and p["expires"] < now():
                raise ValueError("Срок действия промокода истёк ⏳")
            if p["uses_left"] <= 0:
                raise ValueError("Промокод уже полностью использован 😔")
            if self.one("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, uid)):
                raise ValueError("Ты уже активировал этот промокод 😉")
            self.ex("UPDATE promos SET uses_left=uses_left-1 WHERE code=?", (code,))
            self.ex("INSERT INTO promo_uses(code,user_id,created) VALUES(?,?,?)", (code, uid, now()))
            self.change_balance(uid, p["amount"], f"promo:{code}")
            return int(p["amount"])

    # ───────────────────────── кланы ─────────────────────────
    def clan(self, cid: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM clans WHERE id=?", (cid,))

    def clan_by_tag(self, tag: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM clans WHERE lower(tag)=lower(?)", (tag.strip("[]"),))

    def user_clan(self, uid: int) -> sqlite3.Row | None:
        return self.one("SELECT c.* FROM clans c JOIN users u ON u.clan_id=c.id WHERE u.id=?", (uid,))

    def tag_of(self, uid: int) -> str:
        return str(self.val("SELECT c.tag FROM clans c JOIN users u ON u.clan_id=c.id WHERE u.id=?", (uid,), "") or "")

    def create_clan(self, tag: str, name: str, owner: int, price: int) -> sqlite3.Row:
        with self.tx():
            if self.clan_by_tag(tag):
                raise ValueError("Клан с таким тегом уже есть")
            if self.val("SELECT clan_id FROM users WHERE id=?", (owner,), None):
                raise ValueError("Ты уже состоишь в клане")
            self.change_balance(owner, -price, "clan:create")
            cur = self.ex("INSERT INTO clans(tag,name,owner,treasury,open,created) VALUES(?,?,?,0,0,?)",
                          (tag, name, owner, now()))
            cid = cur.lastrowid
            self.ex("UPDATE users SET clan_id=?, clan_role='owner' WHERE id=?", (cid, owner))
            return self.clan(cid)

    def clan_members(self, cid: int) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM users WHERE clan_id=? ORDER BY CASE clan_role WHEN 'owner' THEN 0"
                        " WHEN 'deputy' THEN 1 ELSE 2 END, balance DESC", (cid,))

    def clan_size(self, cid: int) -> int:
        return int(self.val("SELECT COUNT(*) FROM users WHERE clan_id=?", (cid,)))

    def clan_join(self, uid: int, cid: int, role: str = "member") -> bool:
        with self.tx():
            cur = self.ex("UPDATE users SET clan_id=?, clan_role=? WHERE id=? AND clan_id IS NULL", (cid, role, uid))
            self.ex("DELETE FROM clan_invites WHERE user_id=?", (uid,))
            return cur.rowcount == 1

    def clan_leave(self, uid: int) -> None:
        self.ex("UPDATE users SET clan_id=NULL, clan_role='' WHERE id=?", (uid,))

    def clan_set_role(self, uid: int, role: str) -> None:
        self.ex("UPDATE users SET clan_role=? WHERE id=?", (role, uid))

    def clan_set_owner(self, cid: int, new_owner: int, old_owner: int) -> None:
        with self.tx():
            self.ex("UPDATE clans SET owner=? WHERE id=?", (new_owner, cid))
            self.ex("UPDATE users SET clan_role='owner' WHERE id=? AND clan_id=?", (new_owner, cid))
            self.ex("UPDATE users SET clan_role='member' WHERE id=? AND clan_id=?", (old_owner, cid))

    def clan_update(self, cid: int, **fields) -> None:
        for k, v in fields.items():
            if k in ("tag", "name", "open"):
                self.ex(f"UPDATE clans SET {k}=? WHERE id=?", (v, cid))

    def clan_treasury(self, cid: int, uid: int, delta: int, action: str) -> int:
        """Меняет казну клана (delta>0 — пополнение с баланса игрока, delta<0 — выдача игроку)."""
        with self.tx():
            if delta > 0:
                self.change_balance(uid, -delta, f"clan:deposit:{cid}")
                self.ex("UPDATE clans SET treasury=treasury+? WHERE id=?", (delta, cid))
            else:
                cur = self.ex("UPDATE clans SET treasury=treasury+? WHERE id=? AND treasury+?>=0", (delta, cid, delta))
                if cur.rowcount != 1:
                    raise InsufficientFunds("В казне недостаточно средств")
                self.change_balance(uid, -delta, f"clan:withdraw:{cid}")
            self.ex("INSERT INTO clan_log(clan_id,user_id,delta,action,created) VALUES(?,?,?,?,?)",
                    (cid, uid, delta, action, now()))
            return int(self.val("SELECT treasury FROM clans WHERE id=?", (cid,)))

    def delete_clan(self, cid: int) -> int:
        """Удаляет клан, казну возвращает владельцу. Возвращает сумму возврата."""
        with self.tx():
            c = self.clan(cid)
            if not c:
                return 0
            tre = int(c["treasury"])
            if tre > 0:
                self.change_balance(int(c["owner"]), tre, f"clan:dissolve:{cid}")
            self.ex("UPDATE users SET clan_id=NULL, clan_role='' WHERE clan_id=?", (cid,))
            self.ex("DELETE FROM clan_invites WHERE clan_id=?", (cid,))
            self.ex("DELETE FROM clans WHERE id=?", (cid,))
            return tre

    def clan_invite(self, cid: int, uid: int, by: int) -> None:
        self.ex("INSERT INTO clan_invites(clan_id,user_id,by,created) VALUES(?,?,?,?)"
                " ON CONFLICT(clan_id,user_id) DO UPDATE SET by=excluded.by, created=excluded.created",
                (cid, uid, by, now()))

    def clan_invites_for(self, uid: int) -> list[sqlite3.Row]:
        return self.all("SELECT i.*, c.tag, c.name FROM clan_invites i JOIN clans c ON c.id=i.clan_id"
                        " WHERE i.user_id=? AND i.created>? ORDER BY i.created DESC", (uid, now() - 86400))

    def top_clans(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.all("SELECT c.*, (SELECT COUNT(*) FROM users u WHERE u.clan_id=c.id) AS members,"
                        " (SELECT COALESCE(SUM(balance),0) FROM users u WHERE u.clan_id=c.id) AS wealth"
                        " FROM clans c ORDER BY c.treasury DESC, wealth DESC LIMIT ?", (limit,))

    # ───────────────────────── рынок номеров ─────────────────────────
    def listing(self, nid: str) -> sqlite3.Row | None:
        return self.one("SELECT m.*, n.full, n.tier, n.value, n.user_id FROM market m JOIN numbers n ON n.id=m.number_id"
                        " WHERE m.number_id=?", (nid.upper().lstrip("#"),))

    def list_number(self, nid: str, seller: int, price: int, buyer: int | None) -> None:
        self.ex("INSERT INTO market(number_id,seller,price,buyer,created) VALUES(?,?,?,?,?)"
                " ON CONFLICT(number_id) DO UPDATE SET price=excluded.price, buyer=excluded.buyer, created=excluded.created",
                (nid, seller, price, buyer, now()))

    def unlist(self, nid: str) -> bool:
        return self.ex("DELETE FROM market WHERE number_id=?", (nid,)).rowcount == 1

    def market(self, limit: int = 10, offset: int = 0) -> list[sqlite3.Row]:
        return self.all("SELECT m.*, n.full, n.tier, n.value, n.operator, u.first_name AS sname FROM market m"
                        " JOIN numbers n ON n.id=m.number_id JOIN users u ON u.id=m.seller"
                        " WHERE m.buyer IS NULL ORDER BY m.created DESC LIMIT ? OFFSET ?", (limit, offset))

    def market_count(self) -> int:
        return int(self.val("SELECT COUNT(*) FROM market WHERE buyer IS NULL"))

    def user_listings(self, uid: int) -> list[sqlite3.Row]:
        return self.all("SELECT m.*, n.full, n.tier, n.value FROM market m JOIN numbers n ON n.id=m.number_id"
                        " WHERE m.seller=? ORDER BY m.created DESC", (uid,))

    def offers_for(self, uid: int) -> list[sqlite3.Row]:
        return self.all("SELECT m.*, n.full, n.tier, n.value, u.first_name AS sname FROM market m"
                        " JOIN numbers n ON n.id=m.number_id JOIN users u ON u.id=m.seller"
                        " WHERE m.buyer=? ORDER BY m.created DESC", (uid,))

    def buy_number(self, nid: str, buyer: int, fee_pct: int) -> dict:
        """Покупка лота. Возвращает {'seller','price','got'}; бросает ValueError/InsufficientFunds."""
        with self.tx():
            m = self.listing(nid)
            if not m:
                raise ValueError("Этот номер не продаётся")
            if int(m["user_id"]) != int(m["seller"]):
                self.unlist(nid)
                raise ValueError("Лот устарел — номер сменил владельца")
            if m["buyer"] is not None and int(m["buyer"]) != buyer:
                raise ValueError("Это адресное предложение другому игроку")
            if int(m["seller"]) == buyer:
                raise ValueError("Это твой собственный лот")
            price = int(m["price"])
            got = price - price * fee_pct // 100
            self.change_balance(buyer, -price, f"market:buy:{nid}")
            self.change_balance(int(m["seller"]), got, f"market:sell:{nid}")
            if not self.transfer_number(nid, int(m["seller"]), buyer, "market"):
                raise ValueError("Не удалось передать номер")
            return {"seller": int(m["seller"]), "price": price, "got": got, "full": m["full"], "tier": m["tier"]}

    # ───────────────────────── обмены ─────────────────────────
    def create_trade(self, from_user: int, to_user: int, give_id: str, take_id: str, extra: int) -> str:
        tid = new_id(6)
        self.ex("INSERT INTO trades(id,from_user,to_user,give_id,take_id,extra,status,created) VALUES(?,?,?,?,?,?,'open',?)",
                (tid, from_user, to_user, give_id, take_id, extra, now()))
        return tid

    def trade(self, tid: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM trades WHERE id=?", (tid.upper().lstrip("#"),))

    def trades_for(self, uid: int) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM trades WHERE status='open' AND (to_user=? OR from_user=?) ORDER BY created DESC",
                        (uid, uid))

    def set_trade_status(self, tid: str, status: str) -> bool:
        return self.ex("UPDATE trades SET status=?, finished=? WHERE id=? AND status='open'",
                       (status, now(), tid)).rowcount == 1

    def accept_trade(self, tid: str, uid: int) -> sqlite3.Row:
        """Атомарный обмен номерами (+ доплата). Бросает ValueError/InsufficientFunds."""
        with self.tx():
            t = self.trade(tid)
            if not t or t["status"] != "open":
                raise ValueError("Обмен уже неактуален")
            if int(t["to_user"]) != uid:
                raise ValueError("Это предложение адресовано другому игроку")
            give, take = self.number(t["give_id"]), self.number(t["take_id"])
            if not give or int(give["user_id"]) != int(t["from_user"]) or not take or int(take["user_id"]) != uid:
                self.set_trade_status(tid, "void")
                raise ValueError("Один из номеров уже сменил владельца — обмен отменён")
            extra = int(t["extra"])
            if extra > 0:
                self.change_balance(int(t["from_user"]), -extra, f"trade:pay:{tid}")
                self.change_balance(uid, extra, f"trade:get:{tid}")
            self.transfer_number(t["give_id"], int(t["from_user"]), uid, "trade")
            self.transfer_number(t["take_id"], uid, int(t["from_user"]), "trade")
            self.set_trade_status(tid, "done")
            return t

    def expired_trades(self, ttl: int) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM trades WHERE status='open' AND created<?", (now() - ttl,))

    # ───────────────────────── викторины ─────────────────────────
    def active_quiz(self, chat_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM quizzes WHERE chat_id=? AND status='active' ORDER BY id DESC", (chat_id,))

    def create_quiz(self, chat_id: int, creator: int, level: int, reward: int, question: str, answer: str) -> int:
        cur = self.ex("INSERT INTO quizzes(chat_id,creator,level,reward,question,answer,status,created)"
                      " VALUES(?,?,?,?,?,?,'active',?)", (chat_id, creator, level, reward, question, answer, now()))
        return int(cur.lastrowid)

    def finish_quiz(self, qid: int, status: str, winner: int | None = None) -> bool:
        cur = self.ex("UPDATE quizzes SET status=?, winner=?, finished=? WHERE id=? AND status='active'",
                      (status, winner, now(), qid))
        return cur.rowcount == 1

    def expired_quizzes(self, ttl: int) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM quizzes WHERE status='active' AND created<?", (now() - ttl,))

    def set_quiz_msg(self, qid: int, msg_id: int | None) -> None:
        self.ex("UPDATE quizzes SET msg_id=? WHERE id=?", (msg_id, qid))

    # ───────────────────────── аудит и статистика ─────────────────────────
    def audit(self, admin_id: int, action: str, details: Any) -> None:
        self.ex("INSERT INTO audit(admin_id,action,details,created) VALUES(?,?,?,?)",
                (admin_id, action, dumps(details) if not isinstance(details, str) else details, now()))

    def audit_log(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    def stats(self) -> dict:
        t = now()
        day = t - 86400
        return {
            "users": self.val("SELECT COUNT(*) FROM users"),
            "new_today": self.val("SELECT COUNT(*) FROM users WHERE created>?", (day,)),
            "active_today": self.val("SELECT COUNT(*) FROM users WHERE last_seen>?", (day,)),
            "banned": self.val("SELECT COUNT(*) FROM users WHERE banned=1"),
            "balance_sum": self.val("SELECT COALESCE(SUM(balance),0) FROM users"),
            "games": self.val("SELECT COUNT(*) FROM games WHERE status!='active'"),
            "games_today": self.val("SELECT COUNT(*) FROM games WHERE status!='active' AND finished>?", (day,)),
            "total_bet": self.val("SELECT COALESCE(SUM(bet),0) FROM games WHERE status!='active'"),
            "total_payout": self.val("SELECT COALESCE(SUM(payout),0) FROM games WHERE status!='active'"),
            "spins": self.val("SELECT COALESCE(SUM(spins),0) FROM users"),
            "numbers": self.val("SELECT COUNT(*) FROM numbers"),
            "numbers_value": self.val("SELECT COALESCE(SUM(value),0) FROM numbers"),
            "stars": self.val("SELECT COALESCE(SUM(stars),0) FROM payments WHERE refunded=0"),
            "payments": self.val("SELECT COUNT(*) FROM payments"),
            "open_duels": self.val("SELECT COUNT(*) FROM duels WHERE status IN ('open','invited')"),
            "tiers": {r["tier"]: r["c"] for r in self.all("SELECT tier, COUNT(*) c FROM numbers GROUP BY tier")},
        }

    def top(self, kind: str, limit: int = 10) -> list[sqlite3.Row]:
        base = "SELECT u.id, u.first_name, c.tag AS tag, {v} AS v FROM users u LEFT JOIN clans c ON c.id=u.clan_id"
        if kind == "balance":
            return self.all(base.format(v="u.balance") + " WHERE u.banned=0 ORDER BY v DESC LIMIT ?", (limit,))
        if kind == "won":
            return self.all(base.format(v="u.total_won") + " WHERE u.banned=0 ORDER BY v DESC LIMIT ?", (limit,))
        if kind == "collection":
            return self.all("SELECT u.id, u.first_name, c.tag AS tag, COALESCE(SUM(n.value),0) AS v FROM users u"
                            " LEFT JOIN clans c ON c.id=u.clan_id JOIN numbers n ON n.user_id=u.id"
                            " WHERE u.banned=0 GROUP BY u.id ORDER BY v DESC LIMIT ?", (limit,))
        return self.all(base.format(v="u.spins") + " WHERE u.banned=0 ORDER BY v DESC LIMIT ?", (limit,))
