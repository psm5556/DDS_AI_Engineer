import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { token } from "../design/token";

/** turbo 컬러맵 (0~1 → RGB). 해석 결과에 흔히 쓰는 무지개 계열이지만 명도 변화가 고르다. */
export function turbo(t: number): [number, number, number] {
  const x = Math.min(1, Math.max(0, t));
  const r = 0.13572138 + x * (4.6153926 + x * (-42.66032258 + x * (132.13108234 + x * (-152.94239396 + x * 59.28637943))));
  const g = 0.09140261 + x * (2.19418839 + x * (4.84296658 + x * (-14.18503333 + x * (4.27729857 + x * 2.82956604))));
  const b = 0.1066733 + x * (12.64194608 + x * (-60.58204836 + x * (110.36276771 + x * (-89.90310912 + x * 27.34824973))));
  return [Math.min(1, Math.max(0, r)), Math.min(1, Math.max(0, g)), Math.min(1, Math.max(0, b))];
}

/** 3D 형상 + 노드 필드 가시화 (three.js). 예측이 바뀌면 좌표·색만 갱신하고 시점은 유지한다. */
export function FieldViewer3D({ points, triangles, values, vmin, vmax, wireframe, dark, resetKey }: {
  points: Float32Array; triangles: Uint32Array; values: Float32Array; vmin: number; vmax: number;
  wireframe?: boolean; dark?: boolean; resetKey?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const st = useRef<{
    renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera; controls: OrbitControls;
    geom: THREE.BufferGeometry; mesh: THREE.Mesh; edges: THREE.LineSegments; fitted: boolean;
  } | null>(null);

  // 한 번만 장면 구성
  useEffect(() => {
    const el = box.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 1e7);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    scene.add(new THREE.AmbientLight(0xffffff, 0.65));
    const light = new THREE.DirectionalLight(0xffffff, 0.9);
    camera.add(light);
    light.position.set(1, 1, 2);
    scene.add(camera);
    const geom = new THREE.BufferGeometry();
    const mesh = new THREE.Mesh(geom, new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide }));
    // 메쉬 선은 데이터가 들어온 뒤 만든다 (빈 형상으로 WireframeGeometry를 만들면 오류)
    const edges = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x18181b, transparent: true, opacity: 0.25 }));
    scene.add(mesh, edges);
    st.current = { renderer, scene, camera, controls, geom, mesh, edges, fitted: false };

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      camera.aspect = w / Math.max(1, h);
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();
    let raf = 0;
    const loop = () => { controls.update(); renderer.render(scene, camera); raf = requestAnimationFrame(loop); };
    loop();
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      geom.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
      st.current = null;
    };
  }, []);

  // 데이터 갱신
  useEffect(() => {
    const s = st.current;
    if (!s || !points.length) return;
    const n = points.length / 3;
    const colors = new Float32Array(n * 3);
    const span = vmax - vmin || 1;
    for (let i = 0; i < n; i++) {
      const [r, g, b] = turbo((values[i] - vmin) / span);
      colors[i * 3] = r; colors[i * 3 + 1] = g; colors[i * 3 + 2] = b;
    }
    s.geom.setAttribute("position", new THREE.BufferAttribute(points, 3));
    s.geom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    if (triangles.length) s.geom.setIndex(new THREE.BufferAttribute(triangles, 1));
    s.geom.computeVertexNormals();
    s.geom.computeBoundingSphere();
    s.edges.geometry.dispose();
    s.edges.geometry = new THREE.WireframeGeometry(s.geom);
    if (!s.fitted) fit(s);
  }, [points, triangles, values, vmin, vmax]);

  useEffect(() => { if (st.current) st.current.edges.visible = !!wireframe; }, [wireframe]);
  // 뷰 바탕은 디자인 토큰 --panel-2(테마에 따라 바뀜)
  useEffect(() => { if (st.current) st.current.scene.background = new THREE.Color(token("--panel-2") || (dark ? "#1d1d1c" : "#f7f7f5")); }, [dark]);
  useEffect(() => { if (st.current && resetKey) fit(st.current); }, [resetKey]);

  return <div ref={box} className="viewer3d" aria-label="3D 형상·필드 보기 (끌어서 회전, 휠로 확대, 오른쪽 버튼으로 이동)" />;
}

function fit(s: { camera: THREE.PerspectiveCamera; controls: OrbitControls; geom: THREE.BufferGeometry; fitted: boolean }) {
  const bs = s.geom.boundingSphere;
  if (!bs) return;
  const d = bs.radius / Math.sin((s.camera.fov * Math.PI) / 360) * 1.5; // 오른쪽 색 범례에 가리지 않게 여유
  s.controls.target.copy(bs.center);
  s.camera.position.copy(bs.center).add(new THREE.Vector3(0.55, -0.75, 0.45).normalize().multiplyScalar(d));
  s.camera.up.set(0, 0, 1);
  s.camera.near = d / 1000;
  s.camera.far = d * 100;
  s.camera.updateProjectionMatrix();
  s.controls.update();
  s.fitted = true;
}
