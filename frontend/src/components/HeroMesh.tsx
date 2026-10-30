import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { token } from "../design/token";
import { reducedMotion } from "../motion";
import { compile, PRODUCTS, type Compiled, type Tone } from "./heroProducts";

/** 로그인 첫인상: 폴더블 힌지와 드럼세탁기 Tub Assy를 번갈아 보여 준다. 치수가 천천히 바뀌며 형상과 응력이 함께 바뀐다 — 이 앱이 하는 일을 말없이 보여 준다.
 *  형상은 일반적인 구조를 코드로 만든 예시다(실제 제품 CAD·설계값이 아니다). 값도 예시다.
 *  '움직임 줄이기'면 멈춘 한 장을, WebGL을 못 쓰면 같은 격자선을 정지 그림으로 보인다. */
const FADE = 0.7; // 제품이 바뀔 때 흐려지는 시간(초)
const STILL_AT = 4; // 멈춘 그림에 쓸 시각: 힌지가 적당히 접힌 때

/** 정지 그림: 격자선을 축측 투영해 SVG로 그린다 */
function StaticDrawing({ c }: { c: Compiled }) {
  const { path, box } = useMemo(() => {
    const pos = new Float32Array(c.count * 3), ratio = new Float32Array(c.count);
    c.fill(c.product.design(STILL_AT), pos, ratio);
    const px = (n: number) => [pos[n * 3] * 0.813 + pos[n * 3 + 1] * 0.582, pos[n * 3] * 0.338 - pos[n * 3 + 1] * 0.472 - pos[n * 3 + 2] * 0.814];
    let d = "", x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (let i = 0; i < c.lines.length; i += 2) {
      const [a, b] = [px(c.lines[i]), px(c.lines[i + 1])];
      d += `M${a[0].toFixed(1)} ${a[1].toFixed(1)}L${b[0].toFixed(1)} ${b[1].toFixed(1)}`;
      x0 = Math.min(x0, a[0], b[0]); x1 = Math.max(x1, a[0], b[0]); y0 = Math.min(y0, a[1], b[1]); y1 = Math.max(y1, a[1], b[1]);
    }
    return { path: d, box: `${x0} ${y0} ${x1 - x0} ${y1 - y0}` };
  }, [c]);
  return <svg className="hero-static" viewBox={box} aria-hidden="true"><path d={path} vectorEffect="non-scaling-stroke" /></svg>;
}

interface Scene3D { c: Compiled; group: THREE.Group; position: THREE.BufferAttribute; color: THREE.BufferAttribute; skin: THREE.BufferGeometry; ratio: Float32Array; dispose: () => void }

export function HeroMesh() {
  const box = useRef<HTMLDivElement>(null);
  const out = useRef<(HTMLElement | null)[]>([]);
  const [noGL, setNoGL] = useState(false);
  const [shown, setShown] = useState(0);
  const compiled = useMemo(() => PRODUCTS.map((make) => compile(make())), []);
  const product = compiled[shown].product;
  const first = useMemo(() => product.readout(product.design(STILL_AT)), [product]); // 정지 화면의 그림과 같은 값

  useEffect(() => {
    if (noGL) return;
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
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -6000, 6000); // 도면처럼 원근 없이
    camera.up.set(0, 0, 1);
    const dark = document.documentElement.dataset.theme === "dark";
    scene.add(new THREE.HemisphereLight(0xffffff, new THREE.Color(token("--ink-3")), dark ? 1.5 : 1.7));
    const sun = new THREE.DirectionalLight(0xffffff, dark ? 1.5 : 1.3);
    camera.add(sun); // 빛은 시점을 따라간다: 어느 쪽에서 봐도 형상이 읽힌다
    sun.position.set(-0.5, 0.8, 1);
    scene.add(camera);

    const tones: Record<Tone, THREE.Color> = {
      metal: new THREE.Color(token(dark ? "--ink-4" : "--panel")),
      inner: new THREE.Color(token(dark ? "--ink-5" : "--ink-5")),
      rubber: new THREE.Color(token(dark ? "--panel-3" : "--ink-3")),
    };
    const hot = new THREE.Color(token("--brand-solid"));
    const lineMat = new THREE.LineBasicMaterial({ color: new THREE.Color(token("--ink")), transparent: true, opacity: dark ? 0.32 : 0.26 });
    const skinMat = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });

    const scenes: Scene3D[] = compiled.map((c) => {
      const position = new THREE.BufferAttribute(new Float32Array(c.count * 3), 3);
      const color = new THREE.BufferAttribute(new Float32Array(c.count * 3), 3);
      const skin = new THREE.BufferGeometry();
      skin.setAttribute("position", position);
      skin.setAttribute("color", color);
      skin.setIndex(new THREE.BufferAttribute(c.tris, 1));
      const wire = new THREE.BufferGeometry();
      wire.setAttribute("position", position);
      wire.setIndex(new THREE.BufferAttribute(c.lines, 1));
      const group = new THREE.Group();
      group.add(new THREE.Mesh(skin, skinMat), new THREE.LineSegments(wire, lineMat));
      group.visible = false;
      group.traverse((o) => { o.frustumCulled = false; }); // 치수가 계속 바뀌어 경계 구를 다시 구하지 않는다
      scene.add(group);
      return { c, group, position, color, skin, ratio: new Float32Array(c.count), dispose: () => { skin.dispose(); wire.dispose(); } };
    });

    let current = 0;
    const frame = () => {
      const p = scenes[current].c.product;
      const w = el.clientWidth || 1, h = el.clientHeight || 1;
      const span = p.span * Math.max(1, 1000 / w); // 좁아도 모델이 잘리지 않게
      const high = span * h / w;
      // 모델은 오른쪽 아래로: 왼쪽 위의 큰 제목과 겹치지 않는다
      const dx = span * 0.2, dy = high * 0.14;
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

    // 마우스를 따라 시점이 살짝 움직인다
    let mx = 0, my = 0, yaw = 0, pitch = 0;
    const onMove = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      mx = (e.clientX - r.left) / r.width - 0.5;
      my = (e.clientY - r.top) / r.height - 0.5;
    };

    const c0 = new THREE.Color();
    const draw = (s: Scene3D, tau: number, local: number) => {
      const p = s.c.product, d = p.design(tau);
      s.c.fill(d, s.position.array as Float32Array, s.ratio);
      for (let n = 0; n < s.c.count; n++) {
        c0.copy(tones[s.c.tones[n]]).lerp(hot, s.ratio[n]);
        s.color.setXYZ(n, c0.r, c0.g, c0.b);
      }
      s.position.needsUpdate = true;
      s.color.needsUpdate = true;
      s.skin.computeVertexNormals();
      yaw += (mx * 0.5 - yaw) * 0.05;
      pitch += (my * 0.25 - pitch) * 0.05;
      const a = p.yaw[0] + p.yaw[1] * local + yaw, b = p.pitch + pitch;
      const [cx, cy, cz] = p.center, far = 3000;
      camera.position.set(cx + Math.cos(a) * Math.cos(b) * far, cy + Math.sin(a) * Math.cos(b) * far, cz + Math.sin(b) * far);
      camera.lookAt(cx, cy, cz);
      renderer.render(scene, camera);
      return d;
    };

    const ro = new ResizeObserver(() => { resize(); if (reducedMotion()) { draw(scenes[0], STILL_AT, 0); } });
    ro.observe(el);
    resize();
    scenes[0].group.visible = true;

    let raf = 0;
    if (reducedMotion()) {
      draw(scenes[0], STILL_AT, 0); // 움직이지 않는 한 장
    } else {
      window.addEventListener("pointermove", onMove);
      const t0 = performance.now();
      let last = 0, lastOut = 0, start = 0;
      const loop = (now: number) => {
        raf = requestAnimationFrame(loop); // 탭이 숨으면 브라우저가 멈춘다
        if (now - last < 30) return;
        last = now;
        const tau = (now - t0) / 1000;
        let s = scenes[current];
        let local = tau - start;
        if (local > s.c.product.seconds) { // 다음 제품
          s.group.visible = false;
          current = (current + 1) % scenes.length;
          s = scenes[current];
          s.group.visible = true;
          start = tau; local = 0;
          frame();
          setShown(current);
        }
        canvas.style.opacity = String(Math.min(1, local / FADE, (s.c.product.seconds - local) / FADE));
        const d = draw(s, tau, local);
        if (now - lastOut > 120) { // 글자는 자주 바꾸지 않는다
          lastOut = now;
          s.c.product.readout(d).forEach((row, i) => { if (out.current[i]) out.current[i]!.textContent = row[1]; });
        }
      };
      raf = requestAnimationFrame(loop);
    }
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("pointermove", onMove);
      scenes.forEach((s) => s.dispose());
      skinMat.dispose();
      lineMat.dispose();
      renderer.dispose();
      el.removeChild(canvas);
    };
  }, [noGL, compiled]);

  return (
    <div className="hero-mesh" aria-hidden="true">
      <div className="hero-canvas" ref={box}>{noGL && <StaticDrawing c={compiled[0]} />}</div>
      <div className="hero-readout">
        {first.map(([label, value, unit], i) => (
          <span key={label}><i>{label}</i><b ref={(n) => { out.current[i] = n; }}>{value}</b> {unit}</span>
        ))}
        <em>{product.name} · 예시 형상</em>
      </div>
    </div>
  );
}
