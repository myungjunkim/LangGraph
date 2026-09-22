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
    )


def test_load_settings_converts_types(write_config):
    settings = load_settings(write_config())

    assert isinstance(settings.rag_timeout, float)
    assert isinstance(settings.rag_top_k, int)
    assert isinstance(settings.num_ctx, int)
    assert isinstance(settings.temperature, float)
    assert isinstance(settings.llm_timeout, float)
    assert isinstance(settings.recursion_limit, int)


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


def test_config_path_for():
    assert config_path_for("local") == PROJECT_ROOT / "resources" / "config_local.ini"


def test_example_config_has_every_key():
    """example 템플릿만으로 Settings 를 만들 수 있어야 한다."""
    assert load_settings(PROJECT_ROOT / "resources" / "config_local.ini.example").llm_model == "qwen3:14b"
