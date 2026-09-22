"""실제 RAG 서버 + Ollama 를 사용하는 통합 테스트.

실행: pytest -m integration
사전 준비: RAG 서버 기동(`python main.py --active-profile=local`), `ollama serve`, qwen3:14b 설치.
"""
import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent import build_graph
from src.config.settings import config_path_for, load_settings
from src.llm_factory import create_chat_model
from src.rag_client import RagClient
from src.tools import build_tools

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings():
    path = config_path_for("local")
    if not path.is_file():
        pytest.skip(f"설정 파일이 없습니다: {path} (config_local.ini.example 을 복사하세요)")
    return load_settings(path)


@pytest.fixture(scope="module")
def live_env(settings):
    try:
        health = httpx.get(f"{settings.rag_base_url}/check", timeout=5)
    except httpx.HTTPError as e:
        pytest.skip(f"RAG 서버({settings.rag_base_url})에 연결할 수 없습니다: {e}")
    if health.status_code != 200:
        pytest.skip(f"RAG /check 가 {health.status_code} 를 반환했습니다.")

    try:
        tags = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
    except httpx.HTTPError as e:
        pytest.skip(f"Ollama({settings.ollama_base_url})에 연결할 수 없습니다: {e}")
    installed = {m.get("name", "") for m in tags.json().get("models", [])}
    if settings.llm_model not in installed:
        pytest.skip(f"Ollama 에 {settings.llm_model} 모델이 없습니다: {sorted(installed)}")
    return settings


def test_agent_calls_search_openapi_and_cites_sources(live_env):
    settings = live_env
    client = RagClient(settings.rag_base_url, settings.rag_search_path, settings.rag_timeout)
    graph = build_graph(create_chat_model(settings), build_tools(client, settings.rag_top_k))

    state = graph.invoke(
        {"messages": [HumanMessage("메시지 등록 API 호출 방법 알려줘")]},
        config={"recursion_limit": settings.recursion_limit},
    )

    tool_calls = [call for m in state["messages"] if isinstance(m, AIMessage) for call in m.tool_calls]
    assert any(call["name"] == "search_openapi" for call in tool_calls), f"tool_calls={tool_calls}"
    assert "출처:" in state["messages"][-1].content
