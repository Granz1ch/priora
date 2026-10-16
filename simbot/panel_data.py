"""Слой данных секретной панели: чтение статистики и безопасные мутации базы.

Никакого HTTP здесь нет — только SQLite через DB бота, чтобы код можно было
переиспользовать в тестах и при необходимости в других интерфейсах.
Все «руки» админа идут через методы DB (change_balance/audit/…), т.е. панель
делает ровно то же, что и текстовые команды администрации.
"""
from __future__ import annotations

import calendar
import json
import time
from typing import Any

from .config import DEFAULT_SETTINGS, RANK_NAMES, SECTIONS, SECTION_TITLES
from .db import DB, InsufficientFunds, now
from .numbers import TIERS, TIER, appraise, fmt_number, generate_digits, normalize_odds, parse_tier

MSK = 3 * 3600              # группировка по дням — как в ui.date() (МСК)
PAGE_MAX = 200


class PanelError(Exception):
    """Ошибка, текст которой панель показывает админу."""


# ───────────────────────── утилиты ─────────────────────────
def rows(db: DB, sql: str, args: tuple = ()) -> list[dict]:
    return [{k: r[k] for k in r.keys()} for r in db.all(sql, args)]


def one(db: DB, sql: str, args: tuple = ()) -> dict | None:
    r = db.one(sql, args)
    return {k: r[k] for k in r.keys()} if r is not None else None


def _day(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts) + MSK))


def _jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


# ───────────────────────── обзор ─────────────────────────
def series(db: DB, days: int = 14) -> list[dict]:
    """Динамика по дням: игроки, ставки, выплаты, GGR, депозиты, прокрутки."""
    days = max(2, min(60, int(days)))
    start = _day(now() - (days - 1) * 86400)
    buckets: dict[str, dict] = {}
    t = calendar.timegm(time.strptime(start, "%Y-%m-%d")) - MSK

    def b(d: str) -> dict:
        return buckets.setdefault(d, {"day": d, "new_users": 0, "active": 0, "games": 0, "players": 0,
                                      "bet": 0, "payout": 0, "ggr": 0, "stars": 0, "rubles": 0,
                                      "spins": 0, "numbers_value": 0})

    for r in rows(db, "SELECT date(created/1+?,'unixepoch') d, COUNT(*) c FROM users WHERE created>=? GROUP BY d",
                  (MSK, int(t))):
        b(r["d"])["new_users"] = r["c"]
    for r in rows(db, "SELECT date(last_seen/1+?,'unixepoch') d, COUNT(*) c FROM users WHERE last_seen>=? GROUP BY d",
                  (MSK, int(t))):
        b(r["d"])["active"] = r["c"]
    for r in rows(db, "SELECT date(finished/1+?,'unixepoch') d, COUNT(*) c, COALESCE(SUM(bet),0) bet,"
                      " COALESCE(SUM(payout),0) paid, COUNT(DISTINCT user_id) p FROM games"
                      " WHERE status!='active' AND finished>=? GROUP BY d", (MSK, int(t))):
        x = b(r["d"])
        x["games"], x["bet"], x["payout"], x["players"] = r["c"], r["bet"], r["paid"], r["p"]
        x["ggr"] = r["bet"] - r["paid"]
    for r in rows(db, "SELECT date(created/1+?,'unixepoch') d, COUNT(*) c FROM numbers WHERE created>=?"
                      " GROUP BY d", (MSK, int(t))):
        b(r["d"])["spins"] = r["c"]
    for r in rows(db, "SELECT date(created/1+?,'unixepoch') d, COALESCE(SUM(stars),0) s, COALESCE(SUM(rubles),0) rb"
                      " FROM payments WHERE refunded=0 AND created>=? GROUP BY d", (MSK, int(t))):
        b(r["d"])["stars"], b(r["d"])["rubles"] = r["s"], r["rb"]

    out = []
    for i in range(days):
        d = _day(int(t) + i * 86400)
        out.append(buckets.get(d) or {"day": d, "new_users": 0, "active": 0, "games": 0, "players": 0,
                                      "bet": 0, "payout": 0, "ggr": 0, "stars": 0, "rubles": 0,
                                      "spins": 0, "numbers_value": 0})
    return out


def by_kind(db: DB) -> list[dict]:
    """Разрез игр: сколько ставок/выплат по каждому режиму — где казино теряет, а где зарабатывает."""
    return rows(db, "SELECT kind, COUNT(*) games, COUNT(DISTINCT user_id) players, COALESCE(SUM(bet),0) bet,"
                    " COALESCE(SUM(payout),0) payout, COALESCE(SUM(payout)-SUM(bet),0) net,"
                    " SUM(CASE WHEN payout>bet THEN 1 ELSE 0 END) wins, MAX(bet) max_bet"
                    " FROM games WHERE status!='active' GROUP BY kind ORDER BY bet DESC")


def tiers(db: DB) -> list[dict]:
    r = rows(db, "SELECT tier, COUNT(*) c, COALESCE(SUM(value),0) v FROM numbers GROUP BY tier")
    m = {x["tier"]: x for x in r}
    return [{"tier": t, "name": TIER[t]["name"], "emoji": TIER[t]["emoji"],
             "count": m.get(t, {}).get("c", 0), "value": m.get(t, {}).get("v", 0)} for t in TIERS]


def overview(db: DB) -> dict:
    t = now()
    s = db.stats()
    day = t - 86400
    hour = t - 3600
    hr = rows(db, "SELECT COALESCE(SUM(bet),0) bet, COALESCE(SUM(payout),0) paid, COUNT(*) c,"
                  " COUNT(DISTINCT user_id) p, COALESCE(SUM(CASE WHEN payout>bet THEN 1 ELSE 0 END),0) w"
                  " FROM games WHERE status!='active' AND finished>?", (hour,))
    h = hr[0] if hr else {}
    return {
        "now": t,
        "stats": s,
        "ggr": int(s["total_bet"]) - int(s["total_payout"]),
        "hold": round((1 - int(s["total_payout"]) / int(s["total_bet"])) * 100, 2) if s["total_bet"] else 0.0,
        "clans": db.val("SELECT COUNT(*) FROM clans"),
        "treasury": db.val("SELECT COALESCE(SUM(treasury),0) FROM clans"),
        "chats": db.val("SELECT COUNT(*) FROM chats WHERE allowed=1"),
        "chats_banned": db.val("SELECT COUNT(*) FROM chats WHERE banned=1"),
        "promos": db.val("SELECT COUNT(*) FROM promos WHERE uses_left>0 AND (expires=0 OR expires>?)", (t,)),
        "muted": db.val("SELECT COUNT(*) FROM users WHERE muted_until>?", (t,)),
        "warned": db.val("SELECT COUNT(*) FROM users WHERE warns>0"),
        "banned": int(s["banned"]),
        "active_games": db.val("SELECT COUNT(*) FROM games WHERE status='active'"),
        "open_trades": db.val("SELECT COUNT(*) FROM trades WHERE status='open'"),
        "market_lots": db.val("SELECT COUNT(*) FROM market WHERE buyer IS NULL"),
        "quizzes": db.val("SELECT COUNT(*) FROM quizzes WHERE status='active'"),
        "hour": {"bet": int(h.get("bet") or 0), "payout": int(h.get("paid") or 0), "games": int(h.get("c") or 0),
                 "players": int(h.get("p") or 0), "wins": int(h.get("w") or 0)},
        "day": {"new": int(s["new_today"]), "active": int(s["active_today"]), "games": int(s["games_today"]),
                "deposits": db.val("SELECT COUNT(*) FROM payments WHERE created>?", (day,)),
                "stars": db.val("SELECT COALESCE(SUM(stars),0) FROM payments WHERE created>?", (day,))},
        "series": series(db, 14),
        "live": live_games(db),
        "kinds": by_kind(db),
        "tiers": tiers(db),
        "top_balance": rows(db, "SELECT id, first_name, username, balance, games, last_seen FROM users"
                                " WHERE banned=0 ORDER BY balance DESC LIMIT 10"),
        "top_net": rows(db, "SELECT id, first_name, username, total_bet, total_won,"
                            " (total_won-total_bet) net FROM users WHERE games>0 ORDER BY net DESC LIMIT 10"),
        "bottom_net": rows(db, "SELECT id, first_name, username, total_bet, total_won,"
                               " (total_won-total_bet) net FROM users WHERE games>0 ORDER BY net ASC LIMIT 10"),
        "whales": rows(db, "SELECT g.id, g.user_id, u.first_name, g.kind, g.bet, g.payout, g.status, g.created,"
                           " g.finished FROM games g JOIN users u ON u.id=g.user_id WHERE g.bet>=100000"
                           " ORDER BY g.id DESC LIMIT 10"),
        "new_users": rows(db, "SELECT id, first_name, username, balance, created, last_seen FROM users"
                               " ORDER BY created DESC LIMIT 10"),
        "staff": rows(db, "SELECT id, first_name, username, rank, is_admin, balance FROM users"
                          " WHERE rank>0 OR is_admin=1 ORDER BY rank DESC, id"),
        "audit": rows(db, "SELECT * FROM audit ORDER BY id DESC LIMIT 12"),
        "settings": all_settings(db),
        "maintenance": int(db.setting("maintenance", 0)),
        "sections": sections_state(db),
    }


# ───────────────────────── пользователи ─────────────────────────
USER_SORTS = {
    "id": "u.id", "balance": "u.balance", "net": "(u.total_won-u.total_bet)", "games": "u.games",
    "wins": "u.wins", "spins": "u.spins", "last_seen": "u.last_seen", "created": "u.created",
    "nums": "nums", "collection": "coll", "bet": "u.total_bet", "won": "u.total_won", "stars": "u.stars_paid",
    "biggest": "u.biggest_win", "warns": "u.warns", "rank": "u.rank",
}

USER_FILTERS = {
    "all": "1=1",
    "banned": "u.banned=1",
    "muted": "u.muted_until>" ,
    "staff": "u.rank>0",
    "active": "u.last_seen>$ts_day",
    "week": "u.last_seen>$ts_week",
    "new": "u.created>$ts_week",
    "quiet": "u.last_seen<$ts_week",
    "zero": "u.balance=0",
    "rich": "u.balance>=500000",
    "clan": "u.clan_id IS NOT NULL",
    "paid": "u.stars_paid>0",
    "minus": "(u.total_won-u.total_bet)<0",
    "plus": "(u.total_won-u.total_bet)>0",
}

_BASE_USERS = ("FROM users u LEFT JOIN clans c ON c.id=u.clan_id "
               "LEFT JOIN (SELECT user_id, COUNT(*) cnt, COALESCE(SUM(value),0) val FROM numbers GROUP BY user_id) n"
               " ON n.user_id=u.id")
_COLS_USERS = ("u.*, COALESCE(c.tag,'') AS tag, COALESCE(c.name,'') AS clan_name, "
               "COALESCE(n.cnt,0) AS nums, COALESCE(n.val,0) AS coll")


def list_users(db: DB, q: str = "", flt: str = "all", sort: str = "balance", desc: bool = True,
               page: int = 1, limit: int = 50) -> dict:
    limit = max(10, min(PAGE_MAX, int(limit)))
    page = max(1, int(page))
    t = now()
    cond = USER_FILTERS.get(flt, "1=1")
    cond = cond.replace("$ts_day", str(int(t - 86400))).replace("$ts_week", str(int(t - 7 * 86400)))
    if flt == "muted":
        cond = f"u.muted_until>{int(t)}"
    args: list[Any] = []
    q = (q or "").strip()
    if q:
        if q.lstrip("-").isdigit():
            cond += " AND u.id=?"
            args.append(int(q.lstrip("-")))
        else:
            cond += " AND (u.first_name LIKE ? OR u.username LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
    order = USER_SORTS.get(sort, "u.balance")
    d = "DESC" if desc else "ASC"
    where = f"WHERE {cond}"
    total = int(db.val(f"SELECT COUNT(*) { _BASE_USERS} {where}", tuple(args)))
    money_sum = int(db.val(f"SELECT COALESCE(SUM(u.balance),0) {_BASE_USERS} {where}", tuple(args)))
    data = rows(db, f"SELECT {_COLS_USERS} {_BASE_USERS} {where} ORDER BY {order} {d}, u.id DESC LIMIT ? OFFSET ?",
                tuple(args) + (limit, (page - 1) * limit))
    for r in data:
        r["net"] = int(r["total_won"] or 0) - int(r["total_bet"] or 0)
        r["rank"] = int(r["rank"] or 0)
    return {"users": data, "total": total, "page": page, "pages": max(1, -(-total // limit)),
            "limit": limit, "balance_sum": money_sum, "q": q, "filter": flt, "sort": sort, "desc": desc}


def user_ids_by_filter(db: DB, flt: str = "all") -> list[int]:
    """ID игроков под фильтр с вкладки «Игроки» — для адресной рассылки."""
    if not flt or flt == "all":
        return [int(r["id"]) for r in rows(db, "SELECT id FROM users WHERE banned=0")]
    cond = USER_FILTERS.get(flt)
    if not cond:
        raise PanelError(f"Не знаю фильтр <code>{esc_html(flt)}</code>")
    t = now()
    cond = cond.replace("$ts_day", str(int(t - 86400))).replace("$ts_week", str(int(t - 7 * 86400)))
    if flt == "muted":
        cond = f"u.muted_until>{t}"
    return [int(r["id"]) for r in rows(db, f"SELECT u.id FROM users u WHERE {cond}")]


def user_detail(db: DB, uid: int) -> dict:
    """Всё, что известно об игроке: профиль, инвентарь, леджер, игры, дуэли, рынок, варны, платежи, клан."""
    u = one(db, "SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise PanelError(f"Игрок <code>{uid}</code> не найден — он должен хотя бы раз написать боту")
    rank = max(int(u["rank"] or 0), 3 if u["is_admin"] else 0)
    state, data = db.get_state(uid)
    ops = {o["code"]: o for o in rows(db, "SELECT code, name, emoji, price FROM operators")}
    numbers = rows(db, "SELECT * FROM numbers WHERE user_id=? ORDER BY value DESC, created DESC LIMIT 400", (uid,))
    listed = {r["number_id"]: r for r in rows(db, "SELECT * FROM market WHERE seller=?", (uid,))}
    for n in numbers:
        op = ops.get(n["operator"]) or {}
        n["op_name"] = op.get("name", n["operator"])
        n["op_emoji"] = op.get("emoji", "")
        n["tier_name"] = TIER.get(n["tier"], {}).get("name", n["tier"])
        n["tier_emoji"] = TIER.get(n["tier"], {}).get("emoji", "⚪")
        n["listed"] = int(listed[n["id"]]["price"]) if n["id"] in listed else 0
    clan = one(db, "SELECT c.* FROM clans c JOIN users u2 ON u2.clan_id=c.id WHERE u2.id=?", (uid,))
    return {
        "user": {**u, "net": int(u["total_won"] or 0) - int(u["total_bet"] or 0), "rank": rank,
                 "rank_name": RANK_NAMES.get(rank, "Игрок"),
                 "hours": round((now() - int(u["created"])) / 3600, 1),
                 "is_admin": int(u["is_admin"] or 0), "banned": int(u["banned"] or 0),
                 "warns": int(u["warns"] or 0), "clan_id": u["clan_id"], "clan_role": u["clan_role"],
                 "muted_left": max(0, int(u["muted_until"] or 0) - now())},
        "clan": clan,
        "clan_members": rows(db, "SELECT id, first_name, username, clan_role, balance FROM users WHERE clan_id=?"
                                  " ORDER BY CASE clan_role WHEN 'owner' THEN 0 WHEN 'deputy' THEN 1 ELSE 2 END,"
                                  " balance DESC LIMIT 40", (clan["id"],)) if clan else [],
        "inventory": numbers,
        "inventory_count": db.val("SELECT COUNT(*) FROM numbers WHERE user_id=?", (uid,)),
        "inventory_value": db.val("SELECT COALESCE(SUM(value),0) FROM numbers WHERE user_id=?", (uid,)),
        "numbers_moved": rows(db, "SELECT * FROM number_log WHERE from_user=? OR to_user=?"
                                  " ORDER BY id DESC LIMIT 60", (uid, uid)),
        "ledger": rows(db, "SELECT * FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 80", (uid,)),
        "ledger_sum": rows(db, "SELECT reason, COUNT(*) c, COALESCE(SUM(delta),0) s FROM ledger WHERE user_id=?"
                               " AND (reason LIKE 'bonus%' OR reason LIKE 'admin%' OR reason LIKE 'deposit%'"
                               " OR reason LIKE 'promo%' OR reason LIKE 'ref%') GROUP BY reason ORDER BY s DESC"
                               " LIMIT 40", (uid,)),
        "games": rows(db, "SELECT id, kind, bet, payout, status, result, created, finished,"
                          " (payout-bet) net FROM games WHERE user_id=? ORDER BY id DESC LIMIT 80", (uid,)),
        "kinds": rows(db, "SELECT kind, COUNT(*) games, COALESCE(SUM(bet),0) bet, COALESCE(SUM(payout),0) paid,"
                          " COALESCE(SUM(payout)-SUM(bet),0) net FROM games WHERE user_id=? AND status!='active'"
                          " GROUP BY kind ORDER BY bet DESC", (uid,)),
        "duels": rows(db, "SELECT * FROM duels WHERE creator=? OR opponent=? ORDER BY id DESC LIMIT 40",
                      (uid, uid)),
        "trades": rows(db, "SELECT * FROM trades WHERE from_user=? OR to_user=? ORDER BY id DESC LIMIT 40",
                       (uid, uid)),
        "offers": rows(db, "SELECT m.*, n.full, n.tier FROM market m JOIN numbers n ON n.id=m.number_id"
                           " WHERE m.buyer=? ORDER BY m.created DESC LIMIT 40", (uid,)),
        "warns": rows(db, "SELECT * FROM warns WHERE user_id=? ORDER BY id DESC LIMIT 40", (uid,)),
        "payments": rows(db, "SELECT * FROM payments WHERE user_id=? ORDER BY created DESC LIMIT 40", (uid,)),
        "invoices": rows(db, "SELECT * FROM invoices WHERE user_id=? ORDER BY created DESC LIMIT 20", (uid,)),
        "promos": rows(db, "SELECT * FROM promo_uses WHERE user_id=? ORDER BY created DESC LIMIT 40", (uid,)),
        "chats": rows(db, "SELECT DISTINCT g.chat_id, COALESCE(c.title,'—') title, c.allowed, c.banned,"
                          " COUNT(*) games FROM games g LEFT JOIN chats c ON c.id=g.chat_id"
                          " WHERE g.user_id=? GROUP BY g.chat_id ORDER BY games DESC LIMIT 20", (uid,)),
        "refs_from": rows(db, "SELECT id, first_name, username, created FROM users WHERE ref_by=? ORDER BY id DESC"
                              " LIMIT 30", (uid,)),
        "ref_by": one(db, "SELECT id, first_name, username FROM users WHERE id=?", (int(u["ref_by"]),))
                  if u["ref_by"] else None,
        "quizzes_won": rows(db, "SELECT id, chat_id, reward, created FROM quizzes WHERE winner=? ORDER BY id DESC"
                                " LIMIT 20", (uid,)),
        "state": {"state": state, "data": data},
        "active": rows(db, "SELECT id, kind, bet, created FROM games WHERE user_id=? AND status='active'"
                           " ORDER BY id DESC", (uid,)),
        "flags": {"in_admin": db.val("SELECT COUNT(*) FROM users WHERE id=? AND (rank>0 OR is_admin=1)", (uid,)) > 0},
    }


# ───────────────────────── лента игроков / поиск ─────────────────────────
def find_user(db: DB, token: str) -> dict | None:
    token = (token or "").strip()
    if not token:
        return None
    r = db.find_user(token)
    return {"id": int(r["id"]), "first_name": r["first_name"], "username": r["username"],
            "balance": int(r["balance"])} if r else None


def autocomplete(db: DB, q: str, limit: int = 10) -> list[dict]:
    q = (q or "").strip()
    if len(q) < 1:
        return []
    if q.lstrip("-").isdigit():
        return rows(db, "SELECT id, first_name, username, balance FROM users WHERE id=? LIMIT ?",
                    (int(q.lstrip("-")), limit))
    return rows(db, "SELECT id, first_name, username, balance, last_seen FROM users"
                    " WHERE first_name LIKE ? OR username LIKE ? ORDER BY last_seen DESC LIMIT ?",
                (f"%{q}%", f"%{q}%", limit))


# ───────────────────────── таблицы («всю инфу нам известную») ─────────────────────────
#: table -> (user-колонки для фильтра, текстовые колонки для поиска)
TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "users": (("id",), ("first_name", "username", "ban_reason")),
    "numbers": (("user_id",), ("id", "full", "tier", "origin", "operator")),
    "number_log": (("from_user", "to_user"), ("number_id", "full", "action")),
    "ledger": (("user_id",), ("reason", "ref")),
    "games": (("user_id",), ("id", "kind", "status", "result")),
    "duels": (("creator", "opponent"), ("id", "status")),
    "trades": (("from_user", "to_user"), ("id", "give_id", "take_id", "status")),
    "market": (("seller", "buyer"), ("number_id",)),
    "promos": (("created_by",), ("code",)),
    "promo_uses": (("user_id",), ("code",)),
    "payments": (("user_id",), ("charge_id", "payload")),
    "invoices": (("user_id",), ("payload", "status")),
    "audit": (("admin_id",), ("action", "details")),
    "clans": (("owner",), ("tag", "name")),
    "clan_log": (("user_id",), ("action",)),
    "chats": ((), ("title",)),
    "warns": (("user_id", "by"), ("reason",)),
    "quizzes": (("creator", "winner"), ("question", "answer", "status")),
    "states": (("user_id",), ("state",)),
    "settings": ((), ("key", "value")),
    "operators": ((), ("code", "name", "country", "prefix")),
    "countries": ((), ("code", "name")),
}


def browse(db: DB, table: str, q: str = "", user: str = "", page: int = 1, limit: int = 50,
           order: str = "", desc: bool = True) -> dict:
    if table not in TABLES:
        raise PanelError(f"Таблица <code>{esc_html(table)}</code> недоступна. Доступны: "
                         + ", ".join(f"<code>{t}</code>" for t in TABLES))
    user_cols, text_cols = TABLES[table]
    limit = max(10, min(PAGE_MAX, int(limit)))
    page = max(1, int(page))
    cond, args = "1=1", []
    if user and user_cols:
        if user.lstrip("-").isdigit():
            cond += " AND (" + " OR ".join(f"{c}=?" for c in user_cols) + ")"
            args += [int(user.lstrip("-"))] * len(user_cols)
        else:
            ids = [int(r["id"]) for r in rows(db, "SELECT id FROM users WHERE lower(username)=lower(?)"
                                                  " OR first_name LIKE ?", (user.lstrip("@"), f"%{user}%"))]
            if not ids:
                return {"table": table, "rows": [], "total": 0, "page": 1, "pages": 1, "limit": limit}
            cond += " AND (" + " OR ".join(f"{c} IN ({','.join('?' * len(ids))})" for c in user_cols) + ")"
            args += ids * len(user_cols)
    q = (q or "").strip()
    if q and text_cols:
        if q.isdigit():
            cond += " AND (" + " OR ".join(f"CAST({c} AS TEXT)=?" for c in text_cols) + ")"
            args += [q] * len(text_cols)
        else:
            cond += " AND (" + " OR ".join(f"{c} LIKE ?" for c in text_cols) + ")"
            args += [f"%{q}%"] * len(text_cols)
    cols = [r["name"] for r in rows(db, f"PRAGMA table_info({table})")]
    if order in cols:
        sql_order = f"ORDER BY {order} {'DESC' if desc else 'ASC'}, rowid DESC"
    elif "id" in cols:
        sql_order = "ORDER BY id DESC"
    elif "created" in cols:
        sql_order = "ORDER BY created DESC"
    else:
        sql_order = "ORDER BY rowid DESC"
    total = int(db.val(f"SELECT COUNT(*) FROM {table} WHERE {cond}", tuple(args)))
    data = rows(db, f"SELECT * FROM {table} WHERE {cond} {sql_order} LIMIT ? OFFSET ?",
                tuple(args) + (limit, (page - 1) * limit))
    return {"table": table, "columns": cols, "rows": data, "total": total, "page": page,
            "pages": max(1, -(-total // limit)), "limit": limit, "user_cols": list(user_cols),
            "text_cols": list(text_cols)}


def feed(db: DB, kind: str = "", status: str = "finished", limit: int = 60, user: str = "") -> list[dict]:
    """Живая лента ставок (для вкладки «Игры» и автообновления)."""
    cond, args = ["1=1"], []
    if status == "active":
        cond.append("g.status='active'")
    elif status == "finished":
        cond.append("g.status!='active'")
    elif status == "big":
        cond.append("g.bet>=100000")
    if kind:
        cond.append("g.kind=?")
        args.append(kind)
    u = (user or "").strip()
    if u:
        is_id = u.lstrip("-").isdigit()
        cond.append("(u.id=? OR lower(u.username)=lower(?) OR u.first_name LIKE ?)" if is_id
                    else "(lower(u.username)=lower(?) OR u.first_name LIKE ?)")
        if is_id:
            args.append(int(u.lstrip("-")))
        args += [u.lstrip("@"), f"%{u}%"]
    return rows(db, f"SELECT g.id, g.user_id, u.first_name, u.username, g.kind, g.bet, g.payout, g.status,"
                    f" g.result, g.created, g.finished, (g.payout-g.bet) net, COALESCE(c.tag,'') tag"
                    f" FROM games g JOIN users u ON u.id=g.user_id LEFT JOIN clans c ON c.id=u.clan_id"
                    f" WHERE {' AND '.join(cond)} ORDER BY g.id DESC LIMIT ?", tuple(args) + (int(limit),))


def clans(db: DB) -> list[dict]:
    return rows(db, "SELECT c.*, (SELECT COUNT(*) FROM users u WHERE u.clan_id=c.id) members,"
                    " (SELECT COALESCE(SUM(u.balance),0) FROM users u WHERE u.clan_id=c.id) money,"
                    " o.first_name AS owner_name FROM clans c LEFT JOIN users o ON o.id=c.owner"
                    " ORDER BY c.treasury DESC, members DESC LIMIT 200")


def chats(db: DB) -> list[dict]:
    return rows(db, "SELECT c.*, (SELECT COUNT(DISTINCT user_id) FROM games g WHERE g.chat_id=c.id) players,"
                    " (SELECT COUNT(*) FROM games g WHERE g.chat_id=c.id) games,"
                    " (SELECT COUNT(*) FROM duels d WHERE d.chat_id=c.id) duels,"
                    " (SELECT COUNT(*) FROM quizzes q WHERE q.chat_id=c.id) quizzes"
                    " FROM chats c ORDER BY players DESC, c.id DESC LIMIT 300")


def promos(db: DB) -> list[dict]:
    t = now()
    out = rows(db, "SELECT p.*, u.first_name AS creator, (SELECT COUNT(*) FROM promo_uses x WHERE x.code=p.code)"
                  " uses FROM promos p LEFT JOIN users u ON u.id=p.created_by ORDER BY p.created DESC LIMIT 100")
    for r in out:
        r["alive"] = (not r["expires"] or r["expires"] > t) and r["uses_left"] > 0
    return out


def market(db: DB, limit: int = 100) -> list[dict]:
    return rows(db, "SELECT m.*, n.full, n.tier, n.value, s.first_name AS seller, b.first_name AS buyer_name"
                    " FROM market m JOIN numbers n ON n.id=m.number_id JOIN users s ON s.id=m.seller"
                    " LEFT JOIN users b ON b.id=m.buyer ORDER BY m.created DESC LIMIT ?", (int(limit),))


def trades(db: DB, status: str = "", limit: int = 100) -> list[dict]:
    cond = "1=1"
    args: list[Any] = []
    if status:
        cond, args = "t.status=?", [status]
    return rows(db, f"SELECT t.*, a.first_name AS from_name, b.first_name AS to_name, na.full AS give_full,"
                    f" nb.full AS take_full FROM trades t LEFT JOIN users a ON a.id=t.from_user"
                    f" LEFT JOIN users b ON b.id=t.to_user LEFT JOIN numbers na ON na.id=t.give_id"
                    f" LEFT JOIN numbers nb ON nb.id=t.take_id WHERE {cond} ORDER BY t.id DESC LIMIT ?",
                tuple(args) + (int(limit),))


def duels(db: DB, status: str = "", limit: int = 100) -> list[dict]:
    cond, args = "1=1", []
    if status:
        cond, args = "d.status=?", [status]
    return rows(db, f"SELECT d.*, a.first_name AS creator_name, b.first_name AS opponent_name FROM duels d"
                    f" LEFT JOIN users a ON a.id=d.creator LEFT JOIN users b ON b.id=d.opponent"
                    f" WHERE {cond} ORDER BY d.id DESC LIMIT ?", tuple(args) + (int(limit),))


def payments(db: DB, limit: int = 100) -> list[dict]:
    return rows(db, "SELECT p.*, u.first_name, u.username FROM payments p LEFT JOIN users u ON u.id=p.user_id"
                    " ORDER BY p.created DESC LIMIT ?", (int(limit),))


def all_settings(db: DB) -> dict:
    out = {}
    for k in DEFAULT_SETTINGS:
        out[k] = db.setting(k)
    raw = {r["key"]: r["value"] for r in rows(db, "SELECT key, value FROM settings")}
    return {"values": out, "raw": raw, "defaults": dict(DEFAULT_SETTINGS)}


def sections_state(db: DB) -> dict:
    return {key: bool(int(db.setting(f"section:{key}", 1))) for key in SECTION_TITLES}


def operators(db: DB) -> list[dict]:
    out = []
    for o in rows(db, "SELECT o.*, c.name country_name, c.flag, c.dial FROM operators o"
                      " LEFT JOIN countries c ON c.code=o.country ORDER BY o.sort, o.price"):
        try:
            odds = json.loads(o["odds"])
        except (json.JSONDecodeError, TypeError):
            odds = {}
        total = sum(float(odds.get(t, 0)) for t in TIERS) or 1.0
        o["odds"] = odds
        o["odds_pct"] = {t: round(float(odds.get(t, 0)) / total * 100, 3) for t in TIERS}
        o["numbers"] = db.val("SELECT COUNT(*) FROM numbers WHERE operator=?", (o["code"],))
        o["expected"] = round(sum(float(o["odds_pct"].get(t, 0)) / 100 * TIER[t]["value"] for t in TIERS), 1)
        out.append(o)
    return out


def countries(db: DB) -> list[dict]:
    return rows(db, "SELECT c.*, (SELECT COUNT(*) FROM operators o WHERE o.country=c.code) operators,"
                    " (SELECT COUNT(*) FROM numbers n WHERE n.country=c.code) numbers FROM countries c"
                    " ORDER BY c.sort, c.name")


def audit(db: DB, limit: int = 100, action: str = "") -> list[dict]:
    cond, args = "1=1", []
    if action:
        cond, args = "a.action=?", [action]
    return rows(db, f"SELECT a.*, u.first_name admin, u.username admin_uname FROM audit a"
                    f" LEFT JOIN users u ON u.id=a.admin_id WHERE {cond} ORDER BY a.id DESC LIMIT ?",
                tuple(args) + (int(limit),))


def live_games(db: DB) -> list[dict]:
    """Активные сессии краша/апгрейдера с состоянием — видно, кто сейчас играет и на какой множитель зайдёт."""
    out = []
    for g in rows(db, "SELECT g.*, u.first_name, u.username FROM games g JOIN users u ON u.id=g.user_id"
                      " WHERE g.status='active' ORDER BY g.id DESC LIMIT 60"):
        try:
            st = json.loads(g["state"] or "{}")
        except json.JSONDecodeError:
            st = {}
        g["state"] = st
        g["age"] = now() - int(g["created"])
        out.append(g)
    return out


# ═══════════════════════════════════════════════════════════
#  ДЕЙСТВИЯ (пишущие). Каждое возвращает короткий отчёт для UI.
# ═══════════════════════════════════════════════════════════
def require_user(db: DB, uid: Any) -> dict:
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        raise PanelError("Нужен числовой ID игрока") from None
    u = one(db, "SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise PanelError(f"Игрок <code>{uid}</code> не найден")
    return u


def amount(v: Any) -> int:
    s = str(v).strip().replace("\u00a0", "").replace(" ", "")
    mult = 1
    if s and s[-1] in "кК":
        mult, s = 1000, s[:-1]
    if s.endswith("₽"):
        s = s[:-1]
    try:
        n = int(float(s) * mult)
    except (TypeError, ValueError):
        raise PanelError(f"Не понимаю сумму: <code>{esc_html(v)}</code>") from None
    return n


def esc_html(v: Any) -> str:
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def log(db: DB, actor: int, action: str, details: Any) -> None:
    db.audit(actor, action, details if isinstance(details, str) else _jdump(details))


def balance_op(db: DB, actor: int, uid: Any, op: str, value: Any = 0) -> dict:
    u = require_user(db, uid)
    uid = int(u["id"])
    if op == "reset":
        amount_v, reason = 0, "panel:reset"
    elif op == "set":
        amount_v, reason = amount(value), "panel:set"
    elif op == "give":
        amount_v, reason = amount(value), "panel:give"
    elif op == "take":
        amount_v, reason = -min(amount(value), int(u["balance"])), "panel:take"
    else:
        raise PanelError("Операция с балансом: give | take | set | reset")
    if op == "take" and int(u["balance"]) <= 0:
        raise PanelError("У игрока нет средств, которые можно забрать")
    if op != "reset" and amount_v == 0:
        raise PanelError("Сумма не может быть нулевой")
    if amount_v < 0 and int(u["balance"]) + amount_v < 0:
        raise PanelError(f"У игрока только {u['balance']} ₽ — забрать больше нельзя")
    delta = amount_v - (0 if op in ("give", "take") else int(u["balance"]))
    if op == "reset":
        delta = -int(u["balance"])
        if delta == 0:
            return {"ok": True, "balance": 0, "changed": 0, "note": "баланс уже нулевой"}
    new = db.change_balance(uid, delta, f"{reason}:{actor}")
    log(db, actor, f"balance:{op}", {"user": uid, "amount": amount_v if op != "reset" else 0, "to": new})
    return {"ok": True, "balance": new, "changed": delta}


def ban(db: DB, actor: int, uid: Any, on: bool, reason: str = "") -> dict:
    u = require_user(db, uid)
    db.set_ban(int(u["id"]), on, reason)
    log(db, actor, "ban" if on else "unban", {"user": int(u["id"]), "reason": reason})
    return {"ok": True, "banned": 1 if on else 0}


def mute(db: DB, actor: int, uid: Any, seconds: int) -> dict:
    u = require_user(db, uid)
    until = (now() + seconds) if seconds > 0 else 0
    db.set_mute(int(u["id"]), until)
    log(db, actor, "mute", {"user": int(u["id"]), "until": until})
    return {"ok": True, "muted_until": until}


def warn(db: DB, actor: int, uid: Any, reason: str = "") -> dict:
    u = require_user(db, uid)
    n = db.add_warn(int(u["id"]), actor, reason or "из панели")
    limit = int(db.setting("warn_limit", 3))
    extra = {}
    if n >= limit:
        db.set_ban(int(u["id"]), True, f"{limit} предупреждения")
        extra = {"banned": True}
    log(db, actor, "warn", {"user": int(u["id"]), "total": n, **extra})
    return {"ok": True, "warns": n, **extra}


def warn_remove(db: DB, actor: int, uid: Any, all_: bool = False) -> dict:
    u = require_user(db, uid)
    if all_:
        db.clear_warns(int(u["id"]))
        n = 0
    else:
        n = db.remove_warn(int(u["id"]))
    log(db, actor, "warns:clear" if all_ else "warn:remove", {"user": int(u["id"])})
    return {"ok": True, "warns": n}


def set_rank(db: DB, actor: int, uid: Any, rank: int, owner_ids: set[int] = frozenset()) -> dict:
    u = require_user(db, uid)
    rank = int(rank)
    if rank not in RANK_NAMES:
        raise PanelError("Титул 0..5 (0 — игрок)")
    target_rank = max(int(u["rank"] or 0), 3 if u["is_admin"] else 0)
    if int(u["id"]) in set(int(x) for x in owner_ids):
        raise PanelError("Это владелец бота (OWNER_IDS) — его титул фиксирован")
    if target_rank >= 5:
        raise PanelError("Нельзя понизить владельца — правку вносит только смена OWNER_IDS в коде")
    db.set_rank(int(u["id"]), rank)
    if rank == 0:
        db.ex("UPDATE users SET is_admin=0 WHERE id=?", (int(u["id"]),))   # как «-титул» в боте
    log(db, actor, "rank" if rank else "unrank", {"user": int(u["id"]), "rank": rank, "was": target_rank})
    return {"ok": True, "rank": rank, "rank_name": RANK_NAMES[rank]}


def reset_stats(db: DB, actor: int, uid: Any) -> dict:
    u = require_user(db, uid)
    db.ex("UPDATE users SET games=0, wins=0, total_bet=0, total_won=0, biggest_win=0, spins=0, last_bet=0,"
          " daily_streak=0, refs=0 WHERE id=?", (int(u["id"]),))
    log(db, actor, "reset_stats", {"user": int(u["id"])})
    return {"ok": True}


def wipe_inventory(db: DB, actor: int, uid: Any, only_tier: str = "") -> dict:
    """Изъять все номера игрока (или только одной редкости)."""
    u = require_user(db, uid)
    uid = int(u["id"])
    cond, args = "user_id=?", [uid]
    if only_tier:
        tier = parse_tier(str(only_tier)) or (str(only_tier) if str(only_tier) in TIERS else None)
        if not tier:
            raise PanelError(f"Не знаю такую редкость: <code>{esc_html(only_tier)}</code>")
        cond += " AND tier=?"
        args.append(tier)
    ids = [r["id"] for r in rows(db, f"SELECT id FROM numbers WHERE {cond} LIMIT 2000", tuple(args))]
    if not ids:
        raise PanelError("У игрока нет номеров")
    value = int(db.val(f"SELECT COALESCE(SUM(value),0) FROM numbers WHERE {cond}", tuple(args)))
    marks = ",".join("?" * len(ids))
    with db.tx():
        db.ex(f"INSERT INTO number_log(number_id,full,from_user,to_user,action,created)"
              f" SELECT id, full, user_id, NULL, 'admin_wipe', ? FROM numbers WHERE {cond}",
              (now(),) + tuple(args))
        db.ex(f"DELETE FROM market WHERE number_id IN ({marks})", tuple(ids))
        db.ex(f"DELETE FROM numbers WHERE {cond}", tuple(args))
    log(db, actor, "wipe_inventory", {"user": uid, "count": len(ids), "value": value, "tier": only_tier or "all"})
    return {"ok": True, "removed": len(ids), "value": value}


def give_number(db: DB, actor: int, uid: Any, operator: str, tier: str = "") -> dict:
    u = require_user(db, uid)
    op = db.operator(operator) if operator else None
    if op is None:
        ops = db.operators(only_enabled=False)
        if not ops:
            raise PanelError("Нет ни одного оператора")
        op = ops[0] if len(ops) == 1 else None
        if op is None:
            raise PanelError("Выбери оператора: " + ", ".join(o["code"] for o in ops))
    t = db.country(op["country"])
    tr = parse_tier(tier) if tier else None
    if tier and not tr:
        raise PanelError(f"Не понимаю редкость <code>{esc_html(tier)}</code>")
    if not tr:
        from .numbers import roll_tier
        tr = roll_tier(db.operator_odds(op))
    digits = generate_digits(tr, taken=lambda d: db.number_taken(fmt_number(t["dial"], op["prefix"], d)))
    full = fmt_number(t["dial"], op["prefix"], digits)
    n = db.add_number(int(u["id"]), t["code"], op["code"], full, digits, tr, appraise(digits, tr), "admin")
    log(db, actor, "give_number", {"user": int(u["id"]), "number": full, "tier": tr})
    return {"ok": True, "number": {k: n[k] for k in n.keys()}, "user": int(u["id"])}


def take_number(db: DB, actor: int, number_id: str) -> dict:
    n = db.find_number(number_id)
    if not n:
        raise PanelError(f"Номер <code>{esc_html(number_id)}</code> не найден (нужен #ID или последние цифры)")
    owner = int(n["user_id"])
    db.remove_number(n["id"], "admin_take", actor)
    log(db, actor, "take_number", {"user": owner, "number": n["full"], "id": n["id"]})
    return {"ok": True, "from": owner, "full": n["full"]}


def move_number(db: DB, actor: int, number_id: str, to: Any) -> dict:
    n = db.find_number(number_id)
    if not n:
        raise PanelError("Номер не найден")
    dst = require_user(db, to)
    if int(dst["id"]) == int(n["user_id"]):
        raise PanelError("Номер уже у этого игрока")
    if not db.transfer_number(n["id"], int(n["user_id"]), int(dst["id"]), "admin_transfer"):
        raise PanelError("Не удалось передать номер")
    log(db, actor, "move_number", {"number": n["full"], "from": int(n["user_id"]), "to": int(dst["id"])})
    return {"ok": True, "number": n["full"], "from": int(n["user_id"]), "to": int(dst["id"])}


def revalue_number(db: DB, actor: int, number_id: str, value: Any) -> dict:
    n = db.find_number(number_id)
    if not n:
        raise PanelError("Номер не найден")
    v = max(0, amount(value))
    db.ex("UPDATE numbers SET value=? WHERE id=?", (v, n["id"]))
    db.ex("UPDATE number_log SET action='admin_revalue' WHERE number_id=? AND id=("
          "SELECT MAX(id) FROM number_log WHERE number_id=?)", (n["id"], n["id"]))
    log(db, actor, "revalue_number", {"number": n["full"], "from_value": int(n["value"]), "to_value": v})
    return {"ok": True, "value": v}


def promo_create(db: DB, actor: int, code: str, value: Any, uses: int = 1, days: int = 0) -> dict:
    code = (code or "").strip().upper()
    if len(code) < 3 or len(code) > 24 or any(c.isspace() for c in code):
        raise PanelError("Код: 3–24 символа без пробелов")
    amt = amount(value)
    if amt <= 0:
        raise PanelError("Сумма промокода должна быть больше нуля")
    db.create_promo(code, amt, max(1, int(uses)), max(0, int(days)), actor)
    log(db, actor, "promo_create", {"code": code, "amount": amt, "uses": int(uses), "days": int(days)})
    return {"ok": True, "code": code}


def promo_delete(db: DB, actor: int, code: str) -> dict:
    if not db.delete_promo(code):
        raise PanelError("Такого промокода нет")
    log(db, actor, "promo_delete", {"code": code.upper()})
    return {"ok": True}


def promo_bump(db: DB, actor: int, code: str, uses: int) -> dict:
    p = db.promo(code)
    if not p:
        raise PanelError("Такого промокода нет")
    db.ex("UPDATE promos SET uses_left=? WHERE code=?", (max(0, int(uses)), p["code"]))
    log(db, actor, "promo_uses", {"code": p["code"], "uses_left": int(uses)})
    return {"ok": True, "uses_left": int(uses)}


def set_setting(db: DB, actor: int, key: str, value: Any) -> dict:
    key = (key or "").strip().lower()
    if key not in DEFAULT_SETTINGS:
        raise PanelError("Ключей столько: " + ", ".join(DEFAULT_SETTINGS))
    s = str(value).strip()
    if s.lstrip("-").replace(".", "", 1).isdigit():
        f = float(s)
        val = int(f) if f == int(f) else f
    else:
        raise PanelError("Значение должно быть числом")
    db.set_setting(key, val)
    log(db, actor, "setting", {key: val})
    return {"ok": True, "key": key, "value": val}


def set_section(db: DB, actor: int, section: str, on: bool) -> dict:
    name = (section or "").strip().lower().replace("ё", "е")
    key = SECTIONS.get(name) or (name if name in SECTION_TITLES else None)
    if not key:
        raise PanelError("Разделы: " + ", ".join(SECTIONS))
    db.set_setting(f"section:{key}", 1 if on else 0)
    log(db, actor, "section", {"section": key, "on": on})
    return {"ok": True, "section": key, "on": bool(on)}


def set_maintenance(db: DB, actor: int, on: bool) -> dict:
    db.set_setting("maintenance", 1 if on else 0)
    log(db, actor, "maintenance", on)
    return {"ok": True, "maintenance": bool(on)}


def set_operator(db: DB, actor: int, code: str, patch: dict) -> dict:
    op = db.operator(code)
    if not op:
        raise PanelError(f"Оператор <code>{esc_html(code)}</code> не найден")
    fields, args = [], []
    if "price" in patch:
        fields.append("price=?")
        args.append(max(1, amount(patch["price"])))
    if "name" in patch:
        fields.append("name=?")
        args.append(str(patch["name"])[:32])
    if "emoji" in patch:
        fields.append("emoji=?")
        args.append(str(patch["emoji"])[:8])
    if "prefix" in patch:
        p = "".join(c for c in str(patch["prefix"]) if c.isdigit())
        if not p:
            raise PanelError("Префикс — только цифры")
        fields.append("prefix=?")
        args.append(p)
    if "enabled" in patch:
        fields.append("enabled=?")
        args.append(1 if patch["enabled"] in (1, True, "1", "on", "yes") else 0)
    if "odds" in patch:
        raw = patch["odds"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        odds = {t: float((raw or {}).get(t, 0) or 0) for t in TIERS}
        fields.append("odds=?")
        args.append(_jdump(normalize_odds(odds)))
    if not fields:
        raise PanelError("Нечего сохранять")
    args.append(op["code"])
    db.ex(f"UPDATE operators SET {', '.join(fields)} WHERE code=?", tuple(args))
    log(db, actor, "operator", {"code": op["code"], "patch": {k: patch[k] for k in patch if k != "odds"}})
    return {"ok": True, "operator": operators_by_code(db, op["code"])}


def operators_by_code(db: DB, code: str) -> dict:
    for o in operators(db):
        if o["code"] == code:
            return o
    return {}


def set_country(db: DB, actor: int, code: str, on: bool) -> dict:
    c = db.country(code)
    if not c:
        raise PanelError("Страна не найдена")
    db.ex("UPDATE countries SET enabled=? WHERE code=?", (1 if on else 0, code))
    log(db, actor, "country", {"code": code, "on": on})
    return {"ok": True}


def ban_chat(db: DB, actor: int, chat_id: Any, banned: bool) -> dict:
    chat_id = int(chat_id)
    if db.chat(chat_id) is None:
        raise PanelError(f"Чат <code>{chat_id}</code> не зарегистрирован")
    db.ban_chat(chat_id, banned)
    log(db, actor, "chat:ban" if banned else "chat:allow", {"chat": chat_id})
    return {"ok": True, "chat": chat_id, "banned": 1 if banned else 0}


def game_refund(db: DB, actor: int, game_id: str, mark: str = "refunded") -> dict:
    g = db.game(game_id)
    if not g:
        raise PanelError(f"Игра <code>{esc_html(game_id)}</code> не найдена")
    if g["status"] == "active":
        db.change_balance(int(g["user_id"]), int(g["bet"]), f"panel:refund:{g['id']}", ref=f"panel-refund:{g['id']}")
        db.finish_game(g["id"], mark, int(g["bet"]), "возврат из панели")
        log(db, actor, "refund", {"game": g["id"], "user": int(g["user_id"]), "bet": int(g["bet"])})
        return {"ok": True, "refunded": int(g["bet"]), "user": int(g["user_id"])}
    payout, bet = int(g["payout"]), int(g["bet"])
    delta = bet - payout
    if delta == 0:
        raise PanelError("Става уже возвращена / исход нейтральный")
    if delta > 0:
        db.change_balance(int(g["user_id"]), delta, f"panel:refund:{g['id']}", ref=f"panel-refund:{g['id']}")
    else:
        bal = int(db.balance(int(g["user_id"])))
        db.change_balance(int(g["user_id"]), max(-bal, delta), f"panel:unrefund:{g['id']}",
                          ref=f"panel-unrefund:{g['id']}")
    db.ex("UPDATE games SET status=?, result=? WHERE id=?", (mark, f"изменено из панели (было {g['status']})",
                                                              g["id"]))
    log(db, actor, "game_fix", {"game": g["id"], "user": int(g["user_id"]), "delta": delta})
    return {"ok": True, "refunded": delta, "user": int(g["user_id"])}


def duel_cancel(db: DB, actor: int, duel_id: str) -> dict:
    d = db.duel(duel_id)
    if not d:
        raise PanelError("Дуэль не найдена")
    if d["status"] not in ("open", "invited", "playing"):
        raise PanelError(f"Дуэль уже в статусе {d['status']}")
    for uid in (d["creator"], d["opponent"]):
        if uid:
            try:
                db.change_balance(int(uid), int(d["bet"]), f"panel:duel:{d['id']}",
                                  ref=f"panel-duel:{d['id']}:{uid}")
            except InsufficientFunds:
                pass
    db.set_duel_status(d["id"], "cancelled")
    log(db, actor, "duel_cancel", {"duel": d["id"]})
    return {"ok": True}


def trade_cancel(db: DB, actor: int, trade_id: str) -> dict:
    t = db.trade(trade_id)
    if not t:
        raise PanelError("Обмен не найден")
    if t["status"] != "open":
        raise PanelError(f"Обмен уже {t['status']}")
    db.set_trade_status(t["id"], "cancelled")
    log(db, actor, "trade_cancel", {"trade": t["id"]})
    return {"ok": True}


def market_remove(db: DB, actor: int, number_id: str) -> dict:
    m = db.listing(number_id)
    if not m:
        raise PanelError("Лот не найден")
    db.unlist(m["number_id"])
    log(db, actor, "market_remove", {"number": m["number_id"]})
    return {"ok": True}


def clear_state(db: DB, actor: int, uid: Any) -> dict:
    u = require_user(db, uid)
    db.set_state(int(u["id"]), None)
    log(db, actor, "clear_state", {"user": int(u["id"])})
    return {"ok": True}


def clan_treasury(db: DB, actor: int, clan_id: Any, delta: Any) -> dict:
    cid = int(clan_id)
    c = db.clan(cid)
    if not c:
        raise PanelError("Клан не найден")
    d = amount(delta)
    if d < 0 and int(c["treasury"]) + d < 0:
        raise PanelError("В казне меньше средств")
    db.ex("UPDATE clans SET treasury=treasury+? WHERE id=?", (d, cid))
    db.ex("INSERT INTO clan_log(clan_id,user_id,delta,action,created) VALUES(?,?,?,?,?)",
          (cid, actor, d, "panel", now()))
    log(db, actor, "clan_treasury", {"clan": cid, "delta": d})
    return {"ok": True, "treasury": db.val("SELECT treasury FROM clans WHERE id=?", (cid,))}


def clan_delete(db: DB, actor: int, clan_id: Any) -> dict:
    cid = int(clan_id)
    c = db.clan(cid)
    if not c:
        raise PanelError("Клан не найден")
    db.ex("UPDATE users SET clan_id=NULL, clan_role='' WHERE clan_id=?", (cid,))
    db.ex("DELETE FROM clan_invites WHERE clan_id=?", (cid,))
    db.ex("DELETE FROM clan_log WHERE clan_id=?", (cid,))
    db.ex("DELETE FROM clans WHERE id=?", (cid,))
    log(db, actor, "clan_delete", {"clan": cid, "tag": c["tag"], "treasury": int(c["treasury"])})
    return {"ok": True}


def set_balance_note(db: DB, actor: int, uid: Any, note: str) -> dict:
    """Служебная заметка в аудите (в базе отдельной таблицы под заметки нет)."""
    u = require_user(db, uid)
    note = (note or "").strip()[:400]
    if not note:
        raise PanelError("Пустая заметка")
    log(db, actor, "note", {"user": int(u["id"]), "note": note})
    return {"ok": True}
