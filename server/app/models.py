from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
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
    password_hash = Column(String(300), nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)  # 관리자 승인 후 True


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
    id = Column(Integer, primary_key=True)
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


class Artifact(Base):
    __tablename__ = "artifacts"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    flow_id = Column(Integer, ForeignKey("flows.id", ondelete="SET NULL"))
    node_id = Column(String(100))
    # table | scatter | line | bar | parallel | markdown | image
    kind = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now, nullable=False)


class Study(TimestampMixin, Base):
    """설계 탐색 단위. variables: [{name, lower, upper}], objectives: [{name, goal: min|max}]"""
    __tablename__ = "studies"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    variables = Column(JSON, default=list)
    objectives = Column(JSON, default=list)
    adapter = Column(String(50), nullable=False, default="mock")
    adapter_config = Column(JSON, default=dict)
    version = Column(Integer, default=1, nullable=False)

    cases = relationship("Case", cascade="all, delete-orphan", order_by="Case.id")


class Case(TimestampMixin, Base):
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True)
    study_id = Column(Integer, ForeignKey("studies.id", ondelete="CASCADE"), nullable=False)
    params = Column(JSON, default=dict)
    results = Column(JSON, default=dict)
    # pending | running | complete | failed | dry_run
    status = Column(String(20), default="pending", nullable=False)
    source = Column(String(20), default="doe")  # doe | bo | al | manual
    run_dir = Column(String(500))  # 실행한 PC 기준 경로
    run_by = Column(Integer, ForeignKey("users.id"))
    images = Column(JSON, default=list)  # 서버 files 경로 목록
    error = Column(Text)


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
    deployed = Column(Boolean, default=False, nullable=False)
    best_score = Column(Float)
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
