"""서버 설정. 루트의 server.toml(없으면 기본값)과 DDS_* 환경변수를 읽는다."""
from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    host: str = "0.0.0.0"
    port: int = 8765
    data_dir: Path = ROOT / "data"
    secret_key: str = ""
    access_token_hours: int = 12
    # 케이스 실행을 맡을 Agent가 없을 때 서버에서 직접 mock 어댑터를 돌릴지(개발/데모용)
    allow_server_mock: bool = True
    max_upload_mb: int = 1024  # 대화 첨부 파일 최대 크기(CAD·해석 입력 파일)
    # 사내 웹 CAE 뷰어: 결과 파일을 새 창으로 여는 URL 템플릿. {path}=파일 절대경로(그 PC 기준), {host}=PC 이름,
    # {project_id}, {rel}=프로젝트 폴더 기준 상대경로. 예) "http://cae-viewer.corp/open?host={host}&file={path}"
    web_viewer_url: str = ""

    @property
    def db_url(self) -> str:
        return os.environ.get("DDS_DB_URL") or f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


def load_settings() -> Settings:
    s = Settings()
    cfg_path = Path(os.environ.get("DDS_SERVER_CONFIG", ROOT / "server.toml"))
    if cfg_path.exists():
        for key, value in tomllib.loads(cfg_path.read_text(encoding="utf-8")).items():
            if hasattr(s, key):
                setattr(s, key, Path(value) if isinstance(getattr(s, key), Path) else value)
    if os.environ.get("DDS_DATA_DIR"):
        s.data_dir = Path(os.environ["DDS_DATA_DIR"])
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.files_dir.mkdir(parents=True, exist_ok=True)
    if not s.secret_key:
        # 설정이 없으면 data_dir에 한 번 생성해 재시작 후에도 토큰이 유지되게 한다
        key_file = s.data_dir / "secret.key"
        if not key_file.exists():
            key_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        s.secret_key = key_file.read_text(encoding="utf-8").strip()
    return s


settings = load_settings()
