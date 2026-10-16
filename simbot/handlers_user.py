"""Пользовательские команды: старт, помощь, правила, баланс, профиль, номера, передача, топ, бонус, промо, пополнение."""
from __future__ import annotations

import os
import secrets
import time

from . import VERSION
from .config import (CRASH_EDGE, DICE_EXACT, DICE_HALF, MINES_EDGE, RANK_EMOJI, RANK_NAMES, SLOT_JACKPOT, SLOT_TRIPLE,
                     SLOT_TWO_SEVENS, STARS_RATE, UPGRADER_EDGE, UPGRADE_NUM_EDGE)
from .core import Ctx, UserError, router
from .handlers_games import looks_like_amount
from .numbers import RNG, TIERS, parse_tier, tier_label, value_range
from .ui import BRAND, approx, date, datetime_, esc, money, num, number_card, number_line, pct, plural

PAGE = 8


# ───────────────────────── старт и помощь ─────────────────────────
def help_text(ctx: Ctx) -> str:
    lines = [
        f"📱 <b>{BRAND}</b> — команды пишутся прямо в чат",
        "🎮 <b>Игры:</b> <code>краш 500</code> · <code>мины 500 3</code> · <code>слоты 100</code> · "
        "<code>кубик 100 чёт</code> · <code>ап 100 2</code> · <code>дуэль 1000</code> · «забрать»",
        "📱 <b>Номера:</b> <code>крутить тесто</code> · «операторы» · «инвентарь» · <code>номер #ID</code> · "
        "<code>передать #ID @ник</code> · <code>ап #ID эпик</code>",
        "🏪 <b>Торговля:</b> <code>продать #ID 5000</code> · <code>продать все да</code> · <code>купить #ID</code> · «рынок» · "
        "<code>обмен #мой #его</code> · «торговля» — подробнее",
        "👤 <b>Я:</b> «баланс» · «профиль» · «бонус» · «история» · <code>промо КОД</code> · "
        "<code>пополнить 5</code> (⭐ в личке) · «id»",
        "🏆 <b>Топ:</b> «топ» · «топ коллекция» · «топ выигрыши» · «топ кланы»",
        "🏰 <b>Кланы:</b> «клан» · <code>клан создать ТЕГ Название</code> · «клан топ»",
        "📜 «правила» · «игры» — подробнее про игры",
    ]
    if ctx.rank >= 1:
        lines.append("🛠 «админка» — команды управления для твоего титула")
    return "\n".join(lines)


@router.cmd("start", "старт", "меню", "menu")
async def cmd_start(ctx: Ctx) -> None:
    user = ctx.user
    is_new = ctx.state_data.get("__new__", False)
    ref_from = None
    a = ctx.arg(0)
    if a.startswith("ref_") and a[4:].isdigit():
        ref_from = int(a[4:])
    if is_new or (user["created"] >= int(time.time()) - 5 and not user["balance"]):
        bonus = int(ctx.setting("welcome_bonus", 5000))
        with ctx.db.tx():
            if bonus > 0 and not ctx.db.one("SELECT 1 FROM ledger WHERE user_id=? AND reason='welcome'", (ctx.uid,)):
                ctx.db.change_balance(ctx.uid, bonus, "welcome")
            if ref_from and ref_from != ctx.uid and ctx.db.user(ref_from) and not user["ref_by"]:
                rb = int(ctx.setting("ref_bonus", 1000))
                ctx.db.ex("UPDATE users SET ref_by=? WHERE id=?", (ref_from, ctx.uid))
                ctx.db.ex("UPDATE users SET refs=refs+1 WHERE id=?", (ref_from,))
                if rb > 0:
                    ctx.db.change_balance(ref_from, rb, f"ref:{ctx.uid}")
                    await ctx.dm(ref_from, f"🤝 По твоей ссылке пришёл <b>{esc(user['first_name'])}</b> · +{money(rb)}")
        user = ctx.refresh_user()
        head = f"🎉 <b>Добро пожаловать, {esc(user['first_name'])}!</b> Стартовый бонус <b>{money(user['balance'])}</b> уже на счету."
    else:
        head = f"👋 <b>{esc(user['first_name'])}</b> · 💰 {money(user['balance'])}"
    if not ctx.is_group:
        await send_banner(ctx)
    await ctx.reply(head + "\n" + help_text(ctx))


async def send_banner(ctx: Ctx) -> None:
    bot = ctx.bot
    fid = bot.banner_file_id or bot.db.setting("banner_file_id", "")
    if fid:
        if await ctx.api.photo(ctx.chat_id, str(fid)):
            return
        bot.db.set_setting("banner_file_id", "")
        bot.banner_file_id = None
    path = bot.cfg.banner_path
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
        msg = await ctx.api.photo(ctx.chat_id, data)
        if msg and msg.get("photo"):
            bot.banner_file_id = msg["photo"][-1].get("file_id")
            if bot.banner_file_id:
                bot.db.set_setting("banner_file_id", bot.banner_file_id)


@router.cmd("помощь", "help", "команды", "хелп", "справка")
async def cmd_help(ctx: Ctx) -> None:
    await ctx.reply(help_text(ctx))


def rules_text(ctx: Ctx) -> str:
    ops = ctx.db.operators()
    op_line = " · ".join(f"{o['emoji']} {esc(o['name'])} {money(o['price'])}" for o in ops) or "—"
    return "\n".join([
        "📜 <b>ПРАВИЛА</b>",
        "<b>Основные:</b> Бот был создан по мотивам ботов: casinobot, teslabot. Играя в нашу игру — вы автоматически "
        "соглашаетесь с правилами.",
        f"💰 Валюта — рубли (₽). Пополнение: ⭐ Telegram Stars, 1 ⭐ = {num(STARS_RATE)} ₽, вывода нет — только игра.",
        f"🎲 Честность: краш (преимущество {pct(CRASH_EDGE * 100)}), мины ({pct(MINES_EDGE * 100)}), "
        f"кубик (×{DICE_HALF} / ×{DICE_EXACT}), слоты (777 ×{SLOT_JACKPOT}, тройка ×{SLOT_TRIPLE}, две 7 ×{SLOT_TWO_SEVENS}), "
        f"апгрейд ({pct(UPGRADER_EDGE * 100)} / номера {pct(UPGRADE_NUM_EDGE * 100)}), дуэль — комиссия "
        f"{ctx.setting('duel_fee', 5)}% с банка.",
        f"📱 Номера: {op_line}. Редкость и цена каждого номера индивидуальны. Номера можно передавать, продавать и апгрейдить.",
        f"🚫 Запрещено: мультиаккаунты, баги/абуз, оскорбления, спам, попрошайничество. Наказания: варн → мут → бан. "
        f"{ctx.setting('warn_limit', 3)} варна = бан.",
        "🛡 Администрация не возвращает проигранное и не несёт ответственности за передачи между игроками.",
        "⭐ Возврат звёзд — только за незачисленные платежи («платежи»).",
    ])


@router.cmd("правила", "rules")
async def cmd_rules(ctx: Ctx) -> None:
    await ctx.reply(rules_text(ctx))


# ───────────────────────── баланс и профиль ─────────────────────────
@router.cmd("баланс", "balance", "б", "бал", "деньги")
async def cmd_balance(ctx: Ctx) -> None:
    bal = ctx.db.balance(ctx.uid)
    cnt = ctx.db.numbers_count(ctx.uid)
    await ctx.reply(f"💰 {ctx.who}: <b>{money(bal)}</b> · 📱 {cnt} {plural(cnt, 'номер', 'номера', 'номеров')} на "
                    f"{money(ctx.db.collection_value(ctx.uid))}")


def rank_badge(rank: int) -> str:
    return f"{RANK_EMOJI.get(rank, '👤')} {RANK_NAMES.get(rank, 'Игрок')}"


def profile_text(ctx: Ctx, user) -> str:
    uid = int(user["id"])
    games, wins = int(user["games"]), int(user["wins"])
    wr = f"{wins * 100 // games}%" if games else "—"
    clan = ctx.db.user_clan(uid)
    rank = ctx.bot.rank(uid)
    lines = [
        f"👤 {ctx.bot.display(user)} · <code>{uid}</code>" + (f" · @{esc(user['username'])}" if user["username"] else ""),
        f"{rank_badge(rank)}" + (f" · 🏰 [{esc(clan['tag'])}] {esc(clan['name'])}" if clan else ""),
        f"💰 <b>{money(user['balance'])}</b> · 📱 {ctx.db.numbers_count(uid)} на {money(ctx.db.collection_value(uid))}",
        f"🎮 {games} {plural(games, 'игра', 'игры', 'игр')} · {wr} побед · 🏆 рекорд +{money(user['biggest_win'])}",
        f"🎰 {user['spins']} {plural(user['spins'], 'прокрутка', 'прокрутки', 'прокруток')} · ⭐ {user['stars_paid']} · "
        f"🤝 {user['refs']} {plural(user['refs'], 'друг', 'друга', 'друзей')}",
        f"📅 с {date(user['created'])}" + (f" · ⚠️ варнов: {user['warns']}" if user["warns"] else ""),
    ]
    if user["banned"]:
        lines.append(f"🚫 Забанен{(': ' + esc(user['ban_reason'])) if user['ban_reason'] else ''}")
    if int(user["muted_until"] or 0) > time.time():
        lines.append(f"🔇 Мут до {datetime_(user['muted_until'])}")
    return "\n".join(lines)


@router.cmd("профиль", "profile", "я", "стата", "me")
async def cmd_profile(ctx: Ctx) -> None:
    tok = ctx.arg(0)
    if ctx.has_target_token(tok):
        target = ctx.target(tok, allow_self=True)
    else:
        target = ctx.user
    await ctx.reply(profile_text(ctx, target))


@router.cmd("id", "ид", "айди")
async def cmd_id(ctx: Ctx) -> None:
    if ctx.reply_user:
        await ctx.reply(f"🪪 {esc(ctx.reply_user.get('first_name', ''))}: <code>{ctx.reply_user.get('id')}</code>")
        return
    await ctx.reply(f"🪪 Твой ID: <code>{ctx.uid}</code> · чат: <code>{ctx.chat_id}</code>")


@router.cmd("история", "history")
async def cmd_history(ctx: Ctx) -> None:
    rows = ctx.db.history(ctx.uid, 10)
    if not rows:
        raise UserError("Истории пока нет — сыграй что-нибудь 🎮")
    kinds = {"crash": "🚀", "mines": "💣", "slots": "🎰", "dice": "🎲", "upgrader": "🔼", "upgrade_num": "🔼", "duel": "⚔️"}
    lines = ["📜 <b>Последние игры</b>"]
    for g in rows:
        d = int(g["payout"]) - int(g["bet"])
        sign = "+" if d >= 0 else "−"
        lines.append(f"{kinds.get(g['kind'], '🎮')} {datetime_(g['finished'] or g['created'])} · {money(g['bet'])} → "
                     f"<b>{sign}{money(abs(d))}</b> · <i>{esc(g['result'])[:40]}</i>")
    await ctx.reply("\n".join(lines))


# ───────────────────────── номера ─────────────────────────
@router.cmd("инвентарь", "инв", "номера", "numbers", "мои", "коллекция", "nums", "inventory")
async def cmd_numbers(ctx: Ctx) -> None:
    target = ctx.user
    page = 1
    for tok in ctx.args:
        if tok.isdigit() and int(tok) < 1000:
            page = max(1, int(tok))
        elif ctx.has_target_token(tok):
            target = ctx.target(tok, allow_self=True)
    if ctx.reply_user and not ctx.reply_user.get("is_bot"):
        target = ctx.target(None, allow_self=True)
    uid = int(target["id"])
    total = ctx.db.numbers_count(uid)
    mine = uid == ctx.uid
    if not total:
        raise UserError("Инвентарь пуст · <code>крутить тесто</code> — прокрутить номер" if mine else "У игрока нет номеров")
    pages = (total + PAGE - 1) // PAGE
    page = min(page, pages)
    rows = ctx.db.user_numbers(uid, PAGE, (page - 1) * PAGE)
    ops = {o["code"]: o for o in ctx.db.operators(only_enabled=False)}
    listed = {m["number_id"]: int(m["price"]) for m in ctx.db.user_listings(uid)}
    lines = [f"🎒 <b>ИНВЕНТАРЬ</b> {ctx.bot.display(target)} · {total} {plural(total, 'номер', 'номера', 'номеров')} на "
             f"<b>{money(ctx.db.collection_value(uid))}</b>" + (f" · стр. {page}/{pages}" if pages > 1 else "")]
    for n in rows:
        line = number_line(n, ops.get(n["operator"]))
        if n["id"] in listed:
            line += f" · 🏷 {money(listed[n['id']])}"
        lines.append(line)
    if pages > 1 and page < pages:
        lines.append(f"<i>дальше: «инвентарь {page + 1}»</i>")
    if mine:
        lines.append("<i><code>номер #ID</code> · <code>передать #ID @ник</code> · <code>продать #ID 5000</code> · "
                     "<code>продать #ID да</code> (боту) · <code>ап #ID</code> · «торговля»</i>")
    else:
        lines.append(f"<i><code>обмен #мой #его</code> — предложить обмен · <code>продать #ID 5000 @{esc(target['username'])}</code></i>"
                     if target["username"] else "<i><code>обмен #мой #его</code> — предложить обмен</i>")
    await ctx.reply("\n".join(lines))


@router.cmd("номер", "number")
async def cmd_number(ctx: Ctx) -> None:
    n = ctx.db.find_number(ctx.arg(0))
    if not n:
        raise UserError("Номер не найден · <code>номер #ID</code>")
    owner = ctx.db.user(n["user_id"])
    op, c = ctx.db.operator(n["operator"]), ctx.db.country(n["country"])
    m = ctx.db.listing(n["id"])
    extra = f"👤 Владелец: {ctx.bot.display(owner)}"
    if m:
        extra += (f"\n🏷 Продаётся за <b>{money(m['price'])}</b>" + (" (адресно)" if m["buyer"] else "")
                  + f" · <code>купить #{n['id']}</code>")
    await ctx.reply(number_card(n, op, c, extra=extra))


def number_from_args(ctx: Ctx):
    """Номер из аргументов: #ID либо полный номер (можно с пробелами: +7 902 123-45-67). Возвращает (номер, остаток)."""
    args = list(ctx.args)
    if not args:
        return None, args
    n = ctx.db.find_number(args[0])
    if n:
        return n, args[1:]
    k = 0
    while k < len(args) and all(ch.isdigit() or ch in "+-() " for ch in args[k]):
        k += 1
    if k:
        n = ctx.db.find_number("".join(args[:k]))
        if n:
            return n, args[k:]
    return None, args[1:]


@router.cmd("передать", "подарить", "отдать", "transfer", "gift", section="transfer")
async def cmd_transfer(ctx: Ctx) -> None:
    usage = "Передать номер: <code>передать #ID @ник</code> или ответом на сообщение игрока: <code>передать #ID</code>"
    if not ctx.arg(0):
        raise UserError(usage)
    n, rest = number_from_args(ctx)
    if not n or n["user_id"] != ctx.uid:
        raise UserError("Это не твой номер · «номера» — список с ID")
    to = ctx.target(rest[0] if rest else None)
    if to["banned"]:
        raise UserError("Этот игрок заблокирован")
    if not ctx.db.transfer_number(n["id"], ctx.uid, int(to["id"]), "gift"):
        raise UserError("Не удалось передать — номер уже ушёл")
    await ctx.reply(f"🎁 {ctx.who} передал <code>{n['full']}</code> ({tier_label(n['tier'])}, {money(n['value'])}) → "
                    f"{ctx.bot.display(to)}")
    if ctx.is_group or int(to["id"]) != ctx.chat_id:
        await ctx.dm(int(to["id"]), f"🎁 <b>Тебе подарили номер!</b> <code>{n['full']}</code> · {tier_label(n['tier'])} · "
                                    f"{money(n['value'])} · от {esc(ctx.user['first_name'])}")


SELL_ALL_WORDS = ("все", "всё", "all")


async def sell_all(ctx: Ctx) -> None:
    """«продать все [редкость] [да]» — продать боту все номера (или одной редкости) одним махом."""
    rate = int(ctx.setting("sell_rate", 100))
    confirm = False
    tier = None
    for tok in ctx.args[1:]:
        t = tok.lower()
        if t in ("да", "yes", "+"):
            confirm = True
        elif parse_tier(t):
            tier = parse_tier(t)
        else:
            raise UserError("Не понял · <code>продать все</code> · <code>продать все обычные да</code>")
    rows = [n for n in ctx.db.user_numbers(ctx.uid, 10**6) if tier is None or n["tier"] == tier]
    if not rows:
        raise UserError("Нечего продавать · инвентарь пуст" if tier is None else f"Нет номеров: {tier_label(tier)}")
    price_of = {n["id"]: int(n["value"]) * rate // 100 for n in rows}
    total = sum(price_of.values())
    listed = {m["number_id"] for m in ctx.db.user_listings(ctx.uid)}
    if not confirm:
        by_tier = {t: [0, 0] for t in TIERS}
        for n in rows:
            by_tier[n["tier"]][0] += 1
            by_tier[n["tier"]][1] += price_of[n["id"]]
        lines = [f"💸 <b>Продать всё боту</b>{' · ' + tier_label(tier) if tier else ''}"]
        for t in TIERS:
            if by_tier[t][0]:
                lines.append(f"{tier_label(t)} × {by_tier[t][0]} · {money(by_tier[t][1])}")
        lines.append(f"Итого: <b>+{money(total)}</b> за {len(rows)} шт." + (f" · 🏷 {len(listed & set(price_of))} снимутся с рынка"
                                                                    if listed & set(price_of) else ""))
        lines.append(f"Подтверди: <code>продать все{' ' + ctx.arg(1) if tier else ''} да</code>")
        await ctx.reply("\n".join(lines))
        return
    with ctx.db.tx():
        for n in rows:
            if not ctx.db.remove_number(n["id"], "sell", ctx.uid):
                raise UserError("Инвентарь изменился — попробуй ещё раз")
        bal = ctx.db.change_balance(ctx.uid, total, f"sell_all:{len(rows)}")
    await ctx.reply(f"💸 {ctx.who} продал <b>{len(rows)}</b> {plural(len(rows), 'номер', 'номера', 'номеров')} "
                    f"за <b>+{money(total)}</b> · 💰 {money(bal)}")


@router.cmd("продать", "sell")
async def cmd_sell(ctx: Ctx) -> None:
    if ctx.arg(0).lower() in SELL_ALL_WORDS:
        await sell_all(ctx)
        return
    n = ctx.db.find_number(ctx.arg(0))
    if not n or n["user_id"] != ctx.uid:
        raise UserError("Это не твой номер · <code>продать #ID</code> (боту) · <code>продать #ID 5000</code> (на рынок)")
    a1 = ctx.arg(1)
    if a1 and a1.lower() not in ("да", "yes", "+") and (looks_like_amount(a1) or ctx.has_target_token(a1)):
        if not ctx.bot.section_enabled("market"):
            raise UserError("Рынок временно отключён")
        from .handlers_market import list_for_sale
        if looks_like_amount(a1):
            await list_for_sale(ctx, n, a1, ctx.arg(2))
        else:
            if not looks_like_amount(ctx.arg(2)):
                raise UserError("Укажи цену: <code>продать #ID 5000 @ник</code>")
            await list_for_sale(ctx, n, ctx.arg(2), a1)
        return
    rate = int(ctx.setting("sell_rate", 100))
    price = int(n["value"]) * rate // 100
    if a1.lower() not in ("да", "yes", "+"):
        await ctx.reply(f"💸 <code>{n['full']}</code> ({tier_label(n['tier'])}) бот купит за <b>{money(price)}</b>.\n"
                        f"Подтверди: <code>продать #{n['id']} да</code> · или на рынок игрокам: "
                        f"<code>продать #{n['id']} {int(n['value']) * 2}</code>")
        return
    with ctx.db.tx():
        if not ctx.db.remove_number(n["id"], "sell", ctx.uid):
            raise UserError("Номер уже продан")
        bal = ctx.db.change_balance(ctx.uid, price, f"sell:{n['id']}")
    await ctx.reply(f"💸 {ctx.who} продал <code>{n['full']}</code> за <b>+{money(price)}</b> · 💰 {money(bal)}")


# ───────────────────────── топ ─────────────────────────
TOP_KINDS = {"баланс": "balance", "богачи": "balance", "коллекция": "collection", "номера": "collection",
             "выигрыши": "won", "выигрыш": "won", "прокрутки": "spins", "спины": "spins", "кланы": "clans", "клан": "clans"}
TOP_TITLES = {"balance": "💰 ТОП ПО БАЛАНСУ", "collection": "📱 ТОП КОЛЛЕКЦИЙ", "won": "🏆 ТОП ПО ВЫИГРЫШАМ",
              "spins": "🎰 ТОП ПО ПРОКРУТКАМ", "clans": "🏰 ТОП КЛАНОВ"}
MEDALS = ["🥇", "🥈", "🥉"]


@router.cmd("топ", "top", "рейтинг")
async def cmd_top(ctx: Ctx) -> None:
    kind = TOP_KINDS.get(ctx.arg(0).lower().replace("ё", "е"), "balance")
    if kind == "clans":
        from .handlers_clan import clans_top_text
        await ctx.reply(clans_top_text(ctx))
        return
    rows = ctx.db.top(kind, 10)
    lines = [f"<b>{TOP_TITLES[kind]}</b>"]
    for i, r in enumerate(rows):
        medal = MEDALS[i] if i < 3 else f"{i + 1}."
        tag = f"[{esc(r['tag'])}] " if r["tag"] else ""
        v = money(r["v"]) if kind != "spins" else f"{r['v']} шт"
        me = " ← ты" if r["id"] == ctx.uid else ""
        lines.append(f"{medal} {tag}{esc(r['first_name'])} — <b>{v}</b>{me}")
    if not rows:
        lines.append("Пока пусто")
    lines.append("<i>топ коллекция · топ выигрыши · топ прокрутки · топ кланы</i>")
    await ctx.reply("\n".join(lines))


# ───────────────────────── бонус ─────────────────────────
@router.cmd("бонус", "bonus", "daily", "ежедневный", section="bonus")
async def cmd_bonus(ctx: Ctx) -> None:
    user = ctx.refresh_user()
    left = 86400 - (int(time.time()) - int(user["last_daily"]))
    if left > 0:
        h, m = left // 3600, (left % 3600) // 60
        raise UserError(f"Бонус уже забран. Следующий через <b>{h} ч {m} мин</b>")
    lo, hi = int(ctx.setting("daily_min", 500)), int(ctx.setting("daily_max", 2500))
    streak = int(user["daily_streak"]) + 1 if int(time.time()) - int(user["last_daily"]) < 2 * 86400 else 1
    amount = RNG.randint(min(lo, hi), max(lo, hi))
    streak_bonus = min(streak - 1, 7) * 10
    amount = amount * (100 + streak_bonus) // 100
    with ctx.db.tx():
        ctx.db.ex("UPDATE users SET last_daily=?, daily_streak=? WHERE id=?", (int(time.time()), streak, ctx.uid))
        bal = ctx.db.change_balance(ctx.uid, amount, f"daily:{streak}")
    await ctx.reply(f"🎁 {ctx.who} · <b>+{money(amount)}</b>" + (f" · 🔥 серия {streak} дн (+{streak_bonus}%)" if streak > 1 else "")
                    + f" · 💰 {money(bal)}")


# ───────────────────────── промокоды ─────────────────────────
@router.cmd("промо", "promo", "промокод", section="promo")
async def cmd_promo(ctx: Ctx) -> None:
    sub = ctx.arg(0).lower()
    if sub in ("создать", "удалить", "список", "create", "delete", "list"):
        from .handlers_admin import admin_promo
        await admin_promo(ctx)
        return
    if not sub:
        raise UserError("Промокод: <code>промо КОД</code>")
    try:
        amount = ctx.db.redeem_promo(sub, ctx.uid)
    except ValueError as e:
        raise UserError(str(e))
    await ctx.reply(f"🎟 {ctx.who} активировал промокод · <b>+{money(amount)}</b> · 💰 {money(ctx.db.balance(ctx.uid))}")
    if ctx.is_group:
        await ctx.delete_incoming()


# ───────────────────────── пополнение ─────────────────────────
@router.cmd("пополнить", "deposit", "донат", "звезды", "звёзды", "pay", "stars", section="deposit")
async def cmd_deposit(ctx: Ctx) -> None:
    if ctx.is_group:
        await ctx.reply(f"⭐ Пополнение — в личке с ботом: @{esc(ctx.bot.me.get('username', ''))} → "
                        f"<code>пополнить 5</code> (1 ⭐ = {num(STARS_RATE)} ₽)")
        return
    tok = ctx.arg(0)
    lo, hi = int(ctx.setting("min_stars", 1)), int(ctx.setting("max_stars", 10000))
    if not tok or not tok.isdigit():
        await ctx.reply(f"⭐ <b>ПОПОЛНЕНИЕ</b> · 1 ⭐ = {num(STARS_RATE)} ₽ · от {lo} до {num(hi)} ⭐\n"
                        f"Напиши <code>пополнить 5</code> → счёт на 5 ⭐ = {money(5 * STARS_RATE)}")
        return
    await send_invoice(ctx, int(tok))


async def send_invoice(ctx: Ctx, stars: int) -> None:
    lo, hi = int(ctx.setting("min_stars", 1)), int(ctx.setting("max_stars", 10000))
    if stars < lo or stars > hi:
        raise UserError(f"Сумма должна быть от {lo} до {num(hi)} ⭐")
    payload = f"dep:{ctx.uid}:{stars}:{secrets.token_hex(4)}"
    ctx.db.create_invoice(payload, ctx.uid, stars)
    rub = stars * STARS_RATE
    try:
        await ctx.api.invoice(
            ctx.chat_id, title=f"Пополнение на {num(rub)} ₽",
            description=f"{stars} ⭐ → {num(rub)} ₽ на игровой баланс · {BRAND}",
            payload=payload, stars=stars, label=f"{stars} ⭐",
        )
    except Exception as e:  # noqa: BLE001
        raise UserError(f"Не удалось создать счёт: {e}")


@router.on_pre_checkout
async def on_pre_checkout(ctx: Ctx) -> None:
    q = ctx.update.get("pre_checkout_query") or {}
    qid = str(q.get("id", ""))
    payload = str(q.get("invoice_payload") or "")
    inv = ctx.db.invoice(payload)
    if not inv or inv["user_id"] != ctx.uid:
        await ctx.api.answer_pre_checkout(qid, False, "Счёт не найден. Создай новый: «пополнить 5»")
        return
    if str(q.get("currency", "XTR")) != "XTR" or int(q.get("total_amount", inv["stars"])) != int(inv["stars"]):
        await ctx.api.answer_pre_checkout(qid, False, "Сумма счёта изменилась. Создай новый: «пополнить 5»")
        return
    if inv["status"] == "paid":
        await ctx.api.answer_pre_checkout(qid, False, "Этот счёт уже оплачен")
        return
    if ctx.user["banned"]:
        await ctx.api.answer_pre_checkout(qid, False, "Аккаунт заблокирован")
        return
    await ctx.api.answer_pre_checkout(qid, True)


@router.on_payment
async def on_payment(ctx: Ctx) -> None:
    sp = ctx.message.get("successful_payment") or {}
    payload = str(sp.get("invoice_payload") or "")
    charge = str(sp.get("telegram_payment_charge_id") or sp.get("provider_payment_charge_id")
                 or f"{payload}:{ctx.update.get('update_id')}")
    stars = int(sp.get("total_amount") or 0)
    inv = ctx.db.invoice(payload)
    if not stars and inv:
        stars = int(inv["stars"])
    if stars <= 0:
        await ctx.reply("⚠️ Платёж получен, но сумма не распознана. Напиши «платежи», разберёмся.")
        await ctx.bot.notify_admins(f"⚠️ Платёж без суммы от <code>{ctx.uid}</code>: <code>{esc(sp)}</code>")
        return
    rub = stars * STARS_RATE
    if not ctx.db.apply_payment(charge, ctx.uid, stars, rub, payload):
        await ctx.reply("✅ Этот платёж уже был зачислен.")
        return
    bal = ctx.db.balance(ctx.uid)
    await ctx.reply(f"✅ <b>Оплачено: {stars} ⭐ → +{money(rub)}</b> · 💰 {money(bal)}\n🧾 <code>{esc(charge)}</code>")
    await ctx.bot.notify_admins(f"💸 <b>Пополнение</b>: {esc(ctx.user['first_name'])} <code>{ctx.uid}</code> · "
                                f"⭐ {stars} → {money(rub)} · 🧾 <code>{esc(charge)}</code>")


@router.cmd("платежи", "paysupport")
async def cmd_paysupport(ctx: Ctx) -> None:
    admins = ", ".join(f"<code>{a}</code>" for a in sorted(ctx.bot.cfg.admin_ids))
    await ctx.reply(f"🆘 Звёзды списались, а рубли не пришли? Напиши владельцу (ID {admins}): свой ID "
                    f"<code>{ctx.uid}</code>, сумму ⭐ и номер транзакции из чека. "
                    f"<i>Возврат — только за незачисленные платежи.</i>")


@router.cmd("версия", "version", "about")
async def cmd_version(ctx: Ctx) -> None:
    await ctx.reply(f"📱 <b>{BRAND}</b> v{VERSION}")


@router.cmd("редкости", "редкость", "tiers")
async def cmd_tiers(ctx: Ctx) -> None:
    lines = ["💎 <b>РЕДКОСТИ НОМЕРОВ</b>"]
    for t in TIERS:
        lo, hi = value_range(t)
        lines.append(f"{tier_label(t)} · {approx(lo)}–{approx(hi)} ₽")
    lines.append("<i>Цена каждого номера индивидуальна: зависит от красоты цифр</i>")
    await ctx.reply("\n".join(lines))
