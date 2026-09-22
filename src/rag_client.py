"""KUDOS RAG 서버의 POST /v1/search 를 호출하는 HTTP 클라이언트."""
import httpx


class RagClient:
    def __init__(self, base_url: str, search_path: str, timeout: float,
                 transport: httpx.BaseTransport | None = None):
        self._base_url = base_url.rstrip("/")
        self._search_path = search_path
        self._timeout = timeout
        self._transport = transport  # 테스트 주입용

    def search(self, query: str, source: str, top_k: int) -> list[dict]:
        """검색 청크 목록을 돌려준다.

        HTTP 오류(4xx/5xx)는 httpx.HTTPStatusError, 연결/타임아웃 실패는 httpx.HTTPError 로 그대로 전파한다.
        """
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            response = client.post(
                f"{self._base_url}{self._search_path}",
                json={"query": query, "source": source, "top_k": top_k},
            )
            response.raise_for_status()
            return response.json()["chunks"]
