"""获取网关端口的工具函数"""
import os
import sys
import yaml


def get_gateway_port(config_yaml_path: str, upstreams_yaml_path: str, default: int = 18080) -> int:
    """从配置文件中读取网关端口"""

    def _gw_port(path: str) -> int | None:
        if not path or not os.path.exists(path):
            return None
        with open(path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        gw = cfg.get('gateway') or {}
        p = gw.get('port')
        return int(p) if p is not None else None

    # 优先 config.yaml (bootstrap 段), 再回落 upstreams.yaml
    for path in (config_yaml_path, upstreams_yaml_path):
        try:
            port = _gw_port(path)
            if port and port > 0:
                return port
        except Exception:
            pass

    return default


def main() -> int:
    """入口函数"""
    # 确保工作目录是项目根
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    if os.getcwd() != project_root:
        os.chdir(project_root)
    config_path = os.path.join(project_root, 'config', 'config.yaml')
    upstreams_path = os.path.join(project_root, 'config', 'upstreams.yaml')
    return get_gateway_port(config_path, upstreams_path)


if __name__ == '__main__':
    print(main())
