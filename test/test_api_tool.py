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
    assert names == ["search_confluence", "search_openapi", "call_api", "list_api_endpoints"]


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


# --- C12. 엔드포인트 집계 도구 (agent-20 R1) ---

SERVICE = "general-chatbot-api"
SPEC_URL = "https://qa-general-chatbot-api.hunet.ai/openapi.json"

# (메서드,경로) 조합 5개. paths[path] 아래 비메서드 키를 일부러 섞었다(V2)
SPEC = {
    "openapi": "3.1.0",
    "paths": {
        "/check": {"get": {"summary": "Check"}},
        "/items": {
            "get": {"summary": "목록\n조회"},          # 줄바꿈이 항목 줄을 쪼개면 안 된다
            "post": {"operationId": "create_item"},    # summary 가 없으면 operationId
            "delete": {},                              # 둘 다 없으면 설명을 생략한다
            "parameters": [{"name": "page", "in": "query"}],
            "summary": "아이템",
            "description": "아이템 리소스",
            "servers": [{"url": "https://qa.example.com"}],
            "$ref": "#/components/pathItems/items",
        },
        "/a": {"get": {"summary": "첫 번째"}},
    },
}

EXPECTED = (
    "general-chatbot-api 엔드포인트 총 5개 (고유 경로 3개)\n"
    f"출처: {SPEC_URL}\n"
    "- GET /a — 첫 번째\n"
    "- GET /check — Check\n"
    "- DELETE /items\n"
    "- GET /items — 목록 조회\n"
    "- POST /items — create_item"
)


def _list_tool(transport=None, max_chars=2000, services=None):
    from src.api_tool import build_endpoint_list_tool

    return build_endpoint_list_tool(services or SERVICES, timeout=5, max_chars=max_chars,
                                    transport=transport)


def _spec_transport(spec=None, status=200, text=None):
    def respond(request):
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=spec if spec is not None else SPEC)

    return TransportSpy(respond)


def test_endpoint_list_tool_name_and_single_argument():
    """인자는 service 하나뿐이다. 스펙 경로는 고정이라 경로 인자를 두지 않는다."""
    tool = _list_tool()

    assert tool.name == "list_api_endpoints"
    assert set(tool.args) == {"service"}


def test_endpoint_list_tool_description_lists_services():
    description = _list_tool().description

    for name in SERVICES:
        assert name in description
    assert "개수" in description


def test_endpoint_list_requests_the_fixed_openapi_path():
    transport = _spec_transport()

    _list_tool(transport).invoke({"service": SERVICE})

    assert len(transport.requests) == 1
    assert transport.requests[0].method == "GET"
    assert str(transport.requests[0].url) == SPEC_URL


# V1·V2·V5

def test_counts_method_path_pairs_and_ignores_non_method_keys():
    """parameters·summary·description·servers·$ref 는 엔드포인트가 아니다. 세면 개수가 틀린다."""
    result = _list_tool(_spec_transport()).invoke({"service": SERVICE})

    assert result == EXPECTED
    assert result.splitlines()[0] == "general-chatbot-api 엔드포인트 총 5개 (고유 경로 3개)"
    assert len([line for line in result.splitlines() if line.startswith("- ")]) == 5
    for non_method in ["parameters", "servers", "$ref", "아이템"]:
        assert non_method not in result


# V13 고유 경로 수 병기

def test_first_line_shows_both_the_pair_count_and_the_unique_path_count():
    """한 경로에 메서드가 여럿이면 두 기준이 갈린다. 목 스펙은 조합 5 / 경로 3 이다."""
    result = _list_tool(_spec_transport()).invoke({"service": SERVICE})
    first = result.splitlines()[0]

    assert first == "general-chatbot-api 엔드포인트 총 5개 (고유 경로 3개)"
    paths = {line.split(" ", 2)[2].split(" — ")[0]
             for line in result.splitlines() if line.startswith("- ")}
    assert len(paths) == 3 and paths == {"/a", "/check", "/items"}


def test_unique_path_count_is_always_shown_even_when_it_equals_the_pair_count():
    """두 값이 같아도 조건 분기 없이 병기한다(결정론적 단순 규칙)."""
    spec = {"paths": {"/a": {"get": {}}, "/b": {"post": {}}}}

    result = _list_tool(_spec_transport(spec)).invoke({"service": SERVICE})

    assert result.splitlines()[0] == "general-chatbot-api 엔드포인트 총 2개 (고유 경로 2개)"


def test_paths_without_any_method_are_not_counted_as_unique_paths():
    """메서드가 하나도 없는 경로는 엔드포인트도 고유 경로도 아니다."""
    spec = {"paths": {"/a": {"get": {}, "post": {}}, "/b": {"parameters": [], "$ref": "#/x"}}}

    result = _list_tool(_spec_transport(spec)).invoke({"service": SERVICE})

    assert result.splitlines()[0] == "general-chatbot-api 엔드포인트 총 2개 (고유 경로 1개)"


def test_source_url_is_on_the_second_line():
    result = _list_tool(_spec_transport()).invoke({"service": SERVICE})

    assert result.splitlines()[1] == f"출처: {SPEC_URL}"


def test_output_is_deterministic_for_the_same_spec():
    tool = _list_tool(_spec_transport())

    assert tool.invoke({"service": SERVICE}) == tool.invoke({"service": SERVICE}) == EXPECTED


def test_summary_newlines_do_not_break_the_one_line_per_endpoint_rule():
    result = _list_tool(_spec_transport()).invoke({"service": SERVICE})

    assert "- GET /items — 목록 조회" in result.splitlines()
    assert len(result.splitlines()) == 7          # 머리글 2줄 + 항목 5줄


# V3·V4 절단

def test_truncated_output_keeps_the_exact_count_in_the_first_line():
    """max_chars 가 아주 작아도 1행의 총 개수는 살아남는다(개수를 맨 앞에 둔 이유)."""
    result = _list_tool(_spec_transport(), max_chars=10).invoke({"service": SERVICE})

    assert result.splitlines()[0] == "general-chatbot-api 엔드포인트 총 5개 (고유 경로 3개)"
    assert result.splitlines()[1] == f"출처: {SPEC_URL}"
    assert "표시 0개 / 전체 5개" in result


def test_truncation_cuts_at_line_boundaries_and_reports_shown_and_total():
    result = _list_tool(_spec_transport(), max_chars=120).invoke({"service": SERVICE})
    lines = result.splitlines()
    items = lines[2:-1]

    assert 0 < len(items) < 5
    for line in items:                            # 반쪽으로 잘린 항목이 없다
        assert line in EXPECTED.splitlines()
    assert lines[-1] == f"… (목록이 잘렸습니다. 위 총 개수는 정확합니다. 표시 {len(items)}개 / 전체 5개)"


def test_short_list_is_not_truncated():
    result = _list_tool(_spec_transport()).invoke({"service": SERVICE})

    assert "잘렸습니다" not in result


def test_list_truncation_suffix_is_separate_from_the_body_suffix():
    """문구가 다르다. 목록 절단은 '총 개수는 정확하다' 를 모델에게 알려야 한다."""
    from src.api_tool import LIST_TRUNCATED_SUFFIX, TRUNCATED_SUFFIX

    assert LIST_TRUNCATED_SUFFIX != TRUNCATED_SUFFIX
    assert "본문이 잘렸습니다" not in _list_tool(
        _spec_transport(), max_chars=120).invoke({"service": SERVICE})


# V6 QA 가드·미등재 service

def test_build_endpoint_list_tool_raises_on_production_url():
    from src.api_tool import build_endpoint_list_tool

    with pytest.raises(ValueError):
        build_endpoint_list_tool({"prod": "https://message-api.hunet.io"}, timeout=5, max_chars=100)


def test_endpoint_list_unknown_service_returns_guidance_without_request():
    transport = _spec_transport()

    result = _list_tool(transport).invoke({"service": "없는서비스"})

    assert transport.requests == []
    assert "사용할 수 없는 service 입니다: 없는서비스" in result
    for name in SERVICES:
        assert name in result


# V7 오류 처리

@pytest.mark.parametrize("status", [404, 500])
def test_non_200_spec_response_is_explained(status):
    result = _list_tool(_spec_transport(status=status, text="오류")).invoke({"service": SERVICE})

    assert result == f"HTTP {status} GET {SPEC_URL} — OpenAPI 스펙을 가져오지 못했습니다."


def test_redirect_is_not_followed_when_fetching_the_spec():
    transport = TransportSpy(lambda request: httpx.Response(
        302, headers={"location": "https://general-chatbot-api.hunet.ai/openapi.json"}, text=""))

    result = _list_tool(transport).invoke({"service": SERVICE})

    assert "HTTP 302" in result
    assert len(transport.requests) == 1


def test_non_json_spec_response_is_explained():
    result = _list_tool(_spec_transport(text="<html>hi</html>")).invoke({"service": SERVICE})

    assert result == f"GET {SPEC_URL} 응답이 JSON 이 아닙니다."


@pytest.mark.parametrize("spec", [{}, {"paths": {}}, {"paths": {"/x": {"parameters": []}}}])
def test_spec_without_endpoints_is_explained(spec):
    result = _list_tool(_spec_transport(spec)).invoke({"service": SERVICE})

    assert result == f"{SERVICE} 에 엔드포인트가 없습니다."


def test_endpoint_list_network_errors_propagate():
    """도구가 삼키지 않는다. ToolNode(handle_tool_errors=True) 가 ToolMessage 로 바꾼다."""
    def boom(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.HTTPError):
        _list_tool(TransportSpy(boom)).invoke({"service": SERVICE})


# V6 그래프 결합 (도구 4개) + R2 프롬프트

def test_default_graph_without_services_has_no_endpoint_list_tool(write_config):
    from src.agent import build_default_graph
    from test.conftest import CONFIG_TEXT

    text = CONFIG_TEXT.replace("general-chatbot-api=https://qa-general-chatbot-api.hunet.ai\n", "")
    text = text.replace("message-api=https://message-api.qa.hunet.io\n", "")

    graph = build_default_graph(_settings(write_config, text))

    names = [t.name for t in graph.nodes["tools"].bound.tools_by_name.values()]
    assert "list_api_endpoints" not in names


def test_endpoint_list_tool_is_not_a_forced_search_target(write_config):
    """이름이 search_ 로 시작하지 않으므로 force_search 가 임의로 실행하지 않는다."""
    assert not _list_tool().name.startswith("search_")


def test_system_prompt_has_the_endpoint_count_rules():
    from src.agent import SYSTEM_PROMPT

    assert "엔드포인트 개수나 전체 목록을 묻는 질문은 list_api_endpoints 를 사용합니다." in SYSTEM_PROMPT
    assert "검색 결과는 관련도 상위 일부일 뿐입니다. 검색 결과만으로 전체 개수나 전체 목록을 단정하지 않습니다." \
        in SYSTEM_PROMPT
    # 기존 규칙은 번호·문구 그대로 유지된다(규칙 8개)
    assert '5. 검색 결과에 없는 내용은 추측하지 않습니다. 근거를 찾지 못하면 "관련 내용을 문서에서 찾지 못했습니다." 라고 답합니다.' \
        in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.rstrip().endswith("8. 한국어로 간결하게 답합니다.")
    assert "\n9. " not in SYSTEM_PROMPT


# --- Validator 보강 (agent-20): 절단 경계·비정상 스펙·입력 순서·대규모 스펙 ---

def _spec_with(count: int, per_path: int = 1) -> dict:
    """엔드포인트가 count 개인 목 스펙. per_path 로 (조합 수 ≠ 경로 수) 상황을 만든다."""
    methods = ["get", "post", "put", "delete"][:per_path]
    paths = {}
    for i in range((count + per_path - 1) // per_path):
        paths[f"/v1/resource/{i:03d}/detail"] = {m: {"summary": f"설명 {i} {m}"} for m in methods}
    return {"paths": paths}


def test_endpoint_list_exactly_at_max_chars_is_not_truncated():
    """경계값: 전체 길이와 max_chars 가 같으면 자르지 않는다."""
    full = _list_tool(_spec_transport(), max_chars=10 ** 6).invoke({"service": SERVICE})

    result = _list_tool(_spec_transport(), max_chars=len(full)).invoke({"service": SERVICE})

    assert result == full
    assert "잘렸습니다" not in result


def test_endpoint_list_one_char_over_max_chars_drops_only_the_last_item():
    """경계값: 1자만 넘쳐도 줄 단위로 자르므로 마지막 항목 하나만 빠진다."""
    full = _list_tool(_spec_transport(), max_chars=10 ** 6).invoke({"service": SERVICE})
    items = [line for line in full.splitlines() if line.startswith("- ")]

    result = _list_tool(_spec_transport(), max_chars=len(full) - 1).invoke({"service": SERVICE})
    kept = [line for line in result.splitlines() if line.startswith("- ")]

    assert kept == items[:-1]
    assert result.splitlines()[0] == full.splitlines()[0]        # 개수는 그대로
    assert result.splitlines()[-1] == \
        f"… (목록이 잘렸습니다. 위 총 개수는 정확합니다. 표시 {len(items) - 1}개 / 전체 {len(items)}개)"


def test_spec_path_order_does_not_change_the_output():
    """정렬이 입력 순서와 무관하다. 스펙의 키 순서가 바뀌어도 같은 문자열이 나온다."""
    reversed_spec = {"paths": dict(reversed(list(SPEC["paths"].items())))}

    assert _list_tool(_spec_transport(reversed_spec)).invoke({"service": SERVICE}) == EXPECTED


@pytest.mark.parametrize("spec", [
    [1, 2],                                  # 최상위가 dict 가 아니다
    "문자열",
    {"paths": None},
    {"paths": []},
    {"paths": {"/x": "문자열"}},               # 경로 값이 dict 가 아니다
])
def test_malformed_spec_shapes_are_explained_without_raising(spec):
    """스펙이 망가져 있어도 예외 대신 설명 문자열을 준다(모델이 다음 수를 고를 수 있게)."""
    result = _list_tool(_spec_transport(spec)).invoke({"service": SERVICE})

    assert result == f"{SERVICE} 에 엔드포인트가 없습니다."


def test_non_string_summary_falls_back_to_operation_id():
    """summary 가 문자열이 아니면 operationId 로 내려간다. 둘 다 없으면 설명을 생략한다."""
    spec = {"paths": {"/a": {"get": {"summary": 123, "operationId": "fallback"}},
                      "/b": {"get": {"summary": {"ko": "설명"}}}}}

    result = _list_tool(_spec_transport(spec)).invoke({"service": SERVICE})

    assert result.splitlines()[2:] == ["- GET /a — fallback", "- GET /b"]


def test_large_spec_truncated_at_the_real_max_chars_keeps_an_exact_count():
    """운영 설정(max-response-chars=2000)과 message-api 규모(145개)를 목으로 재현한다.

    목록은 잘려도 1행의 두 숫자는 정확해야 한다 — 이 도구가 존재하는 이유다.
    """
    spec = _spec_with(145, per_path=2)        # 경로 73 × 메서드 2 = 조합 146 (message-api 규모)
    full = _list_tool(_spec_transport(spec), max_chars=10 ** 6).invoke({"service": SERVICE})
    items = [line for line in full.splitlines() if line.startswith("- ")]

    result = _list_tool(_spec_transport(spec), max_chars=2000).invoke({"service": SERVICE})
    lines = result.splitlines()
    kept = [line for line in lines if line.startswith("- ")]

    assert lines[0] == f"{SERVICE} 엔드포인트 총 {len(items)}개 (고유 경로 {len(items) // 2}개)"
    assert 0 < len(kept) < len(items)                      # 실제로 잘렸다
    assert kept == items[:len(kept)]                       # 잘린 항목이 반쪽으로 남지 않았다
    assert lines[-1] == \
        f"… (목록이 잘렸습니다. 위 총 개수는 정확합니다. 표시 {len(kept)}개 / 전체 {len(items)}개)"


def test_endpoint_list_result_reaches_the_model_as_a_tool_message_in_graph():
    """그래프 결합 확인: 도구 출력 1행이 손상 없이 ToolMessage 로 모델에 도착한다."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from src.agent import build_graph
    from src.tools import build_tools
    from test.conftest import FakeClient
    from test.test_agent import ScriptedChatModel

    tools = build_tools(FakeClient([]), 6) + [_list_tool(_spec_transport())]
    call = AIMessage(content="", tool_calls=[
        {"name": "list_api_endpoints", "args": {"service": SERVICE}, "id": "e1"}])
    model = ScriptedChatModel(responses=[call, AIMessage("총 5개입니다.")], received=[])

    state = build_graph(model, tools, None).invoke({"messages": [HumanMessage("총 몇 개야?")]})

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert [m.status for m in tool_messages] == ["success"]
    assert tool_messages[0].content == EXPECTED
    assert tool_messages[0].content.splitlines()[0].endswith("총 5개 (고유 경로 3개)")
