"""OpenF1 API 호출 + 시간(UTC) 헬퍼.

라이브 엔진(server.py)과 결과 탭(results.py)이 함께 쓰는 저수준 유틸이다.
server 에도 results 에도 의존하지 않는 '바닥' 모듈이라, 여기로 모으면
server↔results 순환 import가 사라진다.
"""
import requests
from datetime import datetime, timezone

OPENF1 = "https://api.openf1.org/v1"


def _get(path, params=None, date_after=None, date_before=None, extra=""):
    q = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    filt = ""
    if date_after:
        filt += f"&date%3E={date_after}"    # %3E = '>'
    if date_before:
        filt += f"&date%3C={date_before}"   # %3C = '<'
    r = requests.get(f"{OPENF1}/{path}?{q}{filt}{extra}", timeout=8)
    if r.status_code == 404:                # OpenF1은 조회 결과가 0건이면 404 → '데이터 없음'
        return []
    if r.status_code == 429:                # 레이트리밋: 예외로 올려 이번 사이클만 건너뜀
        raise RuntimeError("429 rate-limited")
    r.raise_for_status()
    return r.json()


def _utcnow():
    return datetime.now(timezone.utc)


def _stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _epoch(s):
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()
