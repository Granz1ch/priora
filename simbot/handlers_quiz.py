"""Викторины в чате (создают админы): слово из букв, арифметика или «вопрос | ответ». Первый верный ответ забирает награду."""
from __future__ import annotations

import ast
import logging
import operator
import re

from .core import Ctx, UserError, router
from .numbers import RNG
from .ui import esc, money

log = logging.getLogger("quiz")

LEVELS = {"100": (1, "🟢 лёгкая"), "200": (2, "🟡 средняя"), "300": (3, "🔴 сложная"),
          "легко": (1, "🟢 лёгкая"), "средне": (2, "🟡 средняя"), "сложно": (3, "🔴 сложная"),
          "1": (1, "🟢 лёгкая"), "2": (2, "🟡 средняя"), "3": (3, "🔴 сложная")}
LEVEL_NAMES = {1: "🟢 лёгкая", 2: "🟡 средняя", 3: "🔴 сложная"}
MATH_RE = re.compile(r"^[\d\s+\-*/×÷().,^]+$")
WORD_RE = re.compile(r"^[A-Za-zА-Яа-яЁё]{3,20}$")
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}

QUIZ_USAGE = ("🧠 Викторина: <code>викторина 100 500 слово</code> — уровень (100 легко / 200 средне / 300 сложно), "
              "награда ₽, задание: слово (бот перемешает буквы), пример (<code>10+2</code>) или "
              "<code>вопрос | ответ</code> · «викторина стоп»")


def safe_eval(expr: str) -> float:
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", ".")
    tree = ast.parse(expr, mode="eval")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(b) > 6:
                raise ValueError("слишком большая степень")
            return OPS[type(node.op)](a, b)
        if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
            return OPS[type(node.op)](ev(node.operand))
        raise ValueError("недопустимое выражение")
    return ev(tree)


def norm_answer(s: str) -> str:
    s = s.strip().lower().replace("ё", "е").strip(".,!?;:\"'«»")
    s = re.sub(r"\s+", " ", s)
    try:
        f = float(s.replace(",", "."))
        return str(int(f)) if f == int(f) else f"{f:g}"
    except ValueError:
        return s


def build_task(raw: str) -> tuple[str, str]:
    """Возвращает (текст вопроса, нормализованный ответ)."""
    raw = raw.strip()
    if "|" in raw:
        q, _, a = raw.partition("|")
        q, a = q.strip(), a.strip()
        if not q or not a:
            raise UserError("Формат: <code>вопрос | ответ</code>")
        return f"❓ {esc(q)}", norm_answer(a)
    if MATH_RE.match(raw) and any(ch in raw for ch in "+-*/×÷^"):
        try:
            val = safe_eval(raw)
        except Exception:
            raise UserError("Не могу посчитать пример")
        ans = norm_answer(f"{val:g}" if isinstance(val, float) else str(val))
        return f"🧮 Сколько будет <b>{esc(raw)}</b>?", ans
    if WORD_RE.match(raw):
        letters = list(raw.upper())
        for _ in range(10):
            RNG.shuffle(letters)
            if "".join(letters) != raw.upper():
                break
        return f"🔤 Составь слово из букв: <b>{' '.join(letters)}</b>", norm_answer(raw)
    raise UserError("Задание — одно слово, пример (10+2) или «вопрос | ответ»")


@router.cmd("викторина", "викт", "quiz", section="quiz")
async def cmd_quiz(ctx: Ctx) -> None:
    sub = ctx.arg(0).lower()
    if not sub:
        q = ctx.db.active_quiz(ctx.chat_id)
        if not q:
            raise UserError("Сейчас нет активной викторины" + (f"\n{QUIZ_USAGE}" if ctx.rank >= 3 else ""))
        await ctx.reply(f"🧠 <b>ВИКТОРИНА</b> · {LEVEL_NAMES[int(q['level'])]} · награда <b>{money(q['reward'])}</b>\n"
                        f"{q['question']}\n<i>Пиши ответ прямо в чат!</i>")
        return
    if ctx.rank < 3:
        raise UserError("Викторины создают админы")
    if sub in ("стоп", "stop", "отмена"):
        q = ctx.db.active_quiz(ctx.chat_id)
        if not q or not ctx.db.finish_quiz(int(q["id"]), "cancelled"):
            raise UserError("Нет активной викторины")
        await ctx.reply(f"🛑 Викторина остановлена · ответ был: <b>{esc(q['answer'])}</b>")
        return
    if sub not in LEVELS:
        raise UserError(QUIZ_USAGE)
    level, level_name = LEVELS[sub]
    rw = ctx.arg(1)
    if not rw or not rw.rstrip("кk").replace(".", "", 1).isdigit():
        raise UserError(QUIZ_USAGE)
    from .handlers_games import parse_amount
    reward = parse_amount(rw, 0)
    if reward <= 0 or reward > 10_000_000:
        raise UserError("Награда от 1 до 10 000 000 ₽")
    task = ctx.rest(2)
    if not task:
        raise UserError(QUIZ_USAGE)
    if ctx.db.active_quiz(ctx.chat_id):
        raise UserError("В этом чате уже идёт викторина · «викторина стоп»")
    question, answer = build_task(task)
    qid = ctx.db.create_quiz(ctx.chat_id, ctx.uid, level, reward, question, answer)
    await ctx.delete_incoming()
    ttl = int(ctx.setting("quiz_ttl", 600))
    msg = await ctx.send(f"🧠 <b>ВИКТОРИНА</b> · {level_name} · награда <b>{money(reward)}</b>\n{question}\n"
                         f"<i>Первый верный ответ в чате забирает приз · {ttl // 60} мин</i>")
    ctx.db.set_quiz_msg(qid, (msg or {}).get("message_id"))
    ctx.db.audit(ctx.uid, "quiz", {"chat": ctx.chat_id, "reward": reward, "answer": answer})


async def quiz_answer_hook(ctx: Ctx) -> bool:
    q = ctx.db.active_quiz(ctx.chat_id)
    if not q:
        return False
    if norm_answer(ctx.text) != q["answer"]:
        return False
    if not ctx.db.finish_quiz(int(q["id"]), "done", ctx.uid):
        return True
    bal = ctx.db.change_balance(ctx.uid, int(q["reward"]), f"quiz:{q['id']}", ref=f"quiz:{q['id']}")
    await ctx.reply(f"🎉 <b>Верно!</b> {ctx.who} первым ответил «{esc(q['answer'])}» и получает <b>+{money(q['reward'])}</b> "
                    f"· 💰 {money(bal)}")
    return True


router.texts.insert(0, quiz_answer_hook)   # ответы на викторину проверяем раньше остальных текстовых хуков


async def expire_quizzes(bot) -> None:
    ttl = int(bot.db.setting("quiz_ttl", 600))
    for q in bot.db.expired_quizzes(ttl):
        if bot.db.finish_quiz(int(q["id"]), "expired"):
            await bot.api.send(int(q["chat_id"]), f"⏳ Викторина завершена — никто не ответил. Ответ: <b>{esc(q['answer'])}</b>")
