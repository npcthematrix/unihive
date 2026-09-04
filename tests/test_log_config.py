"""log_config 单元测试"""
import json
import logging
import sys

import pytest

from src.log_config import JsonFormatter, configure_logging


@pytest.fixture
def restore_root_logger():
    """configure_logging 会改全局 root logger，测试后必须还原，否则污染其他测试。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def _make_record(**kwargs) -> logging.LogRecord:
    defaults = dict(
        name="src.gateway_server",
        level=logging.INFO,
        pathname="gateway_server.py",
        lineno=42,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    defaults.update(kwargs)
    return logging.LogRecord(**defaults)


class TestJsonFormatter:
    def test_emits_parseable_json_with_core_fields(self):
        out = JsonFormatter().format(_make_record())
        payload = json.loads(out)
        assert payload["level"] == "INFO"
        assert payload["logger"] == "src.gateway_server"
        assert payload["message"] == "hello world"
        assert payload["ts"].endswith("Z")

    def test_single_line_output(self):
        out = JsonFormatter().format(_make_record(msg="line1\nline2", args=()))
        assert "\n" not in out
        assert json.loads(out)["message"] == "line1\nline2"

    def test_includes_exception_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError:
            record = _make_record(level=logging.ERROR, exc_info=sys.exc_info())
        payload = json.loads(JsonFormatter().format(record))
        assert "ValueError: boom" in payload["exception"]

    def test_includes_extra_fields(self):
        record = _make_record()
        record.tool = "tdx_call"
        record.upstream = "tdx_local"
        payload = json.loads(JsonFormatter().format(record))
        assert payload["tool"] == "tdx_call"
        assert payload["upstream"] == "tdx_local"

    def test_non_serializable_extra_falls_back_to_repr(self):
        record = _make_record()
        record.conn = object()
        payload = json.loads(JsonFormatter().format(record))
        assert "object object at" in payload["conn"]

    def test_does_not_leak_internal_record_attrs(self):
        payload = json.loads(JsonFormatter().format(_make_record()))
        for internal in ("msg", "args", "levelno", "pathname", "relativeCreated"):
            assert internal not in payload


class TestConfigureLogging:
    def test_stdio_keeps_stdout_and_stderr_clean(self, tmp_path, restore_root_logger):
        configure_logging("stdio", log_path=tmp_path / "gateway.log")
        streams = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert streams == []

    def test_http_adds_stderr_handler(self, tmp_path, restore_root_logger):
        configure_logging("http", log_path=tmp_path / "gateway.log")
        streams = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(streams) == 1
        assert streams[0].stream is sys.stderr

    def test_file_handler_writes_json(self, tmp_path, restore_root_logger):
        log_path = tmp_path / "gateway.log"
        configure_logging("stdio", log_path=log_path)
        logging.getLogger("src.test").info("registered %d tools", 147)
        for h in logging.getLogger().handlers:
            h.flush()
        payload = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert payload["message"] == "registered 147 tools"
        assert payload["logger"] == "src.test"

    def test_creates_missing_log_directory(self, tmp_path, restore_root_logger):
        log_path = tmp_path / "nested" / "dir" / "gateway.log"
        configure_logging("stdio", log_path=log_path)
        assert log_path.parent.is_dir()

    def test_replaces_preexisting_handlers(self, tmp_path, restore_root_logger):
        root = logging.getLogger()
        stray = logging.StreamHandler(sys.stdout)
        root.addHandler(stray)
        configure_logging("stdio", log_path=tmp_path / "gateway.log")
        assert stray not in root.handlers
