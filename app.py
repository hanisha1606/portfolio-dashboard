import streamlit as st
import yfinance as yf
import pandas as pd
import time

st.set_page_config(page_title="Portfolio Command Center", layout="wide")

# ---------------------------------------------------------------------------
# CONFIG — Dhruv's holdings
# ---------------------------------------------------------------------------
HOLDINGS = [
    {"ticker": "ACE.NS", "qty": 40, "buy_price": 950},
    {"ticker": "CIPLA.NS", "qty": 8, "buy_price": 1380},
    {"ticker": "HDFCAMC.NS", "qty": 3, "buy_price": 3900},
    {"ticker": "HINDPETRO.NS", "qty": 25, "buy_price": 380},
    {"ticker": "TARIL.NS", "qty": 15, "buy_price": 600},
    {"ticker": "RPTECH.NS", "qty": 30, "buy_price": 240},
    {"ticker": "CGPOWER.NS", "qty": 12, "buy_price": 650},
    {"ticker": "IRFC.NS", "qty": 100, "buy_price": 140},
    {"ticker": "IREDA.NS", "qty": 80, "buy_price": 165},
]

MACRO_TICKERS = {
    "Nifty 50": "^NSEI", "Sensex": "^BSESN", "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC", "Crude Oil": "CL=F", "USD/INR": "INR=X", "US 10yr Yield": "^TNX",
}

NEWS_RSS = {
    "Moneycontrol": "https://www.moneycontrol.com/rss/business.xml",
    "ET Markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
}

# ---------------------------------------------------------------------------
# DATA FETCHING (cached so it doesn't re-hit the API on every click)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=1800)  # refresh every 30 min
def get_stock_data(ticker):
    try:
        info = yf.Ticker(ticker).info
        return {
            "ticker": ticker.replace(".NS", ""),
            "name": info.get("longName"),
            "price": info.get("currentPrice"),
            "pe": info.get("trailingPE"),
            "roe_pct": round(info.get("returnOnEquity", 0) * 100, 1) if info.get("returnOnEquity") else None,
            "debt_to_equity": info.get("debtToEquity"),
            "sales_growth_pct": round(info.get("revenueGrowth", 0) * 100, 1) if info.get("revenueGrowth") else None,
            "sector": info.get("sector"),
        }
    except Exception:
        return None

@st.cache_data(ttl=1800)
def get_nifty200_tickers():
    url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
    try:
        df = pd.read_csv(url)
        return [t + ".NS" for t in df["Symbol"].tolist()]
    except Exception:
        return []

@st.cache_data(ttl=900)
def get_macro():
    rows = []
    for label, tkr in MACRO_TICKERS.items():
        try:
            hist = yf.Ticker(tkr).history(period="2d")
            if len(hist) >= 2:
                last, prev = hist["Close"].iloc[-1], hist["Close"].iloc[-2]
                chg = round((last - prev) / prev * 100, 2)
                rows.append({"label": label, "value": round(last, 2), "chg": chg})
        except Exception:
            pass
    return rows

@st.cache_data(ttl=1800)
def get_news():
    import feedparser
    items = []
    for source, url in NEWS_RSS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:8]:
                items.append({"headline": entry.title, "source": source, "published": entry.get("published", "")})
        except Exception:
            pass
    return items

# ---------------------------------------------------------------------------
# SCORING ENGINE
# ---------------------------------------------------------------------------
def quality_score(row):
    score = 50
    notes = []
    roe = row.get("roe_pct")
    if roe and roe > 20: score += 12; notes.append("Strong ROE")
    elif roe and roe > 12: score += 5
    else: notes.append("Weak ROE"); score -= 8

    de = row.get("debt_to_equity") or 0
    if row.get("sector") == "Financial Services":
        if de < 900: score += 5
    else:
        if de < 50: score += 10; notes.append("Low debt")
        elif de < 150: score += 3
        else: score -= 10; notes.append("High debt")

    pe = row.get("pe")
    if pe and pe < 20: score += 8; notes.append("Cheap valuation")
    elif pe and pe < 35: score += 2
    else: notes.append("Expensive valuation"); score -= 6

    g = row.get("sales_growth_pct")
    if g is not None and g > 20: score += 10; notes.append("Strong sales growth")
    elif g is not None and g > 8: score += 4
    elif g is not None: score -= 4; notes.append("Slow growth")

    score = max(0, min(100, score))
    verdict = "BUY" if score >= 68 else "HOLD" if score >= 48 else "TRIM"
    return score, verdict, ", ".join(notes)

def build_taste_profile(portfolio_df):
    return {
        "avg_pe": portfolio_df["pe"].mean(),
        "avg_roe": portfolio_df["roe_pct"].mean(),
        "avg_growth": portfolio_df["sales_growth_pct"].mean(),
        "top_sectors": portfolio_df["sector"].value_counts().head(2).index.tolist(),
    }

def fit_score(row, taste):
    score = 0
    if row.get("pe"):
        score += max(0, 30 - abs(row["pe"] - taste["avg_pe"]) / taste["avg_pe"] * 100)
    if row.get("sales_growth_pct") is not None:
        score += max(0, 30 - abs(row["sales_growth_pct"] - taste["avg_growth"]))
    if row.get("sector") in taste["top_sectors"]:
        score += 25
    if row.get("roe_pct") is not None:
        score += max(0, 15 - abs(row["roe_pct"] - taste["avg_roe"]))
    return round(min(100, score), 1)

def reason_text(row, fit, taste):
    if row.get("sector") in taste["top_sectors"]:
        return f"Same sector as several of your holdings ({row['sector']})."
    elif fit < 40:
        return "Outside your usual picks, but worth a look on fundamentals alone."
    return "Growth/valuation profile broadly similar to what you already hold."

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("📊 Portfolio Command Center")
tab1, tab2, tab3, tab4 = st.tabs(["My Portfolio", "Discover", "Macro Pulse", "News"])

# ---- Build portfolio dataframe (used across tabs) ----
with st.spinner("Loading your holdings..."):
    port_rows = []
    for h in HOLDINGS:
        d = get_stock_data(h["ticker"])
        if d:
            d["qty"] = h["qty"]
            d["buy_price"] = h["buy_price"]
            port_rows.append(d)
    port_df = pd.DataFrame(port_rows)
    port_df[["score", "verdict", "notes"]] = port_df.apply(lambda r: pd.Series(quality_score(r)), axis=1)
    port_df["cur_value"] = port_df["price"] * port_df["qty"]
    port_df["inv_value"] = port_df["buy_price"] * port_df["qty"]
    port_df["pnl"] = port_df["cur_value"] - port_df["inv_value"]
    port_df["pnl_pct"] = port_df["pnl"] / port_df["inv_value"] * 100

taste = build_taste_profile(port_df)

# ---- TAB 1: Portfolio ----
with tab1:
    c1, c2, c3 = st.columns(3)
    c1.metric("Current Value", f"₹{port_df['cur_value'].sum():,.0f}")
    c2.metric("Invested", f"₹{port_df['inv_value'].sum():,.0f}")
    total_pnl = port_df["pnl"].sum()
    c3.metric("P&L", f"₹{total_pnl:,.0f}", f"{total_pnl/port_df['inv_value'].sum()*100:.1f}%")

    st.subheader("Holdings")
    display_df = port_df[["ticker", "name", "price", "qty", "cur_value", "pnl_pct", "verdict", "notes"]].copy()
    display_df.columns = ["Ticker", "Name", "Price", "Qty", "Current Value", "P&L %", "Verdict", "Why"]
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.subheader("Sector Allocation")
    sector_alloc = port_df.groupby("sector")["cur_value"].sum().reset_index()
    st.bar_chart(sector_alloc.set_index("sector"))

# ---- TAB 2: Discover ----
with tab2:
    st.write(f"Your taste profile: avg P/E **{taste['avg_pe']:.1f}**, avg ROE **{taste['avg_roe']:.1f}%**, "
             f"avg growth **{taste['avg_growth']:.1f}%**, top sectors: **{', '.join(taste['top_sectors'])}**")

    if st.button("Scan Nifty 200 for new ideas"):
        with st.spinner("Scanning ~200 stocks — takes a couple minutes..."):
            universe = get_nifty200_tickers()
            owned = set(h["ticker"].replace(".NS", "") for h in HOLDINGS)
            results = []
            progress = st.progress(0)
            for i, t in enumerate(universe):
                d = get_stock_data(t)
                if d and d["ticker"] not in owned and d.get("pe") and d["pe"] > 0:
                    q, verdict, notes = quality_score(d)
                    f = fit_score(d, taste)
                    final = round(q * 0.6 + f * 0.4, 1)
                    reason = reason_text(d, f, taste)
                    results.append({"Ticker": d["ticker"], "Sector": d["sector"], "Quality": q,
                                     "Fit": f, "Final Rank": final, "Verdict": verdict, "Why": reason})
                progress.progress((i + 1) / len(universe))
                time.sleep(0.1)
            disc_df = pd.DataFrame(results).sort_values("Final Rank", ascending=False)
            st.session_state["discover_results"] = disc_df

    if "discover_results" in st.session_state:
        st.dataframe(st.session_state["discover_results"].head(30), use_container_width=True, hide_index=True)

# ---- TAB 3: Macro ----
with tab3:
    macro = get_macro()
    cols = st.columns(4)
    for i, m in enumerate(macro):
        cols[i % 4].metric(m["label"], m["value"], f"{m['chg']}%")

# ---- TAB 4: News ----
with tab4:
    news = get_news()
    for n in news:
        st.markdown(f"**{n['headline']}**  \n*{n['source']} · {n['published']}*")
        st.divider()
