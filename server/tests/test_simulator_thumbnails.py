"""Simulator 카드 그림: 여는 권한과 똑같이 막고, 링크로 받은 사람에게 프로젝트 결과를 보이지 않으며, 버전마다 한 번만 그리고, 실패해도 목록이 깨지지 않는다."""
import asyncio
import threading
import time
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest

from server.tests.test_api import _auth, client  # noqa: F401 — 같은 서버 fixture 재사용
from server.tests.test_simulators import _deploy, _study, _train

from server.app import models
from server.app.config import settings
from server.app.database import SessionLocal
from server.app.study import thumbnail

PNG = b"\x89PNG\r\n\x1a\n"


def _sim_3d(client, h, name):  # noqa: F811
    """3D 모델이 있는 배포된 Simulator."""
    pid, sid = _study(client, h, name)
    v1 = _train(client, sid, h)
    fm = client.post(f"/api/studies/{sid}/train_field", json={"field": "stress"}, headers=h)
    assert fm.status_code == 200, fm.text
    _deploy(client, v1["id"], h)
    return pid, sid, v1, fm.json()


def _sim_flat(client, h, name):  # noqa: F811
    """3D 모델이 없는 Simulator(mock beam은 필드를 남기지 않는다)."""
    pid, sid = _study(client, h, name, function="beam")
    v1 = _train(client, sid, h)
    _deploy(client, v1["id"], h)
    return pid, sid, v1


def _publish_image(client, h, pid, title, content=PNG + b"project-image", filename="contour.png", flow_id=None):  # noqa: F811
    rel = client.post(f"/api/projects/{pid}/files", files={"file": (filename, content, "image/png")}, headers=h).json()["path"]
    return client.post(f"/api/projects/{pid}/artifacts", headers=h, json={
        "kind": "image", "title": title, "payload": {"path": rel}, "flow_id": flow_id}).json()


def _row(client, h, sid):  # noqa: F811
    got = client.get("/api/simulators", headers=h).json()
    return next(x for x in got["mine"] + got["shared"] if x["study_id"] == sid)


def _count_draws(monkeypatch):
    calls = []
    real = thumbnail.draw
    monkeypatch.setattr(thumbnail, "draw", lambda *a: (calls.append(1), real(*a))[1])
    return calls


def test_owner_member_admin_get_picture_stranger_does_not(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    owner, _ = _auth(client, "thumb_owner", admin)
    member, member_user = _auth(client, "thumb_member", admin)
    stranger, _ = _auth(client, "thumb_stranger", admin)
    pid, sid, v1, _ = _sim_3d(client, owner, "권한")
    client.put(f"/api/projects/{pid}/members/{member_user['id']}", json={"role": "member"}, headers=owner)

    url = _row(client, owner, sid)["thumbnail_url"]
    assert url == f"/api/models/{v1['id']}/thumbnail"
    for who in (owner, member, admin):  # 관리자는 어느 Simulator든 열 수 있다
        r = client.get(url, headers=who)
        assert r.status_code == 200 and r.content.startswith(PNG) and r.headers["content-type"] == "image/png"
        assert "no-cache" in r.headers["cache-control"]  # 권한이 끊긴 뒤 옛 그림을 그대로 쓰지 않게
    assert client.get(url, headers=stranger).status_code == 403
    assert client.get(url).status_code == 401
    # 멤버에서 빠지면 바로 막힌다
    client.delete(f"/api/projects/{pid}/members/{member_user['id']}", headers=owner)
    assert client.get(url, headers=member).status_code == 403


def test_share_thumbnail_only_while_link_is_valid(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    viewer, _ = _auth(client, "thumb_viewer", admin)
    _, sid, v1, _ = _sim_3d(client, admin, "링크 그림")
    tok = client.post(f"/api/models/{v1['id']}/shares", json={}, headers=admin).json()["token"]
    client.get(f"/api/shares/{tok}", headers=viewer)
    row = _row(client, viewer, sid)
    assert row["thumbnail_url"].startswith(f"/api/shares/{tok}/thumbnail")
    r = client.get(row["thumbnail_url"], headers=viewer)
    assert r.status_code == 200 and r.content.startswith(PNG)
    uses = client.get(f"/api/models/{v1['id']}/shares", headers=admin).json()[0]["uses"]
    # 링크로 받은 사람이 프로젝트 쪽 주소로 받을 수는 없다
    assert client.get(f"/api/models/{v1['id']}/thumbnail", headers=viewer).status_code == 403
    assert client.get("/api/shares/없는링크/thumbnail", headers=viewer).status_code == 404
    assert client.get(row["thumbnail_url"]).status_code == 401
    assert client.get(f"/api/models/{v1['id']}/shares", headers=admin).json()[0]["uses"] == uses  # 그림을 받아도 사용 횟수는 그대로

    # 기간이 끝나면 404, 목록에도 주소가 없다
    with SessionLocal() as db:
        sh = db.query(models.ShareLink).filter_by(token=tok).one()
        sh.expires_at = datetime.now() - timedelta(minutes=1)
        db.commit()
    assert client.get(row["thumbnail_url"], headers=viewer).status_code == 404
    row = _row(client, viewer, sid)
    assert row["status"] == "expired" and row["thumbnail_url"] is None
    with SessionLocal() as db:
        db.query(models.ShareLink).filter_by(token=tok).one().expires_at = None
        db.commit()
    url = _row(client, viewer, sid)["thumbnail_url"]
    assert client.get(url, headers=viewer).status_code == 200

    # 중지하면 404, 이미 받은 그림을 확인 요청으로 다시 쓸 수도 없다
    etag = client.get(url, headers=viewer).headers["etag"]
    client.delete(f"/api/shares/{tok}", headers=admin)
    assert client.get(url, headers=viewer).status_code == 404
    assert client.get(url, headers={**viewer, "If-None-Match": etag}).status_code == 404
    row = _row(client, viewer, sid)
    assert row["status"] == "stopped" and row["thumbnail_url"] is None


def test_project_image_is_for_project_people_only(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    viewer, _ = _auth(client, "thumb_viewer2", admin)
    pid, sid, v1 = _sim_flat(client, admin, "이미지 결과")
    url = f"/api/models/{v1['id']}/thumbnail"
    assert client.get(url, headers=admin).status_code == 204  # 그림이 없으면 없다고 답한다

    _publish_image(client, admin, pid, "관계없는 그림", content=PNG + b"other")
    assert client.get(url, headers=admin).status_code == 204  # 이 스터디와 상관없는 이미지는 쓰지 않는다

    _publish_image(client, admin, pid, "[이미지 결과] 응력 분포")
    r = client.get(url, headers=admin)
    assert r.status_code == 200 and r.content == PNG + b"project-image"

    # 링크로 받은 사람에게는 프로젝트 결과 이미지를 보이지 않는다
    tok = client.post(f"/api/models/{v1['id']}/shares", json={}, headers=admin).json()["token"]
    client.get(f"/api/shares/{tok}", headers=viewer)
    row = _row(client, viewer, sid)
    r = client.get(row["thumbnail_url"], headers=viewer)
    assert r.status_code == 204 and r.content == b""


def test_image_from_flow_and_choice_is_stable(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid, v1 = _sim_flat(client, admin, "흐름 그림")
    flow = client.post(f"/api/projects/{pid}/flows", headers=admin, json={
        "title": "흐름", "goal": "", "nodes": [{"id": "a", "label": "a", "type": "doe"}], "edges": []})
    assert flow.status_code == 200, flow.text
    fid = flow.json()["id"]
    url = f"/api/models/{v1['id']}/thumbnail"
    client.post(f"/api/projects/{pid}/artifacts", headers=admin, json={"kind": "markdown", "title": "[흐름 그림] 요약", "payload": {"text": "x"}, "flow_id": fid})
    _publish_image(client, admin, pid, "case · A", content=PNG + b"A", flow_id=fid)
    _publish_image(client, admin, pid, "case · B", content=PNG + b"B", flow_id=fid)
    assert client.get(url, headers=admin).content == PNG + b"B"  # 가장 최근 번호
    assert client.get(url, headers=admin).content == PNG + b"B"
    _publish_image(client, admin, pid, "[흐름 그림] 전용", content=PNG + b"own")
    assert client.get(url, headers=admin).content == PNG + b"own"  # 스터디 자신의 그림이 먼저다


def test_artifact_path_cannot_leave_project_folder(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid, v1 = _sim_flat(client, admin, "경로")
    secret = settings.data_dir / "thumb_secret.png"
    secret.write_bytes(PNG + b"secret")
    client.post(f"/api/projects/{pid}/artifacts", headers=admin, json={
        "kind": "image", "title": "[경로] 밖", "payload": {"path": f"../../{secret.name}"}})
    client.post(f"/api/projects/{pid}/artifacts", headers=admin, json={
        "kind": "image", "title": "[경로] 절대", "payload": {"path": str(secret)}})
    # 프로젝트 폴더 안이어도 그림이 아니면 내주지 않는다
    page = settings.files_dir / f"project_{pid}" / "images" / "page.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(b"<script>alert(1)</script>")
    client.post(f"/api/projects/{pid}/artifacts", headers=admin, json={"kind": "image", "title": "[경로] 그림 아님", "payload": {"path": "images/page.html"}})
    r = client.get(f"/api/models/{v1['id']}/thumbnail", headers=admin)
    assert r.status_code == 204, r.content[:40]


def test_drawn_once_and_reused(client, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    viewer, _ = _auth(client, "thumb_viewer3", admin)
    _, sid, v1, _ = _sim_3d(client, admin, "캐시")
    calls = _count_draws(monkeypatch)
    url = f"/api/models/{v1['id']}/thumbnail"
    first = client.get(url, headers=admin)
    assert first.status_code == 200 and len(calls) == 1
    assert client.get(url, headers=admin).content == first.content and len(calls) == 1
    # 링크로 받은 사람도 같은 그림을 쓴다
    tok = client.post(f"/api/models/{v1['id']}/shares", json={}, headers=admin).json()["token"]
    assert client.get(f"/api/shares/{tok}/thumbnail", headers=viewer).content == first.content and len(calls) == 1
    # 같은 그림이면 다시 보내지 않는다
    r = client.get(url, headers={**admin, "If-None-Match": first.headers["etag"]})
    assert r.status_code == 304 and r.content == b""
    assert list((settings.data_dir / "thumbnails").glob("*.tmp")) == []


def test_refreshes_on_new_version_and_rollback(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    viewer, _ = _auth(client, "thumb_viewer4", admin)
    _, sid, v1, f1 = _sim_3d(client, admin, "버전 그림")
    tok = client.post(f"/api/models/{v1['id']}/shares", json={}, headers=admin).json()["token"]
    client.get(f"/api/shares/{tok}", headers=viewer)
    before = _row(client, admin, sid)["thumbnail_url"]
    shared_before = _row(client, viewer, sid)["thumbnail_url"]
    etag1 = client.get(before, headers=admin).headers["etag"]

    v2 = _train(client, sid, admin)
    _deploy(client, v2["id"], admin)
    f2 = client.post(f"/api/studies/{sid}/train_field", json={"field": "stress"}, headers=admin).json()  # v2에 붙는다
    assert f2["id"] != f1["id"]
    after = _row(client, admin, sid)["thumbnail_url"]
    assert after != before and str(v2["id"]) in after
    r2 = client.get(after, headers=admin)
    assert r2.status_code == 200 and r2.headers["etag"] != etag1
    # 링크는 현재 버전을 따라간다. 주소가 바뀌어 화면이 다시 받는다
    shared_after = _row(client, viewer, sid)["thumbnail_url"]
    assert shared_after != shared_before
    assert client.get(shared_after, headers=viewer).headers["etag"] == r2.headers["etag"]

    # 배포 중에 3D만 다시 학습해도 새 그림이 된다
    f3 = client.post(f"/api/studies/{sid}/train_field", json={"field": "stress"}, headers=admin).json()
    r3 = client.get(after, headers=admin)
    assert f3["id"] != f2["id"] and r3.headers["etag"] != r2.headers["etag"]

    # 되돌리면 그때 그림으로 돌아간다
    _deploy(client, v1["id"], admin)
    back = _row(client, admin, sid)["thumbnail_url"]
    assert back == before
    assert client.get(back, headers=admin).headers["etag"] == etag1
    assert client.get(_row(client, viewer, sid)["thumbnail_url"], headers=viewer).headers["etag"] == etag1


def test_failed_render_falls_back_and_is_not_retried(client, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    viewer, _ = _auth(client, "thumb_viewer5", admin)
    pid, sid, v1, fm = _sim_3d(client, admin, "실패")
    with SessionLocal() as db:
        Path(db.get(models.FieldModel, fm["id"]).path).write_bytes(b"not a joblib file")
    calls = []
    real = thumbnail._render
    monkeypatch.setattr(thumbnail, "_render", lambda job: (calls.append(1), real(job))[1])
    url = f"/api/models/{v1['id']}/thumbnail"
    assert client.get(url, headers=admin).status_code == 204
    assert client.get(url, headers=admin).status_code == 204
    assert len(calls) == 1  # 깨진 모델을 카드를 열 때마다 다시 읽지 않는다
    assert client.get("/api/simulators", headers=admin).status_code == 200

    # 프로젝트 사람에게는 이미지 결과로, 링크로 받은 사람에게는 대체 표시로 넘어간다
    _publish_image(client, admin, pid, "[실패] 컨투어")
    assert client.get(url, headers=admin).content == PNG + b"project-image"
    tok = client.post(f"/api/models/{v1['id']}/shares", json={}, headers=admin).json()["token"]
    assert client.get(f"/api/shares/{tok}/thumbnail", headers=viewer).status_code == 204

    # 시간이 지나면 다시 시도한다
    for dest in thumbnail._failed:
        thumbnail._failed[dest] = time.monotonic() - thumbnail.RETRY_AFTER - 1
    assert client.get(f"/api/shares/{tok}/thumbnail", headers=viewer).status_code == 204
    assert len(calls) == 2

    # 모델 파일이 아예 없어도 같다
    with SessionLocal() as db:
        Path(db.get(models.FieldModel, fm["id"]).path).unlink()
    assert client.get(f"/api/shares/{tok}/thumbnail", headers=viewer).status_code == 204
    assert len(calls) == 2


def test_render_runs_off_the_loop_with_a_limit_and_once_per_picture(tmp_path, monkeypatch):
    lock, active, peak, ran, threads = threading.Lock(), [0], [0], [], set()

    def slow(job):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            ran.append(job.dest)
            threads.add(threading.current_thread().name)
        time.sleep(0.05)
        job.dest.write_bytes(PNG)
        with lock:
            active[0] -= 1
        return True

    monkeypatch.setattr(thumbnail, "_render", slow)
    jobs = [thumbnail.Job(Path("x"), {}, tmp_path / f"{i}.png") for i in range(8)]

    async def run():
        ticks = 0

        async def clock():  # 그리는 동안 이벤트 루프가 계속 돌아야 한다
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        c = asyncio.create_task(clock())
        got = await asyncio.gather(*(thumbnail.render(j) for j in jobs), *(thumbnail.render(jobs[0]) for _ in range(5)))
        c.cancel()
        return got, ticks

    got, ticks = asyncio.run(run())
    assert all(got) and len(ran) == 8  # 같은 그림의 동시 요청은 한 번만 그린다
    assert peak[0] <= thumbnail.RENDER_WORKERS
    assert ticks > 5 and all(t.startswith("thumbnail") for t in threads)


def _grid(n):
    x, y = np.meshgrid(np.linspace(0, 100, n), np.linspace(0, 30, n))
    pts = np.column_stack([x.ravel(), y.ravel(), 5 * np.sin(x.ravel() / 10)])
    i = np.arange(n * n).reshape(n, n)
    a, b, c, d = i[:-1, :-1].ravel(), i[1:, :-1].ravel(), i[1:, 1:].ravel(), i[:-1, 1:].ravel()
    return pts, pts[:, 2], np.vstack([np.column_stack([a, b, c]), np.column_stack([a, c, d])])


def test_big_mesh_is_decimated_without_holes():
    pts, vals, tris = _grid(500)
    assert len(tris) > 400_000
    p, v, t = thumbnail._decimate(pts, vals, tris)
    assert len(t) <= thumbnail.MAX_TRIANGLES and len(p) == len(v) and t.max() < len(p)
    assert np.allclose(p.min(axis=0)[:2], pts.min(axis=0)[:2], atol=1) and np.allclose(p.max(axis=0)[:2], pts.max(axis=0)[:2], atol=1)  # 전체 형상이 남는다
    small_p, small_v, small_t = thumbnail._decimate(*_grid(20))
    assert len(small_t) == 2 * 19 * 19  # 작은 메쉬는 그대로

    from matplotlib.image import imread

    out = BytesIO()
    thumbnail.draw(pts, vals, tris, out)
    out.seek(0)
    img = imread(out, format="png")
    assert img.shape[:2] == (thumbnail.HEIGHT, thumbnail.WIDTH)
    assert img[0, 0, 3] == 0  # 바탕은 투명하다(두 테마에 맞게)
    opaque = img[..., 3] > 0.5
    assert 0.1 < opaque.mean() < 0.95  # 형상이 그림을 채운다
    # 구멍 검사: 그림 가운데 줄에서 칠해진 구간 안쪽에 빈 칸이 없어야 한다
    middle = opaque[thumbnail.HEIGHT // 2]
    inside = np.flatnonzero(middle)
    assert middle[inside[0]:inside[-1] + 1].all()


def test_points_without_triangles_and_flat_values_still_draw():
    pts = np.random.default_rng(0).random((500, 3))
    out = BytesIO()
    thumbnail.draw(pts, np.ones(500), None, out)
    assert out.getvalue().startswith(PNG)


def _painted(pts, vals, tris):
    from matplotlib.image import imread

    out = BytesIO()
    thumbnail.draw(pts, vals, tris, out)
    out.seek(0)
    return (imread(out, format="png")[..., 3] > 0.5).mean()


def test_node_no_triangle_uses_does_not_shrink_the_picture():
    pts, vals, tris = _grid(60)
    normal = _painted(pts, vals, tris)
    far = np.vstack([pts, [1e6, 1e6, 1e6]])
    assert _painted(far, np.append(vals, 99.0), tris) == normal  # 색 범위도 보이는 노드로만 정한다
    # 큰 메쉬도 그 노드 때문에 줄이기 격자가 망가지지 않는다
    big, big_vals, big_tris = _grid(250)
    assert _painted(np.vstack([big, [1e6, 1e6, 1e6]]), np.append(big_vals, 0.0), big_tris) > 0.1


def test_picture_with_nothing_visible_is_a_failure():
    pts, vals, tris = _grid(60)
    # 삼각형이 쓰는 먼 노드: 형상이 점으로 줄어든다
    stray = np.vstack([pts, [[1e6, 1e6, 1e6], [1e6 + 1, 1e6, 1e6], [1e6, 1e6 + 1, 1e6]]])
    with_stray = np.vstack([tris, [len(pts), len(pts) + 1, len(pts) + 2]])
    # 넓이 없는 삼각형뿐이거나, 줄이고 나니 하나도 남지 않거나, 점 하나로 뭉친 경우
    degenerate = np.zeros((40_000, 3), np.int64)
    degenerate[:, 1:] = 1
    for args in (
        (stray, np.zeros(len(stray)), with_stray),
        (pts, vals, degenerate),
        (np.zeros((3, 3)), np.array([np.nan, 1.0, 2.0]), np.array([[0, 1, 2]])),
    ):
        with pytest.raises(ValueError):
            thumbnail.draw(*args, BytesIO())
    big, big_vals, big_tris = _grid(250)
    far = np.vstack([big, [[1e6, 1e6, 1e6], [1e6 + 1, 1e6, 1e6], [1e6, 1e6 + 1, 1e6]]])
    with pytest.raises(ValueError):
        thumbnail.draw(far, np.zeros(len(far)), np.vstack([big_tris, [len(big), len(big) + 1, len(big) + 2]]), BytesIO())


def test_empty_picture_is_not_cached_and_falls_back(client, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid, v1, _ = _sim_3d(client, admin, "빈 그림")
    _publish_image(client, admin, pid, "[빈 그림] 컨투어")

    class Stray:
        variables = []
        triangles = np.array([[0, 1, 2], [3, 4, 5]])

        def predict(self, _):
            pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1e6, 1e6, 1e6], [1e6 + 1, 1e6, 1e6], [1e6, 1e6 + 1, 1e6]], float)
            return pts, np.zeros(6), None

    from server.app.study import fieldmodel

    monkeypatch.setattr(fieldmodel.FieldROM, "load", classmethod(lambda cls, path: Stray()))
    before = set((settings.data_dir / "thumbnails").glob("*.png")) if (settings.data_dir / "thumbnails").exists() else set()
    r = client.get(f"/api/models/{v1['id']}/thumbnail", headers=admin)
    assert r.status_code == 200 and r.content == PNG + b"project-image"  # 빈 상자가 아니라 이미지 결과
    assert set((settings.data_dir / "thumbnails").glob("*.png")) == before


def test_project_image_lookup_runs_off_the_event_loop(client, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid, v1 = _sim_flat(client, admin, "루프 밖")
    real, seen = thumbnail.project_image, []

    def spy(*a):
        try:
            asyncio.get_running_loop()
            seen.append("loop")
        except RuntimeError:
            seen.append("worker")
        return real(*a)

    monkeypatch.setattr(thumbnail, "project_image", spy)
    assert client.get(f"/api/models/{v1['id']}/thumbnail", headers=admin).status_code == 204
    assert seen == ["worker"]


@pytest.mark.parametrize("path", [
    r"\\192.0.2.1\share\x.png", "//192.0.2.1/share/x.png", r"\\?\C:\x.png", r"C:\x.png", "C:x.png", "/etc/x.png", r"\x.png",
    "../x.png", r"a\..\..\x.png", "a\0b.png", "", 5, None, "images/page.html",
])
def test_bad_image_path_is_dropped_without_touching_the_file_system(path, tmp_path, monkeypatch):
    def boom(*a, **k):  # 네트워크 경로는 여기서 멈추므로, 부르기 전에 걸러야 한다
        raise AssertionError("파일 시스템을 봤습니다")

    root = tmp_path.resolve()
    monkeypatch.setattr(Path, "resolve", boom)
    assert thumbnail._image_file(root, path) is None


def test_bad_paths_do_not_hide_a_good_image(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid, v1 = _sim_flat(client, admin, "널")
    _publish_image(client, admin, pid, "[널] 좋은 그림")
    for bad in (r"\\192.0.2.1\share\x.png", "a\u0000b.png"):
        r = client.post(f"/api/projects/{pid}/artifacts", headers=admin, json={"kind": "image", "title": "[널] 나쁜 그림", "payload": {"path": bad}})
        assert r.status_code == 200, r.text
    started = time.monotonic()
    got = client.get(f"/api/models/{v1['id']}/thumbnail", headers=admin)
    assert got.status_code == 200 and got.content == PNG + b"project-image"
    assert time.monotonic() - started < 5  # 네트워크 경로의 시간 초과를 기다리지 않는다


def test_study_name_is_compared_exactly_in_titles(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid, v1 = _sim_flat(client, admin, "a_b%")
    url = f"/api/models/{v1['id']}/thumbnail"
    _publish_image(client, admin, pid, "[axby] 다른 스터디", content=PNG + b"other")
    _publish_image(client, admin, pid, "[A_B%] 대소문자만 다른 스터디", content=PNG + b"other")
    assert client.get(url, headers=admin).status_code == 204  # SQL의 _·%가 와일드카드로 쓰이면 다른 스터디의 그림이 잡힌다
    _publish_image(client, admin, pid, "[a_b%] 이 스터디", content=PNG + b"mine")
    assert client.get(url, headers=admin).content == PNG + b"mine"


def test_near_images_never_outrank_own_even_when_many(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid, v1 = _sim_flat(client, admin, "많은 흐름")
    fid = client.post(f"/api/projects/{pid}/flows", headers=admin, json={
        "title": "흐름", "goal": "", "nodes": [{"id": "a", "label": "a", "type": "doe"}], "edges": []}).json()["id"]
    _publish_image(client, admin, pid, "[많은 흐름] 전용", content=PNG + b"own", flow_id=fid)
    for i in range(thumbnail.MAX_IMAGE_TRIES + 3):
        client.post(f"/api/projects/{pid}/artifacts", headers=admin, json={"kind": "image", "title": f"case {i}", "payload": {"path": "없는파일.png"}, "flow_id": fid})
    assert client.get(f"/api/models/{v1['id']}/thumbnail", headers=admin).content == PNG + b"own"


def test_list_gives_what_the_card_needs(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    _, sid = _study(client, admin, "카드 값")
    v = _train(client, sid, admin)
    _deploy(client, v["id"], admin)
    row = _row(client, admin, sid)
    assert row["n_variables"] == 2 and row["outputs"] == ["max_stress"] and row["thumbnail_url"]
