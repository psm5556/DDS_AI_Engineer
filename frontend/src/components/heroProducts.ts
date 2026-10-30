/** 로그인 첫 화면의 제품 형상. 일반적인 구조를 코드로 만든 예시이며 실제 제품 CAD·설계값이 아니다.
 *  파트는 격자 면(sheet)을 이어 만들고, 절점 수는 고정한 채 치수만 바꿔 형상이 연속으로 변한다. 응력은 형상을 보여 주기 위한 근사값이다. */
export type Tone = "metal" | "rubber" | "inner";
/** [x, y, z, 응력비 0~1] */
type P = [number, number, number, number];

export interface Sheet {
  nu: number; nv: number; closeU: boolean; tone: Tone;
  lineU: number; lineV: number; // 몇 칸마다 격자선을 그릴지
  at: (u: number, v: number, d: Design) => P;
}
export type Design = Record<string, number>;
export interface Product {
  name: string;
  /** 시점: 화면 폭의 절반에 들어갈 크기, 바라볼 중심 */
  span: number; center: [number, number, number];
  /** 보이는 시간(초)과 시점: 방위각(시작, 초당 회전), 앙각 */
  seconds: number; yaw: [number, number]; pitch: number;
  sheets: Sheet[];
  design: (tau: number) => Design;
  readout: (d: Design) => [string, string, string][];
}

const TAU = Math.PI * 2;
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const bump = (x: number, c: number, w: number) => Math.exp(-(((x - c) / w) ** 2));

function sheet(nu: number, nv: number, tone: Tone, at: Sheet["at"], o: { closeU?: boolean; lineU?: number; lineV?: number } = {}): Sheet {
  return { nu, nv, tone, at, closeU: !!o.closeU, lineU: o.lineU ?? 1, lineV: o.lineV ?? 1 };
}

/** 닫힌 단면(a, b)을 축(y)을 따라 민 기둥: 옆면 + 양 끝 뚜껑. place로 제자리에 놓는다. 면마다 절점을 따로 둬 모서리가 또렷하다 */
function prism(o: {
  n: number; along?: number; tone?: Tone; lineU?: number;
  section: (u: number, d: Design) => [number, number];
  span: (d: Design) => [number, number];
  place: (a: number, y: number, b: number, d: Design) => [number, number, number];
  ratio?: (u: number, v: number, d: Design) => number;
  center?: (d: Design) => [number, number];
}): Sheet[] {
  const tone = o.tone ?? "metal", ratio = o.ratio ?? (() => 0);
  const mid = o.center ?? (() => [0, 0] as [number, number]);
  const side = sheet(o.n, o.along ?? 1, tone, (u, v, d) => {
    const [a, b] = o.section(u, d), [y0, y1] = o.span(d);
    return [...o.place(a, lerp(y0, y1, v), b, d), ratio(u, v, d)];
  }, { closeU: true, lineU: o.lineU ?? 1 });
  const cap = (end: 0 | 1) => sheet(o.n, 1, tone, (u, v, d) => {
    const [a, b] = o.section(u, d), [c0, c1] = mid(d), [y0, y1] = o.span(d);
    return [...o.place(lerp(c0, a, v), end ? y1 : y0, lerp(c1, b, v), d), ratio(u, end, d)];
  }, { closeU: true, lineU: o.lineU ?? 1, lineV: 2 });
  return [side, cap(0), cap(1)];
}

/** 둥근 끝을 가진 막대 단면: (0,0) 반지름 r0에서 (len,0) 반지름 r1까지 */
function stadium(u: number, len: number, r0: number, r1: number): [number, number] {
  const t = u * 2;
  if (t < 1) { const a = -Math.PI / 2 + Math.PI * t; return [len + r1 * Math.cos(a), r1 * Math.sin(a)]; }
  const a = Math.PI / 2 + Math.PI * (t - 1);
  return [r0 * Math.cos(a), r0 * Math.sin(a)];
}

function rectSection(u: number, x0: number, x1: number, z0: number, z1: number): [number, number] {
  const t = (u * 4) % 4, f = t % 1;
  if (t < 1) return [lerp(x0, x1, f), z1];
  if (t < 2) return [x1, lerp(z1, z0, f)];
  if (t < 3) return [lerp(x1, x0, f), z0];
  return [x0, lerp(z0, z1, f)];
}

/* ---------- 폴더블 힌지 Assy: 맞물려 도는 두 축의 기어, 암, 양쪽 날개, 가운데 하우징 ---------- */
function hingeAssy(): Product {
  const LEN = 46, TEETH = 12;
  /** 축을 중심으로 접힘 각도만큼 돌린다(오른쪽 side=1, 왼쪽 side=-1) */
  const onAxis = (side: number) => (a: number, y: number, b: number, d: Design): [number, number, number] => {
    const c = Math.cos(d.phi), s = Math.sin(d.phi);
    return [side * (d.a + a * c - b * s), y, a * s + b * c];
  };
  const stress = (d: Design) => clamp01(d.sigma / 300);
  const sheets: Sheet[] = [];

  // 가운데 하우징: 위는 평평하고 아래는 둥근 통
  sheets.push(...prism({
    n: 48, along: 12, lineU: 4,
    section: (u, d) => {
      const w = 2 * d.a + 3.2;
      const t = u * 2;
      if (t < 1) return [lerp(-w, w, t), -d.r - 0.5];
      const a = Math.PI * (t - 1);
      return [w * Math.cos(a), -d.r - 0.5 - (d.r + 2.4) * Math.sin(a)];
    },
    span: () => [-LEN / 2 - 1.5, LEN / 2 + 1.5],
    place: (a, y, b) => [a, y, b],
    center: (d) => [0, -d.r - 1.6],
    ratio: (_u, v) => 0.06 + 0.1 * bump(v, 0.5, 0.45),
  }));

  for (const side of [1, -1]) {
    const place = onAxis(side);
    // 축
    sheets.push(...prism({
      n: 16, along: 1, lineU: 16,
      section: (u) => [0.95 * Math.cos(u * TAU), 0.95 * Math.sin(u * TAU)],
      span: () => [-LEN / 2 - 2.4, LEN / 2 + 2.4],
      place: (a, y, b, d) => { const [x, yy, z] = place(0, y, 0, d); return [x + a, yy, z + b]; },
    }));
    // 기어: 두 축이 맞물려 서로 반대로 돈다
    for (const y0 of [-15, -5.5, 5.5, 15]) {
      sheets.push(...prism({
        n: TEETH * 8, along: 1, lineU: 8,
        section: (u, d) => {
          const th = u * TAU;
          const tooth = Math.tanh(2.6 * Math.cos(TEETH * th)) * 0.5; // 사다리꼴에 가까운 이
          const r = d.a - 0.05 + tooth * 0.75;
          return [r * Math.cos(th), r * Math.sin(th)];
        },
        span: () => [y0 - 1.5, y0 + 1.5],
        place: (a, y, b, d) => {
          // 오른쪽·왼쪽 기어의 이가 어긋나 맞물리도록 왼쪽을 반 피치 돌린다
          const ang = d.phi + (side < 0 ? Math.PI / TEETH : 0);
          const c = Math.cos(ang), s = Math.sin(ang);
          return [side * (d.a + a * c - b * s), y, a * s + b * c];
        },
        ratio: (u, _v, d) => 0.15 + 0.55 * stress(d) * Math.max(0, Math.cos(TEETH * u * TAU)),
      }));
    }
    // 암: 축에서 날개까지 잇는 판. 뿌리에 응력이 모인다
    for (const y0 of [-19.5, -10.2, 10.2, 19.5]) {
      sheets.push(...prism({
        n: 24, along: 1, lineU: 6,
        section: (u, d) => stadium(u, d.a + 8, 2.5, 1.5),
        span: () => [y0 - 1.1, y0 + 1.1],
        place,
        ratio: (u, _v, d) => {
          const [a] = stadium(u, d.a + 8, 2.5, 1.5);
          return clamp01(stress(d) * (1.05 - a / (d.a + 8)));
        },
        center: (d) => [(d.a + 8) / 2, 0],
      }));
    }
    // 날개(휴대폰 몸체에 붙는 브래킷): 뿌리 쪽이 빨개진다
    sheets.push(...prism({
      n: 64, along: 18, lineU: 4,
      section: (u, d) => {
        const x0 = d.a + 6, x1 = x0 + d.w;
        return rectSection(u, x0, x1, -d.t / 2, d.t / 2);
      },
      span: () => [-LEN / 2 - 2.4, LEN / 2 + 2.4],
      place,
      center: (d) => [d.a + 6 + d.w / 2, 0],
      ratio: (u, v, d) => {
        const [a] = rectSection(u, 0, 1, 0, 1);
        const pin = Math.max(bump(v, 0.09, 0.08), bump(v, 0.28, 0.08), bump(v, 0.72, 0.08), bump(v, 0.91, 0.08));
        return clamp01(stress(d) * (1 - a) ** 1.6 * (0.55 + 0.6 * pin));
      },
    }));
    // 날개의 보강 리브와 체결 보스
    for (const y0 of [-LEN / 2 + 1, 0, LEN / 2 - 1]) {
      sheets.push(...prism({
        n: 16, along: 1, lineU: 4,
        section: (u, d) => rectSection(u, d.a + 6, d.a + 6 + d.w * 0.86, d.t / 2 - 0.05, d.t / 2 + 1.1),
        span: () => [y0 - 0.55, y0 + 0.55],
        place,
        center: (d) => [d.a + 6 + d.w * 0.43, d.t / 2 + 0.5],
      }));
    }
    for (const [fx, y0] of [[0.55, -11.5], [0.55, 11.5], [0.86, -11.5], [0.86, 11.5]]) {
      sheets.push(...prism({
        n: 20, along: 1, lineU: 5,
        section: (u) => [1.5 * Math.cos(u * TAU), 1.5 * Math.sin(u * TAU)],
        span: (d) => [d.t / 2 - 0.05, d.t / 2 + 0.95],
        place: (a, y, b, d) => place(d.a + 6 + d.w * fx + a, y0 + b, y, d),
      }));
    }
  }
  return {
    name: "폴더블 힌지 Assy", span: 62, center: [0, 0, 7], seconds: 13, yaw: [-1.25, 0.035], pitch: 0.62, sheets,
    design: (tau) => {
      const phi = 0.74 * (1 - Math.cos(tau * 0.62)); // 펼침 0° ↔ 접힘 쪽으로 약 85°
      const a = 4.3 + 0.7 * Math.sin(tau * 0.41);
      const t = 1.5 + 0.55 * Math.sin(tau * 0.29 + 1);
      const w = 26 + 4 * Math.sin(tau * 0.23 + 2);
      return { phi, a, t, w, r: a, sigma: 40 + 150 * phi * (1.5 / t) ** 2 * (4.3 / a) };
    },
    readout: (d) => [
      ["접힘 각도", ((2 * d.phi * 180) / Math.PI).toFixed(0), "°"], ["힌지 폭", (4 * d.a + 6.4).toFixed(1), "mm"],
      ["날개 두께", d.t.toFixed(2), "mm"], ["최대 응력", d.sigma.toFixed(0), "MPa"],
    ],
  };
}

/* ---------- 드럼세탁기 Tub Assy: 리브 있는 터브, 스파이더 모양 뒷면, 베어링 하우징, 앞면 도어 고무, 안쪽 드럼, 무게추 ---------- */
function tubAssy(): Product {
  const N = 132; // 둘레 칸 수
  const onRing = (x: number, r: number, th: number): [number, number, number] => [x, r * Math.cos(th), r * Math.sin(th)];
  const spoke = (th: number) => Math.max(0, Math.cos(3 * th)) ** 10; // 3갈래 스파이더 리브
  const load = (d: Design) => clamp01(d.sigma / 62);
  const lugs: [number, number, number][] = []; // 파트 위치(각도, 축 방향 비율, 세기) — 그 둘레 터브에 응력이 모인다
  const damper = [-Math.PI / 2 - 0.62, -Math.PI / 2 + 0.62], spring = [Math.PI / 2 - 0.5, Math.PI / 2 + 0.5];
  for (const th of damper) lugs.push([th, 0.32, 1]);
  for (const th of spring) lugs.push([th, 0.62, 0.8]);
  const angDist = (a: number, b: number) => Math.atan2(Math.sin(a - b), Math.cos(a - b));
  const sheets: Sheet[] = [];

  // 터브 몸통: 둘레 비드 3줄 + 길이 방향 리브
  sheets.push(sheet(N, 36, "metal", (u, v, d) => {
    const th = u * TAU;
    const bead = bump(v, 0.3, 0.04) + bump(v, 0.5, 0.04) + bump(v, 0.7, 0.04);
    const longi = Math.max(0, Math.cos(10 * th)) ** 18;
    const r = d.R + d.rib * (0.7 * bead + 0.45 * longi * clamp01(1 - v));
    const near = lugs.reduce((m, [a, f, k]) => Math.max(m, k * bump(angDist(th, a), 0, 0.28) * bump(v, f, 0.14)), 0);
    return [...onRing(-d.D / 2 + d.D * v, r, th), clamp01(load(d) * (0.04 + 0.3 * (1 - v) ** 3 + 0.95 * near))];
  }, { closeU: true, lineU: 6, lineV: 3 }));

  // 뒷면: 볼록한 원판 + 스파이더 리브. 베어링 쪽으로 응력이 모인다
  sheets.push(sheet(N, 16, "metal", (u, v, d) => {
    const th = u * TAU;
    const s = lerp(d.hub, d.R, v);
    const sp = spoke(th) * Math.sin(Math.PI * clamp01(v * 0.92 + 0.08)) ** 0.5;
    const x = -d.D / 2 - 26 * (1 - v) ** 1.2 - (0.9 + d.rib * 2.4) * sp;
    return [...onRing(x, s, th), clamp01(load(d) * ((1 - v) ** 1.5 * (0.7 + 0.5 * spoke(th)) + 0.1))];
  }, { closeU: true, lineU: 6, lineV: 2 }));

  // 베어링 하우징과 축
  const rearX = (d: Design) => -d.D / 2 - 26;
  const axisPrism = (r: (d: Design) => number, x0: (d: Design) => number, x1: (d: Design) => number, ratio: number) => prism({
    n: 36, along: 1, lineU: 6,
    section: (u, d) => [r(d) * Math.cos(u * TAU), r(d) * Math.sin(u * TAU)],
    span: (d) => [x0(d), x1(d)],
    place: (a, y, b) => [y, a, b],
    ratio: (_u, _v, d) => clamp01(load(d) * ratio),
  });
  sheets.push(...axisPrism((d) => d.hub, (d) => rearX(d) - 34, rearX, 0.95));
  sheets.push(...axisPrism((d) => d.hub * 0.42, (d) => rearX(d) - 72, (d) => rearX(d) - 34, 0.55));

  // 앞면: 도어 쪽으로 좁아지는 고리
  sheets.push(sheet(N, 10, "metal", (u, v, d) => {
    const th = u * TAU, k = v * v * (3 - 2 * v);
    return [...onRing(d.D / 2 + 34 * k, lerp(d.R, d.open, v), th), clamp01(load(d) * 0.12)];
  }, { closeU: true, lineU: 6, lineV: 2 }));

  // 도어 고무(가스켓): 주름 있는 고리
  sheets.push(sheet(N, 14, "rubber", (u, v, d) => {
    const th = u * TAU, a = v * TAU;
    const tube = 19 + 2.4 * Math.sin(3 * a);
    return [...onRing(d.D / 2 + 38 + tube * Math.sin(a), d.open - 6 + tube * Math.cos(a), th), 0];
  }, { closeU: true, lineU: 6, lineV: 14 }));

  // 안쪽 드럼: 리프터 3개. 도어로 들여다보인다
  sheets.push(sheet(N, 8, "inner", (u, v, d) => {
    const th = u * TAU;
    const lift = Math.max(0, Math.cos(3 * th + 0.5)) ** 14 * 30;
    return [...onRing(lerp(-d.D / 2 + 8, d.D / 2 - 3, v), d.R * 0.9 - lift, th), 0];
  }, { closeU: true, lineU: 3, lineV: 1 }));
  sheets.push(sheet(N, 4, "inner", (u, v, d) => [...onRing(-d.D / 2 + 8, d.R * 0.9 * v, u * TAU), 0], { closeU: true, lineU: 11, lineV: 1 }));
  // 드럼 앞 테두리: 앞면 고리 안쪽에 숨어 도어 쪽으로 이어진다
  sheets.push(sheet(N, 2, "inner", (u, v, d) => [...onRing(d.D / 2 - 3, lerp(d.R * 0.9, d.open - 16, v), u * TAU), 0], { closeU: true, lineU: 22, lineV: 2 }));

  // 터브 겉에 붙는 파트: 무게추, 스프링 고리, 댐퍼 러그, 배수구
  const polarPart = (o: { th0: (d: Design) => number; th1: (d: Design) => number; f0: number; f1: number; r0: (d: Design) => number; r1: (d: Design) => number; ratio?: number; tone?: Tone }) => prism({
    n: 32, along: 1, lineU: 8, tone: o.tone,
    section: (u, d) => rectSection(u, o.th0(d), o.th1(d), o.r0(d), o.r1(d)),
    span: (d) => [-d.D / 2 + d.D * o.f0, -d.D / 2 + d.D * o.f1],
    place: (a, y, b) => onRing(y, b, a),
    center: (d) => [(o.th0(d) + o.th1(d)) / 2, (o.r0(d) + o.r1(d)) / 2],
    ratio: (_u, _v, d) => clamp01(load(d) * (o.ratio ?? 0)),
  });
  // 무게추: 앞쪽 위·양옆에 두꺼운 호 모양 덩어리
  for (const [c, half] of [[Math.PI / 2, 0.52], [0.1, 0.3], [Math.PI - 0.1, 0.3]]) {
    sheets.push(...polarPart({ th0: () => c - half, th1: () => c + half, f0: 0.8, f1: 0.99, r0: (d) => d.R - 1, r1: (d) => d.R + 30, ratio: 0.08 }));
  }
  for (const th of spring) {
    sheets.push(...polarPart({ th0: () => th - 0.07, th1: () => th + 0.07, f0: 0.55, f1: 0.68, r0: (d) => d.R, r1: (d) => d.R + 30, ratio: 0.75 }));
  }
  for (const th of damper) {
    sheets.push(...polarPart({ th0: () => th - 0.11, th1: () => th + 0.11, f0: 0.22, f1: 0.42, r0: (d) => d.R, r1: (d) => d.R + 36, ratio: 0.95 }));
  }
  sheets.push(...prism({ // 배수구
    n: 24, along: 1, lineU: 6,
    section: (u) => [20 * Math.cos(u * TAU), 20 * Math.sin(u * TAU)],
    span: (d) => [d.R - 4, d.R + 40],
    place: (a, y, b, d) => [d.D * 0.22 + a, b, -y],
    ratio: (_u, _v, d) => clamp01(load(d) * 0.2),
  }));

  return {
    name: "드럼세탁기 Tub Assy", span: 680, center: [-20, 0, 0], seconds: 17, yaw: [-0.55, 0.19], pitch: 0.34, sheets,
    design: (tau) => {
      const R = 272 + 24 * Math.sin(tau * 0.37);
      const D = 330 + 46 * Math.sin(tau * 0.27 + 1);
      const rib = 7.5 + 3.5 * Math.sin(tau * 0.46 + 2);
      return { R, D, rib, hub: 66, open: R * 0.66, sigma: 34 + 26 * (R / 272) ** 2 * (D / 330) * (7.5 / rib) ** 0.7 };
    },
    readout: (d) => [
      ["지름", (2 * d.R).toFixed(0), "mm"], ["깊이", d.D.toFixed(0), "mm"],
      ["리브 높이", d.rib.toFixed(1), "mm"], ["최대 응력", d.sigma.toFixed(0), "MPa"],
    ],
  };
}

/* ---------- 격자 면 → 그릴 수 있는 한 벌(절점 수 고정) ---------- */
export interface Compiled {
  product: Product;
  count: number;
  tris: Uint32Array;
  lines: Uint32Array;
  tones: Tone[];
  fill: (d: Design, pos: Float32Array, ratio: Float32Array) => void;
}

export function compile(product: Product): Compiled {
  let count = 0;
  const tris: number[] = [], lines: number[] = [], tones: Tone[] = [];
  const grids = product.sheets.map((s) => {
    const cols = s.closeU ? s.nu : s.nu + 1, rows = s.nv + 1, base = count;
    const id = (i: number, j: number) => base + (s.closeU ? i % cols : i) + j * cols;
    for (let n = 0; n < cols * rows; n++) tones.push(s.tone);
    for (let i = 0; i < s.nu; i++) {
      for (let j = 0; j < s.nv; j++) {
        const [p, q, r, t] = [id(i, j), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1)];
        tris.push(p, q, r, p, r, t);
        if (i % s.lineU === 0) lines.push(p, t);
        if (j % s.lineV === 0) lines.push(p, q);
        if (j === s.nv - 1) lines.push(t, r);
        if (!s.closeU && i === s.nu - 1) lines.push(q, r);
      }
    }
    count += cols * rows;
    return { s, cols, rows, base };
  });
  return {
    product, count, tones, tris: Uint32Array.from(tris), lines: Uint32Array.from(lines),
    fill: (d, pos, ratio) => {
      for (const { s, cols, rows, base } of grids) {
        for (let j = 0; j < rows; j++) {
          const v = j / s.nv;
          for (let i = 0; i < cols; i++) {
            const n = base + i + j * cols;
            const p = s.at(i / s.nu, v, d);
            pos[n * 3] = p[0]; pos[n * 3 + 1] = p[1]; pos[n * 3 + 2] = p[2];
            ratio[n] = p[3];
          }
        }
      }
    },
  };
}

export const PRODUCTS = [hingeAssy, tubAssy];
