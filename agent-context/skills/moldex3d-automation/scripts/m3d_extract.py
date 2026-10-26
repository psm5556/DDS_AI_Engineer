"""Moldex3D 내보낸 결과 파일 -> results.json / fields/<name>.npz / images/  (tool: python, 플랫폼 venv)

Moldex3D Studio(또는 사내 확인된 API/배치)로 내보낸 텍스트 파일을 읽는다. 툴 호출은 하지 않는다.

  요약표:  --summary summary.csv      행마다 '항목, 값[, 단위]' (CSV/TSV/세미콜론) 또는 '항목 = 값 단위' / '항목: 값 단위' 텍스트
           -> KEY_PATTERNS 로 핵심 키를 뽑아 results.json, 모든 항목은 m3d_summary_all.json
  노드값:  --node-csv warp.csv --name warpage [--value-col "Total"] [--tri-csv tri.csv]
           헤더에서 x/y/z 좌표 열과 값 열을 찾아 fields/<name>.npz (points, values[, triangles])
           + images/<name>_3d.png (matplotlib) + results 에 <name>_max / <name>_min 추가
  옵션:    --require k1,k2 (없으면 실패)  --merge (기존 results.json 에 병합)
실패:  필수 키 누락, 추출 값 0개, 파일 없음 -> results.json 미작성 + exit 1

TODO(사내검증): Moldex3D 결과 내보내기 형식(메뉴, 열 이름, 단위 표기, 인코딩)은 버전마다 다르다.
  첫 케이스의 m3d_summary_all.json 과 노드 CSV 헤더를 보고 KEY_PATTERNS / 열 이름을 맞춘다.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys

NUM_RE = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([^\d\s].*)?$")   # 숫자 + 단위 허용
FLOAT_RE = re.compile(r"^\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*$")              # 순수 숫자

# results 키 -> (항목 이름 정규식 후보, 선택 first|last|max|min). 단위 환산은 하지 않으므로 키에 단위를 적는다.
KEY_PATTERNS = {   # TODO(사내검증): 실제 요약표 항목 이름으로 보정
    "fill_time_s": ([r"filling time", r"fill(ing)? end time"], "first"),
    "max_sprue_pressure_mpa": ([r"max.*sprue pressure", r"sprue pressure.*max"], "max"),
    "max_injection_pressure_mpa": ([r"max.*injection pressure"], "max"),
    "clamp_force_max_tonne": ([r"max.*clamp(ing)? force", r"clamp(ing)? force"], "max"),
    "cycle_time_s": ([r"cycle time"], "first"),
    "max_total_displacement_mm": ([r"max.*total displacement", r"total displacement.*max"], "max"),
    "max_vol_shrinkage_pct": ([r"max.*volumetric shrinkage"], "max"),
}


def log(msg):
    print("[m3d_extract] " + msg)
    sys.stdout.flush()


def snake(text):
    return re.sub(r"_+", "_", re.sub(r"[^0-9a-zA-Z]+", "_", text.strip().lower())).strip("_")


def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp949", "latin-1"):
        try:
            t = raw.decode(enc)
            if "\x00" not in t:
                return t
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def sniff_rows(text):
    sample = text[:4096]
    delim = max([",", "\t", ";"], key=sample.count)
    if sample.count(delim) == 0:
        return None
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]


def parse_summary(text):
    """-> [(label, value, unit)]"""
    items = []
    rows = sniff_rows(text)
    if rows:
        for r in rows:
            if len(r) < 2 or not r[0]:
                continue
            for j in range(1, len(r)):
                m = NUM_RE.match(r[j])
                if m:
                    unit = (m.group(2) or (r[j + 1] if j + 1 < len(r) else "")).strip()
                    items.append((r[0], float(m.group(1)), unit))
                    break
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z][^=:]*?)\s*[=:]\s*(.+)$", line)
        if m:
            n = NUM_RE.match(m.group(2))
            if n:
                items.append((m.group(1).strip(), float(n.group(1)), (n.group(2) or "").strip()))
    return items


def extract_keys(items):
    out = {}
    for key, (pats, pick) in KEY_PATTERNS.items():
        hits = [v for lab, v, _ in items if any(re.search(p, lab, re.IGNORECASE) for p in pats)]
        if hits:
            out[key] = {"first": hits[0], "last": hits[-1], "max": max(hits), "min": min(hits)}[pick]
    return out


def find_col(header, cands):
    norm = [snake(h) for h in header]
    for c in cands:
        for i, h in enumerate(norm):
            if re.fullmatch(c, h):
                return i
    return None


def read_node_csv(path, value_col):
    import numpy as np
    rows = sniff_rows(read_text(path))
    if not rows:
        raise RuntimeError("node file is not delimited text: %s" % path)
    def numeric_row(r, n):
        return len(r) >= n and all(FLOAT_RE.match(c) for c in r[:n])
    # 헤더 = 열이 4개 이상이고 바로 다음 행이 숫자 행인 첫 행 (앞쪽 제목 줄은 건너뜀)
    hi = next((i for i in range(len(rows) - 1)
               if len(rows[i]) >= 4 and not FLOAT_RE.match(rows[i][0]) and numeric_row(rows[i + 1], len(rows[i]))), None)
    if hi is None:
        raise RuntimeError("header row with x/y/z/value columns not found in %s" % path)
    header = rows[hi]
    data = [r for r in rows[hi + 1:] if numeric_row(r, len(header))]
    ix = find_col(header, [r"x", r"x_mm", r"coord_?x.*", r"x_?coord.*", r"pos(ition)?_?x.*"])
    iy = find_col(header, [r"y", r"y_mm", r"coord_?y.*", r"y_?coord.*", r"pos(ition)?_?y.*"])
    iz = find_col(header, [r"z", r"z_mm", r"coord_?z.*", r"z_?coord.*", r"pos(ition)?_?z.*"])
    if None in (ix, iy, iz):
        raise RuntimeError("x/y/z columns not found in header %s" % header)
    iid = find_col(header, [r"node(_?id)?", r"id", r"nid"])
    if value_col:
        cand = [i for i, h in enumerate(header) if value_col.lower() in h.lower()]
        if not cand:
            raise RuntimeError("value column '%s' not in header %s" % (value_col, header))
        iv = cand[0]
    else:
        iv = max(i for i in range(len(header)) if i not in (ix, iy, iz, iid))
    log("node csv: header=%s x=%d y=%d z=%d value=%d(%s) rows=%d" % (header, ix, iy, iz, iv, header[iv], len(data)))
    pts = np.array([[float(r[ix]), float(r[iy]), float(r[iz])] for r in data])
    vals = np.array([float(r[iv]) for r in data])
    ids = [int(float(r[iid])) for r in data] if iid is not None else None
    return pts, vals, ids, header[iv]


def read_tris(path, ids):
    import numpy as np
    rows = sniff_rows(read_text(path)) or []
    index = {n: i for i, n in enumerate(ids)} if ids else None
    tris = []
    for r in rows:
        nums = [c for c in r if c and FLOAT_RE.match(c)]
        if len(nums) < 3:
            continue
        t = [int(float(c)) for c in nums[-3:]]   # 마지막 3열을 노드 번호로 본다
        if index is not None:
            if not all(n in index for n in t):
                continue
            t = [index[n] for n in t]
        tris.append(t)
    return np.array(tris, dtype=np.int32)


def plot3d(points, values, tris, path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(8, 6), dpi=120)
    ax = fig.add_subplot(111, projection="3d")
    if tris is not None and len(tris):
        s = ax.plot_trisurf(points[:, 0], points[:, 1], points[:, 2], triangles=tris, cmap="jet", linewidth=0)
        s.set_array(values[tris].mean(axis=1))
    else:
        s = ax.scatter(points[:, 0], points[:, 1], points[:, 2], c=values, cmap="jet", s=2)
    fig.colorbar(s, ax=ax, shrink=0.6)
    ax.set_title(title)
    ax.view_init(elev=30, azim=-60)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def main():
    import numpy as np
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary")
    ap.add_argument("--node-csv")
    ap.add_argument("--name", default="warpage")
    ap.add_argument("--value-col")
    ap.add_argument("--tri-csv")
    ap.add_argument("--require", default="")
    ap.add_argument("--merge", action="store_true")
    a = ap.parse_args()
    if not a.summary and not a.node_csv:
        raise RuntimeError("give --summary and/or --node-csv")

    results = {}
    if a.summary:
        items = parse_summary(read_text(a.summary))
        allv, seen = {}, {}
        for lab, v, unit in items:
            k = snake(lab) + ("_" + snake(unit) if unit and snake(unit) else "")
            seen[k] = seen.get(k, 0) + 1
            allv[k if seen[k] == 1 else "%s_%d" % (k, seen[k])] = v
        write_json("m3d_summary_all.json", allv)
        log("summary items: %d -> m3d_summary_all.json" % len(items))
        results.update(extract_keys(items))

    if a.node_csv:
        pts, vals, ids, vname = read_node_csv(a.node_csv, a.value_col)
        if len(vals) == 0:
            raise RuntimeError("no node rows in %s" % a.node_csv)
        data = {"points": pts, "values": vals}
        tris = read_tris(a.tri_csv, ids) if a.tri_csv else None
        if tris is not None and len(tris):
            data["triangles"] = tris
        os.makedirs("fields", exist_ok=True)
        np.savez_compressed(os.path.join("fields", a.name + ".npz"), **data)
        log("saved fields/%s.npz nodes=%d tris=%d" % (a.name, len(pts), 0 if tris is None else len(tris)))
        results[a.name + "_max"] = float(vals.max())
        results[a.name + "_min"] = float(vals.min())
        try:
            os.makedirs("images", exist_ok=True)
            plot3d(pts, vals, data.get("triangles"), os.path.join("images", a.name + "_3d.png"), vname)
        except Exception as e:  # 이미지 실패는 케이스 실패 아님
            log("WARNING image failed: %s" % e)

    required = [k.strip() for k in a.require.split(",") if k.strip()]
    missing = [k for k in required if k not in results]
    if missing:
        raise RuntimeError("required keys missing: %s" % ", ".join(missing))
    if not results:
        raise RuntimeError("no result value extracted (see m3d_summary_all.json)")
    if a.merge and os.path.isfile("results.json"):
        with open("results.json", encoding="utf-8") as f:
            old = json.load(f)
        old.update(results)
        results = old
    write_json("results.json", results)
    log("results.json: %s" % json.dumps(results))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("FAILED: %s" % e)
        sys.exit(1)
