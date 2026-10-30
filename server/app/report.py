"""프로젝트 보고서(HTML·Word): AI가 새로 쓴 보고서(models.Report)가 있으면 그 본문에 결과(그림·표)를 번호와 함께 넣고,
없으면 게시된 결과를 차례로 모은다. 그래프는 matplotlib으로 다시 그린다(웹 화면의 Plotly는 브라우저 전용)."""
from __future__ import annotations

import base64
import html
import io
import re
from datetime import datetime

import markdown as md
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from . import models  # noqa: E402
from .config import settings  # noqa: E402

PALETTE = ["#2563eb", "#0d9488", "#7c3aed", "#d97706", "#dc2626", "#64748b"]
KIND_ORDER = {"markdown": 0, "table": 1, "scatter": 2, "line": 2, "bar": 2, "parallel": 2, "image": 3}


def _korean_font():
    # Linux 서버: fonts-nanum 또는 Noto CJK를 설치한다. Noto CJK 묶음 파일(ttc)은 이름이 JP로 읽혀도 한글이 들어 있다
    for name in ("Malgun Gothic", "NanumGothic", "NanumBarunGothic", "AppleGothic", "Noto Sans CJK KR", "Noto Sans KR",
                 "Noto Sans CJK JP"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


_korean_font()


def chart_png(a: models.Artifact) -> bytes | None:
    p = a.payload or {}
    series = p.get("series") or []
    fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=130)
    try:
        if a.kind in ("scatter", "line"):
            for i, s in enumerate(series):
                pts = s.get("points") or []
                xs, ys = [q["x"] for q in pts], [q["y"] for q in pts]
                c = PALETTE[i % len(PALETTE)]
                if a.kind == "line":
                    ax.plot(xs, ys, "-o", ms=3, lw=1.6, color=c, label=s.get("name"))
                else:
                    ax.scatter(xs, ys, s=16, color=c, alpha=0.85, label=s.get("name"))
            if p.get("diagonal"):
                vals = [v for s in series for q in s.get("points") or [] for v in (q["x"], q["y"])]
                if vals:
                    ax.plot([min(vals), max(vals)], [min(vals), max(vals)], "--", color="#a1a1aa", lw=1)
            ax.set_xlabel(p.get("x", ""))
            ax.set_ylabel(p.get("y", ""))
        elif a.kind == "bar":
            cats = p.get("categories") or []
            n = max(1, len(series))
            for i, s in enumerate(series):
                xs = [j + i / (n + 1) for j in range(len(cats))]
                ax.bar(xs, s.get("values") or [], width=1 / (n + 1), color=PALETTE[i % len(PALETTE)], label=s.get("name"))
            ax.set_xticks([j + (n - 1) / (2 * (n + 1)) for j in range(len(cats))], cats)
        elif a.kind == "parallel":
            dims, rows = p.get("dimensions") or [], p.get("rows") or []
            if not dims or not rows:
                return None
            cols = list(zip(*rows))
            norm = [[(v - min(c)) / ((max(c) - min(c)) or 1) for v in c] for c in cols]
            for r in zip(*norm):
                ax.plot(range(len(dims)), r, color="#2563eb", alpha=0.25, lw=1)
            ax.set_xticks(range(len(dims)), dims)
        else:
            return None
        if len(series) > 1:
            ax.legend(fontsize=8, frameon=False)
        ax.grid(alpha=0.25)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        return buf.getvalue()
    finally:
        plt.close(fig)


def image_bytes(project_id: int, a: models.Artifact) -> bytes | None:
    rel = (a.payload or {}).get("path")
    if not rel:
        return None
    f = (settings.files_dir / f"project_{project_id}" / rel).resolve()
    root = (settings.files_dir / f"project_{project_id}").resolve()
    return f.read_bytes() if root in f.parents and f.is_file() else None


def _ordered(arts: list[models.Artifact]) -> list[models.Artifact]:
    return sorted(arts, key=lambda a: (KIND_ORDER.get(a.kind, 9), a.created_at))


# ---------- 작업 흐름 단계 번호 (웹 화면 orderNodes와 같은 규칙) ----------
def order_nodes(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """실행 순서(엣지 기준 위상 정렬, 같은 순위는 원래 순서). 이 순서가 '1단계, 2단계' 번호다."""
    indeg = {n["id"]: 0 for n in nodes}
    for e in edges:
        indeg[e["target"]] = indeg.get(e["target"], 0) + 1
    left, out = list(nodes), []
    while left:
        i = next((k for k, n in enumerate(left) if indeg.get(n["id"], 0) == 0), 0)
        n = left.pop(i)
        out.append(n)
        for e in edges:
            if e["source"] == n["id"]:
                indeg[e["target"]] = indeg.get(e["target"], 1) - 1
    return out


def step_names(flow: models.Flow) -> dict[str, str]:
    """노드 id → '2단계 · DOE 60케이스'"""
    return {n["id"]: f"{i}단계 · {n.get('label', '')}" for i, n in enumerate(order_nodes(flow.nodes or [], flow.edges or []), 1)}


# ---------- AI 보고서 본문: markdown 사이에 ![제목](artifact:<id>) 줄로 결과를 넣는다 ----------
EMBED = re.compile(r"^\s*!\[(.*)\]\(artifact:(\d+)\)\s*$")  # 캡션에 [ ]가 들어가도 한 줄 전체로 본다


def embedded_ids(body: str) -> list[int]:
    return [int(m.group(2)) for line in (body or "").splitlines() if (m := EMBED.match(line))]


def split_body(body: str) -> list[tuple[str, str, int | None]]:
    """본문을 ('md', 글, None) · ('art', 캡션, id) 조각으로 나눈다."""
    parts: list[tuple[str, str, int | None]] = []
    buf: list[str] = []
    for line in (body or "").splitlines():
        m = EMBED.match(line)
        if m:
            if buf:
                parts.append(("md", "\n".join(buf), None))
                buf = []
            parts.append(("art", m.group(1).strip(), int(m.group(2))))
        else:
            buf.append(line)
    if buf:
        parts.append(("md", "\n".join(buf), None))
    return parts


def _figure_label(kind: str, counters: dict[str, int]) -> str:
    key = "표" if kind == "table" else "그림"
    counters[key] = counters.get(key, 0) + 1
    return f"{key} {counters[key]}"


_CSS = """body{font-family:'Malgun Gothic','Pretendard',sans-serif;color:#18181b;max-width:900px;margin:40px auto;padding:0 24px;line-height:1.7}
h1{font-size:26px;margin:0} h2{font-size:19px;margin:34px 0 10px;border-bottom:1px solid #ececef;padding-bottom:6px} h3{font-size:16px;margin:24px 0 8px}
.meta{color:#71717a;font-size:13px} table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0} th,td{border:1px solid #e4e4e7;padding:6px 10px}
th{background:#f4f4f5;color:#52525b;text-align:left} td.r{text-align:right} img{max-width:100%;border:1px solid #ececef;border-radius:8px}
figure{margin:18px 0} figcaption{color:#52525b;font-size:13px;margin-top:6px} .flow li.complete{color:#16a34a} .flow li.failed{color:#dc2626}"""


def _art_html(project_id: int, a: models.Artifact) -> str:
    p = a.payload or {}
    if a.kind == "markdown":
        return md.markdown(html.escape(p.get("text", ""), quote=False), extensions=["tables"])
    if a.kind == "table":
        out = ["<table><tr>" + "".join(f"<th>{html.escape(str(c))}</th>" for c in p.get("columns", [])) + "</tr>"]
        for r in p.get("rows", [])[:200]:
            out.append("<tr>" + "".join(f"<td class='{'r' if isinstance(v, (int, float)) else ''}'>{html.escape(_fmt(v))}</td>" for v in r) + "</tr>")
        return "".join(out) + "</table>"
    data = image_bytes(project_id, a) if a.kind == "image" else chart_png(a)
    if not data:
        return ""
    return f"<img src='data:image/png;base64,{base64.b64encode(data).decode()}' alt='{html.escape(a.title)}'>"


def build_html(project: models.Project, arts: list[models.Artifact], flow: models.Flow | None,
               rep: models.Report | None = None) -> str:
    """rep이 있으면 AI가 쓴 보고서, 없으면 게시된 결과를 차례로 모은 보고서."""
    title = rep.title if rep else project.name
    parts = [f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{_CSS}</style></head><body>
<h1>{html.escape(title)}</h1><p class="meta">{html.escape(project.name if rep else project.description or "")}<br>작성: {(rep.created_at if rep else datetime.now()):%Y-%m-%d %H:%M} · DDS Conversa</p>"""]
    if rep:
        by_id = {a.id: a for a in arts}
        counters: dict[str, int] = {}
        for kind, text, aid in split_body(rep.body):
            a = by_id.get(aid) if aid else None
            if kind == "md":
                parts.append(md.markdown(html.escape(text, quote=False), extensions=["tables"]))
            elif a:
                cap = f"{_figure_label(a.kind, counters)}. {text or a.title}"
                parts.append(f"<figure>{_art_html(project.id, a)}<figcaption>{html.escape(cap)}</figcaption></figure>")
        parts.append("</body></html>")
        return "".join(parts)
    if flow:
        parts.append(f"<h2>작업 흐름 — {html.escape(flow.title)}</h2><p class='meta'>{html.escape(flow.goal or '')}</p><ol class='flow'>")
        for n in order_nodes(flow.nodes or [], flow.edges or []):
            parts.append(f"<li class='{n.get('status')}'>{html.escape(n.get('label', ''))}"
                         f"{' — ' + html.escape(n.get('detail')) if n.get('detail') else ''}</li>")
        parts.append("</ol>")
    for a in _ordered(arts):
        parts.append(f"<h2>{html.escape(a.title)}</h2>")
        parts.append(_art_html(project.id, a))
        if a.kind == "image" and (a.payload or {}).get("caption"):
            parts.append(f"<p class='meta'>{html.escape(a.payload['caption'])}</p>")
    parts.append("</body></html>")
    return "".join(parts)


# ---------- Word ----------
INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")


def _runs(par, text: str):
    """**굵게**와 `코드`만 살려 문단에 글을 넣는다."""
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            par.add_run(piece[2:-2]).bold = True
        elif piece.startswith("`") and piece.endswith("`"):
            par.add_run(piece[1:-1])
        else:
            par.add_run(piece)


def _md_docx(doc, text: str, base_level: int = 0):
    """보고서 markdown을 Word로: 제목(#), 목록(-, 1.), 표(|), 문단."""
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        if not t:
            i += 1
            continue
        if t.startswith("|"):  # 표: 구분선(|---|)은 건너뛴다
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                n = max(len(r) for r in rows)
                tbl = doc.add_table(rows=0, cols=n)
                tbl.style = "Light Grid Accent 1"
                for r in rows:
                    cells = tbl.add_row().cells
                    for k, c in enumerate(r[:n]):
                        cells[k].text = c.replace("**", "")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", t)
        if m:
            doc.add_heading(m.group(2).replace("**", ""), min(4, len(m.group(1)) + base_level))
        elif re.match(r"^[-*]\s+", t):
            _runs(doc.add_paragraph(style="List Bullet"), re.sub(r"^[-*]\s+", "", t))
        elif re.match(r"^\d+[.)]\s+", t):
            _runs(doc.add_paragraph(style="List Number"), re.sub(r"^\d+[.)]\s+", "", t))
        else:
            _runs(doc.add_paragraph(), t)
        i += 1


def _art_docx(doc, project_id: int, a: models.Artifact):
    from docx.shared import Inches
    p = a.payload or {}
    if a.kind == "markdown":
        _md_docx(doc, p.get("text") or "", base_level=1)
    elif a.kind == "table":
        cols, rows = p.get("columns", []), p.get("rows", [])[:200]
        if cols:
            tbl = doc.add_table(rows=1, cols=len(cols))
            tbl.style = "Light Grid Accent 1"
            for i, c in enumerate(cols):
                tbl.rows[0].cells[i].text = str(c)
            for r in rows:
                cells = tbl.add_row().cells
                for i, v in enumerate(r[: len(cols)]):
                    cells[i].text = _fmt(v)
    else:
        data = image_bytes(project_id, a) if a.kind == "image" else chart_png(a)
        if data:
            doc.add_picture(io.BytesIO(data), width=Inches(6.0))


def build_docx(project: models.Project, arts: list[models.Artifact], flow: models.Flow | None,
               rep: models.Report | None = None) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Malgun Gothic"
    style.font.size = Pt(10)
    if rep:
        doc.add_heading(rep.title, 0)
        doc.add_paragraph(f"{project.name}\n작성: {rep.created_at:%Y-%m-%d %H:%M} · DDS Conversa")
        by_id = {a.id: a for a in arts}
        counters: dict[str, int] = {}
        for kind, text, aid in split_body(rep.body):
            a = by_id.get(aid) if aid else None
            if kind == "md":
                _md_docx(doc, text)
            elif a:
                _art_docx(doc, project.id, a)
                cap = doc.add_paragraph(f"{_figure_label(a.kind, counters)}. {text or a.title}")
                cap.runs[0].italic = True
    else:
        doc.add_heading(project.name, 0)
        doc.add_paragraph(f"{project.description or ''}\n작성: {datetime.now():%Y-%m-%d %H:%M} · DDS Conversa")
        if flow:
            doc.add_heading(f"작업 흐름 — {flow.title}", 1)
            if flow.goal:
                doc.add_paragraph(flow.goal)
            for n in order_nodes(flow.nodes or [], flow.edges or []):
                doc.add_paragraph(f"{n.get('label', '')} ({n.get('status')}){' — ' + n['detail'] if n.get('detail') else ''}", style="List Number")
        for a in _ordered(arts):
            doc.add_heading(a.title, 1)
            _art_docx(doc, project.id, a)
            if a.kind == "image" and (a.payload or {}).get("caption"):
                doc.add_paragraph(a.payload["caption"])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------- AI가 보고서를 쓸 때 읽는 자료 ----------
def _num(v):
    return float(f"{v:.5g}") if isinstance(v, float) else v


def summarize(a: models.Artifact) -> dict:
    """결과 하나를 AI가 읽기 좋은 크기로 줄인다(표는 앞 12행, 그래프는 축·범위)."""
    p = a.payload or {}
    if a.kind == "table":
        rows = p.get("rows") or []
        return {"columns": p.get("columns", []), "rows": [[_num(v) for v in r] for r in rows[:12]], "n_rows": len(rows)}
    if a.kind in ("scatter", "line"):
        out = []
        for s in p.get("series") or []:
            pts = s.get("points") or []
            xs, ys = [q["x"] for q in pts], [q["y"] for q in pts]
            out.append({"name": s.get("name"), "n": len(pts),
                        "x_range": [_num(min(xs)), _num(max(xs))] if xs else None,
                        "y_range": [_num(min(ys)), _num(max(ys))] if ys else None, "y_last": _num(ys[-1]) if ys else None})
        return {"x": p.get("x"), "y": p.get("y"), "series": out}
    if a.kind == "bar":
        return {"categories": (p.get("categories") or [])[:30],
                "series": [{"name": s.get("name"), "values": [_num(v) for v in (s.get("values") or [])[:30]]} for s in p.get("series") or []]}
    if a.kind == "parallel":
        return {"dimensions": p.get("dimensions"), "n_rows": len(p.get("rows") or [])}
    if a.kind == "markdown":
        return {"text": (p.get("text") or "")[:4000]}
    return {"caption": p.get("caption", "")}


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return "" if v is None else str(v)
