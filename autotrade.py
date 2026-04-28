import os
import json
import requests
import hashlib
from datetime import datetime, timezone
import pyupbit
from openai import OpenAI
from dotenv import load_dotenv
from ta.utils import dropna
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator
from ta.trend import MACD

from chart_capture import capture_upbit_chart, image_file_to_data_url

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_STATE_FILE = os.path.join(BASE_DIR, "autotrade_state.json")
YOUTUBE_TRANSCRIPT_FILES = [
    os.path.join(BASE_DIR, "youtube_transcript.md"),
    os.path.join(BASE_DIR, "youtube_transcript2.md"),
]
RESERVED_BTC_AMOUNT = float(os.getenv("RESERVED_BTC_AMOUNT", "0.01607018"))
RESERVED_ETH_AMOUNT = float(os.getenv("RESERVED_ETH_AMOUNT", "0.23145639"))
BUY_ALLOCATION_RATIO = 0.25
SELL_ALLOCATION_RATIO = 0.25
TRADE_INTERVAL_SECONDS = int(os.getenv("TRADE_INTERVAL_SECONDS", "7200"))
DUPLICATE_TRADE_COOLDOWN_SECONDS = int(os.getenv("DUPLICATE_TRADE_COOLDOWN_SECONDS", "21600"))


def ai_trading_bot():
    """AI 기반 비트코인 자동매매 봇"""
    client = OpenAI()

    def load_bot_state():
        try:
            with open(BOT_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_bot_state(state):
        try:
            with open(BOT_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"상태 저장 실패: {e}")

    trade_decision_schema = {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["buy", "sell", "hold"],
                "description": "Final trading decision"
            },
            "reason": {
                "type": "string",
                "description": "Short explanation for the decision"
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence score between 0 and 1"
            }
        },
        "required": ["decision", "reason", "confidence"],
        "additionalProperties": False
    }

    def load_text_file(file_path, max_chars=8000):
        """로컬 텍스트 파일을 읽어 너무 길면 자른다."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if len(text) > max_chars:
                return text[:max_chars] + "\n...[truncated]"
            return text
        except Exception as e:
            print(f"텍스트 파일 읽기 실패({file_path}): {e}")
            return None

    def load_youtube_strategy_notes():
        """유튜브 자막 md 파일들을 불러와 전략 참고용 컨텍스트로 결합한다."""
        notes = []
        for file_path in YOUTUBE_TRANSCRIPT_FILES:
            file_text = load_text_file(file_path)
            if file_text:
                notes.append({
                    "source_file": os.path.basename(file_path),
                    "text": file_text,
                })
        return notes or None

    # 1. 업비트 차트 데이터 가져오기
    daily_ohlcv = pyupbit.get_ohlcv("KRW-BTC", count=30, interval="day")
    hourly_ohlcv = pyupbit.get_ohlcv("KRW-BTC", count=24, interval="minute60")

    # 2. 보조지표 추가
    # 일봉 보조지표
    daily_ohlcv = dropna(daily_ohlcv)
    bb_daily = BollingerBands(close=daily_ohlcv["close"], window=20, window_dev=2)
    daily_ohlcv["bb_bbm"] = bb_daily.bollinger_mavg()
    daily_ohlcv["bb_bbh"] = bb_daily.bollinger_hband()
    daily_ohlcv["bb_bbl"] = bb_daily.bollinger_lband()
    rsi_daily = RSIIndicator(close=daily_ohlcv["close"], window=14)
    daily_ohlcv["rsi"] = rsi_daily.rsi()
    macd_daily = MACD(close=daily_ohlcv["close"])
    daily_ohlcv["macd"] = macd_daily.macd()
    daily_ohlcv["macd_signal"] = macd_daily.macd_signal()
    daily_ohlcv["macd_diff"] = macd_daily.macd_diff()

    # 시간봉 보조지표
    hourly_ohlcv = dropna(hourly_ohlcv)
    bb_hourly = BollingerBands(close=hourly_ohlcv["close"], window=20, window_dev=2)
    hourly_ohlcv["bb_bbm"] = bb_hourly.bollinger_mavg()
    hourly_ohlcv["bb_bbh"] = bb_hourly.bollinger_hband()
    hourly_ohlcv["bb_bbl"] = bb_hourly.bollinger_lband()
    rsi_hourly = RSIIndicator(close=hourly_ohlcv["close"], window=14)
    hourly_ohlcv["rsi"] = rsi_hourly.rsi()
    macd_hourly = MACD(close=hourly_ohlcv["close"])
    hourly_ohlcv["macd"] = macd_hourly.macd()
    hourly_ohlcv["macd_signal"] = macd_hourly.macd_signal()
    hourly_ohlcv["macd_diff"] = macd_hourly.macd_diff()

    # 3. 오더북 데이터
    orderbook = pyupbit.get_orderbook(ticker="KRW-BTC")

    # 4. 공포탐욕지수 가져오기
    fng_value = None
    fng_classification = None
    try:
        fng_url = "https://api.alternative.me/fng/?limit=1"
        resp = requests.get(fng_url, timeout=5)
        if resp.status_code == 200:
            fng_data = resp.json()
            if "data" in fng_data and len(fng_data["data"]) > 0:
                fng_value = fng_data["data"][0]["value"]
                fng_classification = fng_data["data"][0]["value_classification"]
    except Exception as e:
        print(f"공포탐욕지수 API 호출 실패: {e}")

    # 5. 투자 상태(잔고) 조회
    access_key = os.getenv("UPBIT_ACCESS_KEY")
    secret_key = os.getenv("UPBIT_SECRET_KEY")
    upbit = pyupbit.Upbit(access_key, secret_key)
    balances_all = upbit.get_balances()
    # BTC, ETH, KRW만 남기기
    balances = [b for b in balances_all if b.get('currency') in ('BTC', 'ETH', 'KRW')]

    btc_balance = float(next((b.get("balance", 0) for b in balances if b.get("currency") == "BTC"), 0) or 0)
    eth_balance = float(next((b.get("balance", 0) for b in balances if b.get("currency") == "ETH"), 0) or 0)
    krw_balance = float(next((b.get("balance", 0) for b in balances if b.get("currency") == "KRW"), 0) or 0)

    current_prices = pyupbit.get_current_price(["KRW-BTC", "KRW-ETH"])
    btc_price = float(current_prices.get("KRW-BTC", 0) or 0)
    eth_price = float(current_prices.get("KRW-ETH", 0) or 0)

    reserved_btc_value = min(btc_balance, RESERVED_BTC_AMOUNT) * btc_price
    reserved_eth_value = min(eth_balance, RESERVED_ETH_AMOUNT) * eth_price
    protected_value_krw = reserved_btc_value + reserved_eth_value
    total_account_value_krw = krw_balance + (btc_balance * btc_price) + (eth_balance * eth_price)
    usable_capital_krw = max(0.0, total_account_value_krw - protected_value_krw)
    buy_budget_krw = min(krw_balance, usable_capital_krw * BUY_ALLOCATION_RATIO)
    sell_budget_btc = max(0.0, btc_balance - RESERVED_BTC_AMOUNT) * SELL_ALLOCATION_RATIO

    # 6. 차트 스크린샷 캡처 + base64 data URL 변환 (Vision 입력용)
    chart_image_path = capture_upbit_chart()
    chart_image_data_url = image_file_to_data_url(chart_image_path)

    # 6-1. 유튜브 자막 md 파일들 불러오기
    youtube_strategy_notes = load_youtube_strategy_notes()

    # 7. 현재 상태 출력
    print("=" * 50)
    print("현재 잔고:")
    print(balances)
    print(f"보호 BTC 수량: {RESERVED_BTC_AMOUNT:.8f} BTC")
    print(f"보호 ETH 수량: {RESERVED_ETH_AMOUNT:.8f} ETH")
    print(f"사용 가능 KRW: {usable_capital_krw:,.0f}원")
    print(f"매수 예산: {buy_budget_krw:,.0f}원")
    print(f"매도 BTC 예산: {sell_budget_btc:.8f} BTC")
    print("\n공포탐욕지수:")
    print(f"Value: {fng_value}, Classification: {fng_classification}")
    print(f"차트 이미지: {chart_image_path}")
    print(f"유튜브 전략 노트: {'loaded' if youtube_strategy_notes else 'not loaded'}")
    print("=" * 50)

    # 8. AI에게 텍스트 + 이미지(Vision) 데이터 제공하고 판단 받기

    def df_to_records_with_string_timestamp(df, n=5):
        """Datetime index를 문자열 컬럼으로 바꿔 JSON 직렬화 가능한 records로 변환"""
        tmp = df.tail(n).reset_index()
        first_col = tmp.columns[0]
        tmp[first_col] = tmp[first_col].astype(str)
        return tmp.to_dict(orient="records")

    ai_input_data = {
        "daily_ohlcv": df_to_records_with_string_timestamp(daily_ohlcv, n=5),
        "hourly_ohlcv": df_to_records_with_string_timestamp(hourly_ohlcv, n=5),
        "orderbook": orderbook,
        "balances": balances,
        "account_summary": {
            "btc_balance": btc_balance,
            "eth_balance": eth_balance,
            "krw_balance": krw_balance,
            "btc_price": btc_price,
            "eth_price": eth_price,
            "protected_btc_amount": RESERVED_BTC_AMOUNT,
            "protected_eth_amount": RESERVED_ETH_AMOUNT,
            "protected_value_krw": protected_value_krw,
            "usable_capital_krw": usable_capital_krw,
            "buy_budget_krw": buy_budget_krw,
            "sell_budget_btc": sell_budget_btc,
        },
        "fear_and_greed_index": {
            "value": fng_value,
            "classification": fng_classification
        },
        "youtube_strategy_notes": youtube_strategy_notes,
    }

    system_prompt = (
        "You are a Bitcoin investment expert. Use both structured market data and the provided BTC chart image. "
        "Provide a buy/sell/hold decision. Consider:\n"
        "- Bollinger Bands (bb_bbm, bb_bbh, bb_bbl)\n"
        "- RSI (rsi)\n"
        "- MACD (macd, macd_signal, macd_diff)\n"
        "- Fear and Greed Index\n"
        "- Current balances and orderbook\n"
        "- Visual chart context from screenshot\n"
        "- YouTube strategy notes loaded from youtube_transcript.md and youtube_transcript2.md\n"
        "You must always reference and apply the trading principles from the YouTube content titled '한국의 전설적인 투자자 워뇨띠의 매매법' when evaluating the current market context and making the final decision. "
        "Treat those principles as a persistent strategy guideline, while still balancing real-time indicators and risk controls.\n"
        "Return only data that fits the provided JSON schema.\n"
        'Example: {"decision":"buy","reason":"technical reason here","confidence":0.85}'
    )

    user_contents = [
        {
            "type": "input_text",
            "text": (
                "Based on this data, should I buy, sell, or hold BTC?\n\n"
                f"{json.dumps(ai_input_data, ensure_ascii=False, default=str)}"
            )
        }
    ]
    if chart_image_data_url:
        user_contents.append(
            {
                "type": "input_image",
                "image_url": chart_image_data_url
            }
        )

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": system_prompt
                    }
                ]
            },
            {
                "role": "user",
                "content": user_contents
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "trade_decision",
                "strict": True,
                "schema": trade_decision_schema
            }
        }
    )

    # 9. AI 응답 파싱
    try:
        result_text = response.output_text
        result = json.loads(result_text)
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"AI 응답 파싱 오류: {e}")
        return

    state = load_bot_state()
    current_signature_source = {
        "decision": result.get("decision"),
        "account_summary": ai_input_data.get("account_summary"),
        "daily_tail": ai_input_data.get("daily_ohlcv"),
        "hourly_tail": ai_input_data.get("hourly_ohlcv"),
        "orderbook": ai_input_data.get("orderbook"),
        "fear_and_greed_index": ai_input_data.get("fear_and_greed_index"),
    }
    current_signature = hashlib.sha256(
        json.dumps(current_signature_source, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    last_trade_signature = state.get("last_trade_signature")
    last_trade_decision = state.get("last_trade_decision")
    last_trade_at = state.get("last_trade_at")

    now_utc = datetime.now(timezone.utc)
    duplicate_trade_blocked = False
    if last_trade_signature == current_signature and last_trade_decision == result.get("decision"):
        duplicate_trade_blocked = True
        print("중복 매매 방지: 동일한 시장 컨텍스트와 동일한 결정을 감지하여 이번 실행은 건너뜁니다.")
    elif last_trade_at:
        try:
            last_trade_dt = datetime.fromisoformat(last_trade_at)
            elapsed_seconds = (now_utc - last_trade_dt).total_seconds()
            if elapsed_seconds < DUPLICATE_TRADE_COOLDOWN_SECONDS and last_trade_decision == result.get("decision"):
                duplicate_trade_blocked = True
                print(
                    f"중복 매매 방지: 마지막 {last_trade_decision} 주문 이후 {elapsed_seconds:.0f}초 경과로, "
                    f"쿨다운({DUPLICATE_TRADE_COOLDOWN_SECONDS}초) 내 동일 결정을 차단합니다."
                )
        except Exception:
            pass

    print(f"\nAI 판단: {result.get('decision', 'ERROR')}")
    print(f"이유: {result.get('reason', 'N/A')}")
    print(f"신뢰도: {result.get('confidence', 'N/A')}")

    if duplicate_trade_blocked:
        save_bot_state({
            "last_decision": result.get("decision"),
            "last_trade_signature": current_signature,
            "last_trade_decision": last_trade_decision,
            "last_trade_at": last_trade_at,
            "last_check_at": now_utc.isoformat(),
        })
        return

    # 10. 매수/매도/관망 실행
    if result["decision"] == "buy":
        try:
            if buy_budget_krw <= 0:
                print("매수 가능 KRW가 없어 매수하지 않습니다.")
                return
            order_amount = round(buy_budget_krw, 0)
            print(
                f"매수 주문 직전: decision=buy, krw_balance={krw_balance:,.0f}, "
                f"usable_capital_krw={usable_capital_krw:,.0f}, buy_allocation_ratio={BUY_ALLOCATION_RATIO:.2f}, "
                f"buy_budget_krw={buy_budget_krw:,.0f}, order_amount={order_amount:,.0f}"
            )
            order_result = upbit.buy_market_order("KRW-BTC", order_amount)
            print(f"매수 주문 완료: {order_result}")
            save_bot_state({
                "last_decision": result.get("decision"),
                "last_trade_signature": current_signature,
                "last_trade_decision": result.get("decision"),
                "last_trade_at": now_utc.isoformat(),
                "last_order_result": order_result,
            })
        except Exception as e:
            print(f"매수 주문 실패: {e}")

    elif result["decision"] == "sell":
        try:
            if sell_budget_btc <= 0:
                print("매도 가능한 BTC가 없어 매도하지 않습니다.")
                return
            print(
                f"매도 주문 직전: decision=sell, btc_balance={btc_balance:.8f}, "
                f"reserved_btc_amount={RESERVED_BTC_AMOUNT:.8f}, sell_allocation_ratio={SELL_ALLOCATION_RATIO:.2f}, "
                f"sell_budget_btc={sell_budget_btc:.8f}, order_volume={sell_budget_btc:.8f}, btc_price={btc_price:,.0f}"
            )
            order_result = upbit.sell_market_order("KRW-BTC", sell_budget_btc)
            print(f"매도 주문 완료: {order_result}")
            save_bot_state({
                "last_decision": result.get("decision"),
                "last_trade_signature": current_signature,
                "last_trade_decision": result.get("decision"),
                "last_trade_at": now_utc.isoformat(),
                "last_order_result": order_result,
            })
        except Exception as e:
            print(f"매도 주문 실패: {e}")

    elif result["decision"] == "hold":
        print("관망 상태 유지")


if __name__ == "__main__":
    import time
    while True:
        try:
            ai_trading_bot()
        except Exception as e:
            print(f"봇 실행 중 오류 발생: {e}")
        time.sleep(TRADE_INTERVAL_SECONDS)  # 기본 2시간마다 실행
