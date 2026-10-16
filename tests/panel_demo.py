"""Демо-режим секретной панели: живой HTTP-сервер с фейковым Bot API и наполненной БД.

Запуск:  python tests/panel_demo.py        →  http://127.0.0.1:8098/priora/panel
Переменные: DEMO_PORT, DEMO_DB, ADMIN_PASSWORD, PANEL_PATH (см. simbot/config.py).

Нужно, чтобы интерфейс можно было пощелкать без токена и без реального чата:
бот поднимается по-настоящему (тот же Bot, тот же DB, тот же код панели),
но Bot API фейковый и наружу ничего не уходит. Данные — выдуманные.
"""
import asyncio
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "123:DEMO")
os.environ.setdefault("PORT", os.environ.get("DEMO_PORT", "8098"))   # health/панель слушают этот порт
os.environ["DB_PATH"] = os.environ.get("DEMO_DB", "/tmp/panel_demo.sqlite3")
os.environ.setdefault("ADMIN_IDS", "1")

from simbot.config import Config  # noqa: E402
from simbot.core import Bot  # noqa: E402
from simbot.numbers import TIER, TIERS  # noqa: E402
import simbot.handlers_user  # noqa: E402,F401
import simbot.handlers_spin  # noqa: E402,F401
import simbot.handlers_games  # noqa: E402,F401
import simbot.handlers_duel  # noqa: E402,F401
import simbot.handlers_market  # noqa: E402,F401
import simbot.handlers_clan  # noqa: E402,F401
import simbot.handlers_quiz  # noqa: E402,F401
import simbot.handlers_admin  # noqa: E402,F401

NAMES = ["Артём", "Максим", "Влад", "Никита", "Женя", "Дима", "Кирилл", "Роман", "Тимур", "Аня",
         "Маша", "Даша", "Катя", "Оля", "Соня", "Вера", "Лиза", "Алиса", "Гоша", "Матвей",
         "Егор", "Илья", "Савва", "Пётр", "Глаша", "Тихон", "Марк", "Лея", "Ясон", "Кира"]
NICKS = ["shark", "lucky", "moon", "zero", "volt", "krapivo", "dice", "miner", "upgrader", "duell",
         "crash", "spin", "hold", "neon", "gambit", "pysh", "tomch", "risa", "kash", "flint",
         "orly", "azazel", "vip", "soft", "buhlo", "svin", "krab", "hand", "fish", "bolt"]
KINDS = ["crash", "mines", "slots", "dice", "upgrader", "duel", "spin"]
OPERATORS = ["testo", "vmn", "ruops", "mega"]


class FakeAPI:
    """Как в тестах: методы есть, сети нет."""

    def __init__(self):
        self.calls = []
        self.mid = 1000

    async def call(self, method, _files=None, _timeout=None, _retries=3, **p):
        self.calls.append((method, p))
        if method == "getMe":
            return {"id": 42, "is_bot": True, "first_name": "Priora (демо)", "username": "priora_demo_bot"}
        if method in ("refundStarPayment", "leaveChat"):
            return True
        return {"message_id": p.get("message_id") or self.mid, "chat": {"id": p.get("chat_id")},
                "text": p.get("text", "")}

    async def send(self, chat_id, text, markup=None, **kw):
        self.mid += 1
        return {"message_id": self.mid, "chat": {"id": chat_id}, "text": text}

    async def send_raw(self, chat_id, text, markup=None, **kw):
        return await self.send(chat_id, text, markup, **kw)

    async def edit(self, chat_id, message_id, text, markup=None):
        return {"message_id": message_id, "text": text}

    async def delete(self, chat_id, message_id):
        return True

    async def answer_cb(self, *a, **kw):
        return None

    async def dice(self, chat_id, emoji="🎲", **kw):
        return {"message_id": 1, "dice": {"emoji": emoji, "value": 6}}

    async def photo(self, chat_id, photo, caption="", markup=None, **kw):
        return await self.send(chat_id, caption)

    async def chat_action(self, *a, **kw):
        return None

    async def invoice(self, chat_id, *a, **kw):
        return await self.send(chat_id, "[invoice]")

    async def answer_pre_checkout(self, *a, **kw):
        return None

    async def refund_stars(self, user_id, charge_id):
        return True

    session = None


def fill(db):
    """Демо-данные: игроки, номера, 14 дней ставок, кланы, рынок, промо, платежи."""
    rnd = random.Random(20261016)
    t = int(time.time())
    day = 86400
    uids = []
    for i in range(30):
        uid = 700000000 + i * 137
        name = NAMES[i % len(NAMES)]
        nick = NICKS[i % len(NICKS)] + (str(i) if i >= len(NICKS) else "")
        db.ensure_user({"id": uid, "first_name": name, "username": nick})
        rank = 5 if i == 0 else (4 if i == 3 else (3 if i == 7 else (2 if i in (11, 19) else 0)))
        bet = rnd.randint(3, 200) * rnd.randint(500, 4000)
        won = int(bet * rnd.uniform(0.55, 1.3))
        db.ex("UPDATE users SET created=?, last_seen=?, balance=?, rank=?, is_admin=?, games=?, wins=?,"
              " total_bet=?, total_won=?, spins=?, biggest_win=?, stars_paid=?, last_bet=?, last_daily=?,"
              " daily_streak=?, refs=? WHERE id=?",
              (t - rnd.randint(2, 60) * day, t - rnd.randint(0, 12) * 3600,
               rnd.choice([0, 0, 0, rnd.randint(200, 9000), rnd.randint(10000, 250000),
                           rnd.randint(300000, 1500000)]), rank, 1 if rank >= 3 else 0,
               rnd.randint(0, 340), rnd.randint(0, 120), bet, won, rnd.randint(0, 200),
               int(bet * 0.4), rnd.choice([0, 0, 50, 150, 500, 1500]), rnd.randint(500, 50000),
               t - rnd.randint(0, 3) * day, rnd.randint(0, 9), rnd.randint(0, 14), uid))
        if i in (17, 24):
            db.set_ban(uid, True, rnd.choice(["флуд", "мультиаккаунт", "возвратforce"])[:20])
        if i == 13:
            db.set_mute(uid, t + 3600 * rnd.randint(2, 40))
        uids.append(uid)

    # ── номера в инвентаре
    nums = []
    for uid in uids:
        for _ in range(rnd.randint(0, 5)):
            tier = rnd.choices(TIERS, weights=[42, 24, 17, 10, 5, 2])[0]
            value = int(TIER[tier]["value"] * rnd.uniform(0.4, 4))
            digits = "".join(rnd.choice("03456789") for _ in range(6))
            row = db.add_number(uid, "ru", rnd.choice(OPERATORS), "+7" + digits, digits, tier, value,
                                rnd.choice(["spin", "buy", "give"]))
            nums.append(row["id"])

    # ── лента ставок за 14 дней
    games = 0
    for d in range(14):
        for _ in range(rnd.randint(25, 70)):
            uid = rnd.choice(uids)
            kind = rnd.choice(KINDS)
            bet = rnd.choice([500, 1000, 2000, 3000, 5000, 10000, 25000, 50000])
            when = t - d * day - rnd.randint(0, day - 1)
            result, payout = {}, 0
            if kind == "crash":
                out = round(rnd.uniform(0.28, 9.4), 2)
                result = {"out": out}
                payout = int(bet * out) if out > 1 and rnd.random() < 0.5 else 0
            elif kind == "mines":
                mult = round(rnd.uniform(1, 8), 2)
                result = {"steps": rnd.randint(0, 12), "mult": mult}
                payout = int(bet * mult) if rnd.random() < 0.45 else 0
            elif kind == "dice":
                mult = round(rnd.uniform(1.05, 32), 2)
                result = {"roll": rnd.randint(0, 99), "over": rnd.choice([31, 50, 62]), "mult": mult}
                payout = int(bet * mult) if rnd.random() < 0.4 else 0
            elif kind == "slots":
                result = {"reels": [rnd.randint(0, 7) for _ in range(3)]}
                payout = bet * 7 if rnd.random() < 0.08 else 0
            elif kind == "upgrader":
                a, b = rnd.choice(TIERS), rnd.choice(TIERS)
                result = {"from_tier": a, "to_tier": b, "chance": rnd.randint(3, 80)}
                payout = int(TIER[b]["value"]) if rnd.random() < 0.4 else 0
            elif kind == "duel":
                result = {"rounds": rnd.randint(1, 5), "win": rnd.random() < 0.5}
                payout = bet * 2 if result["win"] else 0
            else:
                result = {"roll": rnd.randint(1, 6)}
                payout = bet * 6 if rnd.random() < 0.15 else 0
            gid = db.create_game(uid, uid, kind, bet, result)   # chat_id NOT NULL: личка = id юзера
            status = "refunded" if rnd.random() < 0.02 else ("won" if payout > bet else "lost")
            db.finish_game(gid, status, payout, str(result))
            db.ex("UPDATE games SET created=?, finished=? WHERE id=?", (when, when + 120, gid))
            games += 1

    # ── живые сессии: их можно остановить и вернуть ставку прямо из панели
    live = []
    for k, uid in enumerate(uids[:3]):
        gid = db.create_game(uid, uid, "crash", 5000 + k * 2500,
                             {"crash_at": round(rnd.uniform(1.2, 6.5), 2), "auto": rnd.choice([0, 2.0, 3.5])})
        live.append(gid)
        db.ex("UPDATE games SET created=?, msg_id=? WHERE id=?", (t - 40 - k * 11, 900 + k, gid))
    gid = db.create_game(uids[3], uids[3], "mines", 3000, {"steps": 3, "mine_at": 17, "mult": 1.94})
    db.ex("UPDATE games SET created=? WHERE id=?", (t - 70, gid))
    live.append(gid)

    # ── чаты
    for cid, title, allowed in ((-1001234567, "Priora · Оффтоп", True),
                                (-1009876543210, "Казино Клуб", True),
                                (-1005550001112, "Слив ставок", False)):
        db.save_chat(cid, title, uids[0], allowed)
        db.ex("UPDATE chats SET created=?, banned=? WHERE id=?", (t - 200 * day, 0 if allowed else 1, cid))

    # ── кланы
    for i, (tag, nm) in enumerate((("AUR", "Aurora"), ("NEB", "Nebula"), ("VLK", "Волки"))):
        db.ex("INSERT INTO clans(tag,name,owner,treasury,open,created) VALUES(?,?,?,?,0,?)",
              (tag, nm, uids[i * 4], rnd.randint(0, 900000), t - 40 * day))
        cid = int(db.val("SELECT id FROM clans WHERE tag=?", (tag,)))
        for j in range(5):
            db.ex("UPDATE users SET clan_id=?, clan_role=? WHERE id=?",
                  (cid, "owner" if j == 0 else "member", uids[(i * 4 + j) % len(uids)]))

    # ── пополнения
    for uid in uids:
        for _ in range(rnd.randint(0, 3)):
            stars = rnd.choice([50, 100, 250, 500, 1000, 2500, 5000])
            chg = f"chg{rnd.randrange(10 ** 6):06d}"
            db.apply_payment(chg, uid, stars, int(stars * 3.5), f"demo:{chg}")
            db.ex("UPDATE payments SET created=? WHERE charge_id=?", (t - rnd.randint(0, 20) * day, chg))
            if rnd.random() < 0.07:
                db.mark_refunded(chg)

    # ── промокоды
    for code, amount, uses in (("NEWYEAR", 5000, 12), ("PANEL", 1000, 3), ("VIP2026", 25000, 1)):
        db.create_promo(code, amount, uses, 30, uids[0])

    # ── рынок
    for nid in rnd.sample(nums, min(7, len(nums))):
        row = db.number(nid)
        if row:
            db.ex("INSERT OR REPLACE INTO market(number_id,seller,price,created) VALUES(?,?,?,?)",
                  (nid, row["user_id"], int(row["value"] * rnd.uniform(1.05, 2.3)),
                   t - rnd.randint(100, 400000)))

    # ── варны и журнал админ-действий
    for uid in rnd.sample(uids, 6):
        db.add_warn(uid, uids[0], rnd.choice(["мат", "спам", "просьба о возврате", "реклама другого бота"]))
    for _ in range(12):
        db.audit(uids[0], rnd.choice(["give", "ban", "setting", "refund", "broadcast"]),
                 {"user": rnd.choice(uids), "amount": rnd.randint(100, 50000)})

    # ── операторы и настройки
    db.ex("UPDATE operators SET prefix='+', price=2000, enabled=1, country='ru' WHERE code='testo'")
    db.set_setting("min_bet", 500)
    return len(uids), len(nums), games, live


def _count(db, sql: str) -> int:
    return int(db.val(sql) or 0)


async def main() -> None:
    cfg = Config()
    bot = Bot(cfg)
    bot.api = FakeAPI()
    bot.me = {"id": 42, "username": "priora_demo_bot"}
    if _count(bot.db, "SELECT COUNT(*) FROM users") < 5:
        fill(bot.db)
    print(f"\n  Демо-панель Priora v{__import__('simbot').VERSION}")
    print(f"  адрес  : http://127.0.0.1:{cfg.health_port}/{cfg.panel_path}/")
    print(f"  пароль : {cfg.panel_password} (переопределяется ADMIN_PASSWORD)")
    print(f"  база   : {cfg.db_path}")
    live = _count(bot.db, "SELECT COUNT(*) FROM games WHERE status='active'")
    print(f"  данных : игроков {_count(bot.db, 'SELECT COUNT(*) FROM users')},"
          f" ставок {_count(bot.db, 'SELECT COUNT(*) FROM games')},"
          f" номеров {_count(bot.db, 'SELECT COUNT(*) FROM numbers')}, живых игр {live}\n")
    await bot._health_server()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nостановлено")
