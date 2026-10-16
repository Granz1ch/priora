"""Игры текстом: краш, мины, слоты, кубик, апгрейдер. Поддержка подкрутки «(вин)/(луз)» для админов."""
from __future__ import annotations

import asyncio
import logging
import math

from .config import (CRASH_EDGE, CRASH_EDIT_EVERY, CRASH_K, CRASH_MAX, CRASH_TICK, DICE_EXACT, DICE_HALF, MINES_EDGE,
                     SLOT_JACKPOT, SLOT_TRIPLE, SLOT_TWO_SEVENS, UPGRADER_EDGE, UPGRADER_MULTS, UPGRADE_NUM_EDGE)
from .core import Ctx, UserError, router
from .db import InsufficientFunds
from .numbers import RNG, TIER, TIERS, appraise, fmt_number, generate_digits, tier_index, tier_label
from .ui import money, mult as fmult, number_card, pct, plural, short

log = logging.getLogger("games")
TIER_MEAN_FACTOR = 1.3


# ═══════════════════════════ ставки ═══════════════════════════
def parse_amount(text: str, balance: int) -> int:
    t = (text or "").lower().replace(" ", "").replace("\u00a0", "").replace("₽", "").replace(",", ".")
    if t in ("всё", "все", "all", "макс", "max", "ва-банк", "вабанк"):
        return balance
    m = 1
    if t.endswith(("k", "к")):
        m, t = 1_000, t[:-1]
    elif t.endswith(("m", "м", "кк")):
        m, t = 1_000_000, t.rstrip("mмк")
    try:
        return int(float(t) * m)
    except ValueError:
        raise UserError("Сумма числом: 500, 2к или всё")


def looks_like_amount(token: str | None) -> bool:
    if not token:
        return False
    t = token.lower().replace(",", ".").replace("₽", "")
    if t in ("всё", "все", "all", "макс", "max", "вабанк", "ва-банк"):
        return True
    t = t.rstrip("кkмm")
    return t.replace(".", "", 1).isdigit()


def validate_bet(ctx: Ctx, amount: int) -> int:
    lo, hi = int(ctx.setting("min_bet", 50)), int(ctx.setting("max_bet", 1_000_000))
    bal = ctx.db.balance(ctx.uid)
    if amount < lo:
        raise UserError(f"Минимальная ставка — {money(lo)}")
    if amount > hi:
        raise UserError(f"Максимальная ставка — {money(hi)}")
    if amount > bal:
        raise UserError(f"Не хватает {money(amount - bal)} (баланс {money(bal)}). «бонус» или «пополнить»")
    return amount


def bet_arg(ctx: Ctx, token: str | None, usage: str) -> int:
    if not looks_like_amount(token):
        raise UserError(usage)
    return validate_bet(ctx, parse_amount(token, ctx.db.balance(ctx.uid)))


def debit(ctx: Ctx, amount: int, reason: str) -> None:
    try:
        ctx.db.change_balance(ctx.uid, -amount, reason)
    except InsufficientFunds:
        raise UserError("Недостаточно средств на балансе")


def settle(ctx_or_bot, uid: int, gid: str, bet: int, payout: int, status: str, result: str) -> int:
    """Фиксирует результат игры и начисляет выплату. Возвращает новый баланс."""
    db = ctx_or_bot.db
    with db.tx():
        if payout > 0:
            db.change_balance(uid, payout, f"{status}:{gid}", ref=f"payout:{gid}")
        db.finish_game(gid, status, payout, result)
        db.record_game(uid, bet, payout)
        return db.balance(uid)


def parse_mult(token: str | None) -> float | None:
    if not token:
        return None
    t = token.lower().replace("х", "x").replace("×", "x").replace(",", ".").strip("x")
    if t in ("авто", "auto"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


# ═══════════════════════════ КРАШ ═══════════════════════════
class CrashGame:
    __slots__ = ("gid", "uid", "chat_id", "bet", "crash_at", "auto", "msg_id", "lock", "done", "current", "who")

    def __init__(self, gid, uid, chat_id, bet, crash_at, auto, msg_id, who):
        self.gid, self.uid, self.chat_id, self.bet = gid, uid, chat_id, bet
        self.crash_at, self.auto, self.msg_id, self.who = crash_at, auto, msg_id, who
        self.lock = asyncio.Lock()
        self.done = False
        self.current = 1.0


def gen_crash() -> float:
    u = RNG.random() or 1e-9
    x = (1 - CRASH_EDGE) / u
    if x < 1:
        return 1.0
    return min(CRASH_MAX, math.floor(x * 100) / 100)


def crash_bar(m: float) -> str:
    frac = min(1.0, math.log(max(m, 1.0)) / math.log(CRASH_MAX))
    n = max(1, round(frac * 12))
    out = []
    for i in range(12):
        out.append("⬜" if i >= n else ("🟩" if i < 4 else "🟨" if i < 7 else "🟧" if i < 10 else "🟥"))
    return "".join(out)


def crash_text(g: CrashGame, m: float) -> str:
    return (f"🚀 {g.who} · <b>×{m:.2f}</b>" + (f" · авто {fmult(g.auto)}" if g.auto else "") + "\n"
            f"{crash_bar(m)}\n💵 {money(g.bet)} → 💰 <b>{money(math.floor(g.bet * m))}</b> · напиши «забрать»")


CRASH_USAGE = "🚀 Краш: <code>краш 500</code> или с автовыводом <code>краш 500 2</code> · «забрать» — вывести"


@router.cmd("краш", "crash", "кр", section="crash")
async def cmd_crash(ctx: Ctx) -> None:
    a0 = ctx.arg(0)
    if not a0:
        g = ctx.bot.crash_games.get(ctx.uid)
        if g:
            raise UserError(f"Ты уже в полёте (×{g.current:.2f}) — напиши «забрать»")
        await ctx.reply(CRASH_USAGE)
        return
    if a0 in ("забрать", "стоп", "вывод"):
        await cmd_cashout(ctx)
        return
    if ctx.uid in ctx.bot.crash_games:
        raise UserError("Ты уже в полёте — напиши «забрать»")
    bet = bet_arg(ctx, a0, CRASH_USAGE)
    auto = None
    for tok in ctx.args[1:]:
        v = parse_mult(tok)
        if v is not None:
            auto = v
    if auto is not None and not 1.1 <= auto <= CRASH_MAX:
        raise UserError(f"Автовывод от ×1.1 до ×{CRASH_MAX:g}")
    await crash_start(ctx, bet, auto)


async def crash_start(ctx: Ctx, bet: int, auto: float | None) -> None:
    debit(ctx, bet, "crash:bet")
    crash_at = gen_crash()
    if ctx.force == "win":
        crash_at = CRASH_MAX
    elif ctx.force == "lose":
        crash_at = 1.0
    gid = ctx.db.create_game(ctx.uid, ctx.chat_id, "crash", bet, {"crash_at": crash_at, "auto": auto})
    game = CrashGame(gid, ctx.uid, ctx.chat_id, bet, crash_at, auto, None, ctx.who)
    msg = await ctx.reply(crash_text(game, 1.0))
    game.msg_id = (msg or {}).get("message_id")
    ctx.db.update_game(gid, msg_id=game.msg_id)
    ctx.bot.crash_games[ctx.uid] = game
    ctx.bot.spawn(crash_loop(ctx.bot, game))


async def crash_loop(bot, game: CrashGame) -> None:
    n = 0
    try:
        while True:
            await asyncio.sleep(CRASH_TICK)
            async with game.lock:
                if game.done:
                    return
                n += 1
                m = round(math.exp(CRASH_K * n * CRASH_TICK), 2)
                if m >= game.crash_at:
                    await crash_finish(bot, game, win=False, m=game.crash_at)
                    return
                game.current = m
                if game.auto and m >= game.auto:
                    await crash_finish(bot, game, win=True, m=m, auto=True)
                    return
                if m >= CRASH_MAX:
                    await crash_finish(bot, game, win=True, m=CRASH_MAX)
                    return
                if n % CRASH_EDIT_EVERY == 0 and game.msg_id:
                    await bot.api.edit(game.chat_id, game.msg_id, crash_text(game, m))
    except Exception:  # noqa: BLE001
        log.exception("crash loop")
        if not game.done:
            async with game.lock:
                if not game.done:
                    await crash_finish(bot, game, win=True, m=1.0, refund=True)


async def crash_finish(bot, game: CrashGame, win: bool, m: float, auto: bool = False, refund: bool = False) -> None:
    game.done = True
    if bot.crash_games.get(game.uid) is game:
        bot.crash_games.pop(game.uid, None)
    payout = math.floor(game.bet * m) if win else 0
    status = "refunded" if refund else ("win" if win else "lose")
    bal = settle(bot, game.uid, game.gid, game.bet, payout, status,
                 f"{'забрал ' if win else ''}×{m:.2f} · краш ×{game.crash_at:.2f}")
    if refund:
        text = f"♻️ {game.who} · сбой, ставка {money(game.bet)} возвращена"
    elif win:
        text = (f"✅ {game.who} {'авто-вывод' if auto else 'забрал'} <b>×{m:.2f}</b> · <b>+{money(payout - game.bet)}</b>\n"
                f"{crash_bar(game.crash_at)}\n💥 краш был бы на ×{game.crash_at:.2f} · 💰 {money(bal)}")
    else:
        text = (f"💥 <b>КРАШ ×{game.crash_at:.2f}</b> · {game.who} <b>-{money(game.bet)}</b>\n"
                f"{crash_bar(game.crash_at)}\n💰 {money(bal)}")
    res = None
    if game.msg_id:
        res = await bot.api.edit(game.chat_id, game.msg_id, text)
    if res is None:
        await bot.api.send(game.chat_id, text)


@router.cmd("забрать", "стоп", "вывод", "cashout", "з", "stop")
async def cmd_cashout(ctx: Ctx) -> None:
    # админ: «забрать 1000 @ник» — списать деньги у игрока
    if ctx.rank >= 3 and any(looks_like_amount(t) for t in ctx.args) and (
            (ctx.reply_user and not ctx.reply_user.get("is_bot"))
            or any(t.startswith("@") or (t.isdigit() and len(t) >= 5) for t in ctx.args[1:])):
        from .handlers_admin import cmd_take
        await cmd_take(ctx)
        return
    game = ctx.bot.crash_games.get(ctx.uid)
    if game:
        async with game.lock:
            if game.done:
                pass
            elif game.current <= 1.0:
                await ctx.reply("⏳ Ещё разгон — секунду")
                return
            else:
                await crash_finish(ctx.bot, game, win=True, m=game.current)
                return
    g = ctx.db.active_game(ctx.uid, "mines")
    if g:
        await mines_cashout(ctx, g)
        return
    raise UserError("Нечего забирать — нет активного краша или мин")


# ═══════════════════════════ МИНЫ ═══════════════════════════
MINES_USAGE = ("💣 Мины: <code>мины 500 3</code> (ставка и число мин 1–24) · открыть клетку: <code>7</code> или "
               "<code>откр 7</code> · «забрать» — вывести · «отмена» — вернуть ставку до первого хода")


def mines_mult(mines: int, opened: int) -> float:
    p = 1.0
    for i in range(opened):
        p *= (25 - mines - i) / (25 - i)
    return round((1 - MINES_EDGE) / p, 2)


def board(st: dict, reveal: bool = False, hit: int | None = None) -> str:
    opened, mines_set = set(st["open"]), set(st["mines"])
    rows = []
    for r in range(5):
        cells = []
        for c in range(5):
            i = r * 5 + c
            if i == hit:
                cells.append("💥")
            elif i in opened:
                cells.append("💎")
            elif reveal and i in mines_set:
                cells.append("💣")
            else:
                cells.append(f"{i + 1:2d}")
        rows.append(" ".join(cells))
    return "<code>" + "\n".join(rows) + "</code>"


def mines_head(ctx_who: str, bet: int, st: dict) -> str:
    k, opened = st["n"], len(st["open"])
    m = mines_mult(k, opened)
    nxt = mines_mult(k, opened + 1) if opened < 25 - k else None
    return (f"💣 {ctx_who} · {k} {plural(k, 'мина', 'мины', 'мин')} · 💵 {money(bet)} · открыто {opened}/{25 - k}\n"
            f"💎 <b>×{m:.2f}</b> → {money(math.floor(bet * m))}" + (f" · дальше ×{nxt:.2f}" if nxt else " · 🏁 всё!"))


async def show_board(ctx: Ctx, g, st: dict, text: str, final: bool = False) -> None:
    """Удаляет старое поле и присылает новое (ответом на команду)."""
    if g["msg_id"]:
        await ctx.api.delete(ctx.chat_id, int(g["msg_id"]))
    msg = await ctx.reply(text)
    if not final:
        ctx.db.update_game(g["id"], msg_id=(msg or {}).get("message_id"))


@router.cmd("мины", "mines", "мина", "минки", section="mines")
async def cmd_mines(ctx: Ctx) -> None:
    g = ctx.db.active_game(ctx.uid, "mines")
    a0 = ctx.arg(0)
    if a0 in ("забрать", "стоп"):
        if not g:
            raise UserError("Нет активного поля")
        await mines_cashout(ctx, g)
        return
    if a0 in ("отмена", "отменить"):
        if not g:
            raise UserError("Нет активного поля")
        await mines_cancel(ctx, g)
        return
    if g:
        cells = [int(t) for t in ctx.args if t.isdigit() and 1 <= int(t) <= 25]
        if cells and len(cells) == len(ctx.args):
            await mines_open(ctx, g, cells)
            return
        st = ctx.db.game_state(g)
        await show_board(ctx, g, st, mines_head(ctx.who, int(g["bet"]), st) + "\n" + board(st) +
                         "\n<i>«7» — открыть · «забрать» — вывести</i>")
        return
    if not a0:
        await ctx.reply(MINES_USAGE)
        return
    bet = bet_arg(ctx, a0, MINES_USAGE)
    k = 3
    if ctx.arg(1):
        if not ctx.arg(1).isdigit() or not 1 <= int(ctx.arg(1)) <= 24:
            raise UserError("Число мин — от 1 до 24")
        k = int(ctx.arg(1))
    await mines_start(ctx, bet, {"n": k})


async def mines_start(ctx: Ctx, bet: int, data: dict) -> None:
    k = int(data.get("n", 3))
    debit(ctx, bet, "mines:bet")
    cells = RNG.sample(range(25), k)
    st = {"n": k, "mines": cells, "open": [], "force": ctx.force}
    gid = ctx.db.create_game(ctx.uid, ctx.chat_id, "mines", bet, st)
    msg = await ctx.reply(mines_head(ctx.who, bet, st) + "\n" + board(st) +
                          "\n<i>Напиши номер клетки (1–25) · «забрать» — вывести</i>")
    if msg:
        ctx.db.update_game(gid, msg_id=msg.get("message_id"))


@router.cmd("откр", "открыть", "клетка", "open", "о", section="mines")
async def cmd_open(ctx: Ctx) -> None:
    g = ctx.db.active_game(ctx.uid, "mines")
    if not g:
        raise UserError("Нет активного поля · <code>мины 500 3</code>")
    cells = [int(t) for t in ctx.args if t.isdigit() and 1 <= int(t) <= 25]
    if not cells:
        raise UserError("Укажи клетку 1–25: <code>откр 7</code>")
    await mines_open(ctx, g, cells)


@router.on_text
async def mines_text_hook(ctx: Ctx) -> bool:
    """Просто число (или несколько) при открытом поле мин = открыть клетки."""
    toks = ctx.text.replace(",", " ").split()
    if not toks or not all(t.isdigit() and 1 <= int(t) <= 25 for t in toks):
        return False
    g = ctx.db.active_game(ctx.uid, "mines")
    if not g:
        return False
    if not ctx.bot.section_enabled("mines"):
        return False
    await mines_open(ctx, g, [int(t) for t in toks])
    return True


def _apply_force(st: dict, cell: int) -> None:
    """Подкрутка: при «вин» мина уходит с клетки, при «луз» клетка становится миной."""
    force = st.get("force")
    mines_set, opened = set(st["mines"]), set(st["open"])
    if force == "win" and cell in mines_set:
        free = [i for i in range(25) if i not in mines_set and i not in opened and i != cell]
        if free:
            mines_set.discard(cell)
            mines_set.add(RNG.choice(free))
    elif force == "lose" and cell not in mines_set:
        victim = RNG.choice(sorted(mines_set))
        mines_set.discard(victim)
        mines_set.add(cell)
    st["mines"] = sorted(mines_set)


async def mines_open(ctx: Ctx, g, cells: list[int]) -> None:
    st = ctx.db.game_state(g)
    bet = int(g["bet"])
    gid = g["id"]
    for cell in cells:
        cell -= 1
        if cell in st["open"]:
            continue
        _apply_force(st, cell)
        if cell in st["mines"]:
            bal = settle(ctx, ctx.uid, gid, bet, 0, "lose", f"💣 на {len(st['open']) + 1}-й клетке")
            ctx.db.update_game(gid, state=st)
            await show_board(ctx, g, st, f"💥 <b>БАБАХ!</b> {ctx.who} · мина на {len(st['open']) + 1}-й клетке · "
                                         f"<b>-{money(bet)}</b> · 💰 {money(bal)}\n" + board(st, reveal=True, hit=cell),
                             final=True)
            return
        st["open"].append(cell)
        ctx.db.update_game(gid, state=st)
        if len(st["open"]) >= 25 - st["n"]:
            m = mines_mult(st["n"], len(st["open"]))
            payout = math.floor(bet * m)
            bal = settle(ctx, ctx.uid, gid, bet, payout, "win", f"все клетки ×{m:.2f}")
            await show_board(ctx, g, st, f"🏁 {ctx.who} открыл всё поле! <b>×{m:.2f} · +{money(payout - bet)}</b> · "
                                         f"💰 {money(bal)}\n" + board(st, reveal=True), final=True)
            return
    await show_board(ctx, g, st, mines_head(ctx.who, bet, st) + "\n" + board(st) +
                     "\n<i>Ещё клетку или «забрать»</i>")


async def mines_cashout(ctx: Ctx, g) -> None:
    st = ctx.db.game_state(g)
    opened = len(st["open"])
    if opened == 0:
        raise UserError("Сначала открой хотя бы одну клетку (или «отмена»)")
    bet = int(g["bet"])
    m = mines_mult(st["n"], opened)
    payout = math.floor(bet * m)
    bal = settle(ctx, ctx.uid, g["id"], bet, payout, "win", f"×{m:.2f} после {opened} кл.")
    await show_board(ctx, g, st, f"✅ {ctx.who} забрал <b>×{m:.2f}</b> · <b>+{money(payout - bet)}</b> · 💰 {money(bal)}\n"
                     + board(st, reveal=True), final=True)


async def mines_cancel(ctx: Ctx, g) -> None:
    st = ctx.db.game_state(g)
    if st["open"]:
        raise UserError("Уже нельзя — только «забрать»")
    bet = int(g["bet"])
    with ctx.db.tx():
        ctx.db.change_balance(ctx.uid, bet, f"mines:cancel:{g['id']}", ref=f"payout:{g['id']}")
        ctx.db.finish_game(g["id"], "refunded", bet, "отмена")
    if g["msg_id"]:
        await ctx.api.delete(ctx.chat_id, int(g["msg_id"]))
    await ctx.reply(f"✖️ Поле закрыто, {money(bet)} возвращены")


@router.cmd("отмена", "cancel", "отменить")
async def cmd_cancel(ctx: Ctx) -> None:
    g = ctx.db.active_game(ctx.uid, "mines")
    if g:
        await mines_cancel(ctx, g)
        return
    from .handlers_duel import cancel_my_duels
    if await cancel_my_duels(ctx):
        return
    ctx.db.set_state(ctx.uid, None)
    await ctx.reply("Отменять нечего 🙂")


# ═══════════════════════════ СЛОТЫ ═══════════════════════════
SLOT_SYMBOLS = ["🅱️", "🍇", "🍋", "7️⃣"]
SLOTS_USAGE = f"🎰 Слоты: <code>слоты 100</code> · 777 ×{SLOT_JACKPOT} · три одинаковых ×{SLOT_TRIPLE} · две 7 ×{SLOT_TWO_SEVENS}"


def slot_decode(value: int) -> list[int]:
    v = max(1, min(64, int(value))) - 1
    return [v & 3, (v >> 2) & 3, (v >> 4) & 3]


def slot_payout_mult(reels: list[int]) -> tuple[float, str]:
    if reels == [3, 3, 3]:
        return SLOT_JACKPOT, "🎉 <b>ДЖЕКПОТ 777!</b>"
    if reels[0] == reels[1] == reels[2]:
        return SLOT_TRIPLE, "✨ <b>Три одинаковых!</b>"
    if reels.count(3) == 2:
        return SLOT_TWO_SEVENS, "🙂 Две семёрки"
    return 0, "💨 Мимо"


def forced_reels(force: str) -> list[int]:
    if force == "win":
        if RNG.random() < 0.2:
            return [3, 3, 3]
        s = RNG.choice([0, 1, 2])
        return [s, s, s]
    while True:
        r = [RNG.randrange(4) for _ in range(3)]
        if slot_payout_mult(r)[0] == 0:
            return r


@router.cmd("слоты", "слот", "slots", "slot", section="slots")
async def cmd_slots(ctx: Ctx) -> None:
    if not ctx.arg(0):
        await ctx.reply(SLOTS_USAGE)
        return
    bet = bet_arg(ctx, ctx.arg(0), SLOTS_USAGE)
    await slots_start(ctx, bet, {})


async def slots_start(ctx: Ctx, bet: int, data: dict) -> None:
    debit(ctx, bet, "slots:bet")
    gid = ctx.db.create_game(ctx.uid, ctx.chat_id, "slots", bet)
    dice = None
    if ctx.force:
        reels = forced_reels(ctx.force)
        await asyncio.sleep(0.8)
    else:
        kw = {"reply_to_message_id": ctx.msg_id, "allow_sending_without_reply": True} if ctx.is_group and ctx.msg_id else {}
        dice = await ctx.api.dice(ctx.chat_id, "🎰", **kw)
        value = int((dice or {}).get("dice", {}).get("value") or RNG.randint(1, 64))
        reels = slot_decode(value)
        await asyncio.sleep(3.0 if dice else 0.5)
    m, label = slot_payout_mult(reels)
    payout = math.floor(bet * m)
    bal = settle(ctx, ctx.uid, gid, bet, payout, "win" if payout > bet else ("push" if payout == bet else "lose"),
                 "".join(SLOT_SYMBOLS[r] for r in reels))
    line = " | ".join(SLOT_SYMBOLS[r] for r in reels)
    res = f"{label} ×{m:g} · <b>+{money(payout - bet)}</b>" if payout > 0 else f"{label} · <b>-{money(bet)}</b>"
    await ctx.reply(f"🎰 {ctx.who} · [ {line} ]\n{res} · 💰 {money(bal)}")


# ═══════════════════════════ КУБИК ═══════════════════════════
DICE_FACES = "⚀⚁⚂⚃⚄⚅"
DICE_MODES = {"even": ("Чёт", DICE_HALF), "odd": ("Нечет", DICE_HALF),
              "low": ("Меньше (1-3)", DICE_HALF), "high": ("Больше (4-6)", DICE_HALF)}
DICE_WORDS = {"чет": "even", "чёт": "even", "четное": "even", "even": "even",
              "нечет": "odd", "нечёт": "odd", "нечетное": "odd", "odd": "odd",
              "больше": "high", "бол": "high", "high": "high", "б": "high",
              "меньше": "low", "мен": "low", "low": "low", "м": "low"}
DICE_USAGE = (f"🎲 Кубик: <code>кубик 100 чёт</code> · режимы: чёт/нечет/больше/меньше (×{DICE_HALF}) "
              f"или число 1–6 (×{DICE_EXACT})")


def dice_mode_label(mode: str) -> tuple[str, float]:
    if mode.startswith("exact"):
        return f"Число {mode.split(':')[1]}", DICE_EXACT
    return DICE_MODES.get(mode, ("?", 0))


def dice_win(mode: str, v: int) -> bool:
    if mode.startswith("exact"):
        return v == int(mode.split(":")[1])
    return {"even": v % 2 == 0, "odd": v % 2 == 1, "low": v <= 3, "high": v >= 4}.get(mode, False)


def parse_dice_mode(token: str | None) -> str | None:
    if not token:
        return None
    t = token.lower().replace("ё", "е")
    if t.isdigit() and 1 <= int(t) <= 6:
        return f"exact:{t}"
    return DICE_WORDS.get(t) or DICE_WORDS.get(t.replace("ё", "е"))


@router.cmd("кубик", "dice", "куб", "кости", section="dice")
async def cmd_dice(ctx: Ctx) -> None:
    if not ctx.arg(0):
        await ctx.reply(DICE_USAGE)
        return
    a0, a1 = ctx.arg(0), ctx.arg(1)
    # допускаем и «кубик чёт 100»
    if not looks_like_amount(a0) and looks_like_amount(a1):
        a0, a1 = a1, a0
    bet = bet_arg(ctx, a0, DICE_USAGE)
    mode = parse_dice_mode(a1) if a1 else "even"
    if mode is None:
        raise UserError(DICE_USAGE)
    await dice_start(ctx, bet, {"mode": mode})


async def dice_start(ctx: Ctx, bet: int, data: dict) -> None:
    mode = str(data.get("mode", "even"))
    label, m = dice_mode_label(mode)
    if m <= 0:
        raise UserError("Некорректный режим")
    debit(ctx, bet, "dice:bet")
    gid = ctx.db.create_game(ctx.uid, ctx.chat_id, "dice", bet, {"mode": mode})
    if ctx.force:
        pool = [v for v in range(1, 7) if dice_win(mode, v) == (ctx.force == "win")] or [1]
        value = RNG.choice(pool)
        await asyncio.sleep(0.8)
    else:
        kw = {"reply_to_message_id": ctx.msg_id, "allow_sending_without_reply": True} if ctx.is_group and ctx.msg_id else {}
        dice = await ctx.api.dice(ctx.chat_id, "🎲", **kw)
        value = int((dice or {}).get("dice", {}).get("value") or RNG.randint(1, 6))
        await asyncio.sleep(3.5 if dice else 0.5)
    win = dice_win(mode, value)
    payout = math.floor(bet * m) if win else 0
    bal = settle(ctx, ctx.uid, gid, bet, payout, "win" if win else "lose", f"{label}: выпало {value}")
    res = f"🎉 <b>Угадал! ×{m} · +{money(payout - bet)}</b>" if win else f"💔 Мимо · <b>-{money(bet)}</b>"
    await ctx.reply(f"🎲 {ctx.who} · {label} → выпало <b>{value}</b> {DICE_FACES[value - 1]}\n{res} · 💰 {money(bal)}")


# ═══════════════════════════ АПГРЕЙДЕР ═══════════════════════════
WHEEL = 16
UP_USAGE = (f"🔼 Апгрейд: <code>ап 100 2</code> (ставка и множитель {', '.join(f'{m:g}' for m in UPGRADER_MULTS)}) · "
            f"номер: <code>ап #ID</code> → шансы, <code>ап #ID эпик</code> → запуск")


def wheel_frames(chance: float, win: bool) -> list[str]:
    cells = max(1, min(WHEEL - 1, round(chance * WHEEL)))
    zone = "🟩" * cells + "⬛" * (WHEEL - cells)
    final = RNG.randrange(0, cells) if win else RNG.randrange(cells, WHEEL)
    positions = [RNG.randrange(WHEEL), (final + 3) % WHEEL, final]
    return [f"{zone}\n" + "".join("🔺" if i == p else "▫️" for i in range(WHEEL)) for p in positions]


def upg_chance(m: float) -> float:
    return min(0.95, (1 - UPGRADER_EDGE) / m)


@router.cmd("ап", "апгрейд", "up", "апгрейдер", "upgrade", section="upgrader")
async def cmd_up(ctx: Ctx) -> None:
    a0 = ctx.arg(0)
    if not a0:
        await ctx.reply(UP_USAGE)
        return
    if not looks_like_amount(a0):
        await up_number(ctx, a0, ctx.arg(1))
        return
    bet = bet_arg(ctx, a0, UP_USAGE)
    m = parse_mult(ctx.arg(1)) or 2.0
    if m not in UPGRADER_MULTS:
        raise UserError(f"Множитель один из: {', '.join(f'{x:g}' for x in UPGRADER_MULTS)}")
    await upgrader_start(ctx, bet, {"mult": m})


async def animate(ctx: Ctx, head: str, frames: list[str]) -> int | None:
    msg = await ctx.reply(head + frames[0])
    mid = (msg or {}).get("message_id")
    for frame in frames[1:]:
        await asyncio.sleep(0.6)
        await ctx.edit(mid, head + frame)
    await asyncio.sleep(0.6)
    return mid


async def upgrader_start(ctx: Ctx, bet: int, data: dict) -> None:
    m = float(data.get("mult", 2))
    chance = upg_chance(m)
    debit(ctx, bet, "upgrader:bet")
    gid = ctx.db.create_game(ctx.uid, ctx.chat_id, "upgrader", bet, {"mult": m})
    win = (ctx.force == "win") if ctx.force else (RNG.random() < chance)
    target = math.floor(bet * m)
    head = f"🔼 {ctx.who} · <b>{fmult(m)}</b> · шанс {pct(chance * 100)} · {money(bet)} → <b>{money(target)}</b>\n"
    mid = await animate(ctx, head, wheel_frames(chance, win))
    payout = target if win else 0
    bal = settle(ctx, ctx.uid, gid, bet, payout, "win" if win else "lose", f"{fmult(m)} · {'успех' if win else 'мимо'}")
    res = f"✅ <b>УДАЛОСЬ! +{money(payout - bet)}</b>" if win else f"❌ <b>Мимо</b> · -{money(bet)}"
    await ctx.edit(mid, head + f"{res} · 💰 {money(bal)}")


def num_chance(value: int, target_tier: str) -> float:
    return max(0.01, min(0.90, (1 - UPGRADE_NUM_EDGE) * value / (TIER[target_tier]["value"] * TIER_MEAN_FACTOR)))


async def up_number(ctx: Ctx, token: str, tier_token: str | None) -> None:
    from .numbers import parse_tier
    n = ctx.db.find_number(token)
    if not n or n["user_id"] != ctx.uid:
        raise UserError("Номер не найден среди твоих · «номера» — список с ID")
    higher = [t for t in TIERS if tier_index(t) > tier_index(n["tier"])]
    if not higher:
        raise UserError("Это уже максимальная редкость 🔥")
    if not tier_token:
        lines = [f"🔼 <code>{n['full']}</code> · {tier_label(n['tier'])} · {money(n['value'])}"]
        for t in higher:
            lines.append(f"→ {tier_label(t)}: <b>{pct(num_chance(int(n['value']), t) * 100)}</b>")
        lines.append(f"<i>Запуск:</i> <code>ап #{n['id']} {TIER[higher[0]]['name'].lower()}</code> <i>· проигрыш сжигает номер</i>")
        await ctx.reply("\n".join(lines))
        return
    tier = parse_tier(tier_token)
    if tier not in higher:
        raise UserError("Укажи редкость выше текущей: " + ", ".join(TIER[t]["name"].lower() for t in higher))
    op, c = ctx.db.operator(n["operator"]), ctx.db.country(n["country"])
    ch = num_chance(int(n["value"]), tier)
    win = (ctx.force == "win") if ctx.force else (RNG.random() < ch)
    new = None
    with ctx.db.tx():
        if not ctx.db.remove_number(n["id"], "upgrade_burn" if not win else "upgrade_in", ctx.uid):
            raise UserError("Номер уже использован")
        gid = ctx.db.create_game(ctx.uid, ctx.chat_id, "upgrade_num", int(n["value"]), {"from": n["full"], "to": tier})
        if win:
            dial = c["dial"] if c else "+7"
            prefix = op["prefix"] if op else n["full"].split()[1]
            digits = generate_digits(tier, taken=lambda d: ctx.db.number_taken(fmt_number(dial, prefix, d)))
            new = ctx.db.add_number(ctx.uid, n["country"], n["operator"], fmt_number(dial, prefix, digits), digits,
                                    tier, appraise(digits, tier), "upgrade")
        ctx.db.finish_game(gid, "win" if win else "lose", int(new["value"]) if new else 0,
                           f"{n['full']} → {tier}" + (f" {new['full']}" if new else " 💥"))
        ctx.db.record_game(ctx.uid, int(n["value"]), int(new["value"]) if new else 0)
    head = f"🔼 {ctx.who} · <code>{n['full']}</code> → {tier_label(tier)} · шанс {pct(ch * 100)}\n"
    mid = await animate(ctx, head, wheel_frames(ch, win))
    if win and new:
        text = number_card(new, op, c, title=f"✅ <b>АПГРЕЙД УДАЛСЯ!</b> {ctx.who}",
                           extra=f"Было {money(n['value'])} → стало <b>{money(new['value'])}</b>")
    else:
        text = head + f"💥 <b>Номер сгорел</b> · -{money(n['value'])}"
    await ctx.edit(mid, text)


GAMES_HELP = "\n".join([
    "🎮 <b>ИГРЫ</b> — пиши прямо в чат:",
    "🚀 <code>краш 500</code> · <code>краш 500 2</code> (авто ×2) · «забрать»",
    "💣 <code>мины 500 3</code> → клетки <code>7</code>, «забрать»",
    "🎰 <code>слоты 100</code>",
    "🎲 <code>кубик 100 чёт</code> · нечет/больше/меньше/1–6",
    "🔼 <code>ап 100 2</code> · <code>ап #ID эпик</code>",
    "⚔️ <code>дуэль 1000</code> · <code>дуэль 1000 @ник</code> · «принять»",
    "📱 <code>крутить тесто 3</code> · «операторы»",
    "<i>Суммы: 500, 2к, 1.5к, всё · мин. ставка {lo} ₽</i>",
])


@router.cmd("игры", "games", "играть")
async def cmd_games(ctx: Ctx) -> None:
    await ctx.reply(GAMES_HELP.format(lo=short(int(ctx.setting("min_bet", 50)))))
