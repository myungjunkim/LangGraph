import httpx
from langchain_core.messages import AIMessage, HumanMessage

import main
from test.test_agent import _graph, _tool_call_message


def _collect(graph, text="메시지 등록 API 호출 방법 알려줘"):
    lines = []
    main.run_turn(graph, {"configurable": {"thread_id": "t1"}, "recursion_limit": 12}, text, out=lines.append)
    return lines


def test_run_turn_prints_search_line_and_final_answer():
    graph, _ = _graph([_tool_call_message(query="메시지 등록"), AIMessage("POST /v1/messages 입니다.\n출처: 메시지 등록 https://x/2")])

    assert _collect(graph) == [
        "[검색] search_openapi(메시지 등록)",
        "POST /v1/messages 입니다.\n출처: 메시지 등록 https://x/2",
    ]


def test_run_turn_without_tool_call_prints_answer_only():
    graph, _ = _graph([AIMessage("안녕하세요.")])
    assert _collect(graph, "안녕") == ["안녕하세요."]


def test_run_turn_prints_every_tool_call():
    parallel = AIMessage(content="", tool_calls=[
        {"name": "search_confluence", "args": {"query": "정책"}, "id": "c1"},
        {"name": "search_openapi", "args": {"query": "엔드포인트"}, "id": "c2"},
    ])
    graph, _ = _graph([parallel, AIMessage("정리했습니다.")])

    assert _collect(graph) == [
        "[검색] search_confluence(정책)",
        "[검색] search_openapi(엔드포인트)",
        "정리했습니다.",
    ]


def test_run_turn_passes_human_message_to_graph():
    graph, model = _graph([AIMessage("답")])
    _collect(graph, "질문 원문")

    human = [m for m in model.received[0] if isinstance(m, HumanMessage)]
    assert [m.content for m in human] == ["질문 원문"]


def test_warn_if_rag_down_is_silent_when_healthy(monkeypatch):
    monkeypatch.setattr(main.httpx, "get", lambda url, timeout: httpx.Response(200, request=httpx.Request("GET", url)))
    lines = []
    main.warn_if_rag_down("http://rag.test", 3, out=lines.append)
    assert lines == []


def test_warn_if_rag_down_warns_on_connect_error(monkeypatch):
    def boom(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(main.httpx, "get", boom)
    lines = []
    main.warn_if_rag_down("http://rag.test", 3, out=lines.append)
    assert len(lines) == 1 and lines[0].startswith("[경고] RAG 서버(http://rag.test)")


def test_warn_if_rag_down_warns_on_error_status(monkeypatch):
    monkeypatch.setattr(main.httpx, "get",
                        lambda url, timeout: httpx.Response(503, request=httpx.Request("GET", url)))
    lines = []
    main.warn_if_rag_down("http://rag.test/", 3, out=lines.append)
    assert len(lines) == 1 and lines[0].startswith("[경고]")
