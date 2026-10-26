# -*- coding: utf-8 -*-
"""
binout / d3plot 결과 추출 (tool 키: python, 플랫폼 venv)
  ** lasso-python 이 플랫폼 venv 에 설치돼 있어야 한다:  pip install lasso-python  (numpy 포함) **

  python dyna_lasso_extract.py [--list] [--nodes 1001,1002] [--parts 3,4] [--no-d3plot]

출력 키 (results.json 에 합쳐 씀)
  binout : final_kinetic_energy, final_internal_energy, max_kinetic_energy,
           node<id>_max_disp (nodout), max_contact_force_<i> (rcforc)
  d3plot : max_disp, max_shell_vm, max_shell_eps, max_solid_vm, max_solid_eps  (전 state 중 최대)
--list 로 binout 디렉터리/변수 이름과 d3plot 배열 목록을 먼저 확인하고 이름을 맞춘다.
"""
import argparse
import glob
import json
import os
import sys

try:
    import numpy as np
    from lasso.dyna import Binout, D3plot, ArrayType
except ImportError as e:  # 설치 안내 후 실패 종료
    print("[dyna_lasso_extract] lasso-python/numpy not available in this venv: %s" % e, flush=True)
    print("[dyna_lasso_extract] install with: pip install lasso-python", flush=True)
    sys.exit(1)


def log(msg):
    print("[dyna_lasso_extract] " + str(msg), flush=True)


def von_mises(s):
    """s[..., 6] = (xx, yy, zz, xy, yz, zx) -> von Mises"""
    sx, sy, sz, txy, tyz, tzx = (s[..., i] for i in range(6))
    return np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2) + 3.0 * (txy ** 2 + tyz ** 2 + tzx ** 2))


# ------------------------------------------------------------------ binout
def binout_results(nodes, do_list):
    files = glob.glob("binout*")
    if not files:
        return {}
    b = Binout("binout*")
    top = b.read()
    res = {}
    if do_list:
        for d in top:
            log("binout/%s: %s" % (d, b.read(d)))
    if "glstat" in top:
        names = b.read("glstat")
        # TODO(사내검증): 변수 이름은 b.read('glstat') 출력으로 확인
        for k in ("kinetic_energy", "internal_energy", "total_energy", "hourglass_energy"):
            if k in names:
                arr = np.asarray(b.read("glstat", k), dtype=float)
                res["final_" + k] = float(arr[-1])
                if k == "kinetic_energy":
                    res["max_kinetic_energy"] = float(arr.max())
    if "nodout" in top and nodes:
        ids = list(np.asarray(b.read("nodout", "ids")).ravel())
        comps = [np.asarray(b.read("nodout", c), dtype=float)
                 for c in ("x_displacement", "y_displacement", "z_displacement")]  # TODO(사내검증): 이름 확인
        for nid in nodes:
            if nid not in ids:
                log("node %d not in nodout ids" % nid)
                continue
            j = ids.index(nid)
            col = [c[:, j] if c.ndim == 2 else c for c in comps]
            mag = np.sqrt(col[0] ** 2 + col[1] ** 2 + col[2] ** 2)
            res["node%d_max_disp" % nid] = float(mag.max())
    if "rcforc" in top:
        # TODO(사내검증): rcforc 의 ids/side 구조는 버전마다 다르다. b.read('rcforc') 로 확인 후 수정.
        names = b.read("rcforc")
        if all(k in names for k in ("x_force", "y_force", "z_force")):
            fx, fy, fz = (np.asarray(b.read("rcforc", k), dtype=float) for k in ("x_force", "y_force", "z_force"))
            mag = np.sqrt(fx ** 2 + fy ** 2 + fz ** 2)
            if mag.ndim == 1:
                mag = mag[:, None]
            for i in range(mag.shape[1]):
                res["max_contact_force_%d" % i] = float(mag[:, i].max())
    return res


# ------------------------------------------------------------------ d3plot
def d3plot_results(parts, do_list):
    if not os.path.exists("d3plot"):
        return {}
    d3 = D3plot("d3plot")     # d3plot01, 02 ... 자동으로 이어 읽음
    arrs = d3.arrays
    if do_list:
        for k, v in arrs.items():
            log("d3plot %s %s" % (k, getattr(v, "shape", "")))
    res = {}
    if ArrayType.node_displacement in arrs:
        # lasso 의 node_displacement 는 각 state 의 '현재 좌표'다(이름과 달리).
        # state0 을 빼면 좌표/변위 어느 쪽이어도 변위가 된다.
        x = arrs[ArrayType.node_displacement]
        u = x - x[0]
        res["max_disp"] = float(np.sqrt((u ** 2).sum(axis=-1)).max())
        res["n_states"] = float(x.shape[0])

    def part_mask(index_key):
        # TODO(사내검증): element_*_part_indexes 는 part_ids 배열의 인덱스. 파트 필터 동작 확인.
        if not parts or index_key not in arrs or ArrayType.part_ids not in arrs:
            return None
        pids = np.asarray(arrs[ArrayType.part_ids])
        idx = np.asarray(arrs[index_key])
        return np.isin(pids[idx], parts)

    for kind, s_key, e_key, i_key in (
            ("shell", ArrayType.element_shell_stress, ArrayType.element_shell_effective_plastic_strain,
             ArrayType.element_shell_part_indexes),
            ("solid", ArrayType.element_solid_stress, ArrayType.element_solid_effective_plastic_strain,
             ArrayType.element_solid_part_indexes)):
        mask = part_mask(i_key)
        if s_key in arrs:
            vm = von_mises(np.asarray(arrs[s_key], dtype=float))   # (state, elem, [layer/ip], ...)
            if mask is not None:
                vm = vm[:, mask]
            if vm.size:
                res["max_%s_vm" % kind] = float(vm.max())
        if e_key in arrs:
            ep = np.asarray(arrs[e_key], dtype=float)
            if mask is not None:
                ep = ep[:, mask]
            if ep.size:
                res["max_%s_eps" % kind] = float(ep.max())
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--nodes", default="")
    ap.add_argument("--parts", default="")
    ap.add_argument("--no-d3plot", action="store_true")
    a = ap.parse_args()
    nodes = [int(x) for x in a.nodes.split(",") if x.strip()]
    parts = [int(x) for x in a.parts.split(",") if x.strip()]

    res = binout_results(nodes, a.list)
    if not a.no_d3plot:
        res.update(d3plot_results(parts, a.list))
    if a.list:
        log("partial results: " + json.dumps(res, sort_keys=True))
        return 0
    if not res:
        log("no results from binout/d3plot")
        return 1
    path = "results.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            base = json.load(f)
        base.update(res)
        res = base
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict((k, float(v)) for k, v in res.items()), f, indent=2, sort_keys=True)
    log("results: " + json.dumps(res, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        log("FAILED: " + str(e))
        sys.exit(1)
