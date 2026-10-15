"""PostgreSQL repository for Agent Memory and Lessons Learned using pgvector."""

import logging
from pathlib import Path

from lyoko.domain.interfaces.memory import MemoryRepositoryInterface
from lyoko.domain.models.memory import MemoryEntry, MemoryQueryResult
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("lyoko.db.memory")


class PostgresMemoryRepository(MemoryRepositoryInterface):
    """Repository managing persistent semantic memory and operator feedback."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    @staticmethod
    def run_migrations(alembic_ini_path: str | Path | None = None) -> None:
        """Execute Alembic migrations to ensure database schema is up-to-date."""
        from alembic import command
        from alembic.config import Config

        if alembic_ini_path is None:
            candidates = [
                Path(__file__).resolve().parents[4] / "alembic.ini",
                Path("/app/packages/agents/lyoko/alembic.ini"),
                Path("/app/alembic.ini"),
            ]
            for cand in candidates:
                if cand.exists():
                    alembic_ini_path = cand
                    break
            else:
                alembic_ini_path = Path(__file__).resolve().parents[4] / "alembic.ini"
        alembic_ini_path = Path(alembic_ini_path)

        if not alembic_ini_path.exists():
            logger.warning(
                f"Alembic config not found at {alembic_ini_path}. Skipping automatic migrations."
            )
            return

        logger.info(f"Applying Alembic migrations from {alembic_ini_path}...")
        alembic_cfg = Config(str(alembic_ini_path))
        alembic_cfg.set_main_option("script_location", str(alembic_ini_path.parent / "migrations"))
        command.upgrade(alembic_cfg, "head")
        logger.info("Alembic migrations applied successfully.")

    async def save_memory(
        self,
        entry: MemoryEntry,
        embedding: list[float] | None = None,
    ) -> int:
        """Persist a memory entry with optional vector embedding."""
        embedding_str = str(embedding) if embedding is not None else None

        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO agent_memory (
                    namespace,
                    service_name,
                    alert_name,
                    incident_pattern,
                    operator_feedback,
                    action_rule,
                    embedding
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                RETURNING id;
                """,
                (
                    entry.namespace,
                    entry.service_name,
                    entry.alert_name,
                    entry.incident_pattern,
                    entry.operator_feedback,
                    entry.action_rule,
                    embedding_str,
                ),
            )
            row = await cur.fetchone()
            memory_id = row["id"]
            logger.info(
                f"Saved memory {memory_id} for service {entry.service_name} in namespace {entry.namespace}"
            )
            return memory_id

    async def search_memories(
        self,
        query_embedding: list[float] | None = None,
        namespace: str | None = None,
        service_name: str | None = None,
        limit: int = 3,
        min_similarity: float = 0.0,
    ) -> list[MemoryQueryResult]:
        """Search memories using vector cosine similarity or metadata filtering."""
        results: list[MemoryQueryResult] = []

        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            if query_embedding is not None:
                embedding_str = str(query_embedding)
                query = """
                    SELECT id, namespace, service_name, alert_name, incident_pattern,
                           operator_feedback, action_rule, created_at,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM agent_memory
                    WHERE (%s::text IS NULL OR namespace = %s)
                      AND (%s::text IS NULL OR service_name = %s)
                      AND embedding IS NOT NULL
                      AND (1 - (embedding <=> %s::vector)) >= %s
                    ORDER BY embedding <=> %s::vector ASC
                    LIMIT %s;
                """
                params = (
                    embedding_str,
                    namespace,
                    namespace,
                    service_name,
                    service_name,
                    embedding_str,
                    min_similarity,
                    embedding_str,
                    limit,
                )
            else:
                query = """
                    SELECT id, namespace, service_name, alert_name, incident_pattern,
                           operator_feedback, action_rule, created_at,
                           1.0 AS similarity
                    FROM agent_memory
                    WHERE (%s::text IS NULL OR namespace = %s)
                      AND (%s::text IS NULL OR service_name = %s)
                    ORDER BY created_at DESC
                    LIMIT %s;
                """
                params = (
                    namespace,
                    namespace,
                    service_name,
                    service_name,
                    limit,
                )

            await cur.execute(query, params)
            rows = await cur.fetchall()

            for row in rows:
                entry = MemoryEntry(
                    id=row["id"],
                    namespace=row["namespace"],
                    service_name=row["service_name"],
                    alert_name=row["alert_name"],
                    incident_pattern=row["incident_pattern"],
                    operator_feedback=row["operator_feedback"],
                    action_rule=row["action_rule"],
                    created_at=row["created_at"],
                )
                results.append(
                    MemoryQueryResult(
                        memory=entry,
                        similarity=float(row.get("similarity", 1.0)),
                    )
                )

        return results

    async def list_recent_memories(self, limit: int = 50) -> list[MemoryEntry]:
        """List recent memories for inspection."""
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, namespace, service_name, alert_name, incident_pattern,
                       operator_feedback, action_rule, created_at
                FROM agent_memory
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = await cur.fetchall()
            return [
                MemoryEntry(
                    id=row["id"],
                    namespace=row["namespace"],
                    service_name=row["service_name"],
                    alert_name=row["alert_name"],
                    incident_pattern=row["incident_pattern"],
                    operator_feedback=row["operator_feedback"],
                    action_rule=row["action_rule"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    async def delete_memory(self, memory_id: int) -> bool:
        """Delete a memory entry by its primary key ID."""
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                DELETE FROM agent_memory
                WHERE id = %s
                RETURNING id;
                """,
                (memory_id,),
            )
            row = await cur.fetchone()
            if row:
                logger.info(f"Deleted memory {memory_id}")
                return True
            logger.info(f"Memory {memory_id} not found for deletion")
            return False
