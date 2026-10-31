import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ClipboardPaste, Loader2, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, get, post } from "../api";
import { Modal } from "../components/Modal";
import { fmt, PASTE_MAX_BYTES, PASTE_TOO_BIG } from "../format";
import { useToast } from "../toast";
import type { ImportReport, Study, SurrogateModel, TableColumn, TableJob, TablePreview, TrainJob } from "../types";
import { useCanvas } from "./CanvasContext";

/** 표로 바로 Simulator: 표를 붙여 넣으면 알아서 읽고, 어느 열이 결과(출력)인지만 묻고, 만들기 → 학습 → 배포 → Simulator 열기.
 *  회사의 CSV·엑셀 파일은 보안 프로그램으로 암호화되어 있어 파일을 고르는 입구는 두지 않는다.
 *  전부 서버 작업 하나로 돌아 AI·PC Agent가 없어도 되고, 화면을 닫거나 새로고침해도 이어진다. 입력창(Composer)과 붙여넣기 창이 같은 질문 화면을 쓴다. */

const TRAIN_POLL_MS = 1500;
const POLL_RETRIES = 5; // 잠깐 끊겨도 학습은 서버에서 계속되므로 바로 실패로 보이지 않게 한다
type Phase = "ask" | "import" | "train" | "deploy" | "done";

const PHASE_LABEL: Record<TableJob["phase"], string> = { import: "표 등록", train: "예측 모델 학습", deploy: "Simulator 배포", done: "완료" };
export const secText = (n: number) => (n >= 60 ? `${Math.floor(n / 60)}분 ${n % 60}초` : `${n}초`);
const JOBS_KEY = ["table-jobs"];
// 질문 화면이 직접 진행을 보여 주는 작업. 같은 진행을 알림 칸에 또 보이지 않는다
const shownHere = new Set<number>();

/** 내가 시작한 표 작업: 도는 것과 끝났지만 아직 확인하지 않은 것. 서버에 있어 새로고침해도 그대로다 */
export function useTableJobs(): TableJob[] {
  const { data } = useQuery({
    queryKey: JOBS_KEY, queryFn: () => get<TableJob[]>("/api/table-jobs"),
    refetchInterval: (q) => (q.state.data?.some((j) => j.status === "running") ? TRAIN_POLL_MS : 30000),
  });
  return data ?? [];
}

/** 화면 어디에 있든 보이는 진행·결과 알림. 표를 만드는 화면을 닫았거나 새로고침한 뒤에 여기서 이어 본다 */
export function TableJobsBar() {
  const jobs = useTableJobs();
  const qc = useQueryClient();
  const nav = useNavigate();
  const seen = (id: number) => post(`/api/table-jobs/${id}/seen`, {}).finally(() => qc.invalidateQueries({ queryKey: JOBS_KEY }));
  const shown = jobs.filter((j) => !shownHere.has(j.id));
  if (!shown.length) return null;
  return (
    <div className="table-jobs" role="status">
      {shown.map((j) => (
        <div key={j.id} className={`table-job ${j.status}`}>
          {j.status === "running" && <><Loader2 size={14} className="spin" /><span className="grow">'{j.name}' Simulator를 만드는 중입니다 · {PHASE_LABEL[j.phase]} {secText(j.phase_sec)}</span></>}
          {j.status === "done" && <><Check size={14} /><span className="grow">'{j.name}' Simulator를 {j.reused ? "이미 만들어 두었습니다" : "만들었습니다"}{j.excluded_count ? ` · 빠진 행 ${j.excluded_count.toLocaleString("ko-KR")}개는 '불러온 데이터 확인' 카드에 이유가 있습니다` : ""}</span>
            <button type="button" className="small primary" onClick={() => { void seen(j.id); nav(`/projects/${j.project_id}`, { state: { canvasTab: "simulator", studyId: j.study_id, modelId: j.model_id } }); }}>열기</button>
            <button type="button" className="icon-btn" aria-label="닫기" title="닫기" onClick={() => void seen(j.id)}><X size={13} /></button></>}
          {j.status === "failed" && <><AlertTriangle size={14} /><span className="grow">'{j.name}' Simulator를 만들지 못했습니다. {j.error}</span>
            <button type="button" className="icon-btn" aria-label="닫기" title="닫기" onClick={() => void seen(j.id)}><X size={13} /></button></>}
        </div>
      ))}
    </div>
  );
}

const values = (c: TableColumn) => (c.min != null && c.max != null ? `${fmt(c.min)}~${fmt(c.max)}` : "");

function Steps({ phase, elapsed, rows }: { phase: Phase; elapsed: number; rows: number }) {
  const order: [Phase, string][] = [["import", PHASE_LABEL.import], ["train", PHASE_LABEL.train], ["deploy", PHASE_LABEL.deploy]];
  const at = order.findIndex(([k]) => k === phase);
  return (
    <div className="ti-run">
      <ol className="ti-steps">
        {order.map(([k, label], i) => (
          <li key={k} className={i < at ? "done" : i === at ? "now" : ""}>
            {i < at ? <Check size={14} /> : i === at ? <Loader2 size={14} className="spin" /> : <i />}
            <span>{label}{k === "train" && i === at && elapsed > 0 ? ` · ${secText(elapsed)}` : ""}</span>
          </li>
        ))}
      </ol>
      <p className="small muted">
        {phase === "train" ? `${rows.toLocaleString("ko-KR")}행으로 학습 중입니다. 멈춘 것이 아닙니다. 행이 많으면 몇 분 걸립니다. ` : ""}
        화면을 닫거나 새로고침해도 서버에서 이어서 하고, 끝나면 알려 드립니다.
      </p>
    </div>
  );
}

/** 무엇을 알아서 처리했는지. 물어보지 않는 대신 언제든 펼쳐 볼 수 있게 한다 */
function RecognizedNote({ p }: { p: TablePreview }) {
  const fixed = p.columns.filter((c) => c.kind === "constant");
  const removed = p.columns.filter((c) => c.kind !== "numeric" && c.kind !== "constant");
  const dirty = p.columns.filter((c) => c.kind === "numeric" && c.warnings.length);
  const notes = [...p.warnings, ...p.columns.filter((c) => c.kind === "categorical").flatMap((c) => c.warnings)];
  return (
    <details className="ti-note small">
      <summary>인식한 내용 보기{dirty.length + removed.length ? ` · 확인할 것 ${dirty.length + removed.length}건` : ""}</summary>
      <ul>
        {p.header_row == null && <li>머리글이 없어 열 이름을 '열1, 열2…'로 붙였습니다. 머리글까지 함께 복사하면 이름이 그대로 쓰입니다.</li>}
        {p.skipped_rows > 0 && <li>앞의 {p.skipped_rows}줄은 제목으로 보고 건너뛰었습니다.</li>}
        {fixed.length > 0 && <li>고정값(값이 하나뿐이라 변수가 아님): {fixed.map((c) => `${c.name}=${typeof c.value === "number" ? fmt(c.value) : c.value ?? ""}`).join(", ")}</li>}
        {removed.map((c) => <li key={c.name}>뺀 열 '{c.name}': {c.role_reason}</li>)}
        {dirty.flatMap((c) => c.warnings.map((w) => <li key={c.name + w}>'{c.name}': {w}</li>))}
        {notes.map((w) => <li key={w}>{w}</li>)}
      </ul>
    </details>
  );
}

/** 한 가지만 묻는다: 어느 열이 결과(출력)인가. 추측을 미리 골라 두고, 나머지 숫자 열은 전부 입력이다 */
export function TableQuestion({ pid, text, onOpen, onCancel, onRepaste, onMaking }: {
  /** 없으면(첫 화면) 만들 때 프로젝트를 만든다. 보기만 하고 닫았을 때 빈 프로젝트가 남지 않게 */
  pid: number | null; text: string; onOpen: (projectId: number, studyId: number, modelId: number) => void; onCancel: () => void; onRepaste?: () => void;
  /** 만드는 중인지 알린다. 그동안 입력창은 AI로 보내지 않는다(같은 표가 두 길로 간다) */
  onMaking?: (making: boolean) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [preview, setPreview] = useState<TablePreview | null>(null);
  const [readErr, setReadErr] = useState("");
  const [outs, setOuts] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<Phase>("ask");
  const [runErr, setRunErr] = useState("");
  const [rows, setRows] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const made = useRef<number | null>(pid); // 다시 시도할 때 프로젝트가 또 생기지 않게
  const gone = useRef(false); // 닫혀도 서버 작업은 끝까지 간다. 끝난 사실은 알림 칸(TableJobsBar)이 전한다
  const mine = useRef<number | null>(null);
  useEffect(() => {
    gone.current = false;
    return () => { gone.current = true; if (mine.current != null) { shownHere.delete(mine.current); qc.invalidateQueries({ queryKey: JOBS_KEY }); } };
  }, [qc]);
  useEffect(() => { onMaking?.(phase !== "ask"); return () => onMaking?.(false); }, [phase, onMaking]);

  useEffect(() => {
    let live = true;
    setPreview(null); setReadErr("");
    post<TablePreview>("/api/tables/preview/text", { text })
      .then((p) => { if (live) { setPreview(p); setOuts(new Set(p.outputs)); } })
      .catch((e: Error) => { if (live) setReadErr(e.message); });
    return () => { live = false; };
  }, [text]);

  // 추측한 출력을 앞에 둔다. 열이 많아도 무엇이 골라졌는지 먼저 보이게 한다
  const numeric = useMemo(() => {
    const cols = preview?.columns.filter((c) => c.kind === "numeric") ?? [];
    const guessed = new Set(preview?.outputs ?? []);
    return [...cols.filter((c) => guessed.has(c.name)), ...cols.filter((c) => !guessed.has(c.name))];
  }, [preview]);
  const inputs = numeric.filter((c) => !outs.has(c.name));
  const constants = preview?.columns.filter((c) => c.kind === "constant") ?? [];
  // 결과로 보이는 열이 쓸 수 없어 빠졌으면 그 이유가 먼저다. 모르면 다른 열을 출력으로 골라 뜻 없는 모델을 만들게 된다
  const deadOutputs = preview?.columns.filter((c) => c.looks_output) ?? [];
  const deadText = deadOutputs.map((c) => `'${c.name}'은(는) ${c.kind === "constant" ? "값이 모든 행에서 같아 예측할 것이 없습니다" : c.role_reason ?? "쓸 수 없습니다"}`).join(", ");
  const names = (cols: TableColumn[]) => cols.slice(0, 5).map((c) => c.name).join(", ") + (cols.length > 5 ? ` 외 ${cols.length - 5}개` : "");
  const blocked = !preview ? "" : preview.n_rows < 3 ? `데이터 행이 ${preview.n_rows}개뿐입니다. 예측 모델에는 3행 이상이 필요합니다 → 행을 더 복사해 붙여 넣으세요`
    : !numeric.length && constants.length ? `값이 변하는 숫자 열이 없습니다(${names(constants)}은(는) 모든 행에서 값이 같습니다) → 값이 다른 행을 더 복사해 붙여 넣으세요`
    : !numeric.length ? "숫자 열을 찾지 못했습니다 → 머리글과 숫자 값을 함께 복사해 붙여 넣으세요"
    : numeric.length === 1 ? `입력으로 쓸 수 있는 열이 없습니다. 값이 변하는 숫자 열이 '${numeric[0].name}' 하나뿐입니다${constants.length ? `(${names(constants)}은(는) 모든 행에서 값이 같습니다)` : ""} → 입력값이 다른 행을 더 복사해 붙여 넣으세요`
    : !outs.size && deadOutputs.length ? `${deadText} → 엑셀에서 값을 고쳐 다시 복사하거나, 다른 열이 결과라면 그 열을 고르세요`
    : !outs.size ? "결과(출력) 열을 하나 이상 골라 주세요"
    : !inputs.length ? "입력으로 쓸 숫자 열이 남지 않았습니다 → 입력 열을 빼고 출력만 골라 주세요" : "";

  const refresh = (projectId: number) => {
    qc.invalidateQueries({ queryKey: ["studies", projectId] });
    qc.invalidateQueries({ queryKey: ["models", projectId] });
    qc.invalidateQueries({ queryKey: ["study"] });
    qc.invalidateQueries({ queryKey: ["artifacts", projectId] });
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["simulators"] });
  };

  const start = async () => {
    setPhase("import"); setRunErr(""); setElapsed(0);
    onMaking?.(true); // 진행 표시와 같은 렌더에서 알린다. 늦으면 그 사이 붙여 넣은 표가 만드는 표를 덮어쓴다
    const first = [...outs][0];
    const name = `${first}${outs.size > 1 ? ` 외 ${outs.size - 1}개` : ""} 예측`;
    let job: TableJob;
    try {
      // 등록·학습·배포를 서버 작업 하나로 시작한다. 이 화면이 사라져도 서버가 끝까지 한다
      job = await post<TableJob>("/api/table-jobs", { project_id: made.current, text, name, outputs: [...outs].map((n) => ({ name: n })) });
    } catch (e) {
      setPhase("ask"); setRunErr((e as Error).message);
      return;
    }
    mine.current = job.id;
    shownHere.add(job.id);
    qc.invalidateQueries({ queryKey: JOBS_KEY });
    let misses = 0;
    while (job.status === "running" && !gone.current) {
      setPhase(job.phase); setRows(job.rows); setElapsed(job.phase_sec);
      await new Promise((r) => setTimeout(r, TRAIN_POLL_MS));
      try {
        job = await get<TableJob>(`/api/table-jobs/${job.id}`);
        misses = 0;
      } catch (e) { // 404면 기다려도 소용없다. 잠깐 끊긴 것이면 서버는 계속하고 있다
        if ((e instanceof ApiError && e.status === 404) || ++misses > POLL_RETRIES) {
          shownHere.delete(job.id); mine.current = null;
          setPhase("ask"); setRunErr((e as Error).message);
          return;
        }
      }
    }
    if (gone.current) return; // 끝나면 알림 칸이 알린다
    shownHere.delete(job.id); mine.current = null;
    made.current = job.project_id ?? made.current; // 다시 시도할 때 프로젝트가 또 생기지 않게
    if (job.project_id != null) refresh(job.project_id);
    void post(`/api/table-jobs/${job.id}/seen`, {}).finally(() => qc.invalidateQueries({ queryKey: JOBS_KEY }));
    if (job.status === "failed") {
      setPhase("ask"); setRunErr(job.error ?? "Simulator를 만들지 못했습니다 → 다시 시도해 주세요");
      return;
    }
    // 묻지 않고 뺀 행은 결과와 함께 꼭 알린다. 자세한 이유는 결과 카드 '불러온 데이터 확인'에 있다
    const left = job.excluded_count ? ` 빠진 행 ${job.excluded_count.toLocaleString("ko-KR")}개는 '불러온 데이터 확인' 카드에 이유가 있습니다.` : "";
    toast(job.reused ? `같은 표로 만든 '${job.name}' Simulator가 이미 있어 현재 버전을 그대로 엽니다.` : `'${job.name}' Simulator를 만들었습니다.${left}`);
    onOpen(job.project_id!, job.study_id!, job.model_id!);
  };

  const toggle = (n: string) => setOuts((s) => { const x = new Set(s); if (x.has(n)) x.delete(n); else x.add(n); return x; });
  if (readErr) {
    return (
      <div className="ti-q">
        <div className="notice err" role="alert"><AlertTriangle size={15} /><span className="grow">{readErr}</span>
          {onRepaste ? <button type="button" className="small" onClick={onRepaste}>다시 붙여넣기</button>
            : <button type="button" className="icon-btn" aria-label="닫기" title="닫기" onClick={onCancel}><X size={13} /></button>}</div>
      </div>
    );
  }
  if (!preview) return <div className="ti-q"><p className="small muted ti-loading"><Loader2 size={14} className="spin" />표를 읽는 중입니다</p></div>;
  if (phase !== "ask") return <div className="ti-q"><Steps phase={phase} elapsed={elapsed} rows={rows} /></div>;
  return (
    <div className="ti-q">
      <div className="ti-q-head">
        <b>어느 열이 결과(출력)인가요?</b>
        <span className="small muted">표 {preview.n_rows.toLocaleString("ko-KR")}행을 읽었습니다. 나머지 숫자 열은 입력이 됩니다</span>
      </div>
      <div className="ti-chips" role="group" aria-label="결과(출력) 열">
        {numeric.map((c) => (
          <button key={c.name} type="button" className={`ti-chip ${outs.has(c.name) ? "on" : ""}`} aria-pressed={outs.has(c.name)} onClick={() => toggle(c.name)} title={c.raw}>
            {outs.has(c.name) && <Check size={12} />}<b>{c.name}</b>{c.unit && <small>[{c.unit}]</small>}<small className="v">{values(c)}</small>
          </button>
        ))}
      </div>
      {inputs.length > 0 && <p className="small muted ti-inputs" title={inputs.map((c) => c.name).join(", ")}>입력 {inputs.length}개: {inputs.map((c) => c.name).join(", ")}</p>}
      {runErr && <div className="notice err" role="alert"><AlertTriangle size={15} /><span>{runErr}</span></div>}
      <RecognizedNote p={preview} />
      <div className="ti-q-foot">
        <span className="ti-reason grow">{blocked}</span>
        {onRepaste && <button type="button" className="small ghost" onClick={onRepaste}>다시 붙여넣기</button>}
        <button type="button" className="small ghost" onClick={onCancel}>취소</button>
        <button type="button" className="primary" disabled={!!blocked} onClick={() => void start()}><SlidersHorizontal size={14} />Simulator 만들기</button>
      </div>
    </div>
  );
}

/** 있는 스터디에 행 더하기: 붙여 넣으면 등록하고 무엇이 어떻게 됐는지 보여 준 뒤, 다시 학습해 배포할지 묻는다.
 *  Output 추가 안내('출력 열을 더한 표를 행 추가로')도 이 화면으로 온다. 새 열이 있으면 출력으로 더할지 고른다 */
export function TableAddRows({ pid, study, onClose }: { pid: number; study: Study; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<TablePreview | null>(null);
  const [extra, setExtra] = useState<Set<string>>(new Set());
  const [report, setReport] = useState<ImportReport | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const known = new Set([...study.variables.map((v) => v.name), ...(study.outputs ?? []).map((o) => o.name), ...study.objectives.map((o) => o.name)]);
  const fresh = preview?.columns.filter((c) => c.kind === "numeric" && !known.has(c.name)) ?? [];
  const read = async (t: string) => {
    if (!t.trim()) return;
    if (t.length > PASTE_MAX_BYTES) { toast(PASTE_TOO_BIG, true); return; }
    setText(t); setErr("");
    try {
      const p = await post<TablePreview>("/api/tables/preview/text", { text: t });
      setPreview(p);
      setExtra(new Set(p.columns.filter((c) => c.kind === "numeric" && !known.has(c.name)).map((c) => c.name)));
    } catch (e) { setErr((e as Error).message); }
  };
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["study"] });
    qc.invalidateQueries({ queryKey: ["studies", pid] });
    qc.invalidateQueries({ queryKey: ["models", pid] });
    qc.invalidateQueries({ queryKey: ["artifacts", pid] });
    qc.invalidateQueries({ queryKey: ["simulators"] });
  };
  const add = async () => {
    setBusy("등록하는 중입니다"); setErr("");
    try {
      setReport(await post<ImportReport>(`/api/studies/${study.id}/cases/import/text`, { text, outputs: [...extra].map((n) => ({ name: n })) }));
      refresh();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(""); }
  };
  const retrain = async () => {
    setBusy("다시 학습하는 중입니다. 멈춘 것이 아닙니다. 행이 많으면 몇 분 걸립니다"); setErr("");
    try {
      let job = await post<TrainJob>(`/api/studies/${study.id}/train/start`, {});
      while (job.status === "running") {
        await new Promise((r) => setTimeout(r, TRAIN_POLL_MS));
        job = await get<TrainJob>(`/api/train-jobs/${job.job_id}`);
      }
      if (job.status === "failed" || !job.model) throw new Error(job.error ?? "학습하지 못했습니다 → 다시 시도해 주세요");
      const live = await post<SurrogateModel>(`/api/models/${job.model.id}/deploy`, {});
      refresh();
      toast(`Simulator를 v${live.version}으로 배포했습니다. 공유 링크도 이 버전을 보여 줍니다`);
      onClose();
    } catch (e) { setErr((e as Error).message); setBusy(""); }
  };
  return (
    <Modal title={`'${study.name}'에 행 추가`} wide onClose={onClose}>
      {report ? (
        <div className="ti-q">
          <p className="ti-summary">받은 행 {report.received}개 · 새 행 {report.registered}개{report.updated ? ` · 출력을 더한 행 ${report.updated}개` : ""}{report.filled_pending ? ` · 채운 DOE 점 ${report.filled_pending}개` : ""}{report.duplicates ? ` · 이미 있는 행 ${report.duplicates}개` : ""}{report.excluded_count ? ` · 뺀 행 ${report.excluded_count}개` : ""}</p>
          {report.warnings.length > 0 && <ul className="ti-note small">{report.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
          {err && <div className="notice err" role="alert"><AlertTriangle size={15} /><span>{err}</span></div>}
          {busy && <p className="small muted ti-loading"><Loader2 size={14} className="spin" />{busy}</p>}
          <div className="ti-q-foot">
            <span className="small muted grow">배포 중인 Simulator는 다시 학습해 배포할 때까지 그대로입니다.</span>
            <button type="button" className="small ghost" disabled={!!busy} onClick={onClose}>지금은 등록만</button>
            <button type="button" className="primary" disabled={!!busy} onClick={() => void retrain()}><SlidersHorizontal size={14} />다시 학습해 배포</button>
          </div>
        </div>
      ) : preview ? (
        <div className="ti-q">
          <p className="ti-summary">표 {preview.n_rows.toLocaleString("ko-KR")}행을 읽었습니다. 같은 행은 겹치지 않고, 범위 밖 값이 있으면 입력 범위를 넓힙니다.</p>
          {fresh.length > 0 && <>
            <b className="small">이 스터디에 없는 열입니다. 출력으로 더할 열을 고르세요</b>
            <div className="ti-chips" role="group" aria-label="새 출력 열">
              {fresh.map((c) => (
                <button key={c.name} type="button" className={`ti-chip ${extra.has(c.name) ? "on" : ""}`} aria-pressed={extra.has(c.name)} title={c.raw}
                  onClick={() => setExtra((s) => { const x = new Set(s); if (x.has(c.name)) x.delete(c.name); else x.add(c.name); return x; })}>
                  {extra.has(c.name) && <Check size={12} />}<b>{c.name}</b><small className="v">{values(c)}</small>
                </button>
              ))}
            </div>
          </>}
          {err && <div className="notice err" role="alert"><AlertTriangle size={15} /><span>{err}</span></div>}
          <RecognizedNote p={preview} />
          <div className="ti-q-foot">
            {busy && <span className="small muted grow ti-loading"><Loader2 size={14} className="spin" />{busy}</span>}
            <button type="button" className="small ghost" disabled={!!busy} onClick={() => { setPreview(null); setErr(""); }}>다시 붙여넣기</button>
            <button type="button" className="primary" disabled={!!busy} onClick={() => void add()}><Check size={14} />행 추가</button>
          </div>
        </div>
      ) : (
        <div className="ti-paste">
          <label htmlFor="ti-add-box"><ClipboardPaste size={15} /><b>더할 행을 머리글과 함께 복사해 여기에 붙여 넣으세요</b></label>
          <textarea id="ti-add-box" autoFocus spellCheck={false} value={text} aria-label="더할 행 붙여넣기" onChange={(e) => setText(e.target.value)}
            onPaste={(e) => { const t = e.clipboardData.getData("text/plain"); if (t.trim()) { e.preventDefault(); void read(t); } }} />
          {err && <div className="notice err" role="alert"><AlertTriangle size={15} /><span>{err}</span></div>}
          <div className="ti-q-foot">
            <span className="small muted grow">이 스터디의 입력: {study.variables.map((v) => v.name).join(", ")}. 열 이름이 같아야 합니다.</span>
            <button type="button" className="primary" disabled={!text.trim()} onClick={() => void read(text)}>표 읽기</button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/** 붙여넣기 창: 넓은 입력 칸 하나. 붙여 넣는 즉시 읽어 질문으로 넘어간다 */
export function TablePasteDialog({ pid, onClose, onOpen }: {
  pid: number | null; onClose: () => void; onOpen: (projectId: number, studyId: number, modelId: number) => void;
}) {
  const toast = useToast();
  const [draft, setDraft] = useState("");
  const [text, setText] = useState<string | null>(null);
  const commit = (t: string) => {
    if (!t.trim()) return;
    if (t.length > PASTE_MAX_BYTES) { toast(PASTE_TOO_BIG, true); return; }
    setText(t);
  };
  return (
    <Modal title="표로 바로 Simulator" wide onClose={onClose}>
      {text != null ? <TableQuestion pid={pid} text={text} onOpen={onOpen} onCancel={onClose} onRepaste={() => setText(null)} /> : (
        <div className="ti-paste">
          <label htmlFor="ti-paste-box"><ClipboardPaste size={15} /><b>엑셀에서 표를 복사해 여기에 붙여 넣으세요</b></label>
          <textarea id="ti-paste-box" autoFocus spellCheck={false} value={draft} aria-label="표 붙여넣기"
            placeholder={"두께 [mm]\t폭 [mm]\t최대 응력 [MPa]\n1.5\t20\t240\n2.0\t25\t180\n…"}
            onChange={(e) => setDraft(e.target.value)}
            onPaste={(e) => {
              // 엑셀 복사에는 그림도 함께 들어 있다. 글만 쓴다
              const t = e.clipboardData.getData("text/plain");
              if (t.trim()) { e.preventDefault(); setDraft(t); commit(t); }
            }} />
          <div className="ti-q-foot">
            <span className="small muted grow">머리글·단위·숫자 열은 알아서 읽습니다. 입력과 출력이 다른 표에 있으면 한 표로 합쳐 복사해 주세요. AI와 PC Agent가 없어도 됩니다.</span>
            <button type="button" className="primary" disabled={!draft.trim()} onClick={() => commit(draft)}>표 읽기</button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/** 데이터 탭에서 여는 창. 탭이 다시 그려져도 사라지지 않게 작업 공간에 둔다 */
export function TableImportHost({ pid }: { pid: number }) {
  const c = useCanvas();
  if (!c.tableOpen) return null;
  return <TablePasteDialog pid={pid} onClose={() => c.setTableOpen(false)}
    onOpen={(_, studyId, modelId) => { c.setTableOpen(false); c.openSimulator(studyId, modelId); }} />;
}
