"""실제 QA API 에 GET 1건을 보내는 통합 테스트.

실행: pytest -m integration
부작용 없는 조회 엔드포인트만 호출한다. 쓰기 요청은 어떤 경로로도 만들지 않는다(도구에 메서드 인자가 없다).
사내망·VPN 이 필요할 수 있어 연결되지 않으면 skip 한다.
"""
import httpx
import pytest

from src.api_tool import build_api_tool
from src.config.settings import config_path_for, load_settings

pytestmark = pytest.mark.integration

PROBE_PATH = "/openapi.json"


@pytest.fixture(scope="module")
def settings():
    path = config_path_for("local")
    if not path.is_file():
        pytest.skip(f"설정 파일이 없습니다: {path}")
    loaded = load_settings(path)
    if not loaded.api_services:
        pytest.skip("[api-services] 에 등재된 서비스가 없습니다")
    return loaded


@pytest.fixture(scope="module")
def reachable_service(settings):
    """연결되는 QA 서비스 1개를 고른다. 모두 실패하면 skip."""
    errors = []
    for name, base_url in settings.api_services.items():
        try:
            httpx.get(f"{base_url}{PROBE_PATH}", timeout=5, follow_redirects=False)
        except httpx.HTTPError as e:
            errors.append(f"{name}: {e}")
            continue
        return name
    pytest.skip(f"QA API 에 연결할 수 없습니다(사내망·VPN 필요할 수 있음): {errors}")


def test_call_api_reaches_qa_service(settings, reachable_service, capsys):
    tool = build_api_tool(settings.api_services, settings.api_timeout, settings.api_max_chars)

    result = tool.invoke({"service": reachable_service, "path": PROBE_PATH})

    assert result.startswith("HTTP ")
    assert f"GET {settings.api_services[reachable_service]}{PROBE_PATH}" in result
    with capsys.disabled():
        print(f"\n[C11] {reachable_service}{PROBE_PATH} → {result.splitlines()[0]} "
              f"(본문 {len(result.splitlines()[1]) if len(result.splitlines()) > 1 else 0}자)")
    assert "HTTP 200" in result


def test_unknown_service_makes_no_request_against_real_config(settings):
    """실환경 설정에서도 미등록 이름은 네트워크를 쓰지 않고 안내만 한다."""
    tool = build_api_tool(settings.api_services, settings.api_timeout, settings.api_max_chars)

    result = tool.invoke({"service": "없는서비스", "path": PROBE_PATH})

    assert result.startswith("사용할 수 없는 service 입니다")
