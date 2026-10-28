"""OpenAI transport: error mapping, JSON handling and the paid-feature parsers"""
import json

import httpx
import pytest

import openai_client
from openai_client import AIServiceError


REAL_CLIENT = httpx.AsyncClient


def use_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(openai_client.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=transport, **kw))


def chat_reply(content: str):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_compare_parses_full_response(monkeypatch, mock_openai_response):
    seen = {}

    def handler(request):
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json=mock_openai_response)

    use_transport(monkeypatch, handler)
    result = await openai_client.call_openai_completions("JD text " * 5, "Resume text " * 5)
    assert result.matchPercent == 80 and result.jobTitle == "Backend Engineer" and result.company == "Acme"
    assert result.breakdown.titleMatch == 90
    assert result.matchedSkills[0] == "Python"  # order preserved
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert seen["payload"]["messages"][0]["role"] == "system"


async def test_timeout_maps_to_safe_error(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    use_transport(monkeypatch, handler)
    with pytest.raises(AIServiceError, match="too long"):
        await openai_client.call_openai_completions("jd " * 10, "cv " * 10)


async def test_http_error_does_not_leak_upstream_body(monkeypatch):
    use_transport(monkeypatch, lambda r: httpx.Response(401, text="Incorrect API key provided: sk-live-SECRET"))
    with pytest.raises(AIServiceError) as exc:
        await openai_client.call_openai_completions("jd " * 10, "cv " * 10)
    assert "SECRET" not in str(exc.value)


async def test_invalid_json_returns_safe_fallback(monkeypatch):
    use_transport(monkeypatch, lambda r: chat_reply("Sorry, I cannot do that"))
    result = await openai_client.call_openai_completions("jd " * 10, "cv " * 10)
    assert result.matchPercent == 0 and result.matchedSkills == []
    assert result.warnings and "could not be read" in result.warnings[0]


async def test_empty_choices_is_an_error(monkeypatch):
    use_transport(monkeypatch, lambda r: httpx.Response(200, json={"choices": []}))
    with pytest.raises(AIServiceError):
        await openai_client.call_openai_completions("jd " * 10, "cv " * 10)


async def test_missing_api_key(monkeypatch):
    monkeypatch.setattr(openai_client.settings, "OPENAI_API_KEY", "")
    with pytest.raises(ValueError):
        await openai_client.chat_completion("s", "u")


def test_breakdown_is_clamped_and_coerced():
    result = openai_client.validate_and_coerce_response({
        "matchPercent": 150, "matchedSkills": ["a", "a", "b"], "missingSkills": [],
        "breakdown": {"hardSkills": 140, "softSkills": "55", "experience": None, "education": -3},
        "jobTitle": "  ", "company": "X" * 500,
    })
    assert result.matchPercent == 100 and result.matchedSkills == ["a", "b"]
    assert result.breakdown.hardSkills == 100 and result.breakdown.softSkills == 55
    assert result.breakdown.experience == 0 and result.breakdown.education == 0
    assert result.jobTitle is None and len(result.company) == 200


async def test_generate_rewrites_sanitises_output(monkeypatch):
    use_transport(monkeypatch, lambda r: chat_reply(json.dumps({
        "rewrites": [{"original": "a", "suggested": "b", "reason": "r", "keywords": ["k"]}, {"original": "", "suggested": "x"}, "junk"],
        "gaps": [{"skill": "AWS", "advice": "learn"}, {"advice": "no skill"}],
    })))
    out = await openai_client.generate_rewrites("jd", "cv", ["AWS"])
    assert len(out["rewrites"]) == 1 and out["rewrites"][0]["keywords"] == ["k"]
    assert out["gaps"] == [{"skill": "AWS", "advice": "learn"}]


async def test_cover_letter_and_prep_validate(monkeypatch):
    use_transport(monkeypatch, lambda r: chat_reply(json.dumps({"letter": "  Dear team  "})))
    assert await openai_client.generate_cover_letter("jd", "cv", "friendly", "Eng", "Acme") == "Dear team"

    use_transport(monkeypatch, lambda r: chat_reply(json.dumps({"letter": ""})))
    with pytest.raises(AIServiceError):
        await openai_client.generate_cover_letter("jd", "cv", "friendly", "", "")

    use_transport(monkeypatch, lambda r: chat_reply(json.dumps({"questions": [{"question": "Q?", "why": "w"}, {"why": "no q"}]})))
    qs = await openai_client.generate_interview_prep("jd", "cv", [])
    assert [q["question"] for q in qs] == ["Q?"]
