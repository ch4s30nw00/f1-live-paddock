"""개발용 스크립트: OpenF1에서 주행 텔레메트리가 남아있는 최근 본선(Race)을 찾아
표본 500건을 telemetry_9462.json 으로 저장한다. (라이브 보드 개발 초기 테스트용)

실행: py save_telemetry.py
"""
import requests
import json
import time


def download_real_f1_data():
    print("1. 서버에서 최근 경기(Session) 목록 조회 중...")
    session_url = "https://api.openf1.org/v1/sessions?year=2025"

    try:
        res = requests.get(session_url, timeout=5)
        sessions = res.json() if res.status_code == 200 else []

        if not sessions:
            print("2025년 데이터가 없어 2026년 세션 목록으로 재시도")
            res = requests.get("https://api.openf1.org/v1/sessions?year=2026", timeout=5)
            sessions = res.json() if res.status_code == 200 else []

        if not sessions:
            print("세션 목록 조회 실패")
            return False

        # 텔레메트리가 확실히 남는 본선(Race) 세션만 최신순으로 검사한다
        race_sessions = [s for s in sessions if s.get("session_name") == "Race"]
        print(f"총 {len(sessions)}개 세션 중 Race {len(race_sessions)}개를 최신순으로 검사...")
        print("-" * 60)

        for session in reversed(race_sessions):
            session_key = session["session_key"]
            country = session.get("country_name", "Unknown")

            print(f"{country} GP - Race (key {session_key}) 검사 중...", end=" ")

            test_url = f"https://api.openf1.org/v1/car_data?session_key={session_key}&limit=5"
            try:
                time.sleep(1.0)  # OpenF1 레이트리밋 완화용 간격

                test_res = requests.get(test_url, timeout=4)

                if test_res.status_code == 200:
                    real_data = test_res.json()
                    if real_data and len(real_data) > 0:
                        print("데이터 있음 → 표본 500건 다운로드")

                        time.sleep(1.0)
                        final_url = f"https://api.openf1.org/v1/car_data?session_key={session_key}&limit=500"
                        final_res = requests.get(final_url, timeout=5)

                        with open("telemetry_9462.json", "w", encoding="utf-8") as f:
                            json.dump(final_res.json(), f, indent=4)

                        print(f"\n{country} GP 주행 로그 저장 완료 (telemetry_9462.json)")
                        return True
                    else:
                        print("데이터 없음")
                elif test_res.status_code == 429:
                    print("429 레이트리밋 → 5초 대기")
                    time.sleep(5.0)
                else:
                    print(f"서버 에러 {test_res.status_code}")

            except Exception as e:
                print(f"통신 실패: {e}")
                time.sleep(2.0)

        print("-" * 60)
        print("주행 데이터가 남아있는 본선 세션을 찾지 못함")

    except Exception as e:
        print(f"네트워크 연결 실패: {e}")

    return False


if __name__ == "__main__":
    download_real_f1_data()
