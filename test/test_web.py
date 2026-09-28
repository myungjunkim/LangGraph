import json

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from src.config.settings import load_settings
from src.web.app import create_app
from test.conftest import FakeClient
from test.test_agent import ScriptedChatModel, _chunk, _graph, _tool_call_message

ANSWER = "POST /v1/messages 입니다.\n출처: 메시지 등록 https://x/2"


def _frames(response) -> list[tuple[str, object]]:
    """SSE 본문을 (event, data) 목록으로 파싱한다(RAG 테스트와 같은 방식)."""
    parsed = []
    for block in response.text.strip().split("\n\n"):
        lines = block.split("\n")
        event = next(line[len("event: "):] for line in lines if line.startswith("event: "))
        data = next(line[len("data: "):] for line in lines if line.startswith("data: "))
        parsed.append((event, json.loads(data)))
    return parsed


def _app(responses, client=None, checkpointer=None, settings=None):
    graph, model = _graph(responses, client=client, checkpointer=checkpointer)
    return TestClient(create_app(graph, settings)), model


@pytest.fixture
def settings(write_config):
    return load_settings(write_config())


def _post(client, message="메시지 등록 API 호출 방법 알려줘", thread_id="t1"):
    return client.post("/v1/chat/stream", json={"thread_id": thread_id, "message": message})


# --- W1. 정적/헬스 ---

def test_root_serves_the_ui(settings):
    client, _ = _app([AIMessage("답")], settings=settings)
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "<title>KUDOS RAG Agent" in res.text
    assert "fetch(" in res.text


def test_root_ui_is_revalidated_on_every_load(settings):
    """UI 수정 직후 브라우저가 캐시된 옛 index.html 을 쓰지 않도록 매번 재검증시킨다."""
    client, _ = _app([AIMessage("답")], settings=settings)
    assert client.get("/").headers["cache-control"] == "no-cache"


def _fake_get(rag_status=200, models=("qwen3:14b",), rag_error=None):
    def get(url, timeout=None):
        request = httpx.Request("GET", url)
        if url.endswith("/check"):
            if rag_error:
                raise rag_error
            return httpx.Response(rag_status, request=request)
        return httpx.Response(200, json={"models": [{"name": m} for m in models]}, request=request)

    return get


def test_check_ok(settings, monkeypatch):
    import src.web.app as app_module

    monkeypatch.setattr(app_module.httpx, "get", _fake_get())
    client, _ = _app([AIMessage("답")], settings=settings)
    assert client.get("/check").json() == {"status": "ok", "rag": True, "ollama": True}


def test_check_degraded_when_rag_is_down(settings, monkeypatch):
    import src.web.app as app_module

    monkeypatch.setattr(app_module.httpx, "get", _fake_get(rag_error=httpx.ConnectError("refused")))
    client, _ = _app([AIMessage("답")], settings=settings)
    assert client.get("/check").json() == {"status": "degraded", "rag": False, "ollama": True}


def test_check_degraded_when_model_missing(settings, monkeypatch):
    import src.web.app as app_module

    monkeypatch.setattr(app_module.httpx, "get", _fake_get(models=("llama3:8b",)))
    client, _ = _app([AIMessage("답")], settings=settings)
    assert client.get("/check").json() == {"status": "degraded", "rag": True, "ollama": False}


def test_check_degraded_when_rag_returns_error_status(settings, monkeypatch):
    import src.web.app as app_module

    monkeypatch.setattr(app_module.httpx, "get", _fake_get(rag_status=503))
    client, _ = _app([AIMessage("답")], settings=settings)
    assert client.get("/check").json()["rag"] is False


# --- W2. 스트림 정상 ---

def test_stream_emits_search_then_tokens_then_done(settings):
    rag = FakeClient([_chunk()])
    client, _ = _app([_tool_call_message(query="메시지 등록"), AIMessage(ANSWER)], client=rag, settings=settings)

    res = _post(client)

    assert res.status_code == 200
    frames = _frames(res)
    assert frames[0] == ("search", {"tool": "search_openapi", "query": "메시지 등록"})
    assert frames[-1] == ("done", "")
    names = [name for name, _ in frames]
    assert names.count("done") == 1
    assert "token" in names
    assert names.index("search") < names.index("token")
    assert "".join(data for name, data in frames if name == "token") == ANSWER


def test_stream_response_headers(settings):
    client, _ = _app([AIMessage(ANSWER)], settings=settings)
    with client.stream("POST", "/v1/chat/stream", json={"thread_id": "t1", "message": "질문"}) as res:
        assert res.headers["content-type"].startswith("text/event-stream")
        assert res.headers["cache-control"] == "no-cache"
        assert res.headers["x-accel-buffering"] == "no"
        res.read()


def test_sse_data_is_json_encoded(settings):
    client, _ = _app([AIMessage("한글 답변")], settings=settings)
    res = _post(client)
    assert 'data: "한글 답변"' in res.text  # ensure_ascii=False
    assert res.text.endswith("\n\n")


# --- W3. 스트림 변형 ---

def test_stream_without_tool_call_has_no_search_event(settings):
    client, _ = _app([AIMessage("안녕하세요.")], settings=settings)
    frames = _frames(_post(client, "안녕"))
    names = [name for name, _ in frames]
    assert "search" not in names
    assert names[-1] == "done"
    assert "".join(d for n, d in frames if n == "token") == "안녕하세요."


def test_parallel_tool_calls_emit_two_search_events_in_order(settings):
    parallel = AIMessage(content="", tool_calls=[
        {"name": "search_confluence", "args": {"query": "정책"}, "id": "c1"},
        {"name": "search_openapi", "args": {"query": "엔드포인트"}, "id": "c2"},
    ])
    client, _ = _app([parallel, AIMessage("정리했습니다.")], settings=settings)

    frames = _frames(_post(client))

    searches = [data for name, data in frames if name == "search"]
    assert searches == [
        {"tool": "search_confluence", "query": "정책"},
        {"tool": "search_openapi", "query": "엔드포인트"},
    ]


def test_text_before_tool_call_streams_before_search_event(settings):
    """도구 호출 직전 멘트는 search 보다 먼저 흐른다. UI 는 search 에서 그때까지의 본문을 비운다."""
    preamble = AIMessage(content="문서를 먼저 검색해 볼게요.", tool_calls=[
        {"name": "search_openapi", "args": {"query": "메시지 등록"}, "id": "c1"},
    ])
    client, _ = _app([preamble, AIMessage(ANSWER)], settings=settings)

    frames = _frames(_post(client))

    names = [name for name, _ in frames]
    search_at = names.index("search")
    assert "".join(d for n, d in frames[:search_at] if n == "token") == "문서를 먼저 검색해 볼게요."
    assert "".join(d for n, d in frames[search_at:] if n == "token") == ANSWER
    assert names[-1] == "done"


def test_tool_failure_does_not_become_error_frame(settings):
    """도구 예외는 ToolNode 가 흡수해 LLM 이 답변으로 처리한다(agent-01 설계)."""
    rag = FakeClient(error=httpx.ConnectError("refused"))
    client, _ = _app([_tool_call_message(), AIMessage("검색에 실패했습니다.")], client=rag, settings=settings)

    frames = _frames(_post(client))

    names = [name for name, _ in frames]
    assert "error" not in names
    assert names[-1] == "done"
    assert "".join(d for n, d in frames if n == "token") == "검색에 실패했습니다."


class FailingChatModel(ScriptedChatModel):
    """모델 호출 자체가 Ollama 오류를 던지는 경우."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        import ollama

        raise ollama.ResponseError('model "qwen3:14b" not found', 404)


def test_llm_failure_emits_error_frame_last(settings):
    from src.agent import build_graph
    from src.tools import build_tools

    graph = build_graph(FailingChatModel(responses=[AIMessage("쓰이지 않음")], received=[]),
                        build_tools(FakeClient([]), 6))
    client = TestClient(create_app(graph, settings))

    res = _post(client)

    assert res.status_code == 200  # 첫 프레임 전송 후에는 상태 코드를 바꿀 수 없다
    frames = _frames(res)
    assert len(frames) == 1
    event, data = frames[-1]
    assert event == "error"
    assert data.startswith("ResponseError:")
    assert "not found" in data


# --- W4. 멀티턴 ---

def test_same_thread_id_keeps_history(settings):
    from langgraph.checkpoint.memory import InMemorySaver

    client, model = _app([AIMessage("첫 답변"), AIMessage("두 번째 답변")],
                         checkpointer=InMemorySaver(), settings=settings)

    _post(client, "첫 질문", thread_id="same")
    _post(client, "두 번째 질문", thread_id="same")

    contents = [m.content for m in model.received[-1] if isinstance(m, (HumanMessage, AIMessage))]
    assert contents == ["첫 질문", "첫 답변", "두 번째 질문"]


def test_different_thread_id_starts_fresh(settings):
    from langgraph.checkpoint.memory import InMemorySaver

    client, model = _app([AIMessage("첫 답변"), AIMessage("두 번째 답변")],
                         checkpointer=InMemorySaver(), settings=settings)

    _post(client, "첫 질문", thread_id="a")
    _post(client, "두 번째 질문", thread_id="b")

    contents = [m.content for m in model.received[-1] if isinstance(m, (HumanMessage, AIMessage))]
    assert contents == ["두 번째 질문"]


# --- W5. 검증 실패 / 라우트 목록 ---

@pytest.mark.parametrize("body", [
    {"thread_id": "t1", "message": ""},
    {"message": "질문"},
    {"thread_id": "t1"},
    {"thread_id": "", "message": "질문"},
    {"thread_id": "t1", "message": "가" * 4001},
    {"thread_id": "x" * 65, "message": "질문"},
])
def test_invalid_request_returns_422(settings, body):
    client, _ = _app([AIMessage("답")], settings=settings)
    assert client.post("/v1/chat/stream", json=body).status_code == 422


def test_max_lengths_are_accepted(settings):
    client, _ = _app([AIMessage("답")], settings=settings)
    res = client.post("/v1/chat/stream", json={"thread_id": "t" * 64, "message": "가" * 4000})
    assert res.status_code == 200


def test_openapi_lists_only_public_paths(settings):
    client, _ = _app([AIMessage("답")], settings=settings)
    spec = client.get("/openapi.json").json()
    assert sorted(spec["paths"]) == ["/check", "/v1/chat/stream", "/v1/threads/{thread_id}/messages"]
    assert "/" not in spec["paths"]  # include_in_schema=False


# --- 보강(Validator): 헬스체크 세부 규칙 ---

def test_check_functions_strip_trailing_slash(monkeypatch):
    """base_url 끝의 '/' 가 중복 슬래시를 만들지 않는다."""
    import src.web.app as app_module

    seen = []

    def get(url, timeout=None):
        seen.append(url)
        return httpx.Response(200, json={"models": [{"name": "qwen3:14b"}]},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(app_module.httpx, "get", get)
    assert app_module.check_rag("http://h:5010/", 5) is True
    assert app_module.check_ollama("http://h:11434/", "qwen3:14b", 5) is True
    assert seen == ["http://h:5010/check", "http://h:11434/api/tags"]


def test_check_ollama_false_when_tags_status_is_not_200(monkeypatch):
    import src.web.app as app_module

    monkeypatch.setattr(app_module.httpx, "get",
                        lambda url, timeout=None: httpx.Response(500, request=httpx.Request("GET", url)))
    assert app_module.check_ollama("http://h:11434", "qwen3:14b", 5) is False


def test_check_ollama_false_when_body_is_not_json(monkeypatch):
    import src.web.app as app_module

    monkeypatch.setattr(app_module.httpx, "get",
                        lambda url, timeout=None: httpx.Response(200, text="not json",
                                                                 request=httpx.Request("GET", url)))
    assert app_module.check_ollama("http://h:11434", "qwen3:14b", 5) is False


def test_check_ollama_false_when_connection_fails(monkeypatch):
    import src.web.app as app_module

    def boom(url, timeout=None):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(app_module.httpx, "get", boom)
    assert app_module.check_ollama("http://h:11434", "qwen3:14b", 5) is False


# --- 보강(Validator): 스트림 세부 규칙 ---

def test_search_query_defaults_to_empty_string_when_arg_missing(settings):
    """tool_call args 에 query 가 없으면 빈 문자열을 보낸다(KeyError 로 스트림이 끊기지 않는다)."""
    no_query = AIMessage(content="", tool_calls=[{"name": "search_openapi", "args": {}, "id": "c1"}])
    client, _ = _app([no_query, AIMessage("정리했습니다.")], settings=settings)

    frames = _frames(_post(client))

    assert ("search", {"tool": "search_openapi", "query": ""}) in frames
    assert frames[-1] == ("done", "")


def test_tool_node_output_is_not_streamed_as_token(settings):
    """token 은 agent 노드 메시지만 — 도구 결과(ToolMessage)는 흘리지 않는다."""
    rag = FakeClient([_chunk(content="TOOL_RESULT_MARKER")])
    client, _ = _app([_tool_call_message(), AIMessage(ANSWER)], client=rag, settings=settings)

    frames = _frames(_post(client))

    tokens = "".join(data for name, data in frames if name == "token")
    assert "TOOL_RESULT_MARKER" not in tokens
    assert tokens == ANSWER


def test_static_mount_serves_index_html(settings):
    client, _ = _app([AIMessage("답")], settings=settings)
    res = client.get("/static/index.html")
    assert res.status_code == 200
    assert "<title>KUDOS RAG Agent" in res.text


# --- agent-08: rewrite 노드가 SSE 로 새어 나오지 않는다 ---

def test_rewrite_output_is_not_streamed_as_token(settings):
    """R4. 후속 턴에서 rewrite 응답은 token 에도 search 에도 나타나지 않는다."""
    from langgraph.checkpoint.memory import InMemorySaver

    rewritten = "메시지 등록 API의 v1과 v2 차이"
    client, _ = _app([
        _tool_call_message(query="메시지 등록 API"),   # 1턴 agent (도구 호출)
        AIMessage("POST /v1/messages 입니다."),        # 1턴 agent (최종 답변)
        AIMessage(rewritten),                          # 2턴 rewrite
        AIMessage(ANSWER),                             # 2턴 agent
    ], checkpointer=InMemorySaver(), settings=settings)

    _post(client, "메시지 등록 API 알려줘", thread_id="same")
    frames = _frames(_post(client, "방금 알려준 API의 v1이랑 v2 차이는?", thread_id="same"))

    names = [name for name, _ in frames]
    assert names[-1] == "done"
    tokens = "".join(data for name, data in frames if name == "token")
    assert rewritten not in tokens
    assert tokens == ANSWER
    assert [data for name, data in frames if name == "search"] == []


def test_rewrite_turn_keeps_search_then_token_order(settings):
    """R4. 힌트가 붙은 턴에서도 search → token → done 순서와 token 합이 그대로다."""
    from langgraph.checkpoint.memory import InMemorySaver

    rewritten = "메시지 등록 API의 필수 필드"
    client, _ = _app([
        AIMessage("1턴 답변"),                              # 1턴 agent
        AIMessage(rewritten),                               # 2턴 rewrite
        _tool_call_message(query=rewritten),                # 2턴 agent (도구 호출)
        AIMessage(ANSWER),                                  # 2턴 agent (최종 답변)
    ], checkpointer=InMemorySaver(), settings=settings)

    _post(client, "메시지 등록 API 알려줘", thread_id="same")
    frames = _frames(_post(client, "그 API 필수 필드는?", thread_id="same"))

    names = [name for name, _ in frames]
    assert names.index("search") < names.index("token")
    assert names[-1] == "done"
    assert [data for name, data in frames if name == "search"] == [
        {"tool": "search_openapi", "query": rewritten}]
    assert "".join(data for name, data in frames if name == "token") == ANSWER


# --- agent-09: 대화 복원용 히스토리 API ---

def test_thread_messages_returns_questions_and_answers(settings):
    from langgraph.checkpoint.memory import InMemorySaver

    rag = FakeClient([_chunk()])
    client, _ = _app([
        _tool_call_message(query="메시지 등록 API"),   # 도구 호출 AIMessage — 제외 대상
        AIMessage("POST /v1/messages 입니다."),        # 1턴 답변
        AIMessage("독립 질문"),                        # 2턴 rewrite — 대화 기록에 없음
        AIMessage("v1 과 v2 는 …"),                    # 2턴 답변
    ], client=rag, checkpointer=InMemorySaver(), settings=settings)

    _post(client, "메시지 등록 API 알려줘", thread_id="keep")
    _post(client, "v1 이랑 v2 차이는?", thread_id="keep")

    body = client.get("/v1/threads/keep/messages").json()

    assert body == {"messages": [
        {"role": "user", "content": "메시지 등록 API 알려줘"},
        {"role": "assistant", "content": "POST /v1/messages 입니다."},
        {"role": "user", "content": "v1 이랑 v2 차이는?"},
        {"role": "assistant", "content": "v1 과 v2 는 …"},
    ]}


def test_thread_messages_of_unknown_thread_is_empty(settings):
    from langgraph.checkpoint.memory import InMemorySaver

    client, _ = _app([AIMessage("답")], checkpointer=InMemorySaver(), settings=settings)
    res = client.get("/v1/threads/없는대화/messages")
    assert res.status_code == 200 and res.json() == {"messages": []}


@pytest.mark.parametrize("thread_id", ["", "x" * 65])
def test_thread_messages_validates_thread_id(settings, thread_id):
    client, _ = _app([AIMessage("답")], settings=settings)
    res = client.get(f"/v1/threads/{thread_id}/messages")
    assert res.status_code in (404, 422)      # 빈 문자열은 경로가 달라져 404, 65자는 422


def test_thread_messages_max_length_is_accepted(settings):
    from langgraph.checkpoint.memory import InMemorySaver

    client, _ = _app([AIMessage("답")], checkpointer=InMemorySaver(), settings=settings)
    assert client.get(f"/v1/threads/{'x' * 64}/messages").status_code == 200


def test_thread_messages_excludes_tool_records():
    """변환 규칙 단위 확인: ToolMessage·tool_call 전용 AIMessage·빈 content 는 제외."""
    from langchain_core.messages import ToolMessage

    from src.web.app import to_thread_messages

    messages = [
        HumanMessage("질문"),
        _tool_call_message(query="검색어"),
        ToolMessage(content="청크 원문", name="search_openapi", tool_call_id="call_1"),
        AIMessage(""),
        AIMessage("답변"),
    ]

    assert [(m.role, m.content) for m in to_thread_messages(messages)] == [
        ("user", "질문"), ("assistant", "답변")]


def test_thread_messages_excludes_ai_message_that_also_has_tool_calls():
    """content 가 있어도 tool_calls 가 붙은 AIMessage 는 최종 답변이 아니므로 제외한다."""
    from src.web.app import to_thread_messages

    thinking = AIMessage(content="먼저 검색해 볼게요.", tool_calls=[
        {"name": "search_openapi", "args": {"query": "메시지 등록"}, "id": "c1"}])

    assert [(m.role, m.content) for m in to_thread_messages([HumanMessage("질문"), thinking, AIMessage("답변")])] == [
        ("user", "질문"), ("assistant", "답변")]


def test_thread_messages_of_empty_state_is_empty_list():
    from src.web.app import to_thread_messages

    assert to_thread_messages([]) == []


def test_thread_messages_rejects_too_long_thread_id_with_422(settings):
    """65자는 라우팅은 되지만 검증에서 걸려 422 여야 한다(404 가 아님)."""
    client, _ = _app([AIMessage("답")], settings=settings)
    res = client.get(f"/v1/threads/{'x' * 65}/messages")

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["path", "thread_id"]


def test_thread_messages_keeps_question_when_answer_is_missing():
    """도구 기록만 남고 최종 답변이 없어도 질문은 복원된다."""
    from langchain_core.messages import ToolMessage

    from src.web.app import to_thread_messages

    messages = [HumanMessage("질문"), _tool_call_message(query="검색어"),
                ToolMessage(content="청크", name="search_openapi", tool_call_id="call_1")]

    assert [(m.role, m.content) for m in to_thread_messages(messages)] == [("user", "질문")]
