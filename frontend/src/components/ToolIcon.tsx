import { Check } from "lucide-react";

/** CAD·CAE 툴 앱 아이콘: 회사 로고 대신(상표) 툴마다 고유한 색 + 약자.
 *  상태: ready = 컬러 + 초록 체크 · partial = 컬러 + 주황 표시 · missing = 흐리게 · 모름(진단 전) = 컬러만 */
const ICON: Record<string, [string, string, string?]> = {
  // id: [약자, 색, 글자색(밝은 바탕일 때)]
  nx: ["NX", "#00a3a3"],
  catia: ["CA", "#0b5394"],
  solidworks: ["SW", "#d52b1e"],
  creo: ["Cr", "#4caf50"],
  ansa: ["AN", "#e2001a"],
  altair: ["HW", "#1f3a68"],
  simcenter3d: ["S3", "#00897b"],
  abaqus: ["Ab", "#1b4f9c"],
  ansys: ["An", "#ffb71b", "#1a1a1a"],
  msc_nastran: ["Na", "#0072bc"],
  lsdyna: ["LS", "#f9a825", "#1a1a1a"],
  starccm: ["S+", "#00838f"],
  fluent: ["Fl", "#ffca28", "#1a1a1a"],
  openfoam: ["OF", "#1b5e9c"],
  flotherm: ["FT", "#26a69a"],
  comsol: ["CS", "#c62828"],
  adams: ["Ad", "#0091ea"],
  recurdyn: ["RD", "#3949ab"],
  moldflow: ["MF", "#0696d7"],
  moldex3d: ["M3", "#e65100"],
  amesim: ["Am", "#00796b"],
  ncode: ["nC", "#6a1b9a"],
  lighttools: ["LT", "#8e24aa"],
};

/** 아이콘 아래 짧은 이름(긴 정식 이름은 툴팁·자세히 패널에) */
const SHORT: Record<string, string> = {
  ansa: "ANSA · META", altair: "HyperWorks", simcenter3d: "Simcenter 3D", ansys: "Ansys Mechanical", msc_nastran: "MSC Nastran",
  lsdyna: "LS-DYNA", fluent: "Fluent", flotherm: "Flotherm", comsol: "COMSOL", moldflow: "Moldflow", amesim: "Amesim",
  ncode: "nCode DesignLife", creo: "Creo", nx: "NX", catia: "CATIA V5",
};
export const toolLabel = (id: string, name: string) => SHORT[id] ?? name;

function fallback(id: string, name: string): [string, string] {
  const abbr = name.replace(/[^A-Za-z0-9가-힣 ]/g, " ").split(/\s+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 2) || id.slice(0, 2);
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 360;
  return [abbr, `hsl(${h} 55% 42%)`];
}

export function ToolIcon({ id, name, status, size = 64 }: {
  id: string; name: string; status?: "ready" | "partial" | "missing"; size?: number;
}) {
  const [abbr, color, ink] = ICON[id] ?? fallback(id, name);
  return (
    <span className={`tool-icon ${status ?? "unknown"}`} style={{ width: size, height: size, fontSize: size * 0.34, borderRadius: size * 0.24,
      background: `linear-gradient(145deg, ${color}, color-mix(in srgb, ${color} 72%, #000))`, color: ink ?? "#fff" }} aria-hidden="true">
      {abbr}
      {status === "ready" && <i className="tool-icon-badge ok"><Check size={Math.round(size * 0.2)} strokeWidth={3.2} /></i>}
      {status === "partial" && <i className="tool-icon-badge warn">!</i>}
    </span>
  );
}
