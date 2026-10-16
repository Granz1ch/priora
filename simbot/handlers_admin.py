"""Администрирование текстом: титулы, модерация, экономика, номера, промо, разделы, техработы, операторы, чаты."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from .config import DEFAULT_SETTINGS, RANK_ALIASES, RANK_EMOJI, RANK_NAMES, SECTION_TITLES, SECTIONS
from .core import Ctx, UserError, router
from .db import InsufficientFunds
from .handlers_games import looks_like_amount, parse_amount
from .numbers import DEFAULT_ODDS, TIERS, appraise, fmt_number, generate_digits, normalize_odds, parse_tier, roll_tier, tier_label
from .ui import datetime_, dur, esc, money, num, parse_duration

log = logging.getLogger("admin")


# ───────────────────────── справка ─────────────────────────
ADMIN_HELP = [
    (1, "🛡 МОДЕР", [
        ("варн @ник [причина]", "предупреждение, 3 = бан"),
        ("снять варн @ник", "снять одно предупреждение"),
        ("варны @ник", "список предупреждений"),
        ("мут 30м|2ч|1д @ник [причина]", "бот игнорирует игрока"),
        ("размут @ник", "снять мут"),
        ("удалить", "ответом на сообщение: удалить его"),
        ("игрок @ник", "карточка игрока"),
    ]),
    (2, "🛡 ГЛ. МОДЕР", [
        ("бан @ник [причина]", "блокировка в боте"),
        ("разбан @ник", "разблокировать"),
        ("снять варны @ник", "снять все предупреждения"),
    ]),
    (3, "⚜️ АДМИН", [
        ("выдать 1000 @ник", "начислить рубли"),
        ("забрать 1000 @ник", "списать рубли"),
        ("обнулить @ник", "баланс в ноль"),
        ("установить 1000 @ник", "задать баланс"),
        ("выдать номер @ник тесто [редкость]", "выдать номер"),
        ("забрать номер #ID", "изъять номер"),
        ("промо создать КОД 1000 [активаций] [дней]", "промокод"),
        ("промо удалить КОД", "удалить промокод"),
        ("промо список", "все промокоды"),
        ("викторина 100|200|300 награда задание", "слово / пример 10+2 / вопрос | ответ"),
        ("викторина стоп", "остановить викторину"),
        ("краш 500 (вин)", "подкрутка: (вин) / (луз) для краша, мин, слотов, кубика, ап"),
        ("статистика", "общая статистика бота"),
        ("+модер @ник / +глмодер @ник", "выдать титул (ниже своего)"),
        ("-модер @ник", "снять титул"),
    ]),
    (4, "👑 ГЛ. АДМИН", [
        ("техработы вкл|выкл", "заглушка для игроков"),
        ("раздел краш выкл", "выключить раздел: краш, мины, слоты, кубик, ап, дуэль, крутить, кланы, викторина, "
                             "передача, пополнение, бонус, промо, рынок"),
        ("разделы", "состояние разделов"),
        ("рассылка текст", "всем игрокам и чатам"),
        ("настройки", "список настроек"),
        ("настройка min_bet 100", "изменить настройку"),
        ("оператор цена тесто 900", "цена прокрутки"),
        ("оператор шансы тесто 55 27 12 4.5 1.2 0.3", "шансы редкостей, %"),
        ("оператор добавить код префикс цена эмодзи Название [страна]", "новый оператор"),
        ("оператор вкл|выкл|удалить код", "включить / выключить / удалить"),
        ("оператор имя|эмодзи код значение", "переименовать"),
        ("страна добавить kz +7 🇰🇿 Казахстан", "новая страна"),
        ("страна вкл|выкл kz", "включить / выключить страну"),
        ("чаты", "список чатов бота"),
        ("чат запретить [id]", "чёрный список: бот выйдет из чата и не вернётся"),
        ("чат разрешить [id]", "убрать чат из чёрного списка"),
        ("+админ @ник", "выдать титул админа"),
    ]),
    (5, "💎 ВЛАДЕЛЕЦ", [
        ("+гладмин @ник / +владелец @ник", "любые титулы"),
        ("рефанд ID_платежа", "вернуть звёзды"),
        ("аудит", "журнал действий администрации"),
    ]),
]
HELP_FILTER = {"модер": 1, "глмодер": 2, "админ": 3, "гладмин": 4, "владелец": 5}


@router.cmd("админка", "admin", "ап-помощь", "админ помощь", rank=1)
async def cmd_admin(ctx: Ctx) -> None:
    only = HELP_FILTER.get(ctx.arg(0).lower().replace("ё", "е").replace(".", ""))
    lines = [f"🛠 <b>УПРАВЛЕНИЕ</b> · твой титул: {RANK_EMOJI[ctx.rank]} {RANK_NAMES[ctx.rank]}",
             "<i>Цель — @ник, ID или ответ на сообщение игрока</i>"]
    for r, title, items in ADMIN_HELP:
        if ctx.rank < r or (only and only != r):
            continue
        lines.append(f"\n<b>{title}</b>")
        for cmd, desc in items:
            lines.append(f"• <code>{esc(cmd)}</code> — {desc}")
    if not only and ctx.rank >= 3:
        lines.append("\n<i>Только один блок: «админка модер», «админка админ», «админка гладмин»</i>")
    await ctx.reply("\n".join(lines))


def rank_label(r: int) -> str:
    return f"{RANK_EMOJI.get(r, '👤')} {RANK_NAMES.get(r, 'Игрок')}"


def amount_and_target(ctx: Ctx, usage: str, allow_self: bool = True):
    """«выдать 1000 @ник» / «выдать 1000» ответом. Возвращает (amount, target_row)."""
    amt_tok = next((t for t in ctx.args if looks_like_amount(t)), None)
    if amt_tok is None:
        raise UserError(usage)
    rest = [t for t in ctx.args if t is not amt_tok]
    target_tok = next((t for t in rest if t.startswith("@") or t.lstrip("-").isdigit()), None)
    t = ctx.target(target_tok, allow_self=allow_self)
    amount = parse_amount(amt_tok, int(t["balance"]))
    if amount < 0:
        raise UserError("Сумма должна быть положительной")
    return amount, t


def check_power(ctx: Ctx, target) -> None:
    """Нельзя действовать против игрока с титулом не ниже своего (кроме владельца)."""
    tr = ctx.bot.rank(int(target["id"]))
    if tr >= ctx.rank and ctx.rank < 5:
        raise UserError(f"У игрока титул {rank_label(tr)} — не ниже твоего")
    if tr >= 5 and int(target["id"]) in ctx.bot.cfg.admin_ids:
        raise UserError("Это владелец бота")


# ───────────────────────── титулы ─────────────────────────
def _rank_from_cmd(cmd: str) -> int | None:
    key = cmd.lstrip("+-").replace(".", "").replace(" ", "")
    return RANK_ALIASES.get(key)


@router.cmd(*[f"+{k}" for k in RANK_ALIASES], rank=3)
async def cmd_grant(ctx: Ctx) -> None:
    new_rank = _rank_from_cmd(ctx.cmd)
    if new_rank is None:
        raise UserError("Неизвестный титул")
    if new_rank >= ctx.rank and ctx.rank < 5:
        raise UserError(f"Ты можешь выдавать только титулы ниже своего ({rank_label(ctx.rank)})")
    t = ctx.target(ctx.arg(0))
    cur = ctx.bot.rank(int(t["id"]))
    if cur >= ctx.rank and ctx.rank < 5:
        raise UserError(f"У игрока уже титул {rank_label(cur)} — не ниже твоего")
    if int(t["id"]) in ctx.bot.cfg.admin_ids:
        raise UserError("Это владелец бота — титул фиксирован")
    ctx.db.set_rank(int(t["id"]), new_rank)
    ctx.db.audit(ctx.uid, "rank", {"user": t["id"], "rank": new_rank})
    await ctx.reply(f"{RANK_EMOJI[new_rank]} {ctx.bot.display(t)} получает титул <b>{RANK_NAMES[new_rank]}</b> (от {ctx.who})")
    await ctx.dm(int(t["id"]), f"{RANK_EMOJI[new_rank]} Тебе выдан титул <b>{RANK_NAMES[new_rank]}</b> · напиши «админка»")


@router.cmd(*[f"-{k}" for k in RANK_ALIASES], "-титул", "снять титул", "разжаловать", rank=3)
async def cmd_revoke(ctx: Ctx) -> None:
    t = ctx.target(ctx.arg(0))
    cur = ctx.bot.rank(int(t["id"]))
    if cur == 0:
        raise UserError("У игрока нет титула")
    if cur >= ctx.rank and ctx.rank < 5:
        raise UserError(f"У игрока титул {rank_label(cur)} — не ниже твоего")
    if int(t["id"]) in ctx.bot.cfg.admin_ids:
        raise UserError("Это владелец бота — титул фиксирован")
    ctx.db.set_rank(int(t["id"]), 0)
    ctx.db.ex("UPDATE users SET is_admin=0 WHERE id=?", (int(t["id"]),))
    ctx.db.audit(ctx.uid, "unrank", {"user": t["id"], "was": cur})
    await ctx.reply(f"👤 {ctx.bot.display(t)} лишён титула {RANK_NAMES[cur]}")


@router.cmd("титулы", "администрация", "стафф", "staff", "админы")
async def cmd_staff(ctx: Ctx) -> None:
    lines = ["👑 <b>АДМИНИСТРАЦИЯ</b>"]
    seen = set()
    for aid in sorted(ctx.bot.cfg.admin_ids):
        u = ctx.db.user(aid)
        if u:
            seen.add(aid)
            lines.append(f"{rank_label(5)} · {ctx.bot.display(u)}")
    for u in ctx.db.ranked_users():
        if u["id"] in seen:
            continue
        lines.append(f"{rank_label(int(u['rank']))} · {ctx.bot.display(u)}")
    if len(lines) == 1:
        lines.append("Пока никого")
    await ctx.reply("\n".join(lines))


# ───────────────────────── модерация ─────────────────────────
@router.cmd("варн", "warn", "пред", rank=1)
async def cmd_warn(ctx: Ctx) -> None:
    t = ctx.target(ctx.arg(0))
    check_power(ctx, t)
    reason = ctx.rest(0 if ctx.reply_user else 1)
    n = ctx.db.add_warn(int(t["id"]), ctx.uid, reason)
    limit = int(ctx.setting("warn_limit", 3))
    ctx.db.audit(ctx.uid, "warn", {"user": t["id"], "reason": reason, "n": n})
    text = f"⚠️ {ctx.bot.display(t)} получает предупреждение <b>{n}/{limit}</b>" + (f" · {esc(reason)}" if reason else "")
    if n >= limit:
        ctx.db.set_ban(int(t["id"]), True, f"{limit} предупреждения")
        ctx.db.clear_warns(int(t["id"]))
        text += "\n🚫 Лимит предупреждений — <b>бан</b>"
    await ctx.reply(text)


@router.cmd("снять варн", "разварн", "unwarn", rank=1)
async def cmd_unwarn(ctx: Ctx) -> None:
    t = ctx.target(ctx.arg(0))
    n = ctx.db.remove_warn(int(t["id"]))
    ctx.db.audit(ctx.uid, "unwarn", {"user": t["id"]})
    await ctx.reply(f"✅ Снято предупреждение у {ctx.bot.display(t)} · осталось {n}")


@router.cmd("снять варны", "очистить варны", rank=2)
async def cmd_clear_warns(ctx: Ctx) -> None:
    t = ctx.target(ctx.arg(0))
    ctx.db.clear_warns(int(t["id"]))
    ctx.db.audit(ctx.uid, "clearwarns", {"user": t["id"]})
    await ctx.reply(f"✅ Все предупреждения {ctx.bot.display(t)} сняты")


@router.cmd("варны", "warns", rank=1)
async def cmd_warns(ctx: Ctx) -> None:
    t = ctx.target(ctx.arg(0), allow_self=True)
    rows = ctx.db.all("SELECT * FROM warns WHERE user_id=? ORDER BY id DESC LIMIT 5", (int(t["id"]),))
    lines = [f"⚠️ {ctx.bot.display(t)} · предупреждений: <b>{t['warns']}</b>/{ctx.setting('warn_limit', 3)}"]
    for w in rows:
        by = ctx.db.user(int(w["by"]))
        lines.append(f"• {datetime_(w['created'])} · {esc(w['reason']) or '—'} · от {esc(by['first_name']) if by else w['by']}")
    await ctx.reply("\n".join(lines))


@router.cmd("мут", "mute", "заткнуть", rank=1)
async def cmd_mute(ctx: Ctx) -> None:
    dur_tok = next((t for t in ctx.args if parse_duration(t) and not t.startswith("@")), None)
    target_tok = next((t for t in ctx.args if t.startswith("@") or (t.isdigit() and len(t) >= 5 and t != dur_tok)), None)
    t = ctx.target(target_tok)
    check_power(ctx, t)
    seconds = parse_duration(dur_tok) if dur_tok else 3600
    seconds = max(60, min(seconds, 30 * 86400))
    until = int(time.time()) + seconds
    ctx.db.set_mute(int(t["id"]), until)
    reason = " ".join(x for x in ctx.args if x not in (dur_tok, target_tok))
    ctx.db.audit(ctx.uid, "mute", {"user": t["id"], "sec": seconds, "reason": reason})
    await ctx.reply(f"🔇 {ctx.bot.display(t)} в муте на <b>{dur(seconds)}</b> — бот не реагирует на его команды"
                    + (f" · {esc(reason)}" if reason else ""))


@router.cmd("размут", "unmute", rank=1)
async def cmd_unmute(ctx: Ctx) -> None:
    t = ctx.target(ctx.arg(0))
    ctx.db.set_mute(int(t["id"]), 0)
    ctx.db.audit(ctx.uid, "unmute", {"user": t["id"]})
    await ctx.reply(f"🔊 {ctx.bot.display(t)} размучен")


@router.cmd("удалить", "дел", "del", rank=1)
async def cmd_delete(ctx: Ctx) -> None:
    if not ctx.reply_msg:
        raise UserError("Ответь командой на сообщение, которое нужно удалить")
    ok = await ctx.api.delete(ctx.chat_id, int(ctx.reply_msg.get("message_id")))
    await ctx.delete_incoming()
    if not ok:
        await ctx.reply("⚠️ Не удалось удалить — нужны права администратора чата")


@router.cmd("бан", "ban", "забанить", rank=2)
async def cmd_ban(ctx: Ctx) -> None:
    t = ctx.target(ctx.arg(0))
    check_power(ctx, t)
    reason = ctx.rest(0 if ctx.reply_user else 1)
    ctx.db.set_ban(int(t["id"]), True, reason)
    ctx.db.audit(ctx.uid, "ban", {"user": t["id"], "reason": reason})
    await ctx.reply(f"🚫 {ctx.bot.display(t)} заблокирован в боте" + (f" · {esc(reason)}" if reason else ""))
    await ctx.dm(int(t["id"]), "🚫 Ты заблокирован в боте" + (f". Причина: {esc(reason)}" if reason else ""))


@router.cmd("разбан", "unban", rank=2)
async def cmd_unban(ctx: Ctx) -> None:
    t = ctx.target(ctx.arg(0))
    ctx.db.set_ban(int(t["id"]), False)
    ctx.db.clear_warns(int(t["id"]))
    ctx.db.audit(ctx.uid, "unban", {"user": t["id"]})
    await ctx.reply(f"✅ {ctx.bot.display(t)} разблокирован")
    await ctx.dm(int(t["id"]), "✅ Ты разблокирован — добро пожаловать обратно")


@router.cmd("игрок", "user", "кто", "юзер", rank=1)
async def cmd_user(ctx: Ctx) -> None:
    from .handlers_user import profile_text
    t = ctx.target(ctx.arg(0), allow_self=True)
    uid = int(t["id"])
    lines = [profile_text(ctx, t), f"📊 Ставок {money(t['total_bet'])} · выплат {money(t['total_won'])} · "
                                   f"последняя активность {datetime_(t['last_seen'])}"]
    led = ctx.db.all("SELECT * FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,))
    if led:
        lines.append("🧾 " + " · ".join(f"{'+' if l['delta'] >= 0 else ''}{num(l['delta'])} {esc(l['reason'])[:18]}" for l in led))
    if t["ref_by"]:
        lines.append(f"🤝 Пригласил: <code>{t['ref_by']}</code>")
    await ctx.reply("\n".join(lines))


# ───────────────────────── экономика ─────────────────────────
@router.cmd("выдать", "дать", "give", "начислить", rank=3)
async def cmd_give(ctx: Ctx) -> None:
    if ctx.arg(0).lower() in ("номер", "number"):
        ctx.args = ctx.args[1:]
        await cmd_give_number(ctx)
        return
    amount, t = amount_and_target(ctx, "Выдать: <code>выдать 1000 @ник</code> (или ответом на сообщение)")
    if amount <= 0:
        raise UserError("Сумма должна быть больше нуля")
    bal = ctx.db.change_balance(int(t["id"]), amount, f"admin:give:{ctx.uid}")
    ctx.db.audit(ctx.uid, "give", {"user": t["id"], "amount": amount})
    await ctx.reply(f"💸 {ctx.who} выдал {ctx.bot.display(t)} <b>+{money(amount)}</b> · 💰 {money(bal)}")


@router.cmd("забрать у", "отнять", "take", "списать", rank=3)
async def cmd_take(ctx: Ctx) -> None:
    if ctx.arg(0).lower() in ("номер", "number"):
        ctx.args = ctx.args[1:]
        await cmd_take_number(ctx)
        return
    amount, t = amount_and_target(ctx, "Забрать: <code>отнять 1000 @ник</code> (или ответом)")
    amount = min(amount, int(t["balance"]))
    try:
        bal = ctx.db.change_balance(int(t["id"]), -amount, f"admin:take:{ctx.uid}")
    except InsufficientFunds:
        raise UserError("У игрока меньше средств")
    ctx.db.audit(ctx.uid, "take", {"user": t["id"], "amount": amount})
    await ctx.reply(f"💸 {ctx.who} забрал у {ctx.bot.display(t)} <b>-{money(amount)}</b> · 💰 {money(bal)}")


@router.cmd("обнулить", "reset", "обнулить баланс", rank=3)
async def cmd_reset(ctx: Ctx) -> None:
    t = ctx.target(ctx.arg(0))
    check_power(ctx, t)
    ctx.db.set_balance(int(t["id"]), 0, f"admin:reset:{ctx.uid}")
    ctx.db.audit(ctx.uid, "reset", {"user": t["id"]})
    await ctx.reply(f"🧹 Баланс {ctx.bot.display(t)} обнулён")


@router.cmd("установить", "set", "поставить", rank=3)
async def cmd_set(ctx: Ctx) -> None:
    amount, t = amount_and_target(ctx, "Установить баланс: <code>установить 5000 @ник</code>")
    ctx.db.set_balance(int(t["id"]), amount, f"admin:set:{ctx.uid}")
    ctx.db.audit(ctx.uid, "set", {"user": t["id"], "amount": amount})
    await ctx.reply(f"⚙️ Баланс {ctx.bot.display(t)} теперь <b>{money(amount)}</b>")


@router.cmd("выдать номер", rank=3)
async def cmd_give_number(ctx: Ctx) -> None:
    from .handlers_spin import find_operator
    usage = "Выдать номер: <code>выдать номер @ник тесто [редкость]</code>"
    toks = list(ctx.args)
    target_tok = next((t for t in toks if t.startswith("@") or (t.isdigit() and len(t) >= 5)), None)
    t = ctx.target(target_tok, allow_self=True)
    toks = [x for x in toks if x != target_tok]
    op = None
    tier = None
    for x in toks:
        if op is None and find_operator(ctx, x):
            op = find_operator(ctx, x)
        elif parse_tier(x):
            tier = parse_tier(x)
    if op is None:
        ops = ctx.db.operators()
        if len(ops) == 1:
            op = ops[0]
        else:
            raise UserError(usage)
    c = ctx.db.country(op["country"])
    tier = tier or roll_tier(ctx.db.operator_odds(op))
    digits = generate_digits(tier, taken=lambda d: ctx.db.number_taken(fmt_number(c["dial"], op["prefix"], d)))
    n = ctx.db.add_number(int(t["id"]), c["code"], op["code"], fmt_number(c["dial"], op["prefix"], digits), digits, tier,
                          appraise(digits, tier), "admin")
    ctx.db.audit(ctx.uid, "give_number", {"user": t["id"], "number": n["full"], "tier": tier})
    await ctx.reply(f"📱 {ctx.bot.display(t)} получает номер <code>{n['full']}</code> · {tier_label(tier)} · "
                    f"{money(n['value'])} · <code>#{n['id']}</code>")


@router.cmd("забрать номер", "изъять", rank=3)
async def cmd_take_number(ctx: Ctx) -> None:
    n = ctx.db.find_number(ctx.arg(0))
    if not n:
        raise UserError("Номер не найден: <code>забрать номер #ID</code>")
    owner = ctx.db.user(int(n["user_id"]))
    ctx.db.remove_number(n["id"], "admin_take", ctx.uid)
    ctx.db.audit(ctx.uid, "take_number", {"user": n["user_id"], "number": n["full"]})
    await ctx.reply(f"🗑 Номер <code>{n['full']}</code> изъят у {ctx.bot.display(owner)}")


# ───────────────────────── промокоды ─────────────────────────
async def admin_promo(ctx: Ctx) -> None:
    if ctx.rank < 3:
        raise UserError("Промокоды создают админы")
    sub = ctx.arg(0).lower()
    if sub in ("создать", "create"):
        code, amt = ctx.arg(1), ctx.arg(2)
        if not code or not looks_like_amount(amt):
            raise UserError("<code>промо создать КОД 1000 [активаций] [дней]</code>")
        amount = parse_amount(amt, 0)
        uses = int(ctx.arg(3)) if ctx.arg(3).isdigit() else 1
        days = int(ctx.arg(4)) if ctx.arg(4).isdigit() else 0
        ctx.db.create_promo(code, amount, uses, days, ctx.uid)
        ctx.db.audit(ctx.uid, "promo_create", {"code": code.upper(), "amount": amount, "uses": uses, "days": days})
        await ctx.reply(f"🎟 Промокод <code>{esc(code.upper())}</code> · {money(amount)} · {uses} акт."
                        + (f" · {days} дн" if days else ""))
        if ctx.is_group:
            await ctx.delete_incoming()
        return
    if sub in ("удалить", "delete"):
        if not ctx.db.delete_promo(ctx.arg(1)):
            raise UserError("Такого промокода нет")
        ctx.db.audit(ctx.uid, "promo_delete", {"code": ctx.arg(1).upper()})
        await ctx.reply(f"🗑 Промокод {esc(ctx.arg(1).upper())} удалён")
        return
    rows = ctx.db.promos()
    lines = ["🎟 <b>ПРОМОКОДЫ</b>"]
    for p in rows:
        exp = f" · до {datetime_(p['expires'])}" if p["expires"] else ""
        lines.append(f"<code>{esc(p['code'])}</code> · {money(p['amount'])} · {p['uses_left']}/{p['total_uses']}{exp}")
    if not rows:
        lines.append("Пусто")
    if ctx.is_group:
        await ctx.dm(ctx.uid, "\n".join(lines))
        await ctx.reply("📩 Список отправил в личку")
    else:
        await ctx.reply("\n".join(lines))


# ───────────────────────── статистика, аудит ─────────────────────────
@router.cmd("статистика", "stats", "стат", rank=3)
async def cmd_stats(ctx: Ctx) -> None:
    s = ctx.db.stats()
    tiers = " ".join(f"{tier_label(t).split()[0]}{s['tiers'].get(t, 0)}" for t in TIERS)
    await ctx.reply("\n".join([
        "📊 <b>СТАТИСТИКА</b>",
        f"👥 {s['users']} игроков · +{s['new_today']} сегодня · активных {s['active_today']} · банов {s['banned']}",
        f"💰 В обороте {money(s['balance_sum'])} · ⭐ {s['stars']} ({s['payments']} платежей)",
        f"🎮 {s['games']} игр (+{s['games_today']} сегодня) · ставок {money(s['total_bet'])} · выплат {money(s['total_payout'])} · "
        f"доход {money(s['total_bet'] - s['total_payout'])}",
        f"📱 {s['spins']} прокруток · {s['numbers']} номеров на {money(s['numbers_value'])} · {tiers}",
        f"⚔️ открытых дуэлей {s['open_duels']} · 🏰 кланов {ctx.db.val('SELECT COUNT(*) FROM clans')} · "
        f"💬 чатов {ctx.db.val('SELECT COUNT(*) FROM chats WHERE allowed=1')}",
    ]))


@router.cmd("аудит", "audit", rank=5)
async def cmd_audit(ctx: Ctx) -> None:
    rows = ctx.db.audit_log(15)
    lines = ["🧾 <b>АУДИТ</b>"]
    for r in rows:
        a = ctx.db.user(int(r["admin_id"]))
        lines.append(f"{datetime_(r['created'])} · {esc(a['first_name']) if a else r['admin_id']} · <b>{esc(r['action'])}</b> "
                     f"<code>{esc(r['details'])[:60]}</code>")
    await ctx.reply("\n".join(lines))


@router.cmd("рефанд", "refund", "возврат", rank=5)
async def cmd_refund(ctx: Ctx) -> None:
    charge = ctx.arg(0)
    p = ctx.db.payment(charge) if charge else None
    if not p:
        raise UserError("Платёж не найден: <code>рефанд ID_платежа</code> (из чека)")
    if p["refunded"]:
        raise UserError("Этот платёж уже возвращён")
    try:
        await ctx.api.refund_stars(int(p["user_id"]), charge)
    except Exception as e:  # noqa: BLE001
        raise UserError(f"Telegram отклонил возврат: {e}")
    ctx.db.mark_refunded(charge)
    bal = ctx.db.balance(int(p["user_id"]))
    take = min(bal, int(p["rubles"]))
    if take > 0:
        ctx.db.change_balance(int(p["user_id"]), -take, f"refund:{charge}")
    ctx.db.audit(ctx.uid, "refund", {"charge": charge, "user": p["user_id"], "stars": p["stars"]})
    await ctx.reply(f"↩️ Возврат {p['stars']} ⭐ игроку <code>{p['user_id']}</code> выполнен · списано {money(take)}")
    await ctx.dm(int(p["user_id"]), f"↩️ Тебе вернули {p['stars']} ⭐ · с баланса списано {money(take)}")


# ───────────────────────── техработы, разделы, настройки ─────────────────────────
def on_off(tok: str) -> bool | None:
    t = (tok or "").lower()
    if t in ("вкл", "on", "1", "включить", "да"):
        return True
    if t in ("выкл", "off", "0", "выключить", "нет"):
        return False
    return None


@router.cmd("техработы", "maintenance", rank=4)
async def cmd_maint(ctx: Ctx) -> None:
    v = on_off(ctx.arg(0))
    if v is None:
        cur = int(ctx.setting("maintenance", 0))
        await ctx.reply(f"🛠 Техработы сейчас: <b>{'включены' if cur else 'выключены'}</b> · <code>техработы вкл|выкл</code>")
        return
    ctx.db.set_setting("maintenance", 1 if v else 0)
    ctx.db.audit(ctx.uid, "maintenance", v)
    await ctx.reply("🛠 <b>Техработы включены</b> — игроки видят заглушку" if v else "✅ <b>Техработы выключены</b> — бот работает")


@router.cmd("раздел", "section", rank=4)
async def cmd_section(ctx: Ctx) -> None:
    name = ctx.arg(0).lower().replace("ё", "е")
    key = SECTIONS.get(name) or (name if name in SECTION_TITLES else None)
    v = on_off(ctx.arg(1))
    if not key or v is None:
        raise UserError("<code>раздел краш выкл</code> · разделы: " + ", ".join(SECTIONS))
    ctx.db.set_setting(f"section:{key}", 1 if v else 0)
    ctx.db.audit(ctx.uid, "section", {"section": key, "on": v})
    await ctx.reply(f"{'✅' if v else '⛔'} Раздел «{SECTION_TITLES[key]}» {'включён' if v else 'выключен'}")


@router.cmd("разделы", "sections", rank=4)
async def cmd_sections(ctx: Ctx) -> None:
    lines = ["🧩 <b>РАЗДЕЛЫ</b> · <code>раздел краш выкл</code>"]
    for name, key in SECTIONS.items():
        lines.append(f"{'✅' if ctx.bot.section_enabled(key) else '⛔'} {name}")
    await ctx.reply("\n".join(lines))


@router.cmd("настройки", "settings", rank=4)
async def cmd_settings(ctx: Ctx) -> None:
    lines = ["⚙️ <b>НАСТРОЙКИ</b> · <code>настройка min_bet 100</code>"]
    for k in DEFAULT_SETTINGS:
        lines.append(f"<code>{k}</code> = <b>{esc(ctx.setting(k))}</b>")
    await ctx.reply("\n".join(lines))


@router.cmd("настройка", "setting", rank=4)
async def cmd_setting(ctx: Ctx) -> None:
    key, val = ctx.arg(0).lower(), ctx.arg(1)
    if key not in DEFAULT_SETTINGS or not val:
        raise UserError("Ключи: " + ", ".join(f"<code>{k}</code>" for k in DEFAULT_SETTINGS))
    if not val.lstrip("-").replace(".", "", 1).isdigit():
        raise UserError("Значение — число")
    ctx.db.set_setting(key, int(float(val)) if float(val) == int(float(val)) else float(val))
    ctx.db.audit(ctx.uid, "setting", {key: val})
    await ctx.reply(f"⚙️ <code>{key}</code> = <b>{esc(val)}</b>")


# ───────────────────────── рассылка ─────────────────────────
@router.cmd("рассылка", "broadcast", rank=4)
async def cmd_broadcast(ctx: Ctx) -> None:
    text = ctx.rest(0).strip()
    if not text:
        raise UserError("<code>рассылка текст сообщения</code> — уйдёт всем игрокам и во все чаты")
    ids = ctx.db.user_ids()
    chats = [int(c["id"]) for c in ctx.db.chats() if c["allowed"]]
    ctx.db.audit(ctx.uid, "broadcast", {"len": len(text), "users": len(ids)})
    await ctx.reply(f"📣 Рассылка запущена: {len(ids)} игроков + {len(chats)} чатов")

    async def job():
        ok = 0
        for cid in chats + ids:
            if await ctx.api.send(cid, f"📣 {esc(text)}"):
                ok += 1
            await asyncio.sleep(0.05)
        await ctx.dm(ctx.uid, f"📣 Рассылка завершена · доставлено {ok}/{len(ids) + len(chats)}")

    ctx.bot.spawn(job())


# ───────────────────────── операторы и страны ─────────────────────────
async def admin_operator(ctx: Ctx) -> None:
    from .handlers_spin import find_operator
    sub = ctx.arg(0).lower()
    if sub == "добавить":
        code, prefix, price = ctx.arg(1).lower(), ctx.arg(2).lstrip("+"), ctx.arg(3)
        emoji, name = ctx.arg(4), ctx.arg(5)
        country = ctx.arg(6).lower() if ctx.arg(6) else (ctx.db.countries() or [{"code": "ru"}])[0]["code"]
        if not (code and prefix.isdigit() and price.isdigit() and emoji and name):
            raise UserError("<code>оператор добавить код префикс цена эмодзи Название [страна]</code>")
        if ctx.db.operator(code):
            raise UserError("Такой код уже есть")
        if not ctx.db.country(country):
            raise UserError("Такой страны нет · «страны»")
        ctx.db.ex("INSERT INTO operators(code,country,name,emoji,prefix,price,odds,enabled,sort,created)"
                  " VALUES(?,?,?,?,?,?,?,1,?,?)",
                  (code, country, name, emoji, prefix, int(price), json.dumps(DEFAULT_ODDS), 99, int(time.time())))
        ctx.db.audit(ctx.uid, "op_add", code)
        await ctx.reply(f"✅ Оператор {emoji} {esc(name)} ({prefix}) добавлен · {money(int(price))} · шансы по умолчанию")
        return
    op = find_operator(ctx, ctx.arg(1)) or ctx.db.operator(ctx.arg(1).lower())
    if not op:
        raise UserError("Оператор не найден · «операторы»")
    code = op["code"]
    if sub == "цена":
        if not ctx.arg(2).isdigit():
            raise UserError("<code>оператор цена тесто 900</code>")
        ctx.db.ex("UPDATE operators SET price=? WHERE code=?", (int(ctx.arg(2)), code))
        await ctx.reply(f"✅ {op['emoji']} {esc(op['name'])}: цена прокрутки {money(int(ctx.arg(2)))}")
    elif sub == "шансы":
        vals = ctx.args[2:]
        try:
            nums = [float(v.replace(",", ".")) for v in vals]
        except ValueError:
            nums = []
        if len(nums) != len(TIERS):
            raise UserError("Шесть чисел (%): <code>оператор шансы тесто 55 27 12 4.5 1.2 0.3</code>")
        odds = normalize_odds(dict(zip(TIERS, nums)))
        ctx.db.ex("UPDATE operators SET odds=? WHERE code=?", (json.dumps(odds), code))
        await ctx.reply(f"✅ {op['emoji']} {esc(op['name'])}: шансы " + " · ".join(f"{tier_label(t).split()[0]} {odds[t]:g}%" for t in TIERS))
    elif sub in ("вкл", "выкл"):
        ctx.db.ex("UPDATE operators SET enabled=? WHERE code=?", (1 if sub == "вкл" else 0, code))
        await ctx.reply(f"{'✅' if sub == 'вкл' else '⛔'} {op['emoji']} {esc(op['name'])} {'включён' if sub == 'вкл' else 'выключен'}")
    elif sub == "удалить":
        ctx.db.ex("DELETE FROM operators WHERE code=?", (code,))
        await ctx.reply(f"🗑 Оператор {esc(op['name'])} удалён (номера игроков сохранены)")
    elif sub == "имя":
        name = ctx.rest(2).strip()
        if not name:
            raise UserError("<code>оператор имя тесто Новое имя</code>")
        ctx.db.ex("UPDATE operators SET name=? WHERE code=?", (name[:32], code))
        await ctx.reply(f"✅ Оператор переименован: {esc(name)}")
    elif sub == "эмодзи":
        if not ctx.arg(2):
            raise UserError("<code>оператор эмодзи тесто 🟩</code>")
        ctx.db.ex("UPDATE operators SET emoji=? WHERE code=?", (ctx.arg(2)[:8], code))
        await ctx.reply(f"✅ Эмодзи оператора: {ctx.arg(2)}")
    ctx.db.audit(ctx.uid, f"op_{sub}", {"code": code, "args": ctx.args[2:]})


@router.cmd("страны", "countries")
async def cmd_countries(ctx: Ctx) -> None:
    lines = ["🌍 <b>СТРАНЫ</b>"]
    for c in ctx.db.countries(only_enabled=False):
        n = len(ctx.db.operators(c["code"], only_enabled=False))
        lines.append(f"{c['flag']} {esc(c['name'])} ({c['dial']}) · <code>{c['code']}</code> · операторов: {n}"
                     + ("" if c["enabled"] else " · ⛔ выкл"))
    await ctx.reply("\n".join(lines))


@router.cmd("страна", "country", rank=4)
async def cmd_country(ctx: Ctx) -> None:
    sub = ctx.arg(0).lower()
    if sub == "добавить":
        code, dial, flag, name = ctx.arg(1).lower(), ctx.arg(2), ctx.arg(3), ctx.rest(4).strip()
        if not (code and dial.lstrip("+").isdigit() and flag and name):
            raise UserError("<code>страна добавить kz +7 🇰🇿 Казахстан</code>")
        ctx.db.ex("INSERT OR REPLACE INTO countries(code,name,flag,dial,enabled,sort) VALUES(?,?,?,?,1,?)",
                  (code, name[:32], flag, "+" + dial.lstrip("+"), 99))
        ctx.db.audit(ctx.uid, "country_add", code)
        await ctx.reply(f"✅ Страна {flag} {esc(name)} ({'+' + dial.lstrip('+')}) добавлена · теперь добавь операторов")
        return
    if sub in ("вкл", "выкл"):
        c = ctx.db.country(ctx.arg(1).lower())
        if not c:
            raise UserError("Страна не найдена · «страны»")
        ctx.db.ex("UPDATE countries SET enabled=? WHERE code=?", (1 if sub == "вкл" else 0, c["code"]))
        await ctx.reply(f"{'✅' if sub == 'вкл' else '⛔'} {c['flag']} {esc(c['name'])} {'включена' if sub == 'вкл' else 'выключена'}")
        return
    raise UserError("<code>страна добавить kz +7 🇰🇿 Казахстан</code> · <code>страна вкл|выкл kz</code>")


# ───────────────────────── чаты ─────────────────────────
@router.cmd("чат", "chat", "chatallow", rank=4)
async def cmd_chat(ctx: Ctx) -> None:
    """Бота может добавить в чат кто угодно; администрация ведёт только чёрный список."""
    sub = ctx.arg(0).lower()
    cid = int(ctx.arg(1)) if ctx.arg(1).lstrip("-").isdigit() else (ctx.chat_id if ctx.is_group else None)
    if sub in ("запретить", "бан", "deny", "выкл"):
        if cid is None:
            raise UserError("Напиши это в нужном чате или укажи ID: <code>чат запретить -100…</code>")
        ctx.db.ban_chat(cid, True)
        ctx.bot.active_chats.discard(cid)
        if ctx.is_group and cid == ctx.chat_id:
            ctx.bot.left_chats.add(cid)        # ответ ниже уже уведомляет этот чат
        else:
            ctx.bot.left_chats.discard(cid)    # при первом сообщении оттуда бот один раз сообщит и выйдет
        ctx.db.audit(ctx.uid, "chat_ban", cid)
        await ctx.reply(f"⛔ Чат <code>{cid}</code> в чёрном списке — выхожу, добавить бота туда больше нельзя")
        try:
            await ctx.api.call("leaveChat", chat_id=cid, _retries=0)
        except Exception:  # noqa: BLE001
            pass
        return
    if sub in ("разрешить", "разбан", "allow", "вкл"):
        if cid is None:
            raise UserError("Укажи ID чата: <code>чат разрешить -100…</code>")
        ctx.db.ban_chat(cid, False)
        ctx.bot.left_chats.discard(cid)
        if ctx.is_group and cid == ctx.chat_id:
            ctx.db.save_chat(cid, (ctx.message.get("chat") or {}).get("title", ""), ctx.uid, True)
            ctx.bot.active_chats.add(cid)
        ctx.db.audit(ctx.uid, "chat_unban", cid)
        await ctx.reply(f"✅ Чат <code>{cid}</code> разблокирован — бота снова можно добавить")
        return
    await cmd_chats(ctx)


@router.cmd("чаты", "chats", rank=4)
async def cmd_chats(ctx: Ctx) -> None:
    rows = ctx.db.chats()
    lines = ["💬 <b>ЧАТЫ</b> · <code>чат запретить [id]</code> · <code>чат разрешить [id]</code>",
             "<i>✅ бот работает · ▫️ бот вышел · ⛔ чёрный список</i>"]
    for c in rows:
        mark = "⛔" if c["banned"] else ("✅" if c["allowed"] else "▫️")
        lines.append(f"{mark} {esc(c['title']) or '—'} · <code>{c['id']}</code>"
                     + (f" · добавил {c['added_by']}" if c["added_by"] else ""))
    if not rows:
        lines.append("Пока нет — бота ещё никуда не добавляли")
    await ctx.reply("\n".join(lines))


