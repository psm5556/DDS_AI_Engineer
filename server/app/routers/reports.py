"""보고서: AI가 작업 흐름·결과·스터디를 읽고 새로 쓰는 종합 보고서와 그 Word·HTML 내려받기.
AI(dds-flow MCP)는 report-context로 자료를 읽고 POST reports로 저장한다. 웹 '보고서' 탭은 가장 최근 판을 보여 준다."""
import re
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, report, schemas
from ..database import get_db
from ..deps import get_current_user, get_or_404, get_project_for_member, require_member
from ..study import service
from ..ws import manager

router = APIRouter(prefix="/api", tags=["reports"])


def _caption(title: str) -> str:
    """'[탈수 진동] 수렴 이력' → '수렴 이력' — 보고서 그림 설명에는 스터디 머리말이 필요 없다."""
    return re.sub(r"^\[[^\]]*\]\s*", "", title).replace("[", "(").replace("]", ")") or title


def _new_results(db: Session, rep: models.Report) -> int:
    """이 판을 쓴 뒤 새로 생기거나 바뀐 결과 수 — 화면에서 '다시 쓰기'를 권할 때 쓴다."""
    changed = func.coalesce(models.Artifact.updated_at, models.Artifact.created_at)
    return db.query(models.Artifact).filter(models.Artifact.project_id == rep.project_id, changed > rep.created_at).count()


def _issue(db: Session, project: models.Project, rep: models.Report | None, user: models.User | None = None) -> report.Issue:
    """틀(표지·머리글·맺음말)에 쓰는 발행 정보. 판은 프로젝트 안에서 몇 번째인지다."""
    if rep is None:
        return report.make_issue(project, requester=user.display_name if user else "")
    who = db.get(models.User, rep.created_by) if rep.created_by else None
    edition = db.query(models.Report).filter(models.Report.project_id == project.id, models.Report.id <= rep.id).count()
    return report.make_issue(project, rep, edition, who.display_name if who else "")


def _response(db: Session, rep: models.Report) -> dict:
    # 화면도 파일과 같은 틀을 쓰도록 발행 정보를 함께 준다. 표지에 있는 제목 줄은 본문에서 뺀 채로 준다
    project = db.get(models.Project, rep.project_id)
    i = _issue(db, project, rep)
    return {**schemas.ReportResponse.model_validate(rep).model_dump(), "new_results": _new_results(db, rep),
            "body": report.clean_body(rep.body, rep.title), "project_name": project.name, "doc_no": i.doc_no,
            "edition": int(i.edition.removesuffix("판")), "issued_at": i.issued_at, "author": i.author,
            "provenance": i.provenance, "colophon": i.colophon}


@router.get("/projects/{project_id}/reports", response_model=list[schemas.ReportSummary])
def list_reports(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    return db.query(models.Report).filter_by(project_id=project.id).order_by(models.Report.id.desc()).all()


@router.get("/projects/{project_id}/reports/latest", response_model=schemas.ReportResponse | None)
def latest_report(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    rep = db.query(models.Report).filter_by(project_id=project.id).order_by(models.Report.id.desc()).first()
    return _response(db, rep) if rep else None


@router.get("/reports/{report_id}", response_model=schemas.ReportResponse)
def get_report(report_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rep = get_or_404(db, models.Report, report_id, "보고서")
    require_member(rep.project_id, user, db)
    return _response(db, rep)


@router.post("/projects/{project_id}/reports", response_model=schemas.ReportResponse)
def create_report(body: schemas.ReportCreate, project: models.Project = Depends(get_project_for_member),
                  user=Depends(get_current_user), db: Session = Depends(get_db)):
    """AI가 쓴 보고서를 새 판으로 저장한다. 본문에 넣은 결과(artifact:<id>)는 이 프로젝트에 있어야 한다."""
    ids = set(report.embedded_ids(body.body))
    have = {a for (a,) in db.query(models.Artifact.id).filter(models.Artifact.project_id == project.id,
                                                               models.Artifact.id.in_(ids))} if ids else set()
    if ids - have:
        raise HTTPException(400, f"이 프로젝트에 없는 결과를 넣었습니다: artifact {', '.join(map(str, sorted(ids - have)))} "
                                 "→ report-context의 results에 있는 id만 쓰세요")
    rep = models.Report(project_id=project.id, flow_id=body.flow_id, title=body.title, body=body.body, created_by=user.id)
    db.add(rep)
    db.commit()
    db.refresh(rep)
    manager.publish({"type": "report_updated", "project_id": project.id, "data": {"id": rep.id}})
    return _response(db, rep)


@router.get("/projects/{project_id}/report-context")
def report_context(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    """AI가 보고서를 쓰기 전에 읽는 자료: 작업 흐름(단계 번호) · 결과 요약 · 스터디(입력·출력·최적·모델)."""
    flows = db.query(models.Flow).filter_by(project_id=project.id).order_by(models.Flow.id).all()
    steps: dict[tuple, str] = {}
    flows_out = []
    for f in flows:
        if f.status == "rejected":
            continue
        ordered = report.order_nodes(f.nodes or [], f.edges or [])
        for i, n in enumerate(ordered, 1):
            steps[(f.id, n["id"])] = f"{i}단계 · {n.get('label', '')}"
        flows_out.append({"flow_id": f.id, "title": f.title, "goal": f.goal, "status": f.status,
                          "steps": [{"no": i, "id": n["id"], "label": n.get("label"), "type": n.get("type"), "tool": n.get("tool"),
                                     "status": n.get("status"), "detail": n.get("detail", "")} for i, n in enumerate(ordered, 1)]})
    arts = db.query(models.Artifact).filter_by(project_id=project.id).order_by(models.Artifact.id).all()
    results = [{"artifact_id": a.id, "embed": f"![{_caption(a.title)}](artifact:{a.id})", "kind": a.kind, "title": a.title,
                "step": steps.get((a.flow_id, a.node_id), "단계 밖"), "data": report.summarize(a)} for a in arts]

    studies_out = []
    for s in db.query(models.Study).filter_by(project_id=project.id).order_by(models.Study.id):
        counts: dict[str, int] = {}
        for c in s.cases:
            counts[c.status] = counts.get(c.status, 0) + 1
        roles = {o["name"]: f"목적({'최소' if o.get('goal', 'min') == 'min' else '최대'})" for o in s.objectives or []}
        for c in s.constraints or []:
            roles.setdefault(c["name"], "제약 " + " · ".join(x for x in (f"≥ {c['lower']}" if c.get("lower") is not None else "",
                                                                        f"≤ {c['upper']}" if c.get("upper") is not None else "") if x))
        notes = {o["name"]: o.get("note", "") for o in s.outputs or []}
        done = [c for c in s.cases if c.status == "complete"]
        best = service.best_case(s)
        models_out = [{"model_id": m.id, "name": m.name, "n_train": m.n_train, "deployed": m.deployed,
                       "r2": {k: round(v["r2"], 4) for k, v in (m.metrics or {}).items()}}
                      for m in db.query(models.SurrogateModel).filter_by(study_id=s.id).order_by(models.SurrogateModel.id.desc()).limit(3)]
        studies_out.append({
            "study_id": s.id, "name": s.name, "cases": counts,
            "inputs": [{"name": v["name"], "lower": v["lower"], "upper": v["upper"]} for v in s.variables],
            "outputs": [{"name": n, "role": roles.get(n, ""), "note": notes.get(n, ""),
                         "extracted": sum(1 for c in done if n in (c.results or {}))} for n in service.output_names(s)],
            "best": {"case_id": best.id, "params": best.params, "results": best.results} if best else None,
            "models": models_out})
    prev = db.query(models.Report).filter_by(project_id=project.id).order_by(models.Report.id.desc()).first()
    return {"project": {"name": project.name, "description": project.description}, "flows": flows_out,
            "results": results, "studies": studies_out,
            "previous_report": {"title": prev.title, "created_at": prev.created_at.isoformat(timespec="minutes")} if prev else None}


@router.get("/projects/{project_id}/report")
def download_report(format: str = "html", flow_id: int | None = None, report_id: int | None = None,
                    project: models.Project = Depends(get_project_for_member), user=Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """보고서 파일(format: html | docx). AI 보고서가 있으면 가장 최근 판(또는 report_id)을, 없으면 게시된 결과를 모은다.
    flow_id는 AI 보고서가 없을 때 그 작업 흐름의 결과만 모은다. 내용이 무엇이든 같은 틀(report.py)에 담긴다."""
    rep = None
    if report_id:
        rep = get_or_404(db, models.Report, report_id, "보고서")
        if rep.project_id != project.id:
            raise HTTPException(404, "보고서가 없습니다")
    else:
        rep = db.query(models.Report).filter_by(project_id=project.id).order_by(models.Report.id.desc()).first()
    q = db.query(models.Artifact).filter_by(project_id=project.id)
    flow = None
    if rep is None and flow_id:
        flow = get_or_404(db, models.Flow, flow_id, "플로우")
        q = q.filter_by(flow_id=flow_id)
    elif rep is None:
        flow = db.query(models.Flow).filter_by(project_id=project.id).order_by(models.Flow.id.desc()).first()
    arts = q.all()
    name = f"{project.name}_보고서_{date.today():%Y%m%d}"
    issue = _issue(db, project, rep, user)
    if format == "docx":
        data = report.build_docx(project, arts, flow, rep, issue)
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.docx"})
    return HTMLResponse(report.build_html(project, arts, flow, rep, issue),
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.html"})
