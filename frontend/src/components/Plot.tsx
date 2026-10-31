import Plotly from "plotly.js-dist-min";
import type { Config } from "plotly.js";
import createPlotlyComponent from "react-plotly.js/factory";
import { token } from "../design/token";

export const Plot = createPlotlyComponent(Plotly);

/** 그래프 색: 격자·글자·강조·계열(주홍 → 청록 → 보라 → 호박 → 회청 → 빨강) */
export function chartColors() {
  return {
    grid: token("--line"), text: token("--ink-3"), muted: token("--ink-4"), brand: token("--brand"), err: token("--err"), mean: token("--mean"),
    warn: token("--warn-solid"), bg: token("--panel-2"), ink: token("--ink"), band: token("--chart-band"),
    series: ["--brand", "--mean", "--sigma", "--warn-solid", "--epi", "--err"].map(token),
  };
}

export const baseLayout = {
  margin: { l: 56, r: 12, t: 8, b: 44 },
  font: { family: "Pretendard Variable, Pretendard, Malgun Gothic, sans-serif", size: 12 },
  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
  hoverlabel: { font: { family: "Pretendard Variable, Pretendard, Malgun Gothic, sans-serif" } },
};
export const plotConfig: Partial<Config> = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };
