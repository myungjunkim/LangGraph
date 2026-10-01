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
# 목록 절단은 문구가 다르다. 목록이 잘려도 총 개수는 정확하다는 사실을 모델에게 알려야 한다
LIST_TRUNCATED_SUFFIX = "… (목록이 잘렸습니다. 위 총 개수는 정확합니다. 표시 {shown}개 / 전체 {total}개)"
OPENAPI_SPEC_PATH = "/openapi.json"
# OpenAPI 가 정의한 오퍼레이션 키. paths[path] 아래의 parameters·summary·$ref 같은
# 비(非)메서드 키를 세면 개수가 틀리므로 이 화이트리스트에 있는 키만 센다
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


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


def _get(url: str, timeout: float, transport: httpx.BaseTransport | None) -> httpx.Response:
    """이 모듈의 유일한 HTTP 호출 지점. GET 외의 요청이 끼어들 자리를 만들지 않는다.

    리다이렉트로 운영 주소에 가지 않도록 따라가지 않는다. 연결 실패·타임아웃은 ToolNode 가 처리한다.
    """
    with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
        return client.get(url)


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
        return _format_response(_get(url, timeout, transport), url, max_chars)

    call_api.description = (
        "사내 API 를 실제로 호출해 응답을 가져온다. GET 전용이라 데이터가 바뀌지 않는다.\n"
        f"service 는 다음 중 하나: {names}\n"
        "path 는 '/' 로 시작하는 경로이며 쿼리스트링을 포함할 수 있다.\n"
        "먼저 search_openapi 로 경로와 파라미터를 확인한 뒤 호출한다."
    )
    return call_api


def _one_line(text) -> str:
    """설명을 한 줄로 만든다. 한 항목 = 한 줄이 깨지면 모델이 개수를 잘못 센다."""
    return " ".join(text.split()) if isinstance(text, str) else ""


def _collect_endpoints(paths: dict) -> tuple[list[str], int]:
    """(메서드, 경로) 조합을 경로 사전순 → 메서드 사전순으로 한 줄씩 만들고, 고유 경로 수를 함께 센다.

    한 경로에 메서드가 여럿이면 두 수가 갈린다(message-api: 조합 145 / 경로 130).
    세는 기준이 갈리는 값은 둘 다 보여준다.
    """
    lines, path_count = [], 0
    for path in sorted(paths):
        operations = paths[path]
        if not isinstance(operations, dict):
            continue
        before = len(lines)
        for method in sorted(operations):
            if method not in HTTP_METHODS:     # parameters·summary·$ref 등은 엔드포인트가 아니다
                continue
            operation = operations[method] if isinstance(operations[method], dict) else {}
            label = _one_line(operation.get("summary")) or _one_line(operation.get("operationId"))
            line = f"- {method.upper()} {path}"
            lines.append(f"{line} — {label}" if label else line)
        if len(lines) > before:                # 메서드가 하나도 없는 경로는 고유 경로로 세지 않는다
            path_count += 1
    return lines, path_count


def _format_endpoint_list(header: str, endpoints: list[str], max_chars: int) -> str:
    """머리글(총 개수·출처)은 항상 남기고, 넘치면 항목만 줄 경계에서 자른다.

    줄 중간에서 자르면 엔드포인트가 반쪽으로 남아 모델이 오해한다.
    """
    full = "\n".join([header, *endpoints])
    if len(full) <= max_chars:
        return full
    used, shown = len(header), 0
    for line in endpoints:
        if used + 1 + len(line) > max_chars:
            break
        used += 1 + len(line)
        shown += 1
    kept = "\n".join([header, *endpoints[:shown]])
    return f"{kept}\n{LIST_TRUNCATED_SUFFIX.format(shown=shown, total=len(endpoints))}"


def build_endpoint_list_tool(services: dict[str, str], timeout: float, max_chars: int,
                             transport: httpx.BaseTransport | None = None) -> BaseTool:
    """엔드포인트 개수를 세는 list_api_endpoints 도구를 만든다. QA 가드는 call_api 와 동일하다.

    숫자는 코드가 센다. LLM 은 장황한 스펙에서 합계를 틀리므로(agent-19 M4) 세게 하지 않는다.
    """
    base_urls = {name: validate_base_url(name, url) for name, url in services.items()}
    names = ", ".join(sorted(base_urls))

    @tool
    def list_api_endpoints(service: str) -> str:
        """사내 API 서비스의 엔드포인트 총 개수와 전체 목록을 가져온다. 개수는 코드가 세므로 정확하다."""
        base_url = base_urls.get(service)
        if base_url is None:                     # 네트워크를 쓰지 않고 모델이 고칠 수 있게 안내만 한다
            return f"사용할 수 없는 service 입니다: {service}. 가능한 값: {names}"

        url = f"{base_url}{OPENAPI_SPEC_PATH}"
        response = _get(url, timeout, transport)
        if response.status_code != 200:
            return f"HTTP {response.status_code} GET {url} — OpenAPI 스펙을 가져오지 못했습니다."
        try:
            spec = response.json()
        except ValueError:
            return f"GET {url} 응답이 JSON 이 아닙니다."

        paths = spec.get("paths") if isinstance(spec, dict) else None
        endpoints, path_count = _collect_endpoints(paths) if isinstance(paths, dict) else ([], 0)
        if not endpoints:
            return f"{service} 에 엔드포인트가 없습니다."
        # 1행이 총 개수다. 절단은 뒤에서 일어나므로 목록이 잘려도 숫자는 살아남는다.
        # 고유 경로 수는 두 값이 같아도 항상 병기한다 — 기준이 갈릴 때 불신이 생긴다.
        # 2행의 스펙 URL 은 모델이 인용할 출처다(없으면 엉뚱한 URL 을 갖다 붙인다)
        header = (f"{service} 엔드포인트 총 {len(endpoints)}개 (고유 경로 {path_count}개)\n"
                  f"출처: {url}")
        return _format_endpoint_list(header, endpoints, max_chars)

    list_api_endpoints.description = (
        "사내 API 서비스의 엔드포인트 총 개수와 전체 목록을 가져온다. 개수는 코드가 세므로 정확하다.\n"
        f"service 는 다음 중 하나: {names}\n"
        "엔드포인트가 총 몇 개인지, 어떤 엔드포인트들이 있는지 묻는 질문에 이 도구를 쓴다.\n"
        "검색 결과로 개수를 세지 않는다."
    )
    return list_api_endpoints
