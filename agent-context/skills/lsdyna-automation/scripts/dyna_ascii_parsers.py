# -*- coding: utf-8 -*-
"""
LS-DYNA ASCII 출력 파서 (tool 키: python, Python 3, 표준 라이브러리만 사용)

  glstat  : 전체 에너지 (kinetic/internal/total/hourglass energy, energy ratio, added mass ...)
  matsum  : 파트(재료)별 에너지/운동량
  rcforc  : 접촉력 (master/slave 또는 main/secondary)
  nodout  : 절점 변위/속도/가속도/좌표

ASCII 파일은 *DATABASE_GLSTAT 등 요청 카드의 BINARY 필드가 ASCII 출력(1 또는 3)일 때 생성된다.
MPP 는 기본적으로 binout 에 쓰는 경우가 많다 -> dyna_lasso_extract.py 사용.
TODO(사내검증): 줄 형식은 솔버 버전에 따라 조금씩 다르다. 사내 버전 출력 파일 하나로 반드시 검증한다.

CLI (후처리 단계로 사용 가능, 기존 results.json 이 있으면 합친다):
  python dyna_ascii_parsers.py [--nodes 1001,1002] [--part 3]
"""
import argparse
import json
import math
import os
import re
import sys

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?"
ENUM = re.compile(r"[-+]?\d*\.\d+[EeDd][-+]?\d+|[-+]?\d+\.?\d*[EeDd][-+]?\d+")


def _f(s):
    return float(s.replace("D", "E").replace("d", "e"))


def _key(s):
    return re.sub(r"[^0-9a-z]+", "_", s.strip().lower()).strip("_")


def _open(path):
    return open(path, "r", encoding="latin-1")


# ------------------------------------------------------------------ glstat
def parse_glstat(path="glstat"):
    """-> [{'time': t, 'kinetic_energy': .., ...}, ...]  ('이름.....  값' 줄을 읽음, '=' 은 선택)"""
    recs, cur = [], {}
    pat = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /()%_\-]*?)\s*\.{2,}\s*=?\s*(" + NUM + r")\s*$")
    with _open(path) as f:
        for line in f:
            mo = pat.match(line)
            if not mo:
                continue
            k = _key(mo.group(1))
            if k in cur:
                recs.append(cur)
                cur = {}
            cur[k] = _f(mo.group(2))
    if cur:
        recs.append(cur)
    return recs


# ------------------------------------------------------------------ matsum
def parse_matsum(path="matsum"):
    """-> {mat_id: [{'time': t, 'internal_energy':..., 'kinetic_energy':..., ...}, ...]}
    블록 헤더의 'time' 값과 'mat.#=  n  inten=  x  kinen=  y ...' 형식의 'key= value' 쌍을 읽는다.
    TODO(사내검증): 키 이름(inten, kinen, eroded_ie, x-mom ...)과 'mat.#' 표기는 버전별로 확인."""
    out, t, cur_mat = {}, None, None
    pair = re.compile(r"([A-Za-z][A-Za-z0-9_.#\-]*)\s*=\s*(" + NUM + r")")
    tpat = re.compile(r"\btime\s*=?\s*(" + NUM + r")", re.I)
    with _open(path) as f:
        for line in f:
            if re.search(r"\btime\b", line, re.I) and "mat" not in line.lower():
                mo = tpat.search(line)
                if mo:
                    t = _f(mo.group(1))
                    continue
            pairs = pair.findall(line)
            if not pairs:
                continue
            d = {}
            for k, v in pairs:
                kk = _key(k)
                if kk in ("mat", "mat_", "mat_#") or k.lower().startswith("mat"):
                    cur_mat = int(_f(v))
                    out.setdefault(cur_mat, []).append({"time": t})
                    continue
                d[kk] = _f(v)
            if cur_mat is not None and out.get(cur_mat):
                out[cur_mat][-1].update(d)
    return out


# ------------------------------------------------------------------ rcforc
def parse_rcforc(path="rcforc"):
    """-> {('master', id): [(t, fx, fy, fz), ...], ...}"""
    pat = re.compile(r"^\s*(master|slave|main|secondary)\s+(\d+)\s+time\s+(" + NUM + r")\s+x\s+(" + NUM +
                     r")\s+y\s+(" + NUM + r")\s+z\s+(" + NUM + r")", re.I)
    data = {}
    with _open(path) as f:
        for line in f:
            mo = pat.match(line)
            if mo:
                data.setdefault((mo.group(1).lower(), int(mo.group(2))), []).append(
                    tuple(_f(mo.group(i)) for i in range(3, 7)))
    return data


# ------------------------------------------------------------------ nodout
NODOUT_COLS = ["x_disp", "y_disp", "z_disp", "x_vel", "y_vel", "z_vel",
               "x_accl", "y_accl", "z_accl", "x_coor", "y_coor", "z_coor"]


def parse_nodout(path="nodout", nodes=None):
    """-> {node_id: [(t, {'x_disp':..., ...}), ...]}
    'at time 1.0000E-03' 헤더 뒤 '절점번호 + 12개 값' 줄을 읽는다(병진 블록만).
    값이 붙어 있어도(1.0E-03-2.0E-03) E 표기 정규식으로 분리한다.
    회전 블록(rx_disp ...)은 헤더에 'rot' 가 있으면 건너뛴다. TODO(사내검증)."""
    nodes = set(int(n) for n in nodes) if nodes else None
    out, t, skip = {}, None, False
    tpat = re.compile(r"at\s+time\s*(" + NUM + r")", re.I)
    with _open(path) as f:
        for line in f:
            mo = tpat.search(line)
            if mo:
                t = _f(mo.group(1))
                skip = False
                continue
            low = line.lower()
            if "nodal point" in low:
                skip = "rot" in low
                continue
            if skip or t is None:
                continue
            s = line.strip()
            if not s or not s[0].isdigit():
                continue
            head = re.match(r"^\s*(\d+)", line)
            vals = ENUM.findall(line[head.end():])
            if len(vals) < 3:
                continue
            nid = int(head.group(1))
            if nodes and nid not in nodes:
                continue
            out.setdefault(nid, []).append((t, dict(zip(NODOUT_COLS, (_f(v) for v in vals)))))
    return out


# ------------------------------------------------------------------ 요약 -> results
def summarize(case_dir=".", nodes=None, part=None):
    res = {}
    p = os.path.join(case_dir, "glstat")
    if os.path.exists(p):
        recs = parse_glstat(p)
        if recs:
            last = recs[-1]
            for k in ("time", "kinetic_energy", "internal_energy", "total_energy", "hourglass_energy", "energy_ratio"):
                if k in last:
                    res["final_" + k] = last[k]
            res["max_kinetic_energy"] = max(r.get("kinetic_energy", 0.0) for r in recs)
    p = os.path.join(case_dir, "matsum")
    if os.path.exists(p) and part is not None:
        rows = parse_matsum(p).get(int(part), [])
        if rows:
            for k, v in rows[-1].items():
                if k != "time" and v is not None:
                    res["part%s_%s" % (part, k)] = v
    p = os.path.join(case_dir, "rcforc")
    if os.path.exists(p):
        for (side, cid), rows in sorted(parse_rcforc(p).items()):
            if side in ("master", "main"):
                res["max_contact_force_%d" % cid] = max(math.sqrt(x * x + y * y + z * z) for _, x, y, z in rows)
    p = os.path.join(case_dir, "nodout")
    if os.path.exists(p) and nodes:
        for nid, rows in parse_nodout(p, nodes).items():
            umag = [math.sqrt(d["x_disp"] ** 2 + d["y_disp"] ** 2 + d["z_disp"] ** 2) for _, d in rows]
            res["node%d_max_disp" % nid] = max(umag)
            res["node%d_final_disp" % nid] = umag[-1]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", default="", help="comma separated node ids for nodout")
    ap.add_argument("--part", default=None, help="matsum part/material id")
    a = ap.parse_args()
    nodes = [int(x) for x in a.nodes.split(",") if x.strip()]
    res = summarize(".", nodes, a.part)
    if not res:
        print("[dyna_ascii_parsers] no ASCII results found", flush=True)
        return 1
    path = "results.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            base = json.load(f)
        base.update(res)
        res = base
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict((k, float(v)) for k, v in res.items()), f, indent=2, sort_keys=True)
    print("[dyna_ascii_parsers] results: " + json.dumps(res, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
