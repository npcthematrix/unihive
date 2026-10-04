"""Round-12 簇E: 启动期配置错误必须 fail-fast 且报出字段名。

- 未知 upstream.type 以前静默退化成空 command 的 stdio 客户端, 延迟到
  调用时才以费解错误爆炸 → 构造期 ValueError + 合法类型表;
- cache.max_entries / operation_timeout / gateway.per_upstream_timeout 数值强转
  以前裸 int()/float(), 坏值抛无名 ValueError/TypeError → 助手函数带
  字段路径;
- fuyao_cache.tools 非数值 (YAML 模板可能绕过 PUT pydantic 门) →
  fuyao_ttl_for 带字段名 ValueError, _execute_cached 降级不缓存;
- main() 捕 yaml.YAMLError / KeyError → 干净消息 + 退出码 2 (配置错),
  不裸奔 traceback。

2026-10-05: 移除 gateway.rate_limit 段 (rate_limiter 整套下线), 相关 bad-value
用例随之删除; 余下用例覆盖未知 upstream.type / coerce_* / fuyao_cache /
main exit code 四类。
"""
from __future__ import annotations

import pytest
import yaml

from src.unihive.core import cache_strategy
from src.unihive.gateway_server import _helpers


# ---------------------------------------------------------------------------
# 数值字段强转助手
# ---------------------------------------------------------------------------


class TestCoerceNumericFields:
    def test_int_ok(self):
        assert _helpers.coerce_int_field(100, "cache.max_entries") == 100

    def test_int_numeric_string_rejected(self):
        with pytest.raises(ValueError, match=r"cache\.max_entries"):
            _helpers.coerce_int_field("100", "cache.max_entries")

    def test_int_bool_rejected(self):
        with pytest.raises(ValueError, match=r"cache\.max_entries"):
            _helpers.coerce_int_field(True, "cache.max_entries")

    def test_int_garbage_names_field(self):
        with pytest.raises(ValueError, match=r"cache\.max_entries"):
            _helpers.coerce_int_field("abc", "cache.max_entries")

    def test_int_none_uses_default(self):
        assert _helpers.coerce_int_field(None, "cache.max_entries", default=50000) == 50000

    def test_int_out_of_range(self):
        with pytest.raises(ValueError, match=r"cache\.max_entries"):
            _helpers.coerce_int_field(0, "cache.max_entries", minimum=1)
        with pytest.raises(ValueError, match=r"cache\.max_entries"):
            _helpers.coerce_int_field(-5, "cache.max_entries", minimum=1)

    def test_float_ok(self):
        assert _helpers.coerce_float_field(2.5, "cache.operation_timeout") == 2.5

    def test_float_garbage_names_field(self):
        with pytest.raises(ValueError, match=r"cache\.operation_timeout"):
            _helpers.coerce_float_field("fast", "cache.operation_timeout")

    def test_float_bool_rejected(self):
        with pytest.raises(ValueError, match=r"x\.rate"):
            _helpers.coerce_float_field(False, "x.rate")

    def test_float_none_default(self):
        assert _helpers.coerce_float_field(None, "x.rate", default=20.0) == 20.0

    def test_float_must_be_positive(self):
        with pytest.raises(ValueError, match=r"x\.rate"):
            _helpers.coerce_float_field(0.0, "x.rate", minimum=0.0, exclusive_min=True)
        with pytest.raises(ValueError, match=r"x\.rate"):
            _helpers.coerce_float_field(-1.0, "x.rate", minimum=0.0, exclusive_min=True)


# ---------------------------------------------------------------------------
# _apply_gateway_tuning: per_upstream_timeout 坏值带字段名
# 2026-10-05: rate_limit 段已移除, 仅保留 per_upstream_timeout 用例。
# ---------------------------------------------------------------------------


class TestGatewayTuningValidation:
    def _server(self, gateway_cfg, gateway_server_minimal):
        server = gateway_server_minimal
        server.config = {"gateway": gateway_cfg}
        return server

    def test_per_upstream_timeout_validated(self, gateway_server_minimal):
        server = self._server({"per_upstream_timeout": "soon"}, gateway_server_minimal)
        with pytest.raises(ValueError, match="gateway.per_upstream_timeout"):
            server._apply_gateway_tuning()


# ---------------------------------------------------------------------------
# 未知 upstream 类型
# ---------------------------------------------------------------------------


class TestUnknownUpstreamType:
    def test_unknown_type_raises_with_table(self, gateway_server_minimal):
        server = gateway_server_minimal
        with pytest.raises(ValueError) as exc:
            server._construct_upstream_client("weird", {"type": "http_jsonrpc"})
        msg = str(exc.value)
        assert "weird" in msg
        assert "http_jsonrpc" in msg
        for valid in ("http", "mootdx2", "tdx_quant", "omni", "thsdk", "ths_local_gateway", "stdio"):
            assert valid in msg

    def test_stdio_type_still_constructs(self, gateway_server_minimal):
        from src.unihive.api.upstream_client import UpstreamClient

        server = gateway_server_minimal
        client = server._construct_upstream_client(
            "legacy", {"type": "stdio", "package": "some-pkg"}
        )
        assert isinstance(client, UpstreamClient)

    def test_missing_type_defaults_to_stdio(self, gateway_server_minimal):
        """缺 type 键是历史 stdio 扩展点契约, 启动失败的 client 仍需可登记清理。

        显式未知 type 才 fail-fast; None 与 "stdio" 同路 (strict 校验由
        validate_config 报 "missing 'type'", 这里不重复拒)。
        """
        from src.unihive.api.upstream_client import UpstreamClient

        server = gateway_server_minimal
        client = server._construct_upstream_client(
            "legacy", {"package": "some-pkg"}
        )
        assert isinstance(client, UpstreamClient)


# ---------------------------------------------------------------------------
# fuyao_ttl_for 非数值
# ---------------------------------------------------------------------------


class TestFuyaoTtlGarbage:
    def test_non_numeric_tool_ttl_named(self):
        config = {
            "fuyao_cache": {"tools": {"hist_tool": "1h"}},
            "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
        }
        with pytest.raises(ValueError, match=r"fuyao_cache\.tools\.hist_tool"):
            cache_strategy.fuyao_ttl_for(config, "hist_tool")

    def test_bool_tool_ttl_rejected(self):
        config = {
            "fuyao_cache": {"tools": {"hist_tool": True}},
            "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
        }
        with pytest.raises(ValueError):
            cache_strategy.fuyao_ttl_for(config, "hist_tool")


# ---------------------------------------------------------------------------
# main() 配置类异常 → exit 2, 不打 traceback
# ---------------------------------------------------------------------------


class TestMainConfigExceptionMapping:
    def test_yaml_syntax_error_exits_2(self, monkeypatch, capsys):
        import src.unihive.gateway_server as gs_mod

        async def fake_async_main(*a, **kw):
            raise yaml.YAMLError("bad indentation at line 3")

        monkeypatch.setattr(gs_mod, "async_main", fake_async_main)
        monkeypatch.setattr("sys.argv", ["gw", "--config", "x.yaml"])
        with pytest.raises(SystemExit) as exc:
            gs_mod.main()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "bad indentation" in err
        assert "Traceback" not in err

    def test_key_error_exits_2(self, monkeypatch, capsys):
        import src.unihive.gateway_server as gs_mod

        async def fake_async_main(*a, **kw):
            raise KeyError("gateway")

        monkeypatch.setattr(gs_mod, "async_main", fake_async_main)
        monkeypatch.setattr("sys.argv", ["gw"])
        with pytest.raises(SystemExit) as exc:
            gs_mod.main()
        assert exc.value.code == 2
        assert "'gateway'" in capsys.readouterr().err
