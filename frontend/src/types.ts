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
  kind: ArtifactKind; title: string; payload: Record<string, unknown>; created_at: string;
}

export interface ChatSession { id: number; project_id: number; user_id: number; title: string; agent: string; agent_session_id: string | null; created_at: string }
export interface ChatMessage {
  id: number; session_id: number; role: "user" | "agent" | "thought" | "tool" | "system"; content: string;
  meta: { tool_call_id?: string; title?: string; kind?: string; status?: string; raw_input?: unknown; output?: string;
    attachments?: { id: number; filename: string; size: number }[] } | null;
  created_at: string;
}
export interface PermissionOption { optionId: string; name: string; kind: string }
export interface PermissionRequest { request_id: string; title: string; tool_kind?: string; raw_input?: unknown; options: PermissionOption[] }

export interface AgentCli { name: string; label: string; installed: boolean; installable?: boolean; login?: string }
export interface AgentStatus {
  connected: boolean; active_project_id: number | null;
  info: { hostname?: string; version?: string; agents?: AgentCli[]; default_agent?: string; workspace?: string } | null;
}

export interface Variable { name: string; lower: number; upper: number }
export interface Objective { name: string; goal: "min" | "max" }
export interface Study {
  id: number; project_id: number; name: string; variables: Variable[]; objectives: Objective[];
  constraints?: { name: string; upper?: number; lower?: number }[] | null;
  adapter: string; adapter_config: Record<string, unknown>; version: number; created_at: string;
}
export interface Case {
  id: number; study_id: number; params: Record<string, number>; results: Record<string, number>;
  status: string; source: string; run_dir: string | null; run_by: number | null; images: string[] | null; error: string | null;
  fields?: string[] | null;
}
export interface StudyStatus { study: Study; counts: Record<string, number>; best: Case | null; running: boolean }
export interface SurrogateModel {
  id: number; study_id: number; name: string; algo: string; outputs: string[];
  metrics: Record<string, { r2: number; rmse: number }>; n_train: number; deployed: boolean; created_at: string;
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
export interface LocalTree { root: string; path: string; entries: LocalEntry[]; custom?: boolean }
export interface FieldModel {
  id: number; study_id: number; field: string; n_nodes: number; n_train: number; shape_varies: boolean;
  info: { modes?: number; value_modes?: number; geometry_modes?: number; energy?: number; value_error?: number; geometry_error?: number };
  created_at: string;
}
