"""Генерация «красивых» номеров, редкости и их стоимость."""
from __future__ import annotations

import secrets

RNG = secrets.SystemRandom()

TIERS = ["common", "uncommon", "rare", "epic", "legendary", "mythic"]

TIER = {
    "common":    {"name": "Обычный",     "emoji": "⚪", "value": 100,    "flair": ""},
    "uncommon":  {"name": "Необычный",   "emoji": "🟢", "value": 300,    "flair": ""},
    "rare":      {"name": "Редкий",      "emoji": "🔵", "value": 1_000,  "flair": "✨"},
    "epic":      {"name": "Эпический",   "emoji": "🟣", "value": 3_000,  "flair": "💎"},
    "legendary": {"name": "Легендарный", "emoji": "🟠", "value": 15_000, "flair": "👑"},
    "mythic":    {"name": "Мифический",  "emoji": "🔴", "value": 100_000, "flair": "🔥"},
}

# Шансы по умолчанию (в процентах) для новых операторов
DEFAULT_ODDS = {"common": 60, "uncommon": 25, "rare": 10, "epic": 4, "legendary": 0.9, "mythic": 0.1}

TIER_ALIASES = {
    "обычный": "common", "обычн": "common", "белый": "common",
    "необычный": "uncommon", "необычн": "uncommon", "зелёный": "uncommon", "зеленый": "uncommon",
    "редкий": "rare", "редк": "rare", "синий": "rare",
    "эпический": "epic", "эпик": "epic", "фиолетовый": "epic",
    "легендарный": "legendary", "легенда": "legendary", "лега": "legendary", "оранжевый": "legendary",
    "мифический": "mythic", "мифик": "mythic", "миф": "mythic", "красный": "mythic",
}


def parse_tier(token: str) -> str | None:
    """Код редкости по английскому коду или русскому названию (можно начало слова)."""
    t = (token or "").strip().lower()
    if not t:
        return None
    if t in TIERS:
        return t
    if t in TIER_ALIASES:
        return TIER_ALIASES[t]
    for name, code in TIER_ALIASES.items():
        if name.startswith(t) and len(t) >= 3:
            return code
    return None

ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_id(n: int = 6) -> str:
    return "".join(RNG.choice(ID_ALPHABET) for _ in range(n))


def tier_value(tier: str) -> int:
    return TIER[tier]["value"]


def tier_label(tier: str, upper: bool = False) -> str:
    t = TIER[tier]
    name = t["name"].upper() if upper else t["name"]
    return f"{t['emoji']} {name}"


def tier_index(tier: str) -> int:
    return TIERS.index(tier)


def is_seq(s: str) -> bool:
    """Строго возрастающая или убывающая последовательность соседних цифр без перехода через 9→0."""
    if len(s) < 3:
        return False
    d = [int(c) for c in s]
    up = all(d[i + 1] - d[i] == 1 for i in range(len(d) - 1))
    down = all(d[i] - d[i + 1] == 1 for i in range(len(d) - 1))
    return up or down


def classify(s: str) -> str:
    """Определяет редкость 7-значного «хвоста» номера (d1d2d3-d4d5-d6d7)."""
    assert len(s) == 7 and s.isdigit()
    if len(set(s)) == 1:
        return "mythic"
    if is_seq(s) or len(set(s[1:])) == 1:
        return "legendary"
    tail4 = s[3:]
    if len(set(tail4)) == 1:
        return "epic"
    if s[3:5] == s[5:7] and s[3] != s[4]:          # ABAB
        return "epic"
    if is_seq(tail4):                               # 1234 / 4321
        return "epic"
    if len(set(s[4:])) == 1:                        # X-777
        return "rare"
    if s[3] == s[6] and s[4] == s[5] and s[3] != s[4]:   # ABBA
        return "rare"
    if s[3] == s[4] and s[5] == s[6] and s[3] != s[5]:   # AABB
        return "rare"
    if len(set(s[:3])) == 1 and s[5] == s[6]:        # 777-xx-55
        return "rare"
    if s[5] == s[6] or len(set(s[:3])) == 1:        # последняя пара или первая тройка
        return "uncommon"
    return "common"


def _digit(exclude: str | None = None) -> str:
    while True:
        d = str(RNG.randrange(10))
        if d != exclude:
            return d


def _rand(n: int) -> str:
    return "".join(str(RNG.randrange(10)) for _ in range(n))


_SEQ7 = ["0123456", "1234567", "2345678", "3456789", "9876543", "8765432", "7654321", "6543210"]
_SEQ4 = ["0123", "1234", "2345", "3456", "4567", "5678", "6789", "9876", "8765", "7654", "6543", "5432", "4321", "3210"]


def _candidate(tier: str) -> str:
    if tier == "mythic":
        return _digit() * 7
    if tier == "legendary":
        if RNG.random() < 0.5:
            return RNG.choice(_SEQ7)
        d = _digit()
        return _digit(exclude=d) + d * 6
    if tier == "epic":
        r = RNG.random()
        if r < 0.4:
            d = _digit()
            return _rand(2) + _digit(exclude=d) + d * 4
        if r < 0.7:
            a = _digit()
            b = _digit(exclude=a)
            return _rand(3) + a + b + a + b
        return _rand(3) + RNG.choice(_SEQ4)
    if tier == "rare":
        r = RNG.random()
        if r < 0.4:
            d = _digit()
            return _rand(3) + _digit(exclude=d) + d * 3
        if r < 0.7:
            a = _digit()
            b = _digit(exclude=a)
            return _rand(3) + a + b + b + a
        a = _digit()
        b = _digit(exclude=a)
        return _rand(3) + a + a + b + b
    if tier == "uncommon":
        if RNG.random() < 0.7:
            d = _digit()
            return _rand(5) + d + d
        d = _digit()
        return d * 3 + _rand(4)
    return _rand(7)


def generate_digits(tier: str, taken=None) -> str:
    """Генерирует хвост номера заданной редкости. taken — callable(digits) -> bool (занят ли)."""
    for _ in range(200):
        s = _candidate(tier)
        if classify(s) != tier:
            continue
        if taken and taken(s):
            continue
        return s
    # практически недостижимо, но на всякий случай
    return _candidate(tier)


def roll_tier(odds: dict) -> str:
    total = sum(float(odds.get(t, 0)) for t in TIERS)
    r = RNG.random() * total
    acc = 0.0
    for t in TIERS:
        acc += float(odds.get(t, 0))
        if r < acc:
            return t
    return "common"


def fmt_number(dial: str, prefix: str, digits: str) -> str:
    dial = dial if dial.startswith("+") else "+" + dial
    return f"{dial} {prefix} {digits[:3]}-{digits[3:5]}-{digits[5:]}"


def masked_number(dial: str, prefix: str, digits: str, revealed: int) -> str:
    """Для анимации: первые `revealed` цифр открыты, остальные ▒."""
    shown = digits[:revealed] + "▒" * (7 - revealed)
    return fmt_number(dial, prefix, shown)


# ───────────────────────── индивидуальная оценка номера ─────────────────────────
# Базовая стоимость редкости умножается на «престиж» ключевой цифры, бонусы за
# особенности комбинации и небольшой случайный разброс — каждый номер стоит по-своему.
DIGIT_MULT = {"7": 1.5, "8": 1.3, "9": 1.2, "0": 1.15, "5": 1.05, "1": 1.0, "3": 1.0, "6": 1.0, "2": 0.95, "4": 0.8}
DIGIT_NAMES = {"7": "🍀 счастливые семёрки", "8": "🧧 богатые восьмёрки", "9": "🔝 девятки", "0": "⭕ нули",
               "4": "🪦 четвёрки (не любят)"}


def key_digit(s: str) -> str:
    """Цифра, определяющая характер номера: самая частая (если ≥3 раз), иначе последняя."""
    best, cnt = max(((d, s.count(d)) for d in set(s)), key=lambda x: (x[1], x[0]))
    return best if cnt >= 3 else s[-1]


def features(s: str) -> list[tuple[str, float]]:
    """Особенности комбинации → (подпись, бонус к стоимости)."""
    out: list[tuple[str, float]] = []
    kd = key_digit(s)
    if kd in DIGIT_NAMES and DIGIT_MULT[kd] != 1.0:
        out.append((DIGIT_NAMES[kd], DIGIT_MULT[kd] - 1.0))
    if len(set(s)) > 1 and len(set(s[:3])) == 1:
        out.append(("🎯 красивая первая тройка", 0.25))
    if s[:3] == s[4:][::-1] and len(set(s)) > 1:
        out.append(("🪞 зеркальный номер", 0.20))
    if "007" in s:
        out.append(("🕶 агент 007", 0.15))
    if 1 < len(set(s)) <= 2:
        out.append(("🎭 всего две разные цифры", 0.30))
    if 1950 <= int(s[3:]) <= 2030:
        out.append((f"📅 год {s[3:]}", 0.10))
    if s[3:] in ("1111", "2222", "3333", "5555", "6666") and len(set(s)) > 1:
        out.append(("🎰 четвёрка одинаковых", 0.05))
    return out


def appraise(digits: str, tier: str, rng=RNG) -> int:
    """Индивидуальная стоимость номера в рублях."""
    base = TIER[tier]["value"]
    mult = DIGIT_MULT.get(key_digit(digits), 1.0)
    bonus = 1.0 + sum(b for _, b in features(digits) if b > 0)
    spread = rng.uniform(0.85, 1.15)
    v = base * mult * bonus * spread
    step = 10 if v < 1_000 else (50 if v < 10_000 else 500)
    return max(10, int(round(v / step) * step))


def value_range(tier: str) -> tuple[int, int]:
    """Ориентировочный диапазон стоимости номеров редкости (для подсказок)."""
    base = TIER[tier]["value"]
    return int(base * 0.8 * 0.85), int(base * 1.5 * 1.45 * 1.15)


def normalize_odds(odds: dict) -> dict:
    out = {}
    for t in TIERS:
        try:
            out[t] = max(0.0, float(odds.get(t, 0)))
        except (TypeError, ValueError):
            out[t] = 0.0
    if sum(out.values()) <= 0:
        out = dict(DEFAULT_ODDS)
    return out


def odds_ev(odds: dict) -> float:
    """Ожидаемая стоимость номера при данных шансах."""
    odds = normalize_odds(odds)
    total = sum(odds.values())
    return sum(odds[t] / total * tier_value(t) for t in TIERS)
