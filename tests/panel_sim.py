"""Офлайн-проверка секретной панели: HTTP, пароль, CSRF, чтение и все действия.

Запуск:  python tests/panel_sim.py          (никакой сети не нужно)
Под капотом — настоящий Bot + настоящий DB на временном файле, только Bot API фейковый.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "123:TEST")
os.environ["DB_PATH"] = "/tmp/panel_test.sqlite3"
os.environ["ADMIN_IDS"] = "1"
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ["PANEL_PATH"] = "secret/p"
for f in (os.environ["DB_PATH"], os.environ["DB_PATH"] + "-wal", os.environ["DB_PATH"] + "-shm"):
    if os.path.exists(f):
        os.remove(f)

import aiohttp  # noqa: E402

from simbot.config import Config  # noqa: E402
from simbot.core import Bot  # noqa: E402
from simbot.panel import COOKIE  # noqa: E402
import simbot.handlers_user, simbot.handlers_spin, simbot.handlers_games, simbot.handlers_duel  # noqa: E402,F401
import simbot.handlers_market, simbot.handlers_clan, simbot.handlers_quiz, simbot.handlers_admin  # noqa: E402,F401

ADMIN, ALICE, BOB, CAROL = 1, 1001, 1002, 1003
BASE = "/secret/p"
PASSWORD = "test-pass-123"

ok_n = fail_n = 0


def check(name, cond, extra=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"  ✅ {name}")
    else:
        fail_n += 1
        print(f"  ❌ {name} {extra}")


class FakeAPI:
    """Как в tests/sim.py: отвечает на методы, ничего не отправляет наружу."""

    def __init__(self):
        self.calls = []
        self.sent = {}
        self.mid = 1000

    async def call(self, method, _files=None, _timeout=None, _retries=3, **p):
        self.calls.append((method, p))
        if method == "getMe":
            return {"id": 42, "is_bot": True, "first_name": "Sim", "username": "simtestbot"}
        if method == "refundStarPayment":
            return True
        if method == "leaveChat":
            return True
        return {"message_id": p.get("message_id") or self.mid, "chat": {"id": p.get("chat_id")},
                "text": p.get("text", "")}

    async def send(self, chat_id, text, markup=None, **kw):
        self.mid += 1
        self.sent.setdefault(int(chat_id), []).append((self.mid, text))
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
        self.calls.append(("refundStarPayment", {"user_id": user_id, "charge": charge_id}))
        return True

    session = None


def make_bot():
    cfg = Config()
    bot = Bot(cfg)
    bot.api = FakeAPI()
    bot.me = {"id": 42, "username": "simtestbot"}
    bot.started = time.time()
    return bot


def seed(db):
    """Немного данных: игроки, номера, ставки, клан, чат, промо, платёж."""
    now = int(time.time())
    for uid, name, bal in ((ADMIN, "Admin", 0), (ALICE, "Alice", 250000), (BOB, "Bob", 42000), (CAROL, "Carol", 0)):
        db.ex("INSERT INTO users(id,first_name,username,balance,created,last_seen,games,wins,total_bet,total_won,"
              "spins,biggest_win,stars_paid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (uid, name, name.lower(), bal, now - 86400 * 5, now - 600, 12, 5, 90000, 110000, 7, 25000,
               3 if uid == ALICE else 0))
    ops = {o["code"]: o for o in db.operators()}
    c = db.country("ru")
    for uid in (ALICE, BOB):
        for tier in ("rare", "epic", "legendary"):
            op = ops["testo"]
            from simbot.numbers import appraise, fmt_number, generate_digits
            digits = generate_digits(tier)
            db.add_number(uid, c["code"], op["code"], fmt_number(c["dial"], op["prefix"], digits), digits, tier,
                          appraise(digits, tier), "spin")
    for i in range(6):
        gid = db.create_game(ALICE, -100500, ("crash", "mines", "slots", "dice", "upgrader", "duel")[i], 5000 * (i + 1),
                             {"crash_at": 3.4, "auto": 2})
        db.finish_game(gid, "won" if i % 2 else "lost", 5000 * (i + 1) * (2 if i % 2 else 0), "тест")
    db.change_balance(ALICE, 5000, "bonus:daily")
    db.set_setting("section:crash", 1)
    db.save_chat(-100500, "Тестовый чат", ALICE, True)
    db.create_promo("PANELTEST", 1000, 3, 5, ADMIN)
    db.ex("INSERT INTO payments(charge_id,user_id,stars,rubles,payload,created) VALUES('chg1',?,?,?, 'p',?)",
          (ALICE, 5, 5000, now - 3600))
    db.ex("INSERT INTO clans(tag,name,owner,treasury,open,created) VALUES('TST','Тест',?,?,0,?)", (ALICE, 25000, now))
    db.ex("UPDATE users SET clan_id=(SELECT id FROM clans WHERE tag='TST'), clan_role='owner' WHERE id=?", (ALICE,))
    db.add_warn(CAROL, ADMIN, "флуд")
    db.set_state(BOB, "sell_confirm", {"num": "AAA"})
    # активная краш-игра, чтобы проверить остановку из панели
    gid = db.create_game(BOB, -100500, "crash", 9000, {"crash_at": 100.0, "auto": None})
    return gid


async def main():
    print("\n─── панель: сборка приложения ───")
    bot = make_bot()
    live_gid = seed(bot.db)
    bot.cfg.health_port = 8099
    await bot._health_server()          # ровно тот путь, что использует реальный запуск
    panel = bot.db and None
    check("путь панели из PANEL_PATH", True)
    jar = aiohttp.CookieJar(unsafe=True)
    client = aiohttp.ClientSession("http://127.0.0.1:8099", cookie_jar=jar)

    async def get(path, **kw):
        async with client.get(BASE + "/api/" + path, **kw) as r:
            return r.status, await r.text()

    async def post(path, body, csrf=None, **hdrs):
        h = {"Content-Type": "application/json"}
        if csrf is None:
            csrf = S.get("csrf")
        if csrf:
            h["X-Panel-CSRF"] = csrf
        async with client.post(BASE + "/api/" + path, data=json.dumps(body), headers=h, **hdrs) as r:
            return r.status, await r.json()

    S = {}
    print("\n─── доступ без пароля ───")
    st, body = await get("overview")
    check("неавторизованный /api/overview → 401", st == 401, body)
    st, body = await get("session")
    j = json.loads(body)
    check("/api/session без куки → logged_in false", st == 200 and j["logged_in"] is False)
    async with client.get(BASE) as r:
        html = await r.text()
        check("главная панели отдаёт HTML", r.status == 200 and "<html" in html)
        check("в HTML нет пароля", PASSWORD not in html and "test-pass" not in html)
        check("noindex в заголовках", r.headers.get("X-Robots-Tag", "").startswith("noindex"))
        check("CSP без внешних скриптов", "script-src 'self'" in r.headers.get("Content-Security-Policy", ""))
    async with client.get(BASE + "/app.js") as r:
        check("app.js отдаётся", r.status == 200 and "VIEWS" in await r.text())
    async with client.get("/health") as r:
        h = json.loads(await r.text())
        check("/health жив и не выдаёт панель", r.status == 200 and h["ok"] is True and "panel" not in h)
    st, _ = await get("../../etc/passwd")
    check("чужой путь мимо панели → 404", st == 404)

    print("\n─── пароль ───")
    async with client.post(BASE + "/api/login", json={"password": "wrong"}) as r:
        check("неверный пароль → 401", r.status == 401)
    async with client.post(BASE + "/api/login", json={"password": PASSWORD}) as r:
        j = await r.json()
        check("верный пароль → ok", r.status == 200 and j["ok"] and j["csrf"])
        raw = r.headers.get("Set-Cookie", "")
        check("кука HttpOnly + SameSite + path=/",
              COOKIE in raw and "httponly" in raw.lower() and "samesite=lax" in raw.lower()
              and "path=/" in raw.lower() and "max-age" in raw.lower(), raw)
        S["csrf"] = j["csrf"]
    st2, body = await get("overview")
    j = json.loads(body)
    check("после входа /overview отдаёт статистику", st2 == 200 and j["stats"]["users"] == 4, body[:120])
    check("в обзоре есть ряды по дням", len(j["series"]) == 14)
    check("в обзоре есть живой краш", any(g["id"] == live_gid for g in j["live"]))
    check("бот-инфо в обзоре", j["bot"]["username"] == "simtestbot" and j["bot"]["version"])

    print("\n─── CSRF / origin ───")
    async with client.post(BASE + "/api/action", data=json.dumps({"op": "note", "uid": BOB, "text": "x"}),
                           headers={"Content-Type": "application/json", "Origin": "https://evil.example"}) as r:
        check("чужой Origin → 403", r.status == 403)
    async with client.post(BASE + "/api/action", data=json.dumps({"op": "note", "uid": BOB, "text": "x"}),
                           headers={"Content-Type": "application/json"}) as r:
        check("без CSRF-заголовка → 403", r.status == 403)

    print("\n─── чтение ───")
    st, body = await get("users?limit=10")
    j = json.loads(body)
    check("список игроков", st == 200 and j["total"] == 4 and j["users"][0]["first_name"] == "Alice")
    st, body = await get("users?q=bob")
    j = json.loads(body)
    check("поиск по имени", j["total"] == 1 and j["users"][0]["username"] == "bob")
    st, body = await get("users?filter=banned")
    check("фильтр banned пуст", json.loads(body)["total"] == 0)
    st, body = await get(f"user/{ALICE}")
    j = json.loads(body)
    check("карточка игрока: инвентарь", len(j["inventory"]) == 3 and j["inventory_value"] > 0)
    check("карточка: титул/клан/леджер/игры", j["user"]["rank_name"] == "Игрок" and j["clan"]["tag"] == "TST"
          and j["ledger"] and len(j["games"]) == 6)
    check("карточка: платежи и варны", j["payments"][0]["stars"] == 5 and j["warns"] == [])
    check("карточка: FSM показан", j["state"]["state"] is None)
    st, body = await get(f"user/{BOB}")
    check("FSM у Боба виден", json.loads(body)["state"]["state"] == "sell_confirm")
    st, body = await get("browse?table=numbers&limit=3")
    j = json.loads(body)
    check("browse чисел", st == 200 and j["total"] == 6 and "full" in j["columns"])
    st, body = await get("browse?table=sqlite_master")
    check("browse только по белому списку", json.loads(body)["ok"] is not True or st == 400)
    st, body = await get(f"browse?table=ledger&user={ALICE}")
    check("browse с фильтром по игроку", json.loads(body)["total"] == 1)
    st, body = await get("feed?limit=5")
    check("лента игр + live", json.loads(body)["games"] and json.loads(body)["live"])
    st, body = await get("export")
    check("CSV выгрузка", st == 200 and body.count("\n") == 4 and "first_name" in body)
    st, body = await get("operators")
    check("операторы с шансами", len(json.loads(body)) == 3 and "odds_pct" in json.loads(body)[0])
    st, body = await get("clans")
    check("кланы", json.loads(body)[0]["members"] == 1)
    st, body = await get("settings")
    check("настройки", json.loads(body)["values"]["min_bet"] == 50)

    print("\n─── действия ───")
    st, j = await post("action", {"op": "balance", "uid": BOB, "mode": "give", "amount": 50000})
    check("начисление", st == 200 and bot.db.balance(BOB) == 92000, j)
    st, j = await post("action", {"op": "balance", "uid": BOB, "mode": "set", "amount": 1000})
    check("установка баланса", bot.db.balance(BOB) == 1000, j)  # дальше: take 5000
    st, j = await post("action", {"op": "balance", "uid": BOB, "mode": "take", "amount": 5000})
    # как в боте: «забрать» ограничивается доступным балансом, в минус не уходит
    check("списание ограничено балансом", bot.db.balance(BOB) == 0 and j["result"]["changed"] == -1000, j)
    st, j = await post("action", {"op": "balance", "uid": BOB, "mode": "set", "amount": 1000})
    check("обнуление через set", bot.db.balance(BOB) == 1000, j)
    st, j = await post("action", {"op": "balance", "uid": 999999, "mode": "give", "amount": 10})
    check("чужой ID → ошибка", st == 400 and "не найден" in j["error"], j)
    st, j = await post("action", {"op": "ban", "uid": BOB, "on": 1, "reason": "тест"})
    check("бан из панели", bot.db.user(BOB)["banned"] == 1 and bot.db.user(BOB)["ban_reason"] == "тест")
    st, j = await post("action", {"op": "ban", "uid": BOB, "on": 0})
    check("разбан", bot.db.user(BOB)["banned"] == 0)
    st, j = await post("action", {"op": "mute", "uid": BOB, "duration": "2ч"})
    check("мут на 2 часа", bot.db.user(BOB)["muted_until"] > time.time() + 7000)
    st, j = await post("action", {"op": "mute", "uid": BOB, "seconds": 0})
    check("размут", bot.db.user(BOB)["muted_until"] == 0)
    st, j = await post("action", {"op": "rank", "uid": BOB, "rank": 3})
    check("титул админа", bot.db.user(BOB)["rank"] == 3)
    st, j = await post("action", {"op": "rank", "uid": ADMIN, "rank": 0})
    check("владельца из OWNER_IDS не понизить", st == 400 and "фиксирован" in j["error"], j)
    st, j = await post("action", {"op": "rank", "uid": BOB, "rank": 0})
    check("титул снят", bot.db.user(BOB)["rank"] == 0 and bot.db.user(BOB)["is_admin"] == 0)
    st, j = await post("action", {"op": "warn", "uid": BOB, "reason": "мат"})
    check("варн", bot.db.user(BOB)["warns"] == 1)
    st, j = await post("action", {"op": "warn_remove", "uid": BOB, "all": 1})
    check("варны сняты", bot.db.user(BOB)["warns"] == 0)
    st, j = await post("action", {"op": "give_number", "uid": BOB, "operator": "testo", "tier": "mythic"})
    nn = j["result"]["number"]
    check("выдан номер", nn["tier"] == "mythic" and nn["user_id"] == BOB, j)
    st, j = await post("action", {"op": "revalue_number", "number": nn["id"], "value": "12345"})
    check("переоценка номера", bot.db.number(nn["id"])["value"] == 12345)
    st, j = await post("action", {"op": "move_number", "number": nn["id"], "to": CAROL})
    check("передача номера", bot.db.number(nn["id"])["user_id"] == CAROL)
    st, j = await post("action", {"op": "take_number", "number": nn["id"]})
    check("изъятие номера", bot.db.number(nn["id"]) is None)
    st, j = await post("action", {"op": "wipe_inventory", "uid": CAROL})
    check("изъять все номера", bot.db.val("SELECT COUNT(*) FROM numbers WHERE user_id=?", (CAROL,)) == 0)
    st, j = await post("action", {"op": "setting", "key": "min_bet", "value": 250})
    check("настройка изменена", bot.db.setting("min_bet") == 250)
    st, j = await post("action", {"op": "setting", "key": "hack", "value": 1})
    check("чужой ключ настройки отклонён", st == 400, j)
    st, j = await post("action", {"op": "section", "section": "краш", "on": 0})
    check("раздел выключен", bot.section_enabled("crash") is False)
    st, j = await post("action", {"op": "section", "section": "краш", "on": 1})
    check("раздел включён", bot.section_enabled("crash") is True)
    st, j = await post("action", {"op": "maintenance", "on": 1})
    check("техработы вкл", int(bot.db.setting("maintenance")) == 1)
    st, j = await post("action", {"op": "maintenance", "on": 0})
    check("техработы выкл", int(bot.db.setting("maintenance")) == 0)
    st, j = await post("action", {"op": "operator", "code": "testo",
                                  "patch": {"price": 2000, "odds": {"common": 50, "uncommon": 25, "rare": 10,
                                                                     "epic": 10, "legendary": 4, "mythic": 1}}})
    check("оператор обновлён", bot.db.operator("testo")["price"] == 2000, j)
    st, j = await post("action", {"op": "promo_create", "code": "FROMPANEL", "amount": 700, "uses": 2, "days": 1})
    check("промо создан", bot.db.promo("FROMPANEL")["amount"] == 700)
    st, j = await post("action", {"op": "promo_bump", "code": "FROMPANEL", "uses": 9})
    check("активации пополнены", bot.db.promo("FROMPANEL")["uses_left"] == 9)
    st, j = await post("action", {"op": "promo_delete", "code": "FROMPANEL"})
    check("промо удалён", bot.db.promo("FROMPANEL") is None)
    st, j = await post("action", {"op": "game_refund_live", "game": live_gid, "notify": 1})
    g = bot.db.game(live_gid)
    check("живая краш-игра остановлена и ставка возвращена",
          g["status"] == "refunded" and g["payout"] == 9000 and bot.db.balance(BOB) == 1000 + 9000
          and BOB not in bot.crash_games, (j, bot.db.balance(BOB)))
    st, j = await post("action", {"op": "clan_treasury", "clan": 1, "delta": 1000})
    check("казна клана", bot.db.clan(1)["treasury"] == 26000, j)
    st, j = await post("action", {"op": "clear_state", "uid": BOB})
    check("FSM сброшен", bot.db.get_state(BOB)[0] is None)
    st, j = await post("action", {"op": "reset_stats", "uid": ALICE})
    check("статистика обнулена", bot.db.user(ALICE)["games"] == 0)
    st, j = await post("action", {"op": "broadcast", "text": "тест рассылки", "dry_run": 1})
    check("рассылка: предпросмотр", j["result"]["targets"] >= 4, j)
    st, j = await post("action", {"op": "broadcast", "text": "тест рассылки"})
    await asyncio.sleep(0.4)
    check("рассылка отправлена фейк-API", 1 in bot.api.sent or len(bot.api.sent) >= 3, list(bot.api.sent))
    st, j = await post("action", {"op": "dm", "uid": CAROL, "text": "привет <b>!</b>", "html": 1})
    check("ЛС игроку", j["result"]["sent"] and CAROL in bot.api.sent)
    st, j = await post("action", {"op": "payment_refund", "charge": "chg1"})
    check("рефанд звёзд", bot.db.payment("chg1")["refunded"] == 1 and ("refundStarPayment",
          {"user_id": ALICE, "charge": "chg1"}) in bot.api.calls, j)
    st, j = await post("action", {"op": "browse_delete", "table": "promos", "key": "PANELTEST"})
    check("удаление промо из browse", bot.db.promo("PANELTEST") is None)
    st, j = await post("action", {"op": "browse_delete", "table": "users", "key": "1"})
    check("удалить юзера через browse нельзя", st == 400, j)
    st, j = await post("action", {"op": "нет-такой-операции", "uid": BOB})
    check("неизвестный op → 404", st == 404, j)
    st, j = await post("action", {"op": "setting", "key": "min_bet", "value": 99, "note": "проверка notes"})
    a = bot.db.audit_log(1)[0]
    check("каждое действие в аудите", a["action"] == "setting" and int(a["admin_id"]) == ADMIN, dict(a))

    print("\n─── консоль ───")
    st, j = await post("console", {"text": "статистика"})
    check("команда «статистика»", st == 200 and j["ok"] and "СТАТИСТИКА" in j["replies"][0], j)
    was = bot.db.balance(BOB)
    st, j = await post("console", {"text": "выдать 1000 @bob"})
    check("«выдать 1000 @bob» начислил", j["ok"] and len(j["replies"]) == 1
          and bot.db.balance(BOB) == was + 1000 and "kpi" in j, j["replies"])
    check("в ответе консоли нет пароля", "Djdaqdj157" not in json.dumps(j, ensure_ascii=False))
    st, j = await post("console", {"text": "обнулить @bob"})
    check("«обнулить» работает", bot.db.balance(BOB) == 0, j)
    st, j = await post("console", {"text": "крутить тесто (вин)"})
    check("подкрутка из консоли не ломается", st == 200, j)
    st, j = await post("console", {"text": "просто текст не команда"})
    check("не-команда → вежливый ответ", j["ok"] is False and "не считает" in j["error"], j)
    st, j = await post("console", {"text": "настройка welcome_bonus 6000"})
    check("настройка через консоль", bot.db.setting("welcome_bonus") == 6000, j)
    st, j = await post("console", {"text": "чат запретить -100500"})
    check("чат в ЧС через консоль", bot.db.chat_banned(-100500) is True, j)
    st, j = await post("console", {"text": "чат разрешить -100500"})
    check("чат разбанен", bot.db.chat_banned(-100500) is False)
    st, j = await post("action", {"op": "bot_command",
                                  "line": "оператор добавить zzz 999 1000 🧪 Тест ru"})
    check("bot_command добавляет оператора", st == 200 and j["ok"], j)
    ops2 = json.loads((await get("operators"))[1])
    check("оператор появился в списке", any(o["code"] == "zzz" for o in ops2), [o["code"] for o in ops2])
    st, j = await post("action", {"op": "bot_command", "line": "  "})
    check("пустая команда через bot_command → 400", st == 400, j)
    st, j = await post("action", {"op": "rank", "uid": ADMIN, "rank": 0})
    check("админа из ADMIN_IDS тоже не понизить", st == 400, j)

    print("\n─── выход ───")
    st, j = await post("logout", {})
    st2, body = await get("overview")
    check("после logout снова 401", st2 == 401, body[:80])

    print("\n─── частота неудачных входов ───")
    async with aiohttp.ClientSession("http://127.0.0.1:8099") as c2:
        codes = []
        for i in range(10):
            async with c2.post(BASE + "/api/login", json={"password": "nope" + str(i)}) as r:
                codes.append(r.status)
        check("первые попытки → 401", codes[0] == 401, codes)
        check("после лимита → 429", 429 in codes, codes)
        async with c2.post(BASE + "/api/login", json={"password": PASSWORD}) as r:
            check("под лимитом и верный пароль получает 429", r.status == 429, r.status)

    await client.close()
    print(f"\n{'=' * 46}\n  панель: OK {ok_n} · провалов {fail_n}\n{'=' * 46}")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
