import { RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { AgentGlyph } from "../components/AgentGlyph";
import type { TurnStatus } from "../types";

/** 기다리는 동안의 말풍선: 지금 무엇을 왜 기다리는지 + 흐르는 시간 + 안내.
 *  사내 보안 검사로 AI 도구 시작이 몇 분 걸린다. 시간이 흐르는 것이 멈추지 않았다는 가장 쉬운 증거다('움직임 줄이기'에서도 흐른다). */

/** 서버가 준 경과(초)를 받은 때의 이 PC 시각 기준으로 잡아 둔다. PC 시계가 서버와 달라도 맞다. */
export interface TurnClock { turn: TurnStatus; at: number }
export const clockOf = (turn: TurnStatus): TurnClock => ({ turn, at: Date.now() });

export function fmtElapsed(sec: number): string {
  const s = Math.max(0, Math.floor(sec || 0));
  if (s < 60) return `${s}초`;
  if (s < 3600) return `${Math.floor(s / 60)}분 ${s % 60}초`;
  // 초를 빼지 않는다: 긴 해석에서 시간이 1분에 한 번만 바뀌면 멈춘 것처럼 보인다('움직임 줄이기'에서는 이것만 움직인다)
  return `${Math.floor(s / 3600)}시간 ${Math.floor((s % 3600) / 60)}분 ${s % 60}초`;
}

/** 받침이 있으면 '을', 없으면 '를'. 라틴 글자로 끝나는 이름(Claude Code·Gemini CLI)은 '를'로 읽힌다 */
function eulReul(name: string): string {
  const c = name.charCodeAt(name.length - 1);
  return c >= 0xac00 && c <= 0xd7a3 && (c - 0xac00) % 28 ? "을" : "를";
}

const STARTING_DETAIL: Record<string, string> = {
  context_sync: "작업 규칙과 스킬을 준비하고 있습니다",
  cli_start: "프로그램을 띄우고 있습니다",
  initialize: "프로그램이 답하기를 기다리고 있습니다",
};
const SLOW_NOTICE = "사내 보안 검사로 몇 분 걸릴 수 있습니다. 멈춘 것이 아닙니다. 다른 일을 하다 와도 이어서 진행합니다.";
const IDLE_NOTICE_SEC = 60;
const STOP_NO_REPLY_SEC = 30;
const START_STUCK_SEC = 600; // 시작이 이만큼 걸리면 사용자가 판단할 수 있게 다시 시작을 보여 준다

export function WaitingBubble({ clock, agentLabel, flowActivity, toolActivity, onRestart, readOnly, serverDown }: {
  clock: TurnClock; agentLabel: string;
  /** 이 화면이 서버에 닿지 않는다. 진행을 볼 수 없으니 "멈춘 것이 아닙니다"라고 말하지 않는다 */
  serverDown?: boolean;
  /** 진행 중인 작업 흐름 단계 ("OO 진행 중 · 3/7단계"). 프롬프트를 보낸 뒤에만 쓴다: 시작 중에 지난 단계를 보여 주면 틀린 말이 된다 */
  flowActivity: string | null;
  /** 지금 도는 도구의 쉬운 이름("스크립트 실행 중…"). 없으면 기본 문구 */
  toolActivity: string | null;
  onRestart: () => void; readOnly: boolean;
}) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const { turn, at } = clock;
  const gone = (Date.now() - at) / 1000;
  const elapsed = turn.elapsed_sec + gone;
  const stageElapsed = turn.stage_elapsed_sec + gone;
  const idle = turn.idle_sec + gone;

  let label: string;
  let clockSec = elapsed;
  let notes: string[] = [];
  let glyph: "thinking" | "working" = "thinking";
  switch (turn.stage) {
    case "sending":
      label = "요청을 보내는 중";
      break;
    case "agent_starting":
      label = `${agentLabel}${eulReul(agentLabel)} 시작하는 중`;
      if (turn.stage_detail && STARTING_DETAIL[turn.stage_detail]) notes.push(STARTING_DETAIL[turn.stage_detail]);
      notes.push(SLOW_NOTICE);
      break;
    case "session_new":
      label = "새 대화를 여는 중";
      notes = ["AI 도구와 작업 도구를 연결하고 있습니다. 첫 대화는 시간이 조금 걸립니다.", SLOW_NOTICE];
      break;
    case "session_load":
      label = "이전 대화를 이어 붙이는 중";
      notes = ["지난 내용을 AI 도구가 다시 읽고 있습니다.", SLOW_NOTICE];
      break;
    case "reconnecting":
      label = "PC Agent 연결을 다시 잡는 중";
      notes = ["작업은 PC에서 계속됩니다. 연결되면 그동안의 내용을 이어서 보여 줍니다."];
      break;
    case "tool":
      label = flowActivity ?? toolActivity ?? "도구를 실행하는 중";
      clockSec = stageElapsed;
      glyph = "working";
      break;
    default:
      if (turn.stage_detail === "attachments") {
        label = "첨부 파일을 내 PC로 받는 중";
        clockSec = stageElapsed;
      } else if (idle >= IDLE_NOTICE_SEC) {
        label = flowActivity ?? "답을 기다리는 중";
        clockSec = idle;
        notes = ["오래 걸리는 작업일 수 있습니다."];
      } else {
        label = flowActivity ?? "생각하는 중";
        clockSec = stageElapsed;
      }
  }
  // 시작 구간에서만 쓸모 있다. 생각 시간은 요청마다 달라 비교할 수 없다
  const starting = turn.stage === "agent_starting" || turn.stage === "session_new" || turn.stage === "session_load";
  if (starting && turn.typical_start_sec) notes.push(`지난번에는 ${fmtElapsed(turn.typical_start_sec)} 걸렸습니다.`);

  const stopSec = turn.cancelling ? (turn.cancel_elapsed_sec ?? 0) + gone : 0;
  const noReply = turn.cancelling && stopSec >= STOP_NO_REPLY_SEC;
  if (turn.cancelling) {
    label = "중지하는 중";
    clockSec = stopSec;
    notes = noReply
      ? ["AI 도구가 답하지 않습니다. 다시 시작하면 진행 중인 작업과 멈춘 스크립트가 끝나고, 다음 요청 때 이전 대화를 이어 붙입니다. 화면에 열린 CAD 프로그램은 그대로 둡니다."]
      : ["AI 도구가 하던 일을 마무리하고 있습니다."];
  }
  const stuck = starting && elapsed >= START_STUCK_SEC;
  if (stuck) notes.push("너무 오래 걸리면 AI 도구를 다시 시작해 볼 수 있습니다.");
  if (serverDown) {
    label = "서버 연결을 다시 잡는 중";
    clockSec = elapsed;
    notes = ["이 화면이 웹앱 서버에 닿지 않아 진행 상황을 받지 못합니다. PC의 작업은 계속될 수 있고, 다시 연결되면 이어서 보여 줍니다."];
  }

  return (
    <div className="chat-msg agent">
      <div className="bubble thinking waiting-bubble" role="status">
        <div className="waiting-head">
          <AgentGlyph size={16} state={glyph} />
          <span key={label} className="activity-text">{label}</span>
          {/* 시각은 1초마다 바뀌므로 화면 낭독기가 매번 읽지 않게 한다 */}
          <span className="waiting-clock" aria-hidden="true">{fmtElapsed(clockSec)}</span>
        </div>
        {notes.map((n) => <p key={n} className="waiting-note">{n}</p>)}
        {(noReply || stuck) && !readOnly && !serverDown && (
          <button type="button" className="small waiting-restart" onClick={onRestart}><RotateCcw size={13} />AI 도구 다시 시작</button>
        )}
      </div>
    </div>
  );
}
