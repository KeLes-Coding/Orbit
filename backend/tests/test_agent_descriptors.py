from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.agents import router as agents_router
from app.services.langgraph_runtime.agents.data_workspace_agent.adapter import DataWorkspaceAgentAdapter
from app.services.langgraph_runtime.agent_catalog import AgentCatalog, list_builtin_agent_descriptors
from app.services.langgraph_runtime.core.agent_registry import AgentRegistry
from app.services.langgraph_runtime.middleware import AgentMiddleware
from app.services.tools import OrbitToolRuntime


def test_builtin_agent_descriptors_include_public_metadata():
    descriptors = list_builtin_agent_descriptors()
    by_type = {descriptor["agent_type"]: descriptor for descriptor in descriptors}

    assert "web_agent" in by_type
    assert "data_workspace_agent" in by_type
    assert by_type["web_agent"]["display_name"] == "Web Agent"
    assert "web_search" in by_type["web_agent"]["capabilities"]
    assert by_type["data_workspace_agent"]["default_budget"]["timeout_seconds"] > 0


def test_registry_registers_by_descriptor():
    registry = AgentRegistry()
    agent = DataWorkspaceAgentAdapter()

    registry.register(agent)

    assert registry.resolve("data_workspace_agent") is agent
    assert registry.descriptors()[0].agent_type == "data_workspace_agent"


def test_registry_resolves_skill_type_alias():
    registry = AgentRegistry()
    agent = DataWorkspaceAgentAdapter()

    registry.register(agent)

    assert registry.has("data_workspace")
    assert registry.resolve("data_workspace") is agent


def test_normalize_to_agent_type_accepts_skill_type_and_agent_type():
    assert AgentCatalog.normalize_to_agent_type("web_agent") == "web_agent"
    assert AgentCatalog.normalize_to_agent_type("web_research") == "web_agent"
    assert AgentCatalog.normalize_to_agent_type("data_workspace") == "data_workspace_agent"
    assert AgentCatalog.normalize_to_agent_type("unknown") is None
    assert AgentCatalog.normalize_to_agent_type(None) is None


def test_descriptor_public_dict_exposes_skill_fields():
    descriptors = list_builtin_agent_descriptors()
    by_type = {descriptor["agent_type"]: descriptor for descriptor in descriptors}

    web = by_type["web_agent"]
    data = by_type["data_workspace_agent"]

    assert web["skill_type"] == "web_research"
    assert web["execution_mode"] == "workflow"
    assert data["skill_type"] == "data_workspace"
    assert data["artifact_policy"]["produces_artifacts"] is True


def test_agent_descriptors_api():
    app = FastAPI()
    app.include_router(agents_router)
    client = TestClient(app)

    response = client.get("/agents/descriptors")

    assert response.status_code == 200
    agent_types = {item["agent_type"] for item in response.json()}
    assert {"web_agent", "data_workspace_agent"} <= agent_types


def test_builtin_catalog_registers_agents_from_middleware():
    async def fake_llm_invoke(*_args, **_kwargs):
        if False:
            yield None

    registry = AgentRegistry()
    middleware = AgentMiddleware(
        llm_invoke=fake_llm_invoke,
        tool_runtime=OrbitToolRuntime(),
    )

    AgentCatalog.builtins(middleware=middleware).register_into(registry)

    assert registry.has("web_agent")
    assert registry.has("data_workspace_agent")
