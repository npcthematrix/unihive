"""合并后配置的静态一致性校验 (`unihive config lint`).

与启动校验的定位不同:

- ``utils.config_loader.validate_config`` / ``registry.validate_specs`` 是
  启动闸门, 发现错误直接 raise;
- 本模块把同样的检查加上映射/链/参数层的引用完整性检查汇总成一个纯函数
  ``lint_config``, 可在不启动网关、不连上游、不解析密钥环境变量的前提下
  对合并后的完整配置跑 CI/提交前校验, 也为后续 SQLite overlay 合并后的
  校验 (设计文档 §8) 预留扩展点。

输出确定的 issue 列表 (无 IO、无随机序), 便于 golden 测试与分级处理。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..utils.config_loader import validate_config
from .registry import KNOWN_PARAM_NORMALIZERS, resolve_python_type, validate_specs
from .tool_loader import backing_upstreams, enabled_upstream_names

ERROR = "error"
WARNING = "warning"

#: 故意不进 cache.ttl 的 cache_ttl_key: 声明它是为了自解释「这类响应不缓存」,
#: 不是拼写错误。cache.ttl 里以注释形式记录同样的决定
#: (config/upstreams.yaml: "# realtime_quote: 故意不配置 —— 实时行情不缓存")。
UNCACHED_TTL_KEYS = frozenset({"realtime_quote"})


@dataclass(frozen=True)
class LintIssue:
    severity: str  # ERROR | WARNING
    code: str
    message: str


class LintRejected(ValueError):
    """单实体写入门: 候选配置产生 error 级 lint issue, 拒绝写入。"""

    def __init__(self, issues: list[LintIssue]):
        self.issues = list(issues)
        detail = "; ".join(f"{i.code}: {i.message}" for i in issues[:5])
        if len(issues) > 5:
            detail += f"; …(+{len(issues) - 5} more)"
        super().__init__(
            f"entity lint rejected ({len(issues)} error(s)): {detail}"
        )


def lint_config(config: dict, specs: list[dict]) -> list[LintIssue]:
    """对合并后的完整配置与全量工具 spec 跑静态校验。

    Args:
        config: 合并 upstreams.yaml + config.yaml 后的完整配置 dict
            (即启动时网关消费的同一份)。
        specs: ``tool_loader.load_all_tools`` 合并出的全量 spec
            (P0 阶段建议包含 requires_env 门控 spec, 目录正确性与本机
            环境变量无关; CLI 默认全量加载)。

    Returns:
        顺序确定的 LintIssue 列表; 空列表 = 干净配置。
    """
    issues: list[LintIssue] = []
    upstreams = config.get("upstreams") or {}
    routing_cfg = config.get("routing") or {}
    mapping = config.get("upstream_tool_mapping") or {}

    # 1. 结构校验 (上游类型/必填字段/console 段), 每条 C001
    issues.extend(
        LintIssue(ERROR, "C001", msg)
        for msg in validate_config(config)
    )

    # 2. spec 自洽: 缺 name/routing, routing key 无处解析
    issues.extend(
        LintIssue(ERROR, "T001", msg)
        for msg in validate_specs(specs, routing_cfg, mapping, strict=False)
    )

    # 3. 工具名唯一性 (load_all_tools 合并多文件后最容易出现的冲突)
    seen: set[str] = set()
    for spec in specs:
        name = spec.get("name")
        if name and name not in seen:
            seen.add(name)
        elif name:
            issues.append(LintIssue(
                ERROR, "T002", f"Duplicate tool name {name!r} across spec files"
            ))

    # 4. 参数: 重名 error; 未知类型注册期静默回落 str, 属于高风险沉默失败
    for spec in specs:
        _lint_params(spec, issues)

    # 5. 映射/链的上游引用完整性
    known_upstreams = set(upstreams.keys())
    for rk in sorted(mapping):
        backers = mapping[rk]
        if not isinstance(backers, dict):
            issues.append(LintIssue(
                ERROR, "M001",
                f"upstream_tool_mapping[{rk!r}] must be a mapping of "
                f"{{upstream: upstream_tool}}, got {type(backers).__name__}",
            ))
            continue
        for up in backers:
            if up not in known_upstreams:
                issues.append(LintIssue(
                    ERROR, "M001",
                    f"upstream_tool_mapping[{rk!r}] references unknown upstream "
                    f"{up!r}",
                ))

    for rk in sorted(routing_cfg):
        entry = routing_cfg[rk]
        if not isinstance(entry, dict):
            issues.append(LintIssue(
                ERROR, "M002",
                f"routing[{rk!r}] must be a mapping with a 'chain' list, got "
                f"{type(entry).__name__}: {entry!r}",
            ))
            continue
        chain = entry.get("chain")
        if chain is not None and not isinstance(chain, list):
            issues.append(LintIssue(
                ERROR, "M002",
                f"routing[{rk!r}].chain must be a list, got "
                f"{type(chain).__name__}: {chain!r}",
            ))
            continue
        for up in chain or []:
            if up not in known_upstreams:
                issues.append(LintIssue(
                    ERROR, "M002",
                    f"routing[{rk!r}].chain references unknown upstream {up!r}",
                ))

    # 6. 逐工具: 未决路由 / 全部 backer 禁用
    enabled = enabled_upstream_names(config)
    for spec in specs:
        name = spec.get("name")
        if not name:
            continue
        rk = spec.get("routing")
        if not rk:
            continue  # T001 已报 missing routing
        chain_entry = routing_cfg.get(rk)
        has_chain = isinstance(chain_entry, dict) and bool(chain_entry.get("chain"))
        has_mapping = rk in mapping
        has_self_mapping = bool(spec.get("upstream_tool_mapping"))
        # validate_specs 对"routing 块存在但无 chain"是放行的 (in_routing),
        # 但此时 backing_upstreams 为 [] —— 未决路由, 运行时必败。
        # 其余"无处解析"的情形 T001 已覆盖, 不重复告警。
        unrouted_block = (
            isinstance(chain_entry, dict)
            and not chain_entry.get("chain")
            and not has_mapping
            and not has_self_mapping
        )
        if unrouted_block:
            issues.append(LintIssue(
                WARNING, "M003",
                f"Tool {name!r} routing={rk!r} has a routing block without a "
                f"chain and no upstream_tool_mapping (unrouted; runtime calls "
                f"will fail)",
            ))
            continue
        if not (chain_entry or has_mapping or has_self_mapping):
            continue  # T001 已报
        backers = backing_upstreams(rk, config)
        if backers and not any(b in enabled for b in backers):
            issues.append(LintIssue(
                WARNING, "M004",
                f"Tool {name!r} will not be published: all backing upstreams "
                f"{backers} are disabled",
            ))

    # 6b. cache_ttl_key 交叉引用: 指向 cache.ttl 里不存在的档时,
    # effective_ttl 静默回落"不缓存", 拼写错误不会有任何报错。
    # 例外: UNCACHED_TTL_KEYS 是「故意不缓存」的标记 (cache.ttl 里也只留注释),
    # 不报 W003 —— 否则实时行情类工具会永远挂一条无意义的告警。
    ttl_tiers = set((config.get("cache", {}) or {}).get("ttl", {}).keys())
    for spec in specs:
        ttl_key = spec.get("cache_ttl_key")
        if (
            isinstance(ttl_key, str)
            and ttl_key
            and ttl_key not in ttl_tiers
            and ttl_key not in UNCACHED_TTL_KEYS
        ):
            issues.append(LintIssue(
                WARNING, "W003",
                f"Tool {spec.get('name')!r} cache_ttl_key={ttl_key!r} is not "
                f"defined in cache.ttl (known: {sorted(ttl_tiers)}); responses "
                f"will silently be uncached",
            ))

    # 7. 孤儿条目: 没有任何 spec 引用的 mapping/chain
    referenced = {
        (spec.get("routing") or spec.get("name"))
        for spec in specs
        if spec.get("name")
    }
    for rk in sorted(mapping):
        if rk not in referenced:
            issues.append(LintIssue(
                WARNING, "M005",
                f"upstream_tool_mapping[{rk!r}] is not referenced by any tool spec",
            ))
    for rk in sorted(routing_cfg):
        if rk not in referenced:
            issues.append(LintIssue(
                WARNING, "M006",
                f"routing[{rk!r}] is not referenced by any tool spec",
            ))

    return issues


def _lint_params(spec: dict, issues: list[LintIssue]) -> None:
    name = spec.get("name") or "<unnamed>"
    params = spec.get("params") or []
    seen_params: set[str] = set()
    for p in params:
        if not isinstance(p, dict):
            continue
        pname = p.get("name")
        if pname and pname not in seen_params:
            seen_params.add(pname)
        elif pname:
            issues.append(LintIssue(
                ERROR, "T003",
                f"Tool {name!r} declares duplicate parameter {pname!r}",
            ))
        ptype = p.get("type", "str")
        if resolve_python_type(ptype) is None:
            issues.append(LintIssue(
                WARNING, "W001",
                f"Tool {name!r} parameter {pname!r} uses unknown type "
                f"{ptype!r}; registry silently falls back to str",
            ))
        normalize = p.get("normalize")
        if isinstance(normalize, str) and normalize and normalize not in KNOWN_PARAM_NORMALIZERS:
            issues.append(LintIssue(
                WARNING, "W002",
                f"Tool {name!r} parameter {pname!r} normalize={normalize!r} is "
                f"not implemented (known: {sorted(KNOWN_PARAM_NORMALIZERS)}); "
                f"the declared normalization silently does not happen",
            ))
