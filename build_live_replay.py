"""
build_live_replay.py
=====================
OpenF1 API에서 이미 끝난 실제 레이스 하나를 내려받아, 흩어져 있는 시계열
(차량 텔레메트리 / 인터벌 / 트랙 순위 / 랩 섹터)을 프레임 단위로 합쳐
미리 계산된 "라이브 보드" 리플레이 파일(live_replay.json)을 만든다.

server.py 의 웹소켓이 이 파일을 프레임 단위로 스트리밍하면, 프런트 라이브
보드에 전 차량의 속도/RPM/기어/갭/인터벌과 섹터별 색상(옐로/그린/퍼플)이
실제 F1 타이밍 타워처럼 표시된다.

왜 라이브 데이터가 아니라 끝난 레이스인가?
  라이브 텔레메트리는 실제 레이스 주말에만 흐르고 불안정하다.
  끝난 레이스(예: 바레인 2024)는 OpenF1에 모든 데이터가 영구 보존되므로
  개발·데모용 리플레이 소스로 안정적이다.

1회 실행:   py build_live_replay.py
"""

import json
import time
import requests
from datetime import datetime, timezone

BASE = "https://api.openf1.org/v1"

# ---- 리플레이할 레이스 -------------------------------------------------------
# 9472 = 바레인 GP 2024 본선. OpenF1에 전체 데이터가 남아있는 것을 확인함.
SESSION_KEY = 9472
# 레이스 중반의 그린 플래그 6분 구간.
WIN_START = "2024-03-02T15:20:00"
WIN_END   = "2024-03-02T15:26:00"
STEP = 0.5   # 프레임 간격(초) — 0.5초면 파일이 너무 커지지 않으면서 부드럽다

OUT_FILE = "live_replay.json"


def iso(s):
    """ISO-8601 문자열 -> epoch 초(float).

    OpenF1 타임스탬프는 UTC 기준. WIN_START/WIN_END 상수는 타임존 없이
    적었으므로, naive datetime은 로컬이 아닌 UTC로 간주해야 프레임 시각이
    데이터와 어긋나지 않는다.
    """
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def api(path, filters="", **params):
    """v1 엔드포인트 GET.

    OpenF1의 범위 필터는 `date>=값` / `date<=값` 형태의 원시 문법이라
    requests의 일반 파라미터 인코딩이 망가뜨린다. 그래서 범위 필터는
    미리 인코딩한 접미사(filters)로 넘긴다. (예: "&date%3E=2024-...")
    """
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}/{path}?{q}{filters}"
    for attempt in range(6):
        r = requests.get(url, timeout=30)
        if r.status_code == 429:              # 레이트리밋: 물러났다가 재시도
            wait = (attempt + 1) * 8
            print(f"    429 레이트리밋, {wait}초 대기...")
            time.sleep(wait)
            continue
        if r.status_code == 404:              # OpenF1은 결과 0건이면 404를 준다
            return []
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"429 반복으로 포기: {url}")


def window(start, end):
    """미리 인코딩된 'date>=start & date<=end' 접미사 (%3E = '>', %3C = '<')."""
    return f"&date%3E={start}&date%3C={end}"


class Fill:
    """시간 오름차순 스트림의 forward-fill 조회.
    조회 시각은 반드시 단조 증가해야 한다(프레임이 시간순으로 전진하므로)."""
    def __init__(self, pairs):
        pairs = sorted(pairs, key=lambda p: p[0])
        self.times = [p[0] for p in pairs]
        self.vals = [p[1] for p in pairs]
        self.i = -1
        self.n = len(pairs)

    def at(self, t):
        while self.i + 1 < self.n and self.times[self.i + 1] <= t:
            self.i += 1
        return self.vals[self.i] if self.i >= 0 else None


def fmt_gap(g):
    if g is None:
        return "-"
    if isinstance(g, str):
        return g              # 예: "+1 LAP"
    if g == 0:
        return "LEADER"
    return f"+{g:.3f}"


def fmt_int(v, pos):
    if pos == 1 or v is None:
        return "—"       # 리더는 em dash
    if isinstance(v, str):
        return v
    return f"+{v:.3f}"


def build_replay(session_key, win_start, win_end, step, out_file, label):
    """세션의 지정 구간을 내려받아 리플레이 JSON 하나로 합친다.

    바레인 폴백(main)과 최근 레이스 녹화 생성(build_recordings.py)이
    같이 쓴다. {session, session_key, step, drivers, frames} 를 out_file에
    저장하고 (프레임 수, 드라이버 수)를 반환한다."""
    t_start = iso(win_start)
    t_end = iso(win_end)
    print(f"[1/5] 세션 {session_key} 드라이버 목록 조회...")
    drivers_raw = api("drivers", session_key=session_key)
    drivers = {}
    for d in drivers_raw:
        n = d["driver_number"]
        drivers[str(n)] = {
            "acronym": d.get("name_acronym") or str(n),
            "name": d.get("full_name") or d.get("name_acronym") or str(n),
            "team": d.get("team_name") or "",
            "color": (d.get("team_colour") or "888888"),
        }
    nums = sorted(int(k) for k in drivers.keys())
    print(f"      -> 드라이버 {len(nums)}명")

    # ---- car_data (드라이버별 1요청, 구간 필터) ----------------------------
    car = {}
    for i, n in enumerate(nums, 1):
        print(f"[2/5] car_data {i}/{len(nums)} (드라이버 {n}) ...")
        rows = api("car_data", filters=window(win_start, win_end),
                   session_key=session_key, driver_number=n)
        pairs = [(iso(r["date"]), (r["speed"], r["rpm"], r.get("n_gear", 0)))
                 for r in rows]
        car[n] = Fill(pairs)
        time.sleep(0.25)   # 공개 API 레이트리밋 완화

    # ---- intervals (전체 드라이버, 구간 필터) ------------------------------
    print("[3/5] intervals ...")
    iv_rows = api("intervals", filters=window(win_start, win_end),
                  session_key=session_key)
    iv = {n: [] for n in nums}
    for r in iv_rows:
        n = r["driver_number"]
        if n in iv:
            iv[n].append((iso(r["date"]), (r.get("gap_to_leader"), r.get("interval"))))
    iv = {n: Fill(v) for n, v in iv.items()}

    # ---- position (구간 시작 전 기준값이 필요해서 세션 전체 조회) ----------
    print("[4/5] position + laps ...")
    pos_rows = api("position", filters=f"&date%3C={win_end}",
                   session_key=session_key)
    pos = {n: [] for n in nums}
    for r in pos_rows:
        n = r["driver_number"]
        if n in pos:
            pos[n].append((iso(r["date"]), r["position"]))
    pos = {n: Fill(v) for n, v in pos.items()}

    # ---- laps -> 섹터별 색상 이벤트 ----------------------------------------
    laps = api("laps", session_key=session_key)
    events = []   # (완료 시각, 드라이버, 섹터 인덱스 0..2, 소요 시간)
    for lp in laps:
        n = lp.get("driver_number")
        ds = lp.get("date_start")
        if n not in pos or not ds:
            continue
        t0 = iso(ds)
        s1 = lp.get("duration_sector_1")
        s2 = lp.get("duration_sector_2")
        s3 = lp.get("duration_sector_3")
        if s1:
            events.append((t0 + s1, n, 0, s1))
        if s1 and s2:
            events.append((t0 + s1 + s2, n, 1, s2))
        if lp.get("lap_duration"):
            events.append((t0 + lp["lap_duration"], n, 2, s3 or 0))

    # 섹터 완료 시각 순서대로 퍼플/그린/옐로 판정.
    events.sort(key=lambda e: e[0])
    session_best = [None, None, None]
    personal_best = {n: [None, None, None] for n in nums}
    color_events = {n: [] for n in nums}   # (시각, 섹터 인덱스, 색상)
    for t, n, s, dur in events:
        if not dur or dur <= 0:
            continue
        colour = "yellow"
        if session_best[s] is None or dur < session_best[s]:
            colour = "purple"
            session_best[s] = dur
            personal_best[n][s] = dur
        elif personal_best[n][s] is None or dur <= personal_best[n][s]:
            colour = "green"
            personal_best[n][s] = dur
        color_events[n].append((t, s, colour))

    # 섹터별 이벤트를 forward-fill 가능한 "3색 상태" 스트림으로 변환.
    sect = {}
    for n in nums:
        state = [None, None, None]
        stream = []
        for t, s, colour in sorted(color_events[n], key=lambda e: e[0]):
            state = list(state)
            state[s] = colour
            stream.append((t, tuple(state)))
        sect[n] = Fill(stream)

    # ---- 프레임 조립 --------------------------------------------------------
    print("[5/5] 프레임으로 병합 중...")
    frames = []
    t = t_start
    while t <= t_end:
        cars = []
        for n in nums:
            cd = car[n].at(t) or (0, 0, 0)
            gi = iv[n].at(t) or (None, None)
            p = pos[n].at(t)
            sc = sect[n].at(t) or (None, None, None)
            cars.append({
                "num": n,
                "pos": p,
                "speed": cd[0],
                "rpm": cd[1],
                "gear": cd[2],
                "gap": fmt_gap(gi[0]),
                "int": fmt_int(gi[1], p if p else 99),
                "s1": sc[0], "s2": sc[1], "s3": sc[2],
                "_sort": p if p is not None else (
                    999 + (gi[0] if isinstance(gi[0], (int, float)) else 0)),
            })
        cars.sort(key=lambda c: c["_sort"])
        for c in cars:
            del c["_sort"]
        frames.append({"t": round(t - t_start, 1), "cars": cars})
        t += step

    out = {
        "session": label,
        "session_key": session_key,
        "step": step,
        "drivers": drivers,
        "frames": frames,
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"완료 -> {out_file}: {len(frames)} 프레임, 차량 {len(nums)}대")
    return len(frames), len(nums)


def main():
    build_replay(SESSION_KEY, WIN_START, WIN_END, STEP, OUT_FILE,
                 "Bahrain GP 2024 · Race (replay)")
    print("서버 재시작:  py -m uvicorn server:app --reload")


if __name__ == "__main__":
    main()
