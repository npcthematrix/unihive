"""把 SQLite 点路径覆盖合并进 baseline config dict (纯函数, 无 IO)。

设计见 docs/design/2026-09-18-sqlite-config-overlay.md §3/§4:

- YAML (upstreams.yaml + config.yaml) 是出厂基线;
- config_overlay 表的每行是一条点路径叶子值, 例如
  ``upstreams.fuyao.enabled = false`` 或
  ``fuyao_cache.tools.get_x = 60``;
- 合并按字典深度递归: dict+dict 逐键合并, 其余类型 (标量/list)
  overlay 直接替换。合并结果就是下游 router/registry 消费的同一份 dict,
  它们零改动。

本模块不碰 DB / 文件 / 环境变量, pytest 可直接测; 行内 revision 等
存储层概念不进函数签名 (rows 只需有 ``.path`` / ``.value``)。
"""
from __future__ import annotations

import copy
from typing import Any, Iterable, Protocol


class _OverlayRowLike(Protocol):
    path: str
    value: Any


def overlay_tree(rows: Iterable[_OverlayRowLike]) -> dict[str, Any]:
    """把点路径行展开成嵌套 dict。

    同一路径出现多次时后写覆盖先写 (调用方应保证 rows 已按需要排序,
    本函数按输入顺序处理)。两行在路径上打架 (一行的叶子是另一行的
    中间段) 抛 ValueError —— 这是存储层不该出现的脏数据。
    """
    tree: dict[str, Any] = {}
    for r in rows:
        segments = r.path.split(".")
        node = tree
        for segment in segments[:-1]:
            existing = node.get(segment)
            if existing is None:
                child: dict[str, Any] = {}
                node[segment] = child
                node = child
            elif isinstance(existing, dict):
                node = existing
            else:
                raise ValueError(
                    f"Config overlay path conflict: {r.path!r} descends "
                    f"through non-dict segment {segment!r}"
                )
        leaf = segments[-1]
        if isinstance(node.get(leaf), dict):
            raise ValueError(
                f"Config overlay path conflict: {r.path!r} replaces a "
                "nested overlay object with a leaf value"
            )
        node[leaf] = r.value
    return tree


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any], *, path: str) -> None:
    """把 overlay 就地深合并进 base; dict+dict 递归, 其余 overlay 替换。"""
    for key, over_value in overlay.items():
        if key not in base:
            base[key] = over_value
            continue
        base_value = base[key]
        if isinstance(base_value, dict) and isinstance(over_value, dict):
            _deep_merge(base_value, over_value, path=f"{path}.{key}")
            continue
        if (
            isinstance(base_value, dict) != isinstance(over_value, dict)
            and isinstance(over_value, dict)
        ):
            # overlay 想下钻, baseline 却是标量/list —— 结构性冲突,
            # 静默替换会把整段基线配置抹掉, 必须显式失败。
            raise ValueError(
                f"Config overlay conflict at {path}.{key}: baseline is "
                f"{type(base_value).__name__}, overlay descends with a dict"
            )
        base[key] = over_value


def apply_config_overlays(
    baseline: dict[str, Any],
    rows: Iterable[_OverlayRowLike],
) -> dict[str, Any]:
    """返回合并 overlay 后的新 dict (深拷贝基线, 不 mutate 入参)。"""
    rows_list = sorted(rows, key=lambda r: r.path)
    tree = overlay_tree(rows_list)
    merged = copy.deepcopy(baseline)
    _deep_merge(merged, tree, path="")
    return merged
