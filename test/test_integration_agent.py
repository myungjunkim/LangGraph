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


def _turn_queries(graph, thread_id: str, settings, first: str, second: str) -> tuple[list[str], str]:
    """같은 thread_id 로 2턴을 실행하고 2턴에서 만들어진 검색어 목록과 최종 답변을 돌려준다."""
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.recursion_limit}
    state = graph.invoke({"messages": [HumanMessage(first)]}, config=config)
    boundary = len(state["messages"])

    state = graph.invoke({"messages": [HumanMessage(second)]}, config=config)
    second_turn = state["messages"][boundary:]
    queries = [call["args"].get("query", "") for m in second_turn if isinstance(m, AIMessage)
               for call in m.tool_calls]
    return queries, state["messages"][-1].content


@pytest.mark.parametrize("first, keyword, foreign", [
    ("gpts 등록 API 알려줘", "gpts", "메시지"),
    pytest.param("메시지 등록 API 알려줘", "메시지 등록", "gpts",
                 marks=pytest.mark.xfail(strict=False,
                                         reason="후속 질문 검색어에 대상 누락 — agent-07")),
])
def test_followup_question_query_keeps_the_subject_of_the_previous_turn(live_env, capsys,
                                                                       first, keyword, foreign):
    """후속 질문의 검색어가 대화에 없던 이름으로 오염되지 않고, 앞 대화의 대상을 유지해야 한다.

    agent-06 P4(회차 3 기준): 오염 없음은 2종 모두 필수, 대상 유지는 gpts 필수 /
    메시지 등록은 미해결(xfail, agent-07 에서 처리)이지만 단언은 남겨 회복을 감지한다.
    """
    from src.agent import build_default_graph

    thread_id = f"agent-06-p4-{keyword}"
    settings = live_env
    queries, _ = _turn_queries(build_default_graph(settings), thread_id, settings,
                               first,
                               "방금 알려준 API의 v1이랑 v2 차이는 뭐야?")

    with capsys.disabled():
        print(f"\n[P4-{keyword}] 2턴 검색어 {queries}")
    assert queries, "2턴에서 도구를 호출하지 않았다 — 이 질문은 검색이 필요하다"
    assert all(foreign not in q.lower() for q in queries), \
        f"앞 대화에 없던 이름 오염: foreign={foreign!r} queries={queries}"
    # 핵심어는 소문자로 준다(한글은 lower() 영향 없음, `GPTs` 같은 표기 차이만 흡수)
    assert all(keyword in q.lower() for q in queries), \
        f"대상 누락: keyword={keyword!r} queries={queries}"


def test_followup_question_reuses_context_without_losing_the_api_name(live_env, capsys):
    """B8 멀티턴 케이스: 재검색을 하지 않거나, 하더라도 대상(메시지 등록)을 검색어에 남긴다(agent-06 P6)."""
    from src.agent import build_default_graph

    settings = live_env
    queries, answer = _turn_queries(build_default_graph(settings), "agent-06-p6", settings,
                                    "메시지 등록 API 알려줘",
                                    "그 API 필수 필드는?")

    with capsys.disabled():
        print(f"\n[P6] 2턴 검색어 {queries}, 답변 {len(answer)}자")
    assert not queries or any("메시지 등록" in q for q in queries), f"queries={queries}"
