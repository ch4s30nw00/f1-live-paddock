import sys
import json
import asyncio
import sqlite3
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Windows 콘솔(cp949)이 못 찍는 문자가 로그에 섞여도 태스크가 죽지 않게 한다
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


app = FastAPI()

# 드라이버 사진(.webp) 등 정적 파일 서빙
app.mount("/static", StaticFiles(directory="static"), name="static")


# 데이터베이스 초기화: 테이블 생성 + 프로필 시드 데이터 적재
def init_db():
    conn = sqlite3.connect("f1_database.db")
    cursor = conn.cursor()

    # constructors: 컨스트럭터 순위 (외부 API가 주기적으로 동기화)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS constructors (
        team_id TEXT PRIMARY KEY, team_name TEXT NOT NULL, points INTEGER DEFAULT 0
    )""")
    
    # drivers: 드라이버 순위·점수 (외부 API가 주기적으로 동기화)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS drivers (
        driver_number INTEGER PRIMARY KEY, driver_name TEXT NOT NULL, team_id TEXT, points INTEGER DEFAULT 0,
        FOREIGN KEY (team_id) REFERENCES constructors(team_id)
    )""")
    
    # 과거 스키마(이름 기반 PK)로 만들어진 테이블이 남아있을 수 있어 매번 재생성한다
    cursor.execute("DROP TABLE IF EXISTS driver_profiles")

    # driver_profiles: 드라이버 프로필 (사진·머신·소개, driver_number가 기본키)
    cursor.execute("""
    CREATE TABLE driver_profiles (
        driver_number INTEGER PRIMARY KEY,
        image_url TEXT NOT NULL,
        car_model TEXT NOT NULL,
        career_bio TEXT NOT NULL,
        FOREIGN KEY (driver_number) REFERENCES drivers(driver_number)
    )""")
    
    # posts: 커뮤니티 게시판 (사용자 데이터이므로 드롭하지 않는다)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        author TEXT NOT NULL,
        provider TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        user_id INTEGER,
        updated_at TEXT
    )""")
    # 과거 스키마로 만들어진 posts에 새 컬럼 추가 (user_id: 소유자 판별, updated_at: 수정 표시)
    # 옛 글은 user_id가 NULL로 남아 수정·삭제 불가로 취급된다
    for col in ("user_id INTEGER", "updated_at TEXT"):
        try:
            cursor.execute(f"ALTER TABLE posts ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # 이미 있는 컬럼

    # users: OAuth 로그인 사용자
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_id TEXT UNIQUE,
        name TEXT NOT NULL,
        picture TEXT,
        created_at TEXT NOT NULL
    )""")

    # predictions: 예측 게임 (레이스별 우승자 예측, 사용자당 1개)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        year INTEGER NOT NULL,
        round INTEGER NOT NULL,
        predicted_winner TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id),
        UNIQUE(user_id, year, round)
    )""")

    # 프로필 시드 데이터 — driver_number는 외부 API의 공식 카 넘버와 일치해야 한다
    cursor.execute("SELECT COUNT(*) FROM driver_profiles")
    if cursor.fetchone()[0] == 0:
        custom_profiles = [
            # 챔피언십 컨텐더
            (1, "/static/images/norris.webp", "McLaren MCL38", "👑 현재 2026 챔피언십 1위 질주 중! 맥라렌의 새로운 시대"),
            (3, "/static/images/verstappen.webp", "Red Bull Racing RB22", "World Champion 3회 / 현재 역전 추격 중"),
            (6, "/static/images/hadjar.webp", "Red Bull Racing RB22", "레드불 시트를 꿰찬 막강한 잠재력의 신성 레이서"),
            (16, "/static/images/leclerc.webp", "Ferrari SF-26", "모나코의 왕자이자 해밀턴과 함께하는 페라리 에이스"),
            (44, "/static/images/hamilton.webp", "Ferrari SF-26", "스쿠데리아 페라리로 이적한 7회 챔피언 레전드"),
            (81, "/static/images/piastri.webp", "McLaren MCL38", "무서운 성장세로 챔피언십을 위협하는 맥라렌의 특급 영건"),

            # 나머지 그리드 드라이버
            (63, "/static/images/russell.webp", "Mercedes-AMG F1 W17", "메르세데스의 새로운 퍼스트 드라이버, 실버 애로우의 스피드 마스터"),
            (12, "/static/images/antonelli.webp", "Mercedes-AMG F1 W17", "이탈리아의 초신성 루키, 실버 애로우의 미래를 짊어진 주역"),
            (23, "/static/images/albon.webp", "Williams FW48", "윌리엄스의 중심을 잡아주는 든든한 에이스 드라이버"),
            (55, "/static/images/sainz.webp", "Williams FW48", "윌리엄스로 둥지를 튼 부드러운 운영의 마술사, 스페인 스피드스터"),
            (14, "/static/images/alonso.webp", "Aston Martin AMR26", "패독의 살아있는 전설이자 아스톤 마틴의 노련한 심장"),
            (18, "/static/images/stroll.webp", "Aston Martin AMR26", "아스톤 마틴과 오랜 시간 함께해 온 그리드의 베테랑"),
            (31, "/static/images/ocon.webp", "Haas VF-26", "하스 F1 팀으로 이적하여 새로운 도약을 노리는 프랑스 드라이버"),
            (87, "/static/images/bearman.webp", "Haas VF-26", "페라리 주니어 출신, 하스에서 본격적인 풀 시즌을 시작하는 영건"),
            (10, "/static/images/gasly.webp", "Alpine A226", "알핀의 에이스이자 승부사, 프랑스의 스피드 자존심"),
            (43, "/static/images/colapinto.webp", "Alpine A226", "알핀 시트를 확보하며 그리드에 안착한 아르헨티나의 신성"),
            (30, "/static/images/lawson.webp", "RB VCARB 02", "RB의 정식 시트를 께찬 뉴질랜드 출신의 무서운 실력파 레이서"),
            (41, "/static/images/lindblad.webp", "RB VCARB 02", "RB 레이싱 가문에 합류한 주목받는 특급 루키 드라이버"),
            (27, "/static/images/hulkenberg.webp", "Audi F1-26", "새롭게 합류한 아우디 F1 프로젝트의 든든한 선봉장"),
            (5, "/static/images/bortoleto.webp", "Audi F1-26", "아우디의 미래를 책임질 브라질 출신의 2026 기대주 초신성"),
            (77, "/static/images/bottas.webp", "Cadillac F1 Team", "새로운 캐딜락 팀의 중심을 잡아줄 노련한 베테랑 레이서"),
            (11, "/static/images/perez.webp", "Cadillac F1 Team", "캐딜락 F1 팀으로 이적하여 새로운 도전을 시작하는 멕시칸 미니스터")
        ]
        cursor.executemany("INSERT INTO driver_profiles VALUES (?, ?, ?, ?)", custom_profiles)
        print("driver_profiles 시드 데이터 적재 완료")

    conn.commit()
    conn.close()
    sync_standings()


# 드라이버/컨스트럭터 순위를 Jolpica에서 받아 DB에 반영 (시작 시 + 주기적)
def sync_standings():
    try:
        url = "https://api.jolpi.ca/ergast/f1/2026/driverStandings.json"
        response = requests.get(url, timeout=8)
        if response.status_code != 200:
            print("순위 API 응답 실패, 기존 데이터 유지")
            return False

        data = response.json()
        standings_list = data["MRData"]["StandingsTable"]["StandingsLists"][0]["DriverStandings"]
        if not standings_list:
            return False

        conn = sqlite3.connect("f1_database.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM drivers")
        cursor.execute("DELETE FROM constructors")

        for item in standings_list:
            points = int(item["points"])
            driver_info = item["Driver"]
            d_number = int(driver_info.get("permanentNumber", 0))
            d_name = f"{driver_info['givenName']} {driver_info['familyName']}"

            constructor_info = item["Constructors"][0]
            c_id = constructor_info["constructorId"].upper()
            c_name = constructor_info["name"]

            # 컨스트럭터 점수 = 소속 드라이버 점수의 '합계'.
            # 첫 드라이버는 팀 행을 만들고, 같은 팀의 두 번째 드라이버는 점수를 더한다.
            cursor.execute("""
                INSERT INTO constructors (team_id, team_name, points) VALUES (?, ?, ?)
                ON CONFLICT(team_id) DO UPDATE SET points = points + excluded.points
            """, (c_id, c_name, points))
            cursor.execute("INSERT INTO drivers VALUES (?, ?, ?, ?)", (d_number, d_name, c_id, points))

        conn.commit()
        conn.close()
        print("순위 동기화 완료 (Jolpica)")
        return True
    except Exception as e:
        print(f"순위 동기화 실패: {e}, 기존 데이터 유지")
        return False


async def _standings_refresher():
    """15분마다 순위를 다시 받아온다 → 경기 끝나고 Jolpica에 결과가 올라오면 자동 반영."""
    while True:
        await asyncio.sleep(900)
        await asyncio.to_thread(sync_standings)


@app.on_event("startup")
async def _start_standings_refresher():
    asyncio.create_task(_standings_refresher())


# 서버 시작 시 DB 초기화 + 첫 순위 동기화
init_db()


# 홈 화면
@app.get("/")
async def get_homepage():
    return FileResponse("index.html")


# 드라이버 순위 조회 (프로필은 driver_number 기준 LEFT JOIN)
@app.get("/api/standings/drivers")
async def get_driver_standings():
    conn = sqlite3.connect("f1_database.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            d.driver_number, 
            d.driver_name, 
            c.team_name, 
            d.points,
            COALESCE(p.image_url, '/static/images/default.webp'),
            COALESCE(p.car_model, '2026 규정 머신'),
            COALESCE(p.career_bio, '2026 시즌 활약 중인 그리드 레이서')
        FROM drivers d
        JOIN constructors c ON d.team_id = c.team_id
        LEFT JOIN driver_profiles p ON d.driver_number = p.driver_number
        ORDER BY d.points DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "rank": idx + 1, "number": r[0], "name": r[1], "team": r[2], "points": r[3],
            "image_url": r[4], "car_model": r[5], "career_bio": r[6]
        }
        for idx, r in enumerate(rows)
    ]


# 컨스트럭터(팀) 순위 조회
@app.get("/api/standings/constructors")
async def get_constructor_standings():
    conn = sqlite3.connect("f1_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT team_name, points FROM constructors ORDER BY points DESC")
    rows = cursor.fetchall()
    conn.close()
    return [{"rank": idx + 1, "team": r[0], "points": r[1]} for idx, r in enumerate(rows)]


# ============================================================================
# 라이브 타이밍 엔진
#   하나의 백그라운드 태스크가: 세션 자동감지 → 라이브 폴링 → 전체 보드 방송
#     → 세션 종료 후 OpenF1 백필로 다시보기 생성(최근 3개 보관)
#     → 세션 없으면 폴백 리플레이
# ============================================================================
import os
import re
import time as _time
import xml.etree.ElementTree as ET
import concurrent.futures
from datetime import timedelta

from openf1 import _get, _utcnow, _stamp, _epoch   # OpenF1 호출·시간 헬퍼(공용)

REC_DIR = "recordings"          # 녹화 저장 폴더

# F1 공식 SignalR 피드 (FastF1) — 라이브 중 OpenF1 401 우회용
try:
    from f1feed import FEED
except Exception as _e:
    print(f"f1feed 로드 실패(FastF1 미설치?): {_e}")
    FEED = None

# 세션 종료 후 다시보기 백필용
try:
    from build_recordings import backfill_race
except Exception as _e:
    print(f"build_recordings 로드 실패: {_e}")
    backfill_race = None

LIVE_POLL_SEC = 3.0             # 라이브일 때 폴링/방송 주기(초)
IDLE_CHECK_SEC = 20.0          # 유휴(리플레이) 중 라이브 세션 확인 주기(초)
STALE_SEC = 300.0             # 이만큼 데이터가 끊기면 세션 종료로 판단(연기·연장 흡수)
KEEP_RECORDINGS = 3             # 보관할 최근 녹화 개수
BACKFILL_DELAY_SEC = 600        # 세션 종료 후 첫 백필 시도까지 대기(초)
BACKFILL_RETRY_SEC = 600        # OpenF1에 데이터가 아직 없을 때 재시도 간격(초)
BACKFILL_MAX_TRIES = 12         # 재시도 상한 — 약 2시간 기다려도 없으면 포기

os.makedirs(REC_DIR, exist_ok=True)

# --- 서버 공유 상태 ----------------------------------------------------------
CLIENTS = set()                 # 접속 중인 웹소켓들
STATE = {
    "mode": "replay",           # "live" | "replay"
    "session": "",
    "session_key": None,
    "session_start": None,      # 라이브 세션 시작 epoch(경과시간 계산용)
    "drivers": {},              # num(str) -> {acronym, name, team, color}
    "cars": [],                 # 마지막 보드(신규 접속자에게 즉시 전송)
}

# 최후의 폴백: 바레인 2024 리플레이
try:
    with open("live_replay.json", "r", encoding="utf-8") as f:
        FALLBACK = json.load(f)
except FileNotFoundError:
    FALLBACK = None


# --- 포맷 헬퍼 (OpenF1 호출·시간 헬퍼는 openf1.py 로 분리) --------------------
def _fmt_gap(g):
    if g is None: return "-"
    if isinstance(g, str): return g
    if g == 0: return "LEADER"
    return f"+{g:.3f}"

def _fmt_int(v, pos):
    if pos == 1 or v is None: return "—"
    if isinstance(v, str): return v
    return f"+{v:.3f}"

def _latest_by(rows, key="driver_number", datekey="date"):
    """시간이 섞인 행 목록에서 드라이버별 '가장 최근' 행만 남깁니다."""
    out = {}
    for r in rows:
        k = r.get(key)
        if k is None:
            continue
        if k not in out or (r.get(datekey, "") > out[k].get(datekey, "")):
            out[k] = r
    return out

def _sector_colors(laps):
    """완주한 랩들을 시간순으로 훑어 섹터별 퍼플/그린/옐로 판정 -> {num: (s1,s2,s3)}."""
    events = []
    for lp in laps:
        n = lp.get("driver_number"); ds = lp.get("date_start")
        if n is None or not ds:
            continue
        t0 = _epoch(ds)
        s1 = lp.get("duration_sector_1"); s2 = lp.get("duration_sector_2"); s3 = lp.get("duration_sector_3")
        if s1: events.append((t0 + s1, n, 0, s1))
        if s1 and s2: events.append((t0 + s1 + s2, n, 1, s2))
        if lp.get("lap_duration"): events.append((t0 + lp["lap_duration"], n, 2, s3 or 0))
    events.sort(key=lambda e: e[0])
    sb = [None, None, None]; pb = {}; st = {}
    for t, n, s, dur in events:
        if not dur or dur <= 0:
            continue
        pb.setdefault(n, [None, None, None]); st.setdefault(n, [None, None, None])
        if sb[s] is None or dur < sb[s]:
            col = "purple"; sb[s] = dur; pb[n][s] = dur
        elif pb[n][s] is None or dur <= pb[n][s]:
            col = "green"; pb[n][s] = dur
        else:
            col = "yellow"
        st[n][s] = col
    return {n: tuple(v) for n, v in st.items()}


# --- 세션 감지 & 라이브 보드 조립 --------------------------------------------
def _fetch_latest_session():
    s = _get("sessions", {"session_key": "latest"})
    return (s[0] if isinstance(s, list) else s) if s else None

def _load_drivers(sk):
    out = {}
    for d in _get("drivers", {"session_key": sk}):
        n = d.get("driver_number")
        if n is None:
            continue
        out[str(n)] = {
            "acronym": d.get("name_acronym") or str(n),
            "name": d.get("full_name") or str(n),
            "team": d.get("team_name") or "",
            "color": d.get("team_colour") or "888888",
        }
    return out

_laps_cache = {"t": 0.0, "colors": {}}   # 섹터색은 15초마다만 갱신
_prev_cars = {}                          # 샘플 빠진 드라이버는 직전 값 유지

def _build_live_board(sk, drivers):
    """(cars, fresh) 반환. fresh=최근 표본이 실제로 들어왔는지(라이브 여부 판단용)."""
    now = _utcnow()
    hi = _stamp(now + timedelta(seconds=2))   # 상한 시각(살짝 여유) — 미래/주차샘플 배제
    car = _get("car_data", {"session_key": sk}, date_after=_stamp(now - timedelta(seconds=15)), date_before=hi)
    iv  = _get("intervals", {"session_key": sk}, date_after=_stamp(now - timedelta(seconds=45)), date_before=hi)
    pos = _get("position", {"session_key": sk}, date_after=_stamp(now - timedelta(minutes=10)), date_before=hi)
    loc = _get("location", {"session_key": sk}, date_after=_stamp(now - timedelta(seconds=15)), date_before=hi)
    fresh = bool(car) or bool(iv)
    lc = _latest_by(car); li = _latest_by(iv); lp = _latest_by(pos); ll = _latest_by(loc)

    if _time.time() - _laps_cache["t"] > 15:
        try:
            _laps_cache["colors"] = _sector_colors(_get("laps", {"session_key": sk}))
            _laps_cache["t"] = _time.time()
        except Exception:
            pass
    colors = _laps_cache["colors"]

    cars = []
    for numstr in drivers:
        n = int(numstr)
        prev = _prev_cars.get(n, {})
        cd = lc.get(n); ivv = li.get(n); pp = lp.get(n)
        posv = pp["position"] if pp else prev.get("pos")
        sc = colors.get(n, (None, None, None))
        # 서킷 맵 좌표 — (0,0)은 '수신 없음' 표본이라 직전 값 유지
        ld = ll.get(n)
        if ld and not (ld.get("x") == 0 and ld.get("y") == 0):
            x, y = ld.get("x"), ld.get("y")
        else:
            x, y = prev.get("x"), prev.get("y")
        cars.append({
            "num": n,
            "pos": posv,
            "speed": cd["speed"] if cd else prev.get("speed", 0),
            "rpm": cd["rpm"] if cd else prev.get("rpm", 0),
            "gear": cd.get("n_gear") if cd else prev.get("gear", 0),
            "gap": _fmt_gap(ivv["gap_to_leader"]) if ivv else prev.get("gap", "-"),
            "int": _fmt_int(ivv["interval"], posv or 99) if ivv else prev.get("int", "—"),
            "s1": sc[0], "s2": sc[1], "s3": sc[2],
            "x": x, "y": y,
        })
    cars.sort(key=lambda c: c["pos"] if c["pos"] else 99)
    _prev_cars.clear(); _prev_cars.update({c["num"]: c for c in cars})
    return cars, fresh


# --- 다시보기 백필 (라이브 녹화 대체) -----------------------------------------
# 라이브 중 방송 프레임을 그대로 저장하는 방식은 제거했다. 인증 없는 공식
# 피드는 순위 외 데이터(인터벌·텔레메트리)가 빠질 수 있어 녹화 품질이 낮고,
# 세션이 끝나면 OpenF1에 풀 데이터가 올라오므로 종료 후 내려받는 쪽이 항상
# 품질이 좋다. 데이터가 올라올 때까지 일정 간격으로 재시도한다.
_replay_refresh = {"flag": False}   # 백필 완료 → 엔진이 최신 다시보기를 다시 고르게 함

async def _backfill_after_session(session_key, name):
    """세션 종료 후 OpenF1 과거 데이터로 다시보기 파일을 만든다(본선 레이스만)."""
    if backfill_race is None or not session_key:
        return
    if "race" not in name.lower():      # 다시보기 슬롯은 본선 레이스만 채운다
        return
    label = name.split("·")[0].strip() or str(session_key)
    print(f"백필 예약: {label} (key {session_key}), OpenF1 데이터 대기")
    await asyncio.sleep(BACKFILL_DELAY_SEC)
    for attempt in range(1, BACKFILL_MAX_TRIES + 1):
        try:
            path = await asyncio.to_thread(backfill_race, session_key, label)
        except Exception as e:
            print(f"백필 시도 {attempt}/{BACKFILL_MAX_TRIES} 실패: {e}")
            path = None
        if path:
            print(f"백필 완료: {path}")
            _prune_recordings()
            _replay_refresh["flag"] = True
            return
        await asyncio.sleep(BACKFILL_RETRY_SEC)
    print(f"백필 포기 (key {session_key}): OpenF1에 데이터가 올라오지 않음")

def _prune_recordings():
    files = [os.path.join(REC_DIR, f) for f in os.listdir(REC_DIR) if f.endswith(".json")]
    files.sort(key=os.path.getmtime, reverse=True)
    for old in files[KEEP_RECORDINGS:]:
        try:
            os.remove(old); print(f"오래된 녹화 삭제: {old}")
        except OSError:
            pass

def _latest_recording():
    files = [os.path.join(REC_DIR, f) for f in os.listdir(REC_DIR) if f.endswith(".json")]
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    try:
        with open(files[0], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# --- 방송 & 엔진 루프 --------------------------------------------------------
async def _broadcast(msg):
    for ws in list(CLIENTS):
        try:
            await ws.send_json(msg)
        except Exception:
            CLIENTS.discard(ws)

async def _send_meta(ws=None):
    msg = {"type": "meta", "session": STATE["session"],
           "drivers": STATE["drivers"], "live": STATE["mode"] == "live"}
    if ws is not None:
        await ws.send_json(msg)
    else:
        await _broadcast(msg)

async def live_engine():
    """세션 감지는 '스케줄'이 아니라 '데이터 흐름'으로 판단한다.
    - 최근 데이터가 들어오면 LIVE(폴링·방송).
    - STALE_SEC 만큼 끊기면 세션 종료로 보고 백필을 예약한 뒤 다시보기로 전환.
    이 방식이라 경기 연기·연장·red flag 로 공식 시각을 넘겨도 라이브가 이어진다."""
    print("라이브 엔진 시작: 데이터 흐름 기반 세션 감지")
    replay = None
    replay_i = 0
    last_check = 0.0     # 유휴 중 마지막으로 라이브를 확인한 시각
    last_fresh = 0.0     # 마지막으로 신선한 데이터를 받은 시각

    while True:
        nowt = _time.time()

        # ============================ LIVE ============================
        if STATE["mode"] == "live":
            sk, name = STATE["session_key"], STATE["session"]
            if STATE.get("source") == "feed" and FEED:
                try:
                    cars, fresh = FEED.build_board()
                except Exception as e:
                    print("피드 보드 조립 오류:", e)
                    cars, fresh = None, False
            else:
                try:
                    cars, fresh = await asyncio.to_thread(_build_live_board, sk, STATE["drivers"])
                except Exception as e:
                    print("라이브 폴링 오류:", e)
                    cars, fresh = None, False

            if fresh:
                last_fresh = nowt
                # 피드 소스는 드라이버 명단(DriverList)이 뒤늦게 채워질 수 있음
                if STATE.get("source") == "feed" and FEED:
                    dm = FEED.drivers_meta()
                    if dm and dm != STATE["drivers"]:
                        STATE["drivers"] = dm
                        await _send_meta()
                STATE["cars"] = cars
                st = STATE.get("session_start")
                elapsed = max(0.0, _utcnow().timestamp() - st) if st else None
                await _broadcast({"type": "frame", "live": True, "session": name,
                                  "elapsed": elapsed, "cars": cars})
                await asyncio.sleep(LIVE_POLL_SEC)
            elif nowt - last_fresh > STALE_SEC:
                # 데이터가 오래 끊김 → 세션 종료로 판단, 백필 예약 후 다시보기로
                print(f"{name}: {int(STALE_SEC)}s간 데이터 없음 → 세션 종료, 백필 예약")
                asyncio.create_task(_backfill_after_session(sk, name))
                STATE["mode"] = "replay"; replay = None; last_check = 0.0
            else:
                await asyncio.sleep(LIVE_POLL_SEC)   # 일시적 끊김(피트·중계 지연) — 계속 대기

        # ========================= 유휴/리플레이 =========================
        else:
            # 0) F1 공식 피드(FastF1)가 신선한 데이터를 받고 있으면 즉시 LIVE 진입
            if FEED and FEED.is_fresh():
                w = FEED.window or {}
                sk = w.get("session_key") or int(nowt)
                name = FEED.session_name()
                STATE.update(mode="live", session_key=sk, session=name, source="feed")
                try:
                    STATE["session_start"] = _epoch(w["start"]) if w.get("start") else None
                except Exception:
                    STATE["session_start"] = None
                STATE["drivers"] = FEED.drivers_meta()
                _prev_cars.clear()
                last_fresh = nowt
                await _send_meta()
                print(f"LIVE 전환(F1 공식 피드): {name}")
                continue

            # 폴백 재생 중에는 과도한 호출을 막으려 주기적으로만 라이브를 확인
            if nowt - last_check > IDLE_CHECK_SEC:
                last_check = nowt
                try:
                    session = await asyncio.to_thread(_fetch_latest_session)
                    if session:
                        sk = session["session_key"]
                        probe = await asyncio.to_thread(
                            _get, "car_data", {"session_key": sk},
                            _stamp(_utcnow() - timedelta(seconds=90)))
                        if probe:      # 최근 90초 내 데이터 존재 → 라이브 진입
                            name = f'{session.get("country_name","")} · {session.get("session_name","")}'
                            STATE.update(mode="live", session_key=sk, session=name, source="openf1")
                            try:
                                STATE["session_start"] = _epoch(session["date_start"])
                            except Exception:
                                STATE["session_start"] = None
                            STATE["drivers"] = await asyncio.to_thread(_load_drivers, sk)
                            _prev_cars.clear()
                            last_fresh = nowt
                            await _send_meta()
                            print(f"LIVE 전환: {name} (key {sk})")
                            continue
                except Exception as e:
                    print("세션 확인 오류:", e)

            # 백필이 새 다시보기를 만들었으면 최신 녹화로 다시 고른다
            if _replay_refresh["flag"]:
                _replay_refresh["flag"] = False
                replay = None

            if replay is None:
                replay = _latest_recording() or FALLBACK
                replay_i = 0
                STATE["mode"] = "replay"
                STATE["session"] = (replay["session"] + " (다시보기)") if replay else "리플레이 없음"
                STATE["drivers"] = replay["drivers"] if replay else {}
                await _send_meta()
                print(f"REPLAY 모드: {STATE['session']}")

            if replay and replay.get("frames"):
                frame = replay["frames"][replay_i % len(replay["frames"])]
                replay_i += 1
                STATE["cars"] = frame["cars"]
                await _broadcast({"type": "frame", "live": False,
                                  "session": STATE["session"], "cars": frame["cars"]})
                await asyncio.sleep(replay.get("step", 0.5))
            else:
                await asyncio.sleep(3.0)


async def _feed_watcher():
    """세션 시작 5분 전 ~ 종료 30분 후 사이에만 F1 공식 피드 수신기를 돌린다."""
    if FEED is None:
        return
    while True:
        try:
            now = _utcnow().timestamp()
            win = None
            for s in SCHEDULE:
                if _epoch(s["start"]) - 300 <= now <= _epoch(s["end"]) + 1800:
                    win = s
                    break
            if win:
                FEED.ensure_started(win)
            else:
                FEED.ensure_stopped()
        except Exception as e:
            print("피드 워처 오류:", e)
        await asyncio.sleep(30)


@app.get("/api/feed_status")
async def feed_status():
    """F1 공식 피드 수신 상태 (라이브 테스트/디버깅용)."""
    if FEED is None:
        return {"error": "f1feed 모듈이 로드되지 않음"}
    return FEED.status()


@app.on_event("startup")
async def _start_engine():
    asyncio.create_task(live_engine())
    asyncio.create_task(_feed_watcher())


@app.websocket("/ws/f1")
async def ws_f1(websocket: WebSocket):
    await websocket.accept()
    CLIENTS.add(websocket)
    print(f"WS 접속 (총 {len(CLIENTS)}명)")
    try:
        await _send_meta(websocket)                 # 접속 즉시 현재 메타 + 마지막 보드
        if STATE["cars"]:
            await websocket.send_json({"type": "frame", "live": STATE["mode"] == "live",
                                       "session": STATE["session"], "cars": STATE["cars"]})
        while True:
            await websocket.receive_text()          # 클라는 안 보냄 → 끊길 때까지 대기
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        CLIENTS.discard(websocket)
        print(f"WS 접속 종료 (총 {len(CLIENTS)}명)")


# ============================================================================
# 2026 시즌 일정 API (일정 탭 카운트다운용) — 시작 시 1회 로드 + 파일 캐시
#     라이브 세션 중엔 OpenF1이 401로 막히므로, 성공 시 파일로 캐시해두고
#     실패하면 캐시를 쓴다 → 라이브 중 재시작해도 일정 탭이 안 깨진다.
# ============================================================================
SCHED_CACHE = "schedule_2026.json"
try:
    SCHEDULE = sorted(
        [
            {
                "session_key": s["session_key"],
                "name": s.get("session_name", ""),
                "country": s.get("country_name", ""),
                "circuit": s.get("circuit_short_name", ""),
                "start": s.get("date_start"),
                "end": s.get("date_end"),
            }
            for s in _get("sessions", {"year": 2026})
            if s.get("date_start") and s.get("date_end")
        ],
        key=lambda x: x["start"],
    )
    if not SCHEDULE:
        raise RuntimeError("빈 응답")
    with open(SCHED_CACHE, "w", encoding="utf-8") as f:
        json.dump(SCHEDULE, f, ensure_ascii=False)
    print(f"2026 일정 로드 완료: {len(SCHEDULE)} 세션 (캐시 저장)")
except Exception as e:
    # OpenF1 접근 실패(라이브 세션 차단 등) → 이전에 저장해 둔 캐시 사용
    try:
        with open(SCHED_CACHE, "r", encoding="utf-8") as f:
            SCHEDULE = json.load(f)
        print(f"OpenF1 일정 접근 실패 → 캐시 사용 ({len(SCHEDULE)} 세션): {e}")
    except Exception:
        SCHEDULE = []
        print(f"일정 로드 실패 (캐시도 없음): {e}")


def _recording_files():
    files = [os.path.join(REC_DIR, f) for f in os.listdir(REC_DIR) if f.endswith(".json")]
    files.sort(key=os.path.getmtime, reverse=True)   # 최신순
    return files


@app.get("/api/recordings")
async def list_recordings():
    """저장된 녹화 목록(최신순, 최대 KEEP_RECORDINGS개). 드롭다운 채우기용."""
    out = []
    for p in _recording_files()[:KEEP_RECORDINGS]:
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            frames = d.get("frames", [])
            step = d.get("step", LIVE_POLL_SEC)
            out.append({
                "key": d.get("session_key"),
                "session": d.get("session", ""),
                "frames": len(frames),
                "duration": max(0, len(frames) - 1) * step,
            })
        except Exception:
            pass
    return out


@app.get("/api/replay")
async def get_replay(key: str = None):
    """다시보기 소스 전체를 반환. key 없거나 'test' → 바레인 폴백,
    key=세션키 → 해당 녹화. 프런트가 통째로 받아 로컬에서 재생/스크럽."""
    if key and key != "test":
        for p in _recording_files():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if str(d.get("session_key")) == str(key):
                    return d
            except Exception:
                pass
    return FALLBACK or {"session": "", "step": 0.5, "drivers": {}, "frames": []}


@app.get("/api/schedule")
async def get_schedule():
    now = _utcnow().timestamp()
    upcoming = [s for s in SCHEDULE if _epoch(s["end"]) >= now]   # 아직 안 끝난 세션들
    nxt = upcoming[0] if upcoming else None
    live_now = None
    if nxt and _epoch(nxt["start"]) - 120 <= now <= _epoch(nxt["end"]) + 900:
        live_now = nxt
    return {"next": nxt, "live_now": live_now, "upcoming": upcoming[:8]}


# ============================================================================
# 기능별 라우터 등록 (뉴스·커뮤니티·결과는 별도 모듈로 분리)
#   공용 저수준 유틸은 openf1.py 에 있어 이 세 모듈은 server 를 역참조하지 않는다.
# ============================================================================
from news import router as news_router
from community import router as community_router, SESSIONS   # SESSIONS 재노출(테스트·호환용)
from results import router as results_router

app.include_router(news_router)
app.include_router(community_router)
app.include_router(results_router)
