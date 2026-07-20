"""커뮤니티: 구글 OAuth 로그인 + 게시판(CRUD) + 우승자 예측 게임.

로그인 세션(SESSIONS)·OAuth 설정(OAUTH)은 이 기능들만 쓰므로 이 모듈이 소유한다.
DB는 server.init_db()가 만든 f1_database.db를 공유한다(같은 sqlite3 모듈 사용).
server.py는 이 모듈의 router 를 등록하고 SESSIONS 를 재노출한다.

키(Client ID/Secret)는 oauth_config.json 또는 환경변수로 주입.
키가 없으면 해당 로그인은 '설정 필요' 상태로 비활성.
"""
import os
import json
import time as _time
import secrets
import sqlite3
import requests
from urllib.parse import urlencode
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

router = APIRouter()


def _load_oauth():
    cfg = {"google": {"client_id": "", "client_secret": ""},
           "base_url": "http://localhost:8000"}
    try:
        with open("oauth_config.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg["base_url"] = data.get("base_url", cfg["base_url"])
        cfg["google"].update({k: v for k, v in data.get("google", {}).items() if v})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # 환경변수가 있으면 우선
    cfg["google"]["client_id"] = os.environ.get("GOOGLE_CLIENT_ID") or cfg["google"]["client_id"]
    cfg["google"]["client_secret"] = os.environ.get("GOOGLE_CLIENT_SECRET") or cfg["google"]["client_secret"]
    return cfg


OAUTH = _load_oauth()
SESSIONS = {}          # session_id -> {"provider","name","picture"}
_oauth_states = {}     # state -> 생성시각(CSRF 방지)


def _current_user(request: Request):
    sid = request.cookies.get("session_id")
    return SESSIONS.get(sid) if sid else None


def _new_state():
    st = secrets.token_urlsafe(16)
    now = _time.time()
    _oauth_states[st] = now
    for k, v in list(_oauth_states.items()):     # 10분 지난 state 청소
        if now - v > 600:
            _oauth_states.pop(k, None)
    return st


def _finish_login(user):
    sid = secrets.token_urlsafe(24)
    SESSIONS[sid] = user
    resp = RedirectResponse("/?tab=community")
    resp.set_cookie("session_id", sid, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return resp


@router.get("/api/auth_status")
async def auth_status():
    """프런트가 어떤 로그인이 설정됐는지 파악하는 용도."""
    return {"google": bool(OAUTH["google"]["client_id"])}


@router.get("/api/me")
async def api_me(request: Request):
    u = _current_user(request)
    return {"logged_in": bool(u), **(u or {})}


@router.post("/auth/logout")
async def logout(request: Request):
    sid = request.cookies.get("session_id")
    if sid:
        SESSIONS.pop(sid, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session_id")
    return resp


# ---- 구글 OAuth ----
@router.get("/auth/google/login")
async def google_login():
    cid = OAUTH["google"]["client_id"]
    if not cid:
        return RedirectResponse("/?login_error=google_not_configured")
    params = urlencode({
        "client_id": cid,
        "redirect_uri": OAUTH["base_url"] + "/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": _new_state(),
        "prompt": "select_account",
    })
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + params)


@router.get("/auth/google/callback")
async def google_callback(code: str = None, state: str = None):
    if not code or state not in _oauth_states:
        return RedirectResponse("/?login_error=google")
    _oauth_states.pop(state, None)
    try:
        tok = requests.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": OAUTH["google"]["client_id"],
            "client_secret": OAUTH["google"]["client_secret"],
            "redirect_uri": OAUTH["base_url"] + "/auth/google/callback",
            "grant_type": "authorization_code",
        }, timeout=8).json()
        at = tok.get("access_token")
        if not at:
            return RedirectResponse("/?login_error=google_token")
        info = requests.get("https://www.googleapis.com/oauth2/v2/userinfo",
                            headers={"Authorization": f"Bearer {at}"}, timeout=8).json()
    except Exception as e:
        print("구글 로그인 오류:", e)
        return RedirectResponse("/?login_error=google")

    google_id = info.get("id")
    name = info.get("name") or info.get("email") or "구글유저"
    picture = info.get("picture", "")

    conn = sqlite3.connect("f1_database.db")
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE google_id = ?", (google_id,))
    row = cur.fetchone()
    if row:
        user_id = row[0]
    else:
        now = datetime.now(timezone.utc).isoformat()
        cur.execute("INSERT INTO users (google_id, name, picture, created_at) VALUES (?, ?, ?, ?)",
                    (google_id, name, picture, now))
        conn.commit()
        user_id = cur.lastrowid
    conn.close()

    user = {"id": user_id, "provider": "google", "name": name, "picture": picture}
    return _finish_login(user)


# ---- 게시판 (CRUD: POST 작성 / GET 목록 / PUT 수정 / DELETE 삭제) ----
def _post_owner_error(cur, post_id, uid):
    """소유자 검증. 문제 없으면 None, 아니면 에러 코드 반환."""
    cur.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,))
    row = cur.fetchone()
    if not row:
        return "not_found"
    if row[0] is None or row[0] != uid:   # user_id가 NULL인 옛 글은 아무도 못 고친다
        return "forbidden"
    return None


@router.get("/api/posts")
async def get_posts(request: Request):
    u = _current_user(request)
    uid = u.get("id") if u else None
    conn = sqlite3.connect("f1_database.db")
    cur = conn.cursor()
    cur.execute("""SELECT id, author, provider, content, created_at, user_id, updated_at
                   FROM posts ORDER BY id DESC LIMIT 100""")
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "author": r[1], "provider": r[2], "content": r[3], "created_at": r[4],
             "mine": uid is not None and r[5] == uid, "edited": bool(r[6])} for r in rows]


@router.post("/api/posts")
async def create_post(request: Request):
    u = _current_user(request)
    if not u:
        return JSONResponse({"error": "login_required"}, status_code=401)
    body = await request.json()
    content = (body.get("content") or "").strip()[:1000]
    if not content:
        return JSONResponse({"error": "empty"}, status_code=400)
    conn = sqlite3.connect("f1_database.db")
    cur = conn.cursor()
    cur.execute("INSERT INTO posts (author, provider, content, created_at, user_id) VALUES (?, ?, ?, ?, ?)",
                (u["name"], u["provider"], content, datetime.now(timezone.utc).isoformat(), u.get("id")))
    conn.commit()
    post_id = cur.lastrowid
    conn.close()
    return {"ok": True, "id": post_id}


@router.put("/api/posts/{post_id}")
async def update_post(post_id: int, request: Request):
    u = _current_user(request)
    if not u:
        return JSONResponse({"error": "login_required"}, status_code=401)
    body = await request.json()
    content = (body.get("content") or "").strip()[:1000]
    if not content:
        return JSONResponse({"error": "empty"}, status_code=400)
    conn = sqlite3.connect("f1_database.db")
    cur = conn.cursor()
    err = _post_owner_error(cur, post_id, u.get("id"))
    if err:
        conn.close()
        return JSONResponse({"error": err}, status_code=404 if err == "not_found" else 403)
    cur.execute("UPDATE posts SET content = ?, updated_at = ? WHERE id = ?",
                (content, datetime.now(timezone.utc).isoformat(), post_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/api/posts/{post_id}")
async def delete_post(post_id: int, request: Request):
    u = _current_user(request)
    if not u:
        return JSONResponse({"error": "login_required"}, status_code=401)
    conn = sqlite3.connect("f1_database.db")
    cur = conn.cursor()
    err = _post_owner_error(cur, post_id, u.get("id"))
    if err:
        conn.close()
        return JSONResponse({"error": err}, status_code=404 if err == "not_found" else 403)
    cur.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---- 예측 게임 ----
@router.get("/api/predictions/{year}/{round}")
async def get_predictions(year: int, round: int):
    """해당 레이스의 예측 현황 조회"""
    conn = sqlite3.connect("f1_database.db")
    cur = conn.cursor()
    cur.execute("""
        SELECT predicted_winner, COUNT(*) as count
        FROM predictions
        WHERE year = ? AND round = ?
        GROUP BY predicted_winner
        ORDER BY count DESC
    """, (year, round))
    rows = cur.fetchall()
    conn.close()
    return [{"driver": r[0], "votes": r[1]} for r in rows]


@router.post("/api/predictions/{year}/{round}")
async def save_prediction(year: int, round: int, request: Request):
    """현재 사용자의 예측 저장"""
    u = _current_user(request)
    if not u:
        return JSONResponse({"error": "login_required"}, status_code=401)

    body = await request.json()
    driver_name = (body.get("driver_name") or "").strip()
    if not driver_name:
        return JSONResponse({"error": "empty"}, status_code=400)

    conn = sqlite3.connect("f1_database.db")
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    cur.execute("""
        INSERT INTO predictions (user_id, year, round, predicted_winner, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, year, round) DO UPDATE SET
            predicted_winner = excluded.predicted_winner,
            updated_at = excluded.updated_at
    """, (u["id"], year, round, driver_name, now, now))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/api/my-predictions")
async def get_my_predictions(request: Request):
    """현재 사용자의 모든 예측 조회"""
    u = _current_user(request)
    if not u:
        return JSONResponse({"error": "login_required"}, status_code=401)

    conn = sqlite3.connect("f1_database.db")
    cur = conn.cursor()
    cur.execute("""
        SELECT year, round, predicted_winner, updated_at
        FROM predictions
        WHERE user_id = ?
        ORDER BY year DESC, round DESC
    """, (u["id"],))
    rows = cur.fetchall()
    conn.close()
    return [{"year": r[0], "round": r[1], "driver": r[2], "updated_at": r[3]} for r in rows]
