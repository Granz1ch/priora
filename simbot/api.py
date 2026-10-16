"""Асинхронный клиент Bot API (ShapitoGram / Telegram).

Терпим к особенностям клона: словари вместо строгих моделей, повторные попытки
при сетевых ошибках, уважение retry_after, безопасные edit/delete/answer.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

log = logging.getLogger("api")


class APIError(Exception):
    def __init__(self, code: int, description: str, retry_after: int = 0):
        super().__init__(f"[{code}] {description}")
        self.code = code
        self.description = description or ""
        self.retry_after = retry_after


def kb(rows: list[list[tuple[str, str] | dict]]) -> dict:
    """Inline-клавиатура из списка строк. Элемент: (текст, callback_data) или готовый dict."""
    keyboard = []
    for row in rows:
        if not row:
            continue
        line = []
        for btn in row:
            if isinstance(btn, dict):
                line.append(btn)
            else:
                text, data = btn
                line.append({"text": text, "callback_data": data[:64]})
        keyboard.append(line)
    return {"inline_keyboard": keyboard}


class API:
    def __init__(self, token: str, base: str = "https://shapitogram.xyz"):
        self.base = base.rstrip("/") + "/bot" + token + "/"
        self.session: aiohttp.ClientSession | None = None
        self.sem = asyncio.Semaphore(16)

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45),
            json_serialize=lambda o: json.dumps(o, ensure_ascii=False),
        )

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    # ───────────────────────── низкий уровень ─────────────────────────
    async def call(self, method: str, _files: dict | None = None, _timeout: float | None = None,
                   _retries: int = 3, **params: Any) -> Any:
        assert self.session is not None, "API.start() не вызван"
        url = self.base + method
        clean = {k: v for k, v in params.items() if v is not None}
        timeout = aiohttp.ClientTimeout(total=_timeout) if _timeout else None
        last_err: Exception | None = None
        for attempt in range(_retries + 1):
            try:
                if method == "getUpdates":
                    cm = self.session.post(url, json=clean, timeout=timeout)
                    body = await self._do(cm)
                else:
                    async with self.sem:
                        if _files:
                            form = aiohttp.FormData()
                            for k, v in clean.items():
                                form.add_field(k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                            for field, (fname, data) in _files.items():
                                form.add_field(field, data, filename=fname)
                            cm = self.session.post(url, data=form, timeout=timeout)
                        else:
                            cm = self.session.post(url, json=clean, timeout=timeout)
                        body = await self._do(cm)
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
                last_err = e
                if attempt >= _retries:
                    break
                await asyncio.sleep(1.0 + attempt * 1.5)
                continue
            if body.get("ok"):
                return body.get("result")
            code = int(body.get("error_code") or 0)
            desc = str(body.get("description") or "")
            retry = int((body.get("parameters") or {}).get("retry_after") or 0)
            if code == 429 and attempt < _retries:
                await asyncio.sleep(max(1, retry or 2))
                continue
            raise APIError(code, desc, retry)
        raise APIError(0, f"network: {last_err}")

    @staticmethod
    async def _do(cm) -> dict:
        async with cm as resp:
            text = await resp.text()
            if not text:
                return {"ok": False, "error_code": resp.status, "description": "empty response"}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"ok": False, "error_code": resp.status, "description": text[:200]}

    # ───────────────────────── удобные обёртки ─────────────────────────
    async def send(self, chat_id: int, text: str, markup: dict | None = None, **kw) -> dict | None:
        try:
            return await self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                                   reply_markup=markup, link_preview_options={"is_disabled": True}, **kw)
        except APIError as e:
            log.warning("sendMessage %s failed: %s", chat_id, e)
            return None

    async def send_raw(self, chat_id: int, text: str, markup: dict | None = None, **kw) -> dict:
        """Как send(), но пробрасывает ошибку (нужно, когда важно знать результат)."""
        return await self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                               reply_markup=markup, link_preview_options={"is_disabled": True}, **kw)

    async def edit(self, chat_id: int, message_id: int, text: str, markup: dict | None = None) -> dict | None:
        try:
            return await self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                                   parse_mode="HTML", reply_markup=markup,
                                   link_preview_options={"is_disabled": True})
        except APIError as e:
            if "not modified" in e.description.lower():
                return None
            log.debug("editMessageText failed: %s", e)
            return None

    async def delete(self, chat_id: int, message_id: int) -> bool:
        try:
            await self.call("deleteMessage", chat_id=chat_id, message_id=message_id, _retries=1)
            return True
        except APIError:
            return False

    async def answer_cb(self, cb_id: str, text: str | None = None, alert: bool = False) -> None:
        try:
            await self.call("answerCallbackQuery", callback_query_id=cb_id, text=text,
                            show_alert=True if alert else None, _retries=1)
        except APIError as e:
            log.debug("answerCallbackQuery failed: %s", e)

    async def dice(self, chat_id: int, emoji: str = "🎲", **kw) -> dict | None:
        try:
            return await self.call("sendDice", chat_id=chat_id, emoji=emoji, **kw)
        except APIError as e:
            log.warning("sendDice failed: %s", e)
            return None

    async def photo(self, chat_id: int, photo: str | bytes, caption: str = "", markup: dict | None = None,
                    filename: str = "banner.jpg") -> dict | None:
        try:
            if isinstance(photo, bytes):
                return await self.call("sendPhoto", _files={"photo": (filename, photo)}, chat_id=str(chat_id),
                                       caption=caption, parse_mode="HTML", reply_markup=markup, _retries=1)
            return await self.call("sendPhoto", chat_id=chat_id, photo=photo, caption=caption,
                                   parse_mode="HTML", reply_markup=markup, _retries=1)
        except APIError as e:
            log.warning("sendPhoto failed: %s", e)
            return None

    async def chat_action(self, chat_id: int, action: str = "typing") -> None:
        try:
            await self.call("sendChatAction", chat_id=chat_id, action=action, _retries=0)
        except APIError:
            pass

    async def invoice(self, chat_id: int, title: str, description: str, payload: str,
                      stars: int, label: str) -> dict:
        return await self.call(
            "sendInvoice", chat_id=chat_id, title=title, description=description, payload=payload,
            currency="XTR", prices=[{"label": label, "amount": int(stars)}],
        )

    async def answer_pre_checkout(self, query_id: str, ok: bool, error: str | None = None) -> None:
        try:
            await self.call("answerPreCheckoutQuery", pre_checkout_query_id=query_id, ok=ok,
                            error_message=None if ok else (error or "Платёж отклонён"), _retries=1)
        except APIError as e:
            log.warning("answerPreCheckoutQuery failed: %s", e)

    async def refund_stars(self, user_id: int, charge_id: str) -> None:
        await self.call("refundStarPayment", user_id=user_id, telegram_payment_charge_id=charge_id, _retries=0)
