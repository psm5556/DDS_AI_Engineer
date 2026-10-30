"""CAE 해석 데이터 HDF5 저장소(case.h5) — 케이스 하나의 결과를 빠짐없이, 압축해서 한 파일에 담는다.

구조(스키마 dds-cae-h5 v1, docs/cae-data-hdf5.md):
  /                         attrs: format, schema, created, writer, case_id, study_id, project_uid, tool
  /meta                     attrs: params.*, results.*  · case_json(문자열)
  /mesh/<part>/nodes        (N,3) 좌표 · node_ids (N,) · elements/<형식> (M,k) · element_ids/<형식> (M,) · surface (T,3)
  /steps/<step>/time        (F,) 시각(또는 모드·하중 번호)
  /steps/<step>/fields/<이름>  (F, N, C) — 프레임마다 한 줄. attrs: location(node|element|ip), part, components, unit, labels
  /history/<이름>           (T, K) 시간 이력 · attrs: columns, unit
  /files/<경로>             작은 텍스트(로그·입력 덱·json)의 원문 바이트
  /manifest                 JSON: 케이스 폴더의 모든 파일(packed | raw | skipped), 크기·sha256, 검증 결과

압축: gzip(4) + shuffle, 청크는 프레임 하나. 기본은 원래 자료형 그대로(손실 없음). float32=True면 실수를 4바이트로 줄인다.
툴 내장 파이썬(Abaqus 등)은 h5py가 없을 수 있으므로, 케이스 스크립트는 npz·csv로 남기고 PC Agent가 pack_case로 묶는다.
플랫폼 파이썬에서 도는 추출 스크립트(LS-DYNA·Nastran 등)는 CaseH5로 case.h5에 바로 써도 된다(pack_case가 이어서 채운다).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

FORMAT, SCHEMA = "dds-cae-h5", 1
H5_NAME = "case.h5"
# 원문을 함께 담는 작은 텍스트(로그·입력 덱·설정). 그 밖의 큰 바이너리(odb·d3plot·op2·rst 등)는 PC에 원본으로 남기고 목록에만 적는다
TEXT_EXT = {".json", ".log", ".txt", ".csv", ".inp", ".k", ".key", ".dyn", ".bdf", ".dat", ".nas", ".py", ".jou", ".java",
            ".msg", ".sta", ".out", ".f06", ".cfg", ".ini", ".xml", ".yaml", ".yml", ".tcl", ".mac", ".apdl", ".cmd", ".bat", ".sh"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _key(name: str) -> str:
    """HDF5 경로에 쓸 수 있는 이름(/ 금지)."""
    return str(name).replace("/", "_").replace("\\", "_").strip() or "_"


class CaseH5:
    """case.h5 쓰기·읽기. with CaseH5(path, "a") as h: h.add_field("stress", values, step="Step-1", time=0.1)"""

    def __init__(self, path: str | Path, mode: str = "a", compression: str = "gzip", level: int = 4, float32: bool = False):
        import h5py

        self.path = Path(path)
        self.f = h5py.File(self.path, mode)
        self.comp = {"compression": compression, "compression_opts": level} if compression == "gzip" else (
            {"compression": compression} if compression else {})
        self.float32 = float32
        if mode != "r" and "format" not in self.f.attrs:
            self.f.attrs.update({"format": FORMAT, "schema": SCHEMA, "created": _now()})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.f.close()

    # ---------- 쓰기 ----------
    def _data(self, a) -> np.ndarray:
        a = np.asarray(a)
        if self.float32 and a.dtype == np.float64:
            return a.astype(np.float32)
        return a

    def _put(self, path: str, data, chunks=None, **attrs):
        if path in self.f:
            del self.f[path]
        data = self._data(data)
        opts = dict(self.comp, shuffle=bool(self.comp)) if data.size > 16 and data.dtype.kind in "fiub" else {}
        ds = self.f.create_dataset(path, data=data, chunks=chunks if opts else None, **opts)
        ds.attrs.update({k: v for k, v in attrs.items() if v is not None})
        return ds

    def set_meta(self, **attrs):
        self.f.attrs.update({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                             for k, v in attrs.items() if v is not None})

    def set_params(self, params: dict):
        g = self.f.require_group("meta")
        g.attrs.update({f"params.{k}": v for k, v in params.items() if isinstance(v, (int, float, str))})

    def set_results(self, results: dict):
        g = self.f.require_group("meta")
        for k in [k for k in g.attrs if k.startswith("results.")]:
            del g.attrs[k]
        g.attrs.update({f"results.{k}": v for k, v in results.items() if isinstance(v, (int, float))})

    def set_text(self, path: str, text: str):
        if path in self.f:
            del self.f[path]
        self.f.create_dataset(path, data=text, dtype=_str_dtype())

    def add_mesh(self, part: str, nodes, node_ids=None, elements: dict | None = None, element_ids: dict | None = None,
                 surface=None):
        base = f"mesh/{_key(part)}"
        self._put(f"{base}/nodes", nodes)
        if node_ids is not None:
            self._put(f"{base}/node_ids", node_ids)
        for et, conn in (elements or {}).items():
            self._put(f"{base}/elements/{_key(et)}", conn)
        for et, ids in (element_ids or {}).items():
            self._put(f"{base}/element_ids/{_key(et)}", ids)
        if surface is not None:
            self._put(f"{base}/surface", surface)

    def add_field(self, name: str, frames, step: str = "step-1", time=None, location: str = "node", part: str = "main",
                  components: list[str] | None = None, unit: str = "", labels=None, **attrs):
        """frames: (F, N, C) · (N, C) · (N,) — 한 프레임이면 F=1로 둔다. time: (F,) 프레임 시각/번호."""
        a = np.asarray(frames)
        if a.ndim == 1:
            a = a[None, :, None]
        elif a.ndim == 2:
            a = a[None]
        base = f"steps/{_key(step)}"
        chunks = (1,) + a.shape[1:] if a.ndim >= 2 and a[0].size else None
        self._put(f"{base}/fields/{_key(name)}", a, chunks=chunks, location=location, part=_key(part),
                  components=json.dumps(components or [], ensure_ascii=False), unit=unit, **attrs)
        if labels is not None:
            self._put(f"{base}/labels/{_key(name)}", labels)
        if time is not None or f"{base}/time" not in self.f:
            t = np.atleast_1d(np.asarray(time if time is not None else np.arange(a.shape[0]), dtype=np.float64))
            if f"{base}/time" not in self.f or len(t) >= len(self.f[f"{base}/time"]):
                self._put(f"{base}/time", t)

    def add_history(self, name: str, data, columns: list[str] | None = None, unit: str = ""):
        self._put(f"history/{_key(name)}", np.asarray(data), columns=json.dumps(columns or [], ensure_ascii=False), unit=unit)

    def add_file(self, rel: str, data: bytes):
        path = "files/" + "/".join(_key(p) for p in Path(rel).parts)
        if path in self.f:
            del self.f[path]
        self.f.create_dataset(path, data=np.frombuffer(data, dtype=np.uint8), **(dict(self.comp) if len(data) > 64 else {}))

    def write_manifest(self, manifest: dict):
        self.set_text("manifest", json.dumps(manifest, ensure_ascii=False))

    # ---------- 읽기 ----------
    def manifest(self) -> dict:
        return json.loads(_text(self.f["manifest"][()])) if "manifest" in self.f else {}

    def fields(self) -> list[dict]:
        out = []
        for step, g in (self.f.get("steps") or {}).items():
            for name, ds in (g.get("fields") or {}).items():
                out.append({"step": step, "name": name, "shape": list(ds.shape), "dtype": str(ds.dtype),
                            "location": _text(ds.attrs.get("location", "")), "part": _text(ds.attrs.get("part", "")),
                            "unit": _text(ds.attrs.get("unit", "")), "frames": int(ds.shape[0])})
        return out


def _str_dtype():
    import h5py
    return h5py.string_dtype("utf-8")


def _text(v) -> str:
    return v.decode("utf-8") if isinstance(v, bytes) else str(v)


# ---------- 요약 · 스냅샷 ----------
def summary(path: str | Path) -> dict:
    """case.h5에 무엇이 들었는지(화면·MCP용): 메타·메쉬·필드·이력·파일·원본 목록·검증 결과."""
    p = Path(path)
    with CaseH5(p, "r") as h:
        f = h.f
        meta = f.get("meta")
        man = h.manifest()
        parts = [{"part": k, "nodes": int(g["nodes"].shape[0]) if "nodes" in g else 0,
                  "elements": int(sum(ds.shape[0] for ds in (g.get("elements") or {}).values()))}
                 for k, g in (f.get("mesh") or {}).items()]
        hist = [{"name": k, "shape": list(ds.shape), "columns": json.loads(_text(ds.attrs.get("columns", "[]")))}
                for k, ds in (f.get("history") or {}).items()]
        files = []
        f.get("files") and f["files"].visititems(lambda n, o: files.append(n) if hasattr(o, "shape") else None)
        return {
            "format": _text(f.attrs.get("format", "")), "schema": int(f.attrs.get("schema", 0)), "size": p.stat().st_size,
            "params": {k[7:]: _plain(v) for k, v in (meta.attrs.items() if meta is not None else []) if k.startswith("params.")},
            "results": {k[8:]: _plain(v) for k, v in (meta.attrs.items() if meta is not None else []) if k.startswith("results.")},
            "mesh": parts, "fields": h.fields(), "history": hist, "files": files,
            "raw": [x for x in man.get("files", []) if x.get("status") == "raw"],
            "checks": man.get("checks", {}), "packed_at": man.get("packed_at"),
        }


def archive_info(s: dict) -> dict:
    """summary → 케이스에 붙여 둘 짧은 정보(서버 DB·화면): 무엇이 몇 개 담겼고 원본이 얼마나 PC에 남았는지."""
    return {"ok": bool(s["checks"].get("ok")), "size": s["size"], "packed_at": s.get("packed_at"),
            "field_names": sorted({x["name"] for x in s["fields"] if x["name"] != "coordinates"}),
            "fields": len(s["fields"]), "frames": int(sum(x["frames"] for x in s["fields"])),
            "history": len(s["history"]), "files": len(s["files"]),
            "raw": len(s["raw"]), "raw_size": int(sum(x["size"] for x in s["raw"]))}


def _plain(v):
    return v.item() if hasattr(v, "item") else _text(v) if isinstance(v, bytes) else v


def read_snapshot(path: str | Path, field: str, step: str | None = None, frame: int = -1) -> dict:
    """형상·필드 학습용 스냅샷 {points, values, triangles} — fields/*.npz와 같은 모양. 좌표는 그 필드의 part 메쉬,
    같은 step에 'coordinates' 필드(프레임별 변형 좌표)가 있으면 그 프레임 좌표를 쓴다."""
    with CaseH5(path, "r") as h:
        f = h.f
        steps = [step] if step else list(f.get("steps") or {})
        for st in reversed(steps):
            g = f.get(f"steps/{st}/fields")
            if g is None or field not in g:
                continue
            ds = g[field]
            vals = np.asarray(ds[frame])
            vals = vals[:, 0] if vals.ndim == 2 and vals.shape[1] == 1 else vals
            part = _text(ds.attrs.get("part", "main"))
            mesh = f.get(f"mesh/{part}")
            if mesh is None or "nodes" not in mesh:
                raise ValueError(f"'{field}'의 메쉬(mesh/{part})가 없습니다")
            pts = np.asarray(mesh["nodes"][()], dtype=np.float64)
            if "coordinates" in g and _text(g["coordinates"].attrs.get("part", "")) == part:
                pts = np.asarray(g["coordinates"][frame], dtype=np.float64)
            out = {"points": pts, "values": np.asarray(vals, dtype=np.float64)}
            if "surface" in mesh:
                out["triangles"] = np.asarray(mesh["surface"][()], dtype=np.int32)
            return out
    raise KeyError(f"case.h5에 '{field}' 필드가 없습니다")


# ---------- 케이스 폴더 → case.h5 ----------
def _sha256(p: Path, limit: int) -> str | None:
    if p.stat().st_size > limit:
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _npz_field(h: CaseH5, name: str, z, step_default: str):
    """fields/<이름>.npz(기존 규약: points, values, triangles) 또는 확장 키(frames, time, step, location, unit, components, labels,
    node_ids, coordinates, elements__<형식>, element_ids__<형식>, meta_json)를 case.h5에 넣는다.
    part는 meta_json의 part, 없으면 이름(필드마다 자기 표면 메쉬를 가질 수 있다). 값 없이 메쉬만 든 npz도 된다."""
    meta = json.loads(str(z["meta_json"])) if "meta_json" in z else {}
    part = meta.get("part") or name
    step = str(z["step"]) if "step" in z else meta.get("step", step_default)
    if "points" in z:
        h.add_mesh(part, z["points"], node_ids=z["node_ids"] if "node_ids" in z else None,
                   elements={k[10:]: z[k] for k in z.files if k.startswith("elements__")} or None,
                   element_ids={k[13:]: z[k] for k in z.files if k.startswith("element_ids__")} or None,
                   surface=z["triangles"] if "triangles" in z else None)
    data = z["frames"] if "frames" in z else z["values"] if "values" in z else z["data"] if "data" in z else None
    if data is None:
        return None
    h.add_field(meta.get("name", name), data, step=step, time=z["time"] if "time" in z else None,
                location=str(z["location"]) if "location" in z else meta.get("location", "node"), part=part,
                components=meta.get("components") or ([str(c) for c in z["components"]] if "components" in z else None),
                unit=str(z["unit"]) if "unit" in z else meta.get("unit", ""), labels=z["labels"] if "labels" in z else None)
    if "coordinates" in z:  # 프레임별 변형 좌표 (F, N, 3)
        h.add_field("coordinates", z["coordinates"], step=step, location="node", part=part, unit=meta.get("length_unit", ""))
    return {"name": meta.get("name", name), "step": step, "part": part}


def pack_case(run_dir: str | Path, out: str | Path | None = None, meta: dict | None = None, text_max_mb: float = 5,
              hash_max_mb: float = 512, compression: str = "gzip", level: int = 4, float32: bool = False) -> dict:
    """케이스 폴더를 case.h5로 묶는다(이미 있으면 이어서 채운다 — 추출 스크립트가 직접 쓴 내용은 그대로 둔다).
    - params.json · results.json · case.json → /meta
    - fields/*.npz, h5in/*.npz → /mesh · /steps/*/fields   ·   history/*.csv → /history
    - 작은 텍스트(로그·입력 덱) → /files   ·   그 밖의 파일(큰 바이너리 결과)은 원본을 PC에 두고 manifest에 크기·sha256을 적는다
    그다음 다시 열어 모든 데이터를 읽어 보며 검증한다. 돌려주는 값: 요약(summary)."""
    run_dir = Path(run_dir)
    out = Path(out) if out else run_dir / H5_NAME
    files: list[dict] = []
    packed_fields: list[dict] = []
    with CaseH5(out, "a", compression=compression, level=level, float32=float32) as h:
        h.set_meta(**(meta or {}), packed_at=_now())
        for name, fn in (("params.json", h.set_params), ("results.json", h.set_results)):
            p = run_dir / name
            if p.is_file():
                try:
                    fn(json.loads(p.read_text(encoding="utf-8")))
                except ValueError:
                    pass
        if (run_dir / "case.json").is_file():
            h.set_text("meta/case_json", (run_dir / "case.json").read_text(encoding="utf-8"))
        for sub, step in (("fields", "final"), ("h5in", "step-1")):
            for p in sorted((run_dir / sub).glob("*.npz")) if (run_dir / sub).is_dir() else []:
                with np.load(p, allow_pickle=False) as z:
                    info = _npz_field(h, p.stem, z, step)
                if info:
                    packed_fields.append({**info, "source": p.relative_to(run_dir).as_posix()})
        for p in sorted((run_dir / "history").glob("*.csv")) if (run_dir / "history").is_dir() else []:
            lines = p.read_text(encoding="utf-8-sig").splitlines()
            cols = [c.strip() for c in lines[0].split(",")] if lines else []
            try:
                data = np.array([[float(x) for x in ln.split(",")] for ln in lines[1:] if ln.strip()])
            except ValueError:
                data = None
            if data is not None and data.size:
                h.add_history(p.stem, data, columns=cols)
        # 케이스 폴더의 모든 파일을 목록에 남긴다 — 무엇이 h5에 담겼고 무엇이 원본으로 남았는지
        for p in sorted(x for x in run_dir.rglob("*") if x.is_file()):
            rel = p.relative_to(run_dir).as_posix()
            size = p.stat().st_size
            if p.resolve() == out.resolve() or p.suffix in (".tmp", ".lck"):
                files.append({"path": rel, "size": size, "status": "skipped"})
                continue
            item = {"path": rel, "size": size, "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            top = rel.split("/", 1)[0]
            if top in ("fields", "h5in", "history") and p.suffix in (".npz", ".csv"):
                item["status"] = "packed"
            elif p.suffix.lower() in TEXT_EXT and size <= text_max_mb * 1e6:
                h.add_file(rel, p.read_bytes())
                item["status"] = "packed"
            elif p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg") and size <= text_max_mb * 1e6:
                h.add_file(rel, p.read_bytes())
                item["status"] = "packed"
            else:
                item.update(status="raw", sha256=_sha256(p, int(hash_max_mb * 1e6)))
            files.append(item)
        h.write_manifest({"schema": SCHEMA, "packed_at": _now(), "run_dir": str(run_dir), "files": files,
                          "fields": packed_fields, "checks": {}})
    checks = verify(out)
    with CaseH5(out, "a") as h:
        man = h.manifest()
        man["checks"] = checks
        h.write_manifest(man)
    return summary(out)


def verify(path: str | Path) -> dict:
    """모든 데이터셋을 끝까지 읽어 본다(압축·청크가 깨지지 않았는지). 큰 필드는 첫·마지막 프레임만."""
    n, bad = 0, []
    with CaseH5(path, "r") as h:
        def visit(name, obj):
            nonlocal n
            if not hasattr(obj, "shape"):
                return
            try:
                if obj.ndim and obj.shape[0] > 1 and obj.size > 5_000_000:
                    obj[0], obj[-1]
                else:
                    obj[()]
                n += 1
            except Exception as e:  # noqa: BLE001 — 어떤 데이터가 깨졌는지 목록으로 남긴다
                bad.append(f"{name}: {e}")
        h.f.visititems(visit)
    return {"ok": not bad, "datasets": n, "errors": bad[:20], "checked_at": _now()}


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = ["CaseH5", "pack_case", "summary", "archive_info", "read_snapshot", "verify", "file_sha256", "H5_NAME", "FORMAT", "SCHEMA"]
