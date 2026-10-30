"""디자인 규칙 지키기(docs/design-renewal-plan.md): 색·글자 크기·둥글기는 design/tokens.css의 토큰만 쓴다.
CSS 층(styles·layout·app·agentic·modern)에 hex 색, px 글자 크기, px 둥글기를 다시 적으면 실패한다."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
LAYERS = ["styles.css", "layout.css", "app.css", "agentic.css", "modern.css"]


def _css():
    for name in LAYERS:
        text = (SRC / name).read_text(encoding="utf-8")
        yield name, re.sub(r"/\*.*?\*/", "", text, flags=re.S)  # 주석은 검사하지 않는다


def test_no_hex_colors_outside_tokens():
    bad = [f"{n}: {m}" for n, css in _css() for m in re.findall(r"#[0-9a-fA-F]{3,8}\b", css)]
    assert not bad, "색은 design/tokens.css에 토큰으로 두고 var(--…)로 쓴다: " + ", ".join(bad[:10])


def test_font_sizes_use_scale():
    bad = [f"{n}: {m}" for n, css in _css() for m in re.findall(r"font-size:\s*[0-9.]+(?:px|rem|em)", css)]
    assert not bad, "글자 크기는 7단계 토큰(--fs-11·12·13·14·16·20·28)만 쓴다: " + ", ".join(bad[:10])


def test_radii_use_scale():
    bad = []
    for n, css in _css():
        for prop, val in re.findall(r"(border(?:-[a-z]+)*-radius)\s*:([^;}]*)", css):
            if re.search(r"\b[1-9][0-9.]*px", val):
                bad.append(f"{n}: {prop}:{val.strip()}")
    assert not bad, "둥글기는 5단계 토큰(--r-xs·s·m·l·pill)만 쓴다(원은 50%): " + ", ".join(bad[:10])


def test_tokens_defined_for_both_themes():
    tokens = (SRC / "design" / "tokens.css").read_text(encoding="utf-8")
    light, dark = re.split(r'\n:root\[data-theme="dark"\] \{', tokens, maxsplit=1)
    used = {m for _, css in _css() for m in re.findall(r"var\((--[a-z0-9-]+)", css)}
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", light))
    local = {"--c", "--p", "--canvas-w", "--cv-w", "--side-col", "--cv-col"}  # 요소 안에서 정하는 지역 변수(게이지·레이아웃 폭)
    missing = sorted(u for u in used if u not in defined and u not in local)
    assert not missing, "tokens.css에 없는 토큰을 씁니다: " + ", ".join(missing)
    # 어두운 테마는 바탕·글자·선을 모두 다시 정한다
    for t in ("--paper", "--panel", "--ink", "--ink-3", "--line", "--brand", "--ok-ink", "--err-ink", "--warn-ink"):
        assert re.search(rf"{t}\s*:", dark), f"어두운 테마에 {t} 값이 없습니다"


def _theme_values():
    tokens = (SRC / "design" / "tokens.css").read_text(encoding="utf-8")
    light, dark = re.split(r'\n:root\[data-theme="dark"\] \{', tokens, maxsplit=1)
    lv = dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", light))
    dv = {**lv, **dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", dark))}
    return lv, dv


def _rgba(v: str):
    v = v.strip()
    if v.startswith("#"):
        h = v[1:]
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    m = re.match(r"rgb\((\d+) (\d+) (\d+)(?: / ([0-9.]+))?\)", v)
    return (int(m[1]), int(m[2]), int(m[3]), float(m[4] or 1))


def _contrast(fg, bg) -> float:
    def lum(c):
        def ch(x):
            x /= 255
            return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])
    a, b = lum(fg), lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _over(top, base):
    a = top[3]
    return tuple(top[i] * a + base[i] * (1 - a) for i in range(3)) + (1.0,)


def test_text_contrast_both_themes():
    """본문·보조 글자와 상태 태그 글자는 두 테마 모두 대비 4.5:1 이상(WCAG AA)."""
    pairs = [("--ink", "--paper"), ("--ink", "--panel"), ("--ink-2", "--panel"), ("--ink-3", "--panel"), ("--ink-3", "--paper"),
             ("--ink-3", "--panel-2"), ("--on-solid", "--brand-solid"),
             ("--ok-ink", "--ok-soft"), ("--warn-ink", "--warn-soft"), ("--err-ink", "--err-soft"), ("--info-ink", "--info-soft"),
             ("--mean-ink", "--mean-soft")]
    low = []
    for name, vals in zip(("밝은", "어두운"), _theme_values()):
        panel = _rgba(vals["--panel"])
        for fg, bg in pairs:
            b = _over(_rgba(vals[bg]), panel)
            r = _contrast(_over(_rgba(vals[fg]), b), b)
            if r < 4.5:
                low.append(f"{name} {fg} on {bg} = {r:.2f}")
    assert not low, "글자 대비가 낮습니다: " + ", ".join(low)
