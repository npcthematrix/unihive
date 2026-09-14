"""Regression + coverage tests for the Vue console surface.

2026-09-14 console rewrite: console.html is now a thin SPA shell (Vite-built
index.html inlined) and console.js / console.css were archived. The console
UI lives entirely in web/src/views/*.vue; these tests assert that surface is
intact so a Vue view getting accidentally removed during refactor doesn't
silently degrade the console.

Predecessor: test_console_html_unused.py (2026-09-08) which guarded dead-code
removal in the old console.html + console.js. That file is replaced by this
one now that the dead-code surface no longer exists.
"""
from __future__ import annotations

from pathlib import Path

CONSOLE_HTML = Path("console.html").read_text(encoding="utf-8")
WEB_VIEWS = Path("web/src/views")
WEB_API = Path("web/src/api")
WEB_ROUTER = Path("web/src/router/index.ts")

# 6 个路由视图 + LoginView (login 单独路径, 不计入主路由 children)。
EXPECTED_VIEWS = [
    "StatusView.vue",
    "InterfacesView.vue",
    "ConfigView.vue",
    "OmniView.vue",
    "GuidesView.vue",
    "LoginView.vue",
]


class TestVueViewSurface:
    def test_all_main_views_present(self):
        missing = [v for v in EXPECTED_VIEWS if not (WEB_VIEWS / v).is_file()]
        assert missing == [], (
            f"Vue view files missing: {missing}. "
            f"If you renamed/removed a view, also update {WEB_ROUTER}."
        )

    def test_views_subdirectories_intact(self):
        """子组件目录: ConfigView 5 panel + OmniView 3 panel + GuidesView 2 panel。
        任何子组件丢失都会让对应主视图渲染残缺, 这里给一个最低数量下限。
        """
        for subdir, min_count in [
            ("config", 5),
            ("omni", 3),
            ("guides", 2),
        ]:
            files = list((WEB_VIEWS / subdir).glob("*.vue"))
            assert len(files) >= min_count, (
                f"web/src/views/{subdir}/ expected >= {min_count} .vue files, "
                f"got {len(files)}: {[f.name for f in files]}"
            )


class TestConsoleHtmlIsThinShell:
    """console.html 是 Vite 产物嵌入的薄壳, 不应再含任何内联业务代码。"""

    def test_console_html_loads_vite_bundle(self):
        assert "/static/console/assets/index-" in CONSOLE_HTML, (
            "console.html missing reference to the Vite-built bundle "
            "(/static/console/assets/index-*.{js,css}). "
            "Run `cd web && npm run build` and let finalize-build.mjs "
            "regenerate console.html."
        )

    def test_console_html_has_no_inline_panels(self):
        """旧的 id=status / id=interfaces / id=config / id=onboarding /
        id=omni / id=ai-onboarding 全部下沉到 Vue 路由。
        新 console.html 不应再含这些旧 panel 锚点 (否则说明旧版未拆完)。
        """
        for legacy_id in (
            'id="status"',
            'id="interfaces"',
            'id="config"',
            'id="onboarding"',
            'id="ai-onboarding"',
            'id="omni"',
        ):
            assert legacy_id not in CONSOLE_HTML, (
                f"console.html still contains legacy panel {legacy_id!r}. "
                f"This Vue migration replaces inline panels with hash routes."
            )

    def test_console_html_has_no_inline_script_business_logic(self):
        """旧 console.html 不应有 <script> 块含旧 JS 业务符号 (loadStatus,
        wireOnboarding, jumpToPanel 等)。 Vite bundle 走 <script type=module>
        crossorigin, 不会被这里抓到 (regex 看 <script> 后紧接的源代码)。
        """
        bad_markers = [
            "function loadStatus",
            "function loadInterfaces",
            "function wireOnboarding",
            "function jumpToPanel",
            "function loadOnboarding",
        ]
        leaked = [m for m in bad_markers if m in CONSOLE_HTML]
        assert leaked == [], (
            f"console.html still contains inline JS business logic: {leaked}. "
            f"Migrate the corresponding logic into web/src/views/*.vue."
        )


class TestOldConsoleAssetsArchived:
    """旧 console.js / console.css 必须已归档而非残留 (防止新版本误以为还在用)。"""

    def test_old_console_js_archived(self):
        archived = Path("archive/console-pre-vue-2026-09-14/console.js")
        assert archived.is_file(), (
            f"Old console.js not found at {archived}. The 2026-09-14 Vue "
            f"migration should archive it before deleting static/console/console.js."
        )
        live = Path("static/console/console.js")
        assert not live.is_file(), (
            f"{live} still exists after the Vue migration; should be deleted."
        )

    def test_old_console_css_archived(self):
        archived = Path("archive/console-pre-vue-2026-09-14/console.css")
        assert archived.is_file(), (
            f"Old console.css not found at {archived}. The 2026-09-14 Vue "
            f"migration should archive it before deleting static/console/console.css."
        )
        live = Path("static/console/console.css")
        assert not live.is_file(), (
            f"{live} still exists after the Vue migration; should be deleted."
        )


class TestRouterWiresAllViews:
    def test_router_has_all_6_routes(self):
        router_src = WEB_ROUTER.read_text(encoding="utf-8")
        for view in EXPECTED_VIEWS:
            assert view in router_src, (
                f"{view} not referenced in {WEB_ROUTER}. "
                f"If you removed a view, also delete the route."
            )