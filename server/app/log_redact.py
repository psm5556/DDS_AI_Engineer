"""서버 로그에서 토큰을 지운다.

브라우저와 PC Agent는 WebSocket 주소 뒤에 토큰을 붙여 접속하고(`/ws?token=…`, `/ws/agent?token=…`),
uvicorn은 그 주소를 그대로 로그에 쓴다. 로그 파일을 읽는 사람이 그 사람으로 접속할 수 있게 되므로 찍기 전에 가린다.
"""
import logging
import re

_TOKEN = re.compile(r"(?i)([?&]token=)[^&\s\"']+")


def redact(text: str) -> str:
    return _TOKEN.sub(r"\1***", text)


class RedactTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def install() -> None:
    """같은 필터를 두 번 달지 않는다. 시험에서 앱을 다시 불러와도 한 번만 걸린다."""
    for name in ("uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactTokens) for f in logger.filters):
            logger.addFilter(RedactTokens())
