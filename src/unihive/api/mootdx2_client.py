"""MooTDX2 MCP Client 基于 mootdx2 库"""
import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .upstream_client import ToolResult, UpstreamStatus
from ..core.blocking_pool import get_blocking_executor
from ..models.mootdx2_errors import (
    ErrorClassifier,
    MooTDXErrorType,
    error_to_result,
    no_data_result,
)
from .index_registry import (
    INDEX_KIND,
    STOCK_KIND,
    classify_symbol,
    parse_symbol,
)


class SectorDataError(Exception):
    """板块数据相关错误的领域异常

    携带 MooTDXErrorType + 中文消息，由 async wrapper 转 ToolResult。
    """

    def __init__(self, error_type: "MooTDXErrorType", message: str):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


if TYPE_CHECKING:
    from ..models.mootdx2_config import MooTDX2Settings
    from .mootdx2_pool import ConnectionPool

logger = logging.getLogger(__name__)

# K线频率映射
FREQ_MAP = {
    "day": 9,
    "week": 5,
    "month": 6,
    "minute1": 8,
    "minute5": 0,
    "minute15": 1,
    "minute30": 2,
    "minute60": 3,
}

# 数据量限制常量
MAX_LIMIT_KLINE = 1000  # K线最大返回条数
MAX_LIMIT_BATCH_QUOTE = 50  # 批量行情最大股票数
MAX_LIMIT_TRADE = 500  # 逐笔成交最大条数
MAX_LIMIT_TRANSACTIONS = 500  # 历史逐笔最大条数
MAX_LIMIT_STOCK_LIST = 10000  # 股票列表最大条数

# 数据量过大错误消息模板
DATA_TOO_LARGE_MSG = "数据量超过{limit}条，请缩小查询范围或增加筛选条件"


def _validate_kline_record(record: dict) -> bool:
    """校验单条K线数据合理性"""
    try:
        close = float(record.get("close", 0))
        open_ = float(record.get("open", 0))
        high = float(record.get("high", 0))
        low = float(record.get("low", 0))
        vol = float(record.get("vol", 0))
        # 收盘价必须 > 0
        if close <= 0:
            return False
        # 最高价 >= 最低价
        if high < low:
            return False
        # 开盘/收盘应该在最高/最低范围内
        if open_ > high or open_ < low:
            return False
        if close > high or close < low:
            return False
        # 成交量 >= 0
        if vol < 0:
            return False
        return True
    except (TypeError, ValueError):
        return False


def _validate_quote_record(record: dict) -> bool:
    """校验单条行情数据合理性"""
    try:
        close = float(record.get("close", 0))
        if close <= 0:
            return False
        return True
    except (TypeError, ValueError):
        return False


@dataclass
class MooTDX2Config:
    """MooTDX2 配置"""
    name: str
    market: str = "std"
    # 新增配置字段
    settings: "MooTDX2Settings | None" = None  # 完整配置
    pool: "ConnectionPool | None" = None  # 连接池


class MooTDX2Client:
    """MooTDX2 MCP 客户端"""

    def __init__(self, config: MooTDX2Config):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._quotes = None
        self._quotes_cls = None  # 延迟导入
        self._reader = None  # Reader 实例缓存
        self._reader_lock = threading.Lock()  # Reader 缓存锁（高并发保护）
        self._quotes_lock = threading.Lock()  # Quotes 缓存锁（高并发保护）

        # H1 (round 8 audit): 后台周期探活任务, 让 DEGRADED 状态有机会
        # 在 TDX 恢复后自动回到 HEALTHY。
        self._health_task: asyncio.Task | None = None
        self._probe_attempts: int = 0  # 用于 start() 内的重试计数 (H2)

        # 指标收集
        self._request_id = 0
        # L3 (2026-09-15 MCP 二次审视): error_counts 上限防止内存无限增长
        self._ERROR_COUNTS_MAX = 100
        self._metrics = {
            "total_requests": 0,
            "total_errors": 0,
            "error_counts": {},
        }
        # 2026-09-14 round 10 audit: total_errors / error_counts 多 key 复合
        # 写入非原子, 多个 async task 并发会丢更新。加 lock 保护。
        # total_requests 单 int += 是 GIL 原子的, 不需要。
        self._metrics_lock = threading.Lock()

        # 2026-09-15 get_daily_local 内存缓存 (思路 3):
        # 仅缓存 adjust=none 的 records (list[dict]), TTL 2h, maxsize 2048。
        # 99% 不复权 + 50% 同 symbol 重复 → TTL 主导淘汰, 命中率高。
        # 不动 Reader/Quotes/线程池, 不动 SQLite, 失败模式 = 走原路径。
        # 设计稿: docs/superpowers/specs/2026-09-15-get-daily-local-cache-design.md
        try:
            from cachetools import TTLCache
            self._daily_l1 = TTLCache(maxsize=2048, ttl=7200.0)
            self._daily_l1_hits = 0
            self._daily_l1_misses = 0
            self._DAILY_L1_ENABLED = True
            # 2026-09-15 single-flight 去重 first-miss 雪崩:
            #   key = (reader_symbol_key, "none"), value = asyncio.Future[ToolResult]
            # 首个 miss 占位 Future 跑 _get_daily_sync, 后续 miss 共享同一 Future 结果。
            # 仅 adjust="none" 路径使用, 与 L1 缓存 key 对齐。
            self._daily_l1_inflight: dict[tuple[str, str], asyncio.Future] = {}
            self._daily_l1_inflight_lock = asyncio.Lock()  # 索引互斥
        except ImportError:
            self._DAILY_L1_ENABLED = False
            logger.warning("cachetools not installed, get_daily L1 cache disabled")

    def _new_request_id(self) -> str:
        """生成请求 ID"""
        self._request_id += 1
        return f"{self.name}-{int(time.time())}-{self._request_id}"

    def _record_error(self, error_type: str) -> None:
        """原子记录一次错误 (round 10 audit: error_counts[k] += 1 非原子).

        total_errors 简单 int += 是 GIL 安全的, 但 error_counts[key] 复合
        读写需要 lock 保护, 否则并发下会丢更新。

        L3 (2026-09-15 MCP 二次审视): 达到上限时 LRU 清理最老的条目。
        """
        with self._metrics_lock:
            self._metrics["total_errors"] += 1
            error_counts = self._metrics["error_counts"]
            # LRU 清理: 超过上限时删除最老的条目
            if len(error_counts) >= self._ERROR_COUNTS_MAX and error_type not in error_counts:
                oldest_key = next(iter(error_counts))
                del error_counts[oldest_key]
            error_counts[error_type] = error_counts.get(error_type, 0) + 1

    def _get_tdxdir(self) -> str | None:
        """获取 tdxdir，如果没有配置则返回 None 让 mootdx2 自动探测"""
        if self.config.settings:
            tdxdir = self.config.settings.tdxdir
            if tdxdir:
                return tdxdir
        return None

    @staticmethod
    def _filter_records_by_date(records: list, start_date: str, end_date: str) -> list:
        """缓存命中后在 records 上做 start/end 过滤。O(N) dict 遍历。
        date 字段为 'yyyy-MM-dd' 字符串; 字符串比较即可 (ISO 8601)。"""
        if not (start_date or end_date):
            return list(records)
        out = []
        for r in records:
            d = r.get("date", "")
            if not d:
                continue
            if start_date and d < start_date:
                continue
            if end_date and d > end_date:
                continue
            out.append(r)
        return out
        return None

    def _get_reader(self):
        """获取缓存的 Reader 实例（懒初始化，线程安全）。"""
        if self._reader is None:
            with self._reader_lock:
                # 双重检查
                if self._reader is None:
                    from mootdx2.reader import Reader
                    tdxdir = self._get_tdxdir()
                    self._reader = Reader.factory(market="std", tdxdir=tdxdir)
        return self._reader

    # 2026-09-14 round 11 audit (坑 #6 防御):
    # mootdx2 `_default_tdxdir()` 按平台惯例返回:
    #   - Windows: C:/new_tdx
    #   - macOS:   ~/new_tdx
    #   - Linux:   ~/.local/share/new_tdx
    # 跨机器部署时若用户没显式配 tdxdir, 会静默指向空目录 → Reader
    # 返回 DataFrame(data=None) → 上游 no_data, 调用方无法区分
    # "代码错" vs "本地未下载"。这里在 start() 探测一次, 写 INFO/WARN
    # 日志告知用户当前 tdxdir 是否能命中白名单指数 .day 文件。
    # best-effort: 不抛、不改 status、不影响在线接口。
    _PROBE_INDEX_SAMPLES: tuple[tuple[str, str], ...] = (
        ("000001", "sh"),   # 上证指数（必检 SH 命中）
        ("399001", "sz"),   # 深证成指（必检 SZ 命中）
        ("000300", "sh"),   # 沪深300（SH 宽基，曾因前缀推断被错放 sz）
    )

    def _probe_tdxdir(self) -> None:
        """启动期 tdxdir 健康探测（best-effort）。

        探测失败原因（仅写日志，不抛）:
          (1) tdxdir 为空
          (2) 目录不存在
          (3) vipdoc 子目录缺失
          (4) 指数白名单文件一个都没有命中（本地数据未下载）
        """
        tdxdir = self._get_tdxdir()
        if not tdxdir:
            logger.warning(
                f"{self.name}: tdxdir 未配置；离线 reader 工具 "
                f"将返回 tdx_not_installed（在线模式可忽略）"
            )
            return

        base = Path(tdxdir)
        if not base.exists() or not base.is_dir():
            logger.warning(
                f"{self.name}: tdxdir={base} 不存在或不是目录"
            )
            return

        vipdoc = base / "vipdoc"
        if not vipdoc.is_dir():
            logger.warning(
                f"{self.name}: tdxdir={base} 下未发现 vipdoc/ 子目录；"
                f"离线 reader 工具不可用"
            )
            return

        # 探一下白名单指数 .day 文件是否存在（任一命中即视为 OK）
        hits: list[str] = []
        for code, mkt in self._PROBE_INDEX_SAMPLES:
            p = vipdoc / mkt / "lday" / f"{mkt}{code}.day"
            if p.exists():
                hits.append(p.name)
        if hits:
            logger.info(
                f"{self.name}: tdxdir 探测 OK；本地 .day 命中 "
                f"{len(hits)}/{len(self._PROBE_INDEX_SAMPLES)} 个指数样本"
                f"（{hits[:3]}）"
            )
        else:
            logger.warning(
                f"{self.name}: tdxdir/vipdoc 存在但未发现任何白名单指数 "
                f".day 文件；离线指数工具将返回 no_data（请通过 TDX 客户端 "
                f"下载完整本地数据）"
            )

    def _error_result(self, exc: Exception, context: str) -> ToolResult:
        """将异常转换为错误结果"""
        err_info = error_to_result(exc, context)
        error_detail = err_info.get("error", {})
        error_type = error_detail.get("error_type", "internal_error")
        self._record_error(error_type)
        return ToolResult(
            success=False,
            error=error_detail.get("message", str(exc)[:100]),
            error_detail=error_detail,
        )

    def _sector_error_result(self, exc: "SectorDataError") -> ToolResult:
        """将 SectorDataError 转换为 ToolResult（recoverable=False，配置/业务错误）"""
        self._record_error(exc.error_type.value)
        return ToolResult(
            success=False,
            error=exc.message,
            error_detail={
                "error_type": exc.error_type.value,
                "message": exc.message,
                "recoverable": False,
            },
        )

    def _no_data_result(self, message: str = "查询成功但无数据") -> ToolResult:
        """返回 no_data 状态（不是错误，是正常业务状态）"""
        return ToolResult(
            success=True,
            data=None,
            error=message,
            error_detail={
                "error_type": MooTDXErrorType.NO_DATA.value,
                "message": message,
                "recoverable": True,
            },
        )

    def _build_daily_tool_result(
        self,
        *,
        code: str,
        data: list[dict] | None,
        meta: dict,
        adjust: str,
        parsed_for_key,
        reader_symbol_key: str | None,
    ) -> ToolResult:
        """Build ToolResult from `_get_daily_sync` output.

        - `data` truthy -> success ToolResult; attaches `meta["warn"]` if present
        - `data` falsy -> no_data ToolResult with kind-aware message
        - 指数代码 (kind=index) -> "指数 X 本地无 .day 文件 (可能未下载完整本地数据)"
        - 其他 -> "未找到 X 的日线数据"
        """
        if data:
            result = ToolResult(success=True, data=data, source="mootdx2")
            if meta.get("warn"):
                result.error_detail = {"warn": meta["warn"], "recoverable": True}
            return result
        # 无数据
        from .index_registry import parse_symbol, INDEX_KIND
        if meta.get("kind") == INDEX_KIND:
            if parsed_for_key is not None:
                msg = (
                    f"指数 {parsed_for_key.code} 本地无 .day 文件"
                    f"（可能未下载完整本地数据）"
                )
            else:
                try:
                    parsed = parse_symbol(code)
                    msg = (
                        f"指数 {parsed.code} 本地无 .day 文件"
                        f"（可能未下载完整本地数据）"
                    )
                except ValueError:
                    msg = "指数本地无 .day 文件（可能未下载完整本地数据）"
        else:
            msg = f"未找到 {code} 的日线数据"
        return self._no_data_result(msg)

    def get_metrics(self) -> dict:
        """获取客户端指标"""
        return {
            **self._metrics,
            "pool_stats": self.config.pool.get_stats() if self.config.pool else {},
        }

    async def get_metrics_async(self) -> ToolResult:
        """获取服务指标（异步版本供 call_tool 调用）"""
        return ToolResult(
            success=True,
            data=self.get_metrics(),
            source="mootdx2",
        )

    async def get_health(self) -> ToolResult:
        """健康检查接口（轻量 ping）"""
        return ToolResult(
            success=True,
            data={"status": "ok", "source": "mootdx2"},
            source="mootdx2",
        )

    async def get_server_status(self) -> ToolResult:
        """获取服务状态（供 MCP 网关管理台直接读取）"""
        pool_stats = self.config.pool.get_stats() if self.config.pool else {}

        # 当前实际连接的服务器: 优先读 mootdx2 ServerManager 的 current,
        # 退回 StdQuotes.server; quotes 未初始化时为 None。
        current_server = None
        quotes = self._quotes
        if quotes is not None:
            current = None
            manager = getattr(quotes, "manager", None)
            if manager is not None and getattr(manager, "current", None):
                current = manager.current
            elif getattr(quotes, "server", None):
                current = quotes.server
            if current:
                current_server = {"host": current[0], "port": int(current[1])}

        configured_servers = (
            [{"host": s.host, "port": s.port} for s in self.config.settings.servers]
            if self.config.settings else []
        )

        return ToolResult(
            success=True,
            data={
                "version": self.config.settings.version if self.config.settings else "1.0.0",
                "status": self._status.value,
                "current_server": current_server,
                "servers": configured_servers,
                "pool_stats": pool_stats,
                "error_counts": self._metrics.get("error_counts", {}),
                "total_requests": self._metrics.get("total_requests", 0),
            },
            source="mootdx2",
        )

    def _pin_quotes_servers(self, quotes, servers) -> None:
        """把配置的多台行情服务器注入 mootdx2 ServerManager 候选池。

        mootdx2 StdQuotes 的 server= 参数只接受单台 (内部 candidates 固定为
        [server]), 多台顺序故障转移只能通过其 ServerManager 候选列表实现。
        factory 已用首台初始化 manager, 这里把其余服务器追加进同一个
        manager (StdQuotes 与 AutoSwitchClient 共享该对象引用), 运行期请求
        失败时 mootdx2 会自动切换候选并重连。

        auto_switch=1 (factory 处传入) 让阈值为 1: 一次失败即切下一台,
        否则默认阈值 3 会让第二台在单次请求的重试预算内根本轮不到。
        """
        manager = getattr(quotes, "manager", None)
        if manager is None:
            logger.warning(f"{self.name}: Quotes 实例无 manager, 额外服务器候选未注入")
            return
        candidates = [(s.host, int(s.port)) for s in servers]
        with manager.lock:
            for cand in candidates[1:]:
                if cand not in manager._candidates:
                    manager._candidates.append(cand)
        logger.info(
            f"{self.name}: pinned TDX servers (failover order): "
            f"{[f'{h}:{p}' for h, p in candidates]}"
        )

    def _get_quotes(self):
        """获取或创建 Quotes 实例（线程安全）。

        settings.servers 非空时固定连配置的 IP (bestip=False + server=首台,
        其余经 _pin_quotes_servers 注入 failover 候选); 为空时保持原行为
        bestip=True 全网测速自动选优。
        """
        if self._quotes is None:
            with self._quotes_lock:
                # 双重检查
                if self._quotes is None:
                    from mootdx2.quotes import Quotes
                    self._quotes_cls = Quotes
                    settings = self.config.settings
                    pinned = settings.servers if settings else []
                    # 从配置获取超时设置
                    timeout = 30  # 默认 30 秒
                    if settings:
                        timeout = max(
                            settings.connect_timeout_seconds,
                            settings.read_timeout_seconds,
                        ) * 3  # 给足够的重试时间
                    if pinned:
                        first = pinned[0]
                        # bestip=False: 跳过全网测速; auto_switch=1: 失败 1 次
                        # 即切换候选, 保证第二台在单次请求重试预算内能轮到。
                        quotes = Quotes.factory(
                            market=self.config.market,
                            multithread=True,
                            bestip=False,
                            server=(first.host, int(first.port)),
                            auto_switch=1,
                            timeout=timeout,
                        )
                        self._pin_quotes_servers(quotes, pinned)
                        self._quotes = quotes
                    else:
                        self._quotes = Quotes.factory(
                            market=self.config.market,
                            multithread=True,
                            bestip=True,
                            timeout=timeout,
                        )
        return self._quotes

    def _get_quote_sync(self, code: str):
        """同步获取实时行情"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.quotes(symbols=[sym])
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            return {
                "symbol": code,
                "close": float(latest.get("close", 0)),
                "open": float(latest.get("open", 0)),
                "high": float(latest.get("high", 0)),
                "low": float(latest.get("low", 0)),
                "volume": float(latest.get("vol", 0)),
                "amount": float(latest.get("amount", 0)),
            }
        return None

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        # P1 (2026-09-09 7th-round audit): 与 UpstreamClient / OmniClient 对齐,
        # DEGRADED 仍可响应, router 不应直接跳过 fallback。
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    async def start(self):
        """MED-2 (2026-09-14 round 8 audit): 启动时跑一次轻量连通性探测,
        避免 TDX 不可达时仍报 HEALTHY, 首次请求才报错。探测失败设 DEGRADED
        (留 UNAVAILABLE 给"明确不可用"场景, DEGRADED 表示"探测未通但保留余地
        让健康检查后续重试")。探测 2.5s 超时, 不阻塞启动。

        H2 (round 8 audit): 探测最多重试 2 次 (间隔 1s), 避免瞬时网络抖动误判。
        C1 (round 8 audit): 复用 self._quotes 实例, 避免每次 probe 都触发
        Quotes.factory() 重新测速所有服务器, 启动延迟叠加。
        """
        # C1: 提前创建一次 Quotes 实例 (factory 内部测速一次, 后续 probe 复用)
        # 2026-09-14 round 11 audit: tdxdir 探测 (best-effort, 不抛) —
        # 早于 Quotes init, 方便用户在日志里立刻看到本地数据目录错配。
        self._probe_tdxdir()
        try:
            self._ensure_quotes_initialized()
        except Exception as e:
            logger.warning(
                f"{self.name}: Quotes init failed ({type(e).__name__}: {e}); "
                f"status=DEGRADED. TDX server may be unreachable."
            )
            # C5 (round 9 audit): best-effort 清理. 若 Quotes.factory() 抛
            # 异常前已部分初始化 (e.g. socket opened), Python GC 会回收;
            # 这里强制置 None 避免下游误用半初始化对象。
            self._quotes = None
            self._status = UpstreamStatus.DEGRADED
            # 2026-09-14 round 11 audit: init 失败时不该启动 health_loop —
            # 没有 quotes 的话, health_loop 会在每 30s tick 时早 continue
            # (因 self._quotes is None), 等于纯空转。等下次 start() 重新尝试
            # factory 后再启动 health_loop。
            return

        # H2: probe 最多重试 2 次 (总共 3 次尝试)
        for attempt in range(3):
            try:
                ok = await asyncio.wait_for(
                    self._probe_with_existing_quotes(),
                    timeout=2.5,
                )
                if ok:
                    self._status = UpstreamStatus.HEALTHY
                    logger.info(
                        f"{self.name}: startup probe OK (attempt {attempt + 1}/3)"
                    )
                    self._start_health_loop()
                    return
                logger.warning(
                    f"{self.name}: startup probe empty (attempt {attempt + 1}/3)"
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"{self.name}: startup probe timed out (attempt {attempt + 1}/3)"
                )
            except Exception as e:
                logger.warning(
                    f"{self.name}: startup probe failed "
                    f"({type(e).__name__}: {e}) (attempt {attempt + 1}/3)"
                )
            if attempt < 2:
                await asyncio.sleep(1.0)

        # 3 次都失败 → DEGRADED (留余地让 health loop 重试)
        self._status = UpstreamStatus.DEGRADED
        tdxdir = (
            self.config.settings.tdxdir
            if self.config.settings else "N/A"
        )
        logger.warning(
            f"{self.name}: startup probe failed 3 times; "
            f"status=DEGRADED. Check TDX server reachable at "
            f"tdxdir={tdxdir}."
        )
        self._start_health_loop()

    def _ensure_quotes_initialized(self) -> None:
        """C1 (round 8 audit): 提前初始化 Quotes 实例 (factory 测速一次),
        后续 probe 复用, 避免每次都重新 factory 触发测速叠加延迟。

        2026-09-14 round 11 audit: 这里是同步调用 Quotes.factory(), factory
        内部要测速所有 TDX 服务器 (阻塞 IO)。如果 start() 路径上直接调
        _ensure_quotes_initialized, 会卡住 gateway 启动 event loop N 秒。

        调用方 (start() line 327) 已经在 async 上下文中, 这里建议改用
        asyncio.to_thread 包装。但 start() 本身是 awaited, 调用方是
        gateway_server 的并行 startup 任务, 阻塞这一个 client 的 start
        不阻塞其它 upstream — 实际不会卡 gateway 整体启动, 仅是这一个
        upstream 的 ready 信号晚 N 秒。

        折中: 此函数保持同步 (接口简单), caller 负责用 to_thread 包。
        start() 当前是 sync 直接调, 因上面分析不影响其它上游, 暂不修。
        但加注释提醒后续调用方注意。
        """
        self._get_quotes()  # 触发 lazy init (可能阻塞 N 秒)

    async def _probe_with_existing_quotes(self) -> bool:
        """C1: 复用 self._quotes 实例跑 probe (不重新 factory)。

        2026-09-14 round 11 audit: 之前在 _do_probe 内部如果 self._quotes
        为 None 就调 _get_quotes() 重建。但 _do_probe 在 background thread
        跑, 与 event loop 里的 stop() 会产生 race — thread 拿到旧 quotes
        继续用, 而 event loop 已经把它设回 None, 连接泄漏到 GC 才释放。

        新策略: probe 路径不重建 quotes。None 就返回 False, 让 health_loop
        下次 tick 重试 (期间 start() 可重新触发 _get_quotes())。
        """
        def _do_probe():
            q = self._quotes
            if q is None:
                return False
            df = q.quotes(symbols=["000001"])
            return df is not None and len(df) > 0

        return await asyncio.to_thread(_do_probe)

    def _start_health_loop(self) -> None:
        """H1 (round 8 audit): 启动后台周期探活, 让 DEGRADED 状态有机会
        在 TDX 恢复后回到 HEALTHY。每 30s probe 一次 (复用 quotes)。"""
        if self._health_task and not self._health_task.done():
            return
        self._health_task = asyncio.create_task(self._health_loop())

    async def _health_loop(self) -> None:
        """后台周期探活: DEGRADED → HEALTHY 自动恢复, HEALTHY → DEGRADED 失败检测。

        C2 (round 9 audit): 检测 self._quotes is None, stop() 后或 start()
        Quotes.init 失败时不进入 probe 循环, 避免 AttributeError。
        """
        try:
            while True:
                await asyncio.sleep(30.0)
                # C2: stop() 已清理 self._quotes, 不再 probe
                if self._quotes is None:
                    logger.debug(
                        f"{self.name}: health_loop skipped (quotes unavailable)"
                    )
                    continue
                try:
                    ok = await asyncio.wait_for(
                        self._probe_with_existing_quotes(),
                        timeout=2.5,
                    )
                    if ok and self._status != UpstreamStatus.HEALTHY:
                        self._status = UpstreamStatus.HEALTHY
                        logger.info(
                            f"{self.name}: health_loop recovered → HEALTHY"
                        )
                    elif not ok and self._status == UpstreamStatus.HEALTHY:
                        self._status = UpstreamStatus.DEGRADED
                        logger.warning(
                            f"{self.name}: health_loop probe empty → DEGRADED"
                        )
                except asyncio.TimeoutError:
                    if self._status == UpstreamStatus.HEALTHY:
                        self._status = UpstreamStatus.DEGRADED
                        logger.warning(
                            f"{self.name}: health_loop timeout → DEGRADED"
                        )
                except Exception as e:
                    logger.debug(
                        f"{self.name}: health_loop probe error: {e}"
                    )
        except asyncio.CancelledError:
            pass

    async def stop(self):
        # 2026-09-15: 清理 get_daily L1 缓存
        if self._DAILY_L1_ENABLED and hasattr(self, '_daily_l1'):
            self._daily_l1.clear()
            # 取消所有 in-flight future, 防止 waiter 在 stop 后挂起
            for fut in self._daily_l1_inflight.values():
                if not fut.done():
                    fut.set_exception(asyncio.CancelledError())
            self._daily_l1_inflight.clear()

        # H1: 取消后台探活任务
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        # M4 (round 8 audit): 关闭 Quotes 实例防止连接泄漏
        if self._quotes is not None:
            try:
                close_method = getattr(self._quotes, "close", None)
                if close_method:
                    await asyncio.to_thread(close_method)
            except Exception as e:
                logger.debug(f"{self.name}: Quotes.close failed: {e}")
            self._quotes = None
        self._status = UpstreamStatus.UNKNOWN

    async def health_check(self) -> bool:
        """健康检查 — 读取本地 _status, 不真打 TDX 服务 (round 8 决策).

        2026-09-14 round 11 audit: 这是 stale 缓存读, 与 is_available 同源.
        router 决策走 is_available (router.py:153), health_check 主要供
        console UI / /health 端点展示。不会做 fallback 路由决策。
        """
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        """工具路由

        C3 (round 9 audit): 顶层 try/except, 失败时降级 status (HEALTHY →
        DEGRADED), 让 router 快速跳过此上游, 不必等 30s health_loop。
        """
        method_map = {
            "get_quote": self.get_quote,
            "get_kline": self.get_kline,
            "get_batch_quote": self.get_batch_quote,
            "get_minute_data": self.get_minute_data,
            "get_trade": self.get_trade,
            "get_trade_history": self.get_trade_history,
            "get_index_kline": self.get_index_kline,
            "get_code_list": self.get_code_list,
            "get_stock_codes": self.get_stock_codes,
            "get_etf_codes": self.get_etf_codes,
            "get_etf_list": self.get_etf_list,
            "get_market_count": self.get_market_count,
            "get_workday": self.get_workday,
            "get_workday_range": self.get_workday_range,
            "get_index_all": self.get_index_all,
            "get_income": self.get_income,
            # 新增接口
            "get_f10": self.get_f10,
            "get_f10_company": self.get_f10_company,
            "get_minutes": self.get_minutes,
            "get_index_bars": self.get_index_bars,
            "get_xdxr": self.get_xdxr,
            "get_stock_all": self.get_stock_all,
            "get_k_data": self.get_k_data,
            "search_stock": self.search_stock,
            "get_stock_info": self.get_stock_info,
            "get_custom_sector_list": self.get_custom_sector_list,
            "get_custom_sector_stocks": self.get_custom_sector_stocks,
            "get_index_overview": self.get_index_overview,
            # 2026-09-14 round 11 audit: 离线指数 K 线（Reader.daily 强制
            # 无复权），与在线 get_index_kline 区别在于不依赖 TDX 网络。
            "get_index_kline_local": self.get_index_kline_local,
            "stock_top_board": self.stock_top_board,
            "stock_unusual": self.stock_unusual,
            "indicator_atr": self.indicator_atr,
            # Reader 离线接口（本地 .day / .lc1 / .lc5 文件）
            "get_daily": self.get_daily,
            "get_minute": self.get_minute,
            "get_fzline": self.get_fzline,
            # YAML 工具名映射
            "batch_bars": self.batch_bars,
            "get_daily_local": self.get_daily,
            "get_fzline_local": self.get_fzline,
            "get_minute_local": self.get_minute,
            "mootdx2_stock_info": self.get_stock_info,
            "search_stock_local": self.search_stock,
        }

        method = method_map.get(tool_name)
        if not method:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

        try:
            return await method(**params)
        except Exception as e:
            # C3 (round 9 audit): 顶层异常, 降级 status 让 router 快速跳过
            if self._status == UpstreamStatus.HEALTHY:
                self._status = UpstreamStatus.DEGRADED
                logger.warning(
                    f"{self.name}: call_tool({tool_name}) raised, "
                    f"status → DEGRADED: {type(e).__name__}: {e}"
                )
            # 2026-09-14 round 10 audit: 之前这里 `raise` 让异常逃出 router
            # 整个 fallback 链 — router 只 catch TimeoutError, 其它异常会直
            # 接打到 MCP 协议层, fallback 失效。改为 return ToolResult,
            # 让 router 看到 success=False 后串行走到下一上游。
            return self._error_result(e, f"call_tool({tool_name})")

    async def get_quote(self, code: str) -> ToolResult:
        """获取实时行情

        Args:
            code: 股票代码，如 '600000' 或 'sh600000'

        Returns:
            ToolResult: 包含实时行情数据或错误信息
        """
        request_id = self._new_request_id()
        self._metrics["total_requests"] += 1
        logger.info(f"[{request_id}] get_quote({code})")

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_quote_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result(f"股票 {code} 无行情数据（可能停牌或非交易日）")
        except Exception as e:
            logger.error(f"[{request_id}] get_quote failed: {e}")
            return self._error_result(e, f"get_quote({code})")

    def _get_kline_sync(self, code: str, type: str = "day", limit: int = 100):
        """同步获取K线"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        freq = FREQ_MAP.get(type, 9)
        df = q.bars(symbol=sym, frequency=freq, offset=limit)
        if df is not None and len(df) > 0:
            if limit and limit > 0:
                df = df.tail(limit)
            return df.to_dict(orient="records")
        return None

    async def get_kline(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """获取K线

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数，默认100，最大1000

        Returns:
            数据量超过限制时返回错误提示
        """
        # 数据量检查
        if limit > MAX_LIMIT_KLINE:
            return ToolResult(
                success=False,
                error=DATA_TOO_LARGE_MSG.format(limit=MAX_LIMIT_KLINE),
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_PARAM.value,
                    "message": DATA_TOO_LARGE_MSG.format(limit=MAX_LIMIT_KLINE),
                    "recoverable": False,
                },
            )

        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_kline_sync, code, type, limit)
            if data:
                # 数据合理性校验
                valid_data = [r for r in data if _validate_kline_record(r)]
                if not valid_data:
                    return self._no_data_result("K线数据校验失败（数据异常）")
                return ToolResult(success=True, data=valid_data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_kline failed: {e}")
            return self._error_result(e, f"get_kline({code})")

    # ========== 批量行情 ==========
    def _get_batch_quote_sync(self, codes: str):
        """同步批量行情"""
        q = self._get_quotes()
        code_list = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in codes.split(",")]
        df = q.quotes(symbols=code_list)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_batch_quote(self, codes: str) -> ToolResult:
        """获取批量行情"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_batch_quote_sync, codes)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_batch_quote failed: {e}")
            return self._error_result(e, f"get_batch_quote({codes})")

    # ========== 批量K线 ==========
    FREQ_MAP_BATCH = {
        "day": 9,
        "week": 5,
        "month": 6,
        "minute1": 1,
        "minute5": 5,
        "minute15": 15,
        "minute30": 30,
        "minute60": 60,
    }

    def _get_batch_bars_sync(self, codes: str, type: str = "day", limit: int = 100):
        """同步批量K线"""
        q = self._get_quotes()
        code_list = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in codes.split(",")]
        freq = FREQ_MAP_BATCH.get(type, 9)
        result = q.batch_bars(symbols=code_list, frequency=freq, count=limit)
        if result and isinstance(result, dict):
            return {k: v.to_dict(orient="records") if v is not None else [] for k, v in result.items()}
        return None

    async def batch_bars(self, codes: str, type: str = "day", limit: int = 100) -> ToolResult:
        """批量获取K线"""
        if limit > 500:
            limit = 500
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_batch_bars_sync, codes, type, limit)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"batch_bars failed: {e}")
            return self._error_result(e, f"batch_bars({codes})")

    # ========== 今日分时 ==========
    def _get_minute_data_sync(self, code: str):
        """同步今日分时"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.minute(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_minute_data(self, code: str) -> ToolResult:
        """获取今日分时"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_minute_data_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_minute_data failed: {e}")
            return self._error_result(e, f"get_minute_data({code})")

    # ========== 逐笔成交 ==========
    def _get_trade_sync(self, code: str):
        """同步逐笔成交"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.transaction(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_trade(self, code: str) -> ToolResult:
        """获取逐笔成交"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_trade_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_trade failed: {e}")
            return self._error_result(e, f"get_trade({code})")

    # ========== 历史逐笔 ==========
    def _get_trade_history_sync(self, code: str, date: str, start: int, count: int):
        """同步历史逐笔"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.transactions(symbol=sym, date=date, start=start, offset=count)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_trade_history(self, code: str, date: str, start: int = 0, count: int = 100) -> ToolResult:
        """获取历史逐笔"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_trade_history_sync, code, date, start, count)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_trade_history failed: {e}")
            return self._error_result(e, f"get_trade_history({code})")

    # ========== 指数K线 ==========
    def _get_index_kline_sync(self, code: str, type: str):
        """同步指数K线"""
        q = self._get_quotes()
        freq = FREQ_MAP.get(type, 9)
        df = q.index(symbol=code, frequency=freq)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_index_kline(self, code: str, type: str = "day") -> ToolResult:
        """获取指数K线"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_index_kline_sync, code, type)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_index_kline failed: {e}")
            return self._error_result(e, f"get_index_kline({code})")

    # ========== 全量代码列表 ==========
    # mootdx2 Quotes.stocks(market) 只支持沪深 (TDX 协议 market: 1=SH/0=SZ),
    # market=2(BJ) 库会直接抛 MootdxValidationException; 全市场走 stock_all()。
    _CODE_LIST_MARKETS = {"sh": 1, "sz": 0}

    def _get_code_list_sync(self, exchange: str) -> list[dict] | None:
        """同步全量代码列表。返回合同字段 [{"code", "name"}, ...]。

        mootdx2 不支持北交所 (bj), 该入参返回 None 由上层转 no_data。
        """
        q = self._get_quotes()
        exchange = (exchange or "").strip().lower()
        if exchange:
            market = self._CODE_LIST_MARKETS.get(exchange)
            if market is None:
                logger.info(
                    "get_code_list: mootdx2 全量代码列表不支持交易所 %r (仅 sh/sz)",
                    exchange,
                )
                return None
            df = q.stocks(market=market)
        else:
            df = q.stock_all()
        if df is None or len(df) == 0:
            return None
        # 解析器行含 volunit/decimal_point 等内部列, 只保留合同承诺的 code/name
        return [
            {"code": str(row["code"]), "name": row.get("name", "")}
            for row in df.to_dict(orient="records")
            if row.get("code")
        ]

    async def get_code_list(self, exchange: str = "") -> ToolResult:
        """获取全量代码列表

        Args:
            exchange: 交易所, sh=上海 / sz=深圳 / bj=北京(库不支持);
                      "" 或 None 返回沪深全部
        """
        self._metrics["total_requests"] += 1
        exchange_val = (exchange or "").strip().lower()
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                get_blocking_executor(), self._get_code_list_sync, exchange_val
            )
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            if exchange_val == "bj":
                return self._no_data_result(
                    "mootdx2 全量代码列表仅支持沪深 (sh/sz),暂不支持北京证券交易所"
                )
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_code_list failed: {e}")
            return self._error_result(e, f"get_code_list({exchange})")

    # ========== 股票代码列表 ==========
    def _get_stock_codes_sync(self, limit: int, prefix: bool):
        """同步股票代码列表"""
        q = self._get_quotes()
        df = q.stock(exchange="sz")
        if df is not None and len(df) > 0:
            codes = df["code"].tolist()[:limit]
            if prefix:
                return [f"sz{c}" for c in codes]
            return codes
        return None

    async def get_stock_codes(self, limit: int = 100, prefix: bool = False) -> ToolResult:
        """获取股票代码列表"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_stock_codes_sync, limit, prefix)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_stock_codes failed: {e}")
            return self._error_result(e, f"get_stock_codes(limit={limit})")

    # ========== ETF代码列表 ==========
    def _get_etf_codes_sync(self, limit: int, prefix: bool):
        """同步ETF代码列表"""
        q = self._get_quotes()
        df = q.etf(exchange="sz")
        if df is not None and len(df) > 0:
            codes = df["code"].tolist()[:limit]
            if prefix:
                return [f"sz{c}" for c in codes]
            return codes
        return None

    async def get_etf_codes(self, limit: int = 100, prefix: bool = False) -> ToolResult:
        """获取ETF代码列表"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_etf_codes_sync, limit, prefix)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_etf_codes failed: {e}")
            return self._error_result(e, f"get_etf_codes(limit={limit})")

    # ========== ETF列表 ==========
    def _get_etf_list_sync(self, exchange: str, limit: int):
        """同步ETF列表"""
        q = self._get_quotes()
        df = q.etf(exchange=exchange)
        if df is not None and len(df) > 0:
            result = df.head(limit).to_dict(orient="records")
            return result
        return None

    async def get_etf_list(self, exchange: str = "sz", limit: int = 100) -> ToolResult:
        """获取ETF列表"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_etf_list_sync, exchange, limit)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_etf_list failed: {e}")
            return self._error_result(e, f"get_etf_list({exchange})")

    # ========== 市场证券数量 ==========
    def _get_market_count_sync(self, market: int):
        """同步市场证券数量"""
        q = self._get_quotes()
        count = q.stock_count(market=market)
        return {"market": market, "count": count}

    async def get_market_count(self, market: int = 0) -> ToolResult:
        """获取市场证券数量"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_market_count_sync, market)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_market_count failed: {e}")
            return self._error_result(e, f"get_market_count({market})")

    # ========== 交易日查询 ==========
    def _get_workday_sync(self, date: str, count: int):
        """同步交易日查询"""
        from datetime import datetime, timedelta
        # 简化的交易日计算
        try:
            target = datetime.strptime(date, "%Y%m%d")
        except ValueError:
            target = datetime.strptime(date, "%Y-%m-%d")
        # 从目标日期往前找count个交易日
        results = []
        current = target
        while len(results) < count:
            current -= timedelta(days=1)
            if current.weekday() < 5:  # 周一到周五
                results.append(current.strftime("%Y%m%d"))
        results.reverse()
        return results

    async def get_workday(self, date: str, count: int = 1) -> ToolResult:
        """获取交易日"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_workday_sync, date, count)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_workday failed: {e}")
            return self._error_result(e, f"get_workday({date})")

    # ========== 交易日范围 ==========
    def _get_workday_range_sync(self, start: str, end: str):
        """同步交易日范围"""
        from datetime import datetime, timedelta
        try:
            start_dt = datetime.strptime(start, "%Y%m%d")
        except ValueError:
            start_dt = datetime.strptime(start, "%Y-%m-%d")
        try:
            end_dt = datetime.strptime(end, "%Y%m%d")
        except ValueError:
            end_dt = datetime.strptime(end, "%Y-%m-%d")
        results = []
        current = start_dt
        while current <= end_dt:
            if current.weekday() < 5:
                results.append(current.strftime("%Y%m%d"))
            current += timedelta(days=1)
        return results

    async def get_workday_range(self, start: str, end: str) -> ToolResult:
        """获取交易日范围"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_workday_range_sync, start, end)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_workday_range failed: {e}")
            return self._error_result(e, f"get_workday_range({start}-{end})")

    # ========== 全指K线 ==========
    def _get_index_all_sync(self, code: str, type: str, limit: int):
        """同步全指K线"""
        q = self._get_quotes()
        freq = FREQ_MAP.get(type, 9)
        df = q.index(symbol=code, frequency=freq)
        if df is not None and len(df) > 0:
            if limit and limit > 0:
                df = df.tail(limit)
            return df.to_dict(orient="records")
        return None

    async def get_index_all(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """获取全指K线"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_index_all_sync, code, type, limit)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_index_all failed: {e}")
            return self._error_result(e, f"get_index_all({code})")

    # ========== 盘后收益 ==========
    def _get_income_sync(self, code: str):
        """同步盘后收益"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.income(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_income(self, code: str, days: int = 30, start_date: str = "") -> ToolResult:
        """获取盘后收益"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_income_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_income failed: {e}")
            return self._error_result(e, f"get_income({code})")

    # ========== 自定义板块 ==========
    def _scan_blk_files_sync(self) -> list[dict]:
        """直接扫描 T0002/blocknew/*.blk 文件（不依赖 cfg 索引）

        适用于 V7.73+ 通达信：cfg 索引格式可能更新，但 .blk 文件本身仍可读。
        策略：先尝试 cfg 解析（拿到 blockname → blk_file 映射），
        cfg 解析失败时退化为用 .blk 文件名作为 sector_name。

        .blk 文件格式：每行 = market_id(1=SH / 0=SZ / 2=BJ) + 6位股票代码
        例如：'1600000' 表示 SH 600000，'0000001' 表示 SZ 000001。
        """
        tdxdir = self._get_tdxdir()
        blocknew_dir = Path(tdxdir) / "T0002" / "blocknew"
        if not blocknew_dir.exists():
            logger.info(
                f"custom sector dir missing (V7.73+ TDX?): {blocknew_dir}"
            )
            return []

        # 1. 尝试从 cfg 解析 sector_name → blk_file 映射
        name_by_blk: dict[str, str] = {}
        try:
            from mootdx2.tools.customize import Customize
            customize = Customize(tdxdir=tdxdir)
            grouped = customize.search(group=True)
            # mootdx2 返回 DataFrame，列: blockname, block_type, stock_count, code_list
            if grouped is not None and len(grouped) > 0:
                # 检查是否是 DataFrame
                if hasattr(grouped, 'iterrows'):
                    # DataFrame 格式
                    for _, row in grouped.iterrows():
                        blk_file = str(row.get('block_type', '')).strip()
                        name = str(row.get('blockname', '')).strip()
                        if blk_file and name:
                            name_by_blk[blk_file] = name
                else:
                    # 列表格式（旧版本兼容）
                    for entry in grouped or []:
                        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                            name = str(entry[0]).strip()
                            blk_file = entry[2] if len(entry) >= 3 else None
                            if blk_file:
                                name_by_blk[str(blk_file).strip()] = name
        except Exception as e:
            logger.info(
                f"Customize.search failed (V7.73 cfg may be incompatible): {e}; "
                f"falling back to .blk filename as sector name"
            )

        # 2. 扫描 .blk 文件，统计每板块的股票数
        market_prefix_map = {"1": "sh", "0": "sz", "2": "bj"}
        results = []
        for blk_path in sorted(blocknew_dir.glob("*.blk")):
            blk_name = blk_path.stem
            display_name = name_by_blk.get(blk_name, blk_name)
            try:
                content = blk_path.read_text(encoding="gbk", errors="ignore")
            except Exception:
                content = blk_path.read_text(encoding="utf-8", errors="ignore")
            codes = []
            for line in content.splitlines():
                line = line.strip()
                if len(line) >= 7 and line[0].isdigit():
                    code = line[1:]
                    if code.isdigit() and len(code) == 6:
                        codes.append(code)
            results.append({
                "sector_name": display_name,
                "sector_type": "custom",
                "stock_count": len(codes),
                "blk_file": blk_name,
            })
        return results

    def _get_custom_sector_list_sync(self) -> list:
        """同步读取自定义板块列表（T0002/blocknew/）

        Returns:
            list[dict]: [{"sector_name", "sector_type": "custom", "stock_count"}, ...]

        V7.73+ 通达信 cfg 索引可能更新，直接扫描 .blk 文件更稳定。
        """
        return self._scan_blk_files_sync()

    async def get_custom_sector_list(self) -> ToolResult:
        """获取自定义板块列表（用户在 TDX 客户端手动维护）

        数据源：{tdxdir}/T0002/blocknew/blocknew.cfg + *.blk
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_custom_sector_list_sync)
            return ToolResult(success=True, data=data, source="mootdx2")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_custom_sector_list failed: {e}")
            return self._error_result(e, "get_custom_sector_list()")

    def _get_custom_sector_stocks_sync(self, sector_code: str, market: str = "auto") -> list:
        """同步读取自定义板块成分股

        Args:
            sector_code: 板块名称（用户自定义）或 .blk 文件名（不含扩展名）
            market: 市场（保留参数，对齐其它工具签名；内部按 .blk 文件的 market_id 前缀自动推断）

        V7.73+ 通达信 cfg 索引可能更新，直接扫描 .blk 文件更稳定。
        """
        tdxdir = self._get_tdxdir()
        blocknew_dir = Path(tdxdir) / "T0002" / "blocknew"
        if not blocknew_dir.exists():
            logger.info(
                f"custom sector dir missing (V7.73+ TDX?): {blocknew_dir}"
            )
            return []

        # 1. 尝试 cfg 解析拿到 blk_file
        blk_file = None
        try:
            from mootdx2.tools.customize import Customize
            customize = Customize(tdxdir=tdxdir)
            grouped = customize.search(group=True)
            for entry in grouped or []:
                if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                    if str(entry[0]).strip() == sector_code:
                        blk_file = str(entry[2]).strip()
                        break
        except Exception as e:
            logger.info(
                f"Customize.search(name=) failed (V7.73 cfg may be incompatible): {e}; "
                f"treating sector_code as .blk filename"
            )

        # 2. cfg 失败或未匹配时，直接把 sector_code 当作 .blk 文件名
        if blk_file is None:
            blk_file = sector_code

        blk_path = blocknew_dir / f"{blk_file}.blk"
        if not blk_path.exists():
            logger.info(
                f"custom sector '{sector_code}' not found (blk={blk_path} missing)"
            )
            return []

        # 3. 解析 .blk 文件：每行 = market_id + 6位代码
        market_prefix_map = {"1": "sh", "0": "sz", "2": "bj"}
        try:
            content = blk_path.read_text(encoding="gbk", errors="ignore")
        except Exception:
            content = blk_path.read_text(encoding="utf-8", errors="ignore")

        result = []
        for line in content.splitlines():
            line = line.strip()
            if len(line) < 7 or not line[0].isdigit():
                continue
            prefix = line[0]
            code = line[1:]
            if not (code.isdigit() and len(code) == 6):
                continue
            m = market_prefix_map.get(prefix, "sz")
            result.append({"code": code, "market": m})
        return result

    async def get_custom_sector_stocks(
        self, sector_code: str, market: str = "auto"
    ) -> ToolResult:
        """获取自定义板块的成分股

        Args:
            sector_code: 自定义板块名（用户在 TDX 客户端命名的标签）
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                get_blocking_executor(), self._get_custom_sector_stocks_sync, sector_code, market
            )
            return ToolResult(success=True, data=data, source="mootdx2")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_custom_sector_stocks failed: {e}")
            return self._error_result(e, f"get_custom_sector_stocks({sector_code})")

    # ========== 指数概览 ==========
    def _index_overview_sync(self) -> list:
        """同步获取主要指数概览（6个指数）"""
        INDEX_CODES = [
            ("000001", "上证指数", "sh"),
            ("399001", "深证成指", "sz"),
            ("399006", "创业板", "sz"),
            ("000688", "科创50", "sh"),
            ("889999", "北证50", "bj"),
            ("000300", "沪深300", "sh"),
        ]
        q = self._get_quotes()
        # Build code list with market prefixes
        code_list = []
        for c, n, m in INDEX_CODES:
            if m == "sh":
                code_list.append(f"sh{c}")
            elif m == "sz":
                code_list.append(f"sz{c}")
            else:
                code_list.append(f"bj{c}")
        # Strip prefixes for quotes API
        stripped = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in code_list]
        df = q.quotes(symbols=stripped)
        if df is None or len(df) == 0:
            return []
        records = df.to_dict(orient="records")
        # Match back by position (order is preserved)
        result = []
        for i, rec in enumerate(records):
            if i >= len(INDEX_CODES):
                break
            code, name, market = INDEX_CODES[i]
            close = float(rec.get("close", 0))
            pct_chg = float(rec.get("pct_chg", 0))
            result.append({
                "code": code,
                "name": name,
                "market": market,
                "close": close,
                "change_pct": pct_chg,
            })
        return result

    async def get_index_overview(self) -> ToolResult:
        """获取主要指数概览（6个指数：上证、深证、创业板、科创50、北证50、沪深300）"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._index_overview_sync)
            if not data:
                return self._no_data_result("指数数据获取失败")
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"get_index_overview failed: {e}")
            return self._error_result(e, "get_index_overview")

    # ========== F10 基础数据 ==========
    def _get_f10_sync(self, symbol: str, name: str = ""):
        """同步F10基础数据"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.F10(symbol=sym, name=name)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_f10(self, symbol: str, name: str = "") -> ToolResult:
        """获取F10基础数据

        Args:
            symbol: 股票代码
            name: F10数据类型名称（可选）
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_f10_sync, symbol, name)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_f10 failed: {e}")
            return self._error_result(e, f"get_f10({symbol})")

    # ========== F10 公司概况 ==========
    def _get_f10_company_sync(self, symbol: str):
        """同步F10公司概况"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.F10C(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_f10_company(self, symbol: str) -> ToolResult:
        """获取F10公司概况

        Args:
            symbol: 股票代码
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_f10_company_sync, symbol)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_f10_company failed: {e}")
            return self._error_result(e, f"get_f10_company({symbol})")

    # ========== 分钟K线 ==========
    def _get_minutes_sync(self, symbol: str, date: str = "20191023"):
        """同步分钟K线"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.minutes(symbol=sym, date=date)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_minutes(self, symbol: str, date: str = "20191023") -> ToolResult:
        """获取分钟K线

        Args:
            symbol: 股票代码
            date: 日期，默认为 '20191023'
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_minutes_sync, symbol, date)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_minutes failed: {e}")
            return self._error_result(e, f"get_minutes({symbol})")

    # ========== 离线日线（Reader，TDX 本地 .day 文件） ==========
    def _get_daily_sync(
        self, code: str, adjust: str = "none", start_date: str = "", end_date: str = ""
    ) -> list:
        """同步读取离线日线数据。

        Args:
            code: 6 位股票代码（不带市场前缀）
            adjust: 复权方式 none / qfq / hfq（默认不复权）
            start_date: 起始日期 yyyy-MM-dd（可选）
            end_date: 结束日期 yyyy-MM-dd（可选）

        2026-09-14 round 11 audit (坑 #2/#3 防御):
            - 入口用 ``parse_symbol`` 判断 kind
            - kind=index → reader_symbol 强制 "sh000300" 形式,
              否则 mootdx2 ``Reader.find_path`` 会按代码前缀推断
              把 SH 宽基错放到 sz/lday
            - kind=index 时 adjust 强制覆盖为 'none' (静默 + meta.warn)
              否则 mootdx2 走 ``to_adjust`` → ``get_xdxr`` 拉新浪
              复权因子 → 指数无 XDXR 抛 ReversionError 中断调用
        """
        # 入口解析; ValueError 由调用方 _get_daily 入口转 invalid_symbol
        parsed = parse_symbol(code)
        meta = {"kind": parsed.kind, "warn": None}

        # 指数强制无复权（坑 #3）
        if parsed.kind == INDEX_KIND and adjust in ("qfq", "hfq"):
            meta["warn"] = (
                f"指数 {parsed.code} 不支持 {adjust} 复权，已强制为 none"
            )
            adjust = "none"

        # Reader 调用符号：
        #   - stock: 纯 6 位 → reader.find_path 内部前缀推断
        #   - index: 必须 "{market}{code}" 形式，否则错放目录
        reader_symbol = (
            f"{parsed.market}{parsed.code}" if parsed.kind == INDEX_KIND
            else parsed.code
        )

        reader = self._get_reader()
        df = reader.daily(symbol=reader_symbol, adjust=adjust)
        if df is None or getattr(df, "empty", True):
            # 把 kind 信息附在异常路径里，便于 _get_daily 给更明确的 no_data 文案
            setattr(self, "_last_daily_meta", meta)
            return []

        if start_date:
            df = df[df.index >= start_date] if hasattr(df.index, "__ge__") else df
        if end_date:
            df = df[df.index <= end_date] if hasattr(df.index, "__le__") else df

        records = df.reset_index().to_dict(orient="records")
        for r in records:
            if "index" in r:
                idx = r.pop("index")
                r["date"] = str(idx)[:10]
        setattr(self, "_last_daily_meta", meta)
        return records

    async def get_daily(
        self, code: str, adjust: str = "none", start_date: str = "", end_date: str = ""
    ) -> ToolResult:
        """获取股票日线数据（离线，TDX 本地 .day 文件）

        Args:
            code: 股票代码（6 位数字，可带 sh/sz/bj 前缀或 .SH/.SZ/.BJ 后缀）
            adjust: 复权方式
                - 'none': 不复权（默认）
                - 'qfq': 前复权
                - 'hfq': 后复权
                - 指数代码传 qfq/hfq 会被静默覆盖为 none（warn 字段说明）
            start_date: 起始日期 yyyy-MM-dd（可选）
            end_date: 结束日期 yyyy-MM-dd（可选）

        数据源：{tdxdir}/vipdoc/sh/lday/sh600036.day 等本地二进制文件

        2026-09-14 round 11 audit:
            - 指数代码通过 ``parse_symbol`` 走 ``INDEX_MARKET_MAP`` 显式映射
            - 未登记代码 / 后缀冲突 → ``invalid_symbol``
            - 指数 .day 本地不存在 → 更明确的 no_data 文案
        """
        self._metrics["total_requests"] += 1
        try:
            # 2026-09-15: get_daily_local L1 缓存 (仅 adjust=none)
            # 复用用户原 adjust 入参 (经过 _get_daily_sync 内会按 kind 改写)。
            # 缓存 key 用 reader_symbol + adjust 入参, 命中后按 start/end 过滤。
            from .index_registry import parse_symbol, STOCK_KIND, INDEX_KIND
            cache_hit = False
            cached_records = None
            if self._DAILY_L1_ENABLED:
                try:
                    parsed_for_key = parse_symbol(code)
                except ValueError:
                    parsed_for_key = None
                if parsed_for_key is not None:
                    reader_symbol_key = (
                        f"{parsed_for_key.market}{parsed_for_key.code}"
                        if parsed_for_key.kind == INDEX_KIND
                        else parsed_for_key.code
                    )
                    cache_key = (reader_symbol_key, adjust)
                    cached_records = self._daily_l1.get(cache_key)
                    if cached_records is not None:
                        self._daily_l1_hits += 1
                        cache_hit = True
                    else:
                        self._daily_l1_misses += 1

            if cache_hit and cached_records is not None:
                # 在缓存 records 上做 start/end 切片 (dict 遍历, O(N))
                filtered = self._filter_records_by_date(cached_records, start_date, end_date)
                return ToolResult(success=True, data=filtered, source="mootdx2")

            # miss 路径: 仅 adjust=none + 解析成功 → 走 single-flight 去重
            inflight_future: asyncio.Future | None = None
            is_leader = False
            if (
                self._DAILY_L1_ENABLED
                and adjust == "none"
                and parsed_for_key is not None
            ):
                async with self._daily_l1_inflight_lock:
                    existing = self._daily_l1_inflight.get(
                        (reader_symbol_key, adjust)
                    )
                    if existing is not None:
                        inflight_future = existing
                    else:
                        inflight_future = (
                            asyncio.get_running_loop().create_future()
                        )
                        self._daily_l1_inflight[
                            (reader_symbol_key, adjust)
                        ] = inflight_future
                        is_leader = True

            if inflight_future is not None and not is_leader:
                # waiter: 共享 leader 的 ToolResult
                shared = await inflight_future
                return shared

            # leader 路径 (or 非 none 路径 / inflight 旁路)
            try:
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(
                    get_blocking_executor(),
                    self._get_daily_sync,
                    code,
                    adjust,
                    start_date,
                    end_date,
                )
                meta = getattr(
                    self, "_last_daily_meta", {"kind": STOCK_KIND, "warn": None}
                )
                result = self._build_daily_tool_result(
                    code=code,
                    data=data,
                    meta=meta,
                    adjust=adjust,
                    parsed_for_key=parsed_for_key,
                    reader_symbol_key=reader_symbol_key,
                )
                # 写 L1 (仅 adjust=none + 有数据 + 解析成功)
                if (
                    self._DAILY_L1_ENABLED
                    and is_leader
                    and adjust == "none"
                    and data
                    and parsed_for_key is not None
                ):
                    self._daily_l1[(reader_symbol_key, adjust)] = data

                if is_leader and inflight_future is not None:
                    if not inflight_future.done():
                        inflight_future.set_result(result)

                return result
            except ValueError as e:
                # ValueError 来自 _get_daily_sync 内部 parse_symbol 抛出,
                # 转为 error_result 让 waiter 拿到, 然后 re-raise 给外层
                # (外层会再转为 invalid_symbol ToolResult)
                error_result = self._error_result(e, f"get_daily({code})")
                if is_leader and inflight_future is not None:
                    if not inflight_future.done():
                        inflight_future.set_result(error_result)
                raise
            except Exception as e:
                # leader 异常: 转为 error ToolResult, 让 waiter 拿同一结果
                error_result = self._error_result(e, f"get_daily({code})")
                if is_leader and inflight_future is not None:
                    if not inflight_future.done():
                        inflight_future.set_result(error_result)
                return error_result
            finally:
                # 必须在所有路径 (success / ValueError / Exception) 清理 inflight,
                # 否则 leader 释放后, 后续同 key 请求会把过期 Future 当成 leader 复用。
                if is_leader and inflight_future is not None:
                    async with self._daily_l1_inflight_lock:
                        self._daily_l1_inflight.pop(
                            (reader_symbol_key, adjust), None
                        )
        except ValueError as e:
            # parse_symbol 抛: 后缀非法 / 后缀冲突 / 主体非 6 位数字
            self._record_error(MooTDXErrorType.INVALID_SYMBOL.value)
            return ToolResult(
                success=False,
                error=str(e),
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_SYMBOL.value,
                    "message": str(e),
                    "recoverable": False,
                },
            )
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_daily failed: {e}")
            return self._error_result(e, f"get_daily({code})")

    # ========== 离线分钟线（Reader，TDX 本地 .lc1/.lc5 文件） ==========
    def _get_minute_sync(
        self, code: str, suffix: str = "1", start_date: str = "", end_date: str = ""
    ) -> list:
        """同步读取离线分钟线数据。

        Args:
            code: 6 位股票代码
            suffix: '1' = 1 分钟线（minline/.lc1）/ '5' = 5 分钟线（fzline/.lc5）
            start_date: 起始日期（可选）
            end_date: 结束日期（可选）
        """
        tdxdir = self._get_tdxdir()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        suffix_int = 1 if str(suffix) == "1" else 5

        reader = self._get_reader()
        df = reader.minute(symbol=sym, suffix=suffix_int)
        if df is None or df.empty:
            return []

        if start_date:
            df = df[df.index >= start_date] if hasattr(df.index, "__ge__") else df
        if end_date:
            df = df[df.index <= end_date] if hasattr(df.index, "__le__") else df

        records = df.reset_index().to_dict(orient="records")
        for r in records:
            if "index" in r:
                idx = r.pop("index")
                r["datetime"] = str(idx)
        return records

    async def get_minute(
        self, code: str, suffix: str = "1", start_date: str = "", end_date: str = ""
    ) -> ToolResult:
        """获取股票分钟线数据（离线，TDX 本地 .lc1/.lc5 文件）

        Args:
            code: 股票代码（6 位数字）
            suffix: '1' = 1 分钟线（默认）/ '5' = 5 分钟线
            start_date: 起始日期（可选）
            end_date: 结束日期（可选）

        数据源：{tdxdir}/vipdoc/sh/minline/sh600036.lc1
              或 {tdxdir}/vipdoc/sh/fzline/sh600036.lc5
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                get_blocking_executor(), self._get_minute_sync, code, suffix, start_date, end_date
            )
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result(f"未找到 {code} 的{suffix}分钟数据")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_minute failed: {e}")
            return self._error_result(e, f"get_minute({code})")

    # ========== 离线分时线（Reader.fzline，本地 .lc5 文件） ==========
    def _get_fzline_sync(self, code: str) -> list:
        """同步读取离线分时线数据。

        Args:
            code: 6 位股票代码
        """
        reader = self._get_reader()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = reader.fzline(symbol=sym)
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        if isinstance(df, bool):
            return []

        records = df.reset_index().to_dict(orient="records")
        for r in records:
            if "index" in r:
                idx = r.pop("index")
                r["datetime"] = str(idx)
        return records

    async def get_fzline(self, code: str) -> ToolResult:
        """获取股票分时线数据（离线，TDX 本地 .lc5 文件）

        Args:
            code: 股票代码（6 位数字）

        数据源：{tdxdir}/vipdoc/sh/fzline/sh600036.lc5
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_fzline_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result(f"未找到 {code} 的分时线数据")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_fzline failed: {e}")
            return self._error_result(e, f"get_fzline({code})")

    # ========== 离线指数K线（Reader，TDX 本地 .day 文件） ==========
    # 2026-09-14 round 11 audit (坑 #1/#2/#3/#4/#5/#7 防御):
    # mootdx2 ``Reader`` 没有专门的 ``index()`` 方法，离线读指数本质上
    # 仍走 ``Reader.daily(symbol=...)``，但必须显式把指数映射到正确的
    # sh/sz 子目录（坑 #2），强制无复权（坑 #3），并对本地文件缺失
    # 给出明确 no_data 提示（坑 #4），对 offset 截断打 warn（坑 #5），
    # 分钟级不支持（坑 #7）。
    _INDEX_LOCAL_TYPES: tuple[str, ...] = ("day", "week", "month")
    # 注：当前实现只直接返回 .day 文件原始日线数据（type 仅 day）。
    # week/month 由调用方自行 resample，避免 mootdx2 内部周/月频率支持
    # 不完整（Reader.daily 不接受 frequency 参数）的隐式返回问题。

    def _get_index_kline_local_sync(
        self, code: str, limit: int,
    ) -> tuple[list, dict]:
        """同步读取离线指数 K 线（日级，强制 adjust='none'）。

        Returns:
            ``(records, meta)``，meta 含 ``truncated`` 等字段。
        """
        parsed = parse_symbol(code)
        reader = self._get_reader()
        reader_symbol = f"{parsed.market}{parsed.code}"
        df = reader.daily(symbol=reader_symbol, adjust="none")
        meta: dict = {"kind": parsed.kind, "truncated": False}
        if df is None or getattr(df, "empty", True):
            return [], meta

        records = df.reset_index().to_dict(orient="records")
        for r in records:
            if "index" in r:
                idx = r.pop("index")
                r["date"] = str(idx)[:10]

        # 坑 #5 防御: limit 截断时显式标记 truncated
        if records and limit and limit > 0 and len(records) > limit:
            records = records[-limit:]
            meta["truncated"] = True
        return records, meta

    async def get_index_kline_local(
        self, code: str, type: str = "day", limit: int = 100,
    ) -> ToolResult:
        """离线指数 K 线（Reader.daily，强制无复权）。

        与在线 ``get_index_kline`` 的区别:
          - 不依赖 TDX 网络，走本地 .day 文件
          - ``type`` 仅支持 day/week/month（分钟级请用 get_minute_local）
          - 指数代码必须已登记到 ``INDEX_MARKET_MAP``（未登记 → invalid_symbol）

        Args:
            code: 指数代码，6 位数字或带 .SH/.SZ/.BJ 后缀
            type: K线类型，仅 day/week/month（默认 day）
            limit: 返回条数，默认 100，最大 1000

        Returns:
            ToolResult: 数据 / no_data / invalid_symbol / invalid_param / tdx_not_installed
        """
        # type 校验（坑 #7 防御）
        if type not in self._INDEX_LOCAL_TYPES:
            return ToolResult(
                success=False,
                error=(
                    f"get_index_kline_local 仅支持 "
                    f"{'/'.join(self._INDEX_LOCAL_TYPES)}，{type} 请用 get_minute_local"
                ),
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_PARAM.value,
                    "message": f"type={type} 不支持",
                    "recoverable": False,
                },
            )
        if limit > MAX_LIMIT_KLINE:
            return ToolResult(
                success=False,
                error=DATA_TOO_LARGE_MSG.format(limit=MAX_LIMIT_KLINE),
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_PARAM.value,
                    "message": DATA_TOO_LARGE_MSG.format(limit=MAX_LIMIT_KLINE),
                    "recoverable": False,
                },
            )

        self._metrics["total_requests"] += 1
        # 入口解析: 未登记 / 后缀冲突 / 非法后缀 → ValueError → invalid_symbol
        try:
            parsed = parse_symbol(code)
        except ValueError as e:
            self._record_error(MooTDXErrorType.INVALID_SYMBOL.value)
            return ToolResult(
                success=False,
                error=str(e),
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_SYMBOL.value,
                    "message": str(e),
                    "recoverable": False,
                },
            )

        # kind=stock 走错工具: 明确告知用户
        if parsed.kind != INDEX_KIND:
            return ToolResult(
                success=False,
                error=(
                    f"代码 {code} 不是指数（kind={parsed.kind}），"
                    f"请用 get_daily_local / get_kline"
                ),
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_SYMBOL.value,
                    "message": f"{code} 不是指数",
                    "recoverable": False,
                },
            )

        try:
            loop = asyncio.get_event_loop()
            records, meta = await loop.run_in_executor(
                get_blocking_executor(),
                self._get_index_kline_local_sync, code, limit,
            )
            if records:
                result = ToolResult(success=True, data=records, source="mootdx2")
                if meta.get("truncated"):
                    # 坑 #5 防御: offset 截断时显式告警
                    result.error_detail = {
                        "warn": "truncated",
                        "returned": len(records),
                        "requested": limit,
                        "recoverable": True,
                    }
                return result
            # 坑 #4 防御: 本地 .day 缺失给明确 no_data 文案
            return self._no_data_result(
                f"指数 {parsed.code} 本地无 .day 文件"
                f"（可能未下载完整本地数据）"
            )
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_index_kline_local failed: {e}")
            return self._error_result(e, f"get_index_kline_local({code})")

# ========== 指数K线（指定起止） ==========
    def _get_index_bars_sync(self, symbol: str, frequency: int = 9, start: int = 0, offset: int = 800):
        """同步指数K线（指定起止位置）"""
        q = self._get_quotes()
        df = q.index_bars(symbol=symbol, frequency=frequency, start=start, offset=offset)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_index_bars(self, symbol: str, frequency: int = 9, start: int = 0, offset: int = 800) -> ToolResult:
        """获取指数K线（指定起止位置）

        Args:
            symbol: 指数代码，如 '000001'（上证指数）
            frequency: K线频率，默认9（日线），其他：5（周线）、6（月线）
            start: 起始位置
            offset: 偏移量
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_index_bars_sync, symbol, frequency, start, offset)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_index_bars failed: {e}")
            return self._error_result(e, f"get_index_bars({symbol})")

    # ========== 除权除息数据 ==========
    def _get_xdxr_sync(self, symbol: str):
        """同步除权除息数据"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.xdxr(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_xdxr(self, symbol: str) -> ToolResult:
        """获取除权除息数据

        Args:
            symbol: 股票代码
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_xdxr_sync, symbol)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_xdxr failed: {e}")
            return self._error_result(e, f"get_xdxr({symbol})")

    # ========== 全部股票列表 ==========
    def _get_stock_all_sync(self):
        """同步全部股票列表"""
        q = self._get_quotes()
        df = q.stock_all()
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_stock_all(self) -> ToolResult:
        """获取全部股票列表"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_stock_all_sync)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_stock_all failed: {e}")
            return self._error_result(e, "get_stock_all")

    # ========== K线数据（指定日期范围） ==========
    def _get_k_data_sync(self, code: str, start_date: str, end_date: str):
        """同步K线数据（指定日期范围）"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.get_k_data(code=sym, start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_k_data(self, code: str, start_date: str = "", end_date: str = "") -> ToolResult:
        """获取K线数据（指定日期范围）

        Args:
            code: 股票代码
            start_date: 开始日期，格式 YYYYMMDD 或 YYYY-MM-DD
            end_date: 结束日期，格式 YYYYMMDD 或 YYYY-MM-DD
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_k_data_sync, code, start_date, end_date)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_k_data failed: {e}")
            return self._error_result(e, f"get_k_data({code})")

    # ========== 搜索股票 ==========
    def _search_stock_sync(self, keyword: str):
        """同步搜索股票（优先本地 block 文件，fallback 到 SQLite）

        V7.73-64 注意事项：mootdx2 `reader.block()` 接口在新版通达信已废弃
        （官方 dat 文件迁到 infoharbor_block.dat / spblock.dat，mootdx2 未适配)。
        当 block 为空时，fallback 到 SQLite (board.db) 搜索。
        """
        # 优先尝试本地 block 文件
        try:
            reader = self._get_reader()
            if self._get_tdxdir():
                df_block = reader.block()
                if df_block is not None and len(df_block) > 0:
                    kw = keyword.lower()
                    result = []
                    for _, row in df_block.iterrows():
                        code = str(row.get("code", ""))
                        name = row.get("name", "")
                        if not code or not name:
                            continue
                        if kw in code or kw in name.lower():
                            # 判断市场前缀
                            if code.startswith(("60", "68")):
                                market = "sh"
                            elif code.startswith(("00", "30")):
                                market = "sz"
                            elif code.startswith(("8", "4")):
                                market = "bj"
                            else:
                                market = "sz"
                            result.append({
                                "code": code,
                                "market": market,
                                "name": name,
                            })
                    return result[:20]
        except Exception as e:
            logger.debug(f"search_stock reader.block() failed: {e}")

        # Fallback: 从 SQLite (board.db) 搜索
        return self._search_stock_from_sqlite(keyword)

    def _search_stock_from_sqlite(self, keyword: str) -> list[dict]:
        """从 SQLite 数据库搜索股票"""
        import sqlite3
        from pathlib import Path

        db_path = Path("data/board.db")
        if not db_path.exists():
            logger.warning("search_stock: board.db not found")
            return []

        try:
            conn = sqlite3.connect(str(db_path))
            kw = f"%{keyword}%"
            cursor = conn.execute("""
                SELECT DISTINCT stock_code, stock_name
                FROM sector_stocks
                WHERE stock_name LIKE ? OR stock_code LIKE ?
                LIMIT 20
            """, (kw, kw))
            result = []
            for stock_code, stock_name in cursor.fetchall():
                # 判断市场前缀
                if stock_code.startswith(("60", "68")):
                    market = "sh"
                elif stock_code.startswith(("00", "30")):
                    market = "sz"
                elif stock_code.startswith(("8", "4")):
                    market = "bj"
                else:
                    market = "sz"
                result.append({
                    "code": stock_code,
                    "market": market,
                    "name": stock_name,
                })
            conn.close()
            return result
        except Exception as e:
            logger.warning(f"search_stock SQLite fallback failed: {e}")
            return []

    async def search_stock(self, keyword: str) -> ToolResult:
        """搜索股票（名称/代码/拼音模糊匹配）

        Args:
            keyword: 搜索关键词（股票名称、代码或拼音缩写）
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._search_stock_sync, keyword)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"search_stock failed: {e}")
            return self._error_result(e, f"search_stock({keyword})")

    # ========== 聚合股票信息 ==========
    def _get_stock_info_sync(self, code: str, market: str = "sz", kline_period: str = "day", kline_count: int = 100):
        """同步获取聚合股票信息（快照在线 + K线/分时离线）"""
        try:
            q = self._get_quotes()
            sym = f"{market}{code}".lower()

            # 1. 实时快照（在线）
            quote_df = q.quotes(symbol=[sym])
            quote = quote_df.iloc[0].to_dict() if len(quote_df) > 0 else None

            # 2. K线（离线 - Reader）
            reader = self._get_reader()
            daily_df = reader.daily(symbol=code)
            kline_list = daily_df.tail(kline_count).to_dict(orient="records") if daily_df is not None and len(daily_df) > 0 else []

            # 3. 当日分时（离线 - Reader minute）
            minute_df = reader.minute(symbol=code)
            minute_list = minute_df.to_dict(orient="records") if minute_df is not None and len(minute_df) > 0 else []

            return {
                "code": code,
                "market": market,
                "quote": quote,
                "kline": kline_list,
                "minute": minute_list,
            }
        except Exception as e:
            logger.error(f"get_stock_info({market}{code}) failed: {e}")
            return {
                "code": code,
                "market": market,
                "quote": None,
                "kline": [],
                "minute": [],
            }

    async def get_stock_info(self, code: str, market: str = "sz", kline_period: str = "day", kline_count: int = 100) -> ToolResult:
        """获取聚合股票信息（快照 + K线 + 分时）

        Args:
            code: 股票代码
            market: 市场前缀 (sh/sz/bj)
            kline_period: K线周期 (day/week/month/minute1/5/15/30/60)
            kline_count: K线返回条数
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._get_stock_info_sync, code, market, kline_period, kline_count)
            if data.get("quote") is None and not data.get("kline") and not data.get("minute"):
                return self._no_data_result(f"股票 {market}{code} 无数据")
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"get_stock_info failed: {e}")
            return self._error_result(e, f"get_stock_info({market}{code})")

    # ========== ATR 指标 ==========
    def _indicator_atr_sync(self, code: str, type: str = "day", limit: int = 100) -> dict:
        """同步计算ATR指标"""
        import pandas as pd
        kline = self._get_kline_sync(code, type, limit)
        if not kline:
            return {}
        df = pd.DataFrame(kline)
        if df.empty or "close" not in df.columns:
            return {}
        highs = df["high"].astype(float).tolist() if "high" in df.columns else df["close"].astype(float).tolist()
        lows = df["low"].astype(float).tolist() if "low" in df.columns else df["close"].astype(float).tolist()
        closes = df["close"].astype(float).tolist()
        dates = df["date"].tolist() if "date" in df.columns else ["" for _ in closes]

        n = 14
        trs = []
        for i in range(len(closes)):
            if i == 0:
                trs.append(highs[0] - lows[0])
            else:
                hl = highs[i] - lows[i]
                hpc = abs(highs[i] - closes[i - 1])
                lpc = abs(lows[i] - closes[i - 1])
                trs.append(max(hl, hpc, lpc))

        atr = []
        for i in range(len(trs)):
            if i < n - 1:
                atr.append(None)
            elif i == n - 1:
                atr.append(round(sum(trs[:n]) / n, 3))
            else:
                atr.append(round((atr[-1] * (n - 1) + trs[i]) / n, 3))

        return {"atr": atr, "dates": dates}

    async def indicator_atr(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """计算ATR指标（平均真实波幅）

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数，默认100，最大1000
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._indicator_atr_sync, code, type, limit)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"indicator_atr failed: {e}")
            return self._error_result(e, f"indicator_atr({code})")

    # ========== 市场异动（主力监控精灵） ==========
    def _stock_unusual_sync(self, event_type: str = "all") -> list:
        """同步获取市场异动数据"""
        import pandas as pd

        q = self._get_quotes()

        # Get concept blocks
        blocks_df = q.block(block_type="concept")
        if blocks_df is None or blocks_df.empty:
            return []

        # Collect stock codes from blocks (limited to avoid too many requests)
        all_codes = set()
        for _, row in blocks_df.head(50).iterrows():
            block_name = str(row.get("name", ""))
            # Use stock_count as hint — we need actual stock codes
            # Since q.block() returns code_list in newer mootdx2, check for it
            if "code_list" in row and pd.notna(row["code_list"]):
                codes = str(row["code_list"]).split(",")
                for c in codes:
                    c = c.strip()
                    if c:
                        all_codes.add(c)

        stock_list = list(all_codes)[:500]
        if not stock_list:
            return []

        # Batch quote
        results = []
        for i in range(0, len(stock_list), MAX_LIMIT_BATCH_QUOTE):
            chunk = stock_list[i:i + MAX_LIMIT_BATCH_QUOTE]
            prefixed = []
            for c in chunk:
                if str(c).startswith(("60", "68")):
                    prefixed.append(f"sh{c}")
                elif str(c).startswith(("00", "30")):
                    prefixed.append(f"sz{c}")
                elif str(c).startswith(("8", "4")):
                    prefixed.append(f"bj{c}")
                else:
                    prefixed.append(f"sz{c}")
            stripped = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in prefixed]
            batch_df = q.quotes(symbols=stripped)
            if batch_df is not None and len(batch_df) > 0:
                results.extend(batch_df.to_dict(orient="records"))

        if not results:
            return []

        df = pd.DataFrame(results)
        if df.empty:
            return []

        # Filter by event type
        if event_type == "涨":
            df = df[df["pct_chg"] > 0]
        elif event_type == "跌":
            df = df[df["pct_chg"] < 0]
        elif event_type == "放量":
            df = df[df["vol"] > df["vol"].quantile(0.75)] if "vol" in df.columns else df
        elif event_type == "缩量":
            df = df[df["vol"] < df["vol"].quantile(0.25)] if "vol" in df.columns else df
        elif event_type == "炸板":
            df = df[(df["pct_chg"] > 0) & (df["pct_chg"] < 9)]
        elif event_type == "天地板":
            df = df[(df["pct_chg"] <= -9.5) | (df["pct_chg"] >= 9.5)]
        else:  # all
            df = df[(df["pct_chg"].abs() >= 5) | (df["pct_chg"] >= 9.5) | (df["pct_chg"] <= -9.5)]

        # Build result with event label
        result = []
        for _, row in df.head(100).iterrows():
            sym = str(row.get("symbol", ""))
            if sym.startswith("sh"):
                mkt, code = "sh", sym.replace("sh", "")
            elif sym.startswith("sz"):
                mkt, code = "sz", sym.replace("sz", "")
            elif sym.startswith("bj"):
                mkt, code = "bj", sym.replace("bj", "")
            else:
                mkt, code = "sz", sym
            pct = float(row.get("pct_chg", 0))
            if pct >= 9.9:
                event = "涨停"
            elif pct <= -9.9:
                event = "跌停"
            elif pct > 5:
                event = "放量上涨"
            elif pct < -5:
                event = "放量下跌"
            elif pct > 0:
                event = "上涨"
            else:
                event = "下跌"
            result.append({
                "code": code,
                "market": mkt,
                "time": "",
                "event": event,
                "change_pct": pct,
            })
        return result

    def _stock_top_board_sync(self, sort_by: str = "change_pct", direction: str = "desc", limit: int = 50, market: str = "all") -> list:
        """同步获取市场排行榜"""
        import pandas as pd

        VALID_SORT = {"change_pct", "amplitude", "turnover", "volume_ratio", "amount"}
        VALID_MARKET = {"all", "sh", "sz", "bj"}
        if sort_by not in VALID_SORT:
            sort_by = "change_pct"
        if market not in VALID_MARKET:
            market = "all"
        limit = min(limit, 200)

        q = self._get_quotes()
        all_quotes = []

        for exchange in ["sz", "sh"]:
            df_stock = q.stock(exchange=exchange)
            if df_stock is None or df_stock.empty:
                continue
            codes = df_stock["code"].tolist()
            prefixed = []
            for c in codes:
                cs = str(c)
                if cs.startswith(("60", "68")):
                    prefixed.append(f"sh{cs}")
                elif cs.startswith(("00", "30")):
                    prefixed.append(f"sz{cs}")
                elif cs.startswith(("8", "4")):
                    prefixed.append(f"bj{cs}")
                else:
                    prefixed.append(f"sz{cs}")
            for i in range(0, len(prefixed), MAX_LIMIT_BATCH_QUOTE):
                chunk = prefixed[i:i + MAX_LIMIT_BATCH_QUOTE]
                stripped = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in chunk]
                batch_df = q.quotes(symbols=stripped)
                if batch_df is not None and len(batch_df) > 0:
                    all_quotes.extend(batch_df.to_dict(orient="records"))

        if not all_quotes:
            return []

        df_quotes = pd.DataFrame(all_quotes)
        if df_quotes.empty:
            return []

        if market != "all":
            df_quotes = df_quotes[df_quotes["symbol"].str.startswith(market)]

        sort_col = sort_by if sort_by in df_quotes.columns else "pct_chg"
        if sort_col not in df_quotes.columns:
            sort_col = "pct_chg"
        ascending = direction == "asc"
        df_quotes = df_quotes.sort_values(sort_col, ascending=ascending).head(limit)

        result = []
        for _, row in df_quotes.iterrows():
            sym = str(row.get("symbol", ""))
            if sym.startswith("sh"):
                mkt, code = "sh", sym.replace("sh", "")
            elif sym.startswith("sz"):
                mkt, code = "sz", sym.replace("sz", "")
            elif sym.startswith("bj"):
                mkt, code = "bj", sym.replace("bj", "")
            else:
                mkt, code = "sz", sym
            result.append({
                "code": code,
                "market": mkt,
                "close": float(row.get("close", 0)),
                "change_pct": float(row.get("pct_chg", 0)),
                "volume": float(row.get("vol", 0)),
                "amount": float(row.get("amount", 0)),
                "turnover": float(row.get("turnover", 0)),
                "volume_ratio": float(row.get("volume_ratio", 0)),
            })
        return result

    async def stock_top_board(self, sort_by: str = "change_pct", direction: str = "desc", limit: int = 50, market: str = "all") -> ToolResult:
        """获取市场排行榜"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._stock_top_board_sync, sort_by, direction, limit, market)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"stock_top_board failed: {e}")
            return self._error_result(e, "stock_top_board")

    async def stock_unusual(self, event_type: str = "all") -> ToolResult:
        """获取市场异动数据（主力监控精灵）"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(get_blocking_executor(), self._stock_unusual_sync, event_type)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"stock_unusual failed: {e}")
            return self._error_result(e, "stock_unusual")
