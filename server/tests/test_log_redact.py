"""WebSocket 주소에 붙은 토큰이 서버 로그에 남지 않는다."""
import logging

from server.app import log_redact, main  # noqa: F401  main을 불러와야 필터가 걸린다


def _emit(name: str, msg: str, *args) -> str:
    """uvicorn이 쓰는 것과 같은 모양으로 남겨 실제로 찍힐 글자를 본다."""
    seen: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    logger = logging.getLogger(name)
    keep, level = Keep(), logger.level
    logger.addHandler(keep)
    logger.setLevel(logging.INFO)
    try:
        logger.info(msg, *args)
    finally:
        logger.removeHandler(keep)
        logger.setLevel(level)
    return seen[0]


def test_websocket_line_hides_token():
    line = _emit("uvicorn.error", '%s - "WebSocket %s" [accepted]', "127.0.0.1:1", "/ws/agent?token=dds_abcDEF123")
    assert "dds_abcDEF123" not in line
    assert "/ws/agent?token=***" in line


def test_access_line_hides_token_and_keeps_rest():
    line = _emit("uvicorn.access", '%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/ws?a=1&token=eyJhbGci.x.y&b=2", "1.1", 403)
    assert "eyJhbGci" not in line
    assert "/ws?a=1&token=***&b=2" in line and "403" in line


def test_lines_without_token_stay_the_same():
    assert _emit("uvicorn.access", '%s "%s"', "x", "/api/projects?tokens=5") == 'x "/api/projects?tokens=5"'


def test_install_is_once():
    log_redact.install()
    log_redact.install()
    filters = [f for f in logging.getLogger("uvicorn.error").filters if isinstance(f, log_redact.RedactTokens)]
    assert len(filters) == 1
