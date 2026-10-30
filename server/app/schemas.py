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
    is_favorite: bool = False
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
    attachment_ids: list[int] = []


class SessionUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    agent: str | None = None  # 바꾸면 그 AI CLI로 새 세션을 열고, 이전 대화 요약을 넘겨 이어 간다


class AttachmentResponse(ORM):
    id: int
    project_id: int
    filename: str
    size: int
    sha256: str
    created_at: datetime


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
    updated_at: datetime | None = None


# ---------- 보고서 (AI가 새로 쓴 종합 보고서) ----------
class ReportCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1)  # markdown. 결과는 한 줄에 ![제목](artifact:<id>)
    flow_id: int | None = None


class ReportResponse(ORM):
    id: int
    project_id: int
    flow_id: int | None
    title: str
    body: str
    created_at: datetime
    new_results: int = 0  # 이 판을 쓴 뒤 새로 생기거나 바뀐 결과 수(다시 쓸지 판단)


class ReportSummary(ORM):
    id: int
    title: str
    created_at: datetime


# ---------- Study ----------
class Variable(BaseModel):
    name: str
    lower: float
    upper: float


class Objective(BaseModel):
    name: str
    goal: Literal["min", "max"] = "min"


class Constraint(BaseModel):
    name: str  # results.json 키
    upper: float | None = None
    lower: float | None = None


class OutputDef(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)  # results.json 키
    note: str = ""  # 화면에 보일 설명(예: "바닥 전달력 [N]")


class StudyCreate(BaseModel):
    name: str
    variables: list[Variable]
    objectives: list[Objective] = []
    constraints: list[Constraint] = []
    outputs: list[OutputDef] = []
    adapter: str = "mock"
    adapter_config: dict[str, Any] = {}


class OutputAddRequest(BaseModel):
    """Output 추가: 목록에 넣고, post(추출 단계)를 이후 케이스에 붙이고, 이미 해석한 케이스에서도 추출한다."""
    outputs: list[OutputDef] = Field(..., min_length=1)
    post: dict[str, Any] | list[dict[str, Any]] | None = None  # {"tool", "script", "args"} — script 어댑터일 때
    extract_existing: bool = True


class StudyResponse(ORM):
    id: int
    project_id: int
    name: str
    variables: list[dict[str, Any]]
    objectives: list[dict[str, Any]]
    constraints: list[dict[str, Any]] | None = None
    outputs: list[dict[str, Any]] | None = None
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
    fields: list[str] | None = None
    archive: dict[str, Any] | None = None
    error: str | None


class StudyStatus(BaseModel):
    study: StudyResponse
    counts: dict[str, int]
    best: CaseResponse | None
    running: bool = False
    extracting: list[int] = []  # Output을 추출하는 중인 케이스 id


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
    version: int | None = None
    created_by: int | None = None
    deployed_at: datetime | None = None
    field_models: dict[str, int] | None = None
    created_at: datetime


class SimulatorItem(BaseModel):
    """내 Simulator·공유받은 Simulator 목록의 한 줄. 링크로 받은 것은 프로젝트 정보를 보이지 않는다."""
    study_id: int
    study_name: str
    model_id: int
    version: int | None
    outputs: list[str]
    n_train: int
    url: str
    source: str  # mine | project | link
    status: str = "active"  # active | stopped | expired
    project_id: int | None = None
    project_name: str | None = None
    shared_by: str | None = None
    note: str = ""
    share_id: int | None = None
    updated_at: datetime | None = None


class SimulatorList(BaseModel):
    mine: list[SimulatorItem]
    shared: list[SimulatorItem]


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


class AgentSettings(BaseModel):
    auto_update: bool


class DevTestAllow(BaseModel):
    allow: bool


class DevTestPermission(BaseModel):
    allow: bool
    until: datetime | None = None


class DevTestRun(BaseModel):
    op: Literal["info", "python", "tool", "logs", "fetch"]
    user_id: int | None = None  # 시험할 PC의 계정. 허용한 PC가 하나면 비워도 된다
    script: str | None = None
    filename: str | None = None
    tool: str | None = None
    args: list[str] | None = None
    files: dict[str, str] | None = None  # 시험 폴더에 넣을 파일 {이름: base64}
    path: str | None = None
    lines: int | None = None
    timeout_sec: int = Field(600, ge=1, le=7200)


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


class LocalOpen(BaseModel):
    path: str = ""  # 프로젝트 작업 폴더 기준 상대 경로 (비우면 프로젝트 폴더)
    with_viewer: bool = False  # True면 결과 파일을 등록된 CAE 뷰어로 연다


class LocalFolder(BaseModel):
    path: str | None = Field(None, max_length=1000)  # 내 PC의 절대 경로. None이면 기본 위치(작업 공간 아래)


class FolderPick(BaseModel):
    initial: str | None = Field(None, max_length=1000)
    title: str | None = Field(None, max_length=100)


class ToolPath(BaseModel):
    """CAD/CAE 툴 실행 파일을 사용자가 직접 정할 때(빈 값이면 자동으로 찾은 것으로 되돌린다)."""
    path: str | None = Field(None, max_length=1000)


class FieldModelResponse(ORM):
    id: int
    study_id: int
    field: str
    n_nodes: int
    n_train: int
    shape_varies: bool
    info: dict[str, Any]
    created_at: datetime


class TrainFieldRequest(BaseModel):
    field: str
    flow_id: int | None = None
    node_id: str | None = None


class DatasetCreate(BaseModel):
    name: str = ""
    targets: list[str] | None = None  # 비우면 케이스에 있는 표준 출력 전부(변위·Mises·온도 등)
    frames: str | int = "last"  # last | all | 등간격 프레임 수
    step: str | None = None
    flow_id: int | None = None
    node_id: str | None = None


class DatasetResponse(ORM):
    id: int
    study_id: int
    name: str
    spec: dict[str, Any]
    status: str
    pending: list[int]
    excluded: list[dict[str, Any]]
    n_samples: int
    splits: dict[str, int]
    targets: list[str]
    inputs: list[str]
    inputs_dropped: list[dict[str, Any]]
    units_known: bool
    same_mesh: bool
    size: int
    error: str | None
    created_at: datetime
