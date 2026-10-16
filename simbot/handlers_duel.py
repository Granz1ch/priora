"""Дуэли текстом: «дуэль 1000», «дуэль 1000 @ник», «принять», «отклонить», «дуэли»."""
from __future__ import annotations

import asyncio
import logging

from .config import DUEL_TTL
from .core import Ctx, UserError, router
from .db import InsufficientFunds
from .handlers_games import DICE_FACES, bet_arg, looks_like_amount
from .numbers import RNG
from .ui import esc, money

log = logging.getLogger("duel")
DUEL_USAGE = ("⚔️ Дуэль: <code>дуэль 1000</code> — открытый вызов · <code>дуэль 1000 @ник</code> или ответом на "
              "сообщение — вызов игроку · «принять», «отклонить», «дуэли», «дуэль отмена»")


def duel_line(ctx: Ctx, d) -> str:
    cu = ctx.db.user(d["creator"])
    return f"#{d['id']} · {ctx.bot.display(cu)} · <b>{money(d['bet'])}</b>"


@router.cmd("дуэль", "duel", "дуель", section="duel")
async def cmd_duel(ctx: Ctx) -> None:
    a0 = ctx.arg(0).lower()
    if not a0:
        await cmd_duels(ctx)
        return
    if a0 in ("отмена", "отменить"):
        if not await cancel_my_duels(ctx):
            raise UserError("У тебя нет активных вызовов")
        return
    if a0 in ("принять", "ок"):
        ctx.args = ctx.args[1:]
        await cmd_accept(ctx)
        return
    a1 = ctx.arg(1)
    if not looks_like_amount(a0) and looks_like_amount(a1):
        a0, a1 = a1, a0
    bet = bet_arg(ctx, a0, DUEL_USAGE)
    target = None
    if ctx.has_target_token(a1):
        target = ctx.target(a1)
    await duel_create(ctx, bet, target)


async def duel_create(ctx: Ctx, bet: int, target=None) -> None:
    if ctx.db.user_pending_duels(ctx.uid):
        raise UserError("У тебя уже есть активный вызов · «дуэль отмена»")
    if target is not None and target["banned"]:
        raise UserError("Этот игрок заблокирован")
    try:
        ctx.db.change_balance(ctx.uid, -bet, "duel:stake")
    except InsufficientFunds:
        raise UserError("Недостаточно средств")
    did = ctx.db.create_duel(ctx.uid, bet, int(target["id"]) if target is not None else None, ctx.chat_id)
    if target is not None:
        tid = int(target["id"])
        await ctx.reply(f"⚔️ {ctx.who} вызывает {ctx.bot.display(target)} на дуэль · <b>{money(bet)}</b> · #{did}\n"
                        f"🎲 У кого больше на кубике — забирает банк. {ctx.bot.display(target, link=False)}, напиши "
                        f"«принять» или «отклонить» · {DUEL_TTL // 60} мин")
        if ctx.is_group:
            await ctx.dm(tid, f"⚔️ <b>Тебя вызывают на дуэль!</b> {esc(ctx.user['first_name'])} · {money(bet)} · "
                              f"ответь в чате «принять #{did}»")
        else:
            await ctx.dm(tid, f"⚔️ <b>Тебя вызывают на дуэль!</b> {esc(ctx.user['first_name'])} · ставка "
                              f"<b>{money(bet)}</b> · #{did}\nНапиши «принять» или «отклонить» · {DUEL_TTL // 60} мин")
    else:
        await ctx.reply(f"⚔️ {ctx.who} бросает открытый вызов · <b>{money(bet)}</b> · #{did}\n"
                        f"Кто смелый — напиши «принять» · {DUEL_TTL // 60} мин")


@router.cmd("дуэли", "duels", "лобби", section="duel")
async def cmd_duels(ctx: Ctx) -> None:
    rows = ctx.db.pending_for(ctx.uid, ctx.chat_id)
    mine = ctx.db.user_pending_duels(ctx.uid)
    lines = ["⚔️ <b>ДУЭЛИ</b>"]
    for d in rows[:10]:
        kind = "вызов тебе" if d["status"] == "invited" else "открытая"
        lines.append(f"• {duel_line(ctx, d)} · {kind}")
    for d in mine:
        lines.append(f"• твой вызов #{d['id']} · {money(d['bet'])} · ждёт")
    if len(lines) == 1:
        lines.append("Пока пусто")
    lines.append(f"<i>{DUEL_USAGE}</i>")
    await ctx.reply("\n".join(lines))


@router.cmd("принять", "accept", "принимаю", section="duel")
async def cmd_accept(ctx: Ctx) -> None:
    did = ctx.arg(0).lstrip("#").upper()
    if did:
        d = ctx.db.duel(did)
        if not d:
            raise UserError("Дуэль не найдена")
    else:
        rows = ctx.db.pending_for(ctx.uid, ctx.chat_id)
        if not rows:
            raise UserError("Нет вызовов, которые можно принять · «дуэли»")
        d = rows[0]
    await duel_accept(ctx, d)


async def duel_accept(ctx: Ctx, d) -> None:
    did = d["id"]
    if d["creator"] == ctx.uid:
        raise UserError("Это твой собственный вызов")
    if d["status"] not in ("open", "invited"):
        raise UserError("Эта дуэль уже недоступна")
    if d["status"] == "invited" and d["target"] != ctx.uid:
        raise UserError("Этот вызов адресован другому игроку")
    if ctx.is_group and d["chat_id"] and int(d["chat_id"]) != ctx.chat_id:
        raise UserError("Эта дуэль из другого чата")
    bet = int(d["bet"])
    if ctx.db.balance(ctx.uid) < bet:
        raise UserError(f"Нужно {money(bet)} на балансе")
    with ctx.db.tx():
        if not ctx.db.claim_duel(did, ctx.uid):
            raise UserError("Кто-то успел раньше — дуэль уже занята")
        try:
            ctx.db.change_balance(ctx.uid, -bet, f"duel:stake:{did}")
        except InsufficientFunds:
            raise UserError("Недостаточно средств")
    ctx.bot.spawn(run_duel(ctx.bot, did, ctx.chat_id))


@router.cmd("отклонить", "decline", "отказ", section="duel")
async def cmd_decline(ctx: Ctx) -> None:
    did = ctx.arg(0).lstrip("#").upper()
    rows = [r for r in ctx.db.pending_for(ctx.uid, ctx.chat_id) if r["status"] == "invited"]
    d = ctx.db.duel(did) if did else (rows[0] if rows else None)
    if not d or d["target"] != ctx.uid or d["status"] != "invited":
        raise UserError("Нет вызова, который можно отклонить")
    with ctx.db.tx():
        if not ctx.db.set_duel_status(d["id"], "declined", ("invited",)):
            raise UserError("Вызов уже неактуален")
        ctx.db.change_balance(d["creator"], d["bet"], f"duel:refund:{d['id']}", ref=f"duelref:{d['id']}")
    await ctx.reply(f"🙅 {ctx.who} отклонил вызов #{d['id']} · {money(d['bet'])} возвращены сопернику")
    if not ctx.is_group:
        await ctx.dm(d["creator"], f"🙅 <b>{esc(ctx.user['first_name'])}</b> отклонил вызов #{d['id']} · "
                                   f"{money(d['bet'])} возвращены")


async def cancel_my_duels(ctx: Ctx) -> bool:
    mine = ctx.db.user_pending_duels(ctx.uid)
    if not mine:
        return False
    total = 0
    for d in mine:
        with ctx.db.tx():
            if ctx.db.set_duel_status(d["id"], "cancelled", ("open", "invited")):
                ctx.db.change_balance(ctx.uid, d["bet"], f"duel:cancel:{d['id']}", ref=f"duelref:{d['id']}")
                total += int(d["bet"])
    await ctx.reply(f"✖️ Вызов отменён · {money(total)} возвращены")
    return True


# ───────────────────────── розыгрыш ─────────────────────────
async def roll(bot, chat_id: int) -> int:
    d = await bot.api.dice(chat_id, "🎲")
    v = (d or {}).get("dice", {}).get("value")
    return int(v) if v else RNG.randint(1, 6)


async def run_duel(bot, did: str, chat_id: int) -> None:
    db = bot.db
    d = db.duel(did)
    if not d or d["status"] != "playing":
        return
    a, b = int(d["creator"]), int(d["opponent"])
    ua, ub = db.user(a), db.user(b)
    bet = int(d["bet"])
    pot = bet * 2
    fee = int(db.setting("duel_fee", 5))
    prize = pot - pot * fee // 100
    na, nb = bot.display(ua), bot.display(ub)
    group = chat_id < 0
    chats = [chat_id] if group else [a, b]
    for cid in chats:
        await bot.api.send(cid, f"⚔️ <b>Дуэль #{did}</b> · {na} 🆚 {nb} · банк <b>{money(pot)}</b> → победителю "
                                f"{money(prize)}\n🎲 Бросаем…")
    rounds = []
    winner = None
    try:
        for _ in range(3):
            await asyncio.sleep(1.0)
            if group:
                await bot.api.send(chat_id, f"🎲 {na}")
                ra = await roll(bot, chat_id)
                await asyncio.sleep(3.5)
                await bot.api.send(chat_id, f"🎲 {nb}")
                rb = await roll(bot, chat_id)
                await asyncio.sleep(3.5)
            else:
                ra = await roll(bot, a)
                rb = await roll(bot, b)
                await asyncio.sleep(3.5)
            rounds.append([ra, rb])
            line = (f"{na} <b>{ra}</b> {DICE_FACES[ra - 1]} · {nb} <b>{rb}</b> {DICE_FACES[rb - 1]}"
                    + (" → ничья, ещё раз!" if ra == rb else ""))
            for cid in chats:
                await bot.api.send(cid, line)
            if ra != rb:
                winner = a if ra > rb else b
                break
    except Exception:
        log.exception("duel %s", did)
    with db.tx():
        if winner:
            loser = b if winner == a else a
            db.change_balance(winner, prize, f"duel:win:{did}", ref=f"duelwin:{did}")
            db.finish_duel(did, "done", winner, rounds)
            for uid, payout in ((winner, prize), (loser, 0)):
                gid = db.create_game(uid, chat_id, "duel", bet)
                db.finish_game(gid, "win" if payout else "lose", payout, f"#{did}")
                db.record_game(uid, bet, payout)
        else:
            for uid in (a, b):
                db.change_balance(uid, bet, f"duel:draw:{did}", ref=f"duelref:{did}:{uid}")
            db.finish_duel(did, "draw", None, rounds)
    if winner:
        wname = na if winner == a else nb
        text = f"🏆 <b>{wname} побеждает!</b> +{money(prize - bet)} · 💰 {money(db.balance(winner))}"
    else:
        text = "🤝 <b>Ничья</b> три раза подряд! Ставки возвращены"
    for cid in chats:
        await bot.api.send(cid, text)


async def expire_duels(bot) -> None:
    for d in bot.db.expired_duels(DUEL_TTL):
        with bot.db.tx():
            if not bot.db.set_duel_status(d["id"], "expired", ("open", "invited")):
                continue
            bot.db.change_balance(d["creator"], d["bet"], f"duel:expired:{d['id']}", ref=f"duelref:{d['id']}")
        u = bot.db.user(d["creator"])
        cid = int(d["chat_id"]) if d["chat_id"] else int(d["creator"])
        await bot.api.send(cid, f"⏳ Дуэль #{d['id']} истекла · {bot.display(u)}, {money(d['bet'])} возвращены")
