"""Прокрутка номеров текстом: «крутить [оператор] [1-5]», «операторы»."""
from __future__ import annotations

import asyncio

from .config import MAX_SPINS_AT_ONCE
from .core import Ctx, UserError, router
from .db import InsufficientFunds
from .numbers import TIER, TIERS, appraise, fmt_number, generate_digits, masked_number, roll_tier, tier_index, tier_label
from .ui import esc, money, number_card, pct, tier_odds_lines

# Режимы прокрута: цена ×mult, удача — множитель веса каждой редкости в шансах оператора.
SPIN_MODES = {
    "std":   {"name": "Стандарт", "emoji": "🎯", "mult": 1.0, "luck": "базовая",
              "aliases": ("стандарт", "станд", "стд", "обычный режим"),
              "w": {"common": 1, "uncommon": 1.05, "rare": 1.35, "epic": 1.7, "legendary": 2, "mythic": 2.2}},
    "vip":   {"name": "Вип", "emoji": "🌟", "mult": 1.5, "luck": "повышена",
              "aliases": ("вип", "vip"),
              "w": {"common": 0.9, "uncommon": 1.1, "rare": 1.8, "epic": 2.5, "legendary": 3.2, "mythic": 3.5}},
    "prem":  {"name": "Премиум", "emoji": "💎", "mult": 2.0, "luck": "выше",
              "aliases": ("премиум", "прем", "premium"),
              "w": {"common": 0.85, "uncommon": 1.05, "rare": 2.2, "epic": 3.4, "legendary": 4.5, "mythic": 5}},
    "elite": {"name": "Элит", "emoji": "👑", "mult": 3.5, "luck": "очень большая",
              "aliases": ("элит", "элита", "элитный", "elite"),
              "w": {"common": 0.6, "uncommon": 0.9, "rare": 2.8, "epic": 5, "legendary": 7, "mythic": 8}},
}
MODE_ORDER = ("std", "vip", "prem", "elite")


def parse_mode(token: str) -> str | None:
    t = (token or "").strip().lower()
    if not t:
        return None
    for key in MODE_ORDER:
        if t in SPIN_MODES[key]["aliases"]:
            return key
    if len(t) >= 3:
        for key in MODE_ORDER:
            if any(a.startswith(t) for a in SPIN_MODES[key]["aliases"]):
                return key
    return None


def mode_price(op, mode: str) -> int:
    return int(round(int(op["price"]) * SPIN_MODES[mode]["mult"]))


def mode_odds(odds: dict, mode: str) -> dict:
    w = SPIN_MODES[mode]["w"]
    return {t: float(odds.get(t, 0)) * w[t] for t in TIERS}


def modes_line(op) -> str:
    return "  ".join(f"{SPIN_MODES[k]['emoji']} {SPIN_MODES[k]['name']} {money(mode_price(op, k))}" for k in MODE_ORDER)


def find_operator(ctx: Ctx, token: str):
    """Оператор по коду, названию или префиксу (регистр не важен, можно начало слова)."""
    t = (token or "").strip().lower().lstrip("+")
    if not t:
        return None
    ops = ctx.db.operators()
    for op in ops:
        if t in (op["code"].lower(), op["name"].lower(), op["prefix"]):
            return op
    for op in ops:
        if op["name"].lower().startswith(t) or op["code"].lower().startswith(t):
            return op
    return None


def op_odds_short(ctx: Ctx, op) -> str:
    odds = ctx.db.operator_odds(op)
    total = sum(float(v) for v in odds.values()) or 1
    p = {t: float(odds.get(t, 0)) / total * 100 for t in TIERS}
    return f"🟣 {pct(p['epic'])} · 🟠 {pct(p['legendary'])} · 🔴 {pct(p['mythic'])}"


def operators_text(ctx: Ctx) -> str:
    lines = ["📡 <b>ОПЕРАТОРЫ</b> · <code>крутить тесто</code> · <code>крутить сберище 3</code>"]
    for c in ctx.db.countries():
        ops = ctx.db.operators(c["code"])
        if not ops:
            continue
        if len(ctx.db.countries()) > 1:
            lines.append(f"{c['flag']} <b>{esc(c['name'])}</b>")
        for op in ops:
            lines.append(f"{op['emoji']} <b>{esc(op['name'])}</b> {c['dial']} {op['prefix']} · <b>{money(op['price'])}</b> · "
                         f"{op_odds_short(ctx, op)}")
    lines.append("")
    lines.append("🎚 <b>РЕЖИМЫ</b> · <code>крутить тесто вип</code>")
    for k in MODE_ORDER:
        m = SPIN_MODES[k]
        lines.append(f"{m['emoji']} <b>{m['name']}</b> ×{m['mult']:g} · удача {m['luck']}")
    lines.append("<i>«оператор тесто» — все шансы и цены номеров</i>")
    return "\n".join(lines)


@router.cmd("операторы", "operators", "опер", section="spin")
async def cmd_operators(ctx: Ctx) -> None:
    await ctx.reply(operators_text(ctx))


@router.cmd("оператор", "operator", section="spin")
async def cmd_operator(ctx: Ctx) -> None:
    if ctx.rank >= 4 and ctx.arg(0) in ("цена", "шансы", "добавить", "удалить", "вкл", "выкл", "имя", "эмодзи"):
        from .handlers_admin import admin_operator
        await admin_operator(ctx)
        return
    op = find_operator(ctx, ctx.arg(0))
    if not op:
        raise UserError("Такого оператора нет · «операторы»")
    c = ctx.db.country(op["country"])
    lines = [f"{op['emoji']} <b>{esc(op['name'])}</b> · {c['flag'] if c else ''} {c['dial'] if c else '+7'} {op['prefix']} · "
             f"прокрутка <b>{money(op['price'])}</b>"]
    lines += tier_odds_lines(ctx.db.operator_odds(op))
    lines.append("🎚 " + " · ".join(f"{SPIN_MODES[k]['emoji']} {SPIN_MODES[k]['name']} {money(mode_price(op, k))}"
                                   for k in MODE_ORDER))
    lines.append(f"<code>крутить {esc(op['name'].lower())} [режим] [n]</code> · до ×{MAX_SPINS_AT_ONCE} за раз")
    await ctx.reply("\n".join(lines))


SPIN_USAGE = ("📱 Крутить: <code>крутить тесто</code> · <code>крутить сберище вип 3</code> (до 5 за раз)\n"
              "🎚 режимы: стандарт · вип · премиум · элит · «операторы»")


@router.cmd("крутить", "крути", "спин", "spin", "прокрутить", "крутануть", "roll", section="spin")
async def cmd_spin(ctx: Ctx) -> None:
    ops = ctx.db.operators()
    if not ops:
        raise UserError("Пока нет доступных операторов")
    op = None
    count = 1
    mode = None
    for tok in ctx.args:
        if tok.lower().startswith("x") or tok.lower().startswith("х"):
            tok = tok[1:]
        if tok.isdigit():
            count = int(tok)
        elif parse_mode(tok) and (op is not None or find_operator(ctx, tok) is None):
            mode = parse_mode(tok)
        elif op is None:
            op = find_operator(ctx, tok)
            if op is None:
                raise UserError(f"Оператор «{esc(tok)}» не найден · «операторы»")
    if op is None:
        if len(ops) == 1:
            op = ops[0]
        else:
            await ctx.reply(operators_text(ctx))
            return
    if not 1 <= count <= MAX_SPINS_AT_ONCE:
        raise UserError(f"За раз можно от 1 до {MAX_SPINS_AT_ONCE} прокруток")
    await do_spin(ctx, op["code"], count, mode or "std")


def spin_once(ctx: Ctx, op, c, mode: str = "std") -> dict:
    odds = mode_odds(ctx.db.operator_odds(op), mode)
    tier = roll_tier(odds)
    digits = generate_digits(tier, taken=lambda d: ctx.db.number_taken(fmt_number(c["dial"], op["prefix"], d)))
    full = fmt_number(c["dial"], op["prefix"], digits)
    return ctx.db.add_number(ctx.uid, c["code"], op["code"], full, digits, tier, appraise(digits, tier), "spin")


TITLES = {5: "🔥🔥🔥 <b>МИФИЧЕСКИЙ ДРОП!</b> 🔥🔥🔥", 4: "👑 <b>ЛЕГЕНДАРНЫЙ НОМЕР!</b> 👑", 3: "💎 <b>ЭПИЧЕСКИЙ НОМЕР!</b>",
          2: "✨ <b>РЕДКИЙ НОМЕР!</b>", 1: "📱 <b>НОВЫЙ НОМЕР</b>", 0: "📱 <b>НОВЫЙ НОМЕР</b>"}


async def do_spin(ctx: Ctx, code: str, count: int, mode: str = "std") -> None:
    op = ctx.db.operator(code)
    if not op or not op["enabled"]:
        raise UserError("Оператор недоступен")
    c = ctx.db.country(op["country"])
    md = SPIN_MODES[mode]
    cost = mode_price(op, mode) * count
    try:
        with ctx.db.tx():
            ctx.db.change_balance(ctx.uid, -cost, f"spin:{op['code']}:{mode}x{count}")
            ctx.db.ex("UPDATE users SET spins=spins+? WHERE id=?", (count, ctx.uid))
            results = [spin_once(ctx, op, c, mode) for _ in range(count)]
    except InsufficientFunds:
        raise UserError(f"Не хватает {money(cost - ctx.db.balance(ctx.uid))} · «бонус» или «пополнить»")
    best = max(results, key=lambda n: (tier_index(n["tier"]), n["value"]))

    header = (f"🎰 {ctx.who} крутит {op['emoji']} {esc(op['name'])} · {md['emoji']} {md['name']}"
              + (f" ×{count}" if count > 1 else "") + "…")
    mid = None
    for step in (0, 3, 5, 6):
        frame = masked_number(c["dial"], op["prefix"], best["digits"], step)
        spark = "🎲" if step < 4 else ("✨" if step < 6 else "💥")
        text = f"{header}\n{spark} <code>{frame}</code>"
        if mid is None:
            msg = await ctx.reply(text)
            mid = (msg or {}).get("message_id")
        else:
            await ctx.edit(mid, text)
        await asyncio.sleep(0.5)

    bal = ctx.db.balance(ctx.uid)
    ti = tier_index(best["tier"])
    title = f"{TITLES[min(ti, 5)]} · {ctx.who}"
    if count == 1:
        text = number_card(best, op, c, title=title, extra=f"{md['emoji']} {md['name']} · 💰 {money(bal)} · <i>«номера» · «передать #{best['id']} @ник»</i>")
    else:
        total_val = sum(n["value"] for n in results)
        lines = [f"{title} · ×{count}"]
        for n in sorted(results, key=lambda n: (-tier_index(n["tier"]), -n["value"])):
            lines.append(f"{TIER[n['tier']]['emoji']} <code>{n['full']}</code> · <code>#{n['id']}</code> · {money(n['value'])}")
        lines += [f"💵 −{money(cost)} ({md['emoji']} {md['name']}) → 💎 <b>+{money(total_val)}</b> · 💰 {money(bal)}"]
        text = "\n".join(lines)
    await ctx.edit(mid, text)

    if ti >= 4:
        await ctx.bot.notify_admins(f"{'🔥' if ti >= 5 else '👑'} <b>{tier_label(best['tier'])}</b> выпал у "
                                    f"<b>{esc(ctx.user['first_name'])}</b> (<code>{ctx.uid}</code>): "
                                    f"<code>{best['full']}</code>")
