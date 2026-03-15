"""
╔══════════════════════════════════════════════════════════════╗
║   HIGH-FREQUENCY SCALPING BOT — AGGRESSIVE MODE             ║
║   Claude AI + CCXT + Coinbase                                ║
║                                                              ║
║   !! RISK WARNING !!                                         ║
║   ─────────────────                                          ║
║   • This bot trades FAST and OFTEN                           ║
║   • Fees can eat profits on small accounts                   ║
║   • You CAN lose your entire balance                         ║
║   • NO strategy guarantees returns — anyone who says         ║
║     otherwise is lying to you                                ║
║   • Only trade money you can afford to lose 100% of          ║
║                                                              ║
║   REALISTIC outcomes on $100:                                ║
║   Good week  → +5% to +15%  ($5–$15 profit)                 ║
║   Bad week   → -20% to -50% ($20–$50 loss)                  ║
║   Worst case → -100%        (full wipeout)                   ║
╚══════════════════════════════════════════════════════════════╝
"""

import ccxt
import anthropic
import pandas as pd
import pandas_ta as ta
import time
import os
from datetime import datetime


# ════════════════════════════════════════════════════════════════
#  CONFIG — fill in your keys
# ════════════════════════════════════════════════════════════════

COINBASE_KEY    = os.getenv("COINBASE_API_KEY",    "PASTE_YOUR_COINBASE_KEY_HERE")
COINBASE_SECRET = os.getenv("COINBASE_API_SECRET", "PASTE_YOUR_COINBASE_SECRET_HERE")
CLAUDE_KEY      = os.getenv("ANTHROPIC_API_KEY",   "PASTE_YOUR_CLAUDE_KEY_HERE")

# ── AGGRESSIVE SCALPING SETTINGS ────────────────────────────────

TRADING_PAIR         = "BTC/USDC"
TIMEFRAME            = "1m"       # 1-minute candles (was 15m)
CHECK_EVERY_SECONDS  = 90         # Check every 90 seconds (was 15 min)
TRADE_SIZE_PCT       = 0.80       # Use 80% of available balance per trade
MAX_TRADES_PER_HOUR  = 20         # Cap to avoid runaway fee burn

# ── TIGHT SCALPING EXIT RULES ────────────────────────────────────
# Scalping means taking many SMALL wins and cutting losses fast

TAKE_PROFIT_PCT = 0.008   # Exit at +0.8% gain  (was +12%)
STOP_LOSS_PCT   = 0.004   # Exit at -0.4% loss  (was -5%)
# Risk:reward = 1:2 — lose half of what you aim to gain

# ── DAILY SAFETY LIMITS ──────────────────────────────────────────
DAILY_LOSS_LIMIT_PCT = 0.25   # Pause bot if down 25% today
MAX_DAILY_TRADES     = 80     # Hard cap on trades per day

# ── SANDBOX: keep True until you're ready for real money ─────────
SANDBOX = True


# ════════════════════════════════════════════════════════════════
#  BOT STATE
# ════════════════════════════════════════════════════════════════

position          = None
trade_count_hour  = 0
trade_count_day   = 0
last_hour_reset   = datetime.now().hour
last_day_reset    = datetime.now().date()
daily_start_bal   = None
session_trades    = []


# ════════════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════════════

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}")


# ════════════════════════════════════════════════════════════════
#  CONNECT
# ════════════════════════════════════════════════════════════════

def connect():
    # Fix for Coinbase CDP keys — converts escaped \n back into real newlines
    secret = COINBASE_SECRET.replace("\\n", "\n")
    exchange = ccxt.coinbaseadvanced({
        "apiKey": COINBASE_KEY,
        "secret": secret,
    })
    try:
        exchange.fetch_time()
        log("Connected to Coinbase successfully.")
    except Exception as e:
        log(f"Coinbase connection warning: {e}")
        log("Check your API keys if trades fail.")
    claude = anthropic.Anthropic(api_key=CLAUDE_KEY)
    return exchange, claude


# ════════════════════════════════════════════════════════════════
#  MARKET DATA + INDICATORS
# ════════════════════════════════════════════════════════════════

def get_data(exchange):
    """
    Fetches 1-minute candles and calculates scalping indicators:

    EMA 5 / EMA 13  — very fast crossover signals
    RSI 7           — short-period momentum (more sensitive than RSI 14)
    VWAP            — volume-weighted average price (key for scalping)
    Bollinger Bands — shows when price is stretched too far
    """
    try:
        raw = exchange.fetch_ohlcv(TRADING_PAIR, TIMEFRAME, limit=60)
        df  = pd.DataFrame(raw, columns=["time","open","high","low","close","volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms")

        # Fast EMAs for scalping
        df["ema5"]  = ta.ema(df["close"], length=5)
        df["ema13"] = ta.ema(df["close"], length=13)

        # Short RSI — more reactive to recent moves
        df["rsi"] = ta.rsi(df["close"], length=7)

        # Bollinger Bands — upper/lower/middle bands
        bb = ta.bbands(df["close"], length=20, std=2)
        if bb is not None:
            df["bb_upper"] = bb["BBU_20_2.0"]
            df["bb_lower"] = bb["BBL_20_2.0"]
            df["bb_mid"]   = bb["BBM_20_2.0"]
        else:
            df["bb_upper"] = df["close"]
            df["bb_lower"] = df["close"]
            df["bb_mid"]   = df["close"]

        # Volume spike detector — is volume unusually high right now?
        df["vol_avg"]   = df["volume"].rolling(20).mean()
        df["vol_spike"] = df["volume"] > df["vol_avg"] * 1.5

        return df

    except Exception as e:
        log(f"Data error: {e}")
        return None


# ════════════════════════════════════════════════════════════════
#  CLAUDE AI SCALPING DECISION
# ════════════════════════════════════════════════════════════════

def ask_claude(claude_client, df, position_info):
    """
    Sends a tight, fast-turnaround prompt to Claude.
    For scalping we need quick, decisive answers — not long analysis.
    Claude is instructed to look for micro-momentum signals only.
    """
    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Calculate price momentum over last 5 candles
    price_change_5m = (last["close"] - df.iloc[-5]["close"]) / df.iloc[-5]["close"] * 100

    prompt = f"""You are an aggressive crypto scalping assistant making fast 1-minute trades.

Live market snapshot for {TRADING_PAIR}:
- Price now:        ${last['close']:,.2f}
- 5-min change:     {price_change_5m:+.3f}%
- EMA 5:            ${last['ema5']:,.2f}
- EMA 13:           ${last['ema13']:,.2f}
- RSI (7):          {last['rsi']:.1f}
- Bollinger upper:  ${last['bb_upper']:,.2f}
- Bollinger lower:  ${last['bb_lower']:,.2f}
- Volume spike:     {'YES' if last['vol_spike'] else 'no'}
- EMA 5 prev:       ${prev['ema5']:,.2f}
- EMA 13 prev:      ${prev['ema13']:,.2f}
- Position:         {position_info}

Scalping rules — respond BUY only if ALL of these are true:
  1. EMA 5 just crossed above EMA 13
  2. RSI is between 45 and 65 (not overbought)
  3. Price is near or below Bollinger middle band
  4. There is a volume spike

Respond SELL only if:
  - EMA 5 crossed below EMA 13, OR
  - RSI went above 72 (overbought — take profit), OR
  - We have an open position and price dropped below lower Bollinger band

Otherwise respond HOLD.

Reply with EXACTLY one word on line 1: BUY, SELL, or HOLD
Then one sentence on line 2 explaining why."""

    try:
        resp = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log(f"Claude error: {e}")
        return "HOLD\nError reaching Claude — defaulting to HOLD."


# ════════════════════════════════════════════════════════════════
#  ORDER EXECUTION
# ════════════════════════════════════════════════════════════════

def get_usdc_balance(exchange):
    try:
        if SANDBOX:
            return 100.0  # Simulated $100 balance
        bal = exchange.fetch_balance()
        return float(bal["total"].get("USDC", 0))
    except:
        return 0


def buy(exchange, price):
    global position
    balance   = get_usdc_balance(exchange)
    spend_usd = round(balance * TRADE_SIZE_PCT, 2)
    amount    = round(spend_usd / price, 6)

    if SANDBOX:
        log(f"[SANDBOX] BUY  {amount} BTC @ ${price:,.2f}  (${spend_usd:.2f} spent)")
    else:
        try:
            exchange.create_market_buy_order(TRADING_PAIR, amount)
            log(f"BUY  {amount} BTC @ ~${price:,.2f}")
        except Exception as e:
            log(f"Buy failed: {e}")
            return

    position = {
        "entry":       price,
        "amount":      amount,
        "spent_usd":   spend_usd,
        "stop":        price * (1 - STOP_LOSS_PCT),
        "target":      price * (1 + TAKE_PROFIT_PCT),
        "opened_at":   datetime.now(),
    }
    log(f"  Stop-loss: ${position['stop']:,.2f}  |  Target: ${position['target']:,.2f}")


def sell(exchange, price, reason):
    global position, session_trades
    if position is None:
        return

    pnl_pct = (price - position["entry"]) / position["entry"] * 100
    pnl_usd = position["spent_usd"] * (pnl_pct / 100)
    held_sec = (datetime.now() - position["opened_at"]).seconds

    if SANDBOX:
        log(f"[SANDBOX] SELL {position['amount']} BTC @ ${price:,.2f}  ({reason})")
    else:
        try:
            exchange.create_market_sell_order(TRADING_PAIR, position["amount"])
        except Exception as e:
            log(f"Sell failed: {e}")
            return

    log(f"  PnL: {pnl_pct:+.3f}%  (${pnl_usd:+.2f})  held {held_sec}s")
    session_trades.append({"pnl_pct": pnl_pct, "pnl_usd": pnl_usd, "reason": reason})
    position = None

    # Print running session summary
    wins   = [t for t in session_trades if t["pnl_usd"] > 0]
    losses = [t for t in session_trades if t["pnl_usd"] <= 0]
    total  = sum(t["pnl_usd"] for t in session_trades)
    log(f"  Session: {len(wins)}W / {len(losses)}L  |  Total P&L: ${total:+.2f}")


# ════════════════════════════════════════════════════════════════
#  SAFETY GUARDS
# ════════════════════════════════════════════════════════════════

def check_hard_exits(price):
    """Overrides Claude — sells immediately if stop or target is hit."""
    if position is None:
        return False, ""
    if price <= position["stop"]:
        return True, f"stop-loss hit (${price:,.2f} <= ${position['stop']:,.2f})"
    if price >= position["target"]:
        return True, f"take-profit hit (${price:,.2f} >= ${position['target']:,.2f})"
    return False, ""


def reset_counters_if_needed():
    global trade_count_hour, trade_count_day, last_hour_reset, last_day_reset, daily_start_bal
    now = datetime.now()
    if now.hour != last_hour_reset:
        trade_count_hour = 0
        last_hour_reset  = now.hour
    if now.date() != last_day_reset:
        trade_count_day  = 0
        last_day_reset   = now.date()
        daily_start_bal  = None
        log("New day — daily counters reset.")


def daily_loss_exceeded(exchange):
    global daily_start_bal
    if SANDBOX:
        return False
    if daily_start_bal is None:
        daily_start_bal = get_usdc_balance(exchange)
        return False
    current = get_usdc_balance(exchange)
    loss = (daily_start_bal - current) / daily_start_bal
    if loss >= DAILY_LOSS_LIMIT_PCT:
        log(f"Daily loss limit hit ({loss*100:.1f}%). Bot paused for today.")
        return True
    return False


# ════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ════════════════════════════════════════════════════════════════

def run():
    global trade_count_hour, trade_count_day

    log("=" * 55)
    log("HIGH-FREQUENCY SCALPING BOT — STARTING")
    log(f"Pair:      {TRADING_PAIR}")
    log(f"Timeframe: {TIMEFRAME} candles, check every {CHECK_EVERY_SECONDS}s")
    log(f"Targets:   +{TAKE_PROFIT_PCT*100:.1f}% profit  /  -{STOP_LOSS_PCT*100:.1f}% stop")
    log(f"Mode:      {'SANDBOX (simulated)' if SANDBOX else '*** LIVE ***'}")
    log("=" * 55)

    exchange, claude = connect()

    while True:
        try:
            reset_counters_if_needed()

            # ── Daily loss guard ──
            if daily_loss_exceeded(exchange):
                log("Sleeping 1 hour before retrying...")
                time.sleep(3600)
                continue

            # ── Trade frequency caps ──
            if trade_count_hour >= MAX_TRADES_PER_HOUR:
                log(f"Hourly trade cap ({MAX_TRADES_PER_HOUR}) reached. Waiting...")
                time.sleep(60)
                continue

            if trade_count_day >= MAX_DAILY_TRADES:
                log(f"Daily trade cap ({MAX_DAILY_TRADES}) reached. Done for today.")
                time.sleep(3600)
                continue

            # ── Fetch data ──
            df = get_data(exchange)
            if df is None or len(df) < 20:
                time.sleep(CHECK_EVERY_SECONDS)
                continue

            price = df.iloc[-1]["close"]
            rsi   = df.iloc[-1]["rsi"]

            # ── Hard exit check (runs before Claude) ──
            should_exit, exit_reason = check_hard_exits(price)
            if should_exit:
                sell(exchange, price, exit_reason)
                trade_count_hour += 1
                trade_count_day  += 1
                time.sleep(CHECK_EVERY_SECONDS)
                continue

            # ── Ask Claude ──
            pos_str = "none" if position is None else f"long since ${position['entry']:,.2f}"
            answer  = ask_claude(claude, df, pos_str)
            decision = answer.split("\n")[0].strip().upper()
            reason   = answer.split("\n")[1].strip() if "\n" in answer else ""

            log(f"${price:,.2f}  RSI:{rsi:.0f}  →  {decision}  |  {reason}")

            # ── Act ──
            if decision == "BUY" and position is None:
                buy(exchange, price)
                trade_count_hour += 1
                trade_count_day  += 1

            elif decision == "SELL" and position is not None:
                sell(exchange, price, "Claude signal")
                trade_count_hour += 1
                trade_count_day  += 1

            elif position is not None:
                held = (datetime.now() - position["opened_at"]).seconds
                log(f"  Holding — in position {held}s  |  entry ${position['entry']:,.2f}")

        except KeyboardInterrupt:
            log("Bot stopped. Final session summary:")
            if session_trades:
                wins  = [t for t in session_trades if t["pnl_usd"] > 0]
                total = sum(t["pnl_usd"] for t in session_trades)
                log(f"  Trades: {len(session_trades)}  |  Wins: {len(wins)}  |  Total P&L: ${total:+.2f}")
            break

        except Exception as e:
            log(f"Error: {e} — continuing...")

        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    run()
