"""Regression tests for console.html + console.js dead-code removal.

Ensures the cleanup pass on 2026-09-08 stays clean: previously unused
CSS classes and JS functions must stay removed. If someone re-introduces
dead code (e.g. pastes a function from history, copy-pastes an unused
class from a template), this test catches it.

Note: as of 2026-09-14, console.html is a thin SPA shell (HTML scaffold
+ CSS variables) and console.js holds the runtime JS. The function-name
checks below target console.js, where the definitions actually live.
"""
from __future__ import annotations

import re
from pathlib import Path

CONSOLE_HTML = Path("console.html").read_text(encoding="utf-8")
CONSOLE_JS = Path("static/console/console.js").read_text(encoding="utf-8")


# Classes identified in the 2026-09-08 audit. Each was defined in
# <style> but never referenced from the body or JS.
DEAD_CSS_CLASSES = [
    "badge-optional",
    "badge-recommend",
    "cache-stats",
    "cfg-banner-icon",
    "error-msg",
    "hitrate-bar-fill",
    "inv-source-toggles",
    "json-block",
    "stat-item",
    "stat-label",
]

# JS functions identified in the audit. Defined but never called.
DEAD_JS_FUNCTIONS = [
    "formatLatency",
    "formatPercent",
    "syntaxHighlight",
]


def _class_usage_count(cls: str) -> int:
    """Count occurrences of `.cls` (CSS rule) or the literal token `cls`
    anywhere in console.html. Used to detect 'class only defined, never
    used' patterns.
    """
    # Word-boundary match on the class token — catches both .cls (CSS
    # selector) and "cls" (HTML class="..." or JS string).
    return len(re.findall(r"\b" + re.escape(cls) + r"\b", CONSOLE_HTML))


class TestDeadCssClassesRemoved:
    def test_dead_css_classes_not_present(self):
        """Each class below should have ZERO occurrences in console.html.
        If the regex matches, the dead class has been re-introduced.
        """
        reanimated = [c for c in DEAD_CSS_CLASSES if _class_usage_count(c) > 0]
        assert reanimated == [], (
            f"Dead CSS classes re-introduced in console.html: {reanimated}. "
            f"These were removed in the 2026-09-08 cleanup."
        )


class TestDeadJsFunctionsRemoved:
    def test_dead_js_functions_not_present(self):
        """Each function below should have ZERO occurrences. We grep for
        `function name(` to catch only top-level function definitions —
        local vars named the same wouldn't match this regex.
        """
        reanimated = []
        for name in DEAD_JS_FUNCTIONS:
            pattern = r"\bfunction\s+" + re.escape(name) + r"\s*\("
            if re.search(pattern, CONSOLE_HTML):
                reanimated.append(name)
        assert reanimated == [], (
            f"Dead JS functions re-introduced in console.html: {reanimated}. "
            f"These were removed in the 2026-09-08 cleanup."
        )


class TestConsoleHtmlStillFunctional:
    """Sanity check: after dead-code removal, console.html still parses
    and the live JS functions + used CSS classes still resolve."""

    def test_console_html_loads(self):
        # Trivial: just reading it shouldn't raise.
        assert "UniHive Console" in CONSOLE_HTML
        assert 'id="status"' in CONSOLE_HTML
        assert 'id="interfaces"' in CONSOLE_HTML
        assert 'id="config"' in CONSOLE_HTML
        assert 'id="onboarding"' in CONSOLE_HTML

    def test_active_panels_present(self):
        # Some panels may have additional classes (e.g. "panel active"),
        # so match on prefix.
        panel_count = CONSOLE_HTML.count('class="panel')
        assert panel_count >= 4, (
            f"console.html should have 4 main panels (status, interfaces, "
            f"config, onboarding); found {panel_count}"
        )

    def test_live_functions_still_present(self):
        """Functions that ARE used must still be defined. If removal
        accidentally took out a live function, the test catches it.

        Note: as of 2026-09-14 the JS lives in static/console/console.js
        (extracted from inline <script>); console.html is a thin shell.
        """
        live = [
            "loadStatus",
            "loadInterfaces",
            "loadConfig",
            "loadOnboarding",
            "renderAll",
            "renderUpstreamsTable",
            "renderCache",
            "renderCfgUpstreams",
            "renderCfgMapping",
            "renderCfgTools",
            "renderCfgCache",
            "escapeHtml",
            "fetchJSON",
            "formatTime",
            "humanizeDuration",
        ]
        missing = [
            f for f in live
            if not re.search(r"\bfunction\s+" + re.escape(f) + r"\s*\(", CONSOLE_JS)
        ]
        assert missing == [], (
            f"Live JS function definitions missing from console.js: {missing}. "
            f"The dead-code cleanup should not have removed these."
        )

    def test_console_html_loads_console_js(self):
        """console.html must reference /static/console/console.js so the
        runtime functions are actually executed in the browser."""
        assert "/static/console/console.js" in CONSOLE_HTML, (
            "console.html missing <script src=/static/console/console.js> — "
            "the SPA shell cannot execute any of the live functions."
        )