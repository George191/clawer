import json

import pytest

from app.web.agents.adapter import AdapterAgent
from app.web.agents.template import TemplateAgent
from app.web.services.browser_renderer import BrowserRenderer


def test_json_shape_exposes_record_contract_without_full_payload() -> None:
    evidence = BrowserRenderer._json_shape(
        json.dumps(
            {
                "results": {
                    "items": [
                        {
                            "messageId": "internal-id",
                            "warningNumber": "704/26",
                            "category": "ROCKET LAUNCHING",
                            "text": "x" * 1000,
                        }
                    ]
                }
            }
        )
    )

    assert evidence["jsonItemPath"] == "results.items"
    assert evidence["recordFields"] == [
        "messageId",
        "warningNumber",
        "category",
        "text",
    ]
    assert len(evidence["sampleRecord"]["text"]) == 240


def test_template_agent_uses_same_data_type_template_conventions() -> None:
    references = TemplateAgent._reference_template_summaries("news")

    assert any(reference["name"] == "arstechnica" for reference in references)
    assert any("thumbnail" in reference["list_fields"] for reference in references)
    assert any(
        resource["asset_type"] == "attachment"
        for reference in references
        for resource in reference["resources"]
    )


@pytest.mark.asyncio
async def test_template_generation_prompt_contains_references(monkeypatch) -> None:
    agent = TemplateAgent()
    captured: dict[str, str] = {}

    async def fake_generate(prompt: str, max_tokens: int = 8192) -> str:
        captured["prompt"] = prompt
        return "```yaml\nname: generated\n```"

    monkeypatch.setattr(agent, "generate", fake_generate)
    await agent.generate_template(
        "https://example.com/news",
        {
            "data_type": "news",
            "source_kind": "api",
            "fields": [],
            "pagination": {"type": "none"},
        },
    )

    assert '"name": "arstechnica"' in captured["prompt"]
    assert "Current verified evidence wins" in captured["prompt"]


def test_adapter_agent_uses_same_data_type_adapter_conventions() -> None:
    references = AdapterAgent._reference_adapter_summaries("news")

    arstechnica = next(reference for reference in references if reference["template"] == "arstechnica")
    assert arstechnica["uses_news_media_helpers"] is True
    assert "on_after_page" in arstechnica["hooks"]
    assert any(resource["asset_type"] == "image" for resource in arstechnica["resources"])


@pytest.mark.asyncio
async def test_adapter_generation_prompt_contains_references(monkeypatch) -> None:
    agent = AdapterAgent()
    captured: dict[str, str] = {}

    async def fake_generate(prompt: str, max_tokens: int = 8192) -> str:
        captured["prompt"] = prompt
        return "```python\nclass GeneratedAdapter:\n    pass\n```"

    monkeypatch.setattr(agent, "generate", fake_generate)
    await agent.generate_adapter(
        "generated_news",
        "name: generated_news\nbase_url: https://example.com\ndata_type: news\n",
    )

    assert '"template": "arstechnica"' in captured["prompt"]
    assert "The supplied YAML wins" in captured["prompt"]
