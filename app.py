import os
import time
import threading
from contextlib import suppress
from dataclasses import dataclass, asdict
import importlib
import importlib.util
import json
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException

app = FastAPI(title="VN30F1M Trading Forecast API", version="2.0.0")

URL = "https://histdatafeed.vps.com.vn/tradingview/history"
SYMBOL = os.getenv("SYMBOL", "VN30F1M")
RESOLUTION = os.getenv("RESOLUTION", "1")
COUNTBACK = int(os.getenv("COUNTBACK", "330"))
UPDATE_SECONDS = int(os.getenv("UPDATE_SECONDS", "10"))
MIN_BARS = int(os.getenv("MIN_BARS", "50"))
CONTRACT_MULTIPLIER = float(os.getenv("CONTRACT_MULTIPLIER", "100000"))
MARGIN_RATE = float(os.getenv("MARGIN_RATE", "0.13"))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "0.01"))
HF_API_TOKEN = os.getenv("HF_API_TOKEN", "")
HF_MODEL_ID = os.getenv("HF_MODEL_ID", "HuggingFaceH4/zephyr-7b-beta")
HF_MODEL_CANDIDATES = [
    m.strip()
    for m in os.getenv(
        "HF_MODEL_CANDIDATES",
        "HuggingFaceH4/zephyr-7b-beta,google/flan-t5-base,tiiuae/falcon-7b-instruct",
    ).split(",")
    if m.strip()
]
HF_INFERENCE_BASES = [
    "https://router.huggingface.co/hf-inference/models",
    "https://api-inference.huggingface.co/models",
]
USE_LOCAL_HF_MODEL = os.getenv("USE_LOCAL_HF_MODEL", "0").lower() in {"1", "true", "yes"}
HF_LOCAL_MODEL_ID = os.getenv("HF_LOCAL_MODEL_ID", HF_MODEL_ID)
AI_DEBUG_ERRORS = os.getenv("AI_DEBUG_ERRORS", "0").lower() in {"1", "true", "yes"}
KEEPALIVE_EXTERNAL_URL = os.getenv("KEEPALIVE_EXTERNAL_URL", "https://mrlongpro-vn30.hf.space/ping")


@dataclass
class ForecastState:
    signal: str = "WAIT"
    price: float = 0.0
    confidence: float = 0.0
    score: float = 0.0
    reason: str = "System warming up"
    updated_at: int = 0


state = ForecastState()
state_lock = threading.Lock()
http = requests.Session()
update_thread: Optional[threading.Thread] = None
keepalive_thread: Optional[threading.Thread] = None
local_model_lock = threading.Lock()
local_tokenizer = None
local_model = None


# ===== INDICATORS =====
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def build_features(data: Dict[str, List[float]]) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "time": data["t"],
            "open": data["o"],
            "high": data["h"],
            "low": data["l"],
            "close": data["c"],
            "volume": data["v"],
        }
    )

    df["ret_1"] = df["close"].pct_change()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma50"] = df["close"].rolling(50).mean()
    df["std20"] = df["close"].rolling(20).std()
    df["zscore20"] = (df["close"] - df["ma20"]) / df["std20"].replace(0, pd.NA)
    df["rsi14"] = rsi(df["close"], 14)
    df["mom10"] = df["close"].pct_change(10)

    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    df["volatility"] = df["ret_1"].rolling(20).std()

    return df


# ===== DATA FETCH =====
def fetch_data(retries: int = 4, min_bars: int = MIN_BARS) -> Optional[Dict[str, Any]]:
    now = int(time.time())
    for i in range(retries):
        try:
            params = {
                "symbol": SYMBOL,
                "resolution": RESOLUTION,
                "from": now - 3600 * 12,
                "to": now,
                "countback": COUNTBACK,
            }
            res = http.get(URL, params=params, timeout=8)
            res.raise_for_status()
            data = res.json()

            required_keys = {"t", "o", "h", "l", "c", "v"}
            if not required_keys.issubset(data):
                raise ValueError("Response missing OHLCV keys")

            n = len(data["c"])
            if n < min_bars:
                raise ValueError(f"Not enough candles ({n}/{min_bars})")

            return data
        except Exception as exc:
            wait = 1.5**i
            print(f"[fetch retry {i + 1}/{retries}] {exc}; wait={wait:.1f}s")
            time.sleep(wait)

    return None


# ===== FORECAST ENGINE =====
def compute_signal(data: Dict[str, List[float]]) -> Dict[str, Any]:
    df = build_features(data)
    last = df.iloc[-1]

    if len(df) < 50:
        return {
            "signal": "WAIT",
            "price": float(last["close"]),
            "confidence": 0.0,
            "score": 0.0,
            "reason": f"Warming up: need >=50 candles, got {len(df)}",
            "risk": None,
        }

    cols = ["ma20", "ma50", "zscore20", "rsi14", "mom10", "atr14", "volatility"]
    if last[cols].isna().any():
        return {
            "signal": "WAIT",
            "price": float(last["close"]),
            "confidence": 0.0,
            "score": 0.0,
            "reason": "Indicators not ready",
            "risk": None,
        }

    trend_score = 1 if last["close"] > last["ma20"] else -1
    structure_score = 1 if last["ma20"] > last["ma50"] else -1
    momentum_score = 1 if last["mom10"] > 0 else -1

    if last["rsi14"] < 30:
        rsi_score = 1
    elif last["rsi14"] > 70:
        rsi_score = -1
    else:
        rsi_score = 0

    z_score = 1 if last["zscore20"] < -1 else (-1 if last["zscore20"] > 1 else 0)

    raw_score = (
        0.35 * trend_score
        + 0.25 * structure_score
        + 0.2 * momentum_score
        + 0.1 * rsi_score
        + 0.1 * z_score
    )

    confidence = min(1.0, abs(raw_score))

    if last["volatility"] > 0.02:
        signal = "HOLD"
        reason = "Volatility too high, avoid new position"
    elif abs(raw_score) < 0.25:
        signal = "HOLD"
        reason = "Conflicting signals"
    elif raw_score > 0:
        signal = "BUY"
        reason = "Trend and momentum are supportive"
    else:
        signal = "SELL"
        reason = "Trend and momentum are weakening"

    atr = float(last["atr14"])
    price = float(last["close"])

    return {
        "signal": signal,
        "price": price,
        "confidence": round(confidence, 4),
        "score": round(float(raw_score), 4),
        "reason": reason,
        "risk": {
            "stop_loss": round(price - 1.5 * atr, 2) if signal == "BUY" else round(price + 1.5 * atr, 2),
            "take_profit": round(price + 2.5 * atr, 2) if signal == "BUY" else round(price - 2.5 * atr, 2),
            "atr": round(atr, 4),
            "volatility": round(float(last["volatility"]), 6),
        },
    }


def recommend_position_size(price: float, atr: float, account_balance: float) -> Dict[str, Any]:
    """
    Position sizing for VN30F1M-style futures:
    - risk per contract ~= stop distance(points) * contract multiplier
    - margin per contract ~= price * contract multiplier * margin rate
    """
    stop_points = max(atr * 1.5, 1e-6)
    risk_budget = max(account_balance * MAX_RISK_PCT, 0.0)
    risk_per_contract = stop_points * CONTRACT_MULTIPLIER
    margin_per_contract = price * CONTRACT_MULTIPLIER * MARGIN_RATE

    by_risk = int(risk_budget // risk_per_contract) if risk_per_contract > 0 else 0
    by_margin = int(account_balance // margin_per_contract) if margin_per_contract > 0 else 0
    contracts = max(min(by_risk, by_margin), 0)

    return {
        "contracts": contracts,
        "risk_budget": round(risk_budget, 2),
        "risk_per_contract": round(risk_per_contract, 2),
        "margin_per_contract": round(margin_per_contract, 2),
        "max_by_risk": by_risk,
        "max_by_margin": by_margin,
    }


def futures_pnl(side: str, entry: float, exit_price: float, contracts: int = 1) -> Dict[str, Any]:
    points = (exit_price - entry) if side.upper() == "LONG" else (entry - exit_price)
    gross_pnl = points * CONTRACT_MULTIPLIER * max(contracts, 0)
    roi_on_margin = None
    est_margin = entry * CONTRACT_MULTIPLIER * MARGIN_RATE * max(contracts, 0)
    if est_margin > 0:
        roi_on_margin = round((gross_pnl / est_margin) * 100, 2)

    return {
        "side": side.upper(),
        "entry": entry,
        "exit": exit_price,
        "points": round(points, 2),
        "contracts": contracts,
        "gross_pnl_vnd": round(gross_pnl, 2),
        "estimated_margin_vnd": round(est_margin, 2),
        "roi_on_margin_pct": roi_on_margin,
    }


def build_market_snapshot(data: Dict[str, List[float]]) -> Dict[str, Any]:
    df = build_features(data)
    last = df.iloc[-1]
    recent = df.tail(30)

    return {
        "price": round(float(last["close"]), 2),
        "ma20": round(float(last["ma20"]), 2) if pd.notna(last["ma20"]) else None,
        "ma50": round(float(last["ma50"]), 2) if pd.notna(last["ma50"]) else None,
        "rsi14": round(float(last["rsi14"]), 2) if pd.notna(last["rsi14"]) else None,
        "mom10": round(float(last["mom10"]), 6) if pd.notna(last["mom10"]) else None,
        "volatility20": round(float(last["volatility"]), 6) if pd.notna(last["volatility"]) else None,
        "return_30bars_pct": round(float((recent["close"].iloc[-1] / recent["close"].iloc[0] - 1) * 100), 3)
        if len(recent) >= 2
        else None,
        "high_30": round(float(recent["high"].max()), 2),
        "low_30": round(float(recent["low"].min()), 2),
    }


def _parse_hf_text(body: Any) -> str:
    if isinstance(body, list) and body and "generated_text" in body[0]:
        return str(body[0]["generated_text"]).strip()
    if isinstance(body, list) and body and "summary_text" in body[0]:
        return str(body[0]["summary_text"]).strip()
    if isinstance(body, dict) and "generated_text" in body:
        return str(body["generated_text"]).strip()
    return str(body)


def _ensure_local_hf_model() -> Dict[str, Any]:
    global local_tokenizer, local_model

    if importlib.util.find_spec("transformers") is None:
        return {
            "ok": False,
            "reason": "transformers chưa được cài trong môi trường hiện tại.",
        }

    with local_model_lock:
        if local_tokenizer is not None and local_model is not None:
            return {"ok": True, "reason": "cached"}

        transformers = importlib.import_module("transformers")
        AutoTokenizer = transformers.AutoTokenizer
        AutoModelForCausalLM = transformers.AutoModelForCausalLM

        try:
            local_tokenizer = AutoTokenizer.from_pretrained(HF_LOCAL_MODEL_ID)
            local_model = AutoModelForCausalLM.from_pretrained(HF_LOCAL_MODEL_ID)
            return {"ok": True, "reason": "loaded"}
        except Exception as exc:
            local_tokenizer = None
            local_model = None
            return {"ok": False, "reason": f"Không load được local model: {exc}"}


def query_local_hf_recommendation(snapshot: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    status = _ensure_local_hf_model()
    if not status["ok"]:
        return {"source": "local-fallback", "model": HF_LOCAL_MODEL_ID, "text": status["reason"]}

    messages = [
        {
            "role": "user",
            "content": (
                "Bạn là trợ lý giao dịch phái sinh VN30F1M. "
                "Hãy trả JSON ngắn gồm recommendation (BUY/SELL/HOLD), confidence (0-1), rationale, risk_note.\n"
                f"snapshot={snapshot}\n"
                f"baseline={baseline}\n"
            ),
        }
    ]

    with local_model_lock:
        inputs = local_tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(local_model.device)
        outputs = local_model.generate(**inputs, max_new_tokens=120)
        text = local_tokenizer.decode(outputs[0][inputs["input_ids"].shape[-1] :], skip_special_tokens=True)

    return {"source": "huggingface-local", "model": HF_LOCAL_MODEL_ID, "text": text.strip()}


def query_huggingface_recommendation(snapshot: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    if USE_LOCAL_HF_MODEL:
        local_result = query_local_hf_recommendation(snapshot=snapshot, baseline=baseline)
        if local_result["source"] != "local-fallback":
            return local_result

    headers = {"Authorization": f"Bearer {HF_API_TOKEN}"} if HF_API_TOKEN else {}
    prompt = (
        "Bạn là trợ lý giao dịch phái sinh VN30F1M. "
        "Dựa trên snapshot thị trường và baseline signal, hãy trả về JSON ngắn gồm: "
        "recommendation (BUY/SELL/HOLD), confidence (0-1), rationale (<=30 từ), risk_note.\n"
        f"snapshot={snapshot}\n"
        f"baseline={baseline}\n"
    )
    payload = {
        "inputs": prompt,
        "parameters": {"max_new_tokens": 180, "return_full_text": False, "temperature": 0.2},
    }

    errors: List[str] = []
    for model_id in [HF_MODEL_ID] + [m for m in HF_MODEL_CANDIDATES if m != HF_MODEL_ID]:
        for base in HF_INFERENCE_BASES:
            endpoint = f"{base}/{model_id}"
            try:
                res = http.post(endpoint, headers=headers, json=payload, timeout=25)
                if res.status_code in {404, 410, 503}:
                    errors.append(f"{model_id}@{base}: HTTP {res.status_code}")
                    continue
                res.raise_for_status()
                body = res.json()
                text = _parse_hf_text(body)
                return {"source": "huggingface", "model": model_id, "endpoint": base, "text": text}
            except Exception as exc:
                errors.append(f"{model_id}@{base}: {exc}")
                continue

    fallback_signal = baseline.get("signal", "HOLD")
    fallback_conf = float(baseline.get("confidence", 0.0))
    risk = baseline.get("risk") or {}
    atr = risk.get("atr")
    volatility = risk.get("volatility", snapshot.get("volatility20"))
    trend = "uptrend" if (snapshot.get("ma20") or 0) >= (snapshot.get("ma50") or 0) else "downtrend"
    risk_note = (
        f"ATR={atr}, volatility={volatility}. Ưu tiên 1/2 khối lượng chuẩn, luôn đặt SL/TP."
        if atr is not None
        else "Giảm khối lượng và luôn đặt stop-loss."
    )
    result: Dict[str, Any] = {
        "source": "rule-based-ai",
        "model": "internal-fallback",
        "recommendation": fallback_signal,
        "confidence": round(max(0.35, fallback_conf), 2),
        "rationale": f"Rule-AI fallback: {trend}, baseline={fallback_signal}.",
        "risk_note": risk_note,
        "text": "rule-based fallback",
    }
    if AI_DEBUG_ERRORS:
        result["error_details"] = errors
    return result


def normalize_ai_payload(ai_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure AI output is consistently structured (object fields), not only raw text.
    """
    if {"recommendation", "confidence", "rationale", "risk_note"}.issubset(ai_result.keys()):
        return ai_result

    text = ai_result.get("text")
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return {**ai_result, **parsed}
        except Exception:
            pass
    return ai_result


# ===== HEARTBEAT =====
def update_loop() -> None:
    while True:
        try:
            data = fetch_data(min_bars=10)
            if data is None:
                raise RuntimeError("Cannot fetch data from feed")

            fc = compute_signal(data)
            with state_lock:
                state.signal = fc["signal"]
                state.price = fc["price"]
                state.confidence = fc["confidence"]
                state.score = fc["score"]
                state.reason = fc["reason"]
                state.updated_at = int(time.time())

            print(f"[UPDATE] {state.signal} @ {state.price} (conf={state.confidence})")
        except Exception as exc:
            print(f"[LOOP ERROR] {exc}")
        time.sleep(UPDATE_SECONDS)


def start_update_thread() -> None:
    global update_thread
    if update_thread is None or not update_thread.is_alive():
        update_thread = threading.Thread(target=update_loop, daemon=True, name="forecast-updater")
        update_thread.start()
        print("[SYSTEM] update thread started")


def keepalive_loop() -> None:
    """
    Keep the app active on platforms like Hugging Face Spaces and auto-heal
    the forecast worker if it dies unexpectedly.
    """

    while True:
        try:
            start_update_thread()
            # Self-ping helps keep event loop warm on environments with idling behavior.
            with suppress(Exception):
                http.get("http://127.0.0.1:7860/health", timeout=3)
            with suppress(Exception):
                http.get("http://127.0.0.1:8000/health", timeout=3)
            if KEEPALIVE_EXTERNAL_URL:
                with suppress(Exception):
                    http.get(KEEPALIVE_EXTERNAL_URL, timeout=8)
        except Exception as exc:
            print(f"[KEEPALIVE ERROR] {exc}")

        time.sleep(45)


def start_background_services() -> None:
    global keepalive_thread
    start_update_thread()
    if keepalive_thread is None or not keepalive_thread.is_alive():
        keepalive_thread = threading.Thread(target=keepalive_loop, daemon=True, name="system-keepalive")
        keepalive_thread.start()


@app.on_event("startup")
def on_startup() -> None:
    start_background_services()


# ===== API =====
@app.get("/")
def root() -> Dict[str, Any]:
    with state_lock:
        return {
            "status": "running",
            "symbol": SYMBOL,
            **asdict(state),
        }


@app.get("/signal")
def signal() -> Dict[str, Any]:
    with state_lock:
        return {
            "symbol": SYMBOL,
            **asdict(state),
        }


@app.get("/forecast")
def forecast() -> Dict[str, Any]:
    data = fetch_data(min_bars=10)
    if data is None:
        raise HTTPException(status_code=503, detail="Data feed unavailable")
    return compute_signal(data)


@app.get("/position-size")
def position_size(account_balance: float) -> Dict[str, Any]:
    if account_balance <= 0:
        raise HTTPException(status_code=400, detail="account_balance must be > 0")

    data = fetch_data(min_bars=10)
    if data is None:
        raise HTTPException(status_code=503, detail="Data feed unavailable")

    fc = compute_signal(data)
    if not fc.get("risk"):
        return {
            "signal": fc["signal"],
            "reason": fc["reason"],
            "recommendation": None,
        }

    rec = recommend_position_size(
        price=float(fc["price"]),
        atr=float(fc["risk"]["atr"]),
        account_balance=account_balance,
    )
    return {
        "signal": fc["signal"],
        "reason": fc["reason"],
        "price": fc["price"],
        "risk": fc["risk"],
        "recommendation": rec,
    }


@app.get("/pnl")
def pnl(side: str, entry: float, exit_price: float, contracts: int = 1) -> Dict[str, Any]:
    side_up = side.upper()
    if side_up not in {"LONG", "SHORT"}:
        raise HTTPException(status_code=400, detail="side must be LONG or SHORT")
    if contracts <= 0:
        raise HTTPException(status_code=400, detail="contracts must be > 0")
    return futures_pnl(side=side_up, entry=entry, exit_price=exit_price, contracts=contracts)


@app.get("/ai-recommendation")
def ai_recommendation() -> Dict[str, Any]:
    data = fetch_data(min_bars=30)
    if data is None:
        raise HTTPException(status_code=503, detail="Data feed unavailable")

    baseline = compute_signal(data)
    snapshot = build_market_snapshot(data)
    ai_result = normalize_ai_payload(query_huggingface_recommendation(snapshot=snapshot, baseline=baseline))

    return {
        "symbol": SYMBOL,
        "snapshot": snapshot,
        "baseline_signal": baseline,
        "ai": ai_result,
        "note": "AI output chỉ để tham khảo, không phải khuyến nghị đầu tư chắc chắn.",
    }


@app.get("/hf-status")
def hf_status() -> Dict[str, Any]:
    return {
        "model_id": HF_MODEL_ID,
        "local_model_id": HF_LOCAL_MODEL_ID,
        "use_local_hf_model": USE_LOCAL_HF_MODEL,
        "token_configured": bool(HF_API_TOKEN),
        "inference_bases": HF_INFERENCE_BASES,
        "keepalive_external_url": KEEPALIVE_EXTERNAL_URL,
        "how_to_set_token": "Hugging Face Spaces -> Settings -> Variables and secrets -> Secrets -> HF_API_TOKEN",
    }


@app.get("/ohlc")
def ohlc(limit: int = 200) -> List[Dict[str, Any]]:
    data = fetch_data(min_bars=1)
    if data is None:
        raise HTTPException(status_code=503, detail="Data feed unavailable")

    n = len(data["t"])
    start = max(0, n - min(max(limit, 1), n))

    return [
        {
            "time": data["t"][i],
            "open": data["o"][i],
            "high": data["h"][i],
            "low": data["l"][i],
            "close": data["c"][i],
            "volume": data["v"][i],
        }
        for i in range(start, n)
    ]


@app.get("/health")
def health() -> Dict[str, Any]:
    with state_lock:
        age = int(time.time()) - state.updated_at if state.updated_at else None
    return {
        "ok": age is not None and age <= UPDATE_SECONDS * 4,
        "last_update_age_sec": age,
        "update_interval_sec": UPDATE_SECONDS,
        "worker_alive": update_thread.is_alive() if update_thread else False,
        "keepalive_alive": keepalive_thread.is_alive() if keepalive_thread else False,
    }


@app.get("/ping")
def ping() -> Dict[str, str]:
    return {"pong": "ok"}
