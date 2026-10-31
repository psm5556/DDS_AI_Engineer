export interface User { id: number; username: string; display_name: string; is_admin: boolean; is_active: boolean }

export interface Member { user_id: number; username: string; display_name: string; role: "master" | "member" }
export interface Project {
  id: number; name: string; description: string; owner_id: number; version: number;
  created_at: string; updated_at: string; members: Member[]; user_role: "master" | "member" | null;
  flow_status: FlowStatus | null; flow_title: string | null; is_favorite: boolean;
}

export type FlowStatus = "review" | "approved" | "in_progress" | "complete" | "failed" | "rejected";
export type NodeStatus = "pending" | "in_progress" | "complete" | "failed" | "skipped";
export interface FlowNode { id: string; label: string; type: string; tool?: string | null; status: NodeStatus; detail?: string }
export interface FlowEdge { source: string; target: string; label?: string | null }
export interface Flow {
  id: number; project_id: number; title: string; goal: string; nodes: FlowNode[]; edges: FlowEdge[];
  status: FlowStatus; version: number; created_at: string; updated_at: string;
}

export type ArtifactKind = "table" | "scatter" | "line" | "bar" | "parallel" | "markdown" | "image";
export interface Artifact {
  id: number; project_id: number; flow_id: number | null; node_id: string | null;
  kind: ArtifactKind; title: string; payload: Record<string, unknown>; created_at: string; updated_at?: string | null;
}

/** AI가 작업 흐름·결과를 읽고 새로 쓴 종합 보고서(가장 최근 판). 본문에 ![캡션](artifact:<id>) 줄로 결과를 넣는다 */
export interface Report {
  id: number; project_id: number; flow_id: number | null; title: string; body: string; created_at: string; new_results: number;
  /** 보고서 틀(표지·맺음말)의 글. 파일과 같게 서버가 만들어 준다 */
  project_name: string; doc_no: string; edition: number; issued_at: string; author: string; provenance: string; colophon: string;
}
export interface ReportSummary { id: number; title: string; created_at: string }

export interface ChatSession { id: number; project_id: number; user_id: number; title: string; agent: string; agent_session_id: string | null; created_at: string }
export interface ChatMessage {
  id: number; session_id: number; role: "user" | "agent" | "thought" | "tool" | "system"; content: string;
  meta: { tool_call_id?: string; title?: string; kind?: string; status?: string; raw_input?: unknown; output?: string;
    attachments?: { id: number; filename: string; size: number }[] } | null;
  created_at: string;
}
export interface PermissionOption { optionId: string; name: string; kind: string }
export interface PermissionRequest { request_id: string; title: string; tool_kind?: string; raw_input?: unknown; options: PermissionOption[] }

/** GET /api/sessions/{id}/status · turn_started·turn_stage 이벤트. 시간은 서버 기준 경과(초)라 PC 시계가 달라도 맞다. */
export type TurnStage = "idle" | "sending" | "agent_starting" | "session_new" | "session_load" | "thinking" | "tool" | "reconnecting";
export interface TurnStatus {
  stage: TurnStage; stage_detail: string | null; started_at: string | null;
  elapsed_sec: number; stage_elapsed_sec: number; idle_sec: number; typical_start_sec: number | null;
  link: "ok" | "lost"; cancelling: boolean; cancel_elapsed_sec: number | null; agent: string | null;
}
export interface SessionStatus extends TurnStatus { busy: boolean; pending_permissions: PermissionRequest[] }

export interface AgentCli { name: string; label: string; installed: boolean; installable?: boolean; login?: string }
export interface AgentStatus {
  connected: boolean; active_project_id: number | null;
  info: { hostname?: string; version?: string; agents?: AgentCli[]; default_agent?: string; workspace?: string; tools_rev?: string } | null;
}

export interface Variable { name: string; lower: number; upper: number; unit?: string; note?: string }
export interface Objective { name: string; goal: "min" | "max" }
export interface OutputDef { name: string; note?: string; unit?: string }
export interface Study {
  id: number; project_id: number; name: string; variables: Variable[]; objectives: Objective[];
  constraints?: { name: string; upper?: number; lower?: number }[] | null;
  outputs?: OutputDef[] | null;
  adapter: string; adapter_config: Record<string, unknown>; version: number; created_at: string;
  import_info?: { kind?: string; source?: string; fixed?: Record<string, unknown> } | null;
  flow_id?: number | null; has_recipe?: boolean;
}
export interface Case {
  id: number; study_id: number; params: Record<string, number>; results: Record<string, number>;
  status: string; source: string; run_dir: string | null; run_by: number | null; stopped_by?: number | null; images: string[] | null; error: string | null;
  fields?: string[] | null;
  /** 해석 데이터 묶음(case.h5, HDF5) — docs/cae-data-hdf5.md */
  archive?: { stored?: "server" | "pc"; size?: number; ok?: boolean; error?: string; pc_path?: string; field_names?: string[];
    fields?: number; frames?: number; history?: number; raw?: number; raw_size?: number; upload_error?: string } | null;
  /** 불러온 행의 출처(파일·행 번호·폴더 이름) */
  origin?: { row?: number; source?: string; label?: string } | null;
}
/** 학습 데이터가 어디서 왔는가. 링크로 받은 사람에게는 source(파일 이름)가 오지 않는다 */
export interface DataSource { kind: "solver" | "table" | "folders"; label: string; rows: number; total: number; source?: string }
export interface StudyStatus { study: Study; counts: Record<string, number>; best: Case | null; running: boolean; stalled?: number; stalled_by?: string[]; extracting?: number[]; pc_notice?: { by: number; text: string } | null; data_source?: DataSource | null }
export type QualityLevel = "good" | "fair" | "poor";
export interface ModelQuality {
  level: QualityLevel; label: string; warnings: string[]; method?: string;
  by_output: Record<string, { level: QualityLevel; r2: number | null }>;
  excluded_count?: number; rows_total?: number; sampled?: { from: number; to: number } | null;
}
export interface SurrogateModel {
  id: number; study_id: number; name: string; algo: string; outputs: string[];
  metrics: Record<string, { r2: number; rmse: number }>; n_train: number; deployed: boolean; created_at: string;
  version: number | null; deployed_at: string | null; quality?: ModelQuality | null;
}
/** 표 → 등록 → 학습 → 배포 서버 작업. 화면을 닫아도 이어지고 돌아오면 이것을 읽는다 */
export interface TableJob {
  id: number; project_id: number | null; study_id: number | null; model_id: number | null; name: string;
  status: "running" | "done" | "failed"; phase: "import" | "train" | "deploy" | "done"; rows: number; excluded_count: number;
  reused: boolean; error: string | null; seen: boolean; phase_sec: number; elapsed_sec: number;
}
export interface TrainJob {
  job_id: string; study_id: number; status: "running" | "done" | "failed"; outputs: string[]; rows: number;
  elapsed_sec: number; reused: boolean; model: SurrogateModel | null; error: string | null; message: string;
}

/** 표 파일 미리보기. 아무것도 저장하지 않는다 */
export type ColumnRole = "input" | "output" | "fixed" | "exclude";
export interface TableColumn {
  name: string; raw: string; unit: string; kind: "numeric" | "constant" | "categorical" | "id" | "empty"; role: ColumnRole; role_reason: string;
  n_numeric: number; n_missing: number; n_text: number; n_nonfinite: number; unique: number; min: number | null; max: number | null;
  values?: string[]; value?: string | number; warnings: string[]; looks_output?: boolean;
}
export interface TablePreview {
  source: string; header_row: number | null; skipped_rows: number; n_rows: number;
  columns: TableColumn[]; warnings: string[]; outputs: string[];
}
/** 불러오기 보고: 무엇을 등록했고 무엇을 왜 뺐는가 */
export interface ImportReport {
  study_id: number; study_name: string; reused: boolean; received: number; registered: number; duplicates: number;
  excluded_count: number; excluded: { row: number; reason: string; kind: string }[]; excluded_by_reason: Record<string, number>;
  partial_count: number; fixed: Record<string, unknown>; removed_columns: { name: string; reason: string }[];
  variables: Variable[]; outputs: OutputDef[]; warnings: string[]; updated?: number; filled_pending?: number; ignored_columns?: string[];
}

export interface AgentRelease { id: number; version: string; filename: string; sha256: string; size: number; notes: string; is_latest: boolean; created_at: string }
export interface AgentInstance {
  id: number; user_id: number; hostname: string; version: string | null; platform: string | null;
  agents: AgentCli[] | null;
  status: { running_cases?: number; queued_cases?: number; active_prompts?: number; update_state?: string; sessions?: number } | null;
  connected: boolean; last_seen: string; connected_at: string | null; user_display_name: string | null; outdated: boolean;
}
export interface ApiToken { id: number; name: string; created_at: string; last_used_at: string | null; token?: string }

export interface Attachment { id: number; project_id: number; filename: string; size: number; sha256: string; created_at: string }
export interface LocalEntry { name: string; path: string; dir: boolean; size: number | null; modified: string }
export interface LocalTree { root: string; path: string; entries: LocalEntry[]; custom?: boolean; notice?: string | null }
export interface TrainingDataset {
  id: number; study_id: number; name: string; status: "building" | "ready" | "failed"; pending: number[];
  excluded: { case_id: number; reason: string }[]; n_samples: number; splits: { train?: number; val?: number; test?: number };
  targets: string[]; inputs: string[]; inputs_dropped: { name: string; reason: string }[];
  units_known: boolean; same_mesh: boolean; size: number; error: string | null; created_at: string;
}
export interface FieldModel {
  id: number; study_id: number; field: string; n_nodes: number; n_train: number; shape_varies: boolean;
  info: { modes?: number; value_modes?: number; geometry_modes?: number; energy?: number; value_error?: number; geometry_error?: number };
  created_at: string;
}

/** 스터디 판단 도우미 — server/app/study/insights.py */
export type InsightState = "good" | "fair" | "caution" | "unknown";
export interface InsightNext { key?: string; text?: string; label: string; request: string; from?: string; action?: string }
export interface InsightVariant {
  name: string; label: string; unit?: string; state: InsightState; state_label: string; sentence: string; notes: string[];
  facts: Record<string, unknown>; charts: InsightChart[]; next?: InsightNext | null; next_shown_above?: boolean;
}
export type InsightChart = { kind: string } & Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
export interface InsightPick { name: string; label: string; goal: "min" | "max" }
export interface InsightSection {
  key: string; title: string; term: string; state: InsightState; state_label: string; sentence: string; notes: string[]; how_to_read: string;
  basis: string; basis_label: string; next: InsightNext | null; facts: Record<string, unknown>; charts: InsightChart[];
  variants?: InsightVariant[]; pending?: boolean; collapsed?: boolean; next_shown_above?: boolean; bench?: string | null; basis_note?: string;
  pair_choices?: InsightPick[]; pair_first?: InsightPick; pair_default?: string; lead?: string;
}
export interface StudyInsightsData {
  status: "ready" | "computing"; stale: boolean; computed_at: string; error?: string;
  overview: { facts: { complete: number; failed: number; waiting: number; base: number; bo: number; al: number; model_version: number | null; run_text: string | null };
    next: InsightNext | null; word?: string; watch?: { key: string; text: string }[] };
  sections: InsightSection[];
}
export interface TrustLineData { state: InsightState; state_label: string; sentence: string;
  outputs?: Record<string, { label: string; unit?: string; state: InsightState; state_label: string; p80: number | null }> }
