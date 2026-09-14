"""FUYAO cache 配置模型 + save_fuyao_cache (PATCH 写盘 + 乐观锁) + cache_strategy 命中路径。"""
from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from src.unihive.core import cache_strategy
from src.unihive.models.fuyao_cache import (
    FUYAO_ENDPOINT_NAMES,
    FuyaoCacheConfig,
    default_fuyao_cache_config,
)


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------


class TestFuyaoCacheConfigSchema:
    def test_default_factory_returns_four_endpoints(self):
        cfg = default_fuyao_cache_config()
        assert set(cfg.endpoints.keys()) == set(FUYAO_ENDPOINT_NAMES)

    def test_endpoints_known_only(self):
        with pytest.raises(ValueError, match="未知的 fuyao endpoint"):
            FuyaoCacheConfig.model_validate({
                "endpoints": {"fuyao_unknown": {"default": 60, "override": {}}}
            })

    def test_default_must_be_non_negative_int(self):
        with pytest.raises(ValueError):
            FuyaoCacheConfig.model_validate({
                "endpoints": {"fuyao_ashare": {"default": -1, "override": {}}}
            })

    def test_override_seconds_must_be_non_negative(self):
        with pytest.raises(ValueError):
            FuyaoCacheConfig.model_validate({
                "endpoints": {"fuyao_ashare": {"default": 60, "override": {"foo": -1}}}
            })

    def test_override_keys_sorted_after_validation(self):
        cfg = FuyaoCacheConfig.model_validate({
            "endpoints": {
                "fuyao_ashare": {"default": 60, "override": {"zeta": 10, "alpha": 20}}
            }
        })
        assert list(cfg.endpoints["fuyao_ashare"].override.keys()) == ["alpha", "zeta"]

    def test_extra_keys_rejected(self):
        with pytest.raises(ValueError):
            FuyaoCacheConfig.model_validate({
                "endpoints": {
                    "fuyao_ashare": {"default": 60, "override": {}, "rogue": 1}
                }
            })

    def test_total_override_count(self):
        cfg = FuyaoCacheConfig.model_validate({
            "endpoints": {
                "fuyao_ashare": {"default": 60, "override": {"a": 1, "b": 2}},
                "fuyao_index": {"default": 300, "override": {"c": 3}},
                "fuyao_meta": {"default": 86400, "override": {}},
                "fuyao_fund": {"default": 600, "override": {}},
            }
        })
        assert cfg.total_override_count() == 3


# ---------------------------------------------------------------------------
# cache_strategy.get_fuyao_endpoint_for_tool
# ---------------------------------------------------------------------------


class TestGetFuyaoEndpoint:
    def test_single_fuyao_upstream(self):
        cfg = {"upstream_tool_mapping": {"foo": {"fuyao_ashare": "x"}}}
        assert cache_strategy.get_fuyao_endpoint_for_tool(cfg, "foo") == "fuyao_ashare"

    def test_picks_first_endpoint_in_chain_order(self):
        cfg = {
            "upstream_tool_mapping": {
                "foo": {"fuyao_index": "x", "fuyao_ashare": "y"}
            }
        }
        assert cache_strategy.get_fuyao_endpoint_for_tool(cfg, "foo") == "fuyao_index"

    def test_mixed_chain_returns_none(self):
        cfg = {"upstream_tool_mapping": {"foo": {"fuyao_ashare": "x", "mootdx2": "y"}}}
        assert cache_strategy.get_fuyao_endpoint_for_tool(cfg, "foo") is None

    def test_no_mapping_returns_none(self):
        assert cache_strategy.get_fuyao_endpoint_for_tool({}, "foo") is None

    def test_empty_tool_returns_none(self):
        assert cache_strategy.get_fuyao_endpoint_for_tool({"upstream_tool_mapping": {"foo": {}}}, "") is None


# ---------------------------------------------------------------------------
# cache_strategy.fuyao_ttl_for (override → default → None 三级回退)
# ---------------------------------------------------------------------------


class TestFuyaoTtlFor:
    @pytest.fixture
    def cfg(self):
        return {
            "upstream_tool_mapping": {
                "tool_a": {"fuyao_ashare": "x"},
                "tool_b": {"fuyao_index": "x"},
                "tool_c": {"fuyao_meta": "x"},
            },
            "fuyao_cache": {
                "endpoints": {
                    "fuyao_ashare": {"default": 300, "override": {"tool_a": 60}},
                    "fuyao_index": {"default": 3600, "override": {}},
                    "fuyao_meta": {"default": 0, "override": {}},
                }
            },
        }

    def test_override_wins_over_default(self, cfg):
        assert cache_strategy.fuyao_ttl_for(cfg, "tool_a") == 60

    def test_default_used_when_no_override(self, cfg):
        assert cache_strategy.fuyao_ttl_for(cfg, "tool_b") == 3600

    def test_zero_default_returned_as_zero(self, cfg):
        """TTL=0 有效 (实时/不缓存), 必须返回 0 而非 None。"""
        assert cache_strategy.fuyao_ttl_for(cfg, "tool_c") == 0

    def test_unknown_tool_returns_none(self, cfg):
        assert cache_strategy.fuyao_ttl_for(cfg, "tool_unknown") is None

    def test_endpoint_explicit_override(self, cfg):
        """显式传入 endpoint 可跳过 mapping 查询, 用于调用方已确认场景。"""
        assert cache_strategy.fuyao_ttl_for(cfg, "tool_a", endpoint="fuyao_ashare") == 60

    def test_no_fuyao_cache_section(self):
        cfg = {"upstream_tool_mapping": {"tool_a": {"fuyao_ashare": "x"}}}
        assert cache_strategy.fuyao_ttl_for(cfg, "tool_a") is None

    def test_mixed_chain_returns_none(self):
        cfg = {
            "upstream_tool_mapping": {"foo": {"fuyao_ashare": "x", "mootdx2": "y"}},
            "fuyao_cache": {"endpoints": {"fuyao_ashare": {"default": 60, "override": {}}}},
        }
        assert cache_strategy.fuyao_ttl_for(cfg, "foo") is None

    def test_fallback_to_global_ttl_when_fuyao_returns_none(self):
        """fuyao_ttl_for 返回 None 时, 调用方应回退 ttl_for(cache.ttl[ttl_key])。"""
        config = {"cache": {"ttl": {"fundamentals": 3600}}}
        assert cache_strategy.ttl_for(config, "fundamentals") == 3600
        # fuyao 未配置 → None, 不会污染全局
        assert cache_strategy.fuyao_ttl_for(config, "foo") is None


# ---------------------------------------------------------------------------
# save_fuyao_cache (落盘 + 乐观锁)
# ---------------------------------------------------------------------------


@pytest.fixture
def upstream_yaml(tmp_path: Path, monkeypatch) -> Path:
    """写一份最小 upstreams.yaml + 加载 console_api 模块, 用 monkeypatch 切 GATEWAY_CONFIG_PATH。"""
    p = tmp_path / "upstreams.yaml"
    p.write_text(
        textwrap.dedent("""\
            upstreams:
              fuyao_ashare:
                enabled: true
                type: http
                base_url: "https://example.com/mcp"
                api_key: "test"
            cache:
              ttl:
                fundamentals: 3600
            """),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent))
    # 强制 reload console_api, 让 GATEWAY_CONFIG_PATH 指向 tmp
    if "src.unihive.utils.console_api" in sys.modules:
        del sys.modules["src.unihive.utils.console_api"]
    import src.unihive.utils.console_api as ca

    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(p))
    return p


class TestSaveFuyaoCache:
    def test_writes_endpoints_and_preserves_other_sections(self, upstream_yaml):
        ca = importlib.import_module("src.unihive.utils.console_api")
        result = ca.save_fuyao_cache(
            {
                "endpoints": {
                    "fuyao_ashare": {"default": 120, "override": {"tool_a": 30}}
                }
            },
            expected_version=None,
        )
        assert result["fuyao_cache"]["endpoints"]["fuyao_ashare"]["default"] == 120
        assert result["fuyao_cache"]["endpoints"]["fuyao_ashare"]["override"]["tool_a"] == 30
        # 其他顶层段必须保留
        data = yaml.safe_load(upstream_yaml.read_text(encoding="utf-8"))
        assert "upstreams" in data
        assert data["cache"]["ttl"]["fundamentals"] == 3600
        assert "fuyao_cache" in data

    def test_version_conflict_raises(self, upstream_yaml):
        ca = importlib.import_module("src.unihive.utils.console_api")
        with pytest.raises(ca.VersionConflict, match="版本不一致"):
            ca.save_fuyao_cache(
                {"endpoints": {"fuyao_ashare": {"default": 60, "override": {}}}},
                expected_version="wrong-version",
            )

    def test_validation_error_for_negative_default(self, upstream_yaml):
        ca = importlib.import_module("src.unihive.utils.console_api")
        with pytest.raises(ValueError, match="校验失败"):
            ca.save_fuyao_cache(
                {"endpoints": {"fuyao_ashare": {"default": -10, "override": {}}}},
                expected_version=None,
            )

    def test_validation_error_for_unknown_endpoint(self, upstream_yaml):
        ca = importlib.import_module("src.unihive.utils.console_api")
        with pytest.raises(ValueError, match="校验失败"):
            ca.save_fuyao_cache(
                {"endpoints": {"fuyao_bogus": {"default": 60, "override": {}}}},
                expected_version=None,
            )

    def test_load_config_includes_version(self, upstream_yaml):
        ca = importlib.import_module("src.unihive.utils.console_api")
        result = ca.load_config()
        assert "version" in result
        assert len(result["version"]) == 16

    def test_get_fuyao_cache_returns_empty_when_unset(self, upstream_yaml):
        ca = importlib.import_module("src.unihive.utils.console_api")
        result = ca.get_fuyao_cache()
        assert result["endpoints"] == {}

    def test_round_trip(self, upstream_yaml):
        ca = importlib.import_module("src.unihive.utils.console_api")
        version1 = ca.load_config()["version"]
        saved = ca.save_fuyao_cache(
            {
                "endpoints": {
                    "fuyao_ashare": {"default": 200, "override": {"x": 50}},
                    "fuyao_index": {"default": 1200, "override": {}},
                }
            },
            expected_version=version1,
        )
        # version 必须变化 (mtime 变化)
        assert saved["version"] != version1
        # 再读 — 一致
        loaded = ca.get_fuyao_cache()
        assert loaded["endpoints"]["fuyao_ashare"]["default"] == 200
        assert loaded["endpoints"]["fuyao_ashare"]["override"]["x"] == 50
