"""Оформление: форматирование чисел, дат, карточек. Только текст — без кнопок."""
from __future__ import annotations

import html
import time

from .numbers import TIER, TIERS, features, tier_label, value_range

NBSP = "\u00a0"
LINE = "─────────────"
THIN = "┈┈┈┈┈┈┈┈┈┈┈┈┈"
BRAND = "СИМ-РУЛЕТКА"


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def money(n) -> str:
    n = int(n)
    sign = "-" if n < 0 else ""
    return f"{sign}{abs(n):,}".replace(",", NBSP) + f"{NBSP}₽"


def num(n) -> str:
    return f"{int(n):,}".replace(",", NBSP)


def short(n) -> str:
    """Короткая сумма: 1К, 50К, 1М."""
    n = int(n)
    if n >= 1_000_000 and n % 100_000 == 0:
        return f"{n / 1_000_000:g}М"
    if n >= 1_000 and n % 100 == 0:
        return f"{n / 1_000:g}К"
    return num(n)


def approx(n) -> str:
    """Округлённая сумма для диапазонов: 68К, 2.5К, 250."""
    n = int(n)
    if n >= 10_000:
        return f"{round(n / 1_000):g}К"
    if n >= 1_000:
        return f"{n / 1_000:.1f}".rstrip("0").rstrip(".") + "К"
    return str(n)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def mult(x: float) -> str:
    x = float(x)
    return f"×{x:.2f}".rstrip("0").rstrip(".") if x != int(x) else f"×{int(x)}"


def pct(x: float) -> str:
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return f"{s}%"


def signed(n) -> str:
    n = int(n)
    return ("+" if n >= 0 else "") + money(n)


def bar(frac: float, width: int = 10, full: str = "▰", empty: str = "▱") -> str:
    frac = max(0.0, min(1.0, frac))
    n = round(frac * width)
    return full * n + empty * (width - n)


def ago(ts: int) -> str:
    d = int(time.time()) - int(ts)
    if d < 60:
        return "только что"
    if d < 3600:
        return f"{d // 60} мин назад"
    if d < 86400:
        return f"{d // 3600} ч назад"
    return f"{d // 86400} дн назад"


def dur(seconds: int) -> str:
    """Длительность: 2 ч 5 мин, 30 мин, 45 с."""
    s = max(0, int(seconds))
    if s >= 86400:
        return f"{s // 86400} д {(s % 86400) // 3600} ч"
    if s >= 3600:
        return f"{s // 3600} ч {(s % 3600) // 60} мин"
    if s >= 60:
        return f"{s // 60} мин"
    return f"{s} с"


def date(ts: int) -> str:
    return time.strftime("%d.%m.%Y", time.gmtime(int(ts) + 3 * 3600))  # МСК


def datetime_(ts: int) -> str:
    return time.strftime("%d.%m %H:%M", time.gmtime(int(ts) + 3 * 3600))


def mention(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{esc(name)}</a>'


def user_line(row) -> str:
    uname = f" (@{esc(row['username'])})" if row["username"] else ""
    return f"{esc(row['first_name'])}{uname} · <code>{row['id']}</code>"


def tier_odds_lines(odds: dict) -> list[str]:
    total = sum(float(odds.get(t, 0)) for t in TIERS) or 1
    out = []
    for t in TIERS:
        p = float(odds.get(t, 0)) / total * 100
        lo, hi = value_range(t)
        out.append(f"{tier_label(t)} · {pct(p)} · {approx(lo)}–{approx(hi)} ₽")
    return out


def number_line(n, op=None) -> str:
    """Одна строка про номер: 🔵 +7 902 777-12-34 · #ABC123 · 1 250 ₽"""
    e = f"{op['emoji']} " if op else ""
    return f"{TIER[n['tier']]['emoji']} <code>{n['full']}</code> · <code>#{n['id']}</code> · {e}{money(n['value'])}"


def number_card(n, op, country, title: str | None = None, extra: str = "") -> str:
    t = TIER[n["tier"]]
    opname = f"{op['emoji']} {esc(op['name'])}" if op else n["operator"]
    lines = [
        title or "📱 <b>НОМЕР</b>",
        f"📞 <code>{n['full']}</code>",
        f"{tier_label(n['tier'])}{(' ' + t['flair']) if t['flair'] else ''} · {opname} · 💵 <b>{money(n['value'])}</b>",
    ]
    feats = [name for name, _ in features(n["digits"])]
    if feats:
        lines.append("✨ " + " · ".join(feats))
    lines.append(f"🆔 <code>#{n['id']}</code>")
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def parse_duration(token: str) -> int | None:
    """30м / 2ч / 1д / 90 (минуты) → секунды."""
    t = (token or "").lower().replace("ё", "е")
    if not t:
        return None
    unit = 60
    if t[-1] in "мm" and not t.endswith("мин"):
        t = t[:-1]
    elif t.endswith("мин"):
        t = t[:-3]
    elif t[-1] in "чh":
        unit, t = 3600, t[:-1]
    elif t[-1] in "дd":
        unit, t = 86400, t[:-1]
    if not t.replace(".", "", 1).isdigit():
        return None
    return int(float(t) * unit)
