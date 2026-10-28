"""프로젝트 보고서: 아티팩트(요약·표·그래프·이미지)를 모아 HTML 또는 Word(.docx)로 만든다.
그래프는 matplotlib으로 그림 파일을 만들어 넣는다(웹 화면의 Plotly는 브라우저 전용이라 서버에서 다시 그린다)."""
from __future__ import annotations

import base64
import html
import io
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
    for name in ("Malgun Gothic", "NanumGothic", "AppleGothic", "Noto Sans CJK KR"):
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


def build_html(project: models.Project, arts: list[models.Artifact], flow: models.Flow | None) -> str:
    parts = [f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{html.escape(project.name)} 보고서</title>
<style>body{{font-family:'Malgun Gothic','Pretendard',sans-serif;color:#18181b;max-width:900px;margin:40px auto;padding:0 24px;line-height:1.65}}
h1{{font-size:26px;margin:0}} h2{{font-size:18px;margin:32px 0 10px;border-bottom:1px solid #ececef;padding-bottom:6px}}
.meta{{color:#71717a;font-size:13px}} table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #e4e4e7;padding:6px 10px}}
th{{background:#f4f4f5;color:#52525b;text-align:left}} td.r{{text-align:right}} img{{max-width:100%;border:1px solid #ececef;border-radius:8px}}
.flow li.complete{{color:#16a34a}} .flow li.failed{{color:#dc2626}}</style></head><body>
<h1>{html.escape(project.name)}</h1><p class="meta">{html.escape(project.description or "")}<br>작성: {datetime.now():%Y-%m-%d %H:%M} · DDS Conversa</p>"""]
    if flow:
        parts.append(f"<h2>작업 흐름 — {html.escape(flow.title)}</h2><p class='meta'>{html.escape(flow.goal or '')}</p><ol class='flow'>")
        for n in flow.nodes:
            parts.append(f"<li class='{n.get('status')}'>{html.escape(n.get('label', ''))}"
                         f"{' — ' + html.escape(n.get('detail')) if n.get('detail') else ''}</li>")
        parts.append("</ol>")
    for a in _ordered(arts):
        parts.append(f"<h2>{html.escape(a.title)}</h2>")
        p = a.payload or {}
        if a.kind == "markdown":
            parts.append(md.markdown(html.escape(p.get("text", ""), quote=False), extensions=["tables"]))
        elif a.kind == "table":
            parts.append("<table><tr>" + "".join(f"<th>{html.escape(str(c))}</th>" for c in p.get("columns", [])) + "</tr>")
            for r in p.get("rows", [])[:200]:
                parts.append("<tr>" + "".join(f"<td class='{'r' if isinstance(v, (int, float)) else ''}'>{html.escape(_fmt(v))}</td>" for v in r) + "</tr>")
            parts.append("</table>")
        else:
            data = image_bytes(project.id, a) if a.kind == "image" else chart_png(a)
            if data:
                parts.append(f"<img src='data:image/png;base64,{base64.b64encode(data).decode()}' alt='{html.escape(a.title)}'>")
                if p.get("caption"):
                    parts.append(f"<p class='meta'>{html.escape(p['caption'])}</p>")
    parts.append("</body></html>")
    return "".join(parts)


def build_docx(project: models.Project, arts: list[models.Artifact], flow: models.Flow | None) -> bytes:
    from docx import Document
    from docx.shared import Inches, Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Malgun Gothic"
    style.font.size = Pt(10)
    doc.add_heading(project.name, 0)
    doc.add_paragraph(f"{project.description or ''}\n작성: {datetime.now():%Y-%m-%d %H:%M} · DDS Conversa")
    if flow:
        doc.add_heading(f"작업 흐름 — {flow.title}", 1)
        if flow.goal:
            doc.add_paragraph(flow.goal)
        for n in flow.nodes:
            doc.add_paragraph(f"{n.get('label', '')} ({n.get('status')}){' — ' + n['detail'] if n.get('detail') else ''}", style="List Number")
    for a in _ordered(arts):
        doc.add_heading(a.title, 1)
        p = a.payload or {}
        if a.kind == "markdown":
            for line in (p.get("text") or "").splitlines():
                t = line.strip()
                if not t:
                    continue
                if t.startswith("#"):
                    doc.add_heading(t.lstrip("# "), 2)
                elif t.startswith(("- ", "* ")):
                    doc.add_paragraph(t[2:].replace("**", ""), style="List Bullet")
                else:
                    doc.add_paragraph(t.replace("**", ""))
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
            data = image_bytes(project.id, a) if a.kind == "image" else chart_png(a)
            if data:
                doc.add_picture(io.BytesIO(data), width=Inches(6.0))
                if p.get("caption"):
                    doc.add_paragraph(p["caption"])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return "" if v is None else str(v)
