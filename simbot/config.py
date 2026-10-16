"""Конфигурация: владельцы бота прописаны здесь (OWNER_IDS), остальное — из переменных окружения."""
import os

# ─── Владельцы бота (титул «Владелец», ранг 5). Добавь сюда свои Telegram ID ───
OWNER_IDS: frozenset[int] = frozenset({
    1780243337,
})


def _ids(raw: str) -> set[int]:
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


class Config:
    def __init__(self) -> None:
        self.token = os.environ.get("BOT_TOKEN", "").strip()
        if not self.token:
            raise SystemExit("BOT_TOKEN не задан. Укажи токен бота в переменных окружения.")
        self.api_base = os.environ.get("API_BASE", "https://shapitogram.xyz").strip()
        # владельцы из кода + (необязательно) дополнительные из переменной ADMIN_IDS
        self.admin_ids = set(OWNER_IDS) | _ids(os.environ.get("ADMIN_IDS", ""))
        self.db_path = os.environ.get("DB_PATH", "simbot.sqlite3")
        if os.path.dirname(self.db_path):
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)  # напр. /var/data на Render
        # bot-hosting даёт SERVER_PORT, Render/Heroku — PORT
        self.health_port = int(os.environ.get("SERVER_PORT") or os.environ.get("PORT") or 0)
        # Самопинг публичного URL, чтобы Render Free не засыпал (RENDER_EXTERNAL_URL ставится автоматически)
        self.keepalive_url = (os.environ.get("KEEPALIVE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
        self.poll_timeout = int(os.environ.get("POLL_TIMEOUT", "25"))
        self.log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
        self.banner_path = os.environ.get("BANNER_PATH", "assets/banner.jpg")


# ─── Экономика (значения по умолчанию, большинство правится админом в /settings) ───
STARS_RATE = 1000               # 1 ⭐ = 1000 ₽
DEFAULT_SETTINGS = {
    "welcome_bonus": 5000,      # стартовый бонус новому игроку
    "daily_min": 500,           # ежедневный бонус: от
    "daily_max": 2500,          # ежедневный бонус: до
    "ref_bonus": 1000,          # бонус за приглашённого друга
    "min_bet": 50,
    "max_bet": 1_000_000,
    "duel_fee": 5,              # комиссия с банка дуэли, %
    "maintenance": 0,           # режим техработ
    "min_stars": 1,
    "max_stars": 10000,
    "sell_rate": 100,           # продажа номера боту: % от стоимости
    "clan_price": 50_000,       # создание клана
    "quiz_ttl": 600,            # викторина живёт 10 минут
    "warn_limit": 3,            # предупреждений до бана
    "market_fee": 5,            # комиссия рынка номеров, %
}

# ─── Титулы (ранги) ───
RANK_NAMES = {0: "Игрок", 1: "Модер", 2: "Гл. модер", 3: "Админ", 4: "Гл. админ", 5: "Владелец"}
RANK_EMOJI = {0: "👤", 1: "🛡", 2: "🛡", 3: "⚜️", 4: "👑", 5: "💎"}
RANK_ALIASES = {
    "модер": 1, "модератор": 1, "мод": 1,
    "глмодер": 2, "гл.модер": 2, "главмодер": 2, "главныймодер": 2, "старшиймодер": 2, "стмодер": 2,
    "админ": 3, "администратор": 3, "адм": 3,
    "гладмин": 4, "гл.админ": 4, "главадмин": 4, "главныйадмин": 4,
    "владелец": 5, "овнер": 5, "создатель": 5,
}

# ─── Разделы, которые можно включать/выключать ───
SECTIONS = {
    "краш": "crash", "мины": "mines", "слоты": "slots", "кубик": "dice", "ап": "upgrader", "дуэль": "duel",
    "крутить": "spin", "кланы": "clans", "викторина": "quiz", "передача": "transfer", "пополнение": "deposit",
    "бонус": "bonus", "промо": "promo", "рынок": "market",
}
SECTION_TITLES = {v: k for k, v in SECTIONS.items()}

# Коэффициенты игр
CRASH_EDGE = 0.04               # преимущество казино в краше
CRASH_MAX = 100.0
CRASH_TICK = 1.0                # секунд между обновлениями
CRASH_K = 0.1386                # exp(k*t): x2 за 5 сек
MINES_EDGE = 0.04
DICE_EXACT = 5.5
DICE_HALF = 1.9
SLOT_JACKPOT = 25               # 7️⃣7️⃣7️⃣
SLOT_TRIPLE = 6                 # три одинаковых (не семёрки)
SLOT_TWO_SEVENS = 1.5           # две семёрки
UPGRADER_EDGE = 0.08
UPGRADER_MULTS = [1.5, 2, 3, 5, 10, 20, 50, 100]
UPGRADE_NUM_EDGE = 0.10
MINES_OPTIONS = [2, 3, 5, 7, 10, 15, 20, 24]
DUEL_TTL = 15 * 60              # дуэль в лобби живёт 15 минут
SPIN_PACKS = [1, 3, 5]
MAX_SPINS_AT_ONCE = 5
CRASH_EDIT_EVERY = 1            # редактировать сообщение краша каждый N-й тик
