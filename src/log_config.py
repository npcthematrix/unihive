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
        log_path: JSON 日志文件路径, 父目录不存在会自动创建
        level: root logger 级别
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level)

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    if transport != "stdio":
        # 人眼盯屏用纯文本, 机器解析用文件里的 JSON
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(stream_handler)
