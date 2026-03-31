"""Vercel Serverless Function — SPY 실시간 데이터 + 기술적 지표"""
from http.server import BaseHTTPRequestHandler
import json
import traceback

def fetch_spy():
    import yfinance as yf
    import pandas as pd
    import numpy as np
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
    now = datetime.now(ET)

    spy = yf.Ticker("SPY")
    hist = spy.history(period="1y", interval="1d")
    if hist.empty:
        raise RuntimeError("SPY data unavailable")

    c = hist["Close"]; h = hist["High"]; lo = hist["Low"]; v = hist["Volume"]

    last = round(c.iloc[-1], 2)
    prev = round(c.iloc[-2], 2)
    chg = round(last - prev, 2)
    chg_pct = round((chg / prev) * 100, 2)
    d_high = round(h.iloc[-1], 2)
    d_low = round(lo.iloc[-1], 2)
    d_vol = int(v.iloc[-1])
    avg_vol = int(v.tail(20).mean())
    vol_ratio = round(d_vol / avg_vol, 2) if avg_vol else 1.0

    ath = round(c.max(), 2)
    ath_date = c.idxmax().strftime("%m/%d")
    ath_pct = round(((last - ath) / ath) * 100, 2)

    # After-hours
    ah_price = 0; ah_chg = 0; ah_chg_pct = 0
    pm_price = 0; pm_chg = 0; pm_chg_pct = 0
    try:
        info = spy.info
        if info.get("postMarketPrice"):
            ah_price = round(info["postMarketPrice"], 2)
            ah_chg = round(ah_price - last, 2)
            ah_chg_pct = round((ah_chg / last) * 100, 2)
        if info.get("preMarketPrice"):
            pm_price = round(info["preMarketPrice"], 2)
            pm_chg = round(pm_price - last, 2)
            pm_chg_pct = round((pm_chg / last) * 100, 2)
    except: pass

    # MAs
    def ema(s, n): return round(s.ewm(span=n, adjust=False).mean().iloc[-1], 2)
    def sma(s, n): return round(s.tail(n).mean(), 2)
    mas = {"EMA 5":ema(c,5),"T-Line (EMA 8)":ema(c,8),"EMA 9":ema(c,9),
           "EMA 21":ema(c,21),"SMA 50":sma(c,50),"SMA 100":sma(c,100),"SMA 200":sma(c,200)}
    ma_sell = sum(1 for x in mas.values() if last < x)
    ma_buy = len(mas) - ma_sell
    cross = "Death Cross" if sma(c,50) < sma(c,200) else "Golden Cross"

    # RSI
    delta = c.diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss
    rsi = round((100 - (100 / (1 + rs))).iloc[-1], 1)

    # MACD
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    macd_line = e12 - e26
    sig_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_val = round(macd_line.iloc[-1], 3)
    macd_sig = round(sig_line.iloc[-1], 3)
    macd_hist = round((macd_line - sig_line).iloc[-1], 3)

    # Stochastic
    low14 = lo.tail(14).min(); high14 = h.tail(14).max()
    stoch_k = round(((c.iloc[-1] - low14) / (high14 - low14)) * 100, 1) if high14 != low14 else 50

    # ATR
    tr = pd.concat([h-lo, (h-c.shift()).abs(), (lo-c.shift()).abs()], axis=1).max(axis=1)
    atr = round(tr.tail(14).mean(), 2)

    # VWAP (intraday)
    vwap = 0
    try:
        intra = spy.history(period="1d", interval="5m")
        if not intra.empty and intra["Volume"].sum() > 0:
            cv = intra["Volume"].cumsum()
            cvp = (intra["Close"] * intra["Volume"]).cumsum()
            vwap = round(cvp.iloc[-1] / cv.iloc[-1], 2)
    except: pass

    # Bollinger
    sma20 = c.tail(20).mean(); std20 = c.tail(20).std()
    bb_upper = round(sma20 + 2*std20, 2)
    bb_lower = round(sma20 - 2*std20, 2)
    bb_mid = round(sma20, 2)

    # Pivot S/R
    pp = round((d_high+d_low+last)/3, 2)
    r1 = round(2*pp-d_low, 2); r2 = round(pp+(d_high-d_low), 2)
    s1 = round(2*pp-d_high, 2); s2 = round(pp-(d_high-d_low), 2)

    levels = {
        "resistance": [{"label":"SMA 200","price":mas["SMA 200"]},{"label":"SMA 100","price":mas["SMA 100"]},
                       {"label":"SMA 50","price":mas["SMA 50"]},{"label":"Pivot R1","price":r1}],
        "support": [{"label":"Pivot","price":pp},{"label":"Pivot S1","price":s1},
                    {"label":"Pivot S2","price":s2},{"label":"BB Lower","price":bb_lower}],
    }

    # Signal
    def rsi_signal(r):
        if r < 30: return "과매도"
        if r > 70: return "과매수"
        return "중립"

    score = -ma_sell*2 + ma_buy*2
    if rsi < 30: score += 1
    if rsi > 70: score -= 1
    if macd_val < macd_sig: score -= 1
    else: score += 1
    if score <= -8: signal = "Strong Sell"
    elif score <= -3: signal = "Sell"
    elif score >= 8: signal = "Strong Buy"
    elif score >= 3: signal = "Buy"
    else: signal = "Neutral"

    # Extras
    extras = {}
    for sym, key in [("^VIX","vix"),("CL=F","oil"),("ES=F","es_futures"),("GC=F","gold"),("^TNX","us10y")]:
        try:
            eh = yf.Ticker(sym).history(period="5d")
            if not eh.empty:
                extras[key] = round(eh["Close"].iloc[-1], 2)
                if len(eh) >= 2:
                    extras[f"{key}_chg"] = round(((eh["Close"].iloc[-1] - eh["Close"].iloc[-2]) / eh["Close"].iloc[-2]) * 100, 2)
                else: extras[f"{key}_chg"] = 0
            else: extras[key] = 0; extras[f"{key}_chg"] = 0
        except: extras[key] = 0; extras[f"{key}_chg"] = 0

    # Recent 5 days
    recent = []
    for i in range(-5, 0):
        if abs(i) <= len(hist):
            idx = hist.index[i]
            recent.append({"date":idx.strftime("%m/%d"),"close":round(c.iloc[i],2),
                "chg_pct":round(((c.iloc[i]-c.iloc[i-1])/c.iloc[i-1])*100,2),"vol":int(v.iloc[i])})

    # Composite
    st_score = 0
    for nm in ["EMA 5","T-Line (EMA 8)","EMA 9","EMA 21"]:
        st_score += 1 if last > mas[nm] else -1
    if rsi < 20: st_score += 0.5
    elif rsi < 30: st_score += 0
    elif rsi < 45: st_score -= 1
    elif rsi > 70: st_score -= 0.5
    elif rsi > 55: st_score += 1
    st_score += 1 if macd_val > macd_sig else -1
    st_score += 1 if vwap and last > vwap else (-1 if vwap else 0)
    if chg < 0 and d_vol > avg_vol: st_score -= 1
    elif chg > 0 and d_vol > avg_vol: st_score += 1

    if st_score <= -4: st_sig = "Strong Sell"
    elif st_score <= -1.5: st_sig = "Sell"
    elif st_score < 1.5: st_sig = "Neutral"
    elif st_score < 4: st_sig = "Buy"
    else: st_sig = "Strong Buy"

    mt_score = (1.5 if last > mas["SMA 50"] else -1.5) + (2 if last > mas["SMA 200"] else -2)
    mt_score += 1.5 if mas["SMA 50"] > mas["SMA 200"] else -1.5
    mt_score += 1 if rsi > 50 else -1
    if mt_score <= -4: mt_sig = "Strong Sell"
    elif mt_score <= -1: mt_sig = "Sell"
    elif mt_score < 1: mt_sig = "Neutral"
    elif mt_score < 4: mt_sig = "Buy"
    else: mt_sig = "Strong Buy"

    both_bear = "Sell" in st_sig and "Sell" in mt_sig
    both_bull = "Buy" in st_sig and "Buy" in mt_sig
    nr = sorted([l["price"] for l in levels["resistance"]])[0] if levels["resistance"] else last+atr
    ns = sorted([l["price"] for l in levels["support"]], reverse=True)[0] if levels["support"] else last-atr

    if both_bear:
        action = {"direction":"PUT","description":"단기+중기 약세 → 하방","entry":f"${nr} rejection","target":f"${ns}","stop":f"${round(nr+atr*0.3,2)}","sizing":"1/2 Kelly","confidence":"높음"}
    elif both_bull:
        action = {"direction":"CALL","description":"단기+중기 강세 → 상방","entry":f"${ns} 지지확인","target":f"${nr}","stop":f"${round(ns-atr*0.3,2)}","sizing":"1/2 Kelly","confidence":"높음"}
    elif "Sell" in st_sig:
        action = {"direction":"PUT (light)","description":"단기 약세, 중기 미확정","entry":f"${nr} rejection","target":f"${ns}","stop":f"${round(nr+atr*0.3,2)}","sizing":"1/4 Kelly","confidence":"보통"}
    elif "Buy" in st_sig:
        action = {"direction":"CALL (light)","description":"단기 강세, 중기 미확정","entry":f"${ns} 지지확인","target":f"${nr}","stop":f"${round(ns-atr*0.3,2)}","sizing":"1/4 Kelly","confidence":"보통"}
    else:
        action = {"direction":"WAIT","description":"시그널 불명확","entry":"-","target":"-","stop":"-","sizing":"-","confidence":"-"}

    warnings = []
    if rsi < 30: warnings.append(f"RSI {rsi} 과매도 — 반등 가능")
    if d_vol > avg_vol*1.3: warnings.append(f"거래량 {vol_ratio}x — 변동성 확대")
    action["warnings"] = warnings

    return {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S ET"),
        "price": {"last":last,"prev":prev,"chg":chg,"chg_pct":chg_pct,
                  "high":d_high,"low":d_low,"range":round(d_high-d_low,2),
                  "range_pct":round(((d_high-d_low)/d_low)*100,2) if d_low else 0,
                  "after_hours":ah_price,"ah_chg":ah_chg,"ah_chg_pct":ah_chg_pct,
                  "pre_market":pm_price,"pm_chg":pm_chg,"pm_chg_pct":pm_chg_pct},
        "ath":{"price":ath,"date":ath_date,"pct":ath_pct},
        "volume":{"current":d_vol,"avg20":avg_vol,"ratio":vol_ratio},
        "ma":mas,"ma_summary":{"sell":ma_sell,"buy":ma_buy,"cross":cross},
        "indicators":{"RSI":rsi,"MACD":macd_val,"MACD_sig":macd_sig,"MACD_hist":macd_hist,
                      "Stoch_K":stoch_k,"ATR":atr,"RSI_signal":rsi_signal(rsi),
                      "MACD_signal":"매도" if macd_val<macd_sig else "매수",
                      "Stoch_signal":"과매도" if stoch_k<20 else ("과매수" if stoch_k>80 else "중립")},
        "vwap":vwap,"bollinger":{"upper":bb_upper,"mid":bb_mid,"lower":bb_lower},
        "levels":levels,"signal":signal,
        "composite":{"short_term":{"signal":st_sig,"score":round(st_score,1)},
                     "mid_term":{"signal":mt_sig,"score":round(mt_score,1)},
                     "action":action},
        "extras":extras,"recent":recent,
    }

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            data = fetch_spy()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e), "trace": traceback.format_exc()}).encode())
