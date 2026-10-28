"""참고 구현(발췌): dds-live MCP의 NX 빠른 툴 — 집 저장소 커밋 bdf3181의 mcp_servers/dds_live.py에서 뽑았다.
그대로 실행되는 파일이 아니다. 사내 dds_live.py 구조에 맞춰 필요한 부분만 옮긴다.
- 빠른 툴(live_set_params·live_view·live_undo·live_info)은 값을 받아 NX 도우미 호출 코드 3~4줄을 만들고,
  기존 live_exec(tool="nx") 경로로 보낸다 → 사내에서 바꾼 NX 실행 방식을 그대로 탄다.
- 여기 쓰인 이름: NX_QUEUE(대기열 폴더), NX_OPS_DIR(ddslive_nx_ops.py가 있는 폴더), MAX_SIDE=1600,
  TOOLS·_call·_capture_window·live_exec·resolve_pid·settings·api 는 원래 dds_live.py에 있던 것.
"""


def _nx_exec(code: str, wait_sec: int) -> dict:
    NX_QUEUE.mkdir(parents=True, exist_ok=True)
    name = f"job_{int(time.time() * 1000)}"
    # 다 쓴 뒤 이름을 바꾼다 — 대기열을 자동으로 읽는 NX가 반쯤 쓴 코드를 실행하지 않도록
    tmp = NX_QUEUE / f"{name}.tmp"
    tmp.write_text(code, encoding="utf-8")
    os.replace(tmp, NX_QUEUE / f"{name}.py")
    res_file = NX_QUEUE / f"{name}.result.json"
    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if res_file.exists():
            try:
                res = json.loads(res_file.read_text(encoding="utf-8"))
            except ValueError:  # NX가 아직 쓰는 중
                time.sleep(0.05)
                continue
            res["wait_ms"] = int((time.time() - t0) * 1000)
            return res
        time.sleep(0.1)  # 1초 간격이면 단계마다 평균 0.5초씩 더 기다린다
    return {"ok": False, "pending": True, "job": name,
            "error": "NX에서 아직 실행하지 않았습니다. 사용자에게 NX의 'DDS 실행' 버튼(dds_live_nx.py)을 눌러 달라고 안내한 뒤 live_result로 확인하세요."}


def _shrink(path: Path, max_side: int = MAX_SIDE) -> None:
    """게시 전에 긴 변을 max_side로 줄인다(업로드·화면 표시가 빨라진다)."""
    from PIL import Image
    with Image.open(path) as img:
        if max(img.size) <= max_side:
            return
        small = img.copy()  # 파일을 닫은 뒤 같은 경로에 저장한다(Windows 파일 잠금)
    small.thumbnail((max_side, max_side), Image.LANCZOS)
    small.save(path)


def _grab(tool: str, path: Path) -> str | None:
    """툴 화면을 path에 저장한다. 플러그인 뷰포트 → (NX) 도우미 뷰포트 → 창 캡처 순서."""
    if TOOLS.get(tool, {}).get("port"):
        try:
            r = _call(tool, "screenshot", path=str(path))
            if r.get("ok") and r.get("path") and Path(r["path"]).exists():
                return r["path"]
        except OSError:
            pass
    elif tool == "nx":
        r = _nx_ops(f"ops.capture({str(path)!r})", 10)
        if r.get("pending") and r.get("job"):  # NX가 바로 실행하지 않으면 창 캡처로 — 늦게 돌 작업은 지운다
            (NX_QUEUE / f"{r['job']}.py").unlink(missing_ok=True)
        if r.get("ok") and path.exists():
            return str(path)
    return _capture_window(TOOLS.get(tool, {}).get("window", tool), path)


def _publish_image(pid: int, tool: str, path: Path, title: str = "", caption: str = "",
                   flow_id: int | None = None, node_id: str | None = None) -> int:
    _shrink(path)
    rel = api.upload_image(pid, path, "live")
    a = api.post(f"/api/projects/{pid}/artifacts", {
        "kind": "image", "title": title or f"{TOOLS.get(tool, {}).get('label', tool)} 화면 {time.strftime('%H:%M:%S')}",
        "payload": {"path": rel, "caption": caption}, "flow_id": flow_id, "node_id": node_id})
    return a["id"]


def _nx_code(call: str) -> str:
    """NX 세션 안 도우미(ddslive_nx_ops)를 불러 call을 실행하는 짧은 코드. 결과(JSON 문자열)는 result에."""
    return (f"import sys\n_d = {str(NX_OPS_DIR)!r}\n"
            "if _d not in sys.path:\n    sys.path.insert(0, _d)\n"
            "import json, ddslive_nx_ops as ops\nops = ops.fresh()\n"
            f"result = {call}\n")


def _parse(res: dict, t0: float | None = None) -> dict:
    """live_exec 응답의 result(JSON 문자열)를 풀어 한 dict로. 대기 중·실행 오류는 그대로 돌려준다."""
    if not res.get("ok") or res.get("pending"):
        return res
    if not res.get("result"):
        return {**res, "ok": False, "error": "도우미 결과가 비어 있습니다. stdout을 확인하세요."}
    try:
        data = json.loads(res["result"])
    except ValueError:
        return res
    if not isinstance(data, dict):
        return res
    out = {**data}
    ms = dict(out.get("ms") or {})
    if res.get("wait_ms") is not None:
        ms["wait"] = res["wait_ms"]
    if t0 is not None:
        ms["round_trip"] = int((time.time() - t0) * 1000)
    out["ms"] = ms
    if res.get("stdout"):
        out["stdout"] = res["stdout"][-2000:]
    return out


def _nx_ops(call: str, timeout_sec: int = 120) -> dict:
    t0 = time.time()
    return _parse(live_exec("nx", _nx_code(call), timeout_sec), t0)


def _check_values(values: dict) -> str | None:
    if not isinstance(values, dict) or not values:
        return "values는 {\"이름\": 숫자} 형식이어야 합니다"
    bad = [k for k, v in values.items() if not isinstance(v, (int, float)) or isinstance(v, bool)]
    return f"숫자가 아닌 값: {', '.join(bad)}" if bad else None


def _with_capture(out: dict, tool: str, shot: Path | None, pid: int | None, title: str, caption: str,
                  flow_id: int | None, node_id: str | None) -> dict:
    """같은 실행에서 남긴 화면(없으면 창 캡처)을 게시한다."""
    if shot is None or not out.get("ok"):
        return out
    got = out.get("image") if out.get("image") and Path(out["image"]).exists() else _grab(tool, shot)
    if got:
        out["image"] = got
        out["artifact_id"] = _publish_image(pid, tool, Path(got), title, caption, flow_id, node_id)
    else:
        out.setdefault("warnings", []).append("화면을 캡처하지 못했습니다")
    return out


@mcp.tool()
def live_info(tool: str, all_expressions: bool = False) -> dict:
    """열려 있는 모델의 요약(파트·재료·스텝·하중·세트 등). 시작할 때 한 번 읽고, 이름은 여기서 가져온다.
    NX: 열린 파트(cad/idealized/fem/sim), 사용자가 이름 붙인 expression(값·단위·상수 여부), 피처, 바디.
    자동 이름(p12 등) expression까지 보려면 all_expressions=true."""
    if tool == "nx":
        return _nx_ops(f"ops.summary(all_expressions={bool(all_expressions)!r})", 60)
    return _call(tool, "info")


@mcp.tool()
def live_set_params(tool: str, values: dict, fem: bool = False, measure: bool = False, capture: bool = False,
                    title: str = "", caption: str = "", flow_id: int | None = None, node_id: str | None = None,
                    project_id: int | None = None, timeout_sec: int = 120) -> dict:
    """설계변수(치수)를 한 번에 바꾼다 — 코드를 쓰지 않아 빠르다. 여러 개는 한 호출에 모은다.
    tool: nx(expression 이름, 업데이트 1번, 실패하면 자동으로 되돌림, Undo 마크 1개) · practice(model 키)
    values: {"이름": 숫자} (이름은 live_info에서) · fem: Simcenter에서 Idealized·FEM(재메쉬)까지 업데이트
    measure: 질량특성(NX) · capture: 바뀐 화면을 같은 호출에서 게시. 결과의 ms로 어디서 시간이 드는지 볼 수 있다."""
    err = _check_values(values)
    if err:
        return {"ok": False, "error": err}
    pid = resolve_pid(project_id) if capture else None
    shot = _shot_path(pid, tool) if capture else None
    if tool == "nx":
        out = _nx_ops(f"ops.set_params(json.loads({json.dumps(values)!r}), fem={bool(fem)!r}, measure={bool(measure)!r}, "
                      f"capture={str(shot) if shot else None!r})", timeout_sec)
    elif tool == "practice":
        t0 = time.time()
        code = (f"import json\nmodel.update(json.loads({json.dumps(values)!r}))\n"
                f"result = json.dumps({{'ok': True, 'changed': {{k: model[k] for k in {sorted(values)!r}}}}})\n")
        out = _parse(live_exec("practice", code, timeout_sec), t0)
    else:
        return {"ok": False, "error": f"{tool}은(는) live_exec로 값을 바꾸세요(빠른 툴은 nx·practice만)."}
    return _with_capture(out, tool, shot, pid, title, caption, flow_id, node_id)


@mcp.tool()
def live_view(tool: str, orient: str | None = None, fit: bool = True, style: str | None = None,
              show: list[str] | None = None, hide: list[str] | None = None, only: list[str] | None = None,
              capture: bool = False, title: str = "", caption: str = "", flow_id: int | None = None,
              node_id: str | None = None, project_id: int | None = None) -> dict:
    """화면을 바꾼다(NX) — 코드를 쓰지 않아 빠르다.
    orient: front·back·top·bottom·left·right·iso·trimetric · fit: 화면에 맞추기
    style: shaded(음영)·edges(음영+모서리)·wire(와이어)·hidden(숨은선) · show/hide/only: 바디 이름 또는 피처 이름(예: EXTRUDE(3))
    capture: 바뀐 화면을 같은 호출에서 게시"""
    if tool != "nx":
        return {"ok": False, "error": f"{tool}은(는) live_exec로 화면을 바꾸세요(live_view는 nx만)."}
    pid = resolve_pid(project_id) if capture else None
    shot = _shot_path(pid, tool) if capture else None
    out = _nx_ops(f"ops.view(orient={orient!r}, fit={bool(fit)!r}, style={style!r}, show={show!r}, hide={hide!r}, "
                  f"only={only!r}, capture={str(shot) if shot else None!r})", 60)
    return _with_capture(out, tool, shot, pid, title, caption, flow_id, node_id)


@mcp.tool()
def live_undo(tool: str, steps: int = 1) -> dict:
    """live_set_params로 바꾼 것을 최근 것부터 steps번 되돌린다(NX)."""
    if tool != "nx":
        return {"ok": False, "error": f"{tool}은(는) 툴의 Undo를 쓰세요(live_undo는 nx만)."}
    return _nx_ops(f"ops.undo({int(steps)!r})", 60)
