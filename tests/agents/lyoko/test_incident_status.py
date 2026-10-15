"""Unit tests for live incident status formatting."""

from lyoko.application.incidents.status import IncidentStatusFormatter

format_diagnosing_status = IncidentStatusFormatter.format_diagnosing_status
format_remediating_status = IncidentStatusFormatter.format_remediating_status
format_verifying_status = IncidentStatusFormatter.format_verifying_status


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


def test_format_remediating_status_actionable():
    state = {"alert_name": "KubePodCrashLooping"}
    status = format_remediating_status(
        state,
        root_cause="Memory limit too low",
        requires_escalation=False,
    )
    assert "• *Root Cause:* Memory limit too low" in status
    assert "🛠️ *Phase: Remediate*" in status


def test_format_remediating_status_escalation():
    state = {"alert_name": "KubePodCrashLooping"}
    status = format_remediating_status(
        state,
        root_cause="Database credentials invalid",
        requires_escalation=True,
    )
    assert "• *Root Cause:* Database credentials invalid" in status
    assert "⚠️ *Status:* _Escalation required" in status


def test_format_verifying_status():
    state = {"alert_name": "KubePodCrashLooping"}
    status = format_verifying_status(
        state,
        root_cause="Memory limit too low",
        action_taken="Scaled memory to 1Gi",
    )
    assert "• *Root Cause:* Memory limit too low" in status
    assert "• *Action Taken:* Scaled memory to 1Gi" in status
    assert "🔬 *Phase: Verify*" in status
