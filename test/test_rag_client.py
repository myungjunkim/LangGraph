import json

import httpx
import pytest

from src.rag_client import RagClient


def _client(handler) -> RagClient:
    return RagClient("http://rag.test", "/v1/search", 5.0, transport=httpx.MockTransport(handler))


def test_search_sends_expected_request_and_parses_chunks():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"chunks": [
            {"chunk_id": "1#0", "title": "제목", "url": "https://x/1", "source": "confluence",
             "content": "본문", "metadata": {"section": "개요"}},
        ]})

    chunks = _client(handler).search("토큰", "confluence", 6)

    assert seen["method"] == "POST"
    assert seen["url"] == "http://rag.test/v1/search"
    assert seen["body"] == {"query": "토큰", "source": "confluence", "top_k": 6}
    assert chunks == [{"chunk_id": "1#0", "title": "제목", "url": "https://x/1", "source": "confluence",
                       "content": "본문", "metadata": {"section": "개요"}}]


def test_search_returns_empty_list():
    chunks = _client(lambda request: httpx.Response(200, json={"chunks": []})).search("없음", "all", 6)
    assert chunks == []


def test_base_url_trailing_slash_is_normalized():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"chunks": []})

    client = RagClient("http://rag.test/", "/v1/search", 5.0, transport=httpx.MockTransport(handler))
    client.search("q", "all", 6)

    assert seen["url"] == "http://rag.test/v1/search"


@pytest.mark.parametrize("status", [422, 500, 503])
def test_error_status_raises_http_status_error(status):
    client = _client(lambda request: httpx.Response(status, json={"detail": "오류"}))
    with pytest.raises(httpx.HTTPStatusError) as e:
        client.search("q", "all", 6)
    assert e.value.response.status_code == status


def test_connect_error_propagates():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(httpx.HTTPError):
        _client(handler).search("q", "all", 6)


def test_timeout_propagates():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(httpx.HTTPError):
        _client(handler).search("q", "all", 6)


# --- Validator 추가 검증 ---

def test_404_raises_http_status_error():
    client = _client(lambda request: httpx.Response(404, json={"detail": "not found"}))
    with pytest.raises(httpx.HTTPStatusError):
        client.search("q", "all", 6)


def test_search_body_contains_only_contract_keys():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"chunks": []})

    _client(handler).search("q", "openapi", 6)
    assert sorted(seen["body"]) == ["query", "source", "top_k"]


def test_search_preserves_server_chunk_order():
    payload = [{"chunk_id": f"{i}#0", "title": f"t{i}", "url": f"https://x/{i}", "source": "openapi",
                "content": f"c{i}", "metadata": {}} for i in range(3)]
    chunks = _client(lambda request: httpx.Response(200, json={"chunks": payload})).search("q", "openapi", 6)
    assert [c["chunk_id"] for c in chunks] == ["0#0", "1#0", "2#0"]
