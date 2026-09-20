"""
日志配置
- JSON 行格式输出到文件, 便于机器解析
- stdio transport 下不挂任何 stream handler, 把 stdout 完整留给 JSON-RPC
"""
import datetime as _dt
import json
import logging
import sys
from pathlib import Path

# LogRecord 自带属性, 序列化时跳过, 剩下的即调用方通过 extra= 传入的结构化字段
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """把 LogRecord 渲染成单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=repr)


def configure_logging(
    transport: str,
    *,
    log_path: str | Path = "logs/gateway.log",
    level: int = logging.INFO,
) -> None:
    """按 transport 配置 root logger。

    Args:
        transport: "stdio" 时不挂 stream handler, 避免污染 JSON-RPC stream
        log_path: JSON 日志文件路径; 相对路径锚定**项目根** (与启动 CWD
            无关, Round-12: 以前按 CWD 解析, 从别的目录启动会另建 logs/,
            与已锚定的 cache.db/config.db 裂脑); 绝对路径原样使用。
            父目录不存在会自动创建。
        level: root logger 级别

    日志目录不可写时**不抛错、不拒启**: 降级为 stderr-only (stdio 模式
    也应急挂 stderr — stdout 仍是唯一的 JSON-RPC 通道, stderr 允许)。
    """
    # 延迟 import: utils → core 保持软依赖, 避免模块加载环
    from ..core.config_db import project_root

    candidate = Path(log_path)
    path = candidate if candidate.is_absolute() else project_root() / candidate

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level)

    file_handler: logging.FileHandler | None = None
    log_dir_error: OSError | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)
    except OSError as exc:
        log_dir_error = exc

    # http 模式常态挂 stderr (人眼盯屏纯文本, 文件里是 JSON);
    # stdio 模式仅在文件 handler 起不来时应急挂 stderr, 不能因日志设施
    # 故障让网关静默启动失败。
    if transport != "stdio" or file_handler is None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(stream_handler)

    if file_handler is None and log_dir_error is not None:
        root.error(
            "failed to open log file %s (%s); file logging disabled, "
            "falling back to stderr",
            path, log_dir_error,
        )
