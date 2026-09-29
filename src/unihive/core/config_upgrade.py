"""出厂模板升级的字段级三方合并纯函数引擎 (P3-5)。

三方:

- **old**: 本机 seed 时写入 ``config_template`` 的出厂快照 (合并基准 base);
- **new**: 当前代码版本重新构建的出厂 bundle (YAML 模板, 升级后);
- **cur**: ``config.db`` 实体表当前内容 (含用户在控制台的修改)。

合并规则 (实体按 name 对齐, section 按段名对齐):

1. 任一字段三方齐全时做经典三方合并:
   - old == cur (用户没动) → 取 new (出厂更新流入);
   - old == new (出厂没动) → 取 cur (用户修改保留);
   - cur == new (双方改成同一值) → 取该值;
   - 双方都改且不同:
     * 三方都是 dict → 按键递归;
     * 其余 (标量 / list / 类型变了) → **冲突, 默认保留 cur**,
       记入 conflicts, 更新中心可对单条路径选择接受 new。
2. 实体仅存在于 new (出厂新增, old/cur 都没有) → 新增 (builtin)。
3. 实体在 old+cur 但 new 已删:
   - cur 与 old 一致 (用户没动) → 删除;
   - 用户改过 → **保留 + delete_modify 冲突** (可 accept_new 改为接受删除)。
4. 仅存在于 cur (用户/P4 导入, old/new 都没有) → 原样保留, 出厂合并不碰。
5. old+new 有但 cur 缺失 (用户侧删除, 当前 UI 无此路径, 防御):
   new==old 维持删除; new 改过 → modify_delete 冲突, 维持删除
   (可 accept_new 改为恢复出厂值)。
6. dict 递归时, 出厂新增键 (old/cur 均无) 流入; 用户新增键保留;
   出厂删除且用户未改的键移除, 用户改过的键走 delete_modify 冲突。

list 整体视为原子值 (params / chain 等), 不做按元素合并 —— 控制台对
params 的编辑本就是整体替换, 原子冲突语义最清晰。

引擎不做 IO、不读环境变量、不 mutate 输入; 输出 bundle 与
``NormalizedTemplate.to_json()`` 同形, 可直接喂 seed/upsert (#48)。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable

#: 缺失方哨兵 (None 是合法配置值, 不能当 missing)。
MISSING = object()

CONFLICT_MODIFY = "modify_modify"
CONFLICT_DELETE_MODIFY = "delete_modify"
CONFLICT_MODIFY_DELETE = "modify_delete"

RESOLUTION_KEEP_CUR = "keep_cur"
RESOLUTION_ACCEPT_NEW = "accept_new"

ACTION_ADDED = "added"
ACTION_REMOVED = "removed"
ACTION_UPDATED = "updated"
ACTION_UNCHANGED = "unchanged"
ACTION_KEPT_USER = "kept_user"

KIND_SECTION = "section"
KIND_UPSTREAM = "upstream"
KIND_TOOL = "tool"

ORIGIN_BUILTIN = "builtin"
ORIGIN_USER = "user"

#: 工具行参与三方合并的抽取列 + JSON 列 (name 是对齐键, source_file 单列处理)。
_TOOL_SCALAR_FIELDS = (
    "routing",
    "description",
    "cache_ttl_key",
    "requires_env",
    "publish_mode",
)
_TOOL_NODE_FIELDS = ("mapping", "spec")

_UPSTREAM_SCALAR_FIELDS = ("type", "enabled")
_UPSTREAM_NODE_FIELDS = ("config",)


@dataclass(frozen=True)
class FieldConflict:
    """一条字段级冲突。resolution 为实际采用的解法; 缺省 keep_cur。"""

    kind: str
    name: str
    path: str
    conflict: str
    resolution: str
    old: Any = None
    cur: Any = None
    new: Any = None

    @property
    def key(self) -> str:
        """更新中心逐条改判 (accept_new) 时使用的稳定标识。"""
        return f"{self.kind}:{self.name}:{self.path}"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "path": self.path,
            "conflict": self.conflict,
            "resolution": self.resolution,
            "old": None if self.old is MISSING else self.old,
            "cur": None if self.cur is MISSING else self.cur,
            "new": None if self.new is MISSING else self.new,
        }


@dataclass(frozen=True)
class EntityChange:
    """单个实体/段在本次合并中的处置。"""

    kind: str
    name: str
    action: str
    fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "action": self.action,
            "fields": list(self.fields),
            "conflicts": list(self.conflicts),
        }


@dataclass
class UpgradeMergeResult:
    """合并结果: 新 bundle + 逐实体变更台账 + 展平的冲突清单。"""

    bundle: dict[str, Any]
    sections: list[EntityChange] = field(default_factory=list)
    upstreams: list[EntityChange] = field(default_factory=list)
    tools: list[EntityChange] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)

    @property
    def added(self) -> int:
        return self._count(ACTION_ADDED)

    @property
    def removed(self) -> int:
        return self._count(ACTION_REMOVED)

    @property
    def updated(self) -> int:
        return self._count(ACTION_UPDATED)

    @property
    def kept_user(self) -> int:
        return self._count(ACTION_KEPT_USER)

    def _count(self, action: str) -> int:
        return sum(
            1
            for changes in (self.sections, self.upstreams, self.tools)
            for c in changes
            if c.action == action
        )

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def summary(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "removed": self.removed,
            "updated": self.updated,
            "kept_user": self.kept_user,
            "conflicts": len(self.conflicts),
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "bundle": self.bundle,
            "summary": self.summary(),
            "sections": [c.to_json() for c in self.sections],
            "upstreams": [c.to_json() for c in self.upstreams],
            "tools": [c.to_json() for c in self.tools],
            "conflicts": [c.to_json() for c in self.conflicts],
        }


@dataclass
class _NodeOutcome:
    """值级合并的中间结果。value 为 MISSING 表示该键应删除。"""

    value: Any
    absorbed: list[str] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)


# ----------------------------------------------------------------------
# 底层值级三方合并
# ----------------------------------------------------------------------

def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _factory_changed_paths(old: Any, new: Any, path: str) -> list[str]:
    """old == cur 分支: 收集 old→new 实际变化的叶子路径 (供台账展示)。"""
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        paths: list[str] = []
        for key in sorted(set(old) | set(new), key=str):
            child = _join(path, str(key))
            if key not in old or key not in new:
                paths.append(child)
            else:
                paths.extend(_factory_changed_paths(old[key], new[key], child))
        return paths
    # 根路径 "" 也作为 "段根被出厂改动" 的标记保留 (显示层再过滤)。
    return [path]


def _dc(value: Any) -> Any:
    """deepcopy 但 ``MISSING`` 哨兵保持同一性。

    ``copy.deepcopy(MISSING)`` 会产生**新的** ``object()`` 实例, 破坏
    ``FieldConflict.to_json`` 的 ``is MISSING`` 判定, 让哨兵泄漏进 JSON
    序列化 (dry-run/apply HTTP 500: "Object of type object is not
    JSON serializable")。构造 FieldConflict 时一律经本函数。
    """
    return value if value is MISSING else copy.deepcopy(value)


def merge_value(
    kind: str,
    entity: str,
    path: str,
    old: Any,
    cur: Any,
    new: Any,
    resolutions: dict[str, str] | None = None,
) -> _NodeOutcome:
    """合并单个 JSON 节点。缺失方传 :data:`MISSING`。

    返回 value 为 :data:`MISSING` 表示该键应从结果中删除。
    resolutions 键为 ``"<kind>:<entity>:<path>"``, ``accept_new`` 表示
    冲突时改用出厂值; 其余一律 keep_cur。
    """
    resolutions = resolutions or {}
    conflict_key = f"{kind}:{entity}:{path}"

    # 出厂新增 (old/cur 均缺) → 无条件流入。
    if old is MISSING and cur is MISSING and new is not MISSING:
        return _NodeOutcome(copy.deepcopy(new), absorbed=[path] if path else [])
    # old/new 都没有, 只有 cur 有 → 用户新增, 原样保留。
    if old is MISSING and new is MISSING:
        return _NodeOutcome(copy.deepcopy(cur))
    # cur 与 new 均缺: 双方都删了 (或本来就没有), 静默删除, 不算出厂变更。
    if cur is MISSING and new is MISSING:
        return _NodeOutcome(MISSING)

    # cur 缺失, new 还在 (用户侧删除 — 当前无此 UI 路径, 防御性处理)。
    if cur is MISSING:
        if new == old:
            return _NodeOutcome(MISSING)
        if resolutions.get(conflict_key) == RESOLUTION_ACCEPT_NEW:
            return _NodeOutcome(copy.deepcopy(new), absorbed=[path] if path else [])
        return _NodeOutcome(
            MISSING,
            conflicts=[
                FieldConflict(
                    kind, entity, path, CONFLICT_MODIFY_DELETE, RESOLUTION_KEEP_CUR,
                    old=_dc(old), cur=MISSING, new=_dc(new),
                )
            ],
        )

    # 出厂删除 (new 缺), cur 在。
    if new is MISSING:
        if cur == old:
            return _NodeOutcome(MISSING, absorbed=[path] if path else [])
        if resolutions.get(conflict_key) == RESOLUTION_ACCEPT_NEW:
            return _NodeOutcome(MISSING, absorbed=[path] if path else [])
        return _NodeOutcome(
            copy.deepcopy(cur),
            conflicts=[
                FieldConflict(
                    kind, entity, path, CONFLICT_DELETE_MODIFY, RESOLUTION_KEEP_CUR,
                    old=_dc(old), cur=_dc(cur), new=MISSING,
                )
            ],
        )

    # 三方齐全。
    if old == cur:
        return _NodeOutcome(
            copy.deepcopy(new), absorbed=_factory_changed_paths(old, new, path)
        )
    if old == new or cur == new:
        return _NodeOutcome(copy.deepcopy(cur))

    # 双方都改且不同: dict 继续按键递归, 其余原子冲突。
    if isinstance(old, dict) and isinstance(cur, dict) and isinstance(new, dict):
        merged: dict[str, Any] = {}
        outcome = _NodeOutcome(merged)
        for key in sorted(set(old) | set(cur) | set(new), key=str):
            sub = merge_value(
                kind, entity, _join(path, str(key)),
                old.get(key, MISSING), cur.get(key, MISSING), new.get(key, MISSING),
                resolutions,
            )
            outcome.absorbed.extend(sub.absorbed)
            outcome.conflicts.extend(sub.conflicts)
            if sub.value is not MISSING:
                merged[key] = sub.value
        return outcome

    if resolutions.get(conflict_key) == RESOLUTION_ACCEPT_NEW:
        return _NodeOutcome(
            copy.deepcopy(new), absorbed=[path] if path else [],
            conflicts=[
                FieldConflict(
                    kind, entity, path, CONFLICT_MODIFY, RESOLUTION_ACCEPT_NEW,
                    old=_dc(old), cur=_dc(cur), new=_dc(new),
                )
            ],
        )
    return _NodeOutcome(
        copy.deepcopy(cur),
        conflicts=[
            FieldConflict(
                kind, entity, path, CONFLICT_MODIFY, RESOLUTION_KEEP_CUR,
                old=_dc(old), cur=_dc(cur), new=_dc(new),
            )
        ],
    )


# ----------------------------------------------------------------------
# 实体级合并
# ----------------------------------------------------------------------

@dataclass
class _EntityMerge:
    """实体合并中间结果: row 为 None 表示删除该实体。"""

    row: dict[str, Any] | None
    action: str
    fields: list[str] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)


def _index(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in items}


def _ordered_names(
    new: dict[str, dict[str, Any]],
    cur: dict[str, dict[str, Any]],
) -> list[str]:
    """结果顺序: 新模板顺序优先, cur 独有实体 (用户/P4) 按 cur 顺序追加。"""
    names = list(new.keys())
    for name in cur:
        if name not in new and name not in names:
            names.append(name)
    return names


def _merge_entity_fields(
    kind: str,
    name: str,
    old_row: dict[str, Any],
    cur_row: dict[str, Any],
    new_row: dict[str, Any],
    scalar_fields: tuple[str, ...],
    node_fields: tuple[str, ...],
    resolutions: dict[str, str],
) -> tuple[dict[str, Any], list[str], list[FieldConflict]]:
    merged: dict[str, Any] = {}
    absorbed: list[str] = []
    conflicts: list[FieldConflict] = []
    for f in scalar_fields + node_fields:
        outcome = merge_value(
            kind, name, f,
            old_row.get(f, MISSING), cur_row.get(f, MISSING), new_row.get(f, MISSING),
            resolutions,
        )
        if outcome.value is not MISSING:
            merged[f] = outcome.value
        absorbed.extend(outcome.absorbed)
        conflicts.extend(outcome.conflicts)
    return merged, absorbed, conflicts


def _entity_conflict(kind: str, name: str, conflict_type: str, resolution: str,
                     old: Any, cur: Any, new: Any) -> FieldConflict:
    return FieldConflict(kind, name, "", conflict_type, resolution, old=old, cur=cur, new=new)


def _factory_tool_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(copy.deepcopy(row.get(f)) for f in (*_TOOL_SCALAR_FIELDS, *_TOOL_NODE_FIELDS))


def _factory_upstream_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(copy.deepcopy(row.get(f)) for f in ("type", "enabled", "config"))


def _sync_tool_spec(merged_tool: dict[str, Any], name: str) -> None:
    """抽取列回写 spec, 与 apply_tool_patch 的列/spec 同步语义保持一致。

    只在列与 spec 真的不一致时落键, 避免给本来省略 routing/description 的
    出厂 spec 注入默认键 (那会破坏 template_normalize ↔ db_assembler 零损耗往返)。
    """
    spec = merged_tool.get("spec")
    if not isinstance(spec, dict):
        spec = {}
        merged_tool["spec"] = spec
    spec["name"] = name

    routing = merged_tool["routing"]
    if spec.get("routing", name) != routing:
        spec["routing"] = routing

    description = merged_tool["description"]
    if spec.get("description", "") != description:
        if description:
            spec["description"] = description
        else:
            spec.pop("description", None)

    for nullable in ("cache_ttl_key", "requires_env"):
        value = merged_tool.get(nullable)
        if value is None:
            # 列值为 null: 仅当 spec 里是**显式非空值**时才摘除;
            # 出厂 spec 可能显式带 null 键, 必须原样保留 (零损耗往返)。
            if spec.get(nullable) is not None:
                spec.pop(nullable, None)
        else:
            spec[nullable] = value


def _merge_tool(
    name: str,
    old_row: dict[str, Any] | None,
    cur_row: dict[str, Any] | None,
    new_row: dict[str, Any] | None,
    resolutions: dict[str, str],
) -> _EntityMerge:
    root_key = f"{KIND_TOOL}:{name}:"
    accept_new = resolutions.get(root_key) == RESOLUTION_ACCEPT_NEW

    # 仅 cur 有 (用户/P4 导入) → 原样保留, 出厂合并不碰。
    if new_row is None and old_row is None and cur_row is not None:
        kept = {k: copy.deepcopy(v) for k, v in cur_row.items()}
        kept["name"] = name
        kept["origin"] = cur_row.get("origin", ORIGIN_USER)
        return _EntityMerge(kept, ACTION_KEPT_USER)

    # 出厂新增 (old/cur 均无)。
    if old_row is None and cur_row is None and new_row is not None:
        added = {k: copy.deepcopy(v) for k, v in new_row.items() if k != "origin"}
        added["name"] = name
        added["origin"] = ORIGIN_BUILTIN
        _sync_tool_spec(added, name)
        return _EntityMerge(added, ACTION_ADDED)

    # new 有而 cur 无: 出厂没改维持删除; 改过则 modify_delete 冲突。
    if cur_row is None and new_row is not None:
        if old_row is not None and _factory_tool_signature(new_row) == _factory_tool_signature(old_row):
            return _EntityMerge(None, ACTION_UNCHANGED)
        if accept_new:
            added = {k: copy.deepcopy(v) for k, v in new_row.items() if k != "origin"}
            added["name"] = name
            added["origin"] = ORIGIN_BUILTIN
            _sync_tool_spec(added, name)
            return _EntityMerge(added, ACTION_UPDATED, fields=[""], conflicts=[
                _entity_conflict(KIND_TOOL, name, CONFLICT_MODIFY_DELETE,
                                 RESOLUTION_ACCEPT_NEW,
                                 copy.deepcopy(old_row), MISSING, copy.deepcopy(new_row)),
            ])
        return _EntityMerge(None, ACTION_UNCHANGED, conflicts=[
            _entity_conflict(KIND_TOOL, name, CONFLICT_MODIFY_DELETE,
                             RESOLUTION_KEEP_CUR,
                             copy.deepcopy(old_row), MISSING, copy.deepcopy(new_row)),
        ])

    # 出厂删除 (new 无, cur 有): 没改过接受删除; 改过保留 + delete_modify。
    if new_row is None and cur_row is not None:
        if old_row is not None and _factory_tool_signature(cur_row) == _factory_tool_signature(old_row):
            return _EntityMerge(None, ACTION_REMOVED)
        if accept_new:
            return _EntityMerge(None, ACTION_REMOVED, fields=[""], conflicts=[
                _entity_conflict(KIND_TOOL, name, CONFLICT_DELETE_MODIFY,
                                 RESOLUTION_ACCEPT_NEW,
                                 copy.deepcopy(old_row), copy.deepcopy(cur_row), MISSING),
            ])
        kept = {k: copy.deepcopy(v) for k, v in cur_row.items()}
        kept["name"] = name
        kept["origin"] = cur_row.get("origin", ORIGIN_USER)
        return _EntityMerge(kept, ACTION_KEPT_USER, conflicts=[
            _entity_conflict(KIND_TOOL, name, CONFLICT_DELETE_MODIFY,
                             RESOLUTION_KEEP_CUR,
                             copy.deepcopy(old_row), copy.deepcopy(cur_row), MISSING),
        ])

    # 三方齐全: 字段级合并。
    assert cur_row is not None and new_row is not None
    merged_fields, absorbed, conflicts = _merge_entity_fields(
        KIND_TOOL, name, old_row or {}, cur_row, new_row,
        _TOOL_SCALAR_FIELDS, _TOOL_NODE_FIELDS, resolutions,
    )
    merged_tool = dict(merged_fields)
    merged_tool["name"] = name
    # source_file 是出厂记账元数据, 永远跟随新模板, 不产生冲突。
    merged_tool["source_file"] = new_row.get("source_file", cur_row.get("source_file", ""))
    _sync_tool_spec(merged_tool, name)
    merged_tool["origin"] = (
        ORIGIN_BUILTIN
        if _factory_tool_signature(merged_tool) == _factory_tool_signature(new_row)
        else ORIGIN_USER
    )
    action = ACTION_UPDATED if absorbed or conflicts else ACTION_UNCHANGED
    return _EntityMerge(merged_tool, action, fields=absorbed, conflicts=conflicts)


def _merge_upstream(
    name: str,
    old_row: dict[str, Any] | None,
    cur_row: dict[str, Any] | None,
    new_row: dict[str, Any] | None,
    resolutions: dict[str, str],
) -> _EntityMerge:
    root_key = f"{KIND_UPSTREAM}:{name}:"
    accept_new = resolutions.get(root_key) == RESOLUTION_ACCEPT_NEW

    if new_row is None and old_row is None and cur_row is not None:
        kept = {k: copy.deepcopy(v) for k, v in cur_row.items()}
        kept["name"] = name
        kept["origin"] = cur_row.get("origin", ORIGIN_USER)
        return _EntityMerge(kept, ACTION_KEPT_USER)

    if old_row is None and cur_row is None and new_row is not None:
        added = {k: copy.deepcopy(v) for k, v in new_row.items() if k != "origin"}
        added["name"] = name
        added["origin"] = ORIGIN_BUILTIN
        return _EntityMerge(added, ACTION_ADDED)

    if cur_row is None and new_row is not None:
        if old_row is not None and _factory_upstream_signature(new_row) == _factory_upstream_signature(old_row):
            return _EntityMerge(None, ACTION_UNCHANGED)
        if accept_new:
            added = {k: copy.deepcopy(v) for k, v in new_row.items() if k != "origin"}
            added["name"] = name
            added["origin"] = ORIGIN_BUILTIN
            return _EntityMerge(added, ACTION_UPDATED, fields=[""], conflicts=[
                _entity_conflict(KIND_UPSTREAM, name, CONFLICT_MODIFY_DELETE,
                                 RESOLUTION_ACCEPT_NEW,
                                 copy.deepcopy(old_row), MISSING, copy.deepcopy(new_row)),
            ])
        return _EntityMerge(None, ACTION_UNCHANGED, conflicts=[
            _entity_conflict(KIND_UPSTREAM, name, CONFLICT_MODIFY_DELETE,
                             RESOLUTION_KEEP_CUR,
                             copy.deepcopy(old_row), MISSING, copy.deepcopy(new_row)),
        ])

    if new_row is None and cur_row is not None:
        if old_row is not None and _factory_upstream_signature(cur_row) == _factory_upstream_signature(old_row):
            return _EntityMerge(None, ACTION_REMOVED)
        if accept_new:
            return _EntityMerge(None, ACTION_REMOVED, fields=[""], conflicts=[
                _entity_conflict(KIND_UPSTREAM, name, CONFLICT_DELETE_MODIFY,
                                 RESOLUTION_ACCEPT_NEW,
                                 copy.deepcopy(old_row), copy.deepcopy(cur_row), MISSING),
            ])
        kept = {k: copy.deepcopy(v) for k, v in cur_row.items()}
        kept["name"] = name
        kept["origin"] = cur_row.get("origin", ORIGIN_USER)
        return _EntityMerge(kept, ACTION_KEPT_USER, conflicts=[
            _entity_conflict(KIND_UPSTREAM, name, CONFLICT_DELETE_MODIFY,
                             RESOLUTION_KEEP_CUR,
                             copy.deepcopy(old_row), copy.deepcopy(cur_row), MISSING),
        ])

    assert cur_row is not None and new_row is not None
    merged_fields, absorbed, conflicts = _merge_entity_fields(
        KIND_UPSTREAM, name, old_row or {}, cur_row, new_row,
        _UPSTREAM_SCALAR_FIELDS, _UPSTREAM_NODE_FIELDS, resolutions,
    )
    merged_up = dict(merged_fields)
    merged_up["name"] = name
    merged_up["origin"] = (
        ORIGIN_BUILTIN
        if _factory_upstream_signature(merged_up) == _factory_upstream_signature(new_row)
        else ORIGIN_USER
    )
    action = ACTION_UPDATED if absorbed or conflicts else ACTION_UNCHANGED
    return _EntityMerge(merged_up, action, fields=absorbed, conflicts=conflicts)


def _merge_section(
    name: str,
    old_doc: Any,
    cur_doc: Any,
    new_doc: Any,
    resolutions: dict[str, str],
) -> _EntityMerge:
    """section 合并。row 约定: 用 {"doc": value} 承载, None 表示删除段。"""
    root_key = f"{KIND_SECTION}:{name}:"
    accept_new = resolutions.get(root_key) == RESOLUTION_ACCEPT_NEW

    if new_doc is MISSING and old_doc is MISSING and cur_doc is not MISSING:
        return _EntityMerge({"doc": copy.deepcopy(cur_doc)}, ACTION_KEPT_USER)

    if old_doc is MISSING and cur_doc is MISSING and new_doc is not MISSING:
        return _EntityMerge({"doc": copy.deepcopy(new_doc)}, ACTION_ADDED)

    if cur_doc is MISSING and new_doc is not MISSING:
        if old_doc is not MISSING and new_doc == old_doc:
            return _EntityMerge(None, ACTION_UNCHANGED)
        if accept_new:
            return _EntityMerge({"doc": copy.deepcopy(new_doc)}, ACTION_UPDATED, fields=[""], conflicts=[
                _entity_conflict(KIND_SECTION, name, CONFLICT_MODIFY_DELETE,
                                 RESOLUTION_ACCEPT_NEW, old_doc, MISSING, new_doc),
            ])
        return _EntityMerge(None, ACTION_UNCHANGED, conflicts=[
            _entity_conflict(KIND_SECTION, name, CONFLICT_MODIFY_DELETE,
                             RESOLUTION_KEEP_CUR, old_doc, MISSING, new_doc),
        ])

    if new_doc is MISSING and cur_doc is not MISSING:
        if old_doc is not MISSING and cur_doc == old_doc:
            return _EntityMerge(None, ACTION_REMOVED)
        if accept_new:
            return _EntityMerge(None, ACTION_REMOVED, fields=[""], conflicts=[
                _entity_conflict(KIND_SECTION, name, CONFLICT_DELETE_MODIFY,
                                 RESOLUTION_ACCEPT_NEW, old_doc, cur_doc, MISSING),
            ])
        return _EntityMerge({"doc": copy.deepcopy(cur_doc)}, ACTION_KEPT_USER, conflicts=[
            _entity_conflict(KIND_SECTION, name, CONFLICT_DELETE_MODIFY,
                             RESOLUTION_KEEP_CUR, old_doc, cur_doc, MISSING),
        ])

    if cur_doc is MISSING or new_doc is MISSING:
        return _EntityMerge(None, ACTION_UNCHANGED)

    outcome = merge_value(
        KIND_SECTION, name, "", old_doc, cur_doc, new_doc, resolutions
    )
    if outcome.value is MISSING:
        return _EntityMerge(None, ACTION_REMOVED, fields=outcome.absorbed,
                            conflicts=outcome.conflicts)
    action = ACTION_UPDATED if outcome.absorbed or outcome.conflicts else ACTION_UNCHANGED
    return _EntityMerge({"doc": outcome.value}, action,
                        fields=outcome.absorbed, conflicts=outcome.conflicts)


def merge_bundles(
    old_bundle: dict[str, Any] | None,
    cur_bundle: dict[str, Any],
    new_bundle: dict[str, Any],
    *,
    resolutions: dict[str, str] | None = None,
) -> UpgradeMergeResult:
    """字段级三方合并入口。三个 bundle 均为 NormalizedTemplate.to_json() 形态。

    resolutions: 更新中心里用户对冲突的逐条改判,
    键为 :attr:`FieldConflict.key` (``"<kind>:<name>:<path>"``),
    值 ``accept_new`` 表示该路径改用出厂值; 其余一律 keep_cur。
    """
    old_bundle = old_bundle or {}
    resolutions = resolutions or {}

    old_tools = _index(old_bundle.get("tools") or [])
    cur_tools = _index(cur_bundle.get("tools") or [])
    new_tools = _index(new_bundle.get("tools") or [])
    old_ups = _index(old_bundle.get("upstreams") or [])
    cur_ups = _index(cur_bundle.get("upstreams") or [])
    new_ups = _index(new_bundle.get("upstreams") or [])
    old_sections = old_bundle.get("sections") or {}
    cur_sections = cur_bundle.get("sections") or {}
    new_sections = new_bundle.get("sections") or {}

    result = UpgradeMergeResult(bundle={})

    # --- sections (dict: 新模板段顺序优先, 用户独有段按 cur 顺序追加) ---
    merged_sections: dict[str, Any] = {}
    section_names = list(new_sections.keys()) + [
        n for n in cur_sections if n not in new_sections
    ]
    for name in section_names:
        merge = _merge_section(
            name,
            old_sections.get(name, MISSING),
            cur_sections.get(name, MISSING),
            new_sections.get(name, MISSING),
            resolutions,
        )
        if merge.row is not None:
            merged_sections[name] = merge.row["doc"]
        result.conflicts.extend(merge.conflicts)
        if _tracked(merge):
            result.sections.append(_change_record(KIND_SECTION, name, merge))

    # --- upstreams ---
    merged_upstreams: list[dict[str, Any]] = []
    for name in _ordered_names(new_ups, cur_ups):
        merge = _merge_upstream(
            name, old_ups.get(name), cur_ups.get(name), new_ups.get(name), resolutions
        )
        if merge.row is not None:
            merged_upstreams.append(merge.row)
        result.conflicts.extend(merge.conflicts)
        if _tracked(merge):
            result.upstreams.append(_change_record(KIND_UPSTREAM, name, merge))

    # --- tools ---
    merged_tools: list[dict[str, Any]] = []
    for name in _ordered_names(new_tools, cur_tools):
        merge = _merge_tool(
            name, old_tools.get(name), cur_tools.get(name), new_tools.get(name), resolutions
        )
        if merge.row is not None:
            merged_tools.append(merge.row)
        result.conflicts.extend(merge.conflicts)
        if _tracked(merge):
            result.tools.append(_change_record(KIND_TOOL, name, merge))

    result.conflicts.sort(key=lambda c: (c.kind, c.name, c.path, c.conflict))
    result.bundle = {
        "sections": merged_sections,
        "upstreams": merged_upstreams,
        "tools": merged_tools,
    }
    return result


def _tracked(merge: _EntityMerge) -> bool:
    """该实体是否需要进入变更台账: 删除/有冲突/结果存在即记录; 静默缺失不记。"""
    return merge.row is not None or merge.action == ACTION_REMOVED or bool(merge.conflicts)


def _change_record(kind: str, name: str, merge: _EntityMerge) -> EntityChange:
    fields = tuple(sorted({f for f in merge.fields if f}))
    # 根级冲突 (path="") 不进字段路径列表, UI 用冲突台账的实体分组呈现。
    paths = tuple(sorted({c.path for c in merge.conflicts if c.path}))
    return EntityChange(kind, name, merge.action, fields=fields, conflicts=paths)


def unresolved_conflicts(
    result: "UpgradeMergeResult",
    resolutions: dict[str, str] | None = None,
) -> list[FieldConflict]:
    """用户未显式裁决的冲突: key 不在 resolutions 表里的冲突。

    引擎对所有冲突 (含已按 accept_new 吸收的) 都留台账; apply 门只拦截
    用户没在更新中心逐条确认过的。显式 ``keep_cur`` 也算已裁决。
    """
    resolutions = resolutions or {}
    return [c for c in result.conflicts if c.key not in resolutions]


def section_origins(
    new_bundle: dict[str, Any],
    merged_bundle: dict[str, Any],
) -> dict[str, str]:
    """合并后各 section 的 origin: 与新出厂一致为 builtin, 否则 user。

    section 在 bundle 里没有 origin 列 (DB 行才有), apply 层 (#48) 用此映射 upsert。
    """
    new_sections = new_bundle.get("sections") or {}
    return {
        name: (ORIGIN_BUILTIN if doc == new_sections.get(name, MISSING) else ORIGIN_USER)
        for name, doc in (merged_bundle.get("sections") or {}).items()
    }
