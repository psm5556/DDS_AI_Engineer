"""프론트엔드 경로가 빌드 폴더 밖의 파일을 내주면 로그인 없이 DB·비밀 키가 새어 나간다."""
from server.tests.test_api import client  # noqa: F401  임시 데이터 폴더를 먼저 정한다

import pytest

from server.app.main import spa_file


@pytest.fixture()
def dist(tmp_path):
    d = tmp_path / "frontend" / "dist"
    d.mkdir(parents=True)
    (d / "index.html").write_text("index", encoding="utf-8")
    (d / "favicon.svg").write_text("icon", encoding="utf-8")
    (d / "hero").mkdir()
    (d / "hero" / "a.bin").write_text("hero", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "secret.key").write_text("secret", encoding="utf-8")
    return d


@pytest.mark.parametrize("path", [
    "../../data/secret.key",
    "..\\..\\data\\secret.key",
    "hero/../../../data/secret.key",
    "./../../data/secret.key",
])
def test_outside_of_dist_gets_index(dist, path):
    assert spa_file(dist, path) == dist / "index.html"


def test_absolute_path_gets_index(dist):
    # Path끼리 이으면 절대 경로가 앞을 덮어써 어떤 파일이든 가리킨다
    assert spa_file(dist, str(dist.parent.parent / "data" / "secret.key")) == dist / "index.html"


def test_null_byte_gets_index_instead_of_error(dist):
    assert spa_file(dist, "a\0.txt") == dist / "index.html"


def test_empty_and_unknown_routes_get_index(dist):
    assert spa_file(dist, "") == dist / "index.html"
    assert spa_file(dist, "projects/12") == dist / "index.html"
    assert spa_file(dist, "hero") == dist / "index.html"  # 폴더는 파일이 아니다


def test_files_inside_dist_are_served(dist):
    assert spa_file(dist, "favicon.svg").read_text(encoding="utf-8") == "icon"
    assert spa_file(dist, "hero/a.bin").read_text(encoding="utf-8") == "hero"
