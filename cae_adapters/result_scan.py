"""이미 해석을 끝낸 폴더에서 케이스를 찾는다(dds-study.scan_results). 읽기만 하고 원본에는 아무것도 쓰지 않는다.

결과 폴더는 수 GB일 수 있고 네트워크 공유(UNC)에 있기도 해서 파일을 열어 보는 일은 작은 요약 파일로 한정한다.
폴더 수와 시간에 한계를 두고, 넘으면 잘렸다고 알린다(조용히 일부만 돌려주면 케이스를 잃은 줄 모른다).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .tabular import TableEncrypted, TableError, analyze, cell_state, read_table

MAX_DEPTH = 8
MAX_DIRS = 5000
TIME_LIMIT_SEC = 30.0
SMALL_FILE = 5 * 1024 * 1024  # 이보다 큰 요약 파일은 열지 않는다
TABLE_FILE = 20 * 1024 * 1024
MAX_TABLES = 10
SHOWN_CASES = 50

# 결과 파일 → 그 결과에서 값을 뽑는 스킬·스크립트. tool은 dds-cae.run_script의 tool 키. script가 None이면 스킬을 읽고 만든다.
RESULT_KINDS: dict[str, dict[str, Any]] = {
    ".odb": {"kind": "Abaqus", "skill": "abaqus-automation", "script": "abq_odb_extract.py", "tool": "abaqus_python", "field_script": "abq_odb_to_npz.py"},
    "d3plot": {"kind": "LS-DYNA", "skill": "lsdyna-automation", "script": "dyna_lasso_extract.py", "tool": "python", "field_script": "dyna_to_h5.py"},
    "binout": {"kind": "LS-DYNA", "skill": "lsdyna-automation", "script": "dyna_lasso_extract.py", "tool": "python", "field_script": "dyna_to_h5.py"},
    ".op2": {"kind": "Nastran", "skill": "msc-nastran-automation", "script": "nastran_op2_extract.py", "tool": "python", "field_script": None},
    ".rst": {"kind": "Ansys", "skill": "ansys-automation", "script": "dpf_extract_rst.py", "tool": "ansys_python", "field_script": None},
    ".res": {"kind": "Adams", "skill": "adams-automation", "script": "adams_extract.py", "tool": "python", "field_script": None},
    ".srp": {"kind": "Fluent", "skill": "fluent-automation", "script": "fluent_extract.py", "tool": "python", "field_script": None},
    ".h3d": {"kind": "Altair", "skill": "altair-automation", "script": None, "tool": None, "field_script": None},
}
_SKIP_DIRS = {"__pycache__", "node_modules", "images", "history", "h5in", "fields", "$recycle.bin", "system volume information"}
_RESULT_CSV = {"results.csv", "result.csv", "summary.csv", "outputs.csv", "output.csv"}
_PARAM_CSV = {"params.csv", "param.csv", "inputs.csv", "input.csv"}
_TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xlsm"}
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_NAME_PARAM = re.compile(rf"(?:^|[_\s,;-])([^\W\d_][^\W_]*(?:_[^\W\d_][^\W_]*)*)=({_NUM})(?=$|[_\s,;])")


def name_params(label: str) -> dict[str, float]:
    """폴더 이름의 변수: t=3.0_w=30 → {"t": 3.0, "w": 30.0}. t=1e999처럼 무한대가 되는 값은 쓰지 않는다."""
    out: dict[str, float] = {}
    for m in _NAME_PARAM.finditer(label):
        num, state = cell_state(float(m.group(2)))
        if state == "ok":
            out[m.group(1)] = num
    return out


def _numbers(obj: Any) -> tuple[dict[str, float], list[str]]:
    """숫자 값만 남긴다. 평탄한 키만 쓴다는 규약(docs/skill-contract.md)을 따르고 뺀 키는 알려 준다."""
    out: dict[str, float] = {}
    skipped: list[str] = []
    if not isinstance(obj, dict):
        return out, skipped
    for k, v in obj.items():
        num, state = cell_state(v)
        if state == "ok":
            out[str(k)] = num
        elif state != "missing":
            skipped.append(str(k))
    return out, skipped


def _decode_json(data: bytes) -> str:
    """한글 Windows 스크립트는 CP949, PowerShell 5.1 Out-File은 UTF-16으로 쓴다. 표와 같은 범위를 읽는다."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp949")


def _read_json(path: Path) -> Any:
    """못 읽으면 None. 파일 하나가 깨졌다고 폴더 전체 스캔이 멈추면 안 된다(깊게 중첩된 JSON은 RecursionError)."""
    try:
        if path.stat().st_size > SMALL_FILE:
            return None
        return json.loads(_decode_json(path.read_bytes()))
    except (OSError, ValueError, RecursionError):
        return None


_FAILED_STATUS = {"failed", "fail", "failure", "error", "errored", "diverged", "aborted", "cancelled", "canceled", "killed", "timeout", "실패", "오류", "발산", "중단"}


def _failed_reason(*docs: Any) -> str:
    """해석 실패 표시를 찾는다. 실패한 케이스의 값(발산한 1e6, 0.0)을 학습에 넣으면 모델이 조용히 망가진다."""
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        status = str(doc.get("status") or "").strip().lower()
        err = doc.get("error") if isinstance(doc.get("error"), str) else ""
        if status in _FAILED_STATUS:
            return (err or status)[:200]
        for key in ("converged", "success", "ok", "completed"):
            if doc.get(key) is False:
                return f"{key}=false"
        if err.strip() and status not in ("complete", "completed", "ok", "success", "done"):
            return err[:200]
    return ""


def _one_row_csv(path: Path, encrypted: list[str]) -> dict[str, float]:
    """케이스 폴더의 요약 CSV: 1행짜리 표 또는 이름·값 두 열 표. 암호화되어 못 읽으면 encrypted에 적는다('값 없음'과 할 일이 다르다)."""
    try:
        if path.stat().st_size > SMALL_FILE:
            return {}
        table = read_table(path)
    except TableEncrypted:
        encrypted.append(path.name)
        return {}
    except (OSError, TableError):
        return {}
    records = table.records(numeric=True)
    if len(records) == 1:
        return _numbers({k: v for k, v in records[0].items() if k != "_row"})[0]
    if len(table.names) == 2:
        a, b = table.names
        return _numbers({str(r[a]): r[b] for r in records if r.get(a) is not None})[0]
    return {}


def result_kind(filename: str) -> str | None:
    low = filename.lower()
    if low.startswith("d3plot"):
        return "d3plot"
    if low.startswith("binout"):
        return "binout"
    ext = Path(low).suffix
    return ext if ext in RESULT_KINDS else None


def _inspect_dir(path: Path, label: str, files: list[str]) -> dict[str, Any] | None:
    """케이스 폴더로 보이면 내용을 돌려준다. 어떤 표시도 없으면 None."""
    lower = {f.lower(): f for f in files}
    result_files = [f for f in files if result_kind(f)]
    from_name = name_params(path.name)
    fields_dir = path / "fields"
    fields = sorted(p.stem for p in fields_dir.glob("*.npz")) if fields_dir.is_dir() else []
    markers = {"results.json", "params.json", "case.json"} & lower.keys()
    if not (markers or result_files or from_name or fields):
        return None
    params: dict[str, float] = {}
    results: dict[str, float] = {}
    params_from = results_from = ""
    skipped: list[str] = []
    encrypted: list[str] = []
    case_json = _read_json(path / lower["case.json"]) if "case.json" in lower else None
    results_doc = _read_json(path / lower["results.json"]) if "results.json" in lower else None
    unreadable = [n for n in ("params.json", "results.json", "case.json") if n in lower and _read_json(path / lower[n]) is None]
    if "params.json" in lower:
        params, s = _numbers(_read_json(path / lower["params.json"]))
        params_from = "params.json" if params else ""
        skipped += s
    if not params and isinstance(case_json, dict):
        params, s = _numbers(case_json.get("params"))
        params_from = "case.json" if params else ""
        skipped += s
    if not params:
        for n in _PARAM_CSV & lower.keys():
            params = _one_row_csv(path / lower[n], encrypted)
            params_from = lower[n] if params else ""
            if params:
                break
    if not params and from_name:
        params, params_from = from_name, "폴더 이름"
    if "results.json" in lower:
        results, s = _numbers(results_doc)
        results_from = "results.json" if results else ""
        skipped += s
    if not results and isinstance(case_json, dict):
        results, s = _numbers(case_json.get("results"))
        results_from = "case.json" if results else ""
        skipped += s
    if not results:
        for n in _RESULT_CSV & lower.keys():
            results = _one_row_csv(path / lower[n], encrypted)
            results_from = lower[n] if results else ""
            if results:
                break
    failed = _failed_reason(results_doc, case_json)
    case: dict[str, Any] = {"folder": str(path), "label": label, "params": params, "results": results, "params_from": params_from,
                            "results_from": results_from, "result_files": result_files[:5], "fields": fields,
                            "ready": bool(params and (results or fields)) and not failed}
    if skipped:
        case["skipped_keys"] = sorted(set(skipped))[:10]
    if failed:
        case["failed"] = failed
        case["missing"] = f"해석 실패 표시가 있습니다({failed})"
    elif not case["ready"]:
        missing = ([] if params else ["입력값"]) + ([] if results or fields else ["결과값"])
        case["missing"] = " · ".join(missing)
        if unreadable:
            case["missing"] += f" (읽지 못한 파일: {', '.join(unreadable)})"
        if encrypted:
            case["encrypted"] = True
            case["missing"] = f"{', '.join(sorted(set(encrypted)))}이(가) 보안 프로그램으로 암호화되어 값을 읽지 못했습니다 → 입력·출력을 엑셀에서 복사해 표로 붙여 넣으세요"
    return case


def _table_summary(path: Path, root: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(path), "label": str(path.relative_to(root)) if path != root else path.name}
    try:
        if path.stat().st_size > TABLE_FILE:
            info["note"] = "큰 표입니다. inspect_table로 따로 보세요"
            return info
        a = analyze(read_table(path))
    except (OSError, TableError) as e:
        info["note"] = str(e)
        return info
    info.update({"rows": a["n_rows"], "inputs": a["inputs"][:30], "outputs": a["outputs"][:30], "fixed": a["fixed"][:30]})
    return info


def scan_folder(folder: str | Path, max_depth: int = 3, time_limit: float = TIME_LIMIT_SEC, max_dirs: int = MAX_DIRS) -> dict[str, Any]:
    """폴더를 훑어 케이스를 찾는다. 케이스 폴더 안은 더 내려가지 않는다(한 케이스의 하위 폴더를 다른 케이스로 세지 않게)."""
    root = Path(folder).expanduser()
    if not root.exists():
        raise ValueError(f"폴더를 찾지 못했습니다: {root} → 경로와 접근 권한을 확인하세요. 네트워크 폴더면 이 PC에서 열리는지 확인하세요")
    if not root.is_dir():
        raise ValueError(f"폴더가 아니라 파일입니다: {root} → 표면 inspect_table을 쓰고, 해석 폴더면 그 폴더 경로를 주세요")
    depth_limit = max(0, min(int(max_depth), MAX_DEPTH))
    t0 = time.monotonic()
    cases: list[dict[str, Any]] = []
    ext_counts: dict[str, int] = {}
    tables: list[Path] = []
    visited, n_files = 0, 0
    truncated = ""
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        if visited >= max_dirs:
            truncated = f"폴더가 {max_dirs}개를 넘어 앞부분만 봤습니다"
            break
        if time.monotonic() - t0 > time_limit:
            truncated = f"{time_limit:.0f}초 안에 다 보지 못해 앞부분만 봤습니다"
            break
        path, depth = stack.pop()
        visited += 1
        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        files, dirs = [], []
        for e in entries:
            try:
                (dirs if e.is_dir(follow_symlinks=False) else files).append(e.name)
            except OSError:
                continue
        n_files += len(files)
        for f in files:
            key = result_kind(f) or Path(f).suffix.lower() or "(확장자 없음)"
            ext_counts[key] = ext_counts.get(key, 0) + 1
        label = "." if path == root else str(path.relative_to(root))
        case = _inspect_dir(path, label, files)
        if case is not None:
            cases.append(case)
        if case is None or path == root:  # 시작 폴더 자체가 케이스처럼 보여도 그 아래에 케이스가 있을 수 있다
            tables += [path / f for f in files if Path(f).suffix.lower() in _TABLE_EXT]
            if depth < depth_limit:
                for d in reversed(dirs):
                    if d.lower() not in _SKIP_DIRS and not d.startswith("."):
                        stack.append((path / d, depth + 1))
    cases.sort(key=lambda c: c["label"])
    ready = [c for c in cases if c["ready"]]
    failed_cases = [c for c in cases if c.get("failed")]
    kinds: dict[str, dict[str, Any]] = {}
    for c in cases:
        if c["results"] or c.get("failed"):
            continue  # 값이 이미 있는 케이스에는 추출이 필요 없다. 실패한 케이스에서는 뽑을 값이 없다
        for f in c["result_files"]:
            spec = RESULT_KINDS[result_kind(f)]
            entry = kinds.setdefault(spec["kind"], {**spec, "cases": 0, "example": str(Path(c["folder"]) / f)})
            entry["cases"] += 1
            break
    return {"folder": str(root), "visited_dirs": visited, "files": n_files, "cases_total": len(cases), "ready": len(ready),
            "not_ready": len(cases) - len(ready), "failed": len(failed_cases), "encrypted": sum(bool(c.get("encrypted")) for c in cases), "file_kinds": dict(sorted(ext_counts.items(), key=lambda kv: -kv[1])[:20]),
            "inputs": sorted({k for c in cases for k in c["params"]}), "outputs": sorted({k for c in cases for k in c["results"]}),
            "fields": sorted({k for c in cases for k in c["fields"]}), "extract": list(kinds.values()),
            "tables": [_table_summary(p, root) for p in tables[:MAX_TABLES]], "truncated": truncated, "cases": cases}


def brief(scan: dict[str, Any], shown: int = SHOWN_CASES) -> dict[str, Any]:
    """AI에게 돌려줄 요약. 케이스가 많아도 대화가 커지지 않게 앞부분만 준다."""
    out = {k: v for k, v in scan.items() if k != "cases"}
    out["cases"] = [{k: v for k, v in c.items() if k != "folder"} for c in scan["cases"][:shown]]
    out["cases_shown"] = len(out["cases"])
    return out
