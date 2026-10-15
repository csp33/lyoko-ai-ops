"""Unit tests for live incident status formatting."""

from lyoko.application.incidents.status import IncidentStatusFormatter

format_diagnosing_status = IncidentStatusFormatter.format_diagnosing_status


def test_format_diagnosing_status_alert():
    state = {
        "alert_name": "KubePodCrashLooping",
        "labels": {"namespace": "media", "pod": "radarr-0"},
    }
    status = format_diagnosing_status(state)
    assert "⏳ *[LYOKO Incident in Progress]*" in status
    assert "KubePodCrashLooping" in status
    assert "namespace=media, pod=radarr-0" in status


def test_format_diagnosing_status_message():
    state = {
        "text": "Why is radarr pod crashing constantly?",
        "event_type": "message",
    }
    status = format_diagnosing_status(state)
    assert "⏳ *[LYOKO Incident in Progress]*" in status
    assert "Why is radarr pod crashing constantly?" in status
