"""Telegram digest operations: create-once/pin/edit-forever, single-shot
replacement recovery, and the strict ownership rule — the subsystem may
manage only its own pinned message, never unpin or fight anyone else's.
No network, no real Telegram."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.digest.telegram_ops import DigestTelegramOps
from app.services.telegram.publisher import TelegramPublisher

_OURS = "555"
_TEXT = "📌 <b>ملخص أهم التطورات — آخر 6 ساعات</b>"


def _publisher_with_mocks(
    *,
    edit: object = True,
    chat: dict | None = None,
    pin: bool = True,
    sent_id: str = "900",
) -> TelegramPublisher:
    publisher = TelegramPublisher()
    if isinstance(edit, Exception):
        publisher.edit_message = AsyncMock(side_effect=edit)  # type: ignore[method-assign]
    else:
        publisher.edit_message = AsyncMock(return_value=edit)  # type: ignore[method-assign]
    publisher.publish_message = AsyncMock(return_value=sent_id)  # type: ignore[method-assign]
    publisher.pin_chat_message = AsyncMock(return_value=pin)  # type: ignore[method-assign]
    publisher.get_chat = AsyncMock(return_value=chat)  # type: ignore[method-assign]
    return publisher


def _http_400(text: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.invalid")
    response = httpx.Response(400, request=request, text=text)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def _m(fn: object) -> AsyncMock:
    """mypy-safe access to a method replaced by AsyncMock."""
    return cast(AsyncMock, fn)


@pytest.mark.asyncio
async def test_first_run_creates_and_pins_silently() -> None:
    publisher = _publisher_with_mocks(sent_id="42")
    result = await DigestTelegramOps(publisher).publish_digest(_TEXT, None)
    assert result.message_id == "42"
    assert result.created is True
    assert result.pinned is True
    _m(publisher.publish_message).assert_awaited_once_with(_TEXT)
    _m(publisher.pin_chat_message).assert_awaited_once_with(
        "42", disable_notification=True
    )
    _m(publisher.edit_message).assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_message_edited_in_place_never_resent() -> None:
    chat = {"pinned_message": {"message_id": int(_OURS)}}
    publisher = _publisher_with_mocks(chat=chat)
    result = await DigestTelegramOps(publisher).publish_digest(_TEXT, _OURS)
    assert result.message_id == _OURS
    assert result.created is False
    assert result.pinned is True
    _m(publisher.edit_message).assert_awaited_once_with(_OURS, _TEXT)
    _m(publisher.publish_message).assert_not_awaited()
    _m(publisher.pin_chat_message).assert_not_awaited()  # already the newest pin


@pytest.mark.asyncio
async def test_manually_unpinned_digest_is_repinned() -> None:
    publisher = _publisher_with_mocks(chat={"id": -100})  # no pinned_message
    result = await DigestTelegramOps(publisher).publish_digest(_TEXT, _OURS)
    assert result.pinned is True
    _m(publisher.pin_chat_message).assert_awaited_once_with(
        _OURS, disable_notification=True
    )


@pytest.mark.asyncio
async def test_unrelated_admin_pin_left_untouched() -> None:
    chat = {"pinned_message": {"message_id": 12345}}
    publisher = _publisher_with_mocks(chat=chat)
    result = await DigestTelegramOps(publisher).publish_digest(_TEXT, _OURS)
    assert result.pinned is False  # honestly unverified — never fought
    _m(publisher.pin_chat_message).assert_not_awaited()
    _m(publisher.publish_message).assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_message_single_replacement_send_and_pin() -> None:
    publisher = _publisher_with_mocks(
        edit=_http_400("Bad Request: message to edit not found"), sent_id="600"
    )
    result = await DigestTelegramOps(publisher).publish_digest(_TEXT, _OURS)
    assert result.created is True
    assert result.message_id == "600"
    _m(publisher.edit_message).assert_awaited_once()  # no retry loop
    _m(publisher.publish_message).assert_awaited_once()
    _m(publisher.pin_chat_message).assert_awaited_once_with(
        "600", disable_notification=True
    )


@pytest.mark.asyncio
async def test_uneditable_message_also_takes_replacement_path() -> None:
    publisher = _publisher_with_mocks(
        edit=_http_400("Bad Request: message can't be edited"), sent_id="601"
    )
    result = await DigestTelegramOps(publisher).publish_digest(_TEXT, _OURS)
    assert result.created is True
    assert result.message_id == "601"


@pytest.mark.asyncio
async def test_other_edit_errors_propagate() -> None:
    publisher = _publisher_with_mocks(edit=_http_400("Bad Request: chat not found"))
    with pytest.raises(httpx.HTTPStatusError):
        await DigestTelegramOps(publisher).publish_digest(_TEXT, _OURS)
    _m(publisher.publish_message).assert_not_awaited()


@pytest.mark.asyncio
async def test_pin_failure_never_fails_a_delivered_publish() -> None:
    publisher = _publisher_with_mocks(pin=False, sent_id="700")
    result = await DigestTelegramOps(publisher).publish_digest(_TEXT, None)
    assert result.message_id == "700"
    assert result.pinned is False


@pytest.mark.asyncio
async def test_get_chat_failure_is_nonfatal() -> None:
    publisher = _publisher_with_mocks(chat=None)
    result = await DigestTelegramOps(publisher).publish_digest(_TEXT, _OURS)
    assert result.message_id == _OURS
    assert result.pinned is False
    _m(publisher.pin_chat_message).assert_not_awaited()


def test_unpin_api_methods_never_referenced() -> None:
    root = Path(__file__).resolve().parents[1]
    sources = [
        root / "app" / "services" / "telegram" / "publisher.py",
        root / "app" / "services" / "digest" / "telegram_ops.py",
        root / "app" / "services" / "digest" / "service.py",
        root / "app" / "services" / "digest" / "scheduler.py",
    ]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        # The docstring states the rule; no code path may build these calls.
        assert "unpinAllChatMessages" not in text.replace(
            "``unpinAllChatMessages``", ""
        )
        assert "unpinChatMessage" not in text.replace(
            "``unpinChatMessage``", ""
        ).replace("``unpinAllChatMessages``", "")
        assert "deleteMessage" not in text


def _response(status_code: int, body: dict) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        text=str(body),
        json=lambda: body,
        raise_for_status=lambda: None,
    )


@pytest.mark.asyncio
async def test_publisher_pin_sends_silent_integer_payload() -> None:
    publisher = TelegramPublisher()
    publisher.client.post = AsyncMock(  # type: ignore[method-assign]
        return_value=_response(200, {"ok": True, "result": True})
    )
    assert await publisher.pin_chat_message("55") is True
    await_args = _m(publisher.client.post).await_args
    assert await_args is not None
    payload = await_args.kwargs["json"]
    assert payload["message_id"] == 55
    assert payload["disable_notification"] is True


@pytest.mark.asyncio
async def test_publisher_pin_failure_returns_false() -> None:
    publisher = TelegramPublisher()
    publisher.client.post = AsyncMock(  # type: ignore[method-assign]
        return_value=_response(400, {"ok": False, "description": "not enough rights"})
    )
    assert await publisher.pin_chat_message("55") is False


@pytest.mark.asyncio
async def test_token_never_reaches_pin_or_getchat_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    class _CapturingLogger:
        def _record(self, *args: object, **kwargs: object) -> None:
            captured.append(repr(args) + repr(kwargs))

        info = warning = error = debug = _record

    import app.services.telegram.publisher as publisher_module

    monkeypatch.setattr(publisher_module, "logger", _CapturingLogger())
    publisher = TelegramPublisher()
    publisher.client.post = AsyncMock(  # type: ignore[method-assign]
        return_value=_response(400, {"ok": False, "description": "denied"})
    )
    await publisher.pin_chat_message("55")
    await publisher.get_chat()
    everything = " ".join(captured)
    assert publisher.bot_token not in everything
    assert "api.telegram.org" not in everything
