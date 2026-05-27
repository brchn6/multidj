from __future__ import annotations
import pytest
from multidj.config import get_llm_config


def test_returns_none_when_section_missing():
    assert get_llm_config({}) is None


def test_returns_none_when_api_key_missing():
    cfg = {"llm": {"base_url": "https://example.com"}}
    assert get_llm_config(cfg) is None


def test_returns_none_when_base_url_missing():
    cfg = {"llm": {"api_key": "sk-test"}}
    assert get_llm_config(cfg) is None


def test_returns_config_when_both_present():
    cfg = {"llm": {"base_url": "https://opencode.ai/api/v1", "api_key": "sk-test", "model": "deepseek/deepseek-chat"}}
    result = get_llm_config(cfg)
    assert result is not None
    assert result["base_url"] == "https://opencode.ai/api/v1"
    assert result["api_key"] == "sk-test"
    assert result["model"] == "deepseek/deepseek-chat"


def test_default_model_when_not_specified():
    cfg = {"llm": {"base_url": "https://opencode.ai/api/v1", "api_key": "sk-test"}}
    result = get_llm_config(cfg)
    assert result is not None
    assert result["model"] == "gpt-3.5-turbo"
