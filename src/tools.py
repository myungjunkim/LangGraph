"""에이전트가 사용하는 RAG 검색 도구."""
from langchain_core.tools import BaseTool, tool

from src.rag_client import RagClient

NO_RESULT_TEXT = "검색 결과가 없습니다."


def format_chunks(chunks: list[dict]) -> str:
    """검색 청크를 LLM 이 읽을 문자열로 만든다. 출처 인용을 위해 title/url 을 함께 적는다."""
    if not chunks:
        return NO_RESULT_TEXT
    blocks = [
        f"### {chunk.get('title', '')}\n"
        f"- source: {chunk.get('source', '')} | url: {chunk.get('url', '')}\n"
        f"{chunk.get('content', '')}"
        for chunk in chunks
    ]
    return "\n\n".join(blocks)


def build_tools(client: RagClient, top_k: int) -> list[BaseTool]:
    """[search_confluence, search_openapi] 순서로 도구를 만든다."""

    @tool
    def search_confluence(query: str) -> str:
        """팀 Confluence(KUDOS) 문서를 검색한다. 정책, 설정값 정의, 운영 절차, 용어, 배경 설명 질문에 사용한다."""
        return format_chunks(client.search(query, "confluence", top_k))

    @tool
    def search_openapi(query: str) -> str:
        """사내 API 의 OpenAPI 스펙(엔드포인트, HTTP 메서드, 경로, 파라미터, 요청/응답 스키마)을 검색한다. API 호출 방법 질문에 사용한다."""
        return format_chunks(client.search(query, "openapi", top_k))

    return [search_confluence, search_openapi]
