# -*- coding: utf-8 -*-
"""Abaqus ODB 결과 전체 → h5in/*.npz — PC Agent가 케이스가 끝나면 case.h5(HDF5 압축)로 묶는다.
Abaqus 파이썬에는 h5py가 없어 npz로 남긴다(docs/cae-data-hdf5.md 'h5in 규약'). py2/py3 겸용(f-string 없음).

실행(케이스 폴더에서):  abaqus python abq_odb_to_npz.py -- [job.odb] [--every 1] [--fields S,U,PEEQ] [--steps Step-1]
  기본은 모든 스텝·모든 프레임·모든 필드 출력(fieldOutputs)과 모든 이력 출력(historyOutputs)
  --every k : k프레임마다 하나(마지막 프레임은 항상 포함)
남기는 파일:
  h5in/mesh__<인스턴스>.npz     points(노드 좌표)·node_ids·elements__<요소형식>·element_ids__<요소형식>
  h5in/<스텝>__<필드>__<인스턴스>__<위치>.npz
                                frames(F, n, C)·time(F,)·labels(n,)(노드 또는 요소 번호)·meta_json(이름·스텝·위치·성분)
  history/<스텝>__<영역>.csv    time + 이력 출력(RF·U·ALLIE 등) 모든 변수
TODO(사내검증): bulkDataBlocks의 성분·위치(INTEGRATION_POINT·ELEMENT_NODAL)별 행 수가 프레임마다 같은지, 큰 ODB의 메모리·시간,
               Abaqus 2023 이하(py2)에서 numpy savez_compressed 동작
"""
from __future__ import print_function

import json
import os
import sys

import numpy as np
from odbAccess import openOdb


def safe(s):
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(s))


def args():
    a = sys.argv[1:]
    if "--" in a:
        a = a[a.index("--") + 1:]
    opt = {"odb": None, "every": 1, "fields": None, "steps": None}
    i = 0
    while i < len(a):
        if a[i] == "--every":
            opt["every"] = max(1, int(a[i + 1]))
            i += 2
        elif a[i] == "--fields":
            opt["fields"] = set(a[i + 1].split(","))
            i += 2
        elif a[i] == "--steps":
            opt["steps"] = set(a[i + 1].split(","))
            i += 2
        else:
            opt["odb"] = a[i]
            i += 1
    if not opt["odb"]:
        odbs = sorted(f for f in os.listdir(".") if f.lower().endswith(".odb"))
        opt["odb"] = odbs[0] if odbs else None
    return opt


def save_mesh(odb, out):
    for name, inst in odb.rootAssembly.instances.items():
        nodes = inst.nodes
        if not len(nodes):
            continue
        arrs = {"points": np.array([n.coordinates for n in nodes], dtype=np.float64),
                "node_ids": np.array([n.label for n in nodes], dtype=np.int64),
                "meta_json": json.dumps({"part": safe(name), "name": "mesh"})}
        index = dict((n.label, i) for i, n in enumerate(nodes))
        by_type = {}
        for e in inst.elements:
            by_type.setdefault(str(e.type), []).append((e.label, [index.get(n, -1) for n in e.connectivity]))
        for et, rows in by_type.items():
            width = max(len(c) for _, c in rows)
            arrs["elements__" + safe(et)] = np.array([c + [-1] * (width - len(c)) for _, c in rows], dtype=np.int64)
            arrs["element_ids__" + safe(et)] = np.array([lab for lab, _ in rows], dtype=np.int64)
        np.savez_compressed(os.path.join(out, "mesh__%s.npz" % safe(name)), **arrs)


def save_fields(odb, out, opt):
    n_files = 0
    for sname, step in odb.steps.items():
        if opt["steps"] and sname not in opt["steps"]:
            continue
        frames = list(step.frames)
        if not frames:
            continue
        idx = list(range(0, len(frames), opt["every"]))
        if idx[-1] != len(frames) - 1:
            idx.append(len(frames) - 1)
        keys = list(frames[-1].fieldOutputs.keys())
        for fname in keys:
            if opt["fields"] and fname not in opt["fields"]:
                continue
            acc = {}  # (인스턴스, 위치) → {"frames": [...], "labels": ..., "time": [...], "comps": ...}
            for i in idx:
                fr = frames[i]
                if fname not in fr.fieldOutputs.keys():
                    continue
                fo = fr.fieldOutputs[fname]
                per = {}
                for b in fo.bulkDataBlocks:
                    inst = b.instance.name if b.instance is not None else "ASSEMBLY"
                    key = (inst, str(b.position))
                    labels = b.nodeLabels if b.nodeLabels is not None and len(b.nodeLabels) else b.elementLabels
                    d = np.asarray(b.data, dtype=np.float64)
                    d = d.reshape(len(d), -1)
                    per.setdefault(key, []).append((np.asarray(labels), d))
                for key, parts in per.items():
                    labels = np.concatenate([p[0] for p in parts])
                    data = np.concatenate([p[1] for p in parts])
                    a = acc.setdefault(key, {"frames": [], "labels": labels, "time": [], "comps": list(fo.componentLabels)})
                    if data.shape[0] != len(a["labels"]):
                        continue  # 행 수가 바뀐 프레임(요소 삭제 등)은 건너뛴다 — TODO(사내검증)
                    a["frames"].append(data)
                    a["time"].append(fr.frameValue)
            for (inst, pos), a in acc.items():
                if not a["frames"]:
                    continue
                meta = {"name": fname, "step": sname, "part": safe(inst), "location": pos.lower(),
                        "components": a["comps"] or [fname]}
                np.savez_compressed(os.path.join(out, "%s__%s__%s__%s.npz" % (safe(sname), safe(fname), safe(inst), safe(pos))),
                                    frames=np.stack(a["frames"]), time=np.array(a["time"], dtype=np.float64),
                                    labels=a["labels"], meta_json=json.dumps(meta))
                n_files += 1
    return n_files


def save_history(odb, opt):
    if not os.path.isdir("history"):
        os.makedirs("history")
    n = 0
    for sname, step in odb.steps.items():
        if opt["steps"] and sname not in opt["steps"]:
            continue
        for rname, reg in step.historyRegions.items():
            outs = reg.historyOutputs
            if not len(outs):
                continue
            names = list(outs.keys())
            t = [p[0] for p in outs[names[0]].data]
            cols = [np.array([p[1] for p in outs[k].data], dtype=np.float64) for k in names if len(outs[k].data) == len(t)]
            names = [k for k in names if len(outs[k].data) == len(t)]
            with open(os.path.join("history", "%s__%s.csv" % (safe(sname), safe(rname))), "w") as f:
                f.write(",".join(["time"] + names) + "\n")
                for i in range(len(t)):
                    f.write(",".join(["%.9g" % t[i]] + ["%.9g" % c[i] for c in cols]) + "\n")
            n += 1
    return n


def main():
    opt = args()
    if not opt["odb"]:
        raise SystemExit("ODB 파일이 없습니다")
    out = "h5in"
    if not os.path.isdir(out):
        os.makedirs(out)
    odb = openOdb(opt["odb"], readOnly=True)
    try:
        save_mesh(odb, out)
        nf = save_fields(odb, out, opt)
        nh = save_history(odb, opt)
    finally:
        odb.close()
    print("h5in: field files %d, history regions %d (PC Agent packs them into case.h5)" % (nf, nh))


if __name__ == "__main__":
    main()
