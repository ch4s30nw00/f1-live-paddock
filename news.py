"""뉴스 & 영상 (F1 공식 사이트 RSS + F1 공식 유튜브 채널 RSS).

외부 RSS를 서버가 대신 받아(CORS 회피) JSON으로 변환한다. 10분 캐시.
공유 상태가 없어 독립 모듈로 분리했다 — server.py는 router만 등록한다.
"""
import re
import time as _time
import asyncio
import requests
import concurrent.futures
import xml.etree.ElementTree as ET

from fastapi import APIRouter

router = APIRouter()

F1_NEWS_RSS = "https://www.formula1.com/en/latest/all.xml"
F1_YT_CHANNEL = "UCB_qr75-ydFVKSF9Dmo6izg"     # 공식 'FORMULA 1' 채널
F1_YT_RSS = f"https://www.youtube.com/feeds/videos.xml?channel_id={F1_YT_CHANNEL}"
_UA = {"User-Agent": "Mozilla/5.0"}
_feed_cache = {}   # key -> (fetched_at, data)


def _cached_feed(key, ttl, fetch_fn):
    """fetch_fn 결과를 ttl초 캐시. 실패 시 오래된 캐시라도 반환."""
    now = _time.time()
    hit = _feed_cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        data = fetch_fn()
        _feed_cache[key] = (now, data)
        return data
    except Exception as e:
        print(f"피드 로드 실패({key}): {e}")
        return hit[1] if hit else []


def _og_image(url):
    """기사 페이지에서 og:image(대표 이미지) URL을 뽑아온다. 실패 시 빈 문자열."""
    try:
        html = requests.get(url, headers=_UA, timeout=6).text
        m = (re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
             or re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', html))
        return m.group(1) if m else ""
    except Exception:
        return ""


def _fetch_news():
    r = requests.get(F1_NEWS_RSS, headers=_UA, timeout=8)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.findall(".//item"):
        t = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if t and link:
            out.append({
                "title": t,
                "link": link,
                "desc": (it.findtext("description") or "").strip(),
                "date": (it.findtext("pubDate") or "").strip(),
                "image": "",
            })
    out = out[:9]
    # 각 기사 대표 이미지를 병렬로 수집(느린 순차 요청 방지)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for n, img in zip(out, ex.map(_og_image, [n["link"] for n in out])):
            n["image"] = img
    return out


def _fetch_videos():
    r = requests.get(F1_YT_RSS, headers=_UA, timeout=8)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    atom = "{http://www.w3.org/2005/Atom}"
    yt = "{http://www.youtube.com/xml/schemas/2015}"
    out = []
    for e in root.findall(f"{atom}entry"):
        vid = e.findtext(f"{yt}videoId")
        if not vid:
            continue
        out.append({
            "id": vid,
            "title": (e.findtext(f"{atom}title") or "").strip(),
            "date": (e.findtext(f"{atom}published") or "").strip(),
        })
    return out[:9]


@router.get("/api/news")
async def get_news():
    return await asyncio.to_thread(_cached_feed, "news", 600, _fetch_news)


@router.get("/api/videos")
async def get_videos():
    return await asyncio.to_thread(_cached_feed, "videos", 600, _fetch_videos)
