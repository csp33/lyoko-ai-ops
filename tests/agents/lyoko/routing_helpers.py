"""Routing and the two branches of the LYOKO graph: chat and incident."""

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from lyoko.domain.interfaces.mcp import ToolAuthorizer

SCALE_ARGS = {"name": "sonarr", "namespace": "media", "replicas": 2}


def _message(text: str = "scale sonarr to 2 replicas", thread_id: str | None = None) -> dict:
    return {
        "event_type": "message",
        "event_id": "chat-ab12cd34",
        "session_id": "telegram-42-1760000000",
        "chat_id": "42",
        "message_thread_id": thread_id,
        "text": text,
        "labels": {},
        "annotations": {},
    }


def _alert() -> dict:
    return {
        "event_type": "alert",
        "event_id": "incident-abc",
        "session_id": "incident-abc",
        "alert_name": "KubePodCrashLooping",
        "labels": {"alertname": "KubePodCrashLooping", "namespace": "media"},
        "annotations": {},
    }


def _scale(outcomes: list):
    async def chat(authorize: ToolAuthorizer) -> str:
        refusal = await authorize("resources_scale", SCALE_ARGS)
        outcomes.append(refusal)
        return "Scaled sonarr to 2." if refusal is None else "I could not scale it."

    return chat


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr("lyoko.application.workflow.graph.settings.verification_delay_seconds", 0)
    monkeypatch.setattr("lyoko.application.workflow.graph.settings.auto_approved_tools", [])


class _Recorder(BaseCallbackHandler):
    def __init__(self) -> None:
        self.chain_names: list[str] = []

    def on_chain_start(self, serialized, inputs, *, name=None, **kwargs):
        self.chain_names.append(name or "")
