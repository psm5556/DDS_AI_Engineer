import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { token } from "../design/token";
import { reducedMotion } from "../motion";

/** 로그인 첫인상: 구조해석·열유동해석·낙하해석의 실제 계산 결과를 후처리기처럼 보여 준다.
 *  결과는 scripts/hero/ 의 생성기가 CAD → 메쉬 → 해석으로 만든 파일이다(public/hero/). 형상·하중은 예시이며 실제 제품 데이터가 아니다.
 *  '움직임 줄이기'면 멈춘 한 장을, WebGL을 못 쓰면 CAD 윤곽선을 정지 그림으로 보인다. */
const IDS = ["structural", "thermal", "drop"];
const BANDS = 12;
const FADE = 0.6; // 해석이 바뀔 때 흐려지는 시간(초)

/** 후처리기의 기본 무지개 띠: 파랑(낮음) → 빨강(높음) */
function bandColors(): [number, number, number][] {
  const stops: [number, number, number][] = [[0, 0, 1], [0, 0.45, 1], [0, 0.85, 1], [0, 1, 0.55], [0.35, 1, 0], [0.85, 1, 0], [1, 0.75, 0], [1, 0.4, 0], [1, 0, 0]];
  return Array.from({ length: BANDS }, (_, i) => {
    const x = (i / (BANDS - 1)) * (stops.length - 1), k = Math.min(stops.length - 2, Math.floor(x)), f = x - k;
    return [0, 1, 2].map((c) => stops[k][c] + (stops[k + 1][c] - stops[k][c]) * f) as [number, number, number];
  });
}
const COLORS = bandColors();
const css = (c: number[]) => `rgb(${c.map((v) => Math.round(v * 255)).join(",")})`;

interface ArrayInfo { offset: number; type: string; shape: number[] }
interface Meta {
  id: string; kind: string; name: string; method: string; element: string; elements: number; nodes: number; field: string; unit: string;
  vmin: number; vmax: number; scale?: number; lo: number[]; span: number[]; mode: "designs" | "frames"; seconds: number;
  yaw: [number, number]; pitch: number; steps: (Record<string, number> & { maxNode: number })[]; readout: [string, string, string][];
  arrays: Record<string, ArrayInfo>; note?: string; fit?: number; center?: number[]; fall?: number; digits?: number; floor?: boolean; dy?: number;
  plane?: { x: [number, number]; z: [number, number]; y: number };
}
interface Result {
  meta: Meta; tris: Uint32Array; feature: Uint32Array; mesh: Uint32Array; pos: Int16Array; field: Uint8Array; frames: number; count: number;
  lines?: { pos: Int16Array; field: Uint8Array; index: Uint32Array; count: number; perLine: number };
  plane?: { field: Uint8Array; rows: number; cols: number };
}

const TYPED: Record<string, new (b: ArrayBuffer, o: number, n: number) => ArrayBufferView> = { uint32: Uint32Array, int16: Int16Array, uint8: Uint8Array, float32: Float32Array };
async function loadResult(id: string): Promise<Result | null> {
  try {
    const meta = (await (await fetch(`/hero/${id}.json`)).json()) as Meta;
    const buf = await (await fetch(`/hero/${id}.bin`)).arrayBuffer();
    const get = <T,>(name: string) => {
      const a = meta.arrays[name];
      return new TYPED[a.type](buf, a.offset, a.shape.reduce((x, y) => x * y, 1)) as unknown as T;
    };
    const [frames, count] = meta.arrays.pos.shape;
    const r: Result = { meta, tris: get("tris"), feature: get("feature"), mesh: get("mesh"), pos: get("pos"), field: get("field"), frames, count };
    if (meta.arrays.linePos) {
      const idx = get<Uint32Array>("lineIndex");
      let perLine = 1; // 한 유선의 점 수: 이어지지 않는 첫 지점까지
      while (perLine * 2 < idx.length && idx[perLine * 2] === perLine) perLine++;
      r.lines = { pos: get("linePos"), field: get("lineField"), index: idx, count: meta.arrays.linePos.shape[1], perLine: perLine + 1 };
    }
    if (meta.arrays.plane) {
      const [, cols, rows] = meta.arrays.plane.shape;
      r.plane = { field: get("plane"), rows, cols };
    }
    return r;
  } catch {
    return null; // 파일이 없으면 그 해석만 건너뛴다
  }
}

/** 시각 → 앞뒤 단계와 사이 비율 */
function timeline(r: Result, local: number): { a: number; b: number; f: number; rise: number } {
  const n = r.frames, m = r.meta;
  if (m.mode === "frames") { // 낙하: 떨어지는 구간 → 충돌 재생 → 마지막에서 잠깐 멈춤
    const fall = m.fall ?? 0.9, play = m.seconds - fall - 1.4;
    if (local < fall) return { a: 0, b: 0, f: 0, rise: 1 - local / fall };
    const x = Math.min(1, (local - fall) / play) * (n - 1), a = Math.min(n - 2, Math.floor(x));
    return { a, b: a + 1, f: x - a, rise: 0 };
  }
  const x = (local / m.seconds) * n, a = Math.floor(x) % n, f = x - Math.floor(x); // 설계: 각 설계에 머물다 다음으로 부드럽게
  const ease = f < 0.55 ? 0 : ((f - 0.55) / 0.45) ** 2 * (3 - 2 * ((f - 0.55) / 0.45));
  return { a, b: (a + 1) % n, f: ease, rise: 0 };
}

const fmtValue = (v: number, digits = 0) => v.toFixed(digits);

/** 정지 그림: CAD 윤곽선만 축측 투영한다 */
function StaticDrawing({ r }: { r: Result }) {
  const { path, box } = useMemo(() => {
    const { lo, span } = r.meta;
    const p = (n: number) => {
      const [x, y, z] = [0, 1, 2].map((c) => lo[c] + ((r.pos[n * 3 + c] + 32768) / 65535) * span[c]);
      return [x * 0.813 + y * 0.582, x * 0.338 - y * 0.472 - z * 0.814];
    };
    let d = "", x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (let i = 0; i < r.feature.length; i += 2) {
      const [a, b] = [p(r.feature[i]), p(r.feature[i + 1])];
      d += `M${a[0].toFixed(1)} ${a[1].toFixed(1)}L${b[0].toFixed(1)} ${b[1].toFixed(1)}`;
      x0 = Math.min(x0, a[0], b[0]); x1 = Math.max(x1, a[0], b[0]); y0 = Math.min(y0, a[1], b[1]); y1 = Math.max(y1, a[1], b[1]);
    }
    return { path: d, box: `${x0} ${y0} ${x1 - x0} ${y1 - y0}` };
  }, [r]);
  return <svg className="hero-static" viewBox={box} aria-hidden="true"><path d={path} vectorEffect="non-scaling-stroke" /></svg>;
}

const VERT = `
attribute float aS;
varying float vS; varying vec3 vN;
void main() { vS = aS; vN = normalMatrix * normal; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const FRAG = `
uniform sampler2D uBands; uniform float uOpacity;
varying float vS; varying vec3 vN;
void main() {
  vec3 base = texture2D(uBands, vec2(clamp(vS, 0.0, 0.9999), 0.5)).rgb;
  vec3 n = normalize(vN);
  float d = abs(dot(n, normalize(vec3(-0.25, 0.45, 0.85))));
  gl_FragColor = vec4(base * (0.62 + 0.42 * d), uOpacity);
}`;

/** 유선: 온도 띠 색 + 흐르는 방향으로 움직이는 밝기(공기가 흐르는 것이 보인다) */
const LINE_VERT = `
attribute float aS; attribute float aU;
varying float vS; varying float vU;
void main() { vS = aS; vU = aU; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const LINE_FRAG = `
uniform sampler2D uBands; uniform float uTime; uniform float uOpacity;
varying float vS; varying float vU;
void main() {
  vec3 base = texture2D(uBands, vec2(clamp(vS, 0.0, 0.9999), 0.5)).rgb;
  float pulse = fract(vU * 5.0 - uTime * 0.55);
  float warm = 0.35 + 0.65 * smoothstep(0.02, 0.2, vS); // 데워지지 않은 공기는 옅게: 볼 것은 뜨거운 곳이다
  gl_FragColor = vec4(base, uOpacity * warm * (0.3 + 0.7 * smoothstep(0.55, 1.0, pulse)));
}`;
/** 온도 단면: 고체 칸(값 0)은 비우고, 입구 온도에 가까운 공기는 옅게 */
const PLANE_VERT = `
varying vec2 vUv;
void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const PLANE_FRAG = `
uniform sampler2D uField; uniform sampler2D uBands; uniform float uOpacity;
varying vec2 vUv;
void main() {
  float raw = texture2D(uField, vUv).r;
  if (raw < 0.003) discard;
  float s = (raw * 255.0 - 1.0) / 254.0;
  vec3 base = texture2D(uBands, vec2(clamp(s, 0.0, 0.9999), 0.5)).rgb;
  gl_FragColor = vec4(base, uOpacity * smoothstep(0.05, 0.25, s) * 0.85);
}`;

export function HeroMesh() {
  const box = useRef<HTMLDivElement>(null);
  const out = useRef<(HTMLElement | null)[]>([]);
  const maxLabel = useRef<HTMLDivElement>(null);
  const maxText = useRef<HTMLElement>(null);
  const jump = useRef<(i: number) => void>(() => {});
  const [results, setResults] = useState<Result[]>([]);
  const [noGL, setNoGL] = useState(false);
  const [shown, setShown] = useState(0);
  const current = results[shown] as Result | undefined;

  useEffect(() => {
    let live = true;
    // 하나씩 받는다: 첫 결과가 오면 바로 보이고, 입력을 막지 않는다
    (async () => {
      for (const id of IDS) {
        const r = await loadResult(id);
        if (live && r) setResults((old) => (old.some((x) => x.meta.id === r.meta.id) ? old : [...old, r]));
      }
    })();
    return () => { live = false; };
  }, []);

  const ready = results.length > 0;
  const liveResults = useRef<Result[]>([]);
  liveResults.current = results;

  useEffect(() => {
    if (noGL || !ready) return;
    const el = box.current!;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setNoGL(true); // WebGL을 못 쓰는 PC
      return;
    }
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    renderer.setClearColor(0x000000, 0);
    el.appendChild(renderer.domElement);
    const canvas = renderer.domElement;

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -8000, 8000); // 후처리기처럼 원근 없이
    camera.up.set(0, 0, 1);

    const bandTex = new THREE.DataTexture(new Uint8Array(COLORS.flatMap((c) => [...c.map((v) => Math.round(v * 255)), 255])), BANDS, 1, THREE.RGBAFormat);
    bandTex.magFilter = THREE.NearestFilter; bandTex.minFilter = THREE.NearestFilter; bandTex.needsUpdate = true;
    const uniforms = { uBands: { value: bandTex }, uOpacity: { value: 1 } };
    const skinMat = new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: FRAG, uniforms, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
    const ink = new THREE.Color(0x101014);
    const meshMat = new THREE.LineBasicMaterial({ color: ink, transparent: true, opacity: 0.16 });
    const edgeMat = new THREE.LineBasicMaterial({ color: ink, transparent: true, opacity: 0.8 });

    const lineUniforms = { uBands: { value: bandTex }, uTime: { value: 0 }, uOpacity: { value: 1 } };
    const lineMat = new THREE.ShaderMaterial({ vertexShader: LINE_VERT, fragmentShader: LINE_FRAG, uniforms: lineUniforms, transparent: true, depthWrite: false });
    const floorMat = new THREE.LineBasicMaterial({ color: new THREE.Color(token("--ink-3")), transparent: true, opacity: 0.35 }); // 두 테마 모두 바탕 위에서 보이게

    interface Scene3D {
      r: Result; group: THREE.Group; position: THREE.BufferAttribute; scalar: THREE.BufferAttribute; skin: THREE.BufferGeometry; dispose: () => void;
      linePos?: THREE.BufferAttribute; lineS?: THREE.BufferAttribute;
      planeMats?: THREE.ShaderMaterial[]; key?: string;
    }
    const built = new Map<string, Scene3D>();
    const build = (r: Result): Scene3D => {
      const hit = built.get(r.meta.id);
      if (hit) return hit;
      const position = new THREE.BufferAttribute(new Float32Array(r.count * 3), 3);
      const scalar = new THREE.BufferAttribute(new Float32Array(r.count), 1);
      const skin = new THREE.BufferGeometry();
      skin.setAttribute("position", position);
      skin.setAttribute("aS", scalar);
      skin.setIndex(new THREE.BufferAttribute(r.tris, 1));
      const lines = (idx: Uint32Array, mat: THREE.Material) => {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", position);
        g.setIndex(new THREE.BufferAttribute(idx, 1));
        return { obj: new THREE.LineSegments(g, mat), g };
      };
      const a = lines(r.mesh, meshMat), b = lines(r.feature, edgeMat);
      const group = new THREE.Group();
      group.add(new THREE.Mesh(skin, skinMat), a.obj, b.obj);
      group.visible = false;
      group.traverse((o) => { o.frustumCulled = false; }); // 값이 계속 바뀌어 경계 구를 다시 구하지 않는다
      scene.add(group);
      const extra: (() => void)[] = [];
      const s: Scene3D = { r, group, position, scalar, skin, dispose: () => { skin.dispose(); a.g.dispose(); b.g.dispose(); extra.forEach((f) => f()); } };
      if (r.lines) { // 유선
        const g = new THREE.BufferGeometry();
        s.linePos = new THREE.BufferAttribute(new Float32Array(r.lines.count * 3), 3);
        s.lineS = new THREE.BufferAttribute(new Float32Array(r.lines.count), 1);
        const u = new Float32Array(r.lines.count);
        for (let i = 0; i < r.lines.count; i++) u[i] = (i % r.lines.perLine) / (r.lines.perLine - 1) + Math.floor(i / r.lines.perLine) * 0.37;
        g.setAttribute("position", s.linePos);
        g.setAttribute("aS", s.lineS);
        g.setAttribute("aU", new THREE.BufferAttribute(u, 1));
        g.setIndex(new THREE.BufferAttribute(r.lines.index, 1));
        const obj = new THREE.LineSegments(g, lineMat);
        obj.frustumCulled = false;
        group.add(obj);
        extra.push(() => g.dispose());
      }
      if (r.plane && r.meta.plane) { // 온도 단면: 설계마다 한 장
        const { x, z, y } = r.meta.plane;
        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array([x[0], y, z[0], x[1], y, z[0], x[1], y, z[1], x[0], y, z[1]]), 3));
        geo.setAttribute("uv", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]), 2));
        geo.setIndex([0, 1, 2, 0, 2, 3]);
        s.planeMats = [];
        for (let k = 0; k < r.frames; k++) {
          // 파일은 [설계, x, z] 순서다. 텍스처는 가로 z열 단위로 읽으므로 x·z를 바꿔 넣는다
          const data = new Uint8Array(r.plane.cols * r.plane.rows);
          for (let i = 0; i < r.plane.cols; i++) for (let j = 0; j < r.plane.rows; j++) data[j * r.plane.cols + i] = r.plane.field[(k * r.plane.cols + i) * r.plane.rows + j];
          const tex = new THREE.DataTexture(data, r.plane.cols, r.plane.rows, THREE.RedFormat);
          tex.magFilter = THREE.LinearFilter; tex.minFilter = THREE.LinearFilter; tex.needsUpdate = true;
          const mat = new THREE.ShaderMaterial({ vertexShader: PLANE_VERT, fragmentShader: PLANE_FRAG, side: THREE.DoubleSide, transparent: true, depthWrite: false,
            uniforms: { uField: { value: tex }, uBands: { value: bandTex }, uOpacity: { value: 0 } } });
          const mesh = new THREE.Mesh(geo, mat);
          mesh.frustumCulled = false;
          group.add(mesh);
          s.planeMats.push(mat);
          extra.push(() => { tex.dispose(); mat.dispose(); });
        }
        extra.push(() => geo.dispose());
      }
      if (r.meta.floor) { // 바닥: 낙하 시작점 아래의 얇은 격자
        const c = r.meta.center ?? [0, 0, 0], half = (r.meta.fit ?? 120) * 0.56, step = half / 7, pts: number[] = [];
        for (let i = -7; i <= 7; i++) {
          pts.push(c[0] - half, c[1] + i * step, 0, c[0] + half, c[1] + i * step, 0, c[0] + i * step, c[1] - half, 0, c[0] + i * step, c[1] + half, 0);
        }
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pts), 3));
        const obj = new THREE.LineSegments(g, floorMat);
        obj.frustumCulled = false;
        group.add(obj);
        extra.push(() => g.dispose());
      }
      built.set(r.meta.id, s);
      return s;
    };

    let index = 0, start = performance.now() / 1000;
    const frame = () => {
      const r = liveResults.current[index];
      if (!r) return;
      const m = r.meta;
      const w = el.clientWidth || 1, h = el.clientHeight || 1;
      const size = m.fit ?? Math.max(...m.span) * 0.72;
      const span = size * Math.max(1, 1000 / w) * Math.max(1, (w / h) * 0.62); // 세로가 짧아도 잘리지 않게
      const high = span * h / w;
      // 모델은 가운데보다 오른쪽 아래로: 왼쪽 위의 큰 제목과 오른쪽 범례를 피한다
      const dx = span * 0.1, dy = high * (m.dy ?? 0.16);
      camera.left = -span - dx; camera.right = span - dx;
      camera.top = high + dy; camera.bottom = -high + dy;
      camera.updateProjectionMatrix();
    };
    const resize = () => {
      renderer.setSize(el.clientWidth || 1, el.clientHeight || 1, false);
      canvas.style.width = "100%";
      canvas.style.height = "100%";
      frame();
    };

    let mx = 0, my = 0, yaw = 0, pitch = 0;
    const onMove = (e: PointerEvent) => {
      const b = el.getBoundingClientRect();
      mx = (e.clientX - b.left) / b.width - 0.5;
      my = (e.clientY - b.top) / b.height - 0.5;
    };
    const vec = new THREE.Vector3();

    const draw = (s: Scene3D, local: number) => {
      const r = s.r, m = r.meta, { a, b, f, rise } = timeline(r, local);
      const pos = s.position.array as Float32Array, sc = s.scalar.array as Float32Array;
      const key = `${a}|${b}|${f.toFixed(3)}|${rise.toFixed(3)}`;
      const moved = key !== s.key; // 한 설계에 머무는 동안은 다시 계산하지 않는다(로그인 입력을 막지 않게)
      s.key = key;
      if (moved) {
        const n = r.count, pa = a * n * 3, pb = b * n * 3, fa = a * n, fb = b * n;
        const riseZ = rise > 0 ? rise * rise * (m.fit ?? m.span[2]) * 0.9 : 0;
        for (let i = 0; i < n; i++) {
          for (let c = 0; c < 3; c++) {
            const va = r.pos[pa + i * 3 + c], vb = r.pos[pb + i * 3 + c];
            pos[i * 3 + c] = m.lo[c] + ((va + (vb - va) * f + 32768) / 65535) * m.span[c] + (c === 2 ? riseZ : 0);
          }
          sc[i] = (r.field[fa + i] + (r.field[fb + i] - r.field[fa + i]) * f) / 255;
        }
        s.position.needsUpdate = true;
        s.scalar.needsUpdate = true;
        s.skin.computeVertexNormals();
      }
      if (moved && r.lines && s.linePos && s.lineS) {
        const lp = s.linePos.array as Float32Array, ls = s.lineS.array as Float32Array, L = r.lines;
        const qa = a * L.count * 3, qb = b * L.count * 3, sa = a * L.count, sb = b * L.count;
        for (let i = 0; i < L.count; i++) {
          for (let c = 0; c < 3; c++) {
            const va = L.pos[qa + i * 3 + c], vb = L.pos[qb + i * 3 + c];
            lp[i * 3 + c] = m.lo[c] + ((va + (vb - va) * f + 32768) / 65535) * m.span[c];
          }
          ls[i] = (L.field[sa + i] + (L.field[sb + i] - L.field[sa + i]) * f) / 255;
        }
        s.linePos.needsUpdate = true;
        s.lineS.needsUpdate = true;
      }
      lineUniforms.uTime.value = local;
      if (s.planeMats) s.planeMats.forEach((pm, k) => { pm.uniforms.uOpacity.value = k === a ? 1 - f : k === b ? f : 0; });
      yaw += (mx * 0.45 - yaw) * 0.05;
      pitch += (my * 0.22 - pitch) * 0.05;
      const ang = m.yaw[0] + m.yaw[1] * local + yaw, el2 = m.pitch + pitch;
      const c = m.center ?? [m.lo[0] + m.span[0] / 2, m.lo[1] + m.span[1] / 2, m.lo[2] + m.span[2] / 2], far = 3000;
      camera.position.set(c[0] + Math.cos(ang) * Math.cos(el2) * far, c[1] + Math.sin(ang) * Math.cos(el2) * far, c[2] + Math.sin(el2) * far);
      camera.lookAt(c[0], c[1], c[2]);
      renderer.render(scene, camera);
      // 값 표시와 최대값 위치
      const near = f < 0.5 ? a : b, A = m.steps[a], B = m.steps[b];
      m.readout.forEach(([, key], i) => {
        const v = A[key] + (B[key] - A[key]) * f;
        if (out.current[i]) out.current[i]!.textContent = fmtValue(v, Math.abs(v) < 20 ? (m.digits ?? 1) : 0);
      });
      if (maxLabel.current) {
        const step = m.steps[near];
        vec.set(pos[step.maxNode * 3], pos[step.maxNode * 3 + 1], pos[step.maxNode * 3 + 2]).project(camera);
        maxLabel.current.style.transform = `translate(${((vec.x + 1) / 2) * el.clientWidth}px, ${((1 - vec.y) / 2) * el.clientHeight}px)`;
        maxLabel.current.style.opacity = rise > 0 ? "0" : "1";
        if (maxText.current) maxText.current.textContent = `${fmtValue(A.max ?? A.sigma ?? 0, 1)} ${m.unit}`;
      }
    };

    const show = (i: number) => {
      const list = liveResults.current;
      built.forEach((s) => { s.group.visible = false; });
      index = i;
      const s = build(list[i]);
      s.group.visible = true;
      start = performance.now() / 1000;
      frame();
      setShown(i);
    };
    const still = reducedMotion();
    const drawStill = () => draw(build(liveResults.current[index]), 0.1); // 움직이지 않는 한 장
    jump.current = (i) => { show(i); if (still) drawStill(); };

    const ro = new ResizeObserver(() => { resize(); if (still) drawStill(); });
    ro.observe(el);
    resize();
    show(0);

    let raf = 0;
    if (still) {
      drawStill();
    } else {
      window.addEventListener("pointermove", onMove);
      let last = 0;
      const loop = (now: number) => {
        raf = requestAnimationFrame(loop); // 탭이 숨으면 브라우저가 멈춘다
        if (now - last < 33) return;
        last = now;
        const list = liveResults.current;
        const s = build(list[index]);
        let local = Math.max(0, now / 1000 - start); // 프레임 시각은 탭을 바꾼 순간보다 조금 앞설 수 있다
        if (local > s.r.meta.seconds) { show((index + 1) % list.length); local = 0; }
        const cur = build(list[index]);
        canvas.style.opacity = String(Math.min(1, local / FADE, (cur.r.meta.seconds - local) / FADE));
        draw(cur, local);
      };
      raf = requestAnimationFrame(loop);
    }
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("pointermove", onMove);
      built.forEach((s) => s.dispose());
      skinMat.dispose(); meshMat.dispose(); edgeMat.dispose(); lineMat.dispose(); floorMat.dispose(); bandTex.dispose();
      renderer.dispose();
      el.removeChild(canvas);
    };
  }, [noGL, ready]);

  const m = current?.meta;
  const legend = useMemo(() => {
    if (!m) return [];
    return Array.from({ length: BANDS + 1 }, (_, i) => m.vmin + ((m.vmax - m.vmin) * (BANDS - i)) / BANDS);
  }, [m]);

  return (
    <div className="hero-mesh" aria-hidden="true">
      <div className="hero-canvas" ref={box}>{noGL && current && <StaticDrawing r={current} />}</div>
      {m && !noGL && (
        <>
          <div className="hero-max" ref={maxLabel}><i /><span>Max <b ref={maxText} /></span></div>
          <div className="hero-legend">
            <b>{m.field}</b><small>{m.unit}</small>
            <div className="bar">
              <div className="swatch">{COLORS.slice().reverse().map((c, i) => <i key={i} style={{ background: css(c) }} />)}</div>
              <div className="ticks">{legend.map((v, i) => <span key={i}>{v.toFixed(m.vmax < 20 ? 1 : 0)}</span>)}</div>
            </div>
          </div>
        </>
      )}
      {m && (
        <div className="hero-readout">
          {m.readout.map(([label, key, unit], i) => (
            <span key={label}><i>{label}</i><b ref={(n) => { out.current[i] = n; }}>{fmtValue(m.steps[0][key], Math.abs(m.steps[0][key]) < 20 ? (m.digits ?? 1) : 0)}</b> {unit}</span>
          ))}
          <div className="hero-case">
            <span className="tabs">{results.map((r, i) => (
              <button key={r.meta.id} type="button" tabIndex={-1} className={i === shown ? "on" : ""} onClick={() => jump.current(i)}>{r.meta.kind}</button>
            ))}</span>
            <em>{m.name} · {m.method} · {m.element} {m.elements.toLocaleString()}개{m.scale ? ` · 변형 ×${m.scale}` : ""}{m.note ? ` · ${m.note}` : ""}<br />예시 모델을 실제로 계산한 결과입니다. 제품 데이터가 아닙니다.</em>
          </div>
        </div>
      )}
    </div>
  );
}
