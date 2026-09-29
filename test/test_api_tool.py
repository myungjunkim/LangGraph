"""GET 전용 실호출 도구. httpx.MockTransport 로 네트워크 없이 검증한다."""
import json

import httpx
import pytest

from src.api_tool import build_api_tool, validate_base_url

SERVICES = {
    "general-chatbot-api": "https://qa-general-chatbot-api.hunet.ai",
    "message-api": "https://message-api.qa.hunet.io",
}


class TransportSpy(httpx.BaseTransport):
    """요청을 기록하는 가짜 전송 계층. '요청이 발생하지 않았음' 단언의 근거."""

    def __init__(self, responder=None):
        self.requests = []
        self._responder = responder or (lambda request: httpx.Response(200, text="ok"))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)


def _tool(transport=None, max_chars=2000, services=None):
    return build_api_tool(services or SERVICES, timeout=5, max_chars=max_chars, transport=transport)


# --- C2. 운영 차단 ---

@pytest.mark.parametrize("url", list(SERVICES.values()))
def test_validate_accepts_qa_urls(url):
    assert validate_base_url("svc", url) == url


@pytest.mark.parametrize("url, reason", [
    ("http://qa-general-chatbot-api.hunet.ai", "https"),
    ("https://message-api.hunet.io", "QA"),
    ("https://prod.hunet.ai", "QA"),
    ("https://general-chatbot-api.hunet.ai", "QA"),
    ("https://qa.x.com/sub", "경로"),
    ("https://qa.x.com?a=1", "경로"),
    ("https://aqab.hunet.io", "QA"),          # qa 가 라벨 경계가 아니면 통과시키지 않는다
])
def test_validate_rejects_non_qa_or_unsafe_urls(url, reason):
    with pytest.raises(ValueError) as e:
        validate_base_url("svc", url)
    assert reason in str(e.value)
    assert "svc" in str(e.value)


def test_validate_keeps_only_origin():
    assert validate_base_url("svc", "https://qa.example.com") == "https://qa.example.com"


# --- C3. 기동 시점 실패 ---

def test_build_api_tool_raises_on_production_url():
    """운영 주소가 설정에 들어가면 서버가 뜨지 않아야 한다(런타임 문자열 반환이 아니다)."""
    with pytest.raises(ValueError):
        build_api_tool({"prod": "https://message-api.hunet.io"}, timeout=5, max_chars=100)


def test_build_api_tool_raises_even_if_one_service_is_bad():
    services = dict(SERVICES, prod="https://message-api.hunet.io")
    with pytest.raises(ValueError):
        build_api_tool(services, timeout=5, max_chars=100)


def test_qa_check_is_a_module_constant_not_config():
    """QA 표식 검사는 설정으로 끌 수 없다."""
    from pathlib import Path

    source = Path("src/api_tool.py").read_text(encoding="utf-8")
    assert "QA_HOST_PATTERN = re.compile" in source
    assert "config" not in source.lower().replace("config_local", "")


# --- C4. 도구 계약 ---

def test_tool_name_and_arguments():
    call_api = _tool()

    assert call_api.name == "call_api"
    assert set(call_api.args) == {"service", "path"}


@pytest.mark.parametrize("forbidden", ["method", "headers", "body", "data", "json", "verb"])
def test_tool_has_no_method_or_payload_argument(forbidden):
    """GET 외 요청을 보낼 경로 자체가 없다."""
    assert forbidden not in _tool().args


def test_tool_description_lists_services_and_states_get_only():
    description = _tool().description

    assert "GET" in description
    for name in SERVICES:
        assert name in description
    assert "search_openapi" in description


# --- C5. 정상 호출 ---

def test_call_api_sends_get_to_base_url_and_path():
    transport = TransportSpy(lambda request: httpx.Response(200, json={"ok": True}))

    result = _tool(transport).invoke({"service": "message-api", "path": "/v1/messages?page=1"})

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "GET"
    assert str(request.url) == "https://message-api.qa.hunet.io/v1/messages?page=1"
    assert "HTTP 200 GET https://message-api.qa.hunet.io/v1/messages?page=1" in result
    assert json.loads(result.split("\n", 1)[1]) == {"ok": True}


def test_call_api_uses_selected_service_base_url():
    transport = TransportSpy()
    _tool(transport).invoke({"service": "general-chatbot-api", "path": "/openapi.json"})
    assert str(transport.requests[0].url) == "https://qa-general-chatbot-api.hunet.ai/openapi.json"


# --- C6. 오류·리다이렉트 응답 ---

@pytest.mark.parametrize("status", [404, 422, 500])
def test_error_responses_are_returned_not_raised(status):
    transport = TransportSpy(lambda request: httpx.Response(status, text="오류 본문"))

    result = _tool(transport).invoke({"service": "message-api", "path": "/v1/x"})

    assert f"HTTP {status}" in result
    assert "오류 본문" in result


def test_redirect_is_returned_without_following():
    transport = TransportSpy(lambda request: httpx.Response(
        302, headers={"location": "https://message-api.hunet.io/v1/x"}, text=""))

    result = _tool(transport).invoke({"service": "message-api", "path": "/v1/x"})

    assert "HTTP 302" in result
    assert len(transport.requests) == 1        # 운영 주소로 따라가지 않는다


# --- C7. 입력 방어 (요청이 발생하면 안 된다) ---

def test_unknown_service_returns_guidance_without_request():
    transport = TransportSpy()

    result = _tool(transport).invoke({"service": "없는서비스", "path": "/v1/x"})

    assert transport.requests == []
    assert "사용할 수 없는 service 입니다: 없는서비스" in result
    for name in SERVICES:
        assert name in result


@pytest.mark.parametrize("path", ["messages", "//evil.com/x", "https://evil.com/x",
                                  "http://evil.com", "", " /v1/x"])
def test_invalid_path_returns_guidance_without_request(path):
    transport = TransportSpy()

    result = _tool(transport).invoke({"service": "message-api", "path": path})

    assert transport.requests == []
    assert "path 는 '/' 로 시작하는 경로여야 합니다" in result


# --- C8. 절단·예외 전파 ---

def test_long_body_is_truncated_with_original_length():
    body = "가" * 5000
    transport = TransportSpy(lambda request: httpx.Response(200, text=body))

    result = _tool(transport, max_chars=100).invoke({"service": "message-api", "path": "/v1/x"})

    assert "본문이 잘렸습니다. 전체 5000자" in result
    assert len(result) < 400


def test_short_body_is_not_truncated():
    transport = TransportSpy(lambda request: httpx.Response(200, text="짧은 본문"))
    result = _tool(transport, max_chars=100).invoke({"service": "message-api", "path": "/v1/x"})
    assert "잘렸습니다" not in result
    assert result.endswith("짧은 본문")


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("timeout")])
def test_network_errors_propagate(error):
    """도구가 삼키지 않는다. ToolNode(handle_tool_errors=True) 가 ToolMessage 로 바꾼다."""
    def boom(request):
        raise error

    with pytest.raises(httpx.HTTPError):
        _tool(TransportSpy(boom)).invoke({"service": "message-api", "path": "/v1/x"})


# --- C9. 그래프 결합·프롬프트 ---

def _settings(write_config, text=None):
    from src.config.settings import load_settings

    return load_settings(write_config(text) if text else write_config())


def test_default_graph_includes_call_api(write_config):
    from src.agent import build_default_graph

    graph = build_default_graph(_settings(write_config))

    names = [t.name for t in graph.nodes["tools"].bound.tools_by_name.values()]
    assert names == ["search_confluence", "search_openapi", "call_api"]


def test_default_graph_without_services_keeps_two_tools(write_config):
    from src.agent import build_default_graph
    from test.conftest import CONFIG_TEXT

    text = CONFIG_TEXT.replace("general-chatbot-api=https://qa-general-chatbot-api.hunet.ai\n", "")
    text = text.replace("message-api=https://message-api.qa.hunet.io\n", "")

    graph = build_default_graph(_settings(write_config, text))

    names = [t.name for t in graph.nodes["tools"].bound.tools_by_name.values()]
    assert names == ["search_confluence", "search_openapi"]


def test_default_graph_fails_fast_on_production_url(write_config):
    """운영 주소가 설정에 있으면 그래프(=서버)가 뜨지 않는다."""
    from src.agent import build_default_graph
    from test.conftest import CONFIG_TEXT

    text = CONFIG_TEXT.replace("message-api=https://message-api.qa.hunet.io",
                               "message-api=https://message-api.hunet.io")
    with pytest.raises(ValueError):
        build_default_graph(_settings(write_config, text))


def test_system_prompt_has_call_api_rule_and_keeps_existing_rules():
    from src.agent import SYSTEM_PROMPT

    assert "call_api 로 GET 요청을 보냅니다" in SYSTEM_PROMPT
    assert "요청하지 않았는데 임의로 호출하지 않습니다" in SYSTEM_PROMPT
    # 기존 규칙 문구가 그대로 남아 있다(agent-06 규칙 3 포함)
    for kept in ["독립 검색어", "앞 대화에 나오지 않은 이름을 검색어에 넣지 않습니다",
                 "관련 내용을 문서에서 찾지 못했습니다.", "출처:", "한국어로 간결하게 답합니다",
                 "search_confluence", "search_openapi"]:
        assert kept in SYSTEM_PROMPT
    assert "8. 한국어로 간결하게 답합니다." in SYSTEM_PROMPT       # 규칙 8개로 늘었다


# --- C10. 표시 (SSE 이벤트 이름·필드는 불변) ---

def test_tool_call_summary_keeps_query_tools_unchanged():
    from src.agent import tool_call_summary

    assert tool_call_summary({"query": "메시지 등록 API"}) == "메시지 등록 API"
    assert tool_call_summary({}) == ""


def test_tool_call_summary_joins_args_of_call_api():
    from src.agent import tool_call_summary

    assert tool_call_summary({"service": "message-api", "path": "/v1/messages"}) == \
        "message-api /v1/messages"


def test_stream_events_reports_call_api_arguments(write_config):
    """SSE 이벤트 이름·필드는 그대로이고 query 값만 인자 요약으로 채워진다."""
    import json

    from fastapi.testclient import TestClient
    from langchain_core.messages import AIMessage

    from src.config.settings import load_settings
    from src.web.app import create_app
    from test.test_agent import _graph

    call = AIMessage(content="", tool_calls=[
        {"name": "call_api", "args": {"service": "message-api", "path": "/openapi.json"}, "id": "c1"},
    ])
    graph, _ = _graph([call, AIMessage("호출 결과입니다.")])
    client = TestClient(create_app(graph, load_settings(write_config())))

    res = client.post("/v1/chat/stream", json={"thread_id": "t1", "message": "호출해줘"})

    frames = [(b.split("\n")[0].removeprefix("event: "), json.loads(b.split("\n")[1].removeprefix("data: ")))
              for b in res.text.strip().split("\n\n")]
    searches = [data for name, data in frames if name == "search"]
    assert searches == [{"tool": "call_api", "query": "message-api /openapi.json"}]


def test_run_turn_labels_call_api_differently():
    from langchain_core.messages import AIMessage

    import main
    from test.test_agent import _graph

    call = AIMessage(content="", tool_calls=[
        {"name": "call_api", "args": {"service": "message-api", "path": "/openapi.json"}, "id": "c1"},
    ])
    graph, _ = _graph([call, AIMessage("결과입니다.")])
    lines = []
    main.run_turn(graph, {"configurable": {"thread_id": "t1"}}, "호출해줘", out=lines.append)

    assert lines[0] == "[호출] call_api(message-api /openapi.json)"
    assert lines[-1] == "결과입니다."


def test_run_turn_keeps_search_label_for_search_tools():
    from langchain_core.messages import AIMessage

    import main
    from test.test_agent import _graph, _tool_call_message

    graph, _ = _graph([_tool_call_message(query="메시지 등록"), AIMessage("답변")])
    lines = []
    main.run_turn(graph, {"configurable": {"thread_id": "t1"}}, "알려줘", out=lines.append)

    assert lines[0] == "[검색] search_openapi(메시지 등록)"


def test_ui_labels_call_api_as_call():
    import re
    from pathlib import Path

    html = Path("resources/static/index.html").read_text(encoding="utf-8")
    script = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    body = script[script.index("function addSearchLine("):script.index("function linkifyUrls(")]

    assert "data.tool === 'call_api'" in body
    assert "'[호출]'" in body and "'[검색]'" in body
    assert "innerHTML" not in body


# --- Validator 보강: GET 전용의 구조적 보장 ---

@pytest.mark.parametrize("forbidden", [".post(", ".put(", ".patch(", ".delete(",
                                       ".request(", ".stream(", ".head(", ".options(", ".send("])
def test_module_has_no_write_or_arbitrary_method_call(forbidden):
    """모듈 전체에 GET 외의 httpx 호출 지점이 없다(새 호출이 끼어들면 실패한다)."""
    from pathlib import Path

    source = Path("src/api_tool.py").read_text(encoding="utf-8")
    assert forbidden not in source


def test_module_has_exactly_one_http_call_site():
    from pathlib import Path

    source = Path("src/api_tool.py").read_text(encoding="utf-8")
    assert source.count("client.get(") == 1


def test_extra_arguments_cannot_change_the_http_method():
    """모델이 method/json 을 끼워 넣어도 나가는 요청은 여전히 GET 1건이다."""
    transport = TransportSpy()

    _tool(transport).invoke({"service": "message-api", "path": "/v1/x",
                             "method": "POST", "json": {"a": 1}, "headers": {"X": "1"}})

    assert [r.method for r in transport.requests] == ["GET"]
    assert transport.requests[0].read() == b""


@pytest.mark.parametrize("path", ["/\\/evil.com/x", "/..//evil.com", "/%2F%2Fevil.com",
                                  "/ /evil.com", "/@evil.com/x", "/x#@evil.com",
                                  "/x?next=https://evil.com"])
def test_path_cannot_escape_the_registered_qa_host(path):
    """경로 트릭으로 다른 호스트에 요청이 나가지 않는다."""
    transport = TransportSpy()

    _tool(transport).invoke({"service": "message-api", "path": path})

    assert [r.url.host for r in transport.requests] == ["message-api.qa.hunet.io"]


# --- Validator 보강: base URL 검사 경계 ---

@pytest.mark.parametrize("url", ["https://qa.example.com/", "https://qa.example.com#frag",
                                 "https://qa.example.com/?a=1"])
def test_trailing_path_or_fragment_is_rejected(url):
    with pytest.raises(ValueError):
        validate_base_url("svc", url)


def test_userinfo_does_not_bypass_the_qa_check():
    """https://qa.example.com@evil.com 의 실제 접속 호스트는 evil.com 이므로 거부해야 한다."""
    with pytest.raises(ValueError) as e:
        validate_base_url("svc", "https://qa.example.com@evil.com")
    assert "QA" in str(e.value)


@pytest.mark.parametrize("url", ["", "qa.example.com", "//qa.example.com", "ftp://qa.example.com"])
def test_non_https_or_schemeless_base_url_is_rejected(url):
    with pytest.raises(ValueError):
        validate_base_url("svc", url)


# --- Validator 보강: 절단 경계 ---

def test_body_exactly_at_max_chars_is_not_truncated():
    transport = TransportSpy(lambda request: httpx.Response(200, text="가" * 100))

    result = _tool(transport, max_chars=100).invoke({"service": "message-api", "path": "/v1/x"})

    assert "잘렸습니다" not in result
    assert result.endswith("가" * 100)


def test_one_char_over_max_chars_is_truncated():
    transport = TransportSpy(lambda request: httpx.Response(200, text="가" * 101))

    result = _tool(transport, max_chars=100).invoke({"service": "message-api", "path": "/v1/x"})

    assert "본문이 잘렸습니다. 전체 101자" in result


# --- Validator 보강: 그래프에서의 오류 처리 ---

def test_call_api_error_becomes_error_tool_message_in_graph():
    """연결 실패는 도구가 삼키지 않고 ToolNode 가 ToolMessage(status="error") 로 바꾼다."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from src.agent import build_graph
    from src.tools import build_tools
    from test.conftest import FakeClient
    from test.test_agent import ScriptedChatModel

    def boom(request):
        raise httpx.ConnectError("refused")

    tools = build_tools(FakeClient([]), 6) + [_tool(TransportSpy(boom))]
    call = AIMessage(content="", tool_calls=[
        {"name": "call_api", "args": {"service": "message-api", "path": "/v1/x"}, "id": "c1"}])
    model = ScriptedChatModel(responses=[call, AIMessage("호출에 실패했습니다.")], received=[])

    state = build_graph(model, tools, None).invoke({"messages": [HumanMessage("호출해줘")]})

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert [m.status for m in tool_messages] == ["error"]
    assert state["messages"][-1].content == "호출에 실패했습니다."
