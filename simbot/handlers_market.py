"""Инвентарь и торговля номерами: рынок (лоты), адресные предложения, обмен номер-на-номер."""
from __future__ import annotations

from .core import Ctx, UserError, router
from .db import InsufficientFunds
from .handlers_games import looks_like_amount, parse_amount
from .numbers import TIER, tier_label
from .ui import esc, money, plural

PAGE = 8
TRADE_TTL = 30 * 60
MAX_PRICE = 100_000_000

MARKET_HELP = "\n".join([
    "🏪 <b>РЫНОК НОМЕРОВ</b>",
    "<code>продать #ID 5000</code> — выставить лот",
    "<code>продать #ID 5000 @ник</code> — предложить игроку (или ответом)",
    "<code>купить #ID</code> — купить лот · <code>рынок [стр]</code> — все лоты",
    "<code>снять #ID</code> — убрать с продажи · <code>лоты</code> — мои лоты и предложения",
    "<code>обмен #мой #его [доплата]</code> — обмен номерами · <code>обмен принять</code> / <code>обмен отклонить</code> / <code>обмены</code>",
    "<code>продать #ID да</code> — продать боту · <code>продать все да</code> — продать боту всё (или <code>продать все обычные да</code>)",
    "<code>передать #ID @ник</code> — подарить",
])


def num_short(n) -> str:
    return f"{TIER[n['tier']]['emoji']} <code>{n['full']}</code> (<code>#{n['number_id'] if 'number_id' in n.keys() else n['id']}</code>, {money(n['value'])})"


def my_number(ctx: Ctx, token: str | None):
    n = ctx.db.find_number(token or "")
    if not n or int(n["user_id"]) != ctx.uid:
        raise UserError("Это не твой номер · «инвентарь» — список с ID")
    return n


# ───────────────────────── лоты ─────────────────────────
async def list_for_sale(ctx: Ctx, n, price_tok: str, target_tok: str | None) -> None:
    price = parse_amount(price_tok, 0)
    if price < 1 or price > MAX_PRICE:
        raise UserError("Цена от 1 до 100 000 000 ₽")
    buyer = None
    if ctx.has_target_token(target_tok):
        buyer = int(ctx.target(target_tok)["id"])
    ctx.db.list_number(n["id"], ctx.uid, price, buyer)
    fee = int(ctx.setting("market_fee", 5))
    if buyer:
        b = ctx.db.user(buyer)
        await ctx.reply(f"🏷 {ctx.who} предлагает {ctx.bot.display(b)} купить {num_short(n)} за <b>{money(price)}</b>\n"
                        f"{ctx.bot.display(b, link=False)}, напиши <code>купить #{n['id']}</code>")
        if ctx.is_group:
            await ctx.dm(buyer, f"🏷 {esc(ctx.user['first_name'])} предлагает тебе купить <code>{n['full']}</code> "
                                f"({tier_label(n['tier'])}) за {money(price)} · <code>купить #{n['id']}</code>")
    else:
        await ctx.reply(f"🏷 {ctx.who} выставил на рынок {num_short(n)} за <b>{money(price)}</b> · комиссия {fee}%\n"
                        f"Купить: <code>купить #{n['id']}</code> · снять: <code>снять #{n['id']}</code>")


@router.cmd("купить", "buy", section="market")
async def cmd_buy(ctx: Ctx) -> None:
    n = ctx.db.find_number(ctx.arg(0))
    if not n:
        raise UserError("Номер не найден · «рынок» — список лотов")
    m = ctx.db.listing(n["id"])
    if not m:
        raise UserError("Этот номер не продаётся")
    if m["buyer"] is not None and int(m["buyer"]) != ctx.uid:
        raise UserError("Это адресное предложение другому игроку")
    if int(m["seller"]) == ctx.uid:
        raise UserError("Это твой собственный лот · снять: <code>снять #" + n["id"] + "</code>")
    if ctx.arg(1).lower() not in ("да", "yes", "+") and int(m["price"]) >= 50_000:
        await ctx.reply(f"💳 {num_short(n)} за <b>{money(m['price'])}</b> — дорогая покупка.\n"
                        f"Подтверди: <code>купить #{n['id']} да</code>")
        return
    fee = int(ctx.setting("market_fee", 5))
    try:
        res = ctx.db.buy_number(n["id"], ctx.uid, fee)
    except ValueError as e:
        raise UserError(str(e))
    except InsufficientFunds:
        raise UserError(f"Нужно {money(m['price'])} на балансе")
    seller = ctx.db.user(res["seller"])
    await ctx.reply(f"🤝 {ctx.who} купил {num_short(n)} у {ctx.bot.display(seller)} за <b>{money(res['price'])}</b> · "
                    f"💰 {money(ctx.db.balance(ctx.uid))}")
    await ctx.dm(res["seller"], f"💰 Твой номер <code>{n['full']}</code> купил {esc(ctx.user['first_name'])} за "
                                f"{money(res['price'])} · получено {money(res['got'])} (комиссия {fee}%)")


@router.cmd("снять", "unlist", "отозвать", section="market")
async def cmd_unlist(ctx: Ctx) -> None:
    n = my_number(ctx, ctx.arg(0))
    if not ctx.db.unlist(n["id"]):
        raise UserError("Этот номер и так не продаётся")
    await ctx.reply(f"↩️ {num_short(n)} снят с продажи")


@router.cmd("рынок", "market", "маркет", "лавка", section="market")
async def cmd_market(ctx: Ctx) -> None:
    page = max(1, int(ctx.arg(0))) if ctx.arg(0).isdigit() else 1
    total = ctx.db.market_count()
    lines = ["🏪 <b>РЫНОК</b> · " + (f"{total} {plural(total, 'лот', 'лота', 'лотов')}" if total else "лотов нет")]
    for m in ctx.db.offers_for(ctx.uid):
        lines.append(f"🎯 тебе: {TIER[m['tier']]['emoji']} <code>{m['full']}</code> · <b>{money(m['price'])}</b> · от "
                     f"{esc(m['sname'])} · <code>купить #{m['number_id']}</code>")
    if total:
        pages = (total + PAGE - 1) // PAGE
        page = min(page, pages)
        for m in ctx.db.market(PAGE, (page - 1) * PAGE):
            lines.append(f"{TIER[m['tier']]['emoji']} <code>{m['full']}</code> · оценка {money(m['value'])} → "
                         f"<b>{money(m['price'])}</b> · {esc(m['sname'])} · <code>купить #{m['number_id']}</code>")
        if pages > 1:
            lines.append(f"<i>стр. {page}/{pages} · «рынок {page + 1 if page < pages else 1}»</i>")
    lines.append("<i>«продать #ID цена» — выставить · «лоты» — мои · «обмен #мой #его» — обмен</i>")
    await ctx.reply("\n".join(lines))


@router.cmd("лоты", "мои лоты", "listings", section="market")
async def cmd_listings(ctx: Ctx) -> None:
    rows = ctx.db.user_listings(ctx.uid)
    lines = ["🏷 <b>МОИ ЛОТЫ</b>"]
    for m in rows:
        to = f" → <code>{m['buyer']}</code>" if m["buyer"] else ""
        lines.append(f"{TIER[m['tier']]['emoji']} <code>{m['full']}</code> · <b>{money(m['price'])}</b>{to} · "
                     f"<code>снять #{m['number_id']}</code>")
    if not rows:
        lines.append("Пусто · <code>продать #ID 5000</code>")
    offers = ctx.db.offers_for(ctx.uid)
    if offers:
        lines.append("🎯 <b>Предложения мне:</b>")
        for m in offers:
            lines.append(f"{TIER[m['tier']]['emoji']} <code>{m['full']}</code> · <b>{money(m['price'])}</b> · от {esc(m['sname'])} · "
                         f"<code>купить #{m['number_id']}</code>")
    await ctx.reply("\n".join(lines))


# ───────────────────────── обмен ─────────────────────────
@router.cmd("обмен", "trade", "обменять", section="market")
async def cmd_trade(ctx: Ctx) -> None:
    sub = ctx.arg(0).lower()
    if sub in ("принять", "ок", "да"):
        await trade_accept(ctx, ctx.arg(1))
        return
    if sub in ("отклонить", "отказ", "нет"):
        await trade_decline(ctx, ctx.arg(1))
        return
    if sub in ("отмена", "отменить"):
        mine = [t for t in ctx.db.trades_for(ctx.uid) if int(t["from_user"]) == ctx.uid]
        if not mine:
            raise UserError("У тебя нет активных предложений обмена")
        for t in mine:
            ctx.db.set_trade_status(t["id"], "cancelled")
        await ctx.reply(f"✖️ Предложения обмена отменены ({len(mine)})")
        return
    if not sub:
        await cmd_trades(ctx)
        return
    give = my_number(ctx, ctx.arg(0))
    take = ctx.db.find_number(ctx.arg(1))
    if not take:
        raise UserError("Обмен: <code>обмен #мой #его [доплата]</code> — ID чужого номера видно в «номера @ник» или на рынке")
    if int(take["user_id"]) == ctx.uid:
        raise UserError("Второй номер тоже твой")
    extra = 0
    if ctx.arg(2):
        if not looks_like_amount(ctx.arg(2)):
            raise UserError("Доплата числом: <code>обмен #мой #его 500</code>")
        extra = parse_amount(ctx.arg(2), ctx.db.balance(ctx.uid))
        if extra < 0 or extra > ctx.db.balance(ctx.uid):
            raise UserError("Доплата не может быть больше твоего баланса")
    other = ctx.db.user(int(take["user_id"]))
    if other["banned"]:
        raise UserError("Владелец этого номера заблокирован")
    open_mine = [t for t in ctx.db.trades_for(ctx.uid) if int(t["from_user"]) == ctx.uid]
    if len(open_mine) >= 3:
        raise UserError("Не больше 3 активных предложений · «обмен отмена»")
    tid = ctx.db.create_trade(ctx.uid, int(other["id"]), give["id"], take["id"], extra)
    extra_s = f" + <b>{money(extra)}</b>" if extra else ""
    await ctx.reply(f"🔁 {ctx.who} предлагает {ctx.bot.display(other)} обмен #{tid}:\n"
                    f"отдаёт {num_short(give)}{extra_s}\nполучает {num_short(take)}\n"
                    f"{ctx.bot.display(other, link=False)}, напиши <code>обмен принять {tid}</code> или "
                    f"<code>обмен отклонить {tid}</code> · {TRADE_TTL // 60} мин")
    if ctx.is_group:
        await ctx.dm(int(other["id"]), f"🔁 {esc(ctx.user['first_name'])} предлагает обмен: его <code>{give['full']}</code>"
                                       f"{(' + ' + money(extra)) if extra else ''} на твой <code>{take['full']}</code> · "
                                       f"<code>обмен принять {tid}</code>")


def _pick_trade(ctx: Ctx, tok: str, incoming: bool = True):
    rows = [t for t in ctx.db.trades_for(ctx.uid) if (int(t["to_user"]) == ctx.uid) == incoming]
    if tok:
        t = ctx.db.trade(tok)
        if not t or t["status"] != "open":
            raise UserError("Обмен не найден или уже закрыт")
        return t
    if not rows:
        raise UserError("Нет предложений обмена · «обмены»")
    return rows[0]


async def trade_accept(ctx: Ctx, tok: str) -> None:
    t = _pick_trade(ctx, tok)
    try:
        t = ctx.db.accept_trade(t["id"], ctx.uid)
    except ValueError as e:
        raise UserError(str(e))
    except InsufficientFunds:
        raise UserError("У инициатора не хватает средств на доплату")
    other = ctx.db.user(int(t["from_user"]))
    give, take = ctx.db.number(t["give_id"]), ctx.db.number(t["take_id"])
    await ctx.reply(f"✅ Обмен #{t['id']} состоялся! {ctx.who} получил {num_short(give)}, "
                    f"{ctx.bot.display(other)} получил {num_short(take)}"
                    + (f" · доплата {money(t['extra'])}" if t["extra"] else ""))
    await ctx.dm(int(other["id"]), f"✅ {esc(ctx.user['first_name'])} принял обмен #{t['id']} · у тебя теперь "
                                   f"<code>{take['full']}</code>")


async def trade_decline(ctx: Ctx, tok: str) -> None:
    t = _pick_trade(ctx, tok)
    if not ctx.db.set_trade_status(t["id"], "declined"):
        raise UserError("Обмен уже закрыт")
    await ctx.reply(f"🙅 {ctx.who} отклонил обмен #{t['id']}")
    await ctx.dm(int(t["from_user"]), f"🙅 {esc(ctx.user['first_name'])} отклонил обмен #{t['id']}")


@router.cmd("обмены", "trades", section="market")
async def cmd_trades(ctx: Ctx) -> None:
    rows = ctx.db.trades_for(ctx.uid)
    lines = ["🔁 <b>ОБМЕНЫ</b>"]
    for t in rows:
        give, take = ctx.db.number(t["give_id"]), ctx.db.number(t["take_id"])
        if not give or not take:
            continue
        if int(t["to_user"]) == ctx.uid:
            who = ctx.db.user(int(t["from_user"]))
            lines.append(f"📥 #{t['id']} от {esc(who['first_name'])}: {num_short(give)}"
                         + (f" + {money(t['extra'])}" if t["extra"] else "") + f" за твой {num_short(take)} · "
                         f"<code>обмен принять {t['id']}</code>")
        else:
            who = ctx.db.user(int(t["to_user"]))
            lines.append(f"📤 #{t['id']} → {esc(who['first_name'])}: {num_short(give)} за {num_short(take)} · ждёт")
    if len(lines) == 1:
        lines.append("Пусто · <code>обмен #мой #его</code>")
    await ctx.reply("\n".join(lines))


@router.cmd("торговля", "торг", "рынок помощь")
async def cmd_market_help(ctx: Ctx) -> None:
    await ctx.reply(MARKET_HELP)


async def expire_trades(bot) -> None:
    for t in bot.db.expired_trades(TRADE_TTL):
        bot.db.set_trade_status(t["id"], "expired")
