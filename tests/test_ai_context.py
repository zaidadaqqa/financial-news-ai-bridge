"""Tests for the internal application-context block appended to the OpenAI
request (Phase 2.2 hardening, §D). Inspects the actual outbound request
payload built by OpenAIProvider — never makes a real network call (the
httpx client's .post is mocked)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.openai_provider import OpenAIProvider
from app.services.intelligence.engine import classify_news
from app.services.intelligence.models import SAFE_FALLBACK
from app.services.story.models import RelationshipType, StoryDecision

MOCK_AI_RESPONSE_JSON = (
    '{"headline_ar": "x", "explanation_ar": "x", "market_impact_ar": "x", '
    '"translation_ar": "x", "summary_ar": "x", "what_to_watch_ar": null, '
    '"category": "central_bank", "importance": 3, "confidence": 0.8, '
    '"market_bias": "NEUTRAL", "impact": "x", "affected_assets": [], '
    '"actual": null, "forecast": null, "previous": null, "currency": null, '
    '"company": null, "ticker": null}'
)


def _mock_httpx_response(content: str) -> AsyncMock:
    response = AsyncMock()
    response.status_code = 200
    response.raise_for_status = lambda: None
    response.json = lambda: {"choices": [{"message": {"content": content}}]}
    return response


@pytest.fixture
def provider() -> OpenAIProvider:
    return OpenAIProvider()


async def _call_and_capture_payload(
    provider: OpenAIProvider, headline: str, intelligence: object
) -> dict[str, Any]:
    with patch.object(
        provider.client,
        "post",
        AsyncMock(return_value=_mock_httpx_response(MOCK_AI_RESPONSE_JSON)),
    ) as post_mock:
        await provider.generate_financial_translation(headline, intelligence)  # type: ignore[arg-type]
    payload: dict[str, Any] = post_mock.call_args.kwargs["json"]
    return payload


@pytest.mark.asyncio
async def test_context_block_absent_during_fallback(provider: OpenAIProvider) -> None:
    payload = await _call_and_capture_payload(
        provider, "Some ambiguous headline", SAFE_FALLBACK
    )
    user_content = payload["messages"][1]["content"]
    assert user_content == "Some ambiguous headline"
    assert "APPLICATION CONTEXT" not in user_content


@pytest.mark.asyncio
async def test_context_block_absent_when_intelligence_is_none(
    provider: OpenAIProvider,
) -> None:
    payload = await _call_and_capture_payload(provider, "Some headline", None)
    user_content = payload["messages"][1]["content"]
    assert user_content == "Some headline"


@pytest.mark.asyncio
async def test_context_block_present_when_confident(provider: OpenAIProvider) -> None:
    intelligence = classify_news("Fed announces emergency surprise rate cut")
    assert intelligence.is_fallback is False

    payload = await _call_and_capture_payload(
        provider, "Fed announces emergency surprise rate cut", intelligence
    )
    user_content = payload["messages"][1]["content"]
    assert "APPLICATION CONTEXT" in user_content
    assert "category: central_bank" in user_content
    assert "central_bank: FED" in user_content


@pytest.mark.asyncio
async def test_original_headline_clearly_isolated_from_context(
    provider: OpenAIProvider,
) -> None:
    headline = "Fed announces emergency surprise rate cut"
    intelligence = classify_news(headline)

    payload = await _call_and_capture_payload(provider, headline, intelligence)
    user_content = payload["messages"][1]["content"]

    assert user_content.startswith(f"HEADLINE:\n{headline}")
    headline_pos = user_content.index(headline)
    context_pos = user_content.index("APPLICATION CONTEXT")
    assert headline_pos < context_pos


@pytest.mark.asyncio
async def test_no_python_or_enum_repr_leaks_into_prompt(
    provider: OpenAIProvider,
) -> None:
    intelligence = classify_news(
        "Canadian Participation Rate Actual 65.0% (Forecast 65%, Previous 65.0%)"
    )
    assert intelligence.is_fallback is False

    payload = await _call_and_capture_payload(
        provider,
        "Canadian Participation Rate Actual 65.0% (Forecast 65%, Previous 65.0%)",
        intelligence,
    )
    user_content = payload["messages"][1]["content"]

    # No dataclass repr, no Enum repr (e.g. "NewsCategory.ECONOMIC_DATA" or
    # "<NewsCategory.ECONOMIC_DATA: 'economic_data'>"), no object memory
    # addresses.
    assert "NewsIntelligenceResult(" not in user_content
    assert "NewsCategory." not in user_content
    assert "Urgency." not in user_content
    assert "NumericSurprise." not in user_content
    assert "<" not in user_content
    assert "object at 0x" not in user_content


@pytest.mark.asyncio
async def test_context_instructs_no_fabrication_and_no_quoting(
    provider: OpenAIProvider,
) -> None:
    intelligence = classify_news("Fed announces emergency surprise rate cut")
    payload = await _call_and_capture_payload(
        provider, "Fed announces emergency surprise rate cut", intelligence
    )
    user_content = payload["messages"][1]["content"]
    assert "do not quote" in user_content.lower()
    assert "invent" in user_content.lower() or "infer" in user_content.lower()


def test_internal_field_names_not_expected_in_rendered_output() -> None:
    """The formatter never touches the raw context block at all — it only
    reads structured fields off `intelligence`/`ai_data`, so internal field
    names like 'central_bank:' or 'APPLICATION CONTEXT' can never appear in
    a rendered Telegram message. Static assertion: the formatter module
    source has no reference to the context-builder's label strings."""
    import inspect

    from app.services.formatting import telegram_formatter

    source = inspect.getsource(telegram_formatter)
    assert "APPLICATION CONTEXT" not in source


# ---------------------------------------------------------------------------
# Story context block (Phase 3)
# ---------------------------------------------------------------------------


def _story(
    relationship: RelationshipType = RelationshipType.UPDATE,
    prior: str = "Fed cuts rates by 25bp",
) -> StoryDecision:
    return StoryDecision(
        story_id="abc-123-story-id",
        relationship=relationship,
        is_new_story=relationship == RelationshipType.NEW_STORY,
        evidence_score=6,
        matching_reasons=("central_bank:FED:+3", "category:+1"),
        prior_original_headline=prior,
        prior_headline_ar="الفيدرالي يخفض الفائدة",
        prior_at=datetime(2026, 7, 12, 1, 30, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_story_context_present_for_update(provider: OpenAIProvider) -> None:
    intelligence = classify_news("Fed chair explains surprise rate decision")
    payload = await _call_and_capture_payload_with_story(
        provider, "Fed chair explains surprise rate decision", intelligence, _story()
    )
    user_content = payload["messages"][1]["content"]
    assert "previous_development" in user_content
    assert "Fed cuts rates by 25bp" in user_content
    assert "update to" in user_content


@pytest.mark.asyncio
async def test_story_context_absent_for_new_story(provider: OpenAIProvider) -> None:
    intelligence = classify_news("Fed chair explains surprise rate decision")
    story = StoryDecision(
        story_id="xyz",
        relationship=RelationshipType.NEW_STORY,
        is_new_story=True,
        evidence_score=0,
        matching_reasons=("new_story",),
        prior_original_headline=None,
        prior_headline_ar=None,
        prior_at=None,
    )
    payload = await _call_and_capture_payload_with_story(
        provider, "Fed chair explains surprise rate decision", intelligence, story
    )
    user_content = payload["messages"][1]["content"]
    assert "previous_development" not in user_content
    assert "story_status" not in user_content


@pytest.mark.asyncio
async def test_story_context_absent_for_repetition(provider: OpenAIProvider) -> None:
    intelligence = classify_news("Fed chair explains surprise rate decision")
    payload = await _call_and_capture_payload_with_story(
        provider,
        "Fed chair explains surprise rate decision",
        intelligence,
        _story(relationship=RelationshipType.REPETITION),
    )
    assert "previous_development" not in payload["messages"][1]["content"]


@pytest.mark.asyncio
async def test_story_context_no_internal_leakage(provider: OpenAIProvider) -> None:
    intelligence = classify_news("Fed chair explains surprise rate decision")
    payload = await _call_and_capture_payload_with_story(
        provider, "Fed chair explains surprise rate decision", intelligence, _story()
    )
    user_content = payload["messages"][1]["content"]
    assert "abc-123-story-id" not in user_content  # no story ID
    assert "evidence" not in user_content.lower()  # no scores
    assert "+3" not in user_content  # no matching reasons
    assert "RelationshipType." not in user_content  # no enum repr
    assert "StoryDecision(" not in user_content  # no dataclass repr


@pytest.mark.asyncio
async def test_story_context_works_even_with_fallback_intelligence(
    provider: OpenAIProvider,
) -> None:
    # A token-matched story on a low-signal item: story context must still
    # reach the model, under the standard context header.
    payload = await _call_and_capture_payload_with_story(
        provider, "Some low signal follow-up item", SAFE_FALLBACK, _story()
    )
    user_content = payload["messages"][1]["content"]
    assert "APPLICATION CONTEXT" in user_content
    assert "previous_development" in user_content
    assert "category:" not in user_content  # no fabricated intelligence lines


@pytest.mark.asyncio
async def test_story_context_forbids_invented_history_and_market_reaction(
    provider: OpenAIProvider,
) -> None:
    intelligence = classify_news("Fed chair explains surprise rate decision")
    payload = await _call_and_capture_payload_with_story(
        provider, "Fed chair explains surprise rate decision", intelligence, _story()
    )
    content = payload["messages"][1]["content"].lower()
    assert "do not invent any story history" in content
    assert "market reaction" in content


async def _call_and_capture_payload_with_story(
    provider: OpenAIProvider, headline: str, intelligence: object, story: object
) -> dict[str, Any]:
    with patch.object(
        provider.client,
        "post",
        AsyncMock(return_value=_mock_httpx_response(MOCK_AI_RESPONSE_JSON)),
    ) as post_mock:
        await provider.generate_financial_translation(headline, intelligence, story)  # type: ignore[arg-type]
    payload: dict[str, Any] = post_mock.call_args.kwargs["json"]
    return payload
