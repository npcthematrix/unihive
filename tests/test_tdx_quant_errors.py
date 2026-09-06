"""tests/test_tdx_quant_errors.py — TdxQuant 错误翻译层测试。"""
import asyncio

from src.tdx_quant_errors import (
    TdxQuantError,
    TdxQuantErrorType,
    translate_errorid,
    classify_exception,
)


class TestTranslateErrorId:
    def test_errorid_0_is_success(self):
        result = {"ErrorId": "0", "Data": {"Now": "10.5"}}
        err = translate_errorid(result)
        assert err is None

    def test_errorid_missing_treated_as_success(self):
        result = {"Data": {"Now": "10.5"}}
        err = translate_errorid(result)
        assert err is None

    def test_errorid_6_marks_disconnect(self):
        result = {"ErrorId": "6", "ErrMsg": "disconnected"}
        err = translate_errorid(result)
        assert err is not None
        assert err.error_type == TdxQuantErrorType.DISCONNECTED
        assert err.recoverable is True

    def test_errorid_7_marks_disconnect(self):
        result = {"ErrorId": "7"}
        err = translate_errorid(result)
        assert err.error_type == TdxQuantErrorType.DISCONNECTED

    def test_errorid_12_strategy_exists(self):
        result = {"ErrorId": "12", "ErrMsg": "strategy exists"}
        err = translate_errorid(result)
        assert err.error_type == TdxQuantErrorType.STRATEGY_EXISTS
        assert err.recoverable is False

    def test_errorid_unknown_falls_back(self):
        result = {"ErrorId": "99", "ErrMsg": "weird"}
        err = translate_errorid(result)
        assert err.error_type == TdxQuantErrorType.UNKNOWN
        assert "99" in err.message


class TestClassifyException:
    def test_module_not_found_classifies_as_init_failed(self):
        err = classify_exception(ModuleNotFoundError("tqcenter"))
        assert err.error_type == TdxQuantErrorType.INIT_FAILED

    def test_file_not_found_classifies_as_init_failed(self):
        err = classify_exception(FileNotFoundError("tqcenter.py"))
        assert err.error_type == TdxQuantErrorType.INIT_FAILED

    def test_timeout_classifies_as_timeout(self):
        err = classify_exception(asyncio.TimeoutError())
        assert err.error_type == TdxQuantErrorType.TIMEOUT

    def test_connection_error_classifies_as_unavailable(self):
        err = classify_exception(ConnectionError("refused"))
        assert err.error_type == TdxQuantErrorType.UPSTREAM_UNAVAILABLE

    def test_os_error_with_network_keyword_classifies_as_unavailable(self):
        err = classify_exception(OSError("connection refused"))
        assert err.error_type == TdxQuantErrorType.UPSTREAM_UNAVAILABLE

    def test_generic_exception_falls_back_to_unknown(self):
        err = classify_exception(ValueError("boom"))
        assert err.error_type == TdxQuantErrorType.UNKNOWN
