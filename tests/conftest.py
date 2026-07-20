"""테스트 공용 설정.

server.py 는 import 되는 순간 init_db() 실행·일정 API 호출 등 부수효과가 있고,
DB/녹화/캐시를 전부 상대경로("f1_database.db" 등)로 다룬다. 테스트가 실제 사용자
데이터나 네트워크를 건드리지 않도록, server 를 import 하기 "전에":

  1) 임시 작업 디렉터리로 이동한다 → 모든 상대경로 파일 I/O가 임시 폴더에 격리된다.
  2) requests.get/post 를 막는다 → import 시점의 외부 API 호출이 즉시·오프라인으로 끝난다.

이렇게 하면 테스트는 네트워크 없이도, 사용자의 실제 f1_database.db 를 건드리지 않고
항상 동일하게 돌아간다.
"""
import os
import sys
import pathlib
import sqlite3
import tempfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# 1) 상대경로 파일 I/O를 임시 폴더로 격리 (StaticFiles가 요구하는 static 폴더도 만들어 둔다)
_WORKDIR = tempfile.mkdtemp(prefix="f1test_")
os.makedirs(os.path.join(_WORKDIR, "static"), exist_ok=True)
os.chdir(_WORKDIR)

# 2) 네트워크 차단 — server import 시점의 외부 호출을 오프라인으로 처리
import requests  # noqa: E402


def _blocked(*args, **kwargs):
    raise RuntimeError("테스트 중에는 실제 네트워크 호출이 차단됩니다")


requests.get = _blocked
requests.post = _blocked

import server  # noqa: E402  (위 준비가 끝난 뒤에 import 해야 한다)

# 패치 이전의 진짜 sqlite3.connect — 임시 DB 연결에 사용
_REAL_CONNECT = sqlite3.connect


@pytest.fixture
def db_path(tmp_path):
    """테스트마다 새 임시 SQLite 파일 경로."""
    return str(tmp_path / "test.db")


@pytest.fixture
def app_client(monkeypatch, db_path):
    """격리된 임시 DB에 연결된 FastAPI 테스트 클라이언트.

    server 안의 모든 sqlite3.connect("f1_database.db") 호출을 임시 DB로 돌린 뒤
    init_db() 로 스키마를 세운다. 세션(로그인) 상태도 매번 초기화한다.

    반환: (client, server 모듈) — 테스트에서 SESSIONS 조작 등에 server 를 직접 쓴다.
    """
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        server.sqlite3,
        "connect",
        lambda *a, **k: _REAL_CONNECT(db_path, check_same_thread=False),
    )
    server.init_db()            # 임시 DB에 테이블 생성 + 프로필 시드
    server.SESSIONS.clear()     # 로그인 세션 초기화

    # context manager 없이 생성 → startup 이벤트(백그라운드 라이브 엔진 등)를 띄우지 않는다
    client = TestClient(server.app)
    return client, server


def make_user(server, name="테스터"):
    """임시 DB에 사용자 한 명을 만들고 세션까지 붙여, (user_id, session_id) 반환."""
    conn = server.sqlite3.connect("f1_database.db")   # 패치되어 임시 DB로 간다
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (google_id, name, picture, created_at) VALUES (?, ?, ?, ?)",
        (f"gid-{name}", name, "", "2026-01-01T00:00:00"),
    )
    conn.commit()
    uid = cur.lastrowid
    conn.close()

    sid = f"session-{uid}"
    server.SESSIONS[sid] = {"id": uid, "provider": "google", "name": name, "picture": ""}
    return uid, sid
