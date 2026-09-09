"""THSDK 上游离线测试。

thsdk 包本身已安装但不能真实登录，这里把假 module 注入 sys.modules，
验证客户端生命周期、门控、confirm、补全、重登与 DataFrame 转换；
另含 YAML 加载期 requires_env 门控与 annotations 透传测试。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.api.thsdk_client import ThsdkClient
from src.api.upstream_client import UpstreamStatus
from src.core.tool_loader import filter_specs_by_env, load_all_tools
from src.models.thsdk_config import ThsdkConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_YAML = REPO_ROOT / "config" / "tools_ths.yaml"


# thsdk 异常按类名匹配（见 src/exceptions/thsdk_errors.py）
class AuthenticationError(Exception):
    pass


class NotAuthenticatedError(Exception):
    pass


class APIError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code


@pytest.fixture
def guest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THS_USERNAME", raising=False)
    monkeypatch.delenv("THS_PASSWORD", raising=False)
    monkeypatch.delenv("ALLOW_WATCHLIST_WRITE", raising=False)


@pytest.fixture
def fake_mod(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    mod = types.ModuleType("thsdk")
    mod.auth = MagicMock(return_value=True)
    mod.account_permissions = MagicMock(return_value={"level": "guest"})
    mod.get_account_watchlist = MagicMock(return_value={"version": 3, "securities": []})
    mod.get_account_watchlist_groups = MagicMock(return_value=[])
    mod.search_symbols = MagicMock(return_value=[])
    mod.complete_ths_code = MagicMock(return_value=[])
    price_df = pd.DataFrame(
        {"close": [10.6]}, index=pd.to_datetime(["2026-09-01"])
    )
    mod.get_price = MagicMock(return_value=price_df)
    mod.klines = MagicMock(return_value=price_df)
    mod.intraday_data = MagicMock(return_value=[])
    mod.depth = MagicMock(return_value=[])
    mod.tick_level1 = MagicMock(return_value=[])
    mod.corporate_action = MagicMock(return_value=[])
    mod.wencai_nlp = MagicMock(return_value=[])
    mod.news = MagicMock(return_value=[])
    mod.block_constituents = MagicMock(return_value=[])
    mod.market_securities = MagicMock(return_value=[])
    for write_name in (
        "add_account_watchlist_securities",
        "remove_account_watchlist_securities",
        "replace_account_watchlist_securities",
        "clear_account_watchlist",
        "create_account_watchlist_group",
        "delete_account_watchlist_group",
        "rename_account_watchlist_group",
        "add_account_watchlist_group_securities",
        "remove_account_watchlist_group_securities",
        "replace_account_watchlist_group_securities",
    ):
        setattr(mod, write_name, MagicMock(return_value={"ok": True}))
    monkeypatch.setitem(sys.modules, "thsdk", mod)
    return mod


def _client() -> ThsdkClient:
    return ThsdkClient(
        ThsdkConfig(name="thsdk", call_timeout_sec=5, throttle_ms=0)
    )


async def _started(fake_mod: types.ModuleType) -> ThsdkClient:
    client = _client()
    assert await client.start() is True
    return client


# ---------- 生命周期与鉴权 ----------

async def test_start_guest_auth_success(guest_env, fake_mod: types.ModuleType):
    client = await _started(fake_mod)
    assert client.is_available
    fake_mod.auth.assert_called_once_with()

    result = await client.call_tool("ths_auth_status", {})
    assert result.success
    assert result.data == {
        "authenticated": True,
        "auth_mode": "guest",
        "guest": True,
        "watchlist_write_enabled": False,
    }


async def test_start_credentials_mode(
    monkeypatch: pytest.MonkeyPatch, fake_mod: types.ModuleType
):
    monkeypatch.delenv("ALLOW_WATCHLIST_WRITE", raising=False)
    monkeypatch.setenv("THS_USERNAME", "13800000000")
    monkeypatch.setenv("THS_PASSWORD", "secret")
    client = await _started(fake_mod)
    fake_mod.auth.assert_called_once_with("13800000000", "secret")
    result = await client.call_tool("ths_auth_status", {})
    assert result.data["auth_mode"] == "credentials"
    assert result.data["guest"] is False


def test_is_available_false_when_degraded(guest_env, fake_mod):
    """M2: THSDK 没有 "半恢复" 语义, DEGRADED 时 is_available 必须为 False。

    TdxQuant 的 DEGRADED = DLL 部分活着, 仍能响应; THSDK 的 DEGRADED =
    session 临时失效, 不该被路由选上。直接构造 DEGRADED 状态验证契约。
    """
    client = _client()
    # 模拟 _invoke_with_reauth 期间的 DEGRADED 状态
    client._status = UpstreamStatus.DEGRADED
    assert client.is_available is False
    # HEALTHY 才是可用
    client._status = UpstreamStatus.HEALTHY
    assert client.is_available is True
    # UNAVAILABLE 也不可用
    client._status = UpstreamStatus.UNAVAILABLE
    assert client.is_available is False


async def test_password_is_stripped(
    monkeypatch: pytest.MonkeyPatch, fake_mod: types.ModuleType
):
    """THS_PASSWORD 前后空白会被忽略，与 THS_USERNAME 的 .strip() 行为一致。

    否则用户在 .env 里多打了空格，partial-credentials 检查不触发（用户名
    也非空），thsdk 会用带空格的密码登录失败，被错分类为 auth_failed 而非
    "凭证不一致"。
    """
    monkeypatch.setenv("THS_USERNAME", "13800000000")
    monkeypatch.setenv("THS_PASSWORD", "  secret  ")
    client = await _started(fake_mod)
    fake_mod.auth.assert_called_once_with("13800000000", "secret")


async def test_start_partial_credentials_fails(
    monkeypatch: pytest.MonkeyPatch, fake_mod: types.ModuleType
):
    monkeypatch.setenv("THS_USERNAME", "only-user")
    monkeypatch.delenv("THS_PASSWORD", raising=False)
    client = _client()
    assert await client.start() is False
    assert not client.is_available
    # 未登录时只读工具返回明确错误，不假装有数据
    result = await client.call_tool("ths_get_price", {"security": "USHA600519"})
    assert result.success is False
    assert result.error_detail["error_type"] == "not_authenticated"


async def test_start_auth_error_marks_unavailable(
    guest_env, fake_mod: types.ModuleType
):
    fake_mod.auth.side_effect = AuthenticationError("bad credentials")
    client = _client()
    assert await client.start() is False
    result = await client.call_tool("ths_auth_status", {})
    assert result.success
    assert result.data["authenticated"] is False


async def test_auth_status_works_without_start(guest_env, fake_mod):
    client = _client()
    result = await client.call_tool("ths_auth_status", {})
    assert result.success
    assert result.data["authenticated"] is False
    # 其他工具必须先登录
    result = await client.call_tool(
        "ths_search_symbols", {"pattern": "600519"}
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "not_authenticated"


# ---------- 只读调用 / 数据转换 ----------

async def test_get_price_dataframe_to_records(guest_env, fake_mod):
    client = await _started(fake_mod)
    result = await client.call_tool(
        "ths_get_price", {"security": "USHA600519", "count": 1}
    )
    assert result.success
    assert isinstance(result.data, list)
    row = result.data[0]
    assert row["close"] == 10.6
    # DatetimeIndex 被 reset 后转 ISO 字符串
    assert str(row.get("index", "")).startswith("2026-09-01")
    kwargs = fake_mod.get_price.call_args.kwargs
    assert kwargs["frequency"] == "daily"
    assert kwargs["fq"] == "pre"


async def test_unknown_tool_errors(guest_env, fake_mod):
    client = await _started(fake_mod)
    result = await client.call_tool("ths_not_a_tool", {})
    assert result.success is False
    assert "Unknown tool" in result.error


async def test_rate_limited_classification(guest_env, fake_mod):
    fake_mod.news.side_effect = APIError("调用过于频繁", code="-32003")
    client = await _started(fake_mod)
    result = await client.call_tool("ths_news", {})
    assert result.success is False
    assert result.error_detail["error_type"] == "rate_limited"
    assert result.error_detail["recoverable"] is True


# ---------- 写门控 / confirm ----------

async def test_write_blocked_when_switch_off(guest_env, fake_mod):
    client = await _started(fake_mod)
    result = await client.call_tool(
        "ths_add_account_watchlist_securities",
        {"securities": ["USHA600519"]},
    )
    assert result.success is False
    assert "未启用" in result.error
    fake_mod.add_account_watchlist_securities.assert_not_called()


async def test_clear_requires_confirm(
    monkeypatch: pytest.MonkeyPatch, guest_env, fake_mod
):
    monkeypatch.setenv("ALLOW_WATCHLIST_WRITE", "true")
    client = await _started(fake_mod)

    for params in ({}, {"confirm": False}):
        result = await client.call_tool("ths_clear_account_watchlist", params)
        assert result.success is False
        assert "confirm=true" in result.error
    fake_mod.clear_account_watchlist.assert_not_called()


async def test_clear_confirmed_executes(
    monkeypatch: pytest.MonkeyPatch, guest_env, fake_mod
):
    monkeypatch.setenv("ALLOW_WATCHLIST_WRITE", "true")
    client = await _started(fake_mod)
    result = await client.call_tool(
        "ths_clear_account_watchlist", {"confirm": True}
    )
    assert result.success
    fake_mod.clear_account_watchlist.assert_called_once_with()


async def test_replace_watchlist_passes_version_and_confirm(
    monkeypatch: pytest.MonkeyPatch, guest_env, fake_mod
):
    monkeypatch.setenv("ALLOW_WATCHLIST_WRITE", "true")
    client = await _started(fake_mod)
    result = await client.call_tool(
        "ths_replace_account_watchlist_securities",
        {"securities": ["USHA600519"], "version": 7, "confirm": True},
    )
    assert result.success
    kwargs = fake_mod.replace_account_watchlist_securities.call_args.kwargs
    assert kwargs == {"version": 7, "securities": ["USHA600519"]}


# ---------- 短代码补全 / 会话失效重登 ----------

async def test_write_auto_completes_short_codes(
    monkeypatch: pytest.MonkeyPatch, guest_env, fake_mod
):
    monkeypatch.setenv("ALLOW_WATCHLIST_WRITE", "true")
    fake_mod.complete_ths_code.return_value = pd.DataFrame(
        {"ths_code": ["USZA300033"], "name": ["同花顺"]}
    )
    client = await _started(fake_mod)
    result = await client.call_tool(
        "ths_add_account_watchlist_securities", {"securities": ["300033"]}
    )
    assert result.success
    fake_mod.complete_ths_code.assert_called_once_with(["300033"])
    kwargs = fake_mod.add_account_watchlist_securities.call_args.kwargs
    assert kwargs["securities"] == ["USZA300033"]


async def test_unresolvable_short_code_is_invalid_param(
    monkeypatch: pytest.MonkeyPatch, guest_env, fake_mod
):
    monkeypatch.setenv("ALLOW_WATCHLIST_WRITE", "true")
    fake_mod.complete_ths_code.return_value = pd.DataFrame(
        {"ths_code": [], "name": []}
    )
    client = await _started(fake_mod)
    result = await client.call_tool(
        "ths_add_account_watchlist_securities", {"securities": ["999999"]}
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "invalid_param"
    fake_mod.add_account_watchlist_securities.assert_not_called()


async def test_session_expiry_triggers_single_reauth_retry(
    guest_env, fake_mod
):
    price_df = pd.DataFrame({"close": [10.6]})
    fake_mod.get_price.side_effect = [
        NotAuthenticatedError("session expired"),
        price_df,
    ]
    client = await _started(fake_mod)
    assert fake_mod.auth.call_count == 1

    result = await client.call_tool(
        "ths_get_price", {"security": "USHA600519"}
    )
    assert result.success
    assert fake_mod.auth.call_count == 2
    assert fake_mod.get_price.call_count == 2
    assert client.status.name == "HEALTHY"


async def test_write_tool_session_expiry_no_retry(
    monkeypatch: pytest.MonkeyPatch, guest_env, fake_mod
):
    """写工具遇 NotAuthenticatedError 不重试 handler，避免服务端重复执行。

    只读工具可安全重试（idempotent read），写工具不行——若 thsdk 在写中途
    session 失效，服务端可能已部分执行，盲目重试会造成重复修改。
    调用方需要重新调用时由用户显式确认。
    """
    monkeypatch.setenv("ALLOW_WATCHLIST_WRITE", "true")
    fake_mod.replace_account_watchlist_securities.side_effect = [
        NotAuthenticatedError("session expired mid-write"),
    ]
    client = await _started(fake_mod)
    assert fake_mod.auth.call_count == 1

    result = await client.call_tool(
        "ths_replace_account_watchlist_securities",
        {"securities": ["USHA600519"], "version": 1, "confirm": True},
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "not_authenticated"

    # 关键：写工具 handler 只被调一次（无重试）
    assert fake_mod.replace_account_watchlist_securities.call_count == 1
    # 后台 best-effort 重登：让后续只读调用能继续（不直接 fail 在 auth-check）
    assert fake_mod.auth.call_count == 2


# ---------- YAML 加载期门控 ----------

def _yaml_specs() -> list[dict]:
    import yaml

    return yaml.safe_load(TOOLS_YAML.read_text(encoding="utf-8"))["tools"]


def test_yaml_shape():
    specs = _yaml_specs()
    assert len(specs) == 27
    writes = [s for s in specs if s.get("requires_env")]
    assert len(writes) == 10
    assert len(specs) - len(writes) == 17
    for spec in writes:
        assert spec["requires_env"] == "ALLOW_WATCHLIST_WRITE"
        assert spec["dangerous"] is True
        assert spec["description"].startswith("[DANGEROUS WRITE]")
        assert spec["annotations"] == {
            "read_only_hint": False,
            "destructive_hint": True,
        }
        assert spec.get("cache_ttl_key") is None
    confirm = {
        s["name"]
        for s in writes
        if any(p.get("name") == "confirm" for p in s.get("params", []))
    }
    assert confirm == {
        "ths_replace_account_watchlist_securities",
        "ths_clear_account_watchlist",
        "ths_delete_account_watchlist_group",
        "ths_replace_account_watchlist_group_securities",
    }


def test_yaml_write_tools_all_in_WRITE_TOOLS():
    """YAML → 代码方向：每个 YAML 写工具必须出现在 WRITE_TOOLS。

    WRITE_TOOLS 同时承担两个职责：
    1. 运行期双门控（防御性，与 filter_specs_by_env 加载期门控互为冗余）
    2. confirm 二次确认（critical 写操作不可撤销必须 confirm=true）

    漏注册会让该工具的 confirm 校验失效——若本应是 critical 的工具被绕过，
    在 ALLOW_WATCHLIST_WRITE=true 时可直接执行，没有二次确认。
    """
    from src.api.thsdk_client import WRITE_TOOLS

    writes = [s for s in _yaml_specs() if s.get("requires_env")]
    yaml_write_names = {s["routing"] for s in writes}
    code_write_names = set(WRITE_TOOLS.keys())

    missing = yaml_write_names - code_write_names
    assert not missing, (
        f"YAML 写工具未在 WRITE_TOOLS 中注册：{missing}. "
        f"漏注册会导致 confirm 二次确认门控失效。"
    )


def test_WRITE_TOOLS_no_dead_entries():
    """代码 → YAML 方向：WRITE_TOOLS 里的条目必须在 YAML 仍有对应 spec。

    防 WRITE_TOOLS 留旧条目（YAML 已删但字典未同步），造成死代码。
    """
    from src.api.thsdk_client import WRITE_TOOLS

    writes = [s for s in _yaml_specs() if s.get("requires_env")]
    yaml_write_names = {s["routing"] for s in writes}
    code_write_names = set(WRITE_TOOLS.keys())

    dead = code_write_names - yaml_write_names
    assert not dead, (
        f"WRITE_TOOLS 死条目（YAML 已无对应 spec）：{dead}. "
        f"删除 YAML 写工具时必须同步清理 WRITE_TOOLS。"
    )


def test_filter_specs_by_env_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ALLOW_WATCHLIST_WRITE", raising=False)
    specs = filter_specs_by_env(_yaml_specs())
    assert len(specs) == 17
    assert all(not s.get("dangerous") for s in specs)


def test_filter_specs_by_env_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOW_WATCHLIST_WRITE", "true")
    specs = filter_specs_by_env(_yaml_specs())
    assert len(specs) == 27


def test_load_all_tools_gates_specs_and_mapping_off(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("ALLOW_WATCHLIST_WRITE", raising=False)
    cfg: dict = {"tools": []}
    specs = load_all_tools(REPO_ROOT / "config" / "upstreams.yaml", cfg)
    names = {s["name"] for s in specs}
    assert "ths_auth_status" in names
    assert "ths_clear_account_watchlist" not in names
    assert "ths_clear_account_watchlist" not in cfg["upstream_tool_mapping"]


def test_load_all_tools_gates_specs_and_mapping_on(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ALLOW_WATCHLIST_WRITE", "true")
    cfg: dict = {"tools": []}
    specs = load_all_tools(REPO_ROOT / "config" / "upstreams.yaml", cfg)
    names = {s["name"] for s in specs}
    assert "ths_clear_account_watchlist" in names
    assert cfg["upstream_tool_mapping"]["ths_clear_account_watchlist"] == {
        "thsdk": "ths_clear_account_watchlist"
    }


# ---------- registry annotations 透传 ----------

def test_registry_passes_annotations_to_fastmcp():
    from src.core.registry import register_tools_from_config

    captured: list[dict] = []

    class _FakeMCP:
        def tool(self, **kwargs):
            def decorator(fn):
                captured.append(kwargs)
                return fn

            return decorator

    class _FakeServer:
        mcp = _FakeMCP()

        async def _execute_cached(self, *args, **kwargs):
            return {}

    spec = {
        "name": "ths_clear_account_watchlist",
        "routing": "ths_clear_account_watchlist",
        "dangerous": True,
        "annotations": {"read_only_hint": False, "destructive_hint": True},
        "params": [],
    }
    register_tools_from_config(_FakeServer(), [spec])
    assert captured == [
        {"annotations": {"read_only_hint": False, "destructive_hint": True}}
    ]
