import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base


def _now():
    return datetime.now()


class TimestampMixin:
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    display_name = Column(String(100), nullable=False)
    pin_hash = Column(String(300), nullable=False)  # 6자리 PIN의 PBKDF2 해시
    is_admin = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)  # 관리자 승인 후 True
    failed_logins = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime)  # PIN 연속 실패 시 잠금
    agent_auto_update = Column(Boolean, default=True)  # PC Agent 자동 업데이트. 이 컬럼 이전 계정은 NULL이고 켜짐으로 본다
    remote_test_until = Column(DateTime)  # 개발 AI의 원격 시험을 이때까지 허용(관리자 계정만, routers/devtest.py)


class ApiToken(Base):
    """사용자 PC Agent / MCP 서버용 장기 토큰. 원문은 발급 시 한 번만 보여주고 해시만 저장한다."""
    __tablename__ = "api_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    token_hash = Column(String(100), unique=True, nullable=False)
    last_used_at = Column(DateTime)
    created_at = Column(DateTime, default=_now, nullable=False)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = {"sqlite_autoincrement": True}  # 지운 id를 다시 쓰지 않게(새 DB부터)
    id = Column(Integer, primary_key=True)
    # 사용자 PC 작업 폴더가 '이 프로젝트'의 것인지 확인하는 고유값 — DB를 새로 만들어 id가 겹쳐도 데이터가 섞이지 않게
    uid = Column(String(32), default=lambda: uuid.uuid4().hex, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    version = Column(Integer, default=1, nullable=False)

    sessions = relationship("ChatSession", back_populates="project", cascade="all, delete-orphan")
    members = relationship("ProjectMember", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(20), nullable=False, default="member")  # master | member
    user = relationship("User")


class ChatSession(TimestampMixin, Base):
    __tablename__ = "chat_sessions"
    __table_args__ = {"sqlite_autoincrement": True}
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)  # 소유자(이 사람의 PC Agent로 실행)
    title = Column(String(200), default="새 대화")
    agent = Column(String(30), default="gemini", nullable=False)  # gemini | claude | codex | demo
    agent_session_id = Column(String(200))  # ACP sessionId

    project = relationship("Project", back_populates="sessions")
    messages = relationship("ChatMessage", cascade="all, delete-orphan", order_by="ChatMessage.id")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    # 사이드바가 '최근 작업 순'을 정할 때마다 본다. 큰 본문을 건너뛰고 색인만 읽어 메시지가 수십만 개여도 빠르게
    __table_args__ = (Index("ix_chat_messages_session_time", "session_id", "created_at"),)
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    # role: user | agent | thought | tool | system
    role = Column(String(20), nullable=False)
    content = Column(Text, default="")
    meta = Column(JSON, default=dict)  # tool_call: {tool_call_id, title, kind, status, raw_input}
    created_at = Column(DateTime, default=_now, nullable=False)


class Flow(TimestampMixin, Base):
    """에이전트가 제안한 작업 흐름. nodes: [{id, label, type, tool, status, detail}], edges: [{source, target}]"""
    __tablename__ = "flows"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False)
    goal = Column(Text, default="")
    nodes = Column(JSON, default=list)
    edges = Column(JSON, default=list)
    # review(검토) → approved → in_progress(진행) → complete(완료) | failed | rejected
    status = Column(String(20), default="review", nullable=False)
    version = Column(Integer, default=1, nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"))  # 승인한 사람. 다른 멤버의 스크립트를 내 PC에서 돌리려면 내가 승인한 흐름이어야 한다


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_project_time", "project_id", "updated_at", "created_at"),)  # 위와 같은 이유. payload는 표 전체가 들어 있어 크다
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    flow_id = Column(Integer, ForeignKey("flows.id", ondelete="SET NULL"))
    node_id = Column(String(100))
    # table | scatter | line | bar | parallel | markdown | image
    kind = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now)  # 같은 제목으로 다시 게시하면 갱신(보고서가 최신인지 판단)


class Report(Base):
    """AI가 작업 흐름·결과·스터디를 읽고 새로 쓴 종합 보고서(markdown). 결과는 본문에 ![제목](artifact:<id>)로 넣는다.
    다시 쓸 때마다 새 판이 쌓이고, 화면과 Word·HTML은 가장 최근 판을 쓴다."""
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    flow_id = Column(Integer, ForeignKey("flows.id", ondelete="SET NULL"))
    title = Column(String(200), nullable=False)
    body = Column(Text, default="")
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=_now, nullable=False)


class Study(TimestampMixin, Base):
    """설계 탐색 단위. variables: [{name, lower, upper}], objectives: [{name, goal: min|max}],
    constraints: [{name, upper?, lower?}] — 결과값 제약(예: max_stress ≤ 250)"""
    __tablename__ = "studies"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    variables = Column(JSON, default=list)
    objectives = Column(JSON, default=list)
    constraints = Column(JSON, default=list)
    # 추출할 출력(Output) [{name, note}] — 나중에 추가하면 이미 해석한 케이스에서도 추출한다(results 키와 합쳐 화면에 보인다)
    outputs = Column(JSON, default=list)
    adapter = Column(String(50), nullable=False, default="mock")
    adapter_config = Column(JSON, default=dict)
    version = Column(Integer, default=1, nullable=False)
    # 불러온 데이터의 출처: {kind: table|folders, source, fixed: {고정값}, table_hash, cloned_from, report(마지막 검증 요약)}
    import_info = Column(JSON)
    # 케이스를 돌린 PC가 알린 안내 {by: 사용자, text}. 그 사람에게만 보이고, 그 PC의 다음 케이스가 문제없이 끝나면 지운다
    pc_notice = Column(JSON)
    flow_id = Column(Integer)  # 이 스터디를 마지막으로 쓴 작업 흐름. "지난번 흐름대로 다시"에서 흐름 → 스터디를 찾는다
    recipe = Column(JSON)  # 서버에 올린 스크립트 묶음 정보. 스크립트가 없는 멤버 PC가 내려받아 이어 쓴다 (study/recipe.py)
    created_by = Column(Integer)  # 이 스터디의 주인. 이 칸이 생기기 전 스터디는 비어 있고, 처음 돌린 사람을 주인으로 본다(service.study_owner)

    cases = relationship("Case", cascade="all, delete-orphan", order_by="Case.id")

    @property
    def has_recipe(self) -> bool:
        return bool(self.recipe)


class Case(TimestampMixin, Base):
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True)
    study_id = Column(Integer, ForeignKey("studies.id", ondelete="CASCADE"), nullable=False)
    params = Column(JSON, default=dict)
    results = Column(JSON, default=dict)
    # pending | running | complete | failed | dry_run
    status = Column(String(20), default="pending", nullable=False)
    source = Column(String(20), default="doe")  # doe | bo | al | manual | import
    run_dir = Column(String(500))  # 실행한 PC 기준 경로
    run_by = Column(Integer, ForeignKey("users.id"))
    # 중지한 사람. 꺼진 PC는 며칠 뒤에 돌아올 수 있으므로 서버가 다시 시작해도 남아야 한다. 있으면 돌아온 PC의 솔버를 끝내고 늦은 결과를 받지 않는다
    stopped_by = Column(Integer, ForeignKey("users.id"))
    images = Column(JSON, default=list)  # 서버 files 경로 목록
    fields = Column(JSON, default=list)  # 업로드된 필드 이름 목록 (fields/<이름>.npz)
    # 해석 데이터 묶음(case.h5, HDF5) 정보: {stored: server|pc, size, sha256, pc_path, field_names, frames, raw, ok, packed_at}
    archive = Column(JSON)
    train_samples = Column(JSON)  # 학습 샘플(train.h5): 조건 해시 → {path, sha256, size, nodes, frames, targets, inputs, …}
    # 불러온 행의 출처: {hash: 행 해시(다시 불러와도 중복 없게), row: 파일의 행 번호, label: 폴더·행 이름, source: 파일 이름}
    origin = Column(JSON)
    # 완료된 케이스를 다시 돌리는 동안 지켜 두는 이전 결과. 다시 돌리기가 실패해도 값을 잃지 않는다
    previous = Column(JSON)
    error = Column(Text)


class TableJob(TimestampMixin, Base):
    """표 → 등록 → 학습 → 배포를 한 번에 하는 서버 작업. 화면을 닫아도 계속되고 돌아오면 상태를 읽는다 (study/table_job.py)."""
    __tablename__ = "table_jobs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"))  # 첫 화면에서 시작하면 표를 받은 뒤에 정해진다
    study_id = Column(Integer, ForeignKey("studies.id", ondelete="SET NULL"))
    model_id = Column(Integer, ForeignKey("surrogate_models.id", ondelete="SET NULL"))
    name = Column(String(200), nullable=False)
    outputs = Column(JSON)
    status = Column(String(20), default="running", nullable=False)  # running | done | failed
    phase = Column(String(20), default="import", nullable=False)  # import | train | deploy | done
    phase_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime)
    rows = Column(Integer, default=0)
    excluded_count = Column(Integer, default=0)
    reused = Column(Boolean, default=False)
    error = Column(Text)
    seen = Column(Boolean, default=False, nullable=False)  # 끝난 결과를 사용자가 확인했다


class SurrogateModel(Base):
    __tablename__ = "surrogate_models"
    id = Column(Integer, primary_key=True)
    study_id = Column(Integer, ForeignKey("studies.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    algo = Column(String(30), default="gp")
    outputs = Column(JSON, default=list)
    metrics = Column(JSON, default=dict)  # {output: {r2, rmse}}
    n_train = Column(Integer, default=0)
    path = Column(String(500), nullable=False)
    deployed = Column(Boolean, default=False, nullable=False)  # 이 스터디 Simulator의 현재 버전(스터디마다 하나)
    best_score = Column(Float)
    version = Column(Integer)  # 스터디 안에서 1, 2, 3…
    created_by = Column(Integer, ForeignKey("users.id"))
    deployed_at = Column(DateTime)
    field_models = Column(JSON)  # 이 버전의 3D 모델 {필드: 모델 id}. 되돌리면 3D도 그때 것으로 돌아간다
    # 모델 품질: {level: good|fair|poor, label, warnings, by_output, excluded, excluded_count, rows_total, sampled, data_hash}
    quality = Column(JSON)
    created_at = Column(DateTime, default=_now, nullable=False)


class AgentRelease(Base):
    """PC Agent 배포본. is_latest인 것이 자동 업데이트 대상."""
    __tablename__ = "agent_releases"
    id = Column(Integer, primary_key=True)
    version = Column(String(30), unique=True, nullable=False)
    filename = Column(String(200), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size = Column(Integer, nullable=False)
    notes = Column(Text, default="")
    is_latest = Column(Boolean, default=False, nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=_now, nullable=False)


class AgentInstance(Base):
    """사용자 PC Agent 현황(모니터링). hello/heartbeat 때 갱신."""
    __tablename__ = "agent_instances"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    hostname = Column(String(200), nullable=False)
    version = Column(String(30))
    platform = Column(String(300))
    agents = Column(JSON, default=list)  # [{name, label, installed}]
    status = Column(JSON, default=dict)  # {running_cases, active_sessions, update_state}
    connected = Column(Boolean, default=False, nullable=False)
    last_seen = Column(DateTime, default=_now, nullable=False)
    connected_at = Column(DateTime)


class DevTestRecord(Base):
    """개발 AI가 PC에서 실행한 원격 시험 내역. PC 주인이 설정 화면에서 본다."""
    __tablename__ = "devtest_records"
    id = Column(Integer, primary_key=True)
    by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    target_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    hostname = Column(String(200))
    op = Column(String(30), nullable=False)
    tool = Column(String(100))
    ok = Column(Boolean)
    returncode = Column(Integer)
    seconds = Column(Float)
    note = Column(String(300))
    created_at = Column(DateTime, default=_now, nullable=False)


class ShareLink(Base):
    """배포된 Simulator 공유 링크: 프로젝트 멤버가 아니어도(로그인한 사내 사용자) 예측을 쓸 수 있다."""
    __tablename__ = "share_links"
    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False)
    model_id = Column(Integer, ForeignKey("surrogate_models.id", ondelete="CASCADE"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    note = Column(String(200), default="")
    enabled = Column(Boolean, default=True, nullable=False)
    uses = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=_now, nullable=False)


class ShareAccess(Base):
    """공유 링크를 연 사람. 링크를 처음 열면 그 사람의 '공유받은 Simulator' 목록에 들어간다(주소를 따로 보관하지 않아도 되게)."""
    __tablename__ = "share_access"
    share_id = Column(Integer, ForeignKey("share_links.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    hidden = Column(Boolean, default=False, nullable=False)  # 받은 사람이 목록에서 뺐다. 링크를 다시 열면 돌아온다
    last_opened = Column(DateTime, default=_now, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)


class ProjectFavorite(Base):
    __tablename__ = "project_favorites"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime, default=_now, nullable=False)


class Attachment(Base):
    """대화에 첨부한 파일(CAD 모델·입력 파일·참고 자료). 서버에 보관하고, 프롬프트를 보낼 때
    PC Agent가 프로젝트 로컬 폴더(inputs/)로 내려받아 CAD/CAE 툴이 바로 쓸 수 있게 한다."""
    __tablename__ = "attachments"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String(300), nullable=False)
    size = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    stored = Column(String(500), nullable=False)  # files_dir 기준 상대 경로
    created_at = Column(DateTime, default=_now, nullable=False)


class TrainingDataset(Base):
    """학습 데이터셋: 한 스터디의 학습 샘플 묶음. 만들면 고치지 않는다(manifest.json). 조건이 바뀌면 새로 만든다."""
    __tablename__ = "training_datasets"
    id = Column(Integer, primary_key=True)
    study_id = Column(Integer, ForeignKey("studies.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    spec = Column(JSON, default=dict)  # {targets, frames, step}
    status = Column(String(20), default="building", nullable=False)  # building | ready | failed
    pending = Column(JSON, default=list)  # 아직 샘플을 기다리는 케이스
    excluded = Column(JSON, default=list)  # [{case_id, reason}]
    n_samples = Column(Integer, default=0, nullable=False)
    splits = Column(JSON, default=dict)  # {train, val, test}
    targets = Column(JSON, default=list)
    inputs = Column(JSON, default=list)
    inputs_dropped = Column(JSON, default=list)  # [{name, reason}]
    units_known = Column(Boolean, default=False, nullable=False)
    same_mesh = Column(Boolean, default=False, nullable=False)
    size = Column(Integer, default=0, nullable=False)
    error = Column(Text)
    manifest_path = Column(String(500))
    flow_id = Column(Integer)
    node_id = Column(String(100))
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=_now, nullable=False)


class FieldModel(Base):
    """형상·필드 예측 모델(PCA + GP). 설계변수 → 3D 형상과 노드 필드(응력·변위 등)."""
    __tablename__ = "field_models"
    id = Column(Integer, primary_key=True)
    study_id = Column(Integer, ForeignKey("studies.id", ondelete="CASCADE"), nullable=False)
    field = Column(String(100), nullable=False)
    n_nodes = Column(Integer, nullable=False)
    n_train = Column(Integer, nullable=False)
    shape_varies = Column(Boolean, default=False, nullable=False)
    info = Column(JSON, default=dict)  # modes, energy, truncation_error, mode_r2
    path = Column(String(500), nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)
