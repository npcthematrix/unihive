"""MooTDX2 配置管理

配置优先级: 环境变量 > config文件 > 默认值
"""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_CONFIG = {
    "servers": [
        {"host": "106.14.201.78", "port": 7709},
        {"host": "112.74.214.42", "port": 7709},
    ],
    "market": "std",
    "connect_timeout_seconds": 3,
    "read_timeout_seconds": 5,
    "max_retries": 3,
    "retry_backoff_base_seconds": 0.5,
    "connection_pool_size": 5,
    "max_concurrent_requests": 10,
    "auto_select_fastest_server": True,
    "log_level": "INFO",
    "log_path": "./logs/mootdx2.log",
    "tdxdir": "",
}


@dataclass
class ServerConfig:
    """服务器配置"""

    host: str
    port: int = 7709
    latency_ms: Optional[int] = None
    healthy: bool = True


@dataclass
class MooTDX2Settings:
    """MooTDX2 完整配置"""

    # 服务器列表
    servers: list[ServerConfig] = field(default_factory=list)

    # 市场: std(标准), hk(港股)
    market: str = "std"

    # 超时配置
    connect_timeout_seconds: int = 3
    read_timeout_seconds: int = 5

    # 重试配置
    max_retries: int = 3
    retry_backoff_base_seconds: float = 0.5

    # 连接池配置
    connection_pool_size: int = 5
    max_concurrent_requests: int = 10
    auto_select_fastest_server: bool = True

    # 日志配置
    log_level: str = "INFO"
    log_path: str = "./logs/mootdx2.log"

    # 通达信本地路径（用于离线接口）
    tdxdir: str = ""

    # 版本
    version: str = "1.0.0"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MooTDX2Settings":
        """从字典创建配置"""
        servers = []
        for s in data.get("servers", []):
            servers.append(ServerConfig(
                host=s.get("host", "127.0.0.1"),
                port=s.get("port", 7709),
            ))

        return cls(
            servers=servers,
            market=data.get("market", "std"),
            connect_timeout_seconds=data.get("connect_timeout_seconds", 3),
            read_timeout_seconds=data.get("read_timeout_seconds", 5),
            max_retries=data.get("max_retries", 3),
            retry_backoff_base_seconds=data.get("retry_backoff_base_seconds", 0.5),
            connection_pool_size=data.get("connection_pool_size", 5),
            max_concurrent_requests=data.get("max_concurrent_requests", 10),
            auto_select_fastest_server=data.get("auto_select_fastest_server", True),
            log_level=data.get("log_level", "INFO"),
            log_path=data.get("log_path", "./logs/mootdx2.log"),
            tdxdir=data.get("tdxdir", ""),
            version=data.get("version", "1.0.0"),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "servers": [{"host": s.host, "port": s.port} for s in self.servers],
            "market": self.market,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "read_timeout_seconds": self.read_timeout_seconds,
            "max_retries": self.max_retries,
            "retry_backoff_base_seconds": self.retry_backoff_base_seconds,
            "connection_pool_size": self.connection_pool_size,
            "max_concurrent_requests": self.max_concurrent_requests,
            "auto_select_fastest_server": self.auto_select_fastest_server,
            "log_level": self.log_level,
            "log_path": self.log_path,
            "tdxdir": self.tdxdir,
            "version": self.version,
        }


def _resolve_env_vars(value: Any) -> Any:
    """递归解析环境变量占位符 ${VAR} 或 ${VAR:-default}"""
    import re

    if isinstance(value, str):
        # 匹配 ${VAR} 或 ${VAR:-default}
        pattern = r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}"

        def replacer(m: re.Match) -> str:
            var_name = m.group(1)
            default_value = m.group(2)  # 可能为 None
            env_val = os.getenv(var_name)
            if env_val is not None:
                return env_val
            if default_value is not None:
                return default_value
            # 没有默认值且环境变量不存在，保留原样
            return m.group(0)

        return re.sub(pattern, replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_mootdx2_config(config_path: Optional[str] = None) -> MooTDX2Settings:
    """加载 MooTDX2 配置

    优先级: 环境变量 > config文件 > 默认值

    环境变量前缀: MOOTDX2_
    例如: MOOTDX2_CONNECT_TIMEOUT_SECONDS, MOOTDX2_LOG_LEVEL
    """
    # 从环境变量覆盖
    env_overrides: dict[str, Any] = {}

    # 读取环境变量
    env_mappings = {
        "MOOTDX2_MARKET": "market",
        "MOOTDX2_CONNECT_TIMEOUT_SECONDS": "connect_timeout_seconds",
        "MOOTDX2_READ_TIMEOUT_SECONDS": "read_timeout_seconds",
        "MOOTDX2_MAX_RETRIES": "max_retries",
        "MOOTDX2_RETRY_BACKOFF_BASE_SECONDS": "retry_backoff_base_seconds",
        "MOOTDX2_CONNECTION_POOL_SIZE": "connection_pool_size",
        "MOOTDX2_MAX_CONCURRENT_REQUESTS": "max_concurrent_requests",
        "MOOTDX2_AUTO_SELECT_FASTEST_SERVER": "auto_select_fastest_server",
        "MOOTDX2_LOG_LEVEL": "log_level",
        "MOOTDX2_LOG_PATH": "log_path",
        "MOOTDX2_TDXDIR": "tdxdir",
    }

    for env_var, config_key in env_mappings.items():
        env_val = os.getenv(env_var)
        if env_val is not None:
            # 类型转换
            if config_key.startswith("auto_select_"):
                env_overrides[config_key] = env_val.lower() in ("true", "1", "yes")
            elif "timeout" in config_key or "retries" in config_key or "pool" in config_key or "concurrent" in config_key:
                try:
                    env_overrides[config_key] = int(env_val)
                except ValueError:
                    logger.warning(f"Invalid int value for {env_var}: {env_val}")
            else:
                env_overrides[config_key] = env_val

    # 合并默认配置
    config_data = DEFAULT_CONFIG.copy()

    # 加载配置文件
    if config_path:
        config_file = Path(config_path)
    else:
        # 默认查找 config/mootdx2.yaml
        config_file = Path("config/mootdx2.yaml")

    if config_file.exists():
        try:
            with open(config_file, encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}

            # 递归解析环境变量
            file_config = _resolve_env_vars(file_config)

            # 合并文件配置
            if "mootdx2" in file_config:
                config_data.update(file_config["mootdx2"])
            else:
                config_data.update(file_config)

            logger.info(f"Loaded config from {config_file}")
        except Exception as e:
            logger.warning(f"Failed to load config from {config_file}: {e}")

    # 应用环境变量覆盖
    config_data.update(env_overrides)

    # 如果有服务器列表的环境变量 (逗号分隔 host:port)
    env_servers = os.getenv("MOOTDX2_SERVERS")
    if env_servers:
        servers = []
        for sp in env_servers.split(","):
            sp = sp.strip()
            if ":" in sp:
                host, port = sp.rsplit(":", 1)
                servers.append({"host": host, "port": int(port)})
            else:
                servers.append({"host": sp, "port": 7709})
        config_data["servers"] = servers

    return MooTDX2Settings.from_dict(config_data)


def get_default_config_yaml() -> str:
    """生成默认配置的 YAML 格式（用于 config.example.yaml）"""
    return """# MooTDX2 Configuration Example
# 复制此文件为 config/mootdx2.yaml 并根据需要修改

mootdx2:
  # 行情服务器列表（支持多个，用于故障转移）
  servers:
    - host: "106.14.201.78"
      port: 7709
    - host: "112.74.214.42"
      port: 7709

  # 市场: std(标准/A股), hk(港股)
  market: "std"

  # 连接超时（秒）
  connect_timeout_seconds: 3

  # 读取超时（秒）
  read_timeout_seconds: 5

  # 最大重试次数
  max_retries: 3

  # 重试退避基数（秒）
  retry_backoff_base_seconds: 0.5

  # 连接池大小
  connection_pool_size: 5

  # 最大并发请求数
  max_concurrent_requests: 10

  # 自动选择最快服务器
  auto_select_fastest_server: true

  # 日志级别: DEBUG, INFO, WARNING, ERROR
  log_level: "INFO"

  # 日志路径
  log_path: "./logs/mootdx2.log"

# 环境变量覆盖示例:
# MOOTDX2_CONNECT_TIMEOUT_SECONDS=5
# MOOTDX2_LOG_LEVEL=DEBUG
# MOOTDX2_SERVERS=host1:7709,host2:7709
"""
