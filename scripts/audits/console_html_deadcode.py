"""Audit console.html for dead code: unused CSS classes, unused JS functions.

Run: python scripts/audits/console_html_deadcode.py
"""
import re
from pathlib import Path

SRC = Path("console.html").read_text(encoding="utf-8")

# === CSS classes ===
style_match = re.search(r"<style>(.*?)</style>", SRC, re.DOTALL)
css = style_match.group(1) if style_match else ""
# Get top-level class names (selectors like .foo, .foo.bar, .foo .bar)
css_rules = re.findall(r"\.([A-Za-z_][\w\-]*)", css)
css_classes = set(css_rules)

# === Used in HTML body ===
body = re.search(r"<body>(.*?)</body>", SRC, re.DOTALL)
body_html = body.group(1) if body else ""
used_classes = set(re.findall(r'class="([^"]+)"', body_html))
used_flat = set()
for c in used_classes:
    for x in c.split():
        used_flat.add(x)

# === Used in JS (string literals) ===
script_match = re.search(r"<script>(.*?)</script>", SRC, re.DOTALL)
js = script_match.group(1) if script_match else ""

js_classes = set()
# classList.add('foo'), etc.
for m in re.finditer(r"class(?:Name|List)?\.(?:add|remove|toggle|contains)\s*\(\s*['\"]([\w\-]+)['\"]", js):
    js_classes.add(m.group(1))
# className = 'foo bar'
for m in re.finditer(r"className\s*=\s*['\"]([\w\- ]+)['\"]", js):
    for c in m.group(1).split():
        js_classes.add(c)
# querySelectorAll('.foo'), getElementsByClassName('foo')
for m in re.finditer(r"['\"]\.([A-Za-z_][\w\-]+)['\"]", js):
    js_classes.add(m.group(1))
# template literal classes like class="foo bar"
for m in re.finditer(r'class="([^"]+)"', js):
    for c in m.group(1).split():
        js_classes.add(c)
# Inside backtick template literals
for m in re.finditer(r"`([^`]*class[=:][^`]*)`", js):
    for cls in re.findall(r"[\w\-]+", m.group(1)):
        if cls[0].isalpha():
            js_classes.add(cls)
# String concat 'foo-' + var patterns - extract static prefixes
for m in re.finditer(r"['\"]([a-z\-]+)['\"]\s*\+\s*\w+", js):
    js_classes.add(m.group(1))
# Static 'foo-bar' strings anywhere
for m in re.finditer(r"['\"]([a-z][a-z\-]{3,})['\"]", js):
    js_classes.add(m.group(1))

total_used = used_flat | js_classes
unused_css = sorted(css_classes - total_used)

print(f"CSS classes defined: {len(css_classes)}")
print(f"Used in HTML body: {len(used_flat)}")
print(f"Used in JS: {len(js_classes)}")
print(f"CSS classes defined but NOT used anywhere: {len(unused_css)}")
print()
for c in unused_css:
    print(f"  .{c}")

# === Unused JS functions ===
print()
print("=" * 50)
funcs = re.findall(r"^\s*(?:async\s+)?function\s+(\w+)\s*\(", SRC, re.MULTILINE)
unused_funcs = []
for name in set(funcs):
    count = len(re.findall(r"\b" + re.escape(name) + r"\b", SRC))
    if count <= 1:
        unused_funcs.append(name)
print(f"Possibly unused JS functions (defined, no other references):")
for u in sorted(unused_funcs):
    print(f"  - {u}")