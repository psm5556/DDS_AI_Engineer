import { AlertTriangle, Database } from "lucide-react";
import type { DataSource, ModelQuality, QualityLevel, SurrogateModel } from "../types";

const LEVEL_CHIP: Record<QualityLevel, string> = { good: "ok", fair: "warn", poor: "err" };
const LEVEL_LABEL: Record<QualityLevel, string> = { good: "좋음", fair: "보통", poor: "낮음" };
const r2Level = (r2: number): QualityLevel => (r2 >= 0.9 ? "good" : r2 >= 0.7 ? "fair" : "poor");

/** 품질을 저장하기 전에 학습한 옛 모델은 R²로 수준만 정한다 */
function qualityOf(m: SurrogateModel): ModelQuality {
  if (m.quality) return m.quality;
  const by = Object.fromEntries(Object.entries(m.metrics).map(([k, v]) => [k, { level: r2Level(v.r2), r2: v.r2 }]));
  const order: QualityLevel[] = ["good", "fair", "poor"];
  const level = order[Math.max(0, ...Object.values(by).map((b) => order.indexOf(b.level)))];
  return { level, label: LEVEL_LABEL[level], by_output: by, warnings: [] };
}

/** "불러온 표 120행"처럼 학습 데이터의 출처. 표가 아니면 해석 건수로 말한다 */
export function dataSourceText(src?: Partial<DataSource> | null): string {
  if (!src?.label) return "";
  const mixed = (src.total ?? 0) > (src.rows ?? 0) ? ` (전체 ${(src.total ?? 0).toLocaleString("ko-KR")}건)` : ""; // 불러온 표에 해석을 더한 스터디
  return `${src.label} ${(src.rows ?? 0).toLocaleString("ko-KR")}${src.kind === "table" ? "행" : "건"}${mixed}`;
}

/** 이 예측을 얼마나 믿어도 되는가: 학습 데이터 출처, 정확도(R²), 경고. 해석 없이 만든 모델일수록 받는 사람이 알아야 한다 */
export function ModelTrust({ model, source }: { model: SurrogateModel; source?: Partial<DataSource> | null }) {
  const q = qualityOf(model);
  const text = dataSourceText(source);
  return (
    <div className="model-trust">
      <div className="model-trust-row">
        {text && <span className="model-trust-src" title={source?.source ? `원본: ${source.source}` : undefined}><Database size={13} />학습 데이터: {text}</span>}
        <span className={`chip ${LEVEL_CHIP[q.level]}`} title="학습에 쓰지 않은 점으로 잰 값입니다(교차검증)">정확도 {q.label}</span>
        <span className="model-r2">{Object.entries(q.by_output).map(([k, v]) => (
          v.r2 == null ? <span key={k} className="chip" title="값이 모두 같아 정확도를 잴 수 없습니다">{k} 잴 수 없음</span>
            : <span key={k} className={`chip ${LEVEL_CHIP[v.level]}`} title={`${k} R² ${v.r2.toFixed(3)}`}>{k} R² {v.r2.toFixed(3)}</span>
        ))}</span>
        <span className="small muted">R²(결정계수)는 1에 가까울수록 정확합니다</span>
      </div>
      {q.warnings.length > 0 && (
        <div className={`notice ${q.level === "good" ? "info" : "warn"}`} role="status">
          <AlertTriangle size={15} />
          <ul>{q.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
    </div>
  );
}
