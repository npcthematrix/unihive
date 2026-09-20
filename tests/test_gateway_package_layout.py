"""gateway_server god-module 拆包 (2026-09-19) 的结构防回归测试。

约束:
1. 拆包前 GatewayServer 的全部公开/内部方法仍在组装类上 (机械搬迁不丢方法);
2. 9 个历史 facade 导入符号 (CLI/helpers/版本常量) 路径不变;
3. 各 mixin 方法名两两不相交 (MRO 组装无意外覆盖);
4. 单文件软限: facade ``__init__.py`` ≤450 行, 其余子模块 ≤800 行;
5. 关键 monkeypatch binding: 子模块内 register_tool / Cache 必须是模块限定
   调用 (registry.register_tool / cache_mod.Cache), facade 直改 binding 不影响它们。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import src.unihive.gateway_server as gs_mod
from src.unihive.gateway_server import GatewayServer

PKG_DIR = Path(gs_mod.__file__).parent

# ---------------------------------------------------------------------------
# 1. 方法仍在组装类上 (拆包前方法清单, 防搬迁遗漏)
# ---------------------------------------------------------------------------

EXPECTED_METHODS = [
    # facade 保留
    "__init__",
    "_apply_gateway_tuning",
    "_construct_upstream_client",
    # lifecycle
    "initialize",
    "_load_db_config",
    "_do_initialize",
    "start",
    "stop",
    "_stop_one_upstream",
    "_cache_cleanup_loop",
    # http
    "serve_http",
    # upstreams (late start)
    "_start_upstream_client",
    "_spawn_late_start",
    "_late_start_once",
    "_cancel_late_starts",
    # hot toggle
    "set_upstream_enabled",
    "_publishable_specs",
    "_toggle_tool_diff_locked",
    "_publish_tools_locked",
    "_unpublish_tools_locked",
    "_raise_if_toggle_errors",
    "_attach_upstream_client_locked",
    "_detach_upstream_client_locked",
    "_enable_upstream_locked",
    "_disable_upstream_locked",
    "_reconcile_upstream_locked",
    # config edits
    "apply_tool_edit",
    "_build_tool_edit_candidate",
    "_rollback_tool_edit_locked",
    "apply_upstream_edit",
    "_rollback_upstream_edit_locked",
    "_canonical_config",
    # config upgrade
    "_prepare_config_upgrade",
    "_upgrade_issue_json",
    "_upgrade_tool_impact",
    "_upgrade_upstream_impact",
    "preview_config_upgrade",
    "apply_config_upgrade",
    "_rollback_config_upgrade_locked",
    # openapi import
    "_prepare_openapi_import",
    "preview_openapi_import",
    "apply_openapi_import",
    # cached execution
    "_execute_cached",
    # mcp tools
    "_load_all_tools",
    "_uses_db_source",
    "_create_and_register_gateway_mcp",
    "_register_tools",
    "_register_status_tools",
    "_expected_upstream_names",
    "_build_upstream_summary",
    "aggregate_health",
    "_find_omni_client",
]


def test_all_original_methods_present_on_assembled_class():
    missing = [m for m in EXPECTED_METHODS if not callable(getattr(GatewayServer, m, None))]
    assert not missing, f"methods lost during package split: {missing}"


def test_mixin_method_sets_are_disjoint():
    """MRO 中各 mixin 的自有方法不得重名 (跨簇调用全经 self, 靠不重名保证无覆盖)。"""
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for klass in GatewayServer.__mro__:
        if klass is object or klass is GatewayServer:
            continue
        if not klass.__module__.startswith("src.unihive.gateway_server."):
            continue
        for name, attr in klass.__dict__.items():
            if not callable(attr) or name in ("__init__",):
                continue
            if name in seen:
                collisions.append(f"{name}: {seen[name]} vs {klass.__name__}")
            else:
                seen[name] = klass.__name__
    assert not collisions, f"mixin method overlaps: {collisions}"


def test_defining_modules_are_submodules():
    """抽离的方法必须定义在包内子模块, 没残留在 facade (inspect 自动跟随)。"""
    expected_submodule = {
        "serve_http": "http",
        "start": "lifecycle",
        "stop": "lifecycle",
        "_execute_cached": "cached_calls",
        "aggregate_health": "mcp_tools",
        "apply_config_upgrade": "config_upgrade",
        "apply_tool_edit": "config_edits",
        "apply_openapi_import": "openapi_import",
        "set_upstream_enabled": "hot_toggle",
        "_spawn_late_start": "upstreams",
    }
    for method, sub in expected_submodule.items():
        mod = inspect.getmodule(getattr(GatewayServer, method))
        assert mod is not None and mod.__name__.endswith(f".{sub}"), (
            f"{method} should be defined in gateway_server.{sub}, got "
            f"{getattr(mod, '__name__', None)}"
        )
    # facade 必须保留的
    for method in ("__init__", "_construct_upstream_client"):
        mod = inspect.getmodule(getattr(GatewayServer, method))
        assert mod is gs_mod, f"{method} must stay in facade module"


# ---------------------------------------------------------------------------
# 2. 历史 facade 导入路径不变
# ---------------------------------------------------------------------------

def test_historical_facade_symbols_importable():
    from src.unihive.gateway_server import (  # noqa: F401
        GATEWAY_VERSION,
        RESERVED_TOOL_NAMES,
        GatewayServer,
        _build_arg_parser,
        _console_auth_config_from_env,
        _get_console_auth_config,
        _parse_mootdx2_servers,
        _sanitize_sync_message,
        async_main,
        main,
    )


def test_compatibility_attribute_shims_exist():
    """审计测试直读 gs_mod.socket/uvicorn, 测试 setup 直调 gs_mod.register_tool。"""
    assert gs_mod.socket
    assert gs_mod.uvicorn
    assert callable(gs_mod.register_tool)


# ---------------------------------------------------------------------------
# 3. 文件规模软限 (防重新滚成 god module)
# ---------------------------------------------------------------------------

EXPECTED_MODULES = {
    "__init__.py",
    "__main__.py",
    "_helpers.py",
    "cached_calls.py",
    "config_edits.py",
    "config_upgrade.py",
    "hot_toggle.py",
    "http.py",
    "lifecycle.py",
    "mcp_tools.py",
    "openapi_import.py",
    "upstreams.py",
}


def test_package_files_present():
    assert {p.name for p in PKG_DIR.glob("*.py")} == EXPECTED_MODULES


def test_file_size_limits():
    oversized = []
    for path in PKG_DIR.glob("*.py"):
        limit = 450 if path.name == "__init__.py" else 800
        n_lines = len(path.read_text(encoding="utf-8").splitlines())
        if n_lines > limit:
            oversized.append(f"{path.name}: {n_lines} > {limit}")
    assert not oversized, "package files exceed size limits: " + ", ".join(oversized)


# ---------------------------------------------------------------------------
# 4. monkeypatch binding 契约 (facade patch 不到子模块 binding)
# ---------------------------------------------------------------------------

def test_submodules_use_module_qualified_registry_call():
    """含工具摘挂的子模块必须写 registry.register_tool, 不能 from-import 裸调。"""
    for name in ("hot_toggle.py", "config_edits.py", "config_upgrade.py"):
        text = (PKG_DIR / name).read_text(encoding="utf-8")
        assert "registry.register_tool(" in text, name
        # 裸 from ..core.registry import register_tool 会让 facade patch 分叉
        assert "import register_tool" not in text, name


def test_lifecycle_uses_module_qualified_cache_call():
    text = (PKG_DIR / "lifecycle.py").read_text(encoding="utf-8")
    assert "cache_mod.Cache(" in text
