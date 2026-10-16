from __future__ import annotations

import inspect
import time
from pathlib import Path

from telethon import events

from app_paths import runtime_dir


_CHALLENGE_KEYWORDS = (
    "captcha",
    "капч",
    "провер",
    "verify",
    "verification",
    "вериф",
    "защит",
    "defensy",
    "нажм",
    "выберите",
    "подтверд",
    "робот",
    "человек",
)

_SUCCESS_KEYWORDS = (
    "проверка пройдена",
    "проверка успешно",
    "успешно прош",
    "вы прошли провер",
    "верификация пройдена",
    "verification complete",
    "verification passed",
    "successfully verified",
    "доступ разреш",
    "можете писать",
    "можете отправлять",
)


class CaptchaManager:
    """Manual Telegram verification relay.

    The manager never solves a challenge automatically. It only mirrors a
    likely anti-spam verification into NeuroComment and sends the exact answer
    or button choice selected by the user through the already-authorized
    Telethon session.
    """

    def __init__(self, account: str, client, bridge, on_resolved=None):
        self.account = str(account)
        self.client = client
        self.bridge = bridge
        self.on_resolved = on_resolved
        self._registered = False
        self._handler = None

    def register(self) -> None:
        if self._registered:
            return

        @self.client.on(events.NewMessage())
        async def verification_handler(event):
            await self._handle_message(event)

        self._handler = verification_handler
        self._registered = True

    async def _handle_message(self, event) -> None:
        if getattr(event, "out", False):
            return

        message = getattr(event, "message", None)
        if message is None:
            return

        try:
            sender = await event.get_sender()
        except Exception:
            sender = None

        # We intentionally only mirror bot-originated checks. This prevents
        # ordinary user messages from appearing as verification challenges.
        if not bool(getattr(sender, "bot", False)):
            return

        text = str(getattr(event, "raw_text", "") or "").strip()
        low = text.lower()

        sender_username = str(getattr(sender, "username", "") or "")
        sender_first = str(getattr(sender, "first_name", "") or "")
        sender_last = str(getattr(sender, "last_name", "") or "")
        sender_name = " ".join(x for x in (sender_first, sender_last) if x).strip()
        identity = f"{sender_username} {sender_name}".lower()

        # A success message closes the most recent pending challenge from this
        # account/chat and can resume paused autopilot comments.
        if any(token in low for token in _SUCCESS_KEYWORDS):
            resolved = self.bridge.resolve_recent_captcha(
                self.account,
                chat_id=int(getattr(event, "chat_id", 0) or 0),
                max_age_seconds=900,
            )
            if resolved is not None:
                print(
                    f"[{self.account}][VERIFY] Проверка пройдена: "
                    f"{resolved.get('id', '?')}"
                )
                await self._notify_resolved(resolved)
            return

        buttons = self._extract_buttons(message)
        has_keyword = any(token in low for token in _CHALLENGE_KEYWORDS)
        named_guard = any(
            token in identity
            for token in ("defensy", "captcha", "verify", "защит")
        )

        # Require a strong signal: either a known guard identity, or a bot
        # message combining verification wording with interactive buttons/media.
        has_media = bool(
            getattr(message, "photo", None)
            or getattr(message, "document", None)
        )
        if not (
            named_guard and (has_keyword or buttons or has_media)
            or has_keyword and (buttons or has_media)
        ):
            return

        try:
            chat = await event.get_chat()
        except Exception:
            chat = None

        chat_title = (
            str(getattr(chat, "title", "") or "")
            or str(getattr(chat, "username", "") or "")
            or str(getattr(event, "chat_id", "") or "")
        )

        challenge_id = (
            f"{self.account}:"
            f"{int(getattr(event, 'chat_id', 0) or 0)}:"
            f"{int(getattr(message, 'id', 0) or 0)}"
        )

        media_path = ""
        if has_media:
            media_path = await self._download_media(message, challenge_id)

        self.bridge.save_captcha(
            challenge_id,
            account=self.account,
            chat_id=int(getattr(event, "chat_id", 0) or 0),
            message_id=int(getattr(message, "id", 0) or 0),
            chat_title=chat_title,
            sender_id=int(getattr(sender, "id", 0) or 0),
            sender_name=sender_name,
            sender_username=sender_username,
            challenge_text=text,
            buttons=buttons,
            media_path=media_path,
            status="Ожидает ответа",
        )

        print(
            f"[{self.account}][VERIFY] Новая ручная проверка: "
            f"{chat_title} / {sender_username or sender_name or 'bot'}"
        )

    def _extract_buttons(self, message) -> list[dict]:
        result = []
        rows = getattr(message, "buttons", None) or []
        for row_index, row in enumerate(rows):
            for col_index, button in enumerate(row or []):
                text = str(getattr(button, "text", "") or "").strip()
                url = str(getattr(button, "url", "") or "").strip()
                result.append(
                    {
                        "text": text or f"Кнопка {row_index + 1}.{col_index + 1}",
                        "row": row_index,
                        "col": col_index,
                        "type": "url" if url else "callback",
                        "url": url,
                    }
                )
        return result

    async def _download_media(self, message, challenge_id: str) -> str:
        folder = runtime_dir() / "captcha_media" / self.account
        folder.mkdir(parents=True, exist_ok=True)
        safe = challenge_id.replace(":", "_").replace("/", "_")
        target = folder / safe

        try:
            downloaded = await message.download_media(file=str(target))
            return str(downloaded or "")
        except Exception as exc:
            print(
                f"[{self.account}][VERIFY] Не удалось скачать изображение проверки: "
                f"{type(exc).__name__}: {exc}"
            )
            return ""

    def has_recent_pending(self, max_age_seconds: float = 30.0) -> bool:
        return (
            self.bridge.latest_pending_captcha(
                self.account,
                max_age_seconds=max_age_seconds,
            )
            is not None
        )

    async def handle_command(
        self,
        action: str,
        challenge_id: str,
        payload: str | None = None,
    ) -> str:
        challenge = self.bridge.get_captcha(challenge_id)
        if challenge is None:
            return "Проверка уже отсутствует."

        if str(challenge.get("account") or "") != self.account:
            raise RuntimeError("Проверка принадлежит другому Telegram-аккаунту.")

        chat_id = int(challenge.get("chat_id") or 0)
        message_id = int(challenge.get("message_id") or 0)

        try:
            if action == "captcha_text":
                answer = str(payload or "").strip()
                if not answer:
                    raise ValueError("Введите ответ на проверку.")

                await self.client.send_message(chat_id, answer)
                self.bridge.set_captcha_status(
                    challenge_id,
                    "Ответ отправлен",
                )
                print(
                    f"[{self.account}][VERIFY] Текстовый ответ отправлен: "
                    f"{challenge_id}"
                )
                return "Ответ отправлен"

            if action == "captcha_click":
                raw = str(payload or "")
                try:
                    row_text, col_text = raw.split(":", 1)
                    row = int(row_text)
                    col = int(col_text)
                except Exception as exc:
                    raise ValueError("Некорректная кнопка проверки.") from exc

                message = await self.client.get_messages(
                    chat_id,
                    ids=message_id,
                )
                if message is None:
                    raise RuntimeError("Сообщение проверки больше не найдено.")

                rows = getattr(message, "buttons", None) or []
                if row < 0 or row >= len(rows) or col < 0 or col >= len(rows[row]):
                    raise RuntimeError("Кнопка проверки больше недоступна.")

                button = rows[row][col]
                if getattr(button, "url", None):
                    raise RuntimeError(
                        "Это ссылка, а не Telegram-кнопка. "
                        "Откройте её кнопкой в интерфейсе NeuroComment."
                    )

                await message.click(row, col)
                self.bridge.set_captcha_status(
                    challenge_id,
                    "Ответ отправлен",
                )
                print(
                    f"[{self.account}][VERIFY] Нажата кнопка проверки: "
                    f"{challenge_id} [{row}:{col}]"
                )
                return "Кнопка нажата"

            if action == "captcha_done":
                self.bridge.set_captcha_status(
                    challenge_id,
                    "Пройдена",
                )
                await self._notify_resolved(challenge)
                print(
                    f"[{self.account}][VERIFY] Пользователь отметил проверку пройденной: "
                    f"{challenge_id}"
                )
                return "Проверка отмечена как пройденная"

            if action == "captcha_delete":
                self.bridge.remove_captcha(challenge_id)
                print(
                    f"[{self.account}][VERIFY] Проверка удалена: {challenge_id}"
                )
                return "Проверка удалена"

            raise ValueError(f"Неизвестное действие проверки: {action}")

        except Exception as exc:
            self.bridge.set_captcha_status(
                challenge_id,
                "Ошибка отправки",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    async def _notify_resolved(self, challenge: dict) -> None:
        if self.on_resolved is None:
            return
        result = self.on_resolved(challenge)
        if inspect.isawaitable(result):
            await result
