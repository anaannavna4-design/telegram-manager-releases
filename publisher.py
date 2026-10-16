from __future__ import annotations

from telethon.errors import RPCError
from telethon.tl.functions.channels import LeaveChannelRequest
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


async def _leave_source_after_permanent_restriction(inst: dict, item) -> str:
    client = inst["client"]
    try:
        await client(LeaveChannelRequest(item.chat_id))
        return "Исходный канал автоматически покинут."
    except Exception as exc:
        return (
            "Автовыход из исходного канала не удался: "
            f"{type(exc).__name__}: {exc}"
        )


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
        detail = _format_telegram_error(
            exc,
            stage="Отправка комментария",
        )

        error_name = type(exc).__name__
        if error_name in _AUTO_LEAVE_ERROR_NAMES:
            leave_note = await _leave_source_after_permanent_restriction(inst, item)
            detail = detail + "\n" + leave_note

        _remember_error(inst, detail, error_name)
        return _result_for_rpc_error(exc)

    except Exception as exc:
        detail = _format_telegram_error(
            exc,
            stage="Отправка комментария",
        )
        _remember_error(inst, detail, type(exc).__name__)
        return "PUBLISH_ERROR"
