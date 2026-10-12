"""Unit tests for ToolErrorDetector in application/safety layer."""

from lyoko.application.safety.error_detector import ToolErrorDetector


def test_detects_common_error_markers():
    detector = ToolErrorDetector()
    assert detector.is_tool_error("Error executing tool kubectl_get: connection refused")
    assert detector.is_tool_error("{'is_error': True, 'msg': 'failed'}")
    assert detector.is_tool_error('{"is_error": true}')
    assert detector.is_tool_error("Namespace kube-system is forbidden: mutation blocked")
    assert detector.is_tool_error("Permission denied to modify resource")
    assert detector.is_tool_error("Tool not found on any upstream gateway")


def test_accepts_successful_outputs():
    detector = ToolErrorDetector()
    assert not detector.is_tool_error("Pod nginx-123 is Running")
    assert not detector.is_tool_error('{"status": "ok", "items": []}')
    assert not detector.is_tool_error("Deployment patched successfully")


def test_custom_markers():
    detector = ToolErrorDetector(error_markers=("custom_fail",))
    assert detector.is_tool_error("Operation resulted in custom_fail")
    assert not detector.is_tool_error("error executing tool")
