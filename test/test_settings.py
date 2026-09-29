from pathlib import Path

import pytest

from src.config.settings import PROJECT_ROOT, Settings, config_path_for, load_settings
from test.conftest import CONFIG_TEXT


def test_load_settings_reads_all_keys(write_config):
    settings = load_settings(write_config())

    assert settings == Settings(
        rag_base_url="http://127.0.0.1:5010",
        rag_search_path="/v1/search",
        rag_timeout=30.0,
        rag_top_k=6,
        ollama_base_url="http://127.0.0.1:11434",
        llm_model="qwen3:14b",
        num_ctx=16384,
        temperature=0.0,
        llm_timeout=120.0,
        recursion_limit=12,
        host="127.0.0.1",
        port=5020,
        checkpoint_db=PROJECT_ROOT / "data" / "checkpoints.sqlite",
        api_services={"general-chatbot-api": "https://qa-general-chatbot-api.hunet.ai",
                      "message-api": "https://message-api.qa.hunet.io"},
        api_timeout=20.0,
        api_max_chars=2000,
    )


def test_load_settings_converts_types(write_config):
    settings = load_settings(write_config())

    assert isinstance(settings.rag_timeout, float)
    assert isinstance(settings.rag_top_k, int)
    assert isinstance(settings.num_ctx, int)
    assert isinstance(settings.temperature, float)
    assert isinstance(settings.llm_timeout, float)
    assert isinstance(settings.recursion_limit, int)
    assert isinstance(settings.host, str)
    assert isinstance(settings.port, int)
    assert isinstance(settings.checkpoint_db, Path)
    assert isinstance(settings.api_timeout, float)
    assert isinstance(settings.api_max_chars, int)


def test_settings_is_frozen(write_config):
    settings = load_settings(write_config())
    with pytest.raises(Exception):
        settings.rag_top_k = 99


def test_load_settings_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "없는파일.ini")


def test_load_settings_missing_key_raises(write_config):
    text = CONFIG_TEXT.replace("top-k=6\n", "")
    with pytest.raises(KeyError):
        load_settings(write_config(text))


def test_load_settings_missing_section_raises(write_config):
    text = CONFIG_TEXT.replace("[agent]\nrecursion-limit=12\n", "")
    with pytest.raises(KeyError):
        load_settings(write_config(text))


def test_load_settings_missing_fastapi_section_raises(write_config):
    text = CONFIG_TEXT.replace("[fastapi]\nhost=127.0.0.1\nport=5020\n", "")
    with pytest.raises(KeyError):
        load_settings(write_config(text))


def test_config_path_for():
    assert config_path_for("local") == PROJECT_ROOT / "resources" / "config_local.ini"


def test_example_config_has_every_key():
    """example 템플릿만으로 Settings 를 만들 수 있어야 한다."""
    assert load_settings(PROJECT_ROOT / "resources" / "config_local.ini.example").llm_model == "qwen3:14b"


def test_checkpoint_relative_path_is_resolved_from_project_root(write_config):
    """상대 경로는 실행 디렉터리와 무관하게 프로젝트 루트 기준이어야 한다."""
    settings = load_settings(write_config(CONFIG_TEXT.replace("db-path=data/checkpoints.sqlite",
                                                              "db-path=data/sub/db.sqlite")))
    assert settings.checkpoint_db == PROJECT_ROOT / "data" / "sub" / "db.sqlite"
    assert settings.checkpoint_db.is_absolute()


def test_checkpoint_absolute_path_is_kept(write_config, tmp_path):
    absolute = tmp_path / "custom.sqlite"
    settings = load_settings(write_config(CONFIG_TEXT.replace("db-path=data/checkpoints.sqlite",
                                                              f"db-path={absolute}")))
    assert settings.checkpoint_db == absolute


def test_load_settings_missing_checkpoint_section_raises(write_config):
    text = CONFIG_TEXT.replace("[checkpoint]\ndb-path=data/checkpoints.sqlite\n", "")
    with pytest.raises(KeyError):
        load_settings(write_config(text))



def test_load_settings_missing_db_path_key_raises(write_config):
    """섹션은 있는데 키가 없으면 기존 정책대로 KeyError 를 그대로 올린다."""
    text = CONFIG_TEXT.replace("db-path=data/checkpoints.sqlite", "")
    with pytest.raises(KeyError):
        load_settings(write_config(text))


def test_api_services_is_a_name_to_url_mapping(write_config):
    services = load_settings(write_config()).api_services

    assert services == {"general-chatbot-api": "https://qa-general-chatbot-api.hunet.ai",
                        "message-api": "https://message-api.qa.hunet.io"}


def test_empty_api_services_section_is_allowed(write_config):
    """항목이 없으면 빈 dict — 설정 실수로 서버가 못 뜨지는 않게 한다."""
    text = CONFIG_TEXT.replace("general-chatbot-api=https://qa-general-chatbot-api.hunet.ai\n", "")
    text = text.replace("message-api=https://message-api.qa.hunet.io\n", "")
    assert load_settings(write_config(text)).api_services == {}


def test_load_settings_missing_api_services_section_raises(write_config):
    text = CONFIG_TEXT.replace("[api-services]\n", "[지워진섹션]\n")
    with pytest.raises(KeyError):
        load_settings(write_config(text))


def test_load_settings_missing_api_section_raises(write_config):
    text = CONFIG_TEXT.replace("[api]\ntimeout=20\nmax-response-chars=2000\n", "")
    with pytest.raises(KeyError):
        load_settings(write_config(text))

