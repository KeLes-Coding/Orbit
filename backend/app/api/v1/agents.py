from fastapi import APIRouter

from app.services.langgraph_runtime.agent_catalog import list_builtin_agent_descriptors

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/descriptors")
async def list_agent_descriptors() -> list[dict]:
    return list_builtin_agent_descriptors()
