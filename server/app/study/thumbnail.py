"""Simulator 카드 썸네일. 그림은 두 가지뿐이고, 없으면 만들어 내지 않는다(화면이 아는 값으로 표시한다).
1) 그 버전의 3D 모델: 설계 범위 가운데의 예측 형상·필드를 Simulator 첫 시점 그대로 그린다.
2) 프로젝트를 볼 수 있는 사람에게만: 그 스터디·작업 흐름의 이미지 결과. 링크로 받은 사람에게 프로젝트 내용을 보이면 안 되므로 호출하는 쪽이 고른다.

권한은 여기서 보지 않는다. 라우터가 Simulator를 여는 것과 같은 검사를 끝낸 뒤에만 부른다."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

import numpy as np
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from . import service

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 640, 400  # 카드(폭 약 320)의 2배. 고해상도 화면에서도 흐리지 않게
MAX_TRIANGLES = 30_000  # 이보다 큰 메쉬는 줄여서 그린다. 작은 그림에서는 차이가 보이지 않는다
MAX_POINTS = 20_000
RENDER_WORKERS = 2  # 카드가 100장이어도 동시에 이만큼만 그린다. 나머지는 차례를 기다린다
RETRY_AFTER = 600  # 초. 실패한 그림을 카드를 열 때마다 다시 그리지 않는다
MIN_COVERAGE = 0.001  # 그림에서 칠해진 비율이 이보다 작으면 없는 그림으로 친다. 빈 상자를 그림이라고 내주지 않는다
MAX_IMAGE_TRIES = 30  # 이미지 결과를 이만큼만 살펴본다. 쓸 수 없는 결과가 쌓여도 한 요청이 길어지지 않게
VIEW = np.array([0.55, -0.75, 0.45])  # 3D 화면(FieldViewer3D)의 첫 시점과 같은 방향. 열었을 때 그림과 같은 모습이 보이게
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}

_pool = ThreadPoolExecutor(max_workers=RENDER_WORKERS, thread_name_prefix="thumbnail")
_jobs: dict[Path, Future] = {}
_jobs_lock = threading.Lock()
_failed: dict[Path, float] = {}


@dataclass(frozen=True)
class Job:
    """DB 없이 그릴 수 있는 값만 담는다. 대기 중에 DB 연결을 쥐고 있지 않기 위해서다."""
    rom_path: Path
    center: dict[str, float]
    dest: Path


@dataclass(frozen=True)
class Image:
    path: Path
    media_type: str
    tag: str


def _center(study: models.Study) -> dict[str, float]:
    out = {}
    for v in study.variables or []:
        if v.get("lower") is not None and v.get("upper") is not None:
            out[v["name"]] = (float(v["lower"]) + float(v["upper"])) / 2
    return out


def field_jobs(db: Session, m: models.SurrogateModel, study: models.Study) -> list[Job]:
    """이 버전이 쓰는 3D 모델을 3D 화면이 처음 고르는 순서대로. 이름에 모델 파일의 수정 시각을 넣는 것은,
    SQLite가 지워진 번호를 다시 쓰므로 번호만으로는 다른 프로젝트의 옛 그림을 내줄 수 있기 때문이다."""
    center, jobs = _center(study), []
    for fm in service.field_models_for(db, study.id, m):
        if not fm.path:
            continue
        rom = Path(fm.path)
        try:
            stamp = rom.stat().st_mtime_ns
        except OSError:
            continue
        jobs.append(Job(rom, center, settings.data_dir / "thumbnails" / f"m{m.id}_f{fm.id}_{stamp:x}.png"))
    return jobs


def _image_file(root: Path, rel: object) -> Path | None:
    """결과의 path는 AI·PC Agent가 적은 값이다. 프로젝트 폴더 안의 그림 파일만 내준다."""
    if not isinstance(rel, str) or not rel or "\0" in rel:
        return None
    # resolve()는 네트워크 경로(\\서버\공유)면 그 서버가 답할 때까지 멈춘다. 파일 시스템에 묻기 전에 글자만 보고 버린다.
    # 서버가 Linux여도 같은 값은 같게 다루도록 Windows 규칙으로 읽는다('/'도 구분자로 본다)
    written = PureWindowsPath(rel)
    if written.drive or written.root or ".." in written.parts or written.suffix.lower() not in IMAGE_TYPES:
        return None
    try:
        target = (root / rel).resolve()
        return target if root in target.parents and target.is_file() else None
    except (OSError, ValueError):
        return None


def project_image(db: Session, project_id: int, study_name: str) -> Image | None:
    """이 스터디의 이미지 결과, 없으면 이 스터디가 속한 작업 흐름의 이미지 결과. 같은 자료면 언제나 같은 그림(가장 최근 번호)을 고른다.
    스터디 결과는 제목이 '[스터디 이름]'으로 시작한다. 프로젝트의 관계없는 이미지는 쓰지 않는다.
    표·3D 결과는 payload가 크므로 이미지 행만 DB에서 고른다. 파일을 보므로 이벤트 루프 밖에서 부른다."""
    A, prefix = models.Artifact, f"[{study_name}]"
    own = func.substr(A.title, 1, len(prefix)) == prefix  # LIKE와 달리 대소문자·%·_를 그대로 비교한다
    flows = [r[0] for r in db.query(A.flow_id).filter(A.project_id == project_id, own, A.flow_id.isnot(None)).distinct()]
    rows = (db.query(A.id, A.payload)
            .filter(A.project_id == project_id, A.kind == "image", own | A.flow_id.in_(flows))
            .order_by(case((own, 0), else_=1), A.id.desc()).limit(MAX_IMAGE_TRIES).all())
    root = (settings.files_dir / f"project_{project_id}").resolve()
    for artifact_id, payload in rows:
        target = _image_file(root, (payload or {}).get("path"))
        if target:
            try:
                return Image(target, IMAGE_TYPES[target.suffix.lower()], f"a{artifact_id}-{target.stat().st_mtime_ns:x}")
            except OSError:  # 방금 지워진 파일이면 다음 후보로
                continue
    return None


def _decimate(points: np.ndarray, values: np.ndarray, tris: np.ndarray, limit: int = MAX_TRIANGLES):
    """가까운 노드를 격자 칸마다 하나로 합친다. 삼각형을 건너뛰어 고르면 구멍이 나지만 이 방법은 표면이 이어진다."""
    if len(tris) <= limit:
        return points, values, tris
    lo, span = points.min(axis=0), float(np.ptp(points, axis=0).max()) or 1.0
    res = 256
    while True:
        cell = np.floor((points - lo) / (span / res) * (1 - 1e-9)).astype(np.int64)
        keys = cell[:, 0] + cell[:, 1] * (res + 1) + cell[:, 2] * (res + 1) ** 2
        _, inv = np.unique(keys, return_inverse=True)
        inv = inv.ravel()
        count = np.bincount(inv)
        t = inv[tris]
        t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]
        t = np.unique(np.sort(t, axis=1), axis=0)
        if len(t) <= limit or res <= 8:
            pts = np.column_stack([np.bincount(inv, points[:, d]) / count for d in range(3)])
            return pts, np.bincount(inv, values) / count, t
        res //= 2


def _project(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """→ (화면 좌표 (N,2), 깊이 (N,), 클수록 앞). 원근 없이 그린다. 치수 비교가 흐려지지 않게."""
    toward = VIEW / np.linalg.norm(VIEW)
    right = np.cross(-toward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, -toward)
    p = points - (points.max(axis=0) + points.min(axis=0)) / 2
    return np.column_stack([p @ right, p @ up]), p @ toward


def draw(points: np.ndarray, values: np.ndarray, triangles: np.ndarray | None, out) -> None:
    """예측한 형상·필드를 투명 바탕 PNG로 쓴다. 바탕은 카드가 정하므로 두 테마에 그대로 맞는다.
    pyplot은 전역 상태를 쓰므로 스레드에서 쓰지 않고 Figure를 직접 다룬다."""
    from matplotlib import colormaps
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import PolyCollection
    from matplotlib.figure import Figure
    from matplotlib.image import imsave

    points, values = np.asarray(points, float), np.nan_to_num(np.asarray(values, float))
    has_tris = triangles is not None and len(triangles) > 0
    if has_tris:
        # 삼각형이 쓰지 않는 노드는 보이지 않는다. 화면 범위와 메쉬 줄이기 격자에 넣으면 멀리 떨어진 노드 하나에 그림 전체가 점으로 줄어든다
        used, triangles = np.unique(np.asarray(triangles, np.int64), return_inverse=True)
        points, values, triangles = points[used], values[used], triangles.reshape(-1, 3)
    if not len(points) or not np.isfinite(points).all():
        raise ValueError("그릴 수 있는 좌표가 없습니다")
    lo, hi = float(values.min()), float(values.max())
    cmap = colormaps["turbo"]  # 3D 화면과 같은 색
    fig = Figure(figsize=(WIDTH / 100, HEIGHT / 100), dpi=100)
    fig.patch.set_alpha(0)
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_axis_off()
    if has_tris:
        points, values, triangles = _decimate(points, values, triangles)
        if not len(triangles):
            raise ValueError("줄이고 나니 남은 삼각형이 없습니다")
    xy, depth = _project(points)
    # 색 범례가 없는 그림이라 turbo의 맨 아래 어두운 끝을 빼고 쓴다. 그래야 어두운 테마에서 낮은 값 쪽이 바탕에 묻히지 않는다
    norm = lambda v: 0.1 + 0.85 * (v - lo) / (hi - lo) if hi > lo else np.full_like(v, 0.5)  # noqa: E731
    if has_tris:
        order = np.argsort(depth[triangles].mean(axis=1))  # 뒤에서 앞으로 칠한다
        t = triangles[order]
        p = points[t]
        normal = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        length = np.linalg.norm(normal, axis=1)
        facing = np.abs(normal @ (VIEW / np.linalg.norm(VIEW))) / np.where(length > 0, length, 1)  # 안팎 방향이 섞인 메쉬도 있어 양면으로 본다
        rgb = cmap(norm(values[t].mean(axis=1)))[:, :3] * (0.62 + 0.38 * facing)[:, None]
        # 테두리를 면과 같은 색으로 칠해 삼각형 사이 틈을 메운다
        ax.add_collection(PolyCollection(xy[t], facecolors=rgb, edgecolors=rgb, linewidths=0.4))
    else:
        keep = np.argsort(depth)[np.linspace(0, len(depth) - 1, min(len(depth), MAX_POINTS)).astype(int)]
        ax.scatter(xy[keep, 0], xy[keep, 1], c=cmap(norm(values[keep])), s=4, linewidths=0)
    # 축이 그림 전체를 채우므로, 범위를 그림 비율에 맞추면 형상의 가로세로 비가 지켜진다
    (x0, y0), (x1, y1) = xy.min(axis=0), xy.max(axis=0)
    scale = max((x1 - x0) / WIDTH, (y1 - y0) / HEIGHT, 1e-12) * 1.14  # 가장자리 여백
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    ax.set_xlim(cx - scale * WIDTH / 2, cx + scale * WIDTH / 2)
    ax.set_ylim(cy - scale * HEIGHT / 2, cy + scale * HEIGHT / 2)
    canvas.draw()
    rgba = np.asarray(canvas.buffer_rgba())
    # 실패로 돌려야 이미지 결과나 대체 표시로 넘어간다. 거의 빈 그림을 그대로 내주면 다시 학습할 때까지 빈 상자가 남는다
    if (rgba[..., 3] > 127).mean() < MIN_COVERAGE:
        raise ValueError("그림에 보이는 형상이 거의 없습니다")
    imsave(out, rgba, format="png")


def _render(job: Job) -> bool:
    try:
        from .fieldmodel import FieldROM

        rom = FieldROM.load(job.rom_path)
        pts, vals, _ = rom.predict({v: job.center[v] for v in rom.variables})
        mag = vals if vals.ndim == 1 else np.linalg.norm(vals, axis=1)
        job.dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = job.dest.with_name(f"{job.dest.stem}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "wb") as f:
                draw(pts, mag, rom.triangles, f)
            os.replace(tmp, job.dest)  # 쓰다 끊긴 그림을 내주지 않는다
        finally:
            tmp.unlink(missing_ok=True)
        _failed.pop(job.dest, None)
        return True
    except Exception:  # noqa: BLE001 — 그림 하나 때문에 목록이 깨지면 안 된다. 화면은 대체 표시를 쓴다
        log.warning("Simulator 썸네일을 그리지 못했습니다: %s", job.rom_path, exc_info=True)
        _failed[job.dest] = time.monotonic()
        return False


def _submit(job: Job) -> Future:
    """같은 그림을 여러 사람이 동시에 요청해도 한 번만 그린다."""
    with _jobs_lock:
        running = _jobs.get(job.dest)
        if running is not None:
            return running
        future = _pool.submit(_render, job)
        _jobs[job.dest] = future
    future.add_done_callback(lambda _: _jobs.pop(job.dest, None))
    return future


async def render(job: Job) -> bool:
    """그림이 준비되면 True. 이벤트 루프를 막지 않고 작업 스레드에서 그린다."""
    if job.dest.is_file():
        return True
    failed_at = _failed.get(job.dest)
    if failed_at is not None and time.monotonic() - failed_at < RETRY_AFTER:
        return False
    # shield: 한 요청이 끊겨도 같은 그림을 기다리는 다른 요청이 함께 취소되지 않게
    return await asyncio.shield(asyncio.wrap_future(_submit(job)))
