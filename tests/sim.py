"""Офлайн-симуляция v2: фейковый Bot API, личка + групповой чат, все текстовые сценарии."""
import asyncio
import itertools
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "123:TEST")
os.environ["DB_PATH"] = "/tmp/simbot_test.sqlite3"
os.environ["ADMIN_IDS"] = "1"
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import simbot.config as config
config.CRASH_TICK = 0.05
import simbot.handlers_games as hg
hg.CRASH_TICK = 0.05
hg.CRASH_K = 0.1386 * 20          # один тик симуляции = одна игровая секунда

from simbot.config import Config
from simbot.core import Bot
import simbot.handlers_user, simbot.handlers_spin, simbot.handlers_games, simbot.handlers_duel  # noqa
import simbot.handlers_market, simbot.handlers_clan, simbot.handlers_quiz, simbot.handlers_admin  # noqa

ADMIN, ALICE, BOB, CAROL = 1, 1001, 1002, 1003
GROUP, BADGROUP = -100500, -100999
NAMES = {ADMIN: "Admin", ALICE: "Alice", BOB: "Bob", CAROL: "Carol"}


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.msg_id = itertools.count(100)
        self.sent = {}   # chat_id -> list of (message_id, text)
        self.dice_values = []
        self.deleted = []
        self.left = []

    async def start(self): pass
    async def close(self): pass

    async def call(self, method, _files=None, _timeout=None, _retries=3, **p):
        self.calls.append((method, p))
        if method == "getMe":
            return {"id": 42, "is_bot": True, "first_name": "Sim", "username": "simtestbot"}
        if method in ("sendMessage", "sendPhoto", "sendInvoice"):
            mid = next(self.msg_id)
            self.sent.setdefault(p["chat_id"], []).append((mid, p.get("text") or p.get("caption") or "[invoice]"))
            res = {"message_id": mid, "chat": {"id": p["chat_id"]}, "date": int(time.time()), "text": p.get("text", "")}
            if method == "sendPhoto":
                res["photo"] = [{"file_id": "F1", "width": 1, "height": 1}]
            return res
        if method == "editMessageText":
            self.sent.setdefault(p["chat_id"], []).append((p["message_id"], p["text"]))
            return {"message_id": p["message_id"], "chat": {"id": p["chat_id"]}, "text": p["text"]}
        if method == "sendDice":
            mid = next(self.msg_id)
            v = self.dice_values.pop(0) if self.dice_values else (64 if p.get("emoji") == "🎰" else 6)
            return {"message_id": mid, "dice": {"emoji": p.get("emoji"), "value": v}}
        if method == "deleteMessage":
            self.deleted.append((p["chat_id"], p["message_id"]))
            return True
        if method == "leaveChat":
            self.left.append(p["chat_id"])
            return True
        return True

    from simbot.api import API as _A
    send = _A.send
    send_raw = _A.send_raw
    edit = _A.edit
    delete = _A.delete
    answer_cb = _A.answer_cb
    dice = _A.dice
    photo = _A.photo
    chat_action = _A.chat_action
    invoice = _A.invoice
    answer_pre_checkout = _A.answer_pre_checkout
    refund_stars = _A.refund_stars

    def last_text(self, chat_id):
        return self.sent.get(chat_id, [])[-1][1]

    def texts(self, chat_id, n=5):
        return [t for _, t in self.sent.get(chat_id, [])[-n:]]


counter = itertools.count(1)


def user(uid):
    return {"id": uid, "is_bot": False, "first_name": NAMES.get(uid, "U"), "username": NAMES.get(uid, "u").lower()}


def msg_update(uid, text, chat=None, reply_to=None):
    chat_id = chat if chat is not None else uid
    m = {"message_id": next(counter), "date": int(time.time()), "text": text,
         "chat": {"id": chat_id, "type": "private" if chat_id > 0 else "supergroup", "title": "Test group"},
         "from": user(uid)}
    if reply_to:
        m["reply_to_message"] = {"message_id": 5, "from": user(reply_to), "text": "hi"}
    return {"update_id": next(counter), "message": m}


async def main():
    bot = Bot(Config())
    api = FakeAPI()
    bot.api = api
    bot.me = {"username": "simtestbot", "id": 42}
    db = bot.db
    errors = []

    async def send(uid, text, chat=None, reply_to=None):
        await bot._handle(msg_update(uid, text, chat, reply_to))
        await asyncio.sleep(0)

    async def g(uid, text, reply_to=None):
        await send(uid, text, GROUP, reply_to)

    def check(cond, what):
        if not cond:
            errors.append(what)
            print("  ✗", what)
        else:
            print("  ✓", what)

    def last(chat):
        return api.last_text(chat)

    def bal(uid):
        return db.balance(uid)

    # ───── старт, приватные команды ─────
    print("• Старт и справка")
    for u in (ADMIN, ALICE, BOB, CAROL):
        await send(u, "/start")
    check("Добро пожаловать" in last(ALICE) and bal(ALICE) == 5000, "приветствие + стартовый бонус")
    await send(ALICE, "помощь")
    check("краш 500" in last(ALICE), "помощь")
    await send(ALICE, "правила")
    check("casinobot, teslabot" in last(ALICE), "правила содержат обязательный текст")
    await send(ALICE, "баланс")
    check("5" in last(ALICE) and "₽" in last(ALICE), "баланс")
    await send(ALICE, "профиль")
    check("Alice" in last(ALICE) and "Игрок" in last(ALICE), "профиль")
    await send(ALICE, "абракадабра")
    check("Не понял" in last(ALICE), "подсказка на неизвестный текст в личке")
    await send(ALICE, "бонус")
    check("+" in last(ALICE) and bal(ALICE) > 5000, "бонус начислен")
    await send(ALICE, "бонус")
    check("уже забран" in last(ALICE), "повторный бонус отклонён")

    # ───── групповой чат: открыт для всех, чёрный список владельца ─────
    print("• Чаты")

    def joined(chat, by):
        return {"update_id": next(counter), "my_chat_member": {
            "chat": {"id": chat, "type": "supergroup", "title": "Group %s" % chat}, "from": user(by), "date": 1,
            "old_chat_member": {"status": "left"}, "new_chat_member": {"status": "member", "user": {"id": 42}}}}

    await g(ALICE, "баланс")
    check(GROUP not in api.left and "₽" in last(GROUP) and db.chat_allowed(GROUP),
          "неизвестный чат: бот отвечает сразу и регистрирует чат")
    n_before = len(api.sent.get(GROUP, []))
    await bot._handle({"update_id": next(counter), "message": {
        "message_id": next(counter), "date": 1, "chat": {"id": GROUP, "type": "supergroup", "title": "Test group"},
        "from": user(ALICE), "new_chat_members": [{"id": 42, "is_bot": True, "first_name": "Sim"}]}})
    check(len(api.sent.get(GROUP, [])) == n_before, "повторное добавление в активный чат — без второго приветствия")
    await bot._handle(joined(BADGROUP, BOB))
    check(db.chat_allowed(BADGROUP) and BADGROUP not in api.left and "СИМ-Рулетка в чате" in last(BADGROUP),
          "обычный игрок добавил бота → приветствие, бот остался")
    await send(BOB, "баланс", BADGROUP)
    check("₽" in last(BADGROUP), "в новом чате команды работают")
    await send(ALICE, f"чат запретить {BADGROUP}")
    check(not db.chat_banned(BADGROUP), "игрок не может запрещать чаты")
    await send(ADMIN, f"чат запретить {BADGROUP}")
    check(db.chat_banned(BADGROUP) and not db.chat_allowed(BADGROUP) and BADGROUP in api.left,
          "владелец: чат запретить → чёрный список, бот вышел")
    api.left.clear()
    await bot._handle(joined(BADGROUP, BOB))
    check(BADGROUP in api.left and not db.chat_allowed(BADGROUP), "в запрещённый чат бот не добавляется")
    n_before = len(api.sent.get(BADGROUP, []))
    await send(BOB, "краш 100", BADGROUP)
    await send(BOB, "краш 100", BADGROUP)
    check(len(api.sent.get(BADGROUP, [])) == n_before and "заблокирован" in last(BADGROUP),
          "сообщения из запрещённого чата игнорируются без спама")
    await send(ADMIN, f"чат разрешить {BADGROUP}")
    check(not db.chat_banned(BADGROUP), "чат разрешить → убран из чёрного списка")
    api.left.clear()
    await bot._handle(joined(BADGROUP, CAROL))
    check(db.chat_allowed(BADGROUP) and BADGROUP not in api.left and "в чате" in last(BADGROUP),
          "после разблокировки бота снова можно добавить")
    await send(ADMIN, "чаты")
    check("ЧАТЫ" in last(ADMIN) and str(GROUP) in last(ADMIN) and str(BADGROUP) in last(ADMIN), "список чатов")
    check(ADMIN in bot.cfg.admin_ids and bot.rank(ADMIN) == 5, "владелец из кода/конфига имеет ранг 5")

    # ───── игры в группе ─────
    print("• Краш")
    api.sent[GROUP] = []
    b0 = bal(ALICE)
    await g(ALICE, "краш 500 1.5")
    check(bal(ALICE) == b0 - 500 and "🚀" in last(GROUP), "краш стартовал (авто ×1.5)")
    await asyncio.sleep(0.9)
    check(ALICE not in bot.crash_games, "краш завершился")
    check("КРАШ" in last(GROUP) or "авто-вывод" in last(GROUP), "итог краша в чате")
    b0 = bal(ADMIN)
    await g(ADMIN, "краш 500 (вин)")
    await asyncio.sleep(0.25)
    await g(ADMIN, "забрать")
    check(ADMIN not in bot.crash_games, "забрать текстом работает")
    txt = last(GROUP)
    check("забрал" in txt and bal(ADMIN) > b0 - 500, "результат после «забрать»")
    # подкрутка
    b0 = bal(ADMIN)
    await g(ADMIN, "краш 1000 2 (луз)")
    await asyncio.sleep(0.3)
    check("КРАШ ×1.00" in last(GROUP) and bal(ADMIN) == b0 - 1000, "краш (луз) → мгновенный краш")
    b0 = bal(ADMIN)
    await g(ADMIN, "краш 1000 1.5 (вин)")
    await asyncio.sleep(0.5)
    check("авто-вывод" in last(GROUP) and bal(ADMIN) > b0, "краш (вин) → выигрыш")
    b0 = bal(BOB)
    await g(BOB, "краш 1000 1.5 (вин)")
    await asyncio.sleep(0.6)
    check("(вин)" not in last(GROUP) and ("КРАШ" in last(GROUP) or "авто-вывод" in last(GROUP)), "не-админ: скобки игнорируются")

    print("• Мины")
    b0 = bal(ALICE)
    await g(ALICE, "мины 500 3")
    check(bal(ALICE) == b0 - 500 and "💣" in last(GROUP) and "25" in last(GROUP), "мины: поле")
    gm = db.active_game(ALICE, "mines")
    st = db.game_state(gm)
    safe = [i + 1 for i in range(25) if i not in st["mines"]]
    await g(ALICE, str(safe[0]))
    check("открыто 1/22" in last(GROUP), "клетка просто числом")
    await g(ALICE, f"откр {safe[1]} {safe[2]}")
    check("открыто 3/22" in last(GROUP), "несколько клеток за раз")
    await g(ALICE, "забрать")
    check("забрал" in last(GROUP) and bal(ALICE) > b0 - 500, "мины: забрать")
    check(db.active_game(ALICE, "mines") is None, "мины завершены")
    b0 = bal(ALICE)
    await g(ALICE, "мины 500 3")
    gm = db.active_game(ALICE, "mines")
    st = db.game_state(gm)
    await g(ALICE, str(st["mines"][0] + 1))
    check("БАБАХ" in last(GROUP) and bal(ALICE) == b0 - 500, "мины: взрыв")
    await g(ALICE, "мины 500 3")
    await g(ALICE, "отмена")
    check("возвращены" in last(GROUP) and bal(ALICE) == b0 - 500, "мины: отмена до первого хода")
    # подкрутка мин
    await g(ADMIN, "мины 500 24 (вин)")
    gm = db.active_game(ADMIN, "mines")
    st = db.game_state(gm)
    await g(ADMIN, str(st["mines"][0] + 1))
    check("🏁" in last(GROUP) or "открыто 1/1" in last(GROUP), "мины (вин): мина убрана с клетки")
    check(db.active_game(ADMIN, "mines") is None, "мины (вин) с 24 минами → всё поле открыто")
    await g(ADMIN, "мины 500 1 (луз)")
    gm = db.active_game(ADMIN, "mines")
    st = db.game_state(gm)
    safe = [i + 1 for i in range(25) if i not in st["mines"]]
    await g(ADMIN, str(safe[0]))
    check("БАБАХ" in last(GROUP), "мины (луз): безопасная клетка стала миной")

    print("• Слоты и кубик")
    b0 = bal(ALICE)
    api.dice_values = [64]
    await g(ALICE, "слоты 100")
    check("ДЖЕКПОТ" in last(GROUP) and bal(ALICE) == b0 + 2400, "слоты: 777 ×25")
    api.dice_values = [2]
    await g(ALICE, "слоты 100")
    check("Мимо" in last(GROUP), "слоты: мимо")
    b0 = bal(ADMIN)
    await g(ADMIN, "слоты 100 (вин)")
    check(bal(ADMIN) > b0 and "+" in last(GROUP), "слоты (вин)")
    await g(ADMIN, "слоты 100 (луз)")
    check("Мимо" in last(GROUP), "слоты (луз)")
    b0 = bal(ALICE)
    api.dice_values = [4]
    await g(ALICE, "кубик 100 чёт")
    check("Угадал" in last(GROUP) and bal(ALICE) == b0 + 90, "кубик чёт ×1.9")
    api.dice_values = [3]
    await g(ALICE, "кубик 100 3")
    check("×5.5" in last(GROUP), "кубик точное число ×5.5")
    await g(ADMIN, "кубик 100 нечет (луз)")
    check("Мимо" in last(GROUP), "кубик (луз)")
    await g(ADMIN, "кубик 100 5 (вин)")
    check("Угадал" in last(GROUP), "кубик (вин)")
    await g(ALICE, "кубик 10")
    check("Минимальная ставка" in last(GROUP), "минимальная ставка")
    await g(ALICE, "краш 99999999")
    check("Максимальная ставка" in last(GROUP) or "Не хватает" in last(GROUP), "лимит ставки")

    print("• Апгрейд")
    b0 = bal(ADMIN)
    await g(ADMIN, "ап 100 2 (вин)")
    check("УДАЛОСЬ" in last(GROUP) and bal(ADMIN) == b0 + 100, "ап (вин)")
    await g(ADMIN, "ап 100 2 (луз)")
    check("Мимо" in last(GROUP), "ап (луз)")
    await g(ALICE, "ап 100 7")
    check("Множитель" in last(GROUP), "ап: неверный множитель")

    print("• Прокрутка и номера")
    await g(ALICE, "операторы")
    check("Сберище" in last(GROUP) and "5" in last(GROUP), "операторы")
    db.set_balance(ALICE, 50_000, "test")
    await g(ALICE, "крутить пивкофф")
    check("+7 902" in last(GROUP) and db.numbers_count(ALICE) == 1, "крутить 1")
    await g(ALICE, "крутить тесто 3")
    check(db.numbers_count(ALICE) == 4 and "×3" in last(GROUP), "крутить ×3")
    await g(ALICE, "крутить")
    check("ОПЕРАТОРЫ" in last(GROUP), "крутить без оператора → список")
    # режимы прокрута: цена ×1.5 / ×2 / ×3.5
    from simbot.handlers_spin import mode_odds, mode_price
    testo = db.operator("testo") if db.operator("testo") else db.operators()[1]
    b0 = bal(ALICE)
    await g(ALICE, "крутить тесто вип")
    check("Вип" in last(GROUP) and bal(ALICE) == b0 - mode_price(testo, "vip") and mode_price(testo, "vip") == int(testo["price"]) * 3 // 2, "режим вип: цена ×1.5")
    b0 = bal(ALICE)
    await g(ALICE, "крутить элит пивкофф 2")
    check(bal(ALICE) == b0 - 2 * mode_price(db.operators()[0], "elite") and "Элит" in last(GROUP), "режим элит ×2 спина: цена ×3.5")
    b0 = bal(ALICE)
    await g(ALICE, "крутить премиум сберище")
    check(bal(ALICE) == b0 - mode_price(db.operator("sberishche"), "prem") and "Премиум" in last(GROUP), "режим премиум по префиксу: цена ×2")
    odds = {"common": 60, "uncommon": 25, "rare": 10, "epic": 4, "legendary": 0.9, "mythic": 0.1}
    ev = lambda o: sum(o[t] * (1 if True else 0) for t in o) and 0
    std_r = mode_odds(odds, "std")["rare"] / sum(mode_odds(odds, "std").values())
    el_r = mode_odds(odds, "elite")["rare"] / sum(mode_odds(odds, "elite").values())
    check(el_r > std_r and mode_odds(odds, "std")["common"] == 60, "элит поднимает шансы редких")
    await g(ALICE, "крутить хз")
    check("не найден" in last(GROUP), "неизвестный оператор с режимом")
    await g(ALICE, "операторы")
    check("РЕЖИМЫ" in last(GROUP) and "Элит" in last(GROUP), "операторы показывают режимы")
    await g(ALICE, "номера")
    check("#" in last(GROUP) and str(db.numbers_count(ALICE)) in last(GROUP), "номера с ID")
    n = db.user_numbers(ALICE)[0]
    await g(ALICE, f"номер #{n['id']}")
    check(n["full"] in last(GROUP) and "Владелец" in last(GROUP), "карточка номера")
    await g(ALICE, f"передать #{n['id']} @bob")
    check(db.number(n["id"])["user_id"] == BOB and "передал" in last(GROUP), "передать по @нику")
    n2 = db.user_numbers(ALICE)[0]
    await g(ALICE, f"передать #{n2['id']}", reply_to=CAROL)
    check(db.number(n2["id"])["user_id"] == CAROL, "передать ответом на сообщение")
    # продать все
    cnt = db.numbers_count(CAROL)
    expected = sum(int(x["value"]) for x in db.user_numbers(CAROL))
    c0 = bal(CAROL)
    await g(CAROL, "продать все")
    check(cnt > 0 and "Подтверди" in last(GROUP) and db.numbers_count(CAROL) == cnt and bal(CAROL) == c0, "продать все: превью без продажи")
    await g(CAROL, "продать все да")
    check(db.numbers_count(CAROL) == 0 and bal(CAROL) == c0 + expected and "продал" in last(GROUP), "продать все да: весь инвентарь боту")
    await g(CAROL, "продать все да")
    check("Нечего продавать" in last(GROUP), "продать все при пустом инвентаре")
    db.set_balance(CAROL, 0, "test")
    await g(BOB, f"передать #{n2['id']} @alice")
    check("не твой" in last(GROUP), "чужой номер нельзя передать")
    await g(BOB, f"передать {n['full']} @alice")
    check(db.number(n["id"])["user_id"] == ALICE, "передать по полному номеру")
    n3 = db.user_numbers(ALICE)[0]
    b0 = bal(ALICE)
    await g(ALICE, f"продать #{n3['id']}")
    check("Подтверди" in last(GROUP) and bal(ALICE) == b0, "продажа требует подтверждения")
    await g(ALICE, f"продать #{n3['id']} да")
    check(bal(ALICE) == b0 + n3["value"], "продажа выполнена")
    n4 = db.user_numbers(ALICE)[0]
    await g(ALICE, f"ап #{n4['id']}")
    check("шанс" in last(GROUP).lower() or "%" in last(GROUP), "ап номера: шансы")
    await g(ADMIN, "выдать номер @alice сберище миф")
    check(db.user_numbers(ALICE)[0]["tier"] == "mythic", "выдать мифический номер")
    nm = db.user_numbers(ALICE)[0]
    await g(ADMIN, f"забрать номер #{nm['id']}")
    check(db.number(nm["id"]) is None, "забрать номер")
    await g(ADMIN, f"ап #{n4['id']} эпик (вин)", reply_to=None)
    check("не найден" in last(GROUP).lower(), "ап чужого номера запрещён")
    await g(ADMIN, "выдать номер @admin тесто обычный")
    na = db.user_numbers(ADMIN)[0]
    await g(ADMIN, f"ап #{na['id']} редкий (вин)")
    check("АПГРЕЙД УДАЛСЯ" in last(GROUP) and db.user_numbers(ADMIN)[0]["tier"] == "rare", "ап номера (вин)")

    print("• Инвентарь, рынок, обмен")
    db.set_balance(BOB, 50_000, "test")
    await g(ALICE, "инвентарь")
    check("ИНВЕНТАРЬ" in last(GROUP) and "#" in last(GROUP), "инвентарь")
    na = db.user_numbers(ALICE)[0]
    await g(ALICE, f"продать #{na['id']} 3000")
    check(db.listing(na["id"]) is not None and "выставил на рынок" in last(GROUP), "лот на рынке")
    await g(ALICE, "инвентарь")
    check("🏷" in last(GROUP), "лот помечен в инвентаре")
    await g(BOB, "рынок")
    check(na["full"] in last(GROUP) and "3" in last(GROUP), "рынок показывает лот")
    await g(ALICE, f"купить #{na['id']}")
    check("собственный лот" in last(GROUP), "свой лот купить нельзя")
    b_bob, b_alice = bal(BOB), bal(ALICE)
    await g(BOB, f"купить #{na['id']}")
    check(db.number(na["id"])["user_id"] == BOB and bal(BOB) == b_bob - 3000 and bal(ALICE) == b_alice + 2850,
          "покупка: номер перешёл, продавец получил 95%")
    check(db.listing(na["id"]) is None, "лот снят после покупки")
    nb_ = db.user_numbers(BOB)[0]
    await g(BOB, f"продать #{nb_['id']} 60000 @carol")
    check(db.listing(nb_["id"])["buyer"] == CAROL and "предлагает" in last(GROUP), "адресное предложение")
    await g(ALICE, f"купить #{nb_['id']}")
    check("адресное предложение" in last(GROUP), "чужой не может купить адресный лот")
    db.set_balance(CAROL, 70_000, "test")
    await g(CAROL, f"купить #{nb_['id']}")
    check("Подтверди" in last(GROUP) and db.number(nb_["id"])["user_id"] == BOB, "дорогая покупка требует подтверждения")
    await g(CAROL, f"купить #{nb_['id']} да")
    check(db.number(nb_["id"])["user_id"] == CAROL and bal(CAROL) == 10_000, "адресная покупка выполнена")
    nb2 = db.user_numbers(BOB)[0] if db.numbers_count(BOB) else None
    if nb2 is None:
        await g(ADMIN, "выдать номер @bob тесто")
        nb2 = db.user_numbers(BOB)[0]
    await g(BOB, f"продать #{nb2['id']} 100")
    await g(BOB, "лоты")
    check(nb2["full"] in last(GROUP), "мои лоты")
    await g(BOB, f"снять #{nb2['id']}")
    check(db.listing(nb2["id"]) is None and "снят с продажи" in last(GROUP), "снять лот")
    await g(BOB, f"продать #{nb2['id']} 100")
    await g(BOB, f"передать #{nb2['id']} @alice")
    check(db.listing(nb2["id"]) is None, "передача снимает лот")
    # обмен
    await g(ADMIN, "выдать номер @bob тесто редкий")
    nbb = db.user_numbers(BOB)[0]
    nca = db.user_numbers(CAROL)[0]
    await g(BOB, f"обмен #{nbb['id']} #{nca['id']} 500")
    t = db.trades_for(CAROL)
    check(t and "предлагает" in last(GROUP) and t[0]["extra"] == 500, "предложение обмена с доплатой")
    await g(ALICE, f"обмен принять {t[0]['id']}")
    check("другому игроку" in last(GROUP), "чужой не может принять обмен")
    b_bob, b_carol = bal(BOB), bal(CAROL)
    await g(CAROL, "обмен принять")
    check(db.number(nbb["id"])["user_id"] == CAROL and db.number(nca["id"])["user_id"] == BOB, "обмен выполнен")
    check(bal(BOB) == b_bob - 500 and bal(CAROL) == b_carol + 500, "доплата прошла")
    await g(CAROL, f"обмен #{nbb['id']} #{nca['id']}")
    await g(BOB, "обмен отклонить")
    check("отклонил" in last(GROUP) and not db.trades_for(CAROL), "обмен отклонён")
    await g(CAROL, f"обмен #{nbb['id']} #{nca['id']}")
    await g(CAROL, "обмен отмена")
    check("отменены" in last(GROUP) and not db.trades_for(BOB), "обмен отменён инициатором")
    await g(CAROL, "торговля")
    check("РЫНОК НОМЕРОВ" in last(GROUP), "справка по торговле")

    print("• Дуэли")
    db.set_balance(BOB, 20_000, "test")
    db.set_balance(ALICE, 20_000, "test")
    await g(ALICE, "дуэль 1000")
    check("открытый вызов" in last(GROUP) and bal(ALICE) == 19_000, "открытая дуэль")
    await g(ALICE, "дуэль 500")
    check("уже есть активный вызов" in last(GROUP), "вторая дуэль запрещена")
    api.dice_values = [6, 2]
    await g(BOB, "принять")
    await asyncio.sleep(0.1)
    for _ in range(40):
        if db.duel(db.all("SELECT id FROM duels ORDER BY created DESC LIMIT 1")[0]["id"])["status"] == "done":
            break
        await asyncio.sleep(0.5)
    d = db.all("SELECT * FROM duels ORDER BY created DESC LIMIT 1")[0]
    check(d["status"] == "done" and d["winner"] == ALICE, "дуэль сыграна, победила Alice")
    check(bal(ALICE) == 19_000 + 1900 and bal(BOB) == 19_000, "выплата с комиссией 5%")
    check("побеждает" in last(GROUP), "итог дуэли в чате")
    await g(ALICE, "дуэль 1000 @bob")
    check("вызывает" in last(GROUP), "вызов игроку")
    await g(CAROL, "принять")
    check("адресован другому" in last(GROUP) or "Нет вызовов" in last(GROUP), "чужой не может принять адресный вызов")
    await g(BOB, "отклонить")
    check("отклонил" in last(GROUP) and bal(ALICE) == 19_000 + 1900, "отклонение + возврат")
    await g(ALICE, "дуэль 1000", reply_to=BOB)
    check("вызывает" in last(GROUP) and db.user_pending_duels(ALICE), "вызов ответом на сообщение")
    await g(ALICE, "дуэль отмена")
    check(not db.user_pending_duels(ALICE) and "отменён" in last(GROUP), "отмена вызова")

    print("• Кланы")
    db.set_balance(ALICE, 100_000, "test")
    await g(ALICE, "клан")
    check("КЛАНЫ" in last(GROUP), "клан: справка без клана")
    await g(ALICE, "клан создать ТОП Лучшие")
    check(db.user_clan(ALICE) is not None and bal(ALICE) == 50_000, "клан создан за 50 000")
    await g(ALICE, "клан")
    check("[ТОП]" in last(GROUP) and "Казна" in last(GROUP), "карточка клана")
    await g(BOB, "клан создать топ Другие")
    check("уже есть" in last(GROUP), "тег уникален")
    await g(ALICE, "клан пригласить @bob")
    check("приглашают" in last(GROUP), "приглашение")
    await g(BOB, "клан принять ТОП")
    check(db.user_clan(BOB) is not None, "вступление по приглашению")
    await g(CAROL, "клан вступить ТОП")
    check("закрытый" in last(GROUP) and db.user_clan(CAROL) is None, "закрытый клан")
    await g(ALICE, "клан открыть")
    await g(CAROL, "клан вступить ТОП")
    check(db.user_clan(CAROL) is not None, "открытый клан: вступление")
    await g(BOB, "клан внести 1000")
    check(db.clan_by_tag("ТОП")["treasury"] == 1000 and bal(BOB) == 18_000, "взнос в казну")
    await g(BOB, "клан снять 500")
    check("только глава" in last(GROUP), "снять может только глава")
    await g(ALICE, "клан снять 500")
    check(db.clan_by_tag("ТОП")["treasury"] == 500, "глава снял из казны")
    b0 = bal(CAROL)
    await g(ALICE, "клан выдать @carol 200")
    check(db.clan_by_tag("ТОП")["treasury"] == 300 and bal(CAROL) == b0 + 200, "выдача из казны участнику")
    await g(ALICE, "клан зам @bob")
    check(db.user(BOB)["clan_role"] == "deputy", "назначение зама")
    await g(BOB, "клан кик @carol")
    check(db.user_clan(CAROL) is None, "зам выгнал участника")
    await g(ALICE, "топ")
    check("[ТОП]" in last(GROUP), "тег в топе")
    await g(ALICE, "профиль")
    check("[ТОП]" in last(GROUP), "тег в профиле")
    await g(ALICE, "клан топ")
    check("ТОП КЛАНОВ" in last(GROUP) and "Лучшие" in last(GROUP), "топ кланов")
    await g(BOB, "клан выйти")
    check(db.user_clan(BOB) is None, "выход из клана")
    await g(ALICE, "клан удалить")
    check("Подтверди" in last(GROUP), "удаление требует подтверждения")
    b0 = bal(ALICE)
    await g(ALICE, "клан удалить да")
    check(db.user_clan(ALICE) is None and bal(ALICE) == b0 + 300, "клан удалён, казна вернулась")

    print("• Титулы и модерация")
    await g(BOB, "+модер @carol")
    check(db.user(CAROL)["rank"] == 0, "игрок не может выдать титул")
    await g(ADMIN, "+гладмин @bob")
    check(bot.rank(BOB) == 4 and "Гл. админ" in last(GROUP), "владелец выдал гладмина")
    await g(BOB, "+гладмин @carol")
    check(bot.rank(CAROL) == 0 and "ниже своего" in last(GROUP), "нельзя выдать равный титул")
    await g(BOB, "+админ @carol")
    check(bot.rank(CAROL) == 3, "гладмин выдал админа")
    await g(CAROL, "+модер", reply_to=ALICE)
    check(bot.rank(ALICE) == 1, "админ выдал модера ответом на сообщение")
    await g(CAROL, "-админ @bob")
    check(bot.rank(BOB) == 4 and "ниже твоего" in last(GROUP), "нельзя снять титул выше")
    await g(BOB, "-админ @admin")
    check(bot.rank(ADMIN) == 5, "владельца не разжаловать")
    await g(ALICE, "титулы")
    check("Владелец" in last(GROUP) and "Модер" in last(GROUP), "список администрации")
    # модер: варн, мут
    await g(ALICE, "варн @bob спам")
    check("ниже твоего" in last(GROUP), "модер не может варнить гладмина")
    await g(ALICE, "варн", reply_to=CAROL)
    check("ниже твоего" in last(GROUP), "модер не может варнить админа")
    db.set_rank(CAROL, 0)
    await g(ALICE, "варн @carol спам")
    check(db.user(CAROL)["warns"] == 1 and "1/3" in last(GROUP), "варн")
    await g(ALICE, "варн @carol")
    await g(ALICE, "варн @carol")
    check(db.user(CAROL)["banned"] == 1 and "бан" in last(GROUP), "3 варна = бан")
    await g(CAROL, "баланс")
    check("заблокирован" in last(GROUP), "забаненный не играет")
    await g(ALICE, "разбан @carol")
    check("Недостаточно прав" in last(GROUP), "модер не может разбанить")
    await g(BOB, "разбан @carol")
    check(db.user(CAROL)["banned"] == 0, "гладмин разбанил")
    await g(ALICE, "мут 30м @carol флуд")
    check(db.user(CAROL)["muted_until"] > time.time() + 1700 and "30 мин" in last(GROUP), "мут 30м")
    nb = len(api.sent[GROUP])
    await g(CAROL, "баланс")
    check(len(api.sent[GROUP]) == nb, "в муте бот молчит")
    await g(ALICE, "размут @carol")
    nb = len(api.sent[GROUP])
    await g(CAROL, "баланс")
    check(len(api.sent[GROUP]) == nb + 1, "размут")
    await g(ALICE, "удалить", reply_to=CAROL)
    check((GROUP, 5) in api.deleted, "удалить сообщение ответом")
    await g(ALICE, "игрок @carol")
    check("Carol" in last(GROUP) and "Ставок" in last(GROUP), "карточка игрока для модера")
    await g(ALICE, "выдать 1000 @carol")
    check("Недостаточно прав" in last(GROUP), "модер не выдаёт деньги")

    print("• Экономика (админ)")
    b0 = bal(CAROL)
    await g(ADMIN, "выдать 1000 @carol")
    check(bal(CAROL) == b0 + 1000, "выдать")
    await g(ADMIN, "выдать 2к", reply_to=CAROL)
    check(bal(CAROL) == b0 + 3000, "выдать ответом, 2к")
    await g(ADMIN, "забрать 500 @carol")
    check(bal(CAROL) == b0 + 2500, "забрать у игрока")
    await g(ADMIN, "установить 777", reply_to=CAROL)
    check(bal(CAROL) == 777, "установить")
    await g(ADMIN, "обнулить @carol")
    check(bal(CAROL) == 0, "обнулить")
    await g(ADMIN, "выдать 5000 @carol")
    await g(ADMIN, "статистика")
    check("СТАТИСТИКА" in last(GROUP), "статистика")

    print("• Промокоды")
    await g(ADMIN, "промо создать ВЕСНА 1500 2")
    check(db.promo("ВЕСНА") is not None, "промо создан")
    b0 = bal(CAROL)
    await g(CAROL, "промо весна")
    check(bal(CAROL) == b0 + 1500, "промо активирован")
    await g(CAROL, "промо весна")
    check("уже активировал" in last(GROUP), "повтор промо")
    await g(BOB, "промо весна")
    await g(ALICE, "промо весна")
    check("полностью использован" in last(GROUP), "лимит активаций")
    await g(ADMIN, "промо список")
    check("личку" in last(GROUP) and "ВЕСНА" in last(ADMIN), "список промо в личку")
    await g(ADMIN, "промо удалить ВЕСНА")
    check(db.promo("ВЕСНА") is None, "промо удалён")

    print("• Викторины")
    await g(CAROL, "викторина 100 500 слово")
    check("создают админы" in last(GROUP), "викторину создаёт только админ")
    b0 = bal(CAROL)
    await g(ADMIN, "викторина 100 500 золото")
    check("Составь слово" in last(GROUP) and db.active_quiz(GROUP), "викторина: слово")
    await g(CAROL, "серебро")
    check(bal(CAROL) == b0, "неверный ответ")
    await g(CAROL, "Золото!")
    check(bal(CAROL) == b0 + 500 and "Верно" in last(GROUP) and not db.active_quiz(GROUP), "верный ответ → награда")
    await g(ADMIN, "викт 200 300 10+2")
    check("Сколько будет" in last(GROUP), "викторина: пример")
    await g(BOB, "12")
    check("Верно" in last(GROUP), "ответ на пример")
    await g(ADMIN, "викторина 300 1000 Столица Франции? | Париж")
    await g(ADMIN, "викторина 100 100 слово")
    check("уже идёт" in last(GROUP), "одна викторина на чат")
    await g(BOB, "париж")
    check("Верно" in last(GROUP), "вопрос | ответ")
    await g(ADMIN, "викторина 100 100 слово")
    await g(ADMIN, "викторина стоп")
    check("остановлена" in last(GROUP) and not db.active_quiz(GROUP), "викторина стоп")
    await g(ADMIN, "викторина 100 100 слово")
    db.ex("UPDATE quizzes SET created=created-10000 WHERE status='active'")
    from simbot.handlers_quiz import expire_quizzes
    await expire_quizzes(bot)
    check("никто не ответил" in last(GROUP), "викторина истекает")

    print("• Разделы, техработы, настройки")
    await g(BOB, "раздел краш выкл")
    await g(ALICE, "краш 100")
    check("временно отключён" in last(GROUP), "раздел выключен")
    await g(BOB, "раздел краш вкл")
    await g(BOB, "разделы")
    check("✅ краш" in last(GROUP), "список разделов")
    await g(BOB, "техработы вкл")
    await g(ALICE, "баланс")
    check("Технические работы" in last(GROUP), "техработы")
    await g(ADMIN, "баланс")
    check("Технические работы" not in last(GROUP), "админ работает в техработы")
    await g(BOB, "техработы выкл")
    await g(BOB, "настройка min_bet 100")
    await g(ALICE, "кубик 60")
    check("Минимальная ставка — 100" in last(GROUP), "настройка применилась")
    await g(BOB, "настройка min_bet 50")
    await g(BOB, "оператор цена тесто 900")
    check(db.operator("testo")["price"] == 900, "цена оператора")
    await g(BOB, "оператор шансы тесто 50 30 12 5 2.5 0.5")
    check(abs(db.operator_odds(db.operator("testo"))["mythic"] - 0.5) < 0.01, "шансы оператора")
    await g(BOB, "оператор добавить mega 920 800 🟩 Мегафон")
    check(db.operator("mega") is not None, "новый оператор")
    await g(ALICE, "крутить мега")
    check("+7 920" in last(GROUP), "крутить новый оператор")
    await g(BOB, "оператор выкл mega")
    await g(ALICE, "крутить мега")
    check("не найден" in last(GROUP), "выключенный оператор недоступен")
    await g(BOB, "страна добавить kz +7 🇰🇿 Казахстан")
    check(db.country("kz") is not None, "страна добавлена")
    await g(BOB, "чаты")
    check("Test group" in last(GROUP), "список чатов")
    await g(BOB, "рассылка Всем привет")
    await asyncio.sleep(0.5)
    check("Всем привет" in last(CAROL), "рассылка дошла")
    await g(ADMIN, "аудит")
    check("АУДИТ" in last(GROUP), "аудит")

    print("• Пополнение и платежи")
    await g(ALICE, "пополнить 5")
    check("в личке" in last(GROUP), "пополнение в группе → отсылка в личку")
    await send(ALICE, "пополнить 5")
    check(any(m == "sendInvoice" and p["currency"] == "XTR" and p["prices"][0]["amount"] == 5 for m, p in api.calls), "счёт на 5 ⭐")
    inv = db.one("SELECT * FROM invoices WHERE user_id=? ORDER BY created DESC", (ALICE,))
    await bot._handle({"update_id": next(counter), "pre_checkout_query": {
        "id": "pcq1", "from": user(ALICE), "currency": "XTR", "total_amount": 5, "invoice_payload": inv["payload"]}})
    check(any(m == "answerPreCheckoutQuery" and p["ok"] for m, p in api.calls), "pre_checkout ok")
    b0 = bal(ALICE)
    pay = msg_update(ALICE, "")
    pay["message"]["successful_payment"] = {"currency": "XTR", "total_amount": 5, "invoice_payload": inv["payload"],
                                            "telegram_payment_charge_id": "ch_1"}
    await bot._handle(pay)
    check(bal(ALICE) == b0 + 5000, "5 ⭐ → +5000 ₽")
    await bot._handle(pay)
    check(bal(ALICE) == b0 + 5000, "повторный платёж не зачисляется дважды")
    await send(ADMIN, "рефанд ch_1")
    check(db.payment("ch_1")["refunded"] == 1 and bal(ALICE) == b0, "рефанд")

    print("• Прочее")
    await g(ALICE, "история")
    check("Последние игры" in last(GROUP), "история")
    await g(ALICE, "топ коллекция")
    check("КОЛЛЕКЦИЙ" in last(GROUP), "топ коллекций")
    await g(ALICE, "id")
    check(str(ALICE) in last(GROUP), "id")
    await g(ALICE, "/crash@simtestbot 100 1.2")
    await asyncio.sleep(0.4)
    check(ALICE not in bot.crash_games, "слэш-команда с @ботом")
    nb = len(api.sent[GROUP])
    await g(ALICE, "/crash@otherbot 100")
    check(len(api.sent[GROUP]) == nb, "команда чужому боту игнорируется")
    await g(ALICE, "просто болтаем в чате")
    check(len(api.sent[GROUP]) == nb, "болтовня в группе игнорируется")
    await g(ALICE, "игры")
    check("ИГРЫ" in last(GROUP), "игры")
    await g(BOB, "админка")
    check("Гл. админ" in last(GROUP) and "• <code>рассылка текст</code>" in last(GROUP) and "ВЛАДЕЛЕЦ" not in last(GROUP),
          "админка построчно по титулу")
    await g(BOB, "админка модер")
    check("МОДЕР" in last(GROUP) and "рассылка" not in last(GROUP), "админка: только блок модера")
    await send(ALICE, "/admin")
    check("Модер" in last(ALICE), "админка модера в личке")

    # восстановление после рестарта
    await g(ALICE, "краш 300")
    g_active = db.active_game(ALICE, "crash")
    b0 = bal(ALICE)
    bot.crash_games.clear()
    await bot._recover()
    check(bal(ALICE) == b0 + 300 and db.game(g_active["id"])["status"] == "refunded", "возврат ставки после рестарта")

    # целостность: баланс vs леджер
    bad = db.all("SELECT u.id FROM users u WHERE u.balance != (SELECT COALESCE(SUM(delta),0) FROM ledger l WHERE l.user_id=u.id)")
    check(not bad, "балансы совпадают с леджером")
    check(all("inline_keyboard" not in str(p.get("reply_markup")) for m, p in api.calls if p.get("reply_markup")), "ни одной кнопки")

    print()
    print(f"API-вызовов: {len(api.calls)}")
    if errors:
        print(f"ОШИБКИ ({len(errors)}):")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")


if __name__ == "__main__":
    asyncio.run(main())
