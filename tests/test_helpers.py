"""순수 헬퍼 함수 단위 테스트.

네트워크·DB 없이 입력→출력만 검증한다. 타이밍 보드/결과 탭의 포맷팅과
섹터 색 판정 같은 '핵심 도메인 로직'이 대상이다.
"""
import server
import results   # _fmt_secs·_fmt_gap_res·_rows_*·_gp_complete 는 결과 모듈로 분리됨


# --- 갭/인터벌 포맷 (타이밍 보드) -------------------------------------------
def test_fmt_gap_none은_대시():
    assert server._fmt_gap(None) == "-"


def test_fmt_gap_0은_리더():
    assert server._fmt_gap(0) == "LEADER"


def test_fmt_gap_숫자는_소수3자리():
    assert server._fmt_gap(1.5) == "+1.500"


def test_fmt_gap_문자열은_그대로():
    # '+1 LAP' 같은 이미 포맷된 문자열은 손대지 않는다
    assert server._fmt_gap("+1 LAP") == "+1 LAP"


def test_fmt_int_1위는_대시():
    # 선두는 인터벌이 없다
    assert server._fmt_int(1.234, 1) == "—"


def test_fmt_int_none은_대시():
    assert server._fmt_int(None, 5) == "—"


def test_fmt_int_숫자는_소수3자리():
    assert server._fmt_int(0.321, 3) == "+0.321"


# --- 시간 포맷 (결과 탭) -----------------------------------------------------
def test_fmt_secs_분초_표기():
    # 89.708초 = 1분 29.708초
    assert results._fmt_secs(89.708) == "1:29.708"


def test_fmt_secs_1분_미만은_초만():
    assert results._fmt_secs(45.3) == "45.300"


def test_fmt_secs_분단위_초는_제로패딩():
    # 1분 5.5초 → 초 부분이 두 자리로 채워져야 한다
    assert results._fmt_secs(65.5) == "1:05.500"


def test_fmt_secs_none은_빈문자():
    assert results._fmt_secs(None) == ""


def test_fmt_secs_문자열은_그대로():
    assert results._fmt_secs("+1 LAP") == "+1 LAP"


def test_fmt_gap_res_0은_대시():
    assert results._fmt_gap_res(0) == "-"


def test_fmt_gap_res_숫자():
    assert results._fmt_gap_res(2.75) == "+2.750"


# --- 최신 표본 추리기 --------------------------------------------------------
def test_latest_by_드라이버별_최신행():
    rows = [
        {"driver_number": 1, "date": "2024-01-01T00:00:01", "speed": 100},
        {"driver_number": 1, "date": "2024-01-01T00:00:03", "speed": 300},  # 더 최신
        {"driver_number": 1, "date": "2024-01-01T00:00:02", "speed": 200},
        {"driver_number": 44, "date": "2024-01-01T00:00:05", "speed": 250},
    ]
    out = server._latest_by(rows)
    assert out[1]["speed"] == 300      # 1번은 가장 늦은 시각의 행
    assert out[44]["speed"] == 250
    assert set(out.keys()) == {1, 44}


def test_latest_by_키없는행은_무시():
    rows = [{"date": "2024-01-01T00:00:01"}, {"driver_number": 7, "date": "2024-01-01T00:00:01"}]
    out = server._latest_by(rows)
    assert list(out.keys()) == [7]


# --- 섹터 색 판정 (퍼플/그린/옐로) ------------------------------------------
def test_sector_colors_퍼플과_그린():
    """세션 베스트=퍼플, 개인 베스트(세션 베스트는 아님)=그린."""
    laps = [
        {"driver_number": 1, "date_start": "2024-01-01T00:00:00",
         "duration_sector_1": 30.0, "duration_sector_2": 40.0,
         "duration_sector_3": 50.0, "lap_duration": 120.0},
        {"driver_number": 2, "date_start": "2024-01-01T00:00:10",
         "duration_sector_1": 28.0, "duration_sector_2": 42.0,
         "duration_sector_3": 48.0, "lap_duration": 118.0},
    ]
    colors = server._sector_colors(laps)
    # 두 드라이버 모두 각자 기록한 순간엔 섹터1 세션 베스트였다 → 퍼플
    assert colors[1][0] == "purple"
    assert colors[2][0] == "purple"
    # 2번의 섹터2(42.0)는 1번의 40.0보다 느리다 → 세션 베스트 아님, 첫 기록이라 그린
    assert colors[2][1] == "green"


def test_sector_colors_옐로():
    """같은 드라이버가 두 번째 랩에서 자기 베스트보다 느리면 옐로."""
    laps = [
        {"driver_number": 1, "date_start": "2024-01-01T00:00:00", "duration_sector_1": 30.0},
        {"driver_number": 1, "date_start": "2024-01-01T00:03:20", "duration_sector_1": 35.0},
    ]
    colors = server._sector_colors(laps)
    assert colors[1][0] == "yellow"


# --- Jolpica 결과 행 포맷 ----------------------------------------------------
def test_drv_name_이름_합치기():
    assert results._drv_name({"Driver": {"givenName": "Max", "familyName": "Verstappen"}}) == "Max Verstappen"


def test_rows_race_필드매핑():
    payload = [{
        "position": "1", "number": "1", "grid": "2", "laps": "57",
        "points": "25",
        "Driver": {"givenName": "Lando", "familyName": "Norris"},
        "Constructor": {"name": "McLaren"},
        "Time": {"time": "1:30:00.000"},
    }]
    row = results._rows_race(payload)[0]
    assert row["driver"] == "Lando Norris"
    assert row["team"] == "McLaren"
    assert row["points"] == "25"
    assert row["time"] == "1:30:00.000"


def test_rows_race_완주못한경우_status():
    # 완주 시간이 없으면 status(예: 'Accident')로 대체된다
    payload = [{"position": "20", "status": "Accident",
                "Driver": {"givenName": "A", "familyName": "B"},
                "Constructor": {"name": "T"}}]
    assert results._rows_race(payload)[0]["time"] == "Accident"


def test_rows_quali_q1q2q3():
    payload = [{
        "position": "1", "number": "16",
        "Driver": {"givenName": "Charles", "familyName": "Leclerc"},
        "Constructor": {"name": "Ferrari"},
        "Q1": "1:20.000", "Q2": "1:19.500", "Q3": "1:19.000",
    }]
    row = results._rows_quali(payload)[0]
    assert row["q3"] == "1:19.000"
    assert row["driver"] == "Charles Leclerc"


# --- 그랑프리 '완성' 판정 (영구 캐시 조건) ----------------------------------
def test_gp_complete_2022는_레이스만_있어도_완성():
    detail = {"year": 2022, "sessions": {"race": [{"pos": "1"}]}}
    assert results._gp_complete(detail) is True


def test_gp_complete_2024는_fp1없으면_미완성():
    detail = {"year": 2024, "sessions": {"race": [{"pos": "1"}]}}
    assert results._gp_complete(detail) is False


def test_gp_complete_2024_fp1있으면_완성():
    detail = {"year": 2024, "sessions": {"race": [{"pos": "1"}], "fp1": [{"pos": "1"}]}}
    assert results._gp_complete(detail) is True


def test_gp_complete_레이스없으면_미완성():
    detail = {"year": 2022, "sessions": {}}
    assert results._gp_complete(detail) is False
