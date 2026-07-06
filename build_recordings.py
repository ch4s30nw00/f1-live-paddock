"""
build_recordings.py
====================
완료된 레이스를 OpenF1 과거 데이터에서 내려받아 다시보기 파일로 만든다.

- backfill_race(): 레이스 1개를 recordings/<key>_<국가>_Race.json 으로 생성.
  server.py 가 라이브 세션 종료 후 자동 백필할 때 이 함수를 호출한다.
- 스크립트로 직접 실행하면 최근 완료된 레이스 3개를 한 번에 채운다.
  (드롭다운 "최근경기 1·2·3" 초기 세팅용, 재실행 가능)

실행:  py build_recordings.py
"""

import os
import time
from datetime import datetime, timezone, timedelta

from build_live_replay import build_replay, api

REC_DIR = "recordings"
STEP = 1.0          # 프레임 간격(초) — 풀 레이스 기준 파일당 약 15MB, 재생은 충분히 부드럽다
N_RACES = 3         # 최근경기 1/2/3 슬롯 수


def safe(s):
    return "".join(ch if ch.isalnum() else "_" for ch in s)[:40]


def naive_utc(dt):
    """aware datetime -> naive UTC 'YYYY-MM-DDTHH:MM:SS' (OpenF1 필터 형식)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def backfill_race(session_key, country):
    """완료된 레이스 하나를 OpenF1에서 내려받아 recordings/ 에 다시보기로 저장.

    OpenF1에 아직 랩 데이터가 올라오지 않았으면 None을 반환한다(호출측이 재시도).
    성공하면 생성된 파일 경로를 반환한다.

    세션의 date_start/date_end 는 여유가 포함된 약 2시간짜리 슬롯이라,
    1랩 시작(그린 플래그)부터 마지막 랩 완료(체커기)까지 실제 레이스 구간만
    랩 데이터로 계산해서 내려받는다.
    """
    laps = api("laps", session_key=session_key)
    starts = [datetime.fromisoformat(lp["date_start"])
              for lp in laps if lp.get("date_start")]
    ends = [datetime.fromisoformat(lp["date_start"]) + timedelta(seconds=lp["lap_duration"])
            for lp in laps if lp.get("date_start") and lp.get("lap_duration")]
    if not starts or not ends:
        return None

    race_start, race_end = min(starts), max(ends)
    win_start = race_start - timedelta(seconds=5)
    win_end = race_end + timedelta(seconds=5)
    mins = (race_end - race_start).total_seconds() / 60

    label = f"{country} · Race"
    os.makedirs(REC_DIR, exist_ok=True)
    out = os.path.join(REC_DIR, f"{session_key}_{safe(country)}_Race.json")
    print(f"=== {label} (key {session_key}) 레이스 구간 {race_start:%H:%M}~{race_end:%H:%M} UTC (~{mins:.0f}분) ===")
    build_replay(session_key, naive_utc(win_start), naive_utc(win_end), STEP, out, label)
    return out


def main():
    now = datetime.now(timezone.utc)

    print("OpenF1에서 완료된 2026 레이스 검색 중...")
    races = api("sessions", year=2026, session_name="Race")
    completed = [s for s in races
                 if s.get("date_end") and datetime.fromisoformat(s["date_end"]) < now]
    completed.sort(key=lambda s: s["date_start"])
    picks = completed[-N_RACES:]     # 최근 N개 (과거 -> 최신)
    names = ", ".join(p.get("country_name", "?") for p in picks)
    print(f"  -> {len(picks)}개 레이스 생성 예정: {names}\n")

    # 과거 -> 최신 순으로 만들어 최신 레이스가 가장 최근 mtime을 갖게 한다.
    # (그래야 드롭다운에서 "최근경기 1"이 최신 레이스가 됨)
    for s in picks:
        out = backfill_race(s["session_key"], s.get("country_name", ""))
        if out is None:
            print(f"  건너뜀 (key {s['session_key']}): 랩 데이터 없음")
        print()
        time.sleep(1.0)

    print("녹화 생성 완료. 브라우저를 새로고침하면 최근경기 1·2·3이 채워진다.")


if __name__ == "__main__":
    main()
