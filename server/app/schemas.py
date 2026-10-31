from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


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
    uid: str | None = None  # PC Agent·MCP가 작업 폴더를 찾는 고유값
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
    # 보고서 틀(표지·맺음말): 파일(report.py)과 웹 화면이 같은 글을 쓰도록 서버가 만들어 준다
    project_name: str = ""
    doc_no: str = ""  # DDS-P0012-R003
    edition: int = 1  # 프로젝트 안에서 몇 번째 판인지
    issued_at: str = ""  # 발행 일시 'YYYY-MM-DD HH:MM'
    author: str = ""
    provenance: str = ""  # 표지의 발행처·작성 방식 한 줄
    colophon: str = ""  # 맺음말


class ReportSummary(ORM):
    id: int
    title: str
    created_at: datetime


# ---------- Study ----------
class Variable(BaseModel):
    name: str
    lower: FiniteFloat  # JSON의 NaN을 그대로 받으면 나중에 응답 직렬화가 깨진다
    upper: FiniteFloat
    unit: str | None = None
    note: str = ""  # 화면과 문장에 쓸 이름과 단위. 예: "스프링 강성 [%]"


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


class StudyAdapterRequest(BaseModel):
    """해석 방법을 나중에 연결한다(불러온 데이터·DOE 계획만 있던 스터디). 대기 케이스는 그대로 이어서 돌릴 수 있다."""
    adapter: str
    adapter_config: dict[str, Any] = {}


class StudyCloneRequest(BaseModel):
    """변수·범위·출력·목적·해석 방법을 복사한 새 스터디를 만든다. 케이스·모델은 복사하지 않아 옛 결과는 그대로다."""
    name: str = ""
    project_id: int | None = None  # 비우면 같은 프로젝트. 다른 프로젝트로 옮길 때는 양쪽 멤버여야 한다
    variables: list[Variable] | None = None
    objectives: list[Objective] | None = None
    constraints: list[Constraint] | None = None
    outputs: list[OutputDef] | None = None
    adapter: str | None = None
    adapter_config: dict[str, Any] | None = None


class ImportColumn(BaseModel):
    name: str
    unit: str = ""
    note: str = ""
    lower: float | None = None  # 입력에만 쓴다. 비우면 데이터의 최소·최대
    upper: float | None = None


class StudyImportRequest(BaseModel):
    """이미 가진 입력·출력을 표째로 등록해 스터디를 만든다. 해석을 돌리지 않는다.
    행의 _row(파일의 행 번호)·_run_dir(원본 폴더, 기록만 한다)·_label(폴더 이름 등)은 값이 아니라 출처다."""
    name: str = ""
    source: str = ""  # 파일·폴더 이름. 화면의 '데이터 출처'에 보인다
    kind: Literal["table", "folders"] = "table"
    inputs: list[ImportColumn] = []
    outputs: list[ImportColumn] = []
    objectives: list[Objective] = []
    constraints: list[Constraint] = []
    rows: list[dict[str, Any]] = Field(default=[], max_length=200_000)
    fixed: dict[str, Any] = {}  # 모든 행에 같은 값(변수 아님). 나중에 해석을 연결하면 params에 함께 간다
    expects_fields: bool = False  # 3D 필드를 케이스별로 따로 올린다(스칼라 출력이 없어도 받는다)
    flow_id: int | None = None
    node_id: str | None = None


class CaseImportRequest(BaseModel):
    """있는 스터디에 행을 더한다. 범위 밖 값은 범위를 넓히고 알린다. 다시 학습하면 새 모델 버전이 된다."""
    rows: list[dict[str, Any]] = Field(default=[], max_length=200_000)
    outputs: list[ImportColumn] = []  # 새로 더할 출력 열
    source: str = ""
    expects_fields: bool = False
    flow_id: int | None = None
    node_id: str | None = None


class TableMapping(BaseModel):
    """표 파일 등록의 열 지정. 비워 두면 미리보기의 추측을 그대로 쓴다."""
    name: str = ""
    inputs: list[ImportColumn] | None = None
    outputs: list[ImportColumn] | None = None
    objectives: list[Objective] = []
    constraints: list[Constraint] = []
    fixed: dict[str, Any] = {}
    sheet: str | None = None
    header_row: int | None = None
    key: str | None = None  # 출력 파일을 따로 줄 때 잇는 열
    expects_fields: bool = False
    flow_id: int | None = None
    node_id: str | None = None


class TablePasteRequest(BaseModel):
    """붙여 넣은 표(엑셀 복사 글) 분석. 파일이 보안 프로그램으로 암호화된 회사의 기본 입구다."""
    text: str
    header_row: int | None = None


class TablePasteImport(TableMapping):
    """붙여 넣은 표 등록. outputs만 주면 나머지 숫자 열이 입력이 된다. 둘 다 비우면 미리보기의 추측대로 한다."""
    text: str


class TableJobCreate(TablePasteImport):
    """표로 Simulator: 등록·학습·배포를 서버 작업 하나로 돌린다. project_id가 없으면(첫 화면) 표를 받을 수 있을 때 프로젝트를 만든다."""
    project_id: int | None = None


class TableJobResponse(BaseModel):
    id: int
    project_id: int | None = None
    study_id: int | None = None
    model_id: int | None = None
    name: str
    status: Literal["running", "done", "failed"]
    phase: Literal["import", "train", "deploy", "done"]
    rows: int = 0
    excluded_count: int = 0
    reused: bool = False  # 같은 표로 만든 Simulator가 있어 그 현재 버전을 그대로 쓴다
    error: str | None = None
    seen: bool = False
    phase_sec: int = 0
    elapsed_sec: int = 0


class ImportReport(BaseModel):
    """검증 보고: 무엇을 등록했고 무엇을 왜 뺐는가."""
    study_id: int
    study_name: str
    reused: bool = False  # 같은 표를 다시 넣어 기존 스터디를 돌려줬다
    received: int = 0
    registered: int = 0  # 새로 생긴 케이스
    updated: int = 0  # 입력이 같은 기존 케이스에 새 출력을 더한 수
    filled_pending: int = 0  # 대기 중이던 DOE 케이스를 이 값으로 채운 수
    duplicates: int = 0
    excluded_count: int = 0
    excluded: list[dict[str, Any]] = []  # {row, reason, kind} 앞 100개
    excluded_by_reason: dict[str, int] = {}  # 결측·숫자 아님·중복 등
    partial_count: int = 0  # 일부 출력만 있어 등록한 행(그 출력 학습에서는 빠진다)
    partial: list[dict[str, Any]] = []
    fixed: dict[str, Any] = {}
    removed_columns: list[dict[str, Any]] = []  # {name, reason, values?}
    ignored_columns: list[str] = []
    range_changes: list[dict[str, Any]] = []  # {name, lower, upper, old_lower, old_upper}
    variables: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    warnings: list[str] = []
    case_ids: list[int] = []
    cases: list[dict[str, Any]] = []  # {row, case_id} 필드를 케이스별로 올릴 때 쓴다
    join: dict[str, Any] | None = None


class TablePreview(BaseModel):
    """표 파일 열 분석. 열마다 kind(numeric·constant·categorical·id·empty)와 role(input·output·fixed·exclude)."""
    source: str
    sheet: str | None = None
    sheets: list[str] = []
    header_row: int | None = None
    skipped_rows: int = 0
    n_rows: int = 0
    encoding: str = ""
    delimiter: str = ""
    decimal_comma: bool = False
    columns: list[dict[str, Any]] = []
    warnings: list[str] = []
    sample_rows: list[dict[str, Any]] = []
    inputs: list[str] = []
    outputs: list[str] = []
    fixed: list[str] = []
    excluded: list[str] = []
    join: dict[str, Any] | None = None


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
    import_info: dict[str, Any] | None = None
    flow_id: int | None = None
    has_recipe: bool = False
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
    stopped_by: int | None = None
    images: list[str] | None
    fields: list[str] | None = None
    archive: dict[str, Any] | None = None
    origin: dict[str, Any] | None = None
    error: str | None


class StudyStatus(BaseModel):
    study: StudyResponse
    counts: dict[str, int]
    best: CaseResponse | None
    running: bool = False
    stalled: int = 0  # 대기·실행 중인데 그 PC Agent가 꺼져 있는 케이스 수
    stalled_by: list[str] = []  # 그 PC 주인 이름
    extracting: list[int] = []  # Output을 추출하는 중인 케이스 id
    pc_notice: dict[str, Any] | None = None  # 케이스를 돌린 PC가 알린 안내 {by, text}
    data_source: dict[str, Any] | None = None  # {kind, label, rows, source}


class DoeRequest(BaseModel):
    method: Literal["lhs", "sobol", "random"] = "lhs"
    n: int = Field(20, ge=1, le=10000)
    seed: int | None = None
    flow_id: int | None = None
    node_id: str | None = None


class RunRequest(BaseModel):
    case_ids: list[int] | None = None  # None이면 pending 전체. 제안한 점만 돌릴 때는 반드시 지정한다
    dry_run: bool = False
    flow_id: int | None = None  # 이 실행을 승인받은 작업 흐름. 다른 멤버의 스크립트를 돌릴 때 확인한다


class StopRequest(BaseModel):
    case_ids: list[int] | None = None  # None이면 대기·실행 중 전체


class StopResponse(BaseModel):
    stopping: int  # PC Agent에 중지를 보낸 케이스 수
    others: int  # 다른 멤버의 PC에서 돌아 중지하지 않은 케이스 수
    released: int = 0  # 실행하던 PC가 꺼져 있어 서버에서 중지로 처리한 케이스 수
    note: str = ""


class TrainRequest(BaseModel):
    outputs: list[str] | None = None  # None이면 목적·직전 모델의 출력과 제약·추가 출력(값이 다 있는 것)
    algo: Literal["gp"] = "gp"
    flow_id: int | None = None
    node_id: str | None = None


class TrainStartRequest(TrainRequest):
    """오래 걸리는 학습을 백그라운드로 돌린다. 같은 호출을 다시 해도 새 버전이 겹치지 않는다."""
    force: bool = False  # 같은 데이터·같은 출력의 모델이 있어도 새 버전을 만든다
class InsightPublish(BaseModel):
    """판단 도우미 결과를 작업 흐름·보고서용 결과로 게시한다. keys: summary·influence·active·pareto 중 고른다(비우면 전부)."""
    flow_id: int | None = None
    node_id: str | None = None
    keys: list[str] | None = None


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
    quality: dict[str, Any] | None = None  # {level: good|fair|poor, label, warnings, by_output, excluded, rows_total, sampled}
    created_at: datetime


class TrainJobResponse(BaseModel):
    job_id: str
    study_id: int
    status: Literal["running", "done", "failed"]
    outputs: list[str] = []
    rows: int = 0
    elapsed_sec: float = 0
    reused: bool = False  # 같은 데이터로 이미 학습한 모델을 돌려줬다(재시도로 버전이 겹치지 않게)
    model: ModelResponse | None = None
    error: str | None = None
    message: str = ""


class CandidatesRequest(BaseModel):
    """모델로만 후보를 찾는다. 케이스를 만들지 않고 해석도 돌리지 않는다."""
    mode: Literal["bo", "al"] = "bo"
    n: int = Field(5, ge=1, le=50)
    model_id: int | None = None
    objective: str | None = None  # 비우면 스터디의 첫 목적
    goal: Literal["min", "max"] | None = None
    constraints: list[Constraint] | None = None  # 비우면 스터디의 제약


class CandidatesResponse(BaseModel):
    study_id: int
    model_id: int
    mode: str
    verified: bool = False  # 항상 False: 해석으로 확인되지 않은 예측이다
    note: str
    objective: str | None = None
    goal: str | None = None
    model_version: int | None = None  # 후보를 찾은 모델 버전
    candidates: list[dict[str, Any]]  # {params, predicted, std, feasible(true·false·null=모름), verified: false}
    best_known: dict[str, Any] | None = None  # 데이터에 실제로 있는 최적 행(비교용)


class SimulatorItem(BaseModel):
    """내 Simulator·공유받은 Simulator 목록의 한 줄. 링크로 받은 것은 프로젝트 정보를 보이지 않는다."""
    study_id: int
    study_name: str
    model_id: int
    version: int | None
    outputs: list[str]
    n_variables: int = 0
    n_train: int
    url: str
    thumbnail_url: str | None = None  # 카드 그림(인증 필요). 공유가 끊기면 없다
    source: str  # mine | project | link
    status: str = "active"  # active | stopped | expired
    project_id: int | None = None
    project_name: str | None = None
    shared_by: str | None = None
    note: str = ""
    share_id: int | None = None
    updated_at: datetime | None = None
    quality: str | None = None  # good | fair | poor
    data_source: dict[str, Any] | None = None


class SimulatorList(BaseModel):
    mine: list[SimulatorItem]
    shared: list[SimulatorItem]


class PredictRequest(BaseModel):
    params: dict[str, FiniteFloat]


class PredictBatchRequest(BaseModel):
    points: list[dict[str, FiniteFloat]] = Field(..., max_length=2000)


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
    warning: str | None = None  # 공유하는 모델의 정확도가 낮을 때 만든 사람에게 알린다


class RecipeResponse(BaseModel):
    """서버에 둔 스크립트 묶음 정보. 서버는 실행하지 않고 보관·전달만 한다. 멤버 PC에서 돌릴 때는 Flow 승인에 만든 사람을 보인다."""
    has: bool
    study_id: int
    version: int = 0
    files: list[dict[str, Any]] = []  # {path, size, sha256}
    size: int = 0
    sha256: str = ""
    adapter: str = ""
    adapter_config: dict[str, Any] = {}
    note: str = ""
    created_by: int | None = None
    created_by_name: str = ""
    created_at: str = ""
    warnings: list[str] = []


class FlowStudyLink(BaseModel):
    id: int
    name: str
    adapter: str
    counts: dict[str, int] = {}
    latest_model_id: int | None = None
    deployed_model_id: int | None = None
    has_recipe: bool = False


class FlowSummary(BaseModel):
    """지난 흐름을 찾을 때 쓰는 목록. 흐름 하나와 거기에 연결된 스터디."""
    id: int
    title: str
    goal: str
    status: str
    created_at: datetime
    nodes: list[dict[str, Any]] = []  # {id, label, type, status}
    studies: list[FlowStudyLink] = []


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
