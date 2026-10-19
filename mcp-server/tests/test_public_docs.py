"""Public guidance must agree with the shipped MCP surface (issue #72)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from mcp.types import PaginatedRequestParams
from starlette.testclient import TestClient

from crystalium.config import Config
from crystalium.server import _build_server, build_http_app

ROOT = Path(__file__).resolve().parents[2]


def _check_http_docs(readme: str, config: Config) -> None:
    """Require a runnable container recipe and the implemented endpoint caveats."""
    assert callable(build_http_app)
    assert "NotImplementedError" not in readme
    assert "Streamable HTTP" in readme
    assert "docker compose run --rm" in readme
    assert "CRYSTALIUM_TRANSPORT=http" in readme
    assert "CRYSTALIUM_HTTP_HOST=0.0.0.0" in readme
    assert f"127.0.0.1:{config.http_port}:{config.http_port}" in readme
    assert f"http://127.0.0.1:{config.http_port}{config.http_path}/" in readme
    assert "python -m crystalium serve" in readme
    assert "authentication" in readme
    assert "TLS" in readme


def test_readme_http_matches_implementation(tmp_path: Path, monkeypatch) -> None:
    """Catch the former stub claim and missing HTTP startup instructions."""
    monkeypatch.setenv("CRYSTALIUM_SKIP_SLOW", "1")
    config = Config(data_dir=tmp_path)
    _check_http_docs((ROOT / "README.md").read_text(), config)
    app, _scheduler, _manager = build_http_app(config)
    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            config.http_path + "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "docs-check", "version": "1"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "crystalium"


@pytest.mark.parametrize("regression", ["stub", "missing_recipe"])
def test_http_docs_guard_rejects_regressions(tmp_path: Path, regression: str) -> None:
    """Prove the documentation guard rejects both acceptance failure modes."""
    readme = (ROOT / "README.md").read_text()
    if regression == "stub":
        readme += "\nHTTP stub raises NotImplementedError\n"
    else:
        readme = readme.replace("CRYSTALIUM_TRANSPORT=http", "transport omitted")
    with pytest.raises(AssertionError):
        _check_http_docs(readme, Config(data_dir=tmp_path))


def test_public_guidance_matches_mcp_initialization(tmp_path: Path, monkeypatch) -> None:
    """Verify advice exposed to clients without changing explicit search defaults."""
    monkeypatch.setenv("CRYSTALIUM_SKIP_SLOW", "1")
    server, _scheduler = _build_server(Config(data_dir=tmp_path / "project"))
    instructions = server.create_initialization_options().instructions
    assert instructions is not None
    for required in (
        "explicit need",
        "prior failure",
        "ambiguity",
        "k<=3",
        "fix-span",
        "provisional",
        "representative",
        "#74",
        "needs_memory=true",
        "prior_failure=true",
    ):
        assert required in instructions
    assert not re.search(r"always\s+recall|recall.*before reasoning", instructions, re.I)

    handler = server.get_request_handler("tools/list")
    result = asyncio.run(handler.handler(None, PaginatedRequestParams()))
    readme = (ROOT / "README.md").read_text()
    surface = readme.split("## MCP surface", 1)[1].split("\n---", 1)[0]
    documented = set(re.findall(r"^\| `([a-z_]+)\(", surface, re.M))
    assert documented == {tool.name for tool in result.tools}
    recall = next(tool for tool in result.tools if tool.name == "recall")
    assert recall.input_schema["properties"]["k"]["default"] == 10
    assert "k<=3" in recall.description
    assert all(tool.output_schema is None for tool in result.tools)
    for capability in ("resources", "prompts", "structured output", "elicitation"):
        assert capability in surface
    assert "tools-only" in surface
    for method in ("resources/list", "resources/read", "prompts/list", "prompts/get"):
        assert server.get_request_handler(method) is None


@pytest.mark.parametrize(
    "path", ["README.md", "AGENTS.md", "CLAUDE.md", "PERSONA.md", "docs/memory-injection.md"]
)
def test_tracked_agent_guidance_is_conditional(path: str) -> None:
    """Keep first-party entry points aligned without rewriting installed cortex files."""
    guidance = (ROOT / path).read_text()
    for required in ("explicit need", "prior failure", "ambiguity", "k<=3", "fix-span"):
        assert required in guidance
    assert "provisional" in guidance
    assert "representative" in guidance
    assert not re.search(r"always\s+recall|recall.*before reasoning", guidance, re.I)
