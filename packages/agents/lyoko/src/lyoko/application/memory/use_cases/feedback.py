"""Use case for recording human operator feedback into semantic memory."""

import logging
import re

from lyoko.domain.interfaces.embeddings import EmbeddingsServiceInterface
from lyoko.domain.interfaces.memory import MemoryRepositoryInterface
from lyoko.domain.models.memory import FeedbackRequest, MemoryEntry

logger = logging.getLogger("lyoko.application.memory.use_cases.feedback")


class RecordFeedbackUseCase:
    """Encapsulates the workflow of ingesting and persisting operator feedback rules."""

    def __init__(
        self,
        memory_repository: MemoryRepositoryInterface | None,
        embeddings_service: EmbeddingsServiceInterface | None = None,
    ) -> None:
        self.memory_repository = memory_repository
        self.embeddings_service = embeddings_service

    async def execute_from_request(self, payload: FeedbackRequest) -> int:
        """Persist feedback received from an API request and return the new memory ID."""
        if not self.memory_repository:
            raise RuntimeError("PostgreSQL memory repository is not available or configured.")

        embedding = None
        if self.embeddings_service:
            text_to_embed = (
                f"{payload.alert_name or ''} {payload.service_name} {payload.namespace} "
                f"{payload.incident_pattern} {payload.operator_feedback}"
            )
            embedding = await self.embeddings_service.embed_text(text_to_embed)

        entry = MemoryEntry(
            namespace=payload.namespace,
            service_name=payload.service_name,
            alert_name=payload.alert_name,
            incident_pattern=payload.incident_pattern,
            operator_feedback=payload.operator_feedback,
            action_rule=payload.action_rule,
        )

        return await self.memory_repository.save_memory(entry, embedding=embedding)

    async def execute_from_chat_command(
        self,
        feedback_content: str,
        reply_to_text: str | None = None,
    ) -> tuple[int, str, str]:
        """Parse chat context, persist feedback, and return (memory_id, service_name, namespace)."""
        if not self.memory_repository:
            raise RuntimeError("PostgreSQL memory repository is not available.")

        namespace = "default"
        service_name = "general"
        alert_name = "OperatorRule"
        incident_pattern = feedback_content

        if reply_to_text:
            ns_match = re.search(
                r"Namespace:?\s*`?([a-zA-Z0-9_\-]+)`?", reply_to_text, re.IGNORECASE
            )
            pod_match = re.search(r"Pod:?\s*`?([a-zA-Z0-9_\-]+)`?", reply_to_text, re.IGNORECASE)
            alert_match = re.search(
                r"Alert:?\s*`?([a-zA-Z0-9_\-]+)`?", reply_to_text, re.IGNORECASE
            )

            if ns_match:
                namespace = ns_match.group(1)
            if pod_match:
                pod_name = pod_match.group(1)
                service_name = pod_name.rsplit("-", 2)[0]
            if alert_match:
                alert_name = alert_match.group(1)
            incident_pattern = f"Context: {reply_to_text[:200]}..."

        embedding = None
        if self.embeddings_service:
            text_to_embed = (
                f"{alert_name} {service_name} {namespace} {incident_pattern} {feedback_content}"
            )
            embedding = await self.embeddings_service.embed_text(text_to_embed)

        entry = MemoryEntry(
            namespace=namespace,
            service_name=service_name,
            alert_name=alert_name,
            incident_pattern=incident_pattern,
            operator_feedback=feedback_content,
            action_rule=feedback_content,
        )

        memory_id = await self.memory_repository.save_memory(entry, embedding=embedding)
        return memory_id, service_name, namespace
