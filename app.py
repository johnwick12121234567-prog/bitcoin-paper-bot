import os, json, time, threading, secrets
from datetime import datetime, timezone
from functools import wraps

import requests
from flask import Flask, jsonify, request, Response, render_template_string

APP = Flask(__name__)
DATA_FILE = os.getenv("DATA_FILE", "bot_state.json")
STARTING_CAD = 20.00
MAX_DRAWDOWN_CAD = 2.00
MAX_POSITION_CAD = 10.00
# Planning estimate only; replace with your actual fee tier before evaluating results.
ESTIMATED_FEE_RATE = float(os.getenv("ESTIMATED_FEE_RATE", "0.006"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
PRODUCT = "BTC-CAD"
API_ROOT = "https://api.exchange.coinbase.com"

lock = threading.RLock()
state = {
    "cash": STARTING_CAD, "btc": 0.0, "entry_price": None,
    "starting_equity": STARTING_CAD, "last_price": None,
    "last_update": None, "status": "RUNNING", "reason": "Paper trading only",
    "trades": [], "prices": [], "last_signal": "WAIT",
    "last_candle_time": None, "last_error": None
}

def load_state():
    global state
    try:
        with open(DATA_FILE, "r") as f:
            saved = json.load(f)
        if saved.get("starting_equity") == STARTING_CAD:
            state.update(saved)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

def save_state():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, DATA_FILE)

def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = os.getenv("DASHBOARD_PASSWORD", "")
        if not expected:
            return "Server setup incomplete: set DASHBOARD_PASSWORD.", 503
        auth = request.authorization
        if not auth or auth.username != "owner" or not secrets.compare_digest(auth.password or "", expected):
            return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Bitcoin Paper Bot"'})
        return fn(*args, **kwargs)
    return wrapper

def get_price():
    r = requests.get(f"{API_ROOT}/products/{PRODUCT}/ticker", timeout=12,
                     headers={"User-Agent": "BTC-CAD-Paper-Trading-Demo/1.0"})
    r.raise_for_status()
    return float(r.json()["price"])

def get_hourly_closes():
    # Coinbase Exchange candles: [time, low, high, open, close, volume]
    end = int(time.time())
    start = end - (30 * 3600)
    r = requests.get(
        f"{API_ROOT}/products/{PRODUCT}/candles",
        params={"granularity": 3600, "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(end, timezone.utc).isoformat()},
        timeout=12, headers={"User-Agent": "BTC-CAD-Paper-Trading-Demo/1.0"}
    )
    r.raise_for_status()
    rows = sorted(r.json(), key=lambda row: row[0])
    return [(int(row[0]), float(row[4])) for row in rows]

def equity(price):
    return state["cash"] + state["btc"] * price

def add_trade(side, price, cad_value, fee, note):
    state["trades"].insert(0, {
        "time": datetime.now(timezone.utc).isoformat(),
        "side": side, "price": price, "cad": cad_value,
        "fee": fee, "note": note
    })
    state["trades"] = state["trades"][:100]

def evaluate():
    price = get_price()
    candles = get_hourly_closes()
    with lock:
        state["last_price"] = price
        state["last_update"] = datetime.now(timezone.utc).isoformat()
        state["last_error"] = None
        state["prices"].append({"time": state["last_update"], "price": price})
        state["prices"] = state["prices"][-240:]

        current_equity = equity(price)
        if current_equity <= state["starting_equity"] - MAX_DRAWDOWN_CAD:
            state["status"] = "STOPPED"
            state["reason"] = "Maximum $2 CAD drawdown reached. Manual reset required."
            state["last_signal"] = "RISK STOP"
            save_state()
            return

        # Only act once per newly closed hourly candle. Exclude the current, unfinished candle.
        if len(candles) < 22:
            state["reason"] = "Collecting enough hourly price history (need 22 candles)."
            save_state()
            return

        closed = candles[:-1]
        candle_time = closed[-1][0]
        if state.get("last_candle_time") == candle_time:
            save_state()
            return

        closes = [c[1] for c in closed]
        fast = sum(closes[-5:]) / 5
        slow = sum(closes[-20:]) / 20
        previous_fast = sum(closes[-6:-1]) / 5
        previous_slow = sum(closes[-21:-1]) / 20
        buy_cross = previous_fast <= previous_slow and fast > slow
        sell_cross = previous_fast >= previous_slow and fast < slow
        state["last_candle_time"] = candle_time

        if state["status"] != "RUNNING":
            save_state()
            return

        if buy_cross and state["btc"] <= 0:
            budget = min(MAX_POSITION_CAD, state["cash"])
            fee = budget * ESTIMATED_FEE_RATE
            spend = budget - fee
            if spend >= 1:
                btc_bought = spend / price
                state["cash"] -= budget
                state["btc"] += btc_bought
                state["entry_price"] = price
                add_trade("BUY", price, budget, fee, "5-hour SMA crossed above 20-hour SMA")
                state["last_signal"] = "BUY"
                state["reason"] = "Simulated buy; no real funds used."
        elif sell_cross and state["btc"] > 0:
            gross = state["btc"] * price
            fee = gross * ESTIMATED_FEE_RATE
            net = gross - fee
            state["cash"] += net
            add_trade("SELL", price, gross, fee, "5-hour SMA crossed below 20-hour SMA")
            state["btc"] = 0.0
            state["entry_price"] = None
            state["last_signal"] = "SELL"
            state["reason"] = "Simulated sell; no real funds used."
        else:
            state["last_signal"] = "HOLD"
            state["reason"] = "No new moving-average crossover. Holding paper position."
        save_state()

def worker():
    while True:
        try:
            evaluate()
        except Exception as e:
            with lock:
                state["last_error"] = str(e)[:240]
                state["last_update"] = datetime.now(timezone.utc).isoformat()
                save_state()
        time.sleep(max(30, POLL_SECONDS))

HTML = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#101827"><title>BTC-CAD Paper Bot</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#edf2f7;font:16px system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:760px;margin:auto;padding:20px}.top{display:flex;justify-content:space-between;align-items:center;gap:12px}
h1{font-size:22px;margin:6px 0}small,.muted{color:#9aa9bd}.tag{border:1px solid #42634e;color:#a7e2b8;padding:6px 10px;border-radius:20px;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin:18px 0}.card{background:#151f30;border:1px solid #25344a;border-radius:14px;padding:16px}
.label{color:#9aa9bd;font-size:13px}.value{font-size:25px;font-weight:700;margin-top:7px;overflow-wrap:anywhere}
section{margin-top:14px}.actions{display:flex;gap:10px;flex-wrap:wrap}button{background:#253b59;color:white;border:0;border-radius:10px;padding:12px 16px;font-weight:650;font-size:14px}
button.danger{background:#6e3038}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:10px 5px;border-bottom:1px solid #2b394d}th{color:#9aa9bd}
.notice{background:#30291b;border:1px solid #65522b;padding:13px;border-radius:12px;line-height:1.45}
.status{font-weight:700}.good{color:#91e2a8}.bad{color:#ff9b9b}
footer{color:#8292a8;font-size:12px;line-height:1.5;margin:24px 0}
@media(max-width:420px){main{padding:14px}.value{font-size:21px}.grid{gap:8px}.card{padding:13px}}
</style></head><body><main>
<div class="top"><div><small>COINBASE MARKET • BTC-CAD</small><h1>Bitcoin Paper-Trading Bot</h1></div><span class="tag">SIMULATION ONLY</span></div>
<div class="notice"><b>Paper trading only.</b> This dashboard does not connect to your Coinbase account, place real orders, or move money.</div>
<div class="grid">
<div class="card"><div class="label">Portfolio value</div><div class="value" id="equity">$20.00</div></div>
<div class="card"><div class="label">Bitcoin price</div><div class="value" id="price">Loading…</div></div>
<div class="card"><div class="label">Available cash</div><div class="value" id="cash">$20.00</div></div>
<div class="card"><div class="label">BTC held</div><div class="value" id="btc">0.00000000</div></div>
<div class="card"><div class="label">Profit / loss</div><div class="value" id="pnl">$0.00</div></div>
<div class="card"><div class="label">Bot status</div><div class="value status" id="status">Loading…</div></div>
</div>
<section class="card"><div class="label">Strategy signal</div><div class="value" id="signal">—</div><p class="muted" id="reason">Loading bot state…</p><p class="muted" id="updated"></p></section>
<section class="card"><b>Risk controls</b><p class="muted">Starting balance: $20 CAD · Maximum drawdown: $2 CAD · Maximum position: $10 CAD · Leverage: none</p>
<div class="actions"><button onclick="action('pause')">Pause bot</button><button onclick="action('resume')">Resume bot</button><button class="danger" onclick="action('reset')">Reset simulation</button></div>
<p class="muted" id="actionMsg"></p></section>
<section class="card"><b>Paper trades</b><div style="overflow-x:auto"><table><thead><tr><th>Side / time</th><th>BTC price</th><th>CAD value</th><th>Est. fee</th></tr></thead><tbody id="trades"><tr><td colspan="4">No trades yet</td></tr></tbody></table></div></section>
<footer>Prices are retrieved from Coinbase Exchange public market data. The strategy uses a 5-hour / 20-hour simple moving-average crossover and only evaluates newly closed hourly candles. Estimated fees are illustrative, not your actual Coinbase fee tier. Paper results do not predict live results. Keep this page private and use a strong dashboard password.</footer>
</main>
<script>
const money=n=>new Intl.NumberFormat('en-CA',{style:'currency',currency:'CAD'}).format(n||0);
async function refresh(){try{let r=await fetch('/api/status');if(!r.ok)throw Error('Login/session error');let s=await r.json();
document.getElementById('equity').textContent=money(s.equity);
document.getElementById('price').textContent=money(s.price);
document.getElementById('cash').textContent=money(s.cash);
document.getElementById('btc').textContent=(s.btc||0).toFixed(8);
let pnl=s.equity-20, p=document.getElementById('pnl');p.textContent=(pnl>=0?'+':'')+money(pnl);p.className='value '+(pnl>=0?'good':'bad');
let st=document.getElementById('status');st.textContent=s.status;st.className='value status '+(s.status==='RUNNING'?'good':'bad');
document.getElementById('signal').textContent=s.signal;
document.getElementById('reason').textContent=s.reason;
document.getElementById('updated').textContent='Last update: '+(s.updated||'waiting for data')+(s.error?' · Error: '+s.error:'');
let rows=s.trades.map(t=>`<tr><td>${t.side}<br><span class="muted">${new Date(t.time).toLocaleString()}</span></td><td>${money(t.price)}</td><td>${money(t.cad)}</td><td>${money(t.fee)}</td></tr>`).join('');
document.getElementById('trades').innerHTML=rows||'<tr><td colspan="4">No trades yet</td></tr>';
}catch(e){document.getElementById('reason').textContent='Unable to load dashboard: '+e.message}}
async function action(a){if(a==='reset'&&!confirm('Reset paper balance to $20 and clear trade history?'))return;
let r=await fetch('/api/'+a,{method:'POST'});let j=await r.json();document.getElementById('actionMsg').textContent=j.message||j.error||'Updated';refresh()}
refresh();setInterval(refresh,15000);
</script></body></html>
"""

@APP.get("/")
@auth_required
def home():
    return render_template_string(HTML)

@APP.get("/api/status")
@auth_required
def api_status():
    with lock:
        price = state["last_price"] or 0
        eq = equity(price) if price else state["cash"] + state["btc"] * 0
        return jsonify({
            "cash": state["cash"], "btc": state["btc"], "price": price,
            "equity": eq, "status": state["status"], "reason": state["reason"],
            "signal": state["last_signal"], "updated": state["last_update"],
            "error": state["last_error"], "trades": state["trades"]
        })

@APP.post("/api/pause")
@auth_required
def pause():
    with lock:
        state["status"] = "PAUSED"; state["reason"] = "Paused manually."; save_state()
    return jsonify(message="Bot paused.")

@APP.post("/api/resume")
@auth_required
def resume():
    with lock:
        if equity(state["last_price"] or 0) <= state["starting_equity"] - MAX_DRAWDOWN_CAD:
            return jsonify(error="Loss limit reached. Reset the simulation before resuming."), 400
        state["status"] = "RUNNING"; state["reason"] = "Resumed manually."; save_state()
    return jsonify(message="Bot resumed.")

@APP.post("/api/reset")
@auth_required
def reset():
    with lock:
        state.update({
            "cash": STARTING_CAD, "btc": 0.0, "entry_price": None,
            "starting_equity": STARTING_CAD, "status": "RUNNING",
            "reason": "Simulation reset. Paper trading only.", "trades": [],
            "last_signal": "WAIT", "last_candle_time": None, "last_error": None
        })
        save_state()
    return jsonify(message="Simulation reset to $20 CAD.")

# Start the single background paper-trading worker when the app module is loaded
# (works both with `python app.py` and the Gunicorn/Procfile deployment).
os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)
load_state()
_worker_started = False
_worker_lock = threading.Lock()

def start_worker_once():
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=worker, daemon=True, name="paper-trading-worker").start()
            _worker_started = True

start_worker_once()

if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
