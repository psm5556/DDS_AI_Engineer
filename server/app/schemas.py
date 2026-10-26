from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- User / Auth ----------
class UserResponse(ORM):
    id: int
    username: str
    display_name: str
    is_admin: bool
    is_active: bool


class UserAdminUpdate(BaseModel):
    display_name: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None
    pin: str | None = None  # 관리자가 PIN 초기화 (6자리 숫자)


class PinChange(BaseModel):
    current_pin: str
    new_pin: str


class LoginResponse(BaseModel):
    access_token: str
    user: UserResponse


class ApiTokenCreate(BaseModel):
    name: str = "내 PC"


class ApiTokenResponse(ORM):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None


class ApiTokenCreated(ApiTokenResponse):
    token: str  # 발급 시 한 번만 보여준다


# ---------- Project ----------
class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    version: int


class ProjectBase(ORM):
    id: int
    name: str
    description: str
    owner_id: int
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectMemberResponse(BaseModel):
    user_id: int
    username: str
    display_name: str
    role: str


class ProjectResponse(ProjectBase):
    members: list[ProjectMemberResponse]
    user_role: str | None
    flow_status: str | None = None
    flow_title: str | None = None


class MemberUpsert(BaseModel):
    role: Literal["master", "member"] = "member"


# ---------- Chat ----------
class ChatSessionCreate(BaseModel):
    agent: str | None = None  # None이면 내 PC Agent의 기본 에이전트


class ChatSessionResponse(ORM):
    id: int
    project_id: int
    user_id: int
    title: str
    agent: str
    agent_session_id: str | None
    created_at: datetime


class ChatMessageResponse(ORM):
    id: int
    session_id: int
    role: str
    content: str
    meta: dict[str, Any] | None
    created_at: datetime


class PromptRequest(BaseModel):
    text: str


class AgentInstall(BaseModel):
    agent: str


class PermissionReply(BaseModel):
    request_id: str
    option_id: str | None = None  # None이면 취소


# ---------- Flow ----------
NodeStatus = Literal["pending", "in_progress", "complete", "failed", "skipped"]


class FlowNode(BaseModel):
    id: str
    label: str
    type: str = "task"  # cad | pre | solve | post | doe | train | optimize | deploy | task
    tool: str | None = None  # nx | abaqus | mock | ...
    status: NodeStatus = "pending"
    detail: str = ""


class FlowEdge(BaseModel):
    source: str
    target: str
    label: str | None = None


class FlowCreate(BaseModel):
    title: str
    goal: str = ""
    nodes: list[FlowNode]
    edges: list[FlowEdge] = []


class FlowNodeUpdate(BaseModel):
    status: NodeStatus | None = None
    detail: str | None = None


class FlowStatusUpdate(BaseModel):
    status: Literal["review", "approved", "in_progress", "complete", "failed", "rejected"]
    version: int


class FlowResponse(ORM):
    id: int
    project_id: int
    title: str
    goal: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


# ---------- Artifact ----------
ArtifactKind = Literal["table", "scatter", "line", "bar", "parallel", "markdown", "image"]


class ArtifactCreate(BaseModel):
    kind: ArtifactKind
    title: str
    payload: dict[str, Any]
    flow_id: int | None = None
    node_id: str | None = None


class ArtifactResponse(ORM):
    id: int
    project_id: int
    flow_id: int | None
    node_id: str | None
    kind: str
    title: str
    payload: dict[str, Any]
    created_at: datetime


# ---------- Study ----------
class Variable(BaseModel):
    name: str
    lower: float
    upper: float


class Objective(BaseModel):
    name: str
    goal: Literal["min", "max"] = "min"


class StudyCreate(BaseModel):
    name: str
    variables: list[Variable]
    objectives: list[Objective] = []
    adapter: str = "mock"
    adapter_config: dict[str, Any] = {}


class StudyResponse(ORM):
    id: int
    project_id: int
    name: str
    variables: list[dict[str, Any]]
    objectives: list[dict[str, Any]]
    adapter: str
    adapter_config: dict[str, Any]
    version: int
    created_at: datetime


class CaseResponse(ORM):
    id: int
    study_id: int
    params: dict[str, Any]
    results: dict[str, Any]
    status: str
    source: str
    run_dir: str | None
    run_by: int | None
    images: list[str] | None
    error: str | None


class StudyStatus(BaseModel):
    study: StudyResponse
    counts: dict[str, int]
    best: CaseResponse | None
    running: bool = False


class DoeRequest(BaseModel):
    method: Literal["lhs", "sobol", "random"] = "lhs"
    n: int = Field(20, ge=1, le=10000)
    seed: int | None = None
    flow_id: int | None = None
    node_id: str | None = None


class RunRequest(BaseModel):
    case_ids: list[int] | None = None  # None이면 pending 전체
    dry_run: bool = False


class TrainRequest(BaseModel):
    outputs: list[str] | None = None  # None이면 objectives 전체
    algo: Literal["gp"] = "gp"
    flow_id: int | None = None
    node_id: str | None = None


class SuggestRequest(BaseModel):
    mode: Literal["bo", "al"] = "bo"
    n: int = Field(1, ge=1, le=50)
    model_id: int | None = None  # None이면 최신 모델
    flow_id: int | None = None
    node_id: str | None = None


class ModelResponse(ORM):
    id: int
    study_id: int
    name: str
    algo: str
    outputs: list[str]
    metrics: dict[str, Any]
    n_train: int
    deployed: bool
    created_at: datetime


class PredictRequest(BaseModel):
    params: dict[str, float]


class PredictBatchRequest(BaseModel):
    points: list[dict[str, float]] = Field(..., max_length=2000)


class PredictBatchResponse(BaseModel):
    mean: dict[str, list[float]]
    std: dict[str, list[float]]


class PredictResponse(BaseModel):
    mean: dict[str, float]
    std: dict[str, float]


# ---------- PC Agent 배포·모니터링 ----------
class AgentReleaseResponse(ORM):
    id: int
    version: str
    filename: str
    sha256: str
    size: int
    notes: str
    is_latest: bool
    created_at: datetime


class AgentInstanceResponse(ORM):
    id: int
    user_id: int
    hostname: str
    version: str | None
    platform: str | None
    agents: list[dict[str, Any]] | None
    status: dict[str, Any] | None
    connected: bool
    last_seen: datetime
    connected_at: datetime | None
    user_display_name: str | None = None
    outdated: bool = False


class ShareCreate(BaseModel):
    note: str = ""
    expires_days: int | None = Field(None, ge=1, le=3650)


class ShareResponse(ORM):
    id: int
    token: str
    model_id: int
    note: str
    enabled: bool
    uses: int
    expires_at: datetime | None
    created_at: datetime
