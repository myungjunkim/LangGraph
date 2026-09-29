"""사내 API 실호출 도구(GET 전용).

안전 원칙:
- 메서드 인자를 두지 않는다. 도구가 할 수 있는 일이 GET 뿐이라 쓰기 요청을 보낼 경로가 없다.
- 운영 환경 차단은 이중 가드다. ① 설정 [api-services] 에 등재된 이름만 부를 수 있고
  ② 그 base URL 이 아래 QA 표식·https·순수 origin 검사를 통과해야 한다.
  이 검사는 코드 상수라 설정으로 끌 수 없고, 위반하면 도구 생성(=서버 기동) 시점에 ValueError 다.
"""
import re
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import BaseTool, tool

ALLOWED_SCHEME = "https"
QA_HOST_PATTERN = re.compile(r"(^|[.-])qa([.-]|$)")     # 운영 차단 — 설정으로 끌 수 없다
TRUNCATED_SUFFIX = "… (본문이 잘렸습니다. 전체 {length}자)"


def validate_base_url(name: str, base_url: str) -> str:
    """스킴·QA 표식·순수 origin 검사. 위반 시 ValueError(이유 포함)."""
    parts = urlsplit(base_url)
    if parts.scheme != ALLOWED_SCHEME:
        raise ValueError(f"[api-services] {name}: {ALLOWED_SCHEME} 주소만 허용합니다: {base_url}")
    if not parts.hostname or not QA_HOST_PATTERN.search(parts.hostname):
        raise ValueError(f"[api-services] {name}: QA 환경 주소가 아닙니다(호스트에 qa 라벨 필요): {base_url}")
    if parts.path or parts.query or parts.fragment:
        raise ValueError(f"[api-services] {name}: 경로·쿼리 없는 주소만 허용합니다: {base_url}")
    return f"{parts.scheme}://{parts.netloc}"


def _format_response(response: httpx.Response, url: str, max_chars: int) -> str:
    body = response.text
    if len(body) > max_chars:
        body = body[:max_chars] + TRUNCATED_SUFFIX.format(length=len(response.text))
    return f"HTTP {response.status_code} GET {url}\n{body}"


def build_api_tool(services: dict[str, str], timeout: float, max_chars: int,
                   transport: httpx.BaseTransport | None = None) -> BaseTool:
    """GET 전용 call_api 도구를 만든다. 등재된 주소가 QA 가 아니면 여기서 ValueError."""
    base_urls = {name: validate_base_url(name, url) for name, url in services.items()}
    names = ", ".join(sorted(base_urls))

    @tool
    def call_api(service: str, path: str) -> str:
        """사내 API 를 실제로 호출해 응답을 가져온다. GET 전용이라 데이터가 바뀌지 않는다."""
        base_url = base_urls.get(service)
        if base_url is None:                     # 네트워크를 쓰지 않고 모델이 고칠 수 있게 안내만 한다
            return f"사용할 수 없는 service 입니다: {service}. 가능한 값: {names}"
        if not path.startswith("/") or path.startswith("//"):
            return f"path 는 '/' 로 시작하는 경로여야 합니다: {path}"

        url = f"{base_url}{path}"
        # 리다이렉트로 운영 주소에 가지 않도록 따라가지 않는다. 연결 실패·타임아웃은 ToolNode 가 처리한다
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
            response = client.get(url)
        return _format_response(response, url, max_chars)

    call_api.description = (
        "사내 API 를 실제로 호출해 응답을 가져온다. GET 전용이라 데이터가 바뀌지 않는다.\n"
        f"service 는 다음 중 하나: {names}\n"
        "path 는 '/' 로 시작하는 경로이며 쿼리스트링을 포함할 수 있다.\n"
        "먼저 search_openapi 로 경로와 파라미터를 확인한 뒤 호출한다."
    )
    return call_api
