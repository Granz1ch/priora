"""Кланы: тег, название, казна. Все команды начинаются с «клан …»."""
from __future__ import annotations

import re

from .core import Ctx, UserError, router
from .db import InsufficientFunds
from .handlers_games import parse_amount
from .ui import date, esc, money

TAG_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9]{2,6}$")
MAX_MEMBERS = 30
ROLE_NAMES = {"owner": "👑 Глава", "deputy": "⭐ Зам", "member": "👤"}

CLAN_HELP = "\n".join([
    "🏰 <b>КЛАНЫ</b>",
    "<code>клан создать ТЕГ Название</code> — создать ({price})",
    "<code>клан</code> — мой клан · <code>клан инфо ТЕГ</code> · <code>клан топ</code>",
    "<code>клан пригласить @ник</code> (или ответом) · <code>клан принять [ТЕГ]</code> · <code>клан вступить ТЕГ</code>",
    "<code>клан внести 1000</code> — в казну · <code>клан снять 1000</code> · <code>клан выдать @ник 500</code> (глава)",
    "<code>клан зам @ник</code> · <code>клан кик @ник</code> · <code>клан передать @ник</code> · <code>клан открыть/закрыть</code>",
    "<code>клан название Новое имя</code> · <code>клан выйти</code> · <code>клан удалить да</code>",
])


def clan_card(ctx: Ctx, c) -> str:
    members = ctx.db.clan_members(int(c["id"]))
    owner = ctx.db.user(int(c["owner"]))
    lines = [
        f"🏰 <b>[{esc(c['tag'])}] {esc(c['name'])}</b> · {'🔓 открытый' if c['open'] else '🔒 по приглашению'}",
        f"👑 Глава: {ctx.bot.display(owner, tag=False)} · 👥 {len(members)}/{MAX_MEMBERS} · 📅 с {date(c['created'])}",
        f"💰 Казна: <b>{money(c['treasury'])}</b> · 💎 капитал участников: {money(sum(int(m['balance']) for m in members))}",
    ]
    for m in members[1:11]:
        lines.append(f"{ROLE_NAMES.get(m['clan_role'], '👤')} {esc(m['first_name'])} · {money(m['balance'])}")
    if len(members) > 11:
        lines.append(f"<i>…и ещё {len(members) - 11}</i>")
    return "\n".join(lines)


def clans_top_text(ctx: Ctx) -> str:
    rows = ctx.db.top_clans(10)
    lines = ["<b>🏰 ТОП КЛАНОВ</b>"]
    medals = ["🥇", "🥈", "🥉"]
    for i, c in enumerate(rows):
        lines.append(f"{medals[i] if i < 3 else f'{i + 1}.'} [{esc(c['tag'])}] {esc(c['name'])} — казна <b>{money(c['treasury'])}</b>"
                     f" · 👥 {c['members']} · 💎 {money(c['wealth'])}")
    if not rows:
        lines.append("Кланов пока нет · <code>клан создать ТЕГ Название</code>")
    return "\n".join(lines)


def my_clan(ctx: Ctx):
    c = ctx.db.user_clan(ctx.uid)
    if not c:
        raise UserError("Ты не в клане · <code>клан создать ТЕГ Название</code> или попроси приглашение")
    return c


def need_role(ctx: Ctx, c, roles=("owner",)) -> None:
    role = ctx.refresh_user()["clan_role"]
    if role not in roles and ctx.rank < 4:
        raise UserError("Это может только " + ("глава клана" if roles == ("owner",) else "глава или зам"))


@router.cmd("клан", "clan", "кланы", "clans", section="clans")
async def cmd_clan(ctx: Ctx) -> None:
    sub = ctx.arg(0).lower().replace("ё", "е")
    if ctx.cmd in ("кланы", "clans") and not sub:
        sub = "топ"
    fn = SUBS.get(sub)
    if fn is None:
        if sub in ("", "мой", "инфо") and not ctx.arg(1):
            c = ctx.db.user_clan(ctx.uid)
            if c:
                await ctx.reply(clan_card(ctx, c))
                return
        if sub in ("помощь", "help", ""):
            await ctx.reply(CLAN_HELP.format(price=money(ctx.setting("clan_price", 50_000))))
            return
        raise UserError("Не знаю такой команды клана · «клан помощь»")
    await fn(ctx)


async def sub_create(ctx: Ctx) -> None:
    tag, name = ctx.arg(1), ctx.rest(2).strip()
    price = int(ctx.setting("clan_price", 50_000))
    if not tag or not name:
        raise UserError(f"Создать клан: <code>клан создать ТЕГ Название</code> · стоимость {money(price)}")
    if not TAG_RE.match(tag):
        raise UserError("Тег — 2–6 букв или цифр без пробелов")
    if len(name) > 24:
        raise UserError("Название до 24 символов")
    if ctx.db.user_clan(ctx.uid):
        raise UserError("Ты уже в клане · сначала «клан выйти»")
    try:
        c = ctx.db.create_clan(tag.upper(), name, ctx.uid, price)
    except ValueError as e:
        raise UserError(str(e))
    except InsufficientFunds:
        raise UserError(f"Нужно {money(price)} на балансе")
    await ctx.reply(f"🏰 {ctx.who} основал клан <b>[{esc(c['tag'])}] {esc(c['name'])}</b> за {money(price)}!\n"
                    f"Приглашай: <code>клан пригласить @ник</code> · казна: <code>клан внести 1000</code>")


async def sub_info(ctx: Ctx) -> None:
    tag = ctx.arg(1)
    c = ctx.db.clan_by_tag(tag) if tag else ctx.db.user_clan(ctx.uid)
    if not c:
        raise UserError("Клан не найден · «клан топ»")
    await ctx.reply(clan_card(ctx, c))


async def sub_top(ctx: Ctx) -> None:
    await ctx.reply(clans_top_text(ctx))


async def sub_invite(ctx: Ctx) -> None:
    c = my_clan(ctx)
    need_role(ctx, c, ("owner", "deputy"))
    t = ctx.target(ctx.arg(1))
    if t["clan_id"]:
        raise UserError("Этот игрок уже в клане")
    if ctx.db.clan_size(int(c["id"])) >= MAX_MEMBERS:
        raise UserError(f"В клане максимум {MAX_MEMBERS} участников")
    ctx.db.clan_invite(int(c["id"]), int(t["id"]), ctx.uid)
    await ctx.reply(f"✉️ {ctx.bot.display(t)}, тебя приглашают в клан <b>[{esc(c['tag'])}] {esc(c['name'])}</b> · "
                    f"напиши <code>клан принять {esc(c['tag'])}</code>")
    if ctx.is_group:
        await ctx.dm(int(t["id"]), f"✉️ Приглашение в клан [{esc(c['tag'])}] {esc(c['name'])} от "
                                   f"{esc(ctx.user['first_name'])} · <code>клан принять {esc(c['tag'])}</code>")


async def sub_accept(ctx: Ctx) -> None:
    if ctx.user["clan_id"]:
        raise UserError("Ты уже в клане")
    invites = ctx.db.clan_invites_for(ctx.uid)
    tag = ctx.arg(1)
    if tag:
        invites = [i for i in invites if i["tag"].lower() == tag.lower().strip("[]")]
    if not invites:
        raise UserError("Нет приглашений" + (f" от клана {esc(tag)}" if tag else ""))
    inv = invites[0]
    c = ctx.db.clan(int(inv["clan_id"]))
    if not c:
        raise UserError("Клан уже распущен")
    if ctx.db.clan_size(int(c["id"])) >= MAX_MEMBERS:
        raise UserError("В клане нет мест")
    if not ctx.db.clan_join(ctx.uid, int(c["id"])):
        raise UserError("Не удалось вступить")
    await ctx.reply(f"🏰 {ctx.who} вступил в клан <b>[{esc(c['tag'])}] {esc(c['name'])}</b>!")


async def sub_join(ctx: Ctx) -> None:
    if ctx.user["clan_id"]:
        raise UserError("Ты уже в клане")
    c = ctx.db.clan_by_tag(ctx.arg(1)) if ctx.arg(1) else None
    if not c:
        raise UserError("Укажи тег: <code>клан вступить ТЕГ</code>")
    if not c["open"]:
        # заявка = просьба о приглашении
        owner = ctx.db.user(int(c["owner"]))
        await ctx.reply(f"🔒 Клан [{esc(c['tag'])}] закрытый. Глава {ctx.bot.display(owner)} может пригласить: "
                        f"<code>клан пригласить @{esc(ctx.user['username']) if ctx.user['username'] else ctx.uid}</code>")
        await ctx.dm(int(c["owner"]), f"📨 {esc(ctx.user['first_name'])} (<code>{ctx.uid}</code>) хочет в клан "
                                      f"[{esc(c['tag'])}] · <code>клан пригласить {ctx.uid}</code>")
        return
    if ctx.db.clan_size(int(c["id"])) >= MAX_MEMBERS:
        raise UserError("В клане нет мест")
    if not ctx.db.clan_join(ctx.uid, int(c["id"])):
        raise UserError("Не удалось вступить")
    await ctx.reply(f"🏰 {ctx.who} вступил в клан <b>[{esc(c['tag'])}] {esc(c['name'])}</b>!")


async def sub_leave(ctx: Ctx) -> None:
    c = my_clan(ctx)
    if int(c["owner"]) == ctx.uid:
        raise UserError("Глава не может выйти · передай клан («клан передать @ник») или удали («клан удалить да»)")
    ctx.db.clan_leave(ctx.uid)
    await ctx.reply(f"🚪 {ctx.who} покинул клан [{esc(c['tag'])}]")


async def sub_kick(ctx: Ctx) -> None:
    c = my_clan(ctx)
    need_role(ctx, c, ("owner", "deputy"))
    t = ctx.target(ctx.arg(1))
    if t["clan_id"] != c["id"]:
        raise UserError("Этот игрок не в твоём клане")
    if int(t["id"]) == int(c["owner"]):
        raise UserError("Главу выгнать нельзя")
    if t["clan_role"] == "deputy" and ctx.user["clan_role"] != "owner" and ctx.rank < 4:
        raise UserError("Зама может выгнать только глава")
    ctx.db.clan_leave(int(t["id"]))
    await ctx.reply(f"👢 {ctx.bot.display(t)} исключён из клана [{esc(c['tag'])}]")


async def sub_deputy(ctx: Ctx) -> None:
    c = my_clan(ctx)
    need_role(ctx, c)
    t = ctx.target(ctx.arg(1))
    if t["clan_id"] != c["id"]:
        raise UserError("Этот игрок не в твоём клане")
    new_role = "member" if t["clan_role"] == "deputy" else "deputy"
    ctx.db.clan_set_role(int(t["id"]), new_role)
    await ctx.reply(f"{'⭐' if new_role == 'deputy' else '👤'} {ctx.bot.display(t)} теперь "
                    f"{'зам главы' if new_role == 'deputy' else 'обычный участник'} клана [{esc(c['tag'])}]")


async def sub_transfer(ctx: Ctx) -> None:
    c = my_clan(ctx)
    need_role(ctx, c)
    t = ctx.target(ctx.arg(1))
    if t["clan_id"] != c["id"]:
        raise UserError("Этот игрок не в твоём клане")
    ctx.db.clan_set_owner(int(c["id"]), int(t["id"]), int(c["owner"]))
    await ctx.reply(f"👑 Клан [{esc(c['tag'])}] передан {ctx.bot.display(t)}")


async def sub_delete(ctx: Ctx) -> None:
    c = my_clan(ctx)
    need_role(ctx, c)
    if ctx.arg(1).lower() not in ("да", "yes"):
        raise UserError(f"Удалить клан [{esc(c['tag'])}]? Казна {money(c['treasury'])} вернётся главе. "
                        f"Подтверди: <code>клан удалить да</code>")
    back = ctx.db.delete_clan(int(c["id"]))
    await ctx.reply(f"🏚 Клан [{esc(c['tag'])}] {esc(c['name'])} распущен" + (f" · казна {money(back)} возвращена главе" if back else ""))


async def sub_deposit(ctx: Ctx) -> None:
    c = my_clan(ctx)
    amount = parse_amount(ctx.arg(1), ctx.db.balance(ctx.uid)) if ctx.arg(1) else 0
    if amount <= 0:
        raise UserError("Сумма: <code>клан внести 1000</code>")
    try:
        tre = ctx.db.clan_treasury(int(c["id"]), ctx.uid, amount, "deposit")
    except InsufficientFunds:
        raise UserError("Недостаточно средств")
    await ctx.reply(f"🏦 {ctx.who} внёс в казну [{esc(c['tag'])}] <b>{money(amount)}</b> · казна: <b>{money(tre)}</b>")


async def sub_withdraw(ctx: Ctx) -> None:
    c = my_clan(ctx)
    need_role(ctx, c)
    amount = parse_amount(ctx.arg(1), int(c["treasury"])) if ctx.arg(1) else 0
    if amount <= 0:
        raise UserError("Сумма: <code>клан снять 1000</code>")
    try:
        tre = ctx.db.clan_treasury(int(c["id"]), ctx.uid, -amount, "withdraw")
    except InsufficientFunds:
        raise UserError(f"В казне только {money(c['treasury'])}")
    await ctx.reply(f"🏦 {ctx.who} снял из казны [{esc(c['tag'])}] <b>{money(amount)}</b> · казна: <b>{money(tre)}</b>")


async def sub_pay(ctx: Ctx) -> None:
    c = my_clan(ctx)
    need_role(ctx, c)
    # «клан выдать @ник 500» или ответом «клан выдать 500»
    toks = ctx.args[1:]
    amount_tok = next((t for t in toks if t.replace(".", "", 1).rstrip("кk").isdigit()), None)
    user_tok = next((t for t in toks if t != amount_tok), None)
    t = ctx.target(user_tok, allow_self=True)
    if t["clan_id"] != c["id"]:
        raise UserError("Этот игрок не в твоём клане")
    amount = parse_amount(amount_tok, int(c["treasury"])) if amount_tok else 0
    if amount <= 0:
        raise UserError("Сумма: <code>клан выдать @ник 500</code>")
    try:
        tre = ctx.db.clan_treasury(int(c["id"]), int(t["id"]), -amount, f"pay:{ctx.uid}")
    except InsufficientFunds:
        raise UserError(f"В казне только {money(c['treasury'])}")
    await ctx.reply(f"🏦 Из казны [{esc(c['tag'])}] выдано {ctx.bot.display(t)}: <b>{money(amount)}</b> · казна: {money(tre)}")


async def sub_open(ctx: Ctx) -> None:
    c = my_clan(ctx)
    need_role(ctx, c)
    val = 1 if ctx.arg(0).lower() == "открыть" else 0
    ctx.db.clan_update(int(c["id"]), open=val)
    await ctx.reply(f"🏰 Клан [{esc(c['tag'])}] теперь {'🔓 открытый — вступить может любой' if val else '🔒 по приглашению'}")


async def sub_rename(ctx: Ctx) -> None:
    c = my_clan(ctx)
    need_role(ctx, c)
    name = ctx.rest(1).strip()
    if not name or len(name) > 24:
        raise UserError("Название до 24 символов: <code>клан название Новое имя</code>")
    ctx.db.clan_update(int(c["id"]), name=name)
    await ctx.reply(f"🏰 Клан [{esc(c['tag'])}] теперь называется <b>{esc(name)}</b>")


async def sub_retag(ctx: Ctx) -> None:
    c = my_clan(ctx)
    need_role(ctx, c)
    tag = ctx.arg(1)
    if not tag or not TAG_RE.match(tag):
        raise UserError("Тег — 2–6 букв или цифр: <code>клан тег NEW</code>")
    if ctx.db.clan_by_tag(tag):
        raise UserError("Такой тег уже занят")
    ctx.db.clan_update(int(c["id"]), tag=tag.upper())
    await ctx.reply(f"🏰 Новый тег клана: <b>[{esc(tag.upper())}]</b>")


SUBS = {
    "создать": sub_create, "инфо": sub_info, "топ": sub_top, "пригласить": sub_invite, "принять": sub_accept,
    "вступить": sub_join, "выйти": sub_leave, "кик": sub_kick, "выгнать": sub_kick, "зам": sub_deputy,
    "передать": sub_transfer, "удалить": sub_delete, "распустить": sub_delete, "внести": sub_deposit,
    "пополнить": sub_deposit, "снять": sub_withdraw, "выдать": sub_pay, "открыть": sub_open, "закрыть": sub_open,
    "название": sub_rename, "имя": sub_rename, "тег": sub_retag,
}
