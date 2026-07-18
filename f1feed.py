# ============================================================================
# FastF1(F1 공식 SignalR 피드) 라이브 데이터 수신기
#   - OpenF1 무료 티어가 라이브 중 401로 막히는 문제를 우회한다.
#   - F1이 2025년부터 피드에 인증을 붙여서:
#       · F1TV 구독 토큰이 저장돼 있으면 → 인증 모드(전체 데이터)
#       · 없으면 → no-auth 모드(세션에 따라 부분 데이터만 올 수 있음)
#   - server.py 의 live_engine 이 build_board() 로 보드를 뽑아간다.
#     (엔진/프런트가 쓰는 cars 형태를 그대로 만들어 준다)
# ============================================================================
import base64
import json
import logging
import os
import threading
import time
import zlib

import requests
from fastf1.livetiming.client import SignalRClient
from signalrcore.hub_connection_builder import HubConnectionBuilder
from signalrcore.messages.completion_message import CompletionMessage

log = logging.getLogger("f1feed")
logging.basicConfig(format="%(asctime)s [f1feed] %(message)s")
log.setLevel(logging.INFO)

# 수신할 토픽 — 보드에 필요한 것만 (Position.z 는 서킷 맵의 드라이버 위치 점 용)
TOPICS = ["Heartbeat", "DriverList", "SessionInfo", "SessionStatus",
          "TrackStatus", "TimingData", "TimingAppData", "CarData.z",
          "Position.z", "LapCount"]

FRESH_SEC = 15.0    # 마지막 수신이 이 안이면 '라이브 중'으로 판단


def _stored_token():
    """fastf1 이 저장해 둔 F1TV 구독 토큰. 만료/없음 → None (no-auth 모드).
    절대 fastf1.get_auth_token() 을 직접 부르지 않는다 — 토큰이 없으면
    그 함수는 브라우저 인증 서버를 띄우고 '블로킹'되기 때문."""
    try:
        from fastf1.internals.f1auth import AUTH_DATA_FILE
        tok = AUTH_DATA_FILE.read_text().strip()
        if not tok:
            return None
        import jwt
        payload = jwt.decode(tok, options={"verify_signature": False})
        exp = payload.get("exp")
        if exp and exp < time.time():
            log.info("저장된 F1TV 토큰이 만료됨 → no-auth 모드로 진행")
            return None
        return tok
    except Exception:
        return None


def _inflate(b64):
    """'.z' 토픽(base64 + raw deflate) 해제."""
    return json.loads(zlib.decompress(base64.b64decode(b64), -zlib.MAX_WBITS))


def _merge(dst, src):
    """F1 피드는 '부분 패치'를 보낸다 → 딕셔너리 딥머지.
    스냅샷은 리스트로, 패치는 {"인덱스": ...} 딕셔너리로 오는 필드가 있어
    리스트를 만나면 딕셔너리로 바꿔서 병합한다."""
    for k, v in src.items():
        if k == "_kf":
            continue
        if isinstance(v, dict):
            cur = dst.get(k)
            if isinstance(cur, list):
                cur = {str(i): x for i, x in enumerate(cur)}
                dst[k] = cur
            if not isinstance(cur, dict):
                cur = {}
                dst[k] = cur
            _merge(cur, v)
        else:
            dst[k] = v


def _sector_color(sectors, i):
    """TimingData 의 Sectors 에서 섹터 i 색상(purple/green/yellow/None)."""
    if isinstance(sectors, list):
        s = sectors[i] if i < len(sectors) else None
    elif isinstance(sectors, dict):
        s = sectors.get(str(i))
    else:
        s = None
    if not isinstance(s, dict) or not s.get("Value"):
        return None
    if s.get("OverallFastest"):
        return "purple"
    if s.get("PersonalFastest"):
        return "green"
    return "yellow"


class _Client(SignalRClient):
    """파일 저장 대신 콜백으로 메시지를 넘기는 FastF1 클라이언트."""

    def __init__(self, on_data, token, timeout=120):
        super().__init__(filename=os.devnull, timeout=timeout,
                         logger=log, no_auth=(token is None))
        self.topics = TOPICS
        self._on_data = on_data
        self._token = token

    def _on_message(self, msg):
        self._t_last_message = time.time()
        try:
            if isinstance(msg, CompletionMessage):
                # Subscribe 응답 = 전체 스냅샷 {토픽: 데이터}
                for topic, data in (msg.result or {}).items():
                    self._on_data(topic, data)
            elif isinstance(msg, list) and len(msg) >= 2:
                # 스트리밍 feed = [토픽, 데이터, 타임스탬프]
                self._on_data(msg[0], msg[1])
        except Exception:
            log.exception("피드 메시지 처리 실패")

    def _run(self):
        # 원본 _run 과 동일하되, 토큰을 '저장된 것만' 쓰도록 교체
        # (원본은 토큰이 없으면 브라우저 인증으로 블로킹됨)
        self._output_file = open(os.devnull, "w")
        r = requests.options(self._negotiate_url, headers=self.headers,
                             timeout=10)
        self.headers.update(
            {"Cookie": f"AWSALBCORS={r.cookies['AWSALBCORS']}"})
        options = {"verify_ssl": True, "headers": self.headers}
        if self._token:
            options["access_token_factory"] = lambda: self._token
        self._connection = HubConnectionBuilder() \
            .with_url(self._connection_url, options=options) \
            .configure_logging(logging.WARNING) \
            .build()
        self._connection.on_open(self._on_connect)
        self._connection.on_close(self._on_close)
        self._connection.on("feed", self._on_message)
        self._connection.start()

        t0 = time.time()
        while not self._is_connected:
            if time.time() - t0 > 20:
                raise TimeoutError("SignalR 연결 실패(20s)")
            time.sleep(0.1)
        self._connection.send("Subscribe", [self.topics],
                              on_invocation=self._on_message)


class F1Feed:
    """수신 상태를 들고 있다가 live_engine 에 보드를 만들어 주는 싱글턴."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._stop = False
        self._client = None
        # 수신 상태
        self._last_data = 0.0
        self._timing = {}          # TimingData 누적 (Lines)
        self._drivers_raw = {}     # DriverList 누적
        self._car_channels = {}    # num(str) -> {speed,rpm,gear}
        self._positions = {}       # num(str) -> (x, y) 트랙 좌표
        self._session_info = {}
        self._session_status = ""
        # 스케줄 쪽에서 넣어주는 메타(녹화 파일명·시작시각용)
        self.window = None         # {"session_key","name","country","start"}

    # ---------- 수신 콜백 ----------
    def _on_data(self, topic, data):
        try:
            if topic.endswith(".z") and isinstance(data, str):
                data = _inflate(data)
            with self._lock:
                if topic == "TimingData" and isinstance(data, dict):
                    _merge(self._timing, data)
                elif topic == "DriverList" and isinstance(data, dict):
                    _merge(self._drivers_raw, data)
                elif topic == "CarData.z":
                    for entry in (data or {}).get("Entries", []):
                        for num, car in (entry.get("Cars") or {}).items():
                            ch = car.get("Channels") or {}
                            slot = self._car_channels.setdefault(num, {})
                            if "2" in ch:
                                slot["speed"] = ch["2"]
                            if "0" in ch:
                                slot["rpm"] = ch["0"]
                            if "3" in ch:
                                slot["gear"] = ch["3"]
                elif topic == "Position.z":
                    for entry in (data or {}).get("Position", []):
                        for num, p in (entry.get("Entries") or {}).items():
                            x, y = p.get("X"), p.get("Y")
                            if x is not None and y is not None and not (x == 0 and y == 0):
                                self._positions[num] = (x, y)
                elif topic == "SessionInfo" and isinstance(data, dict):
                    _merge(self._session_info, data)
                elif topic == "SessionStatus" and isinstance(data, dict):
                    self._session_status = data.get("Status") or self._session_status
                # Heartbeat 등은 수신시각 갱신 용도로만 쓴다
                if topic != "Heartbeat":
                    self._last_data = time.time()
        except Exception:
            log.exception(f"{topic} 파싱 실패")

    # ---------- 엔진이 쓰는 API ----------
    def is_fresh(self):
        with self._lock:
            has_lines = bool(self._timing.get("Lines"))
        return has_lines and (time.time() - self._last_data) < FRESH_SEC

    def session_name(self):
        with self._lock:
            meet = (self._session_info.get("Meeting") or {}).get("Name", "")
            name = self._session_info.get("Name", "")
        if meet or name:
            return f"{meet} · {name}".strip(" ·")
        w = self.window or {}
        return f'{w.get("country","")} · {w.get("name","")}'.strip(" ·")

    def drivers_meta(self):
        """엔진 STATE['drivers'] 형태: num(str) -> acronym/name/team/color"""
        out = {}
        with self._lock:
            for num, d in self._drivers_raw.items():
                if not isinstance(d, dict) or not num.isdigit():
                    continue
                out[num] = {
                    "acronym": d.get("Tla") or num,
                    "name": d.get("FullName") or num,
                    "team": d.get("TeamName") or "",
                    "color": d.get("TeamColour") or "888888",
                }
        return out

    def build_board(self):
        """(cars, fresh) — server.py 의 _build_live_board 와 동일한 형태."""
        with self._lock:
            lines = dict(self._timing.get("Lines") or {})
            channels = {n: dict(c) for n, c in self._car_channels.items()}
            positions = dict(self._positions)
        cars = []
        for numstr, line in lines.items():
            if not numstr.isdigit() or not isinstance(line, dict):
                continue
            try:
                pos = int(line.get("Position") or 0) or None
            except (TypeError, ValueError):
                pos = None
            ch = channels.get(numstr, {})
            gap = line.get("GapToLeader") or line.get("TimeDiffToFastest") or ""
            iv = line.get("IntervalToPositionAhead")
            intv = (iv or {}).get("Value") if isinstance(iv, dict) else None
            intv = intv or line.get("TimeDiffToPositionAhead") or ""
            sec = line.get("Sectors")
            cars.append({
                "num": int(numstr),
                "pos": pos,
                "speed": ch.get("speed", 0),
                "rpm": ch.get("rpm", 0),
                "gear": ch.get("gear", 0),
                "gap": "LEADER" if pos == 1 else (gap or "-"),
                "int": "—" if pos == 1 else (intv or "—"),
                "s1": _sector_color(sec, 0),
                "s2": _sector_color(sec, 1),
                "s3": _sector_color(sec, 2),
                "x": positions.get(numstr, (None, None))[0],
                "y": positions.get(numstr, (None, None))[1],
            })
        cars.sort(key=lambda c: c["pos"] if c["pos"] else 99)
        return cars, self.is_fresh()

    def status(self):
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "auth": bool(_stored_token()),
                "last_data_ago": round(time.time() - self._last_data, 1)
                                 if self._last_data else None,
                "session_status": self._session_status,
                "lines": len(self._timing.get("Lines") or {}),
                "drivers": len(self._drivers_raw),
                "window": self.window,
            }

    # ---------- 수명 관리 (스케줄 워처가 부른다) ----------
    def ensure_started(self, window):
        self.window = window
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def ensure_stopped(self):
        if not (self._thread and self._thread.is_alive()):
            return
        self._stop = True
        try:
            if self._client and self._client._connection:
                self._client._connection.stop()
        except Exception:
            pass

    def _loop(self):
        """세션 시간창 동안 접속 유지. 끊기면(타임아웃 포함) 재시도."""
        log.info("F1 공식 피드 수신 시작 (window=%s)", self.window)
        while not self._stop:
            token = _stored_token()
            mode = "F1TV 인증" if token else "no-auth"
            try:
                self._client = _Client(self._on_data, token)
                log.info("SignalR 접속 시도... (%s 모드)", mode)
                self._client._run()          # 접속 + 구독
                self._client._supervise()    # timeout까지 수신 감시(블로킹)
            except Exception as e:
                log.warning("피드 접속 실패: %s", e)
            finally:
                try:
                    if self._client and self._client._connection:
                        self._client._connection.stop()
                except Exception:
                    pass
            if self._stop:
                break
            log.info("30초 후 재접속...")
            for _ in range(30):
                if self._stop:
                    break
                time.sleep(1)
        # 세션 창이 끝나서 정지 → 다음 세션을 위해 수신 상태 리셋
        with self._lock:
            self._timing.clear()
            self._car_channels.clear()
            self._positions.clear()
            self._session_info.clear()
            self._last_data = 0.0
        log.info("F1 공식 피드 수신 종료")


FEED = F1Feed()
