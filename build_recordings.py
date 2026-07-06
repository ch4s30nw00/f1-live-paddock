"""
build_recordings.py
====================
"최근경기 1·2·3" 드롭다운을 채우기 위해, 이미 끝난 2026 레이스 최근 3개를
OpenF1 과거 데이터에서 내려받아 리플레이 파일로 만든다.
(라이브 녹화 없이도 가능 — OpenF1은 지난 세션을 모두 보존한다)

각 레이스를 recordings/<key>_<국가>_Race.json 으로 저장하면
/api/recordings 가 그 목록을 읽어 드롭다운에 띄운다.

1회 실행(재실행 가능):  py build_recordings.py
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


def main():
    os.makedirs(REC_DIR, exist_ok=True)
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
        sk = s["session_key"]
        country = s.get("country_name", "")

        # 세션의 date_start/date_end 는 여유가 포함된 약 2시간짜리 슬롯이다.
        # 실제 레이스는 1랩 시작(그린 플래그)부터 마지막 랩 완료(체커기)까지이므로
        # 슬롯 전체가 아니라 실제 레이스 구간만 잘라서 받는다.
        laps = api("laps", session_key=sk)
        starts = [datetime.fromisoformat(lp["date_start"])
                  for lp in laps if lp.get("date_start")]
        ends = [datetime.fromisoformat(lp["date_start"]) + timedelta(seconds=lp["lap_duration"])
                for lp in laps if lp.get("date_start") and lp.get("lap_duration")]
        race_start = min(starts) if starts else datetime.fromisoformat(s["date_start"])
        race_end = max(ends) if ends else datetime.fromisoformat(s["date_end"])
        win_start = race_start - timedelta(seconds=5)
        win_end = race_end + timedelta(seconds=5)
        mins = (race_end - race_start).total_seconds() / 60

        label = f"{country} · Race"
        out = os.path.join(REC_DIR, f"{sk}_{safe(country)}_Race.json")
        print(f"=== {label} (key {sk}) — 레이스 구간 {race_start:%H:%M}–{race_end:%H:%M} UTC (~{mins:.0f}분) ===")
        build_replay(sk, naive_utc(win_start), naive_utc(win_end), STEP, out, label)
        print()
        time.sleep(1.0)

    print("녹화 생성 완료. 브라우저를 새로고침하면 최근경기 1·2·3이 채워진다.")


if __name__ == "__main__":
    main()
