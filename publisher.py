from __future__ import annotations

import asyncio

from telethon.errors import RPCError
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import GetDiscussionMessageRequest


_LIMITED_ERROR_NAMES = {
    "FloodWaitError",
    "SlowModeWaitError",
    "PeerFloodError",
    "UserBannedInChannelError",
    "ChatWriteForbiddenError",
    "UserRestrictedError",
    "ChatRestrictedError",
    "ChatAdminRequiredError",
    "UserDeactivatedBanError",
}

_AUTO_LEAVE_ERROR_NAMES = {
    "UserBannedInChannelError",
    "ChatWriteForbiddenError",
    "ChatRestrictedError",
}


def _format_telegram_error(exc: BaseException, *, stage: str) -> str:
    """Build a readable diagnostic without hiding Telegram's original text."""
    error_type = type(exc).__name__
    message = str(exc).strip() or repr(exc)

    parts = [
        f"Этап: {stage}",
        f"Тип: {error_type}",
        f"Сообщение Telegram: {message}",
    ]

    code = getattr(exc, "code", None)
    if code is not None:
        parts.append(f"RPC code: {code}")

    seconds = getattr(exc, "seconds", None)
    if seconds is not None:
        try:
            parts.append(f"Ожидание: {int(seconds)} сек.")
        except Exception:
            parts.append(f"Ожидание: {seconds}")

    request = getattr(exc, "request", None)
    if request is not None:
        parts.append(f"Запрос: {type(request).__name__}")

    return "\n".join(parts)


def _remember_error(inst: dict, detail: str, error_type: str = "") -> None:
    inst["last_publish_error"] = str(detail)
    inst["last_publish_error_type"] = str(error_type)


def _clear_error(inst: dict) -> None:
    inst["last_publish_error"] = ""
    inst["last_publish_error_type"] = ""


async def _leave_peer_safely(client, peer, label: str) -> str:
    try:
        await client(LeaveChannelRequest(peer))
        return f"{label}: успешно покинут."
    except Exception as exc:
        name = type(exc).__name__
        if name == "UserNotParticipantError":
            return f"{label}: аккаунт уже не состоит в нём."
        return f"{label}: выйти не удалось — {name}: {exc}"


async def _cleanup_after_permanent_restriction(inst: dict, item, root) -> str:
    client = inst["client"]
    notes = []

    # The actual ban normally belongs to the linked discussion group because
    # that is where SendMessageRequest is sent.
    try:
        discussion_peer = root.peer_id
    except Exception:
        discussion_peer = None

    if discussion_peer is not None:
        notes.append(
            await _leave_peer_safely(
                client,
                discussion_peer,
                "Группа обсуждений",
            )
        )

    # Also leave the source broadcast channel if the account is subscribed.
    notes.append(
        await _leave_peer_safely(
            client,
            item.chat_id,
            "Исходный канал",
        )
    )

    return "\n".join(notes)


async def _join_discussion_and_retry(inst: dict, item, root):
    """Join the linked discussion group and retry the comment exactly once."""
    client = inst["client"]

    try:
        await client(JoinChannelRequest(root.peer_id))
        print(
            f"[{inst.get('name', '?')}][TELEGRAM] "
            f"Вступил в группу обсуждений для {item.key}; проверяю защиту."
        )
    except RPCError as join_exc:
        join_name = type(join_exc).__name__
        detail = _format_telegram_error(
            join_exc,
            stage="Вступление в группу обсуждений",
        )
        if join_name == "InviteRequestSentError":
            detail += (
                "\nTelegram принял заявку на вступление. "
                "Комментарий можно будет отправить после одобрения заявки."
            )
        _remember_error(inst, detail, join_name)
        return "PUBLISH_ERROR"
    except Exception as join_exc:
        detail = _format_telegram_error(
            join_exc,
            stage="Вступление в группу обсуждений",
        )
        _remember_error(inst, detail, type(join_exc).__name__)
        return "PUBLISH_ERROR"

    captcha_manager = inst.get("captcha_manager")
    if captcha_manager is not None:
        # Give Telegram/guard bots a short moment to deliver the verification
        # challenge triggered by joining the discussion group.
        await asyncio.sleep(1.5)
        if captcha_manager.has_recent_pending(max_age_seconds=20):
            detail = (
                "После вступления в группу обсуждений Telegram/защитный бот "
                "потребовал ручную проверку. Откройте вкладку «Проверки» "
                "в NeuroComment и ответьте на неё там."
            )
            _remember_error(inst, detail, "CaptchaRequired")
            return "CAPTCHA_REQUIRED"

    try:
        await client.send_message(
            entity=root.peer_id,
            message=item.comment,
            reply_to=root.id,
        )
        _clear_error(inst)
        return True
    except RPCError as retry_exc:
        captcha_manager = inst.get("captcha_manager")
        if (
            captcha_manager is not None
            and captcha_manager.has_recent_pending(max_age_seconds=30)
        ):
            detail = (
                "После вступления в группу обсуждений обнаружена ручная "
                "антиспам-проверка. Ответьте на неё во вкладке «Проверки»."
            )
            _remember_error(inst, detail, "CaptchaRequired")
            return "CAPTCHA_REQUIRED"

        detail = _format_telegram_error(
            retry_exc,
            stage="Повторная отправка после вступления в обсуждение",
        )
        _remember_error(inst, detail, type(retry_exc).__name__)
        return _result_for_rpc_error(retry_exc)
    except Exception as retry_exc:
        detail = _format_telegram_error(
            retry_exc,
            stage="Повторная отправка после вступления в обсуждение",
        )
        _remember_error(inst, detail, type(retry_exc).__name__)
        return "PUBLISH_ERROR"


def _result_for_rpc_error(exc: RPCError) -> str:
    if type(exc).__name__ in _LIMITED_ERROR_NAMES:
        return "ACCOUNT_LIMITED"
    return "PUBLISH_ERROR"


async def publish_comment(inst: dict, item):
    """Publish a generated comment and keep the exact Telegram failure reason.

    Compatibility:
      True               -> published
      ACCOUNT_LIMITED    -> Telegram/account/chat restriction; keep task
      PUBLISH_ERROR      -> other publish error; keep task
      PERMANENT_RESTRICTION -> banned/write-forbidden; do not retry
      CAPTCHA_REQUIRED    -> wait for a manual verification answer
    """
    client = inst["client"]
    _clear_error(inst)

    try:
        discussion = await client(
            GetDiscussionMessageRequest(
                peer=item.chat_id,
                msg_id=item.message_id,
            )
        )
    except RPCError as exc:
        detail = _format_telegram_error(
            exc,
            stage="Получение группы обсуждений",
        )
        _remember_error(inst, detail, type(exc).__name__)
        return _result_for_rpc_error(exc)
    except Exception as exc:
        detail = _format_telegram_error(
            exc,
            stage="Получение группы обсуждений",
        )
        _remember_error(inst, detail, type(exc).__name__)
        return "PUBLISH_ERROR"

    if not getattr(discussion, "messages", None):
        detail = (
            "Этап: Получение группы обсуждений\n"
            "Тип: DiscussionMessageNotFound\n"
            "Сообщение: Telegram не вернул сообщение обсуждения для этого поста."
        )
        _remember_error(inst, detail, "DiscussionMessageNotFound")
        return "PUBLISH_ERROR"

    root = discussion.messages[0]

    try:
        await client.send_message(
            entity=root.peer_id,
            message=item.comment,
            reply_to=root.id,
        )
        _clear_error(inst)
        return True

    except RPCError as exc:
        error_name = type(exc).__name__

        captcha_manager = inst.get("captcha_manager")
        if (
            captcha_manager is not None
            and captcha_manager.has_recent_pending(max_age_seconds=30)
        ):
            detail = (
                "Защитный бот требует ручную проверку перед публикацией. "
                "Откройте вкладку «Проверки» в NeuroComment."
            )
            _remember_error(inst, detail, "CaptchaRequired")
            return "CAPTCHA_REQUIRED"

        # Telegram can allow reading a channel but require explicit membership
        # in its linked discussion group before the first comment.
        if error_name == "ChatGuestSendForbiddenError":
            return await _join_discussion_and_retry(inst, item, root)

        detail = _format_telegram_error(
            exc,
            stage="Отправка комментария",
        )

        if error_name in _AUTO_LEAVE_ERROR_NAMES:
            cleanup_note = await _cleanup_after_permanent_restriction(
                inst, item, root
            )
            detail = detail + "\n" + cleanup_note
            _remember_error(inst, detail, error_name)
            return "PERMANENT_RESTRICTION"

        _remember_error(inst, detail, error_name)
        return _result_for_rpc_error(exc)

    except Exception as exc:
        detail = _format_telegram_error(
            exc,
            stage="Отправка комментария",
        )
        _remember_error(inst, detail, type(exc).__name__)
        return "PUBLISH_ERROR"
