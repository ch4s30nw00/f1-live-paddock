"""API 통합 테스트 (격리된 임시 DB + FastAPI TestClient).

게시판 CRUD의 '소유자 검증'과 예측 게임의 '업서트(재투표)' 처럼, 이 프로젝트의
핵심 서버 로직이 실제 HTTP 요청으로 올바르게 동작하는지 확인한다.
"""
from conftest import make_user


# --- 순위 API 스모크 테스트 --------------------------------------------------
def test_constructor_standings_리스트_반환(app_client):
    client, _ = app_client
    r = client.get("/api/standings/constructors")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# --- 게시판: 인증 ------------------------------------------------------------
def test_글작성_로그인필요(app_client):
    client, _ = app_client
    r = client.post("/api/posts", json={"content": "안녕하세요"})
    assert r.status_code == 401
    assert r.json()["error"] == "login_required"


def test_빈내용은_거부(app_client):
    client, server = app_client
    _, sid = make_user(server)
    client.cookies.set("session_id", sid)
    r = client.post("/api/posts", json={"content": "   "})   # 공백뿐
    assert r.status_code == 400
    assert r.json()["error"] == "empty"


# --- 게시판: 작성 & 조회 -----------------------------------------------------
def test_작성한_글이_목록에_나온다(app_client):
    client, server = app_client
    _, sid = make_user(server, "글쓴이")
    client.cookies.set("session_id", sid)

    r = client.post("/api/posts", json={"content": "첫 글입니다"})
    assert r.status_code == 200
    post_id = r.json()["id"]

    r = client.get("/api/posts")
    assert r.status_code == 200
    posts = r.json()
    assert len(posts) == 1
    assert posts[0]["id"] == post_id
    assert posts[0]["content"] == "첫 글입니다"
    assert posts[0]["author"] == "글쓴이"
    assert posts[0]["mine"] is True        # 작성자 본인이 조회하면 mine=True
    assert posts[0]["edited"] is False


def test_로그인안하면_mine은_false(app_client):
    client, server = app_client
    _, sid = make_user(server)
    client.cookies.set("session_id", sid)
    client.post("/api/posts", json={"content": "공개 글"})

    client.cookies.clear()                 # 로그아웃 상태로 목록 조회
    posts = client.get("/api/posts").json()
    assert posts[0]["mine"] is False


# --- 게시판: 수정 (소유자만) -------------------------------------------------
def test_작성자는_자기글_수정가능(app_client):
    client, server = app_client
    _, sid = make_user(server)
    client.cookies.set("session_id", sid)
    post_id = client.post("/api/posts", json={"content": "원본"}).json()["id"]

    r = client.put(f"/api/posts/{post_id}", json={"content": "수정됨"})
    assert r.status_code == 200

    posts = client.get("/api/posts").json()
    assert posts[0]["content"] == "수정됨"
    assert posts[0]["edited"] is True      # updated_at 이 채워지면 edited=True


def test_남의글은_수정_불가_403(app_client):
    client, server = app_client
    # 사용자 A가 글 작성
    _, sid_a = make_user(server, "작성자A")
    client.cookies.set("session_id", sid_a)
    post_id = client.post("/api/posts", json={"content": "A의 글"}).json()["id"]

    # 사용자 B가 수정 시도 → 403
    _, sid_b = make_user(server, "침입자B")
    client.cookies.set("session_id", sid_b)
    r = client.put(f"/api/posts/{post_id}", json={"content": "해킹"})
    assert r.status_code == 403
    assert r.json()["error"] == "forbidden"

    # 원본은 그대로여야 한다
    client.cookies.set("session_id", sid_a)
    assert client.get("/api/posts").json()[0]["content"] == "A의 글"


def test_없는글_수정은_404(app_client):
    client, server = app_client
    _, sid = make_user(server)
    client.cookies.set("session_id", sid)
    r = client.put("/api/posts/99999", json={"content": "유령"})
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


# --- 게시판: 삭제 (소유자만) -------------------------------------------------
def test_작성자는_자기글_삭제가능(app_client):
    client, server = app_client
    _, sid = make_user(server)
    client.cookies.set("session_id", sid)
    post_id = client.post("/api/posts", json={"content": "지울 글"}).json()["id"]

    r = client.delete(f"/api/posts/{post_id}")
    assert r.status_code == 200
    assert client.get("/api/posts").json() == []      # 목록에서 사라짐


def test_남의글은_삭제_불가_403(app_client):
    client, server = app_client
    _, sid_a = make_user(server, "작성자A")
    client.cookies.set("session_id", sid_a)
    post_id = client.post("/api/posts", json={"content": "A의 글"}).json()["id"]

    _, sid_b = make_user(server, "침입자B")
    client.cookies.set("session_id", sid_b)
    r = client.delete(f"/api/posts/{post_id}")
    assert r.status_code == 403

    client.cookies.set("session_id", sid_a)
    assert len(client.get("/api/posts").json()) == 1   # 여전히 존재


# --- 예측 게임: 저장 & 업서트(재투표) ---------------------------------------
def test_예측_저장하고_집계된다(app_client):
    client, server = app_client
    _, sid = make_user(server)
    client.cookies.set("session_id", sid)

    r = client.post("/api/predictions/2026/1", json={"driver_name": "Lando Norris"})
    assert r.status_code == 200

    tally = client.get("/api/predictions/2026/1").json()
    assert tally == [{"driver": "Lando Norris", "votes": 1}]


def test_재투표는_새_행이_아니라_갱신(app_client):
    """같은 레이스에 다시 투표하면 표가 늘지 않고 예측만 바뀌어야 한다 (UNIQUE 업서트)."""
    client, server = app_client
    _, sid = make_user(server)
    client.cookies.set("session_id", sid)

    client.post("/api/predictions/2026/1", json={"driver_name": "Max Verstappen"})
    client.post("/api/predictions/2026/1", json={"driver_name": "Lando Norris"})  # 마음 바꿈

    tally = client.get("/api/predictions/2026/1").json()
    assert tally == [{"driver": "Lando Norris", "votes": 1}]   # 총 1표, 최신 예측만

    mine = client.get("/api/my-predictions").json()
    assert len(mine) == 1
    assert mine[0]["driver"] == "Lando Norris"


def test_예측_로그인필요(app_client):
    client, _ = app_client
    r = client.post("/api/predictions/2026/1", json={"driver_name": "Lando Norris"})
    assert r.status_code == 401
