# -*- coding: utf-8 -*-
"""
Dual Sniper Pro 동적 백테스트 시뮬레이터 (재구현판)
====================================================
근거 자료:
  1) "Dual Sniper Pro 전략 로직 설명서" (업데이트 2026-07-23) — 사용자 업로드 이미지 1,2
  2) 참고 백테스트 사이트 실제 결과 스크린샷 — 이미지 3,4 (파라미터명·컬럼 구조 참고)
  3) 기존 Gemini 작성 app.py — 데이터/기본 골격 참고

이 파일에서 "원본 그대로 확정된 것"과 "추정/가정한 것"을 구분해 둡니다.
────────────────────────────────────────────────────────
[확정] 매뉴얼에 명시된 항목
  - 공격모드: 매수 분할은 항상 균등비중(변경불가), 티어별 독립 관리, 하루 1티어 매수 + 보유티어 매도주문
  - 공격 매도기준(n%) = min + (max-min) * (1-x)^(1/a),  x = clip((매수RSI-35)/30, 0, 1)
  - 공격 보유일(n일)   = min + (max-min) * (1-x)^(1/a),  x = clip((매수RSI-35)/30, 0, 1)
    (기본값 0.1~3.0%, 7~30일 / 화면3의 "자세히" 옵션으로 min·max·a 모두 커스텀 가능하게 구현)
  - 방어모드 매수가 = min(조건1, 조건2), 조건1 = factor·(직전 MA기준-1일 종가합) / (MA기준 - factor)
  - 방어모드 매도가 = factor·(직전 MA기준-1일 종가합) / (MA기준 - factor), 전량 LOC 매도(익절시 전 티어 동시청산)
  - 매수할당금 = 남은현금 × (다음티어 비중 / 남은티어 비중합) — 방어모드는 비중 커스텀 가능(예: 6,13,20,27,34)
  - 소수점: 매수조건가는 내림(2자리), 매도조건가는 올림(2자리)
  - 공통: MOC 매도가 있는 날은 그 외 매도(익절 LOC)는 발생하지 않음
  - 공통: 전일종가 > 전일MA(5) 이면 1티어는 만기 MOC 매도를 보류(익절 매도는 예외 없이 진행)

[가정/추정] 매뉴얼에 없거나 "비공개"라고 명시된 항목 — 아래처럼 처리했습니다
  - 모드 전환 조건(공격/방어 전환 기준)은 매뉴얼에 "비공개"라 명시되어 있어 재현 불가.
    → 기본값은 예전 코드처럼 "종가 ≥ MA(20)=공격, 미만=방어"로 두되,
      사이드바에서 "실제 백테스트 사이트 모드값 CSV 업로드"를 선택하면 그 값을 그대로 사용하도록 만들었습니다.
      (요청하신 "유저가 업로드하면 그 값으로 백테스트" 기능)
  - 공격모드 매수기준가의 "전일 Fi 부호"에서 Fi가 무엇의 약자인지 매뉴얼에 정의가 없습니다.
    → 공통사항에 이미 종가-MA(5) 비교가 등장하므로, Fi = (전일종가 - 전일MA5)로 가정했습니다.
      사이드바에 이 가정을 밝혀두었고, 필요시 쉽게 다른 지표로 교체할 수 있게 함수 하나로 분리해 두었습니다.
  - 데이터는 종가만 있고 시가/고가/저가가 없어, LOC/MOC 주문이 "그 날 정확히 그 가격에 체결됐는지"는
    검증 불가합니다. → 계산된 지정가가 그 날 체결된 것으로 간주(종가 대신 지정가로 손익 계산)하는
    일봉 기반 근사 방식을 사용했습니다. (대부분의 데일리바 백테스터가 쓰는 방식과 동일)
  - 방어모드 보유기간은 매뉴얼에 공식이 없고 화면3에도 고정 숫자(8)로만 보여, 고정값 파라미터로 두었습니다.
"""

import streamlit as st
import pandas as pd
import numpy as np
import math
import io

st.set_page_config(page_title="2026 SOXL 듀얼스나이퍼 동적 백테스트", layout="wide")
st.markdown(
    """<style>
    .block-container { padding-top: 3rem; padding-bottom: 0rem; max-width: 100%; }
    section[data-testid="stSidebar"] .block-container { padding-top: 2.2rem; }
    </style>""",
    unsafe_allow_html=True,
)

# ────────────────────────────────────────────────────────────────
# 1. 기본 내장 가격 데이터 (2026년 SOXL, 170 영업일 — 실제 사이트 '로켓셋' 백테스트 매매로그 기준으로 교정)
#    — 필요시 사이드바에서 CSV로 교체 가능
# ────────────────────────────────────────────────────────────────
RAW_PRICE_DATA = [
    ("26-01-02 금", 47.24), ("26-01-05 월", 49.22), ("26-01-06 화", 54.01), ("26-01-07 수", 52.29), ("26-01-08 목", 49.65),
    ("26-01-09 금", 53.95), ("26-01-12 월", 54.65), ("26-01-13 화", 56.07), ("26-01-14 수", 55.38), ("26-01-15 목", 58.08),
    ("26-01-16 금", 60.75), ("26-01-20 화", 57.94), ("26-01-21 수", 63.48), ("26-01-22 목", 63.72), ("26-01-23 금", 61.6),
    ("26-01-26 월", 60.7), ("26-01-27 화", 64.96), ("26-01-28 수", 70.09), ("26-01-29 목", 70.47), ("26-01-30 금", 61.79),
    ("26-02-02 월", 65.2), ("26-02-03 화", 61.2), ("26-02-04 수", 53.19), ("26-02-05 목", 53.25), ("26-02-06 금", 61.75),
    ("26-02-09 월", 64.03), ("26-02-10 화", 63.34), ("26-02-11 수", 68.05), ("26-02-12 목", 63.1), ("26-02-13 금", 64.59),
    ("26-02-17 화", 64.35), ("26-02-18 수", 66.29), ("26-02-19 목", 65.23), ("26-02-20 금", 67.11), ("26-02-23 월", 65.86),
    ("26-02-24 화", 68.53), ("26-02-25 수", 71.86), ("26-02-26 목", 65.34), ("26-02-27 금", 62.77), ("26-03-02 월", 62.76),
    ("26-03-03 화", 53.42), ("26-03-04 수", 56.62), ("26-03-05 목", 54.8), ("26-03-06 금", 47.89), ("26-03-09 월", 53.32),
    ("26-03-10 화", 54.59), ("26-03-11 수", 56.09), ("26-03-12 목", 50.28), ("26-03-13 금", 50.72), ("26-03-16 월", 53.69),
    ("26-03-17 화", 54.95), ("26-03-18 수", 54.02), ("26-03-19 목", 54.85), ("26-03-20 금", 51.14), ("26-03-23 월", 53.03),
    ("26-03-24 화", 54.96), ("26-03-25 수", 57.04), ("26-03-26 목", 48.97), ("26-03-27 금", 46.61), ("26-03-30 월", 40.62),
    ("26-03-31 화", 47.91), ("26-04-01 수", 52.26), ("26-04-02 목", 52.75), ("26-04-06 월", 54.81), ("26-04-07 화", 56.55),
    ("26-04-08 수", 67.5), ("26-04-09 목", 71.98), ("26-04-10 금", 76.39), ("26-04-13 월", 80.56), ("26-04-14 화", 85.31),
    ("26-04-15 수", 85.96), ("26-04-16 목", 88.37), ("26-04-17 금", 94.68), ("26-04-20 월", 95.94), ("26-04-21 화", 98.09),
    ("26-04-22 수", 105.64), ("26-04-23 목", 112.77), ("26-04-24 금", 128.32), ("26-04-27 월", 123.39), ("26-04-28 화", 109.56),
    ("26-04-29 수", 117.97), ("26-04-30 목", 126.98), ("26-05-01 금", 130.4), ("26-05-04 월", 127.55), ("26-05-05 화", 144.16),
    ("26-05-06 수", 165.85), ("26-05-07 목", 152.1), ("26-05-08 금", 176.94), ("26-05-11 월", 190.42), ("26-05-12 화", 172.52),
    ("26-05-13 수", 184.24), ("26-05-14 목", 186.19), ("26-05-15 금", 164.18), ("26-05-18 월", 151.75), ("26-05-19 화", 151.89),
    ("26-05-20 수", 173.2), ("26-05-21 목", 178.39), ("26-05-22 금", 190.56), ("26-05-26 화", 225.79), ("26-05-27 수", 217.98),
    ("26-05-28 목", 224.63), ("26-05-29 금", 224.34), ("26-06-01 월", 227.03), ("26-06-02 화", 266.32), ("26-06-03 수", 280.54),
    ("26-06-04 목", 262.7), ("26-06-05 금", 182.54), ("26-06-08 월", 211.44), ("26-06-09 화", 201.68), ("26-06-10 수", 180.65),
    ("26-06-11 목", 223.99), ("26-06-12 금", 234.68), ("26-06-15 월", 272.5), ("26-06-16 화", 226.19), ("26-06-17 수", 233.86),
    ("26-06-18 목", 279.29), ("26-06-22 월", 300.77), ("26-06-23 화", 231.42), ("26-06-24 수", 229.57), ("26-06-25 목", 252.61),
    ("26-06-26 금", 215.6), ("26-06-29 월", 236.52), ("26-06-30 화", 266.71), ("26-07-01 수", 217.55), ("26-07-02 목", 181.47),
    ("26-07-06 월", 194.65), ("26-07-07 화", 165.28), ("26-07-08 수", 174.82), ("26-07-09 목", 192.45), ("26-07-10 금", 192.26),
    ("26-07-13 월", 165.37), ("26-07-14 화", 176.66), ("26-07-15 수", 165.55), ("26-07-16 목", 142.48), ("26-07-17 금", 135.47),
    ("26-07-20 월", 136.81), ("26-07-21 화", 158.54), ("26-07-22 수", 160.99), ("26-07-23 목", 157.5), ("26-07-24 금", 136.81),
    ("26-07-27 월", 128.15), ("26-07-28 화", 109.54), ("26-07-29 수", 91.99), ("26-07-30 목", 114.72), ("26-07-31 금", 114.72),
    ("26-08-03 월", 116.71), ("26-08-04 화", 139.9), ("26-08-05 수", 132.07), ("26-08-06 목", 132.33), ("26-08-07 금", 140.25),
    ("26-08-10 월", 130.0), ("26-08-11 화", 133.0), ("26-08-12 수", 142.16), ("26-08-13 목", 145.36), ("26-08-14 금", 144.95),
    ("26-08-17 월", 151.53), ("26-08-18 화", 129.1), ("26-08-19 수", 120.74), ("26-08-20 목", 122.21), ("26-08-21 금", 120.6),
    ("26-08-24 월", 111.16), ("26-08-25 화", 115.67), ("26-08-26 수", 116.6), ("26-08-27 목", 123.05), ("26-08-28 금", 111.34),
    ("26-08-31 월", 112.79), ("26-09-01 화", 105.91), ("26-09-02 수", 106.35), ("26-09-03 목", 106.74), ("26-09-04 금", 117.28),
]


# ────────────────────────────────────────────────────────────────
# 2. 유틸 함수
# ────────────────────────────────────────────────────────────────
def floor_2(x: float) -> float:
    """매수조건가: 기준가 기준 소수점 2자리에서 내림"""
    return math.floor(x * 100) / 100


def ceil_2(x: float) -> float:
    """매도조건가: 종가 기준 소수점 2자리에서 올림"""
    return math.ceil(x * 100) / 100


def clip01(v: float) -> float:
    return min(1.0, max(0.0, v))


def rsi_ratio(buy_rsi: float) -> float:
    """x = (매수RSI - 35) / 30, 0~1로 클리핑"""
    return clip01((buy_rsi - 35.0) / 30.0)


def attack_sell_pct(buy_rsi: float, pmin: float, pmax: float, a: float) -> float:
    """공격 매도기준(n%) = min + (max-min)*(1-x)^(1/a)"""
    x = rsi_ratio(buy_rsi)
    return (pmin + (pmax - pmin) * (1 - x) ** (1.0 / a)) / 100.0


def attack_hold_days(buy_rsi: float, dmin: float, dmax: float, a: float) -> int:
    """공격 보유일(n일) = min + (max-min)*(1-x)^(1/a)"""
    x = rsi_ratio(buy_rsi)
    return round(dmin + (dmax - dmin) * (1 - x) ** (1.0 / a))


def defense_price(prev_sum: float, n_ma: int, cond_pct: float) -> float:
    """factor·(직전 MA기준-1일 종가합) / (MA기준 - factor)"""
    factor = 1.0 + cond_pct / 100.0
    denom = n_ma - factor
    if denom == 0 or prev_sum is None or np.isnan(prev_sum):
        return np.nan
    return factor * prev_sum / denom


def parse_weight_vector(text: str, n_splits: int):
    """'6,13,20,27,34' 같은 문자열을 정규화된 비중 리스트로 변환. 실패/미입력시 균등분할."""
    try:
        parts = [float(p.strip()) for p in text.split(",") if p.strip() != ""]
        if len(parts) != n_splits or sum(parts) <= 0:
            raise ValueError
        s = sum(parts)
        return [p / s for p in parts]
    except Exception:
        return [1.0 / n_splits] * n_splits


# ────────────────────────────────────────────────────────────────
# 3. 사이드바 UI
# ────────────────────────────────────────────────────────────────
st.sidebar.markdown("### 📁 데이터 소스")
data_source = st.sidebar.radio(
    "가격 데이터 소스",
    ["Yahoo Finance 실시간 조회", "내장 데이터 (2026 SOXL, 고정값)", "CSV 업로드"],
    index=0,
    help="Yahoo Finance를 선택하면 매번 최신 종가를 자동으로 받아옵니다. "
         "내장 데이터는 이 대화에서 검증에 썼던 2026-01-02~09-04 고정 스냅샷입니다.",
)

YF_KR_WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]


def _to_kr_date_str(ts) -> str:
    ts = pd.Timestamp(ts)
    return f"{ts.strftime('%y-%m-%d')} {YF_KR_WEEKDAY[ts.weekday()]}"


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_yfinance_prices(ticker: str, start_iso: str, end_iso: str, lookback_days: int = 120):
    """Yahoo Finance에서 일별 종가를 받아온다. 시작일 이전 lookback_days만큼 더 받아와서
    MA(5)/MA(20)/RSI(14)가 첫날부터 실제 과거 시세로 정상 warm-up 되게 한다(내장 데이터의
    역산 워밍업이 필요 없어짐 — Yahoo가 진짜 과거 시세를 갖고 있으므로).
    실패 시 (None, 0, 에러메시지) 반환."""
    try:
        import yfinance as yf
    except ImportError:
        return None, 0, "yfinance 패키지가 설치되어 있지 않습니다 (pip install yfinance 필요)."

    try:
        start_ts = pd.Timestamp(start_iso) - pd.Timedelta(days=lookback_days)
        end_ts = pd.Timestamp(end_iso) + pd.Timedelta(days=1)
        raw = yf.download(ticker, start=start_ts, end=end_ts, progress=False, auto_adjust=False)
        if raw is None or raw.empty:
            return None, 0, f"'{ticker}' 티커에 대한 데이터를 받아오지 못했습니다."
        raw = raw.reset_index()
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0] if c[0] else c[1] for c in raw.columns]
        close_col = "Close" if "Close" in raw.columns else "Adj Close"
        df_fetched = pd.DataFrame(
            {"날짜": raw["Date"].apply(_to_kr_date_str), "종가": raw[close_col].astype(float)}
        )
        n_before_start = int((pd.to_datetime(raw["Date"]) < pd.Timestamp(start_iso)).sum())
        return df_fetched, n_before_start, None
    except Exception as e:
        return None, 0, f"조회 중 오류: {e}"


price_file = None
_yf_result = None
if data_source == "Yahoo Finance 실시간 조회":
    yf_c1, yf_c2, yf_c3 = st.sidebar.columns([1, 1, 1])
    yf_ticker = yf_c1.text_input("티커", value="SOXL", key="yf_ticker")
    yf_start = yf_c2.date_input("시작일", value=pd.Timestamp("2026-01-01").date(), key="yf_start")
    yf_end = yf_c3.date_input("종료일", value=pd.Timestamp.today().date(), key="yf_end")
    if st.sidebar.button("📡 시세 불러오기", use_container_width=True):
        with st.spinner(f"{yf_ticker} 시세를 Yahoo Finance에서 불러오는 중..."):
            df_fetched, n_before, err = fetch_yfinance_prices(
                yf_ticker, str(yf_start), str(yf_end)
            )
        if err:
            st.sidebar.error(f"⚠️ {err} — 아래에서 '내장 데이터' 또는 'CSV 업로드'로 바꿔주세요.")
        else:
            st.session_state["yf_fetched"] = (df_fetched, n_before, yf_ticker)
            st.sidebar.success(f"✅ {yf_ticker} {len(df_fetched)-n_before}영업일 시세 로드 완료")
    if "yf_fetched" in st.session_state:
        _yf_result = st.session_state["yf_fetched"]
        st.sidebar.caption(f"현재 로드된 데이터: {_yf_result[2]} ({len(_yf_result[0])-_yf_result[1]}일)")
    else:
        st.sidebar.info("👆 '시세 불러오기'를 눌러야 데이터가 로드됩니다. 그 전까지는 내장 데이터로 표시됩니다.")
elif data_source == "CSV 업로드":
    price_file = st.sidebar.file_uploader(
        "가격데이터 CSV (열: 날짜,종가)", type=["csv"], key="price_csv"
    )

st.sidebar.caption("🔒 모드값(공격/방어) 붙여넣기는 상단 **'모드값 입력'** 탭에서 합니다.")

tab_dash, tab_log, tab_mode = st.tabs(["📊 대시보드", "📝 매매로그", "🔒 모드값 입력"])


def parse_pasted_mode(text: str):
    """엑셀에서 복사한 '날짜<TAB>모드' 텍스트를 dict로 변환. 콤마로 붙여넣어도 인식."""
    text = text.strip()
    if not text:
        return None
    try:
        parsed = pd.read_csv(io.StringIO(text), sep=None, engine="python", header=None)
    except Exception:
        return None
    if parsed.shape[1] < 2:
        return None
    parsed = parsed.iloc[:, :2]
    parsed.columns = ["날짜", "모드"]
    parsed["날짜"] = parsed["날짜"].astype(str).str.strip()
    parsed["모드"] = parsed["모드"].astype(str).str.strip()
    parsed = parsed[~parsed["모드"].str.contains("모드")]  # 헤더줄 섞여 들어온 경우 제거
    parsed = parsed[parsed["모드"].isin(["공격", "방어"])]
    if parsed.empty:
        return None
    return dict(zip(parsed["날짜"], parsed["모드"]))


with tab_mode:
    st.markdown("#### 🔒 실제 사이트 모드값(공격/방어) 붙여넣기")
    st.markdown(
        "모드 전환 조건 자체가 매뉴얼상 **비공개**라 정확히 재현할 수 없습니다. "
        "실제 백테스트 사이트의 **매매로그**에서 `날짜`, `모드` 두 열을 그대로 드래그해 복사(Ctrl+C)한 뒤, "
        "아래 상자에 붙여넣으면(Ctrl+V) 그 값을 그대로 사용합니다. "
        "(Fi값은 넣을 필요 없습니다 — 매수 로직 안에서 자동 계산됩니다.)"
    )
    mode_paste = st.text_area(
        "여기에 붙여넣기 (예: `26-01-02 금␉공격` 한 줄씩, 헤더 줄이 섞여도 됩니다)",
        value="",
        height=180,
        key="mode_paste",
        placeholder="26-01-02 금\t공격\n26-01-05 월\t공격\n26-01-06 화\t방어\n...",
    )
    _mode_preview = parse_pasted_mode(mode_paste)
    if _mode_preview:
        st.success(f"✅ {len(_mode_preview)}개 날짜의 모드값을 인식했습니다.")
        st.dataframe(
            pd.DataFrame(
                {"날짜": list(_mode_preview.keys()), "모드": list(_mode_preview.values())}
            ),
            use_container_width=True,
            hide_index=True,
            height=300,
        )
    else:
        st.info("아직 붙여넣은 값이 없습니다 — 값이 없으면 사이드바의 '자동(종가 vs MA20)' 판정을 사용합니다.")


if price_file is not None:
    df_price_src = pd.read_csv(price_file)
    df_price_src.columns = ["날짜", "종가"]
    N_WARMUP = 0  # 사용자 CSV는 위밍업 사전 데이터를 역산할 수 없어 그대로 사용(초반 며칠 MA(5)는 NaN일 수 있음)
elif data_source == "Yahoo Finance 실시간 조회" and _yf_result is not None:
    df_price_src, N_WARMUP, _ = _yf_result[0], _yf_result[1], _yf_result[2]
    # Yahoo Finance는 실제 과거 시세를 갖고 있으므로 앞의 N_WARMUP일이 그대로 진짜 warm-up 데이터가 됨
    # (내장 데이터처럼 역산해서 만든 근사치가 아니라 100% 실제 시세)
else:
    # 실제 사이트 매매로그의 MA(5) 값을 역산해서 만든 2025-12-26~12-31 4영업일 워밍업 데이터.
    # (01-02~01-05 구간 MA(5)가 site 로그와 정확히 일치하도록 역산 검증 완료 — 자세한 근거는 대화 참고)

    WARMUP_PRICE_DATA = [
        ("25-12-26 금", 44.17),
        ("25-12-29 월", 43.86),
        ("25-12-30 화", 43.64),
        ("25-12-31 수", 42.04),
    ]
    N_WARMUP = len(WARMUP_PRICE_DATA)
    df_price_src = pd.DataFrame(WARMUP_PRICE_DATA + RAW_PRICE_DATA, columns=["날짜", "종가"])

df_base = df_price_src.copy().reset_index(drop=True)
df_base["MA(5)"] = df_base["종가"].rolling(5).mean()
df_base["MA(20)"] = df_base["종가"].rolling(20).mean()

delta = df_base["종가"].diff()
# Wilder 방식(지수平활) RSI로 변경 — 대부분의 실전 차트/사이트가 쓰는 표준 RSI 공식.
# 단순 rolling(14) 평균은 site의 실제 RSI값과 계속 어긋나는 게 확인되어 교체함.
# alpha=1/14, adjust=False가 표준 Wilder smoothing과 동일. 워밍업(가격 이력)이 길수록 더 정확해짐
# — Yahoo Finance 실시간 조회는 이를 위해 시작일보다 충분히 이전부터 데이터를 받아온다(아래 lookback_days).
gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / 14, adjust=False, min_periods=1).mean()
loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / 14, adjust=False, min_periods=1).mean()
rs = gain / loss
df_base["RSI"] = 100 - (100 / (1 + rs))
df_base["RSI"] = df_base["RSI"].fillna(50)

mode_override = _mode_preview

with st.sidebar.form("bt_form"):
    st.markdown("### 📅 백테스트 기간 설정")
    all_dates = df_base["날짜"].tolist()[N_WARMUP:]  # 워밍업 날짜는 선택 목록에서 제외
    start_date, end_date = st.select_slider(
        "시작일과 종료일을 선택하세요", options=all_dates, value=(all_dates[0], all_dates[-1])
    )

    st.markdown("---")
    st.markdown("### ⚙️ 공통 파라미터")
    init_cap = st.number_input("시작 자본금 ($)", value=10000, step=1000)
    fee_pct = st.number_input("거래 수수료 (%)", value=0.07, step=0.01) / 100

    use_pasted_mode = st.checkbox(
        "🔒 붙여넣은 모드값 사용 ('모드값 입력' 탭 참고)",
        value=True,
        help="'모드값 입력' 탭에 붙여넣은 값이 없으면 이 체크와 상관없이 자동판정(종가 vs MA20)을 씁니다.",
    )
    apply_moc_priority = st.checkbox(
        "MOC 매도가 있으면 그 외 매도(익절) 배제", value=True
    )
    apply_t1_hold = st.checkbox(
        "전일종가>전일MA(5)면 1티어 만기MOC 보류", value=True
    )

    st.markdown("#### ⚔️ 공격모드")
    att_splits = st.number_input("공격 분할수", value=6, min_value=1, max_value=20)
    att_fi_buy_pct = st.number_input(
        "매수조건(종가%) — Fi(+)일 때 전일종가 대비 프리미엄", value=8.0, step=0.1,
        help="실제 사이트 '로켓셋' 프리셋 값(8.0)으로 검증 완료. "
             "Fi(-) 고정값은 아래 '자세히'에서 조절 가능(기본 -0.1%)."
    )
    with st.expander("공격 매수조건(Fi- 고정값) 자세히"):
        att_fi_neg_pct = st.number_input(
            "Fi(-)일 때 전일종가 대비 (%, 고정)", value=-0.1, step=0.05, key="att_fi_neg_pct"
        )
    with st.expander("공격 매도조건(정규화 계수 α) 자세히"):
        att_sell_min = st.number_input("매도% 최소값", value=0.1, step=0.1, key="att_sell_min")
        att_sell_max = st.number_input("매도% 최대값", value=3.0, step=0.1, key="att_sell_max")
        att_sell_a = st.number_input("매도% 지수 α", value=0.4, step=0.1, key="att_sell_a")
    with st.expander("공격 보유기간(정규화 계수 α) 자세히"):
        att_hold_min = st.number_input("보유일 최소값", value=7, step=1, key="att_hold_min")
        att_hold_max = st.number_input("보유일 최대값", value=30, step=1, key="att_hold_max")
        att_hold_a = st.number_input("보유일 지수 α", value=2.0, step=0.1, key="att_hold_a")
    st.caption("공격모드 매수비중: 균등분할 고정(매뉴얼상 변경 불가)")

    st.markdown("#### 🛡️ 방어모드")
    def_splits = st.number_input("방어 분할수", value=5, min_value=1, max_value=20)
    def_ma_n = st.number_input("MA기준 (n)", value=3, min_value=2, max_value=60)
    def_buy_cond1 = st.number_input("매수조건1 (MA%)", value=-0.6, step=0.1)
    def_buy_cond2 = st.number_input("매수조건2 (전일종가%)", value=5.5, step=0.1)
    def_sell_cond = st.number_input("매도조건 (MA%)", value=0.7, step=0.1)
    def_max_hold = st.number_input("방어 최대 보유일(고정)", value=8, min_value=1)
    def_weight_text = st.text_input(
        "방어 매수비중(쉼표구분, 분할수와 개수 일치시 적용·아니면 균등)", value="6,13,20,27,34"
    )

    st.markdown("---")
    run_clicked = st.form_submit_button("🚀 백테스트 실행", use_container_width=True)

st.sidebar.caption(
    "파라미터나 모드값을 바꿔도 위 **'백테스트 실행'** 버튼을 눌러야 결과에 반영됩니다."
)

# ────────────────────────────────────────────────────────────────
# 4. 방어모드 가격계산용 사전 컬럼 (MA기준-1 직전 종가합)
# ────────────────────────────────────────────────────────────────
df_base["방어_직전합"] = df_base["종가"].shift(1).rolling(window=max(def_ma_n - 1, 1)).sum()


# ────────────────────────────────────────────────────────────────
# 5. 백테스트 엔진
# ────────────────────────────────────────────────────────────────
def determine_mode(date, close, ma20):
    if use_pasted_mode and mode_override and date in mode_override:
        return mode_override[date]
    return "공격" if (not np.isnan(ma20) and close >= ma20) else "방어"


def run_backtest(df_slice: pd.DataFrame, start_capital: float):
    df_slice = df_slice.reset_index(drop=True)
    cash = start_capital
    open_positions = []  # list of dict: tier, shares, buy_price, mode, hold_days, buy_rsi
    history = []
    trades = []  # 개별 청산 건 기록 (승률/모드별 통계용)
    peak_equity = start_capital
    att_weight = 1.0 / att_splits
    def_weights = parse_weight_vector(def_weight_text, def_splits)

    for idx, row in df_slice.iterrows():
        date = row["날짜"]
        close = row["종가"]
        ma5 = row["MA(5)"] if not np.isnan(row["MA(5)"]) else close
        ma20 = row["MA(20)"]
        rsi = row["RSI"]
        prev_close = df_slice.loc[idx - 1, "종가"] if idx > 0 else close
        prev_prev_close = df_slice.loc[idx - 2, "종가"] if idx > 1 else prev_close
        prev_ma5 = df_slice.loc[idx - 1, "MA(5)"] if idx > 0 else ma5
        prev_sum_def = row["방어_직전합"]

        mode = determine_mode(date, close, ma20)

        for pos in open_positions:
            pos["hold_days"] += 1

        # ---- 1. 매도 판정 -------------------------------------------------
        moc_candidates = []   # (pos, forced_reason)
        loc_candidates = []   # (pos, ) target 매도 후보

        for pos in open_positions:
            if pos["mode"] == "공격":
                target_pct = attack_sell_pct(pos["buy_rsi"], att_sell_min, att_sell_max, att_sell_a)
                max_hold = attack_hold_days(pos["buy_rsi"], att_hold_min, att_hold_max, att_hold_a)
                target_price = ceil_2(pos["buy_price"] * (1 + target_pct))
                hit_target = close >= target_price
                expired = pos["hold_days"] >= max_hold
            else:
                sell_p = defense_price(prev_sum_def, def_ma_n, def_sell_cond)
                target_price = ceil_2(sell_p) if not np.isnan(sell_p) else np.inf
                hit_target = close >= target_price
                expired = pos["hold_days"] >= def_max_hold

            t1_delay = False
            if apply_t1_hold and pos["tier"] == 1 and hit_target:
                # 매뉴얼: "전일종가>전일MA(5)면 1티어는 매도보류(MOC 제외)"
                # → MOC(만기 강제청산)는 예외, 정상 익절(LOC) 매도가 보류 대상.
                if idx > 0 and prev_close > prev_ma5:
                    t1_delay = True

            if expired and (not hit_target or t1_delay):
                # 만기(MOC)는 목표가 도달 여부와 무관하게 발동. 다만 "목표가 도달 + 1티어 매도보류"가
                # 겹치는 경우, 매도보류가 막고 있는 상황을 만기가 예외적으로 뚫고 강제청산한다(매뉴얼
                # "1티어 매도보류(MOC 제외)" 규정 — MOC는 이 보류의 적용 대상에서 제외됨을 의미).
                # 실제 사이트 로그(01-12 매수 T1 → 01-23 공T1MOC)로 검증된 동작.
                moc_candidates.append(pos)
            elif hit_target and not t1_delay:
                loc_candidates.append(pos)

        sell_qty_today = 0
        sell_amount_today = 0.0
        sell_pnl_today = 0.0
        sell_cond_today = []
        sold_ids = set()

        if moc_candidates:
            # MOC 매도가 있는 날 → 그 외(LOC 익절) 매도는 발생하지 않음
            targets = moc_candidates
        else:
            targets = loc_candidates if not apply_moc_priority else loc_candidates

        # 방어모드는 목표가 도달시 "전량"(방어 포지션 전부) 동시청산
        def_hit_all = any(p["mode"] == "방어" for p in targets) and not moc_candidates
        if def_hit_all:
            extra_def = [p for p in open_positions if p["mode"] == "방어" and p not in targets]
            targets = targets + extra_def

        for pos in targets:
            sell_val = pos["shares"] * close * (1 - fee_pct)
            cost_val = pos["shares"] * pos["buy_price"]
            pnl = sell_val - cost_val
            cash += sell_val
            sell_qty_today += pos["shares"]
            sell_amount_today += sell_val
            sell_pnl_today += pnl
            reason = "MOC만기" if pos in moc_candidates else "익절"
            sell_cond_today.append(f"T{pos['tier']}{pos['mode'][0]} {reason}")
            sold_ids.add(id(pos))
            trades.append(
                {
                    "날짜": date,
                    "모드": pos["mode"],
                    "티어": pos["tier"],
                    "손익": pnl,
                    "손익률": (pnl / cost_val * 100) if cost_val > 0 else 0.0,
                    "사유": reason,
                }
            )

        open_positions = [p for p in open_positions if id(p) not in sold_ids]

        # ---- 2. 매수 판정 -------------------------------------------------
        max_splits = att_splits if mode == "공격" else def_splits
        curr_tier = len([p for p in open_positions if p["mode"] == mode]) + 1
        # (티어는 모드별로 별도 관리: 공격 진입중 방어로 바뀌면 방어 티어를 새로 센다)

        buy_qty_today = 0
        buy_amount_today = 0.0

        if curr_tier <= max_splits and idx > 0:
            # idx==0(첫날)은 "전일" 데이터가 없어 매수 자체가 발생하지 않음 (사이트 로그와 일치)
            curr_equity = cash + sum(p["shares"] * close for p in open_positions)
            weights = [att_weight] * att_splits if mode == "공격" else def_weights
            remaining_weight_sum = sum(weights[curr_tier - 1:])
            next_weight = weights[curr_tier - 1] if curr_tier - 1 < len(weights) else weights[-1]
            budget = cash * (next_weight / remaining_weight_sum) if remaining_weight_sum > 0 else 0

            if mode == "공격":
                fi = prev_close - prev_prev_close  # Fi = 전일 종가 등락 부호(전전일 대비)
                if fi >= 0:
                    buy_limit = floor_2(prev_close * (1 + att_fi_buy_pct / 100.0))
                else:
                    buy_limit = floor_2(prev_close * (1 + att_fi_neg_pct / 100.0))
            else:
                p1 = defense_price(prev_sum_def, def_ma_n, def_buy_cond1)
                p2 = prev_close * (1 + def_buy_cond2 / 100.0)
                cands = [v for v in [p1, p2] if not np.isnan(v)]
                buy_limit = floor_2(min(cands)) if cands else close

            # LOC 매수: 종가가 지정가(buy_limit) 이하일 때만 체결. 수량은 지정가 기준으로 보수적으로
            # 산정하고(예산 초과 방지), 실제 체결/지불은 그날 종가로 이뤄짐(사이트 로그로 검증됨).
            fill_ok = close <= buy_limit

            if fill_ok and budget > 0 and cash >= 0 and buy_limit > 0:
                shares = int(budget / (buy_limit * (1 + fee_pct)))
                if shares > 0:
                    cost = shares * close * (1 + fee_pct)
                    if cost <= cash:
                        cash -= cost
                        buy_qty_today = shares
                        buy_amount_today = cost
                        open_positions.append(
                            {
                                "tier": curr_tier,
                                "shares": shares,
                                "buy_price": close,
                                "mode": mode,
                                "hold_days": 0,  # 매수일=0, 다음날부터 +1 (T1#2 인스턴스 01-12→01-23 MOC로 검증: hold_days=8=max_hold)
                                "buy_rsi": rsi,
                            }
                        )

        # ---- 3. 집계 --------------------------------------------------
        total_shares = sum(p["shares"] for p in open_positions)
        pos_val = total_shares * close
        total_equity = cash + pos_val
        avg_price = (
            sum(p["shares"] * p["buy_price"] for p in open_positions) / total_shares
            if total_shares > 0
            else 0
        )
        unrealized_pnl = sum(p["shares"] * (close - p["buy_price"]) for p in open_positions)

        peak_equity = max(peak_equity, total_equity)
        drawdown = ((total_equity - peak_equity) / peak_equity) * 100
        cum_return = ((total_equity - start_capital) / start_capital) * 100
        cash_ratio = (cash / total_equity) * 100 if total_equity > 0 else 0

        history.append(
            {
                "날짜": date,
                "종가": close,
                "MA(5)": round(ma5, 2),
                "RSI": round(rsi, 1),
                "모드": mode,
                "실매수수량": buy_qty_today if buy_qty_today > 0 else "-",
                "실매수금액": f"{buy_amount_today:,.2f}" if buy_amount_today > 0 else "-",
                "매도조건": ", ".join(sell_cond_today) if sell_cond_today else "-",
                "실매도수량": sell_qty_today if sell_qty_today > 0 else "-",
                "매도금액": f"{sell_amount_today:,.2f}" if sell_amount_today > 0 else "-",
                "손익": f"{sell_pnl_today:,.2f}" if sell_pnl_today != 0 else "-",
                "보유수량": total_shares,
                "평단가": round(avg_price, 2) if avg_price > 0 else "-",
                "총자산": round(total_equity, 2),
                "누적수익률": f"{cum_return:.2f}%",
                "DD": f"{drawdown:.2f}%",
                "현금": round(cash, 2),
                "현금비중": f"{cash_ratio:.1f}%",
                "미실현손익": round(unrealized_pnl, 2),
            }
        )

    df_hist = pd.DataFrame(history)
    for col in ["실매수수량", "실매도수량", "평단가"]:
        df_hist[col] = df_hist[col].astype(str)
    df_trades = pd.DataFrame(trades)
    return df_hist, df_trades


# ────────────────────────────────────────────────────────────────
# 6. 실행 및 출력
# ────────────────────────────────────────────────────────────────
s_idx = all_dates.index(start_date) + N_WARMUP
e_idx = all_dates.index(end_date) + N_WARMUP
df_target = df_base.iloc[s_idx : e_idx + 1].copy()

df_result, df_trades = run_backtest(df_target, init_cap)

# ---- 지표 계산 -------------------------------------------------------
final_eq = float(df_result["총자산"].iloc[-1])
total_profit = final_eq - init_cap
total_return_pct = (final_eq / init_cap - 1) * 100
n_days = len(df_result)
cagr_pct = ((final_eq / init_cap) ** (252.0 / n_days) - 1) * 100 if n_days > 0 and final_eq > 0 else float("nan")

dd_series = df_result["DD"].apply(lambda s: float(s.replace("%", "")))
mdd = dd_series.min()
mdd_date = df_result.loc[dd_series.idxmin(), "날짜"] if len(dd_series) else "-"
avg_dd = dd_series.mean()

equity_series = df_result["총자산"].astype(float)
daily_ret = equity_series.pct_change().dropna()
sharpe = (daily_ret.mean() / daily_ret.std() * (252 ** 0.5)) if daily_ret.std() > 0 else float("nan")
calmar = (cagr_pct / abs(mdd)) if mdd != 0 else float("nan")

buy_days = (df_result["실매수수량"] != "-").sum()
buy_fill_pct = buy_days / n_days * 100 if n_days else 0
final_unrealized = float(df_result["미실현손익"].iloc[-1])
final_cash_ratio = df_result["현금비중"].iloc[-1]

if not df_trades.empty:
    win_rate = (df_trades["손익"] > 0).mean() * 100
else:
    win_rate = float("nan")


def mode_stats(mode_name: str):
    if df_trades.empty:
        return dict(profit=0.0, win_rate=float("nan"), count=0)
    sub = df_trades[df_trades["모드"] == mode_name]
    if sub.empty:
        return dict(profit=0.0, win_rate=float("nan"), count=0)
    return dict(
        profit=sub["손익"].sum(),
        win_rate=(sub["손익"] > 0).mean() * 100,
        count=len(sub),
    )


att_stats = mode_stats("공격")
def_stats = mode_stats("방어")

# ---- 📊 대시보드 탭 ----------------------------------------------------
with tab_dash:
    st.markdown(f"### 📊 듀얼스나이퍼 동적 백테스트 결과 ({start_date} ~ {end_date})")
    _mode_src_note = (
        f"🔒 붙여넣은 모드값 사용 중 ({len(mode_override)}일)"
        if use_pasted_mode and mode_override
        else "⚙️ 자동판정(종가 vs MA20) 사용 중"
    )
    st.caption(f"{_mode_src_note} · 파라미터 변경 후에는 사이드바의 '백테스트 실행'을 눌러야 아래 결과에 반영됩니다.")

    r1c1, r1c2, r1c3, r1c4, r1c5, r1c6 = st.columns(6)
    r1c1.metric("수익금", f"${total_profit:,.2f}")
    r1c2.metric("수익률", f"{total_return_pct:.2f}%")
    r1c3.metric("CAGR", f"{cagr_pct:.2f}%" if cagr_pct == cagr_pct else "-")
    r1c4.metric("MDD", f"{mdd:.2f}%")
    r1c5.metric("샤프지수", f"{sharpe:.2f}" if sharpe == sharpe else "-")
    r1c6.metric("칼마지수", f"{calmar:.2f}" if calmar == calmar else "-")

    r2c1, r2c2, r2c3, r2c4, r2c5, r2c6 = st.columns(6)
    r2c1.metric("MDD 기록일", str(mdd_date))
    r2c2.metric("평균 DD", f"{avg_dd:.2f}%")
    r2c3.metric("전체 승률", f"{win_rate:.1f}%" if win_rate == win_rate else "-")
    r2c4.metric("매수 체결일", f"{buy_days}/{n_days} ({buy_fill_pct:.0f}%)")
    r2c5.metric("미체결손익", f"${final_unrealized:,.2f}")
    r2c6.metric("최종 현금비중", final_cash_ratio)

    st.markdown("---")
    m1, m2 = st.columns(2)
    with m1:
        st.markdown("#### ⚔️ 공격모드")
        a1, a2, a3 = st.columns(3)
        a1.metric("공격 수익금", f"${att_stats['profit']:,.2f}")
        a2.metric("공격 승률", f"{att_stats['win_rate']:.1f}%" if att_stats["win_rate"] == att_stats["win_rate"] else "-")
        a3.metric("공격 청산건수", f"{att_stats['count']}")
    with m2:
        st.markdown("#### 🛡️ 방어모드")
        d1, d2, d3 = st.columns(3)
        d1.metric("방어 수익금", f"${def_stats['profit']:,.2f}")
        d2.metric("방어 승률", f"{def_stats['win_rate']:.1f}%" if def_stats["win_rate"] == def_stats["win_rate"] else "-")
        d3.metric("방어 청산건수", f"{def_stats['count']}")

    st.markdown("---")
    st.markdown("#### 📈 자산 추이")
    st.line_chart(df_result.set_index("날짜")["총자산"])

    with st.expander("⚠️ 이 백테스트가 원본 사이트와 다를 수 있는 지점(가정 목록) 펼쳐보기"):
        st.markdown(
            """
- **모드 전환 조건**: 매뉴얼에 "비공개"로 명시되어 있어 자동판정(종가 vs MA20)은 추정치입니다.
  가장 정확히 맞추려면 **'모드값 입력'** 탭에 실제 사이트의 날짜별 모드값을 엑셀에서 복사해 그대로 붙여넣어 주세요.
- **Fi 지표 정의**: Fi는 사용자가 값을 넣어줄 필요가 없습니다 — 매수 시점 로직(전일종가-전일MA(5) 부호)으로
  가격데이터에서 자동 계산됩니다. 매뉴얼에 Fi의 정확한 정의가 없어 이 방식으로 가정했다는 점만 참고해주세요.
- **LOC/MOC 체결가**: 일봉(종가)만 있어 시가·고가·저가 기반 체결 검증이 불가합니다.
  계산된 지정가가 그날 그대로 체결된 것으로 간주하는 근사치입니다.
- **방어모드 보유기간**: 매뉴얼에 공식이 없어 고정값 파라미터로 두었습니다.
- **CAGR/샤프/칼마/승률**: 원본 사이트의 정확한 산출식이 공개되어 있지 않아 일반적인 정의(연환산 252거래일 기준,
  일간수익률 표준편차 기준 샤프지수 등)로 계산한 값입니다. 공격/방어 수익금·승률은 각 모드에서 청산된
  개별 거래 기준 합산치이며, 원본 사이트의 "공격 CAGR/공격 MDD"처럼 자산곡선을 모드별로 분리한 것은 아닙니다.
            """
        )

# ---- 📝 매매로그 탭 -----------------------------------------------------
with tab_log:
    st.markdown(f"### 📝 매매로그 ({start_date} ~ {end_date})")
    st.caption(_mode_src_note)
    st.dataframe(df_result, use_container_width=True, hide_index=True, height=700)

