import { useQuery } from "@tanstack/react-query";
import { Box, Grid3x3, Lock, LockOpen, RotateCcw, Waves } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { get, post } from "../api";
import { fmt } from "../format";
import { useTheme } from "../theme";
import type { FieldModel } from "../types";
import { FieldViewer3D, turbo } from "./FieldViewer3D";

interface Mesh { field: string; n_nodes: number; shape_varies: boolean; triangles_b64: string; n_triangles: number }
interface Pred { points_b64: string; values_b64: string; uncertainty_b64: string; min: number; max: number; max_uncertainty: number }

const decode = (b64: string) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)).buffer;

/** Simulator의 3D 형상·필드 보기: 설계변수를 바꾸면 예측 형상과 필드 분포가 바로 바뀐다 */
export function Field3DPanel({ listUrl, baseUrl, params }: { listUrl: string; baseUrl: (id: number) => string; params: Record<string, number> }) {
  const { theme } = useTheme();
  const { data: list } = useQuery({ queryKey: ["field-models", listUrl], queryFn: () => get<FieldModel[]>(listUrl) });
  const [fid, setFid] = useState<number | null>(null);
  const fm = list?.find((f) => f.id === fid) ?? list?.[0];
  const { data: mesh } = useQuery({ queryKey: ["field-mesh", fm?.id], enabled: !!fm, queryFn: () => get<Mesh>(`${baseUrl(fm!.id)}/mesh`) });
  const [pred, setPred] = useState<Pred | null>(null);
  const [showUnc, setShowUnc] = useState(false);
  const [wire, setWire] = useState(false);
  const [lockRange, setLockRange] = useState<[number, number] | null>(null);
  const [reset, setReset] = useState(0);

  useEffect(() => {
    if (!fm || !Object.keys(params).length) return;
    const t = setTimeout(() => { post<Pred>(`${baseUrl(fm.id)}/predict`, { params }).then(setPred).catch(() => {}); }, 150);
    return () => clearTimeout(t);
  }, [fm, params, baseUrl]);

  const arrays = useMemo(() => {
    if (!pred || !mesh) return null;
    return {
      points: new Float32Array(decode(pred.points_b64)), values: new Float32Array(decode(pred.values_b64)),
      unc: new Float32Array(decode(pred.uncertainty_b64)), triangles: new Uint32Array(decode(mesh.triangles_b64)),
    };
  }, [pred, mesh]);

  if (!list?.length) return null;
  const vals = showUnc ? arrays?.unc : arrays?.values;
  const range: [number, number] = showUnc ? [0, pred?.max_uncertainty || 1] : lockRange ?? [pred?.min ?? 0, pred?.max ?? 1];
  const stops = Array.from({ length: 11 }, (_, i) => { const [r, g, b] = turbo(i / 10); return `rgb(${r * 255},${g * 255},${b * 255}) ${i * 10}%`; });

  return (
    <section className="block field3d">
      <div className="block-head">
        <h3><Box size={16} />3D 형상·필드</h3>
        {list.length > 1 && (
          <span className="seg">{list.map((f) => <button key={f.id} className={fm?.id === f.id ? "on" : ""} onClick={() => setFid(f.id)}>{f.field}</button>)}</span>
        )}
        <span className="small muted">{fm?.shape_varies ? "형상·필드 함께 예측" : "필드 예측"} · 노드 {fm?.n_nodes.toLocaleString()} · 학습 {fm?.n_train}건</span>
      </div>
      {/* 보기 옵션: 아이콘만으로는 뜻이 모호해 글자를 붙인다 */}
      <div className="view-tools" role="toolbar" aria-label="3D 보기 옵션">
        <button className={`tog ${showUnc ? "on" : ""}`} title="데이터가 부족해 예측을 덜 믿을 수 있는 곳" aria-pressed={showUnc}
          onClick={() => setShowUnc(!showUnc)}><Waves size={14} />불확실도</button>
        <button className={`tog ${lockRange ? "on" : ""}`} title="지금 색 범위를 고정해 설계끼리 비교" aria-pressed={!!lockRange}
          onClick={() => setLockRange(lockRange ? null : [pred?.min ?? 0, pred?.max ?? 1])} disabled={showUnc}>
          {lockRange ? <Lock size={13} /> : <LockOpen size={13} />}색 범위 고정</button>
        <button className={`tog ${wire ? "on" : ""}`} aria-pressed={wire} onClick={() => setWire(!wire)}><Grid3x3 size={14} />메쉬 선</button>
        <span className="grow" />
        <button className="tog" onClick={() => setReset((n) => n + 1)}><RotateCcw size={13} />시점 초기화</button>
      </div>
      <div className="field3d-body">
        {arrays && vals ? (
          <FieldViewer3D points={arrays.points} triangles={arrays.triangles} values={vals} vmin={range[0]} vmax={range[1]}
            wireframe={wire} dark={theme === "dark"} resetKey={reset} />
        ) : <div className="busy"><span className="spinner" /> 3D 예측 중</div>}
        <div className="colorbar" aria-label="색 범례">
          <span className="t">{showUnc ? `${fm?.field} 불확실도` : fm?.field}</span>
          <span className="v">{fmt(range[1], 4)}</span>
          <i style={{ background: `linear-gradient(to top, ${stops.join(", ")})` }} />
          <span className="v">{fmt(range[0], 4)}</span>
        </div>
      </div>
      <p className="small muted" style={{ marginTop: 8 }}>끌어서 회전 · 휠로 확대 · 오른쪽 버튼으로 이동. 최대 {fm?.field} = <b>{fmt(pred?.max, 4)}</b></p>
    </section>
  );
}
