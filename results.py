"""그랑프리 경기 결과 (연도별 2022~) — 결과 탭용.

  레이스·퀄리파잉·스프린트 = Jolpica/Ergast (전 시즌 지원)
  FP1·2·3 + 스프린트 퀄리파잉 = OpenF1 session_result (2023 시즌부터 존재)
  끝난 그랑프리는 results_cache/ 에 영구 캐시 → 과거 시즌은 API 재호출 없음

OpenF1 접근(_get)과 시간 헬퍼(_epoch/_utcnow)는 라이브 엔진과 공유하는데, 공용 모듈
openf1.py 에서 가져온다(server 를 역참조하지 않아 순환 import가 없다).
server.py는 이 모듈의 router 를 등록만 한다.
"""
import os
import json
import asyncio
import time as _time
import requests
from datetime import datetime, timezone

from fastapi import APIRouter

from openf1 import _get, _epoch, _utcnow

router = APIRouter()

JOLPICA = "https://api.jolpi.ca/ergast/f1"
RESULTS_DIR = "results_cache"
os.makedirs(RESULTS_DIR, exist_ok=True)


def _jolpi(path):
    r = requests.get(f"{JOLPICA}/{path}", timeout=10)
    r.raise_for_status()
    return r.json()["MRData"]


def _res_cache_read(name):
    try:
        with open(os.path.join(RESULTS_DIR, name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _res_cache_write(name, data):
    try:
        with open(os.path.join(RESULTS_DIR, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print("결과 캐시 저장 실패:", e)


def _fmt_secs(v):
    """OpenF1 duration(초) → '1:29.708' 표기. 문자열('+1 LAP' 등)은 그대로."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    m = int(v // 60)
    s = v - m * 60
    return f"{m}:{s:06.3f}" if m else f"{s:.3f}"


def _fmt_gap_res(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return f"+{v:.3f}" if v else "-"


# ---- 시즌 그랑프리 목록 (Jolpica 일정) ----
def _fetch_year_races(year):
    races = _jolpi(f"{year}.json?limit=100")["RaceTable"]["Races"]
    return [{
        "round": int(r["round"]),
        "name": r["raceName"],
        "circuit": r["Circuit"]["circuitName"],
        "country": r["Circuit"]["Location"].get("country", ""),
        "date": r.get("date", ""),
        "has_sprint": bool(r.get("Sprint")),
    } for r in races]


@router.get("/api/results/{year}")
async def api_results_year(year: int):
    cname = f"races_{year}.json"
    cached = _res_cache_read(cname)
    if cached and year < _utcnow().year:      # 지난 시즌 일정은 불변 → 캐시 그대로
        return cached
    try:
        data = await asyncio.to_thread(_fetch_year_races, year)
        if data:
            _res_cache_write(cname, data)
        return data
    except Exception as e:
        print(f"{year} 시즌 목록 로드 실패: {e}")
        return cached or []


# ---- Jolpica 결과 행 포맷 ----
def _drv_name(r):
    d = r.get("Driver", {})
    return f'{d.get("givenName", "")} {d.get("familyName", "")}'.strip()


def _rows_race(results):
    return [{
        "pos": r.get("positionText") or r.get("position", ""),
        "num": r.get("number", ""),
        "driver": _drv_name(r),
        "team": r.get("Constructor", {}).get("name", ""),
        "grid": r.get("grid", ""),
        "laps": r.get("laps", ""),
        "time": (r.get("Time") or {}).get("time") or r.get("status", ""),
        "points": r.get("points", "0"),
    } for r in results]


def _rows_quali(results):
    return [{
        "pos": r.get("position", ""),
        "num": r.get("number", ""),
        "driver": _drv_name(r),
        "team": r.get("Constructor", {}).get("name", ""),
        "q1": r.get("Q1", ""), "q2": r.get("Q2", ""), "q3": r.get("Q3", ""),
    } for r in results]


# ---- OpenF1: FP1·2·3 + 스프린트 퀄리파잉 결과 (2023+) ----
_openf1_meet_cache = {}   # year -> [meetings] (테스트 제외)


def _openf1_meetings(year):
    if year not in _openf1_meet_cache:
        ms = _get("meetings", {"year": year})
        _openf1_meet_cache[year] = [m for m in ms
                                    if "test" not in (m.get("meeting_name") or "").lower()]
    return _openf1_meet_cache[year]


def _openf1_practice_results(year, race_date):
    """레이스 날짜로 OpenF1 미팅을 찾아 FP·스프린트퀄리 결과를 가져온다."""
    out = {}
    if year < 2023 or not race_date:          # OpenF1 데이터는 2023 시즌부터
        return out
    target = datetime.fromisoformat(race_date).replace(tzinfo=timezone.utc).timestamp()
    best = None
    for m in _openf1_meetings(year):
        try:
            diff = abs(target - _epoch(m["date_start"]))
        except Exception:
            continue
        if diff < 5 * 86400 and (best is None or diff < best[0]):
            best = (diff, m)
    if not best:
        return out
    mk = best[1]["meeting_key"]

    name_map = {"Practice 1": "fp1", "Practice 2": "fp2", "Practice 3": "fp3",
                "Sprint Shootout": "sprint_quali",      # 2023 명칭
                "Sprint Qualifying": "sprint_quali"}    # 2024+ 명칭
    smap = {}
    for s in _get("sessions", {"meeting_key": mk}):
        k = name_map.get(s.get("session_name", ""))
        if k:
            smap[k] = s["session_key"]
    if not smap:
        return out

    dmap = {}
    for d in _get("drivers", {"meeting_key": mk}):
        n = d.get("driver_number")
        if n is not None and n not in dmap:
            dmap[n] = d

    for k, sk in smap.items():
        _time.sleep(0.3)                       # OpenF1 연속 호출 레이트리밋 완화
        try:
            res = _get("session_result", {"session_key": sk})
        except Exception as e:
            print(f"session_result 실패(sk={sk}): {e}")
            continue
        rows = []
        for r in sorted(res, key=lambda x: x.get("position") or 99):
            d = dmap.get(r.get("driver_number"), {})
            dur, gap = r.get("duration"), r.get("gap_to_leader")
            status = "DNS" if r.get("dns") else ("DSQ" if r.get("dsq") else ("DNF" if r.get("dnf") else ""))
            row = {
                "pos": r.get("position") or "-",
                "num": r.get("driver_number", ""),
                "driver": d.get("full_name") or d.get("broadcast_name") or str(r.get("driver_number", "")),
                "team": d.get("team_name", ""),
                "laps": r.get("number_of_laps", ""),
                "status": status,
            }
            if isinstance(dur, list):          # 퀄리형 세션: duration = [Q1, Q2, Q3]
                q = (list(dur) + [None] * 3)[:3]
                row.update(q1=_fmt_secs(q[0]), q2=_fmt_secs(q[1]), q3=_fmt_secs(q[2]))
            else:                              # 프랙티스: duration = 베스트 랩(초)
                row.update(time=_fmt_secs(dur), gap=_fmt_gap_res(gap))
            rows.append(row)
        if rows:
            out[k] = rows
    return out


# ---- 그랑프리 상세 (세션별 결과 묶음) ----
def _fetch_gp_detail(year, rnd):
    races = _res_cache_read(f"races_{year}.json") or _fetch_year_races(year)
    meta = next((r for r in races if r["round"] == rnd), {})
    detail = {"year": year, "round": rnd,
              "name": meta.get("name", ""), "circuit": meta.get("circuit", ""),
              "date": meta.get("date", ""), "sessions": {}}

    r = _jolpi(f"{year}/{rnd}/results.json?limit=100")["RaceTable"]["Races"]
    if r and r[0].get("Results"):
        detail["sessions"]["race"] = _rows_race(r[0]["Results"])
    q = _jolpi(f"{year}/{rnd}/qualifying.json?limit=100")["RaceTable"]["Races"]
    if q and q[0].get("QualifyingResults"):
        detail["sessions"]["qualifying"] = _rows_quali(q[0]["QualifyingResults"])
    if meta.get("has_sprint", True):           # 일정 메타가 없으면 일단 시도
        s = _jolpi(f"{year}/{rnd}/sprint.json?limit=100")["RaceTable"]["Races"]
        if s and s[0].get("SprintResults"):
            detail["sessions"]["sprint"] = _rows_race(s[0]["SprintResults"])

    try:
        detail["sessions"].update(_openf1_practice_results(year, detail["date"]))
    except Exception as e:
        print(f"OpenF1 FP/SQ 결과 로드 실패({year} R{rnd}): {e}")
    return detail


def _gp_complete(detail):
    """레이스 결과가 있고 (2023+면) FP1도 확보됐을 때만 '완성'으로 보고 영구 캐시."""
    ss = detail.get("sessions", {})
    return bool(ss.get("race")) and (detail["year"] < 2023 or bool(ss.get("fp1")))


@router.get("/api/results/{year}/{rnd}")
async def api_results_gp(year: int, rnd: int):
    cname = f"gp_{year}_{rnd}.json"
    cached = _res_cache_read(cname)
    if cached and _gp_complete(cached):
        return cached
    try:
        detail = await asyncio.to_thread(_fetch_gp_detail, year, rnd)
    except Exception as e:
        print(f"GP 결과 로드 실패({year} R{rnd}): {e}")
        return cached or {"year": year, "round": rnd, "name": "", "sessions": {}}
    if _gp_complete(detail):
        _res_cache_write(cname, detail)
    return detail
