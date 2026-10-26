import Plotly from "plotly.js-dist-min";
import type { Config } from "plotly.js";
import createPlotlyComponent from "react-plotly.js/factory";

export const Plot = createPlotlyComponent(Plotly);

export const COLORS = { mean: "#0e6e6e", sigma: "#6b4fa8", epi: "#6f7f8e", ok: "#2e7d4f", warn: "#9a6412", ink: "#17212b" };
// 평균: 청록 계열 순차 색상 / 산포: 보라 계열 / 불확실성: 회색 / 규격 확률: 초록
export const SCALE_MEAN: [number, string][] = [[0, "#f2f8f7"], [0.35, "#9fd1cc"], [0.7, "#2f8f8a"], [1, "#0a4747"]];
export const SCALE_SIGMA: [number, string][] = [[0, "#f6f3fb"], [0.4, "#c5b5e3"], [0.75, "#8566c2"], [1, "#3f2a6e"]];
export const SCALE_EPI: [number, string][] = [[0, "#f6f7f8"], [0.5, "#b7c1ca"], [1, "#46525e"]];
export const SCALE_PROB: [number, string][] = [[0, "#f7f3ea"], [0.5, "#cde6d4"], [0.8, "#62ab7d"], [1, "#1d5d37"]];

export const baseLayout = {
  margin: { l: 56, r: 12, t: 8, b: 44 },
  font: { family: "IBM Plex Sans KR, Pretendard, Malgun Gothic, sans-serif", size: 12, color: "#8a8a94" },
  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
  hoverlabel: { font: { family: "IBM Plex Sans KR, Pretendard, Malgun Gothic, sans-serif" } },
};
export const plotConfig: Partial<Config> = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };
