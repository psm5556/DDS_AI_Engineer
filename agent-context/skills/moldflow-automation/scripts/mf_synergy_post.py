"""Moldflow 결과 후처리 (tool: moldflow_synergy = 플랫폼 python 3.12 + pywin32 + numpy + matplotlib)

해석이 끝난 스터디를 Synergy COM 으로 열어
  1) 결과 플롯의 노드/요소 값 통계(max/min) -> results.json 에 병합 (PLOT_STATS)
  2) 결과 플롯 PNG -> images/<key>.png  (Viewer.ShowPlot + Viewer.SaveImage)
  3) 노드 필드(예: 휨 변형량) -> fields/<name>.npz (points, values, triangles) + images/<name>_3d.png(matplotlib)

실행 예 (adapter post 단계):
  python mf_synergy_post.py --project proj/proj.mpi --study-name cover_study [--field-plot "Deflection, all effects:Deflection" --field-name warpage] [--strict]
실패 정책: --strict 이면 PLOT_STATS 하나라도 실패 시 exit 1. 기본은 이미지/필드 실패를 경고로만 남긴다.

TODO(사내검증): 플롯 이름은 Synergy UI 언어(한글/영문)와 버전에 따라 다르다. --list-plots 로 실제 이름을 먼저 확인한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

# results 키 -> (플롯 이름, 통계 max|min|count_nonzero, 배율)
# 독립변수(시간 등)가 있는 결과는 마지막 값(최종 시점)을 쓴다.
PLOT_STATS = {
    "fill_time_max_s": ("Fill time", "max", 1.0),
    "vp_pressure_max_mpa": ("Pressure at V/P switchover", "max", 1.0),
    "flow_front_temp_min_c": ("Temperature at flow front", "min", 1.0),
    "vol_shrinkage_max_pct": ("Volumetric shrinkage at ejection", "max", 1.0),
    "warp_deflection_max_mm": ("Deflection, all effects:Deflection", "max", 1.0),
    "weld_line_nodes": ("Weld lines", "count_nonzero", 1.0),   # 웰드라인이 표시된 엔티티 수(지표용)
    "air_trap_nodes": ("Air traps", "count_nonzero", 1.0),
}
# images/<key>.png 로 저장할 플롯
IMAGE_PLOTS = {
    "fill_time": "Fill time",
    "vp_pressure": "Pressure at V/P switchover",
    "warp_deflection": "Deflection, all effects:Deflection",
    "weld_lines": "Weld lines",
    "air_traps": "Air traps",
}


def log(msg):
    print("[mf_post] " + str(msg))
    sys.stdout.flush()


def connect_and_open(project, study_name):
    import win32com.client
    syn = None
    inst = os.environ.get("SAInstance")
    if inst:
        try:
            syn = win32com.client.GetObject(inst).GetSASynergy   # TODO(사내검증)
        except Exception:
            syn = None
    if syn is None:
        syn = win32com.client.Dispatch("synergy.Synergy")
    syn.SetUnits("Metric")
    syn.OpenProject(os.path.abspath(project))
    syn.Project.OpenItemByName(study_name, "Study")   # TODO(사내검증)
    return syn


def arr(a):
    return [a.Val(i) for i in range(a.Size)] if a is not None else []


def plot_data(syn, plot_name):
    """플롯 이름 -> (엔티티 ID 리스트, 값 리스트, 데이터 종류 NDDT|ELDT). 마지막 독립변수 값 기준."""
    pm = syn.PlotManager
    plot = pm.FindPlotByName(plot_name)
    if plot is None:
        raise RuntimeError("plot not found: %s" % plot_name)
    ds = plot.GetDataID()
    indp = syn.CreateDoubleArray()
    pm.GetIndpValues(ds, indp)
    sel = syn.CreateDoubleArray()
    if indp.Size > 0:
        sel.AddDouble(indp.Val(indp.Size - 1))   # 마지막 시점
    ent = syn.CreateIntegerArray()
    val = syn.CreateDoubleArray()
    pm.GetScalarData(ds, sel, ent, val)   # TODO(사내검증): 벡터 결과는 GetScalarData 가 아니라 별도 API
    dtype = ""
    try:
        dtype = pm.GetDataType(ds)
    except Exception:
        pass
    ids = [ent.Val(i) for i in range(ent.Size)]
    vals = arr(val)
    if not vals:
        raise RuntimeError("no scalar data in plot: %s" % plot_name)
    return ids, vals, dtype


def stat(vals, how):
    if how == "max":
        return max(vals)
    if how == "min":
        return min(vals)
    if how == "count_nonzero":
        return float(sum(1 for v in vals if abs(v) > 0))
    raise ValueError(how)


def save_plot_image(syn, plot_name, path):
    plot = syn.PlotManager.FindPlotByName(plot_name)
    if plot is None:
        raise RuntimeError("plot not found: %s" % plot_name)
    viewer = syn.Viewer
    viewer.ShowPlot(plot)
    # TODO(사내검증): SaveImage 지원 확장자(png/jpg/bmp)와 해상도 지정 변형 함수 존재 여부
    viewer.SaveImage(os.path.abspath(path))


def mesh_surface(syn):
    """노드 좌표와 삼각형(표면/미드플레인 메쉬) -> (id 목록, 좌표, 삼각형 노드 id).
    TODO(사내검증): GetFirstNode/GetNextNode/GetNodeCoord/GetEntityID/GetFirstTri/GetNextTri/GetElemNodes 이름과 반환형.
    3D(사면체) 메쉬는 삼각형이 없으므로 triangles 가 비고 점 필드만 저장된다."""
    sd = syn.StudyDoc
    ids, coords = [], []
    node = sd.GetFirstNode()
    while node is not None and node.Size > 0:
        v = sd.GetNodeCoord(node)
        ids.append(sd.GetEntityID(node))
        coords.append((v.X, v.Y, v.Z))
        node = sd.GetNextNode(node)
    tris = []
    tri = sd.GetFirstTri()
    while tri is not None and tri.Size > 0:
        nodes = sd.GetElemNodes(tri)
        tris.append(tuple(sd.GetEntityID(nodes.Entity(i)) for i in range(3)))
        tri = sd.GetNextTri(tri)
    return ids, coords, tris


def save_field(syn, plot_name, name):
    import numpy as np
    ent_ids, vals, dtype = plot_data(syn, plot_name)
    if dtype and dtype != "NDDT":
        raise RuntimeError("field plot is not nodal data (%s)" % dtype)
    vmap = dict(zip(ent_ids, vals))
    ids, coords, tris = mesh_surface(syn)
    index = {nid: i for i, nid in enumerate(ids)}
    points = np.asarray(coords, dtype=float)
    values = np.asarray([vmap.get(n, 0.0) for n in ids], dtype=float)
    os.makedirs("fields", exist_ok=True)
    data = {"points": points, "values": values}
    if tris:
        data["triangles"] = np.asarray([[index[n] for n in t] for t in tris if all(n in index for n in t)],
                                       dtype=np.int32)
    np.savez_compressed(os.path.join("fields", name + ".npz"), **data)
    log("saved fields/%s.npz nodes=%d tris=%d max=%.6g" % (name, len(points), len(tris), values.max()))
    mpl_image(points, values, data.get("triangles"), os.path.join("images", name + "_3d.png"), plot_name)


def mpl_image(points, values, tris, path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(8, 6), dpi=120)
    ax = fig.add_subplot(111, projection="3d")
    if tris is not None and len(tris):
        surf = ax.plot_trisurf(points[:, 0], points[:, 1], points[:, 2], triangles=tris, cmap="jet", linewidth=0)
        surf.set_array(values[tris].mean(axis=1))
        fig.colorbar(surf, ax=ax, shrink=0.6)
    else:
        sc = ax.scatter(points[:, 0], points[:, 1], points[:, 2], c=values, cmap="jet", s=2)
        fig.colorbar(sc, ax=ax, shrink=0.6)
    ax.set_title(title)
    ax.view_init(elev=30, azim=-60)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--study-name", required=True)
    ap.add_argument("--field-plot", help="nodal result plot to save as fields/<name>.npz")
    ap.add_argument("--field-name", default="warpage")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--list-plots", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    os.makedirs("images", exist_ok=True)

    syn = connect_and_open(a.project, a.study_name)
    try:
        if a.list_plots:
            pm = syn.PlotManager
            p = pm.GetFirstPlot()
            names = []
            while p is not None:
                names.append(p.GetName())
                p = pm.GetNextPlot(p)
            with open("mf_plots.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(names) + "\n")
            log("wrote mf_plots.txt (%d plots)" % len(names))
            return 0

        stats, failed = {}, []
        for key, (pname, how, scale) in PLOT_STATS.items():
            try:
                _, vals, _ = plot_data(syn, pname)
                stats[key] = stat(vals, how) * scale
                log("%s = %.6g (%s, %s)" % (key, stats[key], pname, how))
            except Exception as e:
                failed.append(key)
                log("WARNING stat %s failed: %s" % (key, e))
        if failed and a.strict:
            raise RuntimeError("plot stats failed: %s" % failed)

        if not a.no_images:
            for key, pname in IMAGE_PLOTS.items():
                try:
                    save_plot_image(syn, pname, os.path.join("images", key + ".png"))
                    log("image images/%s.png" % key)
                except Exception as e:
                    log("WARNING image %s failed: %s" % (key, e))
        if a.field_plot:
            try:
                save_field(syn, a.field_plot, a.field_name)
            except Exception as e:
                if a.strict:
                    raise
                log("WARNING field export failed: %s" % e)
    finally:
        try:
            syn.Quit(False)   # TODO(사내검증)
        except Exception:
            pass

    res_path = "results.json"
    results = {}
    if os.path.isfile(res_path):
        with open(res_path, encoding="utf-8") as f:
            results = json.load(f)
    elif not stats:
        raise RuntimeError("no results.json and no plot stats")
    results.update(stats)
    tmp = res_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True)
    os.replace(tmp, res_path)
    log("results.json updated (%d keys)" % len(results))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
