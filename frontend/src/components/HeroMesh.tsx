import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { token } from "../design/token";
import { reducedMotion } from "../motion";

/** 로그인 첫인상: 실시간 3D 브래킷. 두께·리브 높이가 천천히 바뀌며 형상과 응력이 함께 바뀐다 — 이 앱이 하는 일을 말없이 보여 준다.
 *  식은 데모 해석과 같다(cae_adapters/mock.py의 _bracket·bracket_mesh). 값은 예시다.
 *  WebGL을 못 쓰거나 '움직임 줄이기'면 같은 선 그림을 정지 화면으로 보인다. */
const L = 200, W = 60, RIB = 6, P = 2000, E = 210e3, RHO = 7.85e-6;
const SHOW_DEFLECTION = 25; // 처짐이 눈에 보이게 키운다
const STRESS_MAX = 150; // 색이 끝까지 차는 응력(MPa). 보통 설계 범위에서 색 변화가 잘 보이는 값

/** 직육면체 표면(칸 수 nx·ny·nz, 치수 0~1)의 절점·삼각형·격자선. 치수가 바뀌어도 위상은 같다 */
function unitBox(nx: number, ny: number, nz: number) {
  const index = new Map<string, number>();
  const pts: number[][] = [];
  const tris: number[] = [];
  const lines: number[] = [];
  const node = (i: number, j: number, k: number) => {
    const key = `${i},${j},${k}`;
    if (!index.has(key)) { index.set(key, pts.length); pts.push([i / nx, j / ny, k / nz]); }
    return index.get(key)!;
  };
  const face = (na: number, nb: number, at: (a: number, b: number) => number) => {
    for (let a = 0; a < na; a++) {
      for (let b = 0; b < nb; b++) {
        const [p, q, r, s] = [at(a, b), at(a + 1, b), at(a + 1, b + 1), at(a, b + 1)];
        tris.push(p, q, r, p, r, s);
        lines.push(p, q, p, s);
        if (a === na - 1) lines.push(q, r);
        if (b === nb - 1) lines.push(s, r);
      }
    }
  };
  face(nx, ny, (a, b) => node(a, b, 0));
  face(nx, ny, (a, b) => node(a, b, nz));
  face(nx, nz, (a, b) => node(a, 0, b));
  face(nx, nz, (a, b) => node(a, ny, b));
  face(ny, nz, (a, b) => node(0, a, b));
  face(ny, nz, (a, b) => node(nx, a, b));
  return { pts, tris, lines };
}

/** 플레이트 + 리브 한 벌(절점 수 고정) */
function bracketTopology() {
  const plate = unitBox(32, 8, 2), rib = unitBox(32, 2, 4);
  const off = plate.pts.length;
  const count = off + rib.pts.length;
  return {
    count, off, plate: plate.pts, rib: rib.pts,
    tris: [...plate.tris, ...rib.tris.map((n) => n + off)],
    lines: [...plate.lines, ...rib.lines.map((n) => n + off)],
  };
}
type Topology = ReturnType<typeof bracketTopology>;

function section(t: number, r: number) {
  const a1 = W * t, a2 = RIB * r;
  const zc = (a1 * t / 2 + a2 * (t + r / 2)) / (a1 + a2);
  const inertia = W * t ** 3 / 12 + a1 * (t / 2 - zc) ** 2 + RIB * r ** 3 / 12 + a2 * (t + r / 2 - zc) ** 2;
  return { area: a1 + a2, zc, inertia };
}

/** 시각 τ의 설계: 두 변수가 서로 다른 주기로 바뀐다(둘이 맞물려 되풀이가 눈에 띄지 않게) */
function design(tau: number) {
  const t = 10 + 4 * Math.sin(tau * 0.31);
  const r = 25 + 15 * Math.sin(tau * 0.19 + 1.1);
  const { area, zc, inertia } = section(t, r);
  return { t, r, zc, inertia, mass: RHO * area * L * 1000, stress: P * L * Math.max(zc, t + r - zc) / inertia };
}

/** 위치(positions)와 응력비(0~1)를 채운다 */
function fill(topo: Topology, d: ReturnType<typeof design>, pos: Float32Array, ratio: Float32Array) {
  const put = (n: number, u: number[], y0: number, y1: number, z0: number, z1: number) => {
    const x = u[0] * L, y = y0 + u[1] * (y1 - y0), z = z0 + u[2] * (z1 - z0);
    const bend = P * x * x * (3 * L - x) / (6 * E * d.inertia) * SHOW_DEFLECTION;
    pos[n * 3] = x - L / 2;
    pos[n * 3 + 1] = y;
    pos[n * 3 + 2] = z - bend;
    ratio[n] = Math.min(1, Math.abs(P * (L - x) * (z - d.zc) / d.inertia) / STRESS_MAX);
  };
  topo.plate.forEach((u, n) => put(n, u, -W / 2, W / 2, 0, d.t));
  topo.rib.forEach((u, n) => put(topo.off + n, u, -RIB / 2, RIB / 2, d.t, d.t + d.r));
}

function readout(d: ReturnType<typeof design>) {
  return { t: d.t.toFixed(1), r: d.r.toFixed(1), m: d.mass.toFixed(0), s: d.stress.toFixed(0) };
}

/** 정지 그림: 같은 격자선을 축측 투영해 SVG로 그린다 */
function StaticDrawing({ topo }: { topo: Topology }) {
  const path = useMemo(() => {
    const pos = new Float32Array(topo.count * 3), ratio = new Float32Array(topo.count);
    fill(topo, design(0), pos, ratio);
    const px = (n: number) => {
      const [x, y, z] = [pos[n * 3], pos[n * 3 + 1], pos[n * 3 + 2]];
      return `${(x * 0.813 + y * 0.582).toFixed(1)} ${(x * 0.338 - y * 0.472 - z * 0.814).toFixed(1)}`; // 움직이는 그림의 기본 시점과 같다
    };
    let d = "";
    for (let i = 0; i < topo.lines.length; i += 2) d += `M${px(topo.lines[i])}L${px(topo.lines[i + 1])}`;
    return d;
  }, [topo]);
  return <svg className="hero-static" viewBox="-112 -90 224 150" aria-hidden="true"><path d={path} /></svg>;
}

export function HeroMesh() {
  const box = useRef<HTMLDivElement>(null);
  const out = useRef<Record<string, HTMLElement | null>>({});
  const [still, setStill] = useState(reducedMotion);
  const topo = useMemo(bracketTopology, []);
  const first = readout(design(0));

  useEffect(() => {
    if (still) return;
    const el = box.current!;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setStill(true); // WebGL을 못 쓰는 PC
      return;
    }
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    renderer.setClearColor(0x000000, 0);
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -2000, 2000); // 도면처럼 원근 없이
    camera.up.set(0, 0, 1);
    const position = new THREE.BufferAttribute(new Float32Array(topo.count * 3), 3);
    const color = new THREE.BufferAttribute(new Float32Array(topo.count * 3), 3);
    const ratio = new Float32Array(topo.count);
    const skin = new THREE.BufferGeometry();
    skin.setAttribute("position", position);
    skin.setAttribute("color", color);
    skin.setIndex(topo.tris);
    const mesh = new THREE.Mesh(skin, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
    const wire = new THREE.BufferGeometry();
    wire.setAttribute("position", position);
    wire.setIndex(topo.lines);
    const lines = new THREE.LineSegments(wire, new THREE.LineBasicMaterial({ color: new THREE.Color(token("--ink")), transparent: true, opacity: 0.5 }));
    const model = new THREE.Group();
    model.add(mesh, lines);
    scene.add(model);
    const base = new THREE.Color(token("--panel")), hot = new THREE.Color(token("--brand-solid")), c = new THREE.Color();

    const resize = () => {
      const w = el.clientWidth || 1, h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      const span = 170 * Math.max(1, 1000 / w); // 좁아도 모델이 잘리지 않게
      const high = span * h / w;
      // 모델은 오른쪽 아래로: 왼쪽 위의 큰 제목과 겹치지 않는다
      const dx = span * 0.24, dy = high * 0.2;
      camera.left = -span - dx; camera.right = span - dx;
      camera.top = high + dy; camera.bottom = -high + dy;
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    // 마우스를 따라 시점이 살짝 움직인다
    let mx = 0, my = 0, yaw = 0, pitch = 0;
    const onMove = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      mx = (e.clientX - r.left) / r.width - 0.5;
      my = (e.clientY - r.top) / r.height - 0.5;
    };
    window.addEventListener("pointermove", onMove);

    const t0 = performance.now();
    let raf = 0, last = 0, lastOut = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop); // 탭이 숨으면 브라우저가 멈춘다
      if (now - last < 30) return;
      last = now;
      const tau = (now - t0) / 1000;
      const d = design(tau);
      fill(topo, d, position.array as Float32Array, ratio);
      for (let n = 0; n < topo.count; n++) {
        c.copy(base).lerp(hot, ratio[n]);
        color.setXYZ(n, c.r, c.g, c.b);
      }
      position.needsUpdate = true;
      color.needsUpdate = true;
      yaw += (mx * 0.5 - yaw) * 0.05;
      pitch += (my * 0.25 - pitch) * 0.05;
      const a = -0.95 + Math.sin(tau * 0.12) * 0.28 + yaw, b = 0.62 + pitch;
      camera.position.set(Math.cos(a) * Math.cos(b) * 500, Math.sin(a) * Math.cos(b) * 500, Math.sin(b) * 500);
      camera.lookAt(0, 0, 8);
      renderer.render(scene, camera);
      if (now - lastOut > 120) { // 글자는 자주 바꾸지 않는다
        lastOut = now;
        const v = readout(d);
        for (const k of Object.keys(v) as (keyof typeof v)[]) if (out.current[k]) out.current[k]!.textContent = v[k];
      }
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("pointermove", onMove);
      skin.dispose();
      wire.dispose();
      (mesh.material as THREE.Material).dispose();
      (lines.material as THREE.Material).dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, [still, topo]);

  const cell = (label: string, key: keyof typeof first, unit: string) => (
    <span><i>{label}</i><b ref={(n) => { out.current[key] = n; }}>{first[key]}</b> {unit}</span>
  );
  return (
    <div className="hero-mesh" aria-hidden="true">
      <div className="hero-canvas" ref={box}>{still && <StaticDrawing topo={topo} />}</div>
      <div className="hero-readout">
        {cell("두께", "t", "mm")}{cell("리브", "r", "mm")}{cell("질량", "m", "g")}{cell("최대 응력", "s", "MPa")}
        <em>예시 모델</em>
      </div>
    </div>
  );
}
