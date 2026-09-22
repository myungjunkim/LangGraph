import pytest

from src.tools import NO_RESULT_TEXT, build_tools, format_chunks
from test.conftest import FakeClient


def _chunk(chunk_id="1#0", title="인증 정책", url="https://x/1", source="confluence", content="토큰은 헤더로 전달한다."):
    return {"chunk_id": chunk_id, "title": title, "url": url, "source": source,
            "content": content, "metadata": {"chunk_id": chunk_id}}


def test_format_chunks_single_block():
    assert format_chunks([_chunk()]) == (
        "### 인증 정책\n"
        "- source: confluence | url: https://x/1\n"
        "토큰은 헤더로 전달한다."
    )


def test_format_chunks_joins_blocks_with_blank_line():
    text = format_chunks([_chunk(), _chunk("2#0", "메시지 등록", "https://x/2", "openapi", "POST /v1/messages")])

    assert text == (
        "### 인증 정책\n"
        "- source: confluence | url: https://x/1\n"
        "토큰은 헤더로 전달한다."
        "\n\n"
        "### 메시지 등록\n"
        "- source: openapi | url: https://x/2\n"
        "POST /v1/messages"
    )


def test_format_chunks_empty():
    assert format_chunks([]) == NO_RESULT_TEXT


def test_format_chunks_missing_fields_are_blank():
    assert format_chunks([{"content": "본문"}]) == "### \n- source:  | url: \n본문"


def test_build_tools_order_and_names():
    tools = build_tools(FakeClient(), 6)
    assert [t.name for t in tools] == ["search_confluence", "search_openapi"]


@pytest.mark.parametrize("index", [0, 1])
def test_tool_takes_only_query_and_has_description(index):
    tool = build_tools(FakeClient(), 6)[index]
    assert list(tool.args) == ["query"]
    assert tool.args["query"]["type"] == "string"
    assert tool.description.strip()


def test_tool_descriptions_match_contract():
    confluence, openapi = build_tools(FakeClient(), 6)
    assert confluence.description == "팀 Confluence(KUDOS) 문서를 검색한다. 정책, 설정값 정의, 운영 절차, 용어, 배경 설명 질문에 사용한다."
    assert openapi.description == (
        "사내 API 의 OpenAPI 스펙(엔드포인트, HTTP 메서드, 경로, 파라미터, 요청/응답 스키마)을 검색한다. API 호출 방법 질문에 사용한다."
    )


@pytest.mark.parametrize("index, source", [(0, "confluence"), (1, "openapi")])
def test_tool_maps_source_and_top_k(index, source):
    client = FakeClient([_chunk()])
    tool = build_tools(client, 4)[index]

    result = tool.invoke({"query": "토큰"})

    assert client.calls == [("토큰", source, 4)]
    assert result.startswith("### 인증 정책")


@pytest.mark.parametrize("index", [0, 1])
def test_tool_returns_no_result_text(index):
    tool = build_tools(FakeClient([]), 6)[index]
    assert tool.invoke({"query": "없는 내용"}) == NO_RESULT_TEXT


def test_tool_does_not_swallow_client_error():
    """RagClient 예외는 도구가 잡지 않는다(ToolNode 가 ToolMessage 로 변환)."""
    import httpx

    tool = build_tools(FakeClient(error=httpx.ConnectError("refused")), 6)[0]
    with pytest.raises(httpx.HTTPError):
        tool.invoke({"query": "q"})


# --- Validator 추가 검증: 순서/원문 보존 ---

def test_format_chunks_preserves_order_and_count():
    chunks = [_chunk(f"{i}#0", f"제목{i}", f"https://x/{i}", "confluence", f"본문{i}") for i in range(3)]
    text = format_chunks(chunks)

    assert text.count("### ") == 3
    assert [line for line in text.splitlines() if line.startswith("### ")] == ["### 제목0", "### 제목1", "### 제목2"]
    assert text.index("본문0") < text.index("본문1") < text.index("본문2")


def test_format_chunks_keeps_multiline_content_verbatim():
    """content 안의 개행은 그대로 남고, 블록 구분자는 '\\n\\n' 이다."""
    content = "## POST /v1/messages\n- service_key (string) (필수)"
    text = format_chunks([_chunk(content=content), _chunk("2#0", "두번째", "https://x/2", "openapi", "본문2")])

    assert content in text
    blocks = text.split("\n\n")
    assert len(blocks) == 2 and blocks[1].startswith("### 두번째")


def test_build_tools_uses_given_top_k_for_both_tools():
    client = FakeClient([])
    confluence, openapi = build_tools(client, 3)
    confluence.invoke({"query": "a"})
    openapi.invoke({"query": "b"})
    assert client.calls == [("a", "confluence", 3), ("b", "openapi", 3)]
