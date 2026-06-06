from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent_artifact import AgentArtifact
from app.models.agent_run import AgentRun
from app.models.message import Message


class AgentArtifactRepository:
    # AgentArtifactRepository 负责把 agent 执行产物从 message metadata 中沉淀为可查询事实。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def persist_message_artifacts(
        self,
        *,
        message: Message,
        response_metadata: dict[str, Any],
        status: str,
        error: str | None = None,
    ) -> AgentRun | None:
        agent_type = response_metadata.get("agent_type")
        if not isinstance(agent_type, str) or not agent_type:
            return None

        existing = await self.get_run_by_message(message_id=message.id)
        if existing is not None:
            return existing

        run = AgentRun(
            conversation_id=message.conversation_id,
            message_id=message.id,
            agent_type=agent_type,
            status=status,
            sandbox_id=response_metadata.get("sandbox_id") if isinstance(response_metadata.get("sandbox_id"), str) else None,
            error=error,
            completed_at=datetime.now(timezone.utc),
        )
        self.session.add(run)
        await self.session.flush()

        artifacts = response_metadata.get("artifacts")
        if isinstance(artifacts, list):
            for item in artifacts:
                if not isinstance(item, dict):
                    continue
                artifact = self._build_artifact(run_id=run.id, item=item)
                if artifact is not None:
                    self.session.add(artifact)

        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def get_run_by_message(self, *, message_id: UUID) -> AgentRun | None:
        statement = (
            select(AgentRun)
            .where(AgentRun.message_id == message_id)
            .options(selectinload(AgentRun.artifacts))
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_artifacts_for_message(self, *, message_id: UUID) -> list[AgentArtifact]:
        run = await self.get_run_by_message(message_id=message_id)
        if run is None:
            return []
        return sorted(run.artifacts, key=lambda item: item.created_at)

    @staticmethod
    def _build_artifact(*, run_id: UUID, item: dict[str, Any]) -> AgentArtifact | None:
        artifact_type = item.get("type")
        name = item.get("name")
        path = item.get("path")
        if not all(isinstance(value, str) and value for value in (artifact_type, name, path)):
            return None

        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        preview = item.get("preview")
        preview_payload = preview if isinstance(preview, dict) else {}
        preview_path = item.get("preview_path")
        if not isinstance(preview_path, str):
            preview_path = item.get("preview") if isinstance(item.get("preview"), str) else None

        return AgentArtifact(
            run_id=run_id,
            type=artifact_type,
            name=name,
            path=path,
            preview_path=preview_path,
            metadata_=metadata,
            preview=preview_payload,
        )
