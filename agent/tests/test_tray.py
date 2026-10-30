"""트레이 아이콘: 파비콘과 같은 파란 바탕의 흰 D + 상태 점."""
import pytest

pytest.importorskip("PIL")

from agent.dds_agent.tray import COLORS, _icon_image  # noqa: E402

BLUE = (20, 40, 160)


def _near(px, rgb, tol=12):
    return all(abs(a - b) <= tol for a, b in zip(px[:3], rgb))


def test_icon_is_white_d_on_samsung_blue():
    img = _icon_image("connected")
    assert img.size == (64, 64) and img.mode == "RGBA"
    assert _near(img.getpixel((6, 32)), BLUE)  # 바탕
    assert _near(img.getpixel((17, 30)), (255, 255, 255))  # D의 세로획
    assert _near(img.getpixel((29, 29)), BLUE)  # D의 빈 공간
    assert _near(img.getpixel((41, 29)), (255, 255, 255))  # D의 오른쪽 곡선
    assert img.getpixel((0, 0))[3] == 0  # 모서리는 둥글게 비운다


@pytest.mark.parametrize("state", list(COLORS))
def test_state_dot_shows_status(state):
    assert _near(_icon_image(state).getpixel((52, 52)), COLORS[state])  # 상태는 색 점으로 한눈에
