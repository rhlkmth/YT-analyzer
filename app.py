import os
from io import BytesIO
from datetime import datetime, date

import streamlit as st
import pandas as pd
from googleapiclient.discovery import build

# ---------- CONSTANTS ----------
DEFAULT_VIEW_BRACKETS = {
    "1K-5K": (1_000, 5_000),
    "5K-10K": (5_000, 10_000),
    "10K-25K": (10_000, 25_000),
    "25K-50K": (25_000, 50_000),
    "50K-100K": (50_000, 100_000),
    "100K-250K": (100_000, 250_000),
    "250K-500K": (250_000, 500_000),
    "500K-1M": (500_000, 1_000_000),
    "1M+": (1_000_000, float("inf")),
}
SHORTS_LIMIT_SEC_DEFAULT = 180

# ---------- DATA HELPERS ----------
def check_if_short(duration_iso: str, limit_sec: int = SHORTS_LIMIT_SEC_DEFAULT) -> bool:
    h = m = s = 0
    d = duration_iso.replace("PT", "")
    if "H" in d:
        h, d = d.split("H")
        h = int(h)
    if "M" in d:
        m, d = d.split("M")
        m = int(m)
    if "S" in d:
        s = int(d.replace("S", ""))
    return h * 3600 + m * 60 + s <= limit_sec


def get_all_video_ids(api, channel_id: str) -> list[str]:
    uploads_id = (
        api.channels()
        .list(part="contentDetails", id=channel_id)
        .execute()["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    )
    ids, nxt = [], None
    while True:
        pl = (
            api.playlistItems()
            .list(part="contentDetails", playlistId=uploads_id, maxResults=50, pageToken=nxt)
            .execute()
        )
        ids.extend(i["contentDetails"]["videoId"] for i in pl["items"])
        nxt = pl.get("nextPageToken")
        if not nxt:
            break
    return ids


def get_video_details(api, video_ids: list[str], short_limit: int) -> pd.DataFrame:
    rows = []
    for i in range(0, len(video_ids), 50):
        resp = (
            api.videos()
            .list(part="snippet,statistics,contentDetails", id=",".join(video_ids[i : i + 50]))
            .execute()
        )
        for itm in resp["items"]:
            pub_dt = datetime.strptime(itm["snippet"]["publishedAt"], "%Y-%m-%dT%H:%M:%SZ")
            iso_dur = itm["contentDetails"]["duration"]
            rows.append(
                {
                    "video_id": itm["id"],
                    "title": itm["snippet"]["title"],
                    "published_date": pub_dt.date(),
                    "month": pub_dt.strftime("%B %Y"),
                    "view_count": int(itm["statistics"].get("viewCount", 0)),
                    "duration_iso": iso_dur,
                    "form": "Short" if check_if_short(iso_dur, short_limit) else "Long",
                }
            )
    return pd.DataFrame(rows)


def monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("month")
    return (
        pd.DataFrame(
            {
                "total_videos": g.size(),
                "shorts": g.apply(lambda x: (x["form"] == "Short").sum()),
                "longs": g.apply(lambda x: (x["form"] == "Long").sum()),
                "total_views": g["view_count"].sum(),
                "avg_views": g["view_count"].mean().astype(int),
            }
        )
        .reset_index()
        .sort_values("month")
    )


def view_bracket_split(df: pd.DataFrame, brackets: dict) -> pd.DataFrame:
    out = []
    for m, sub in df.groupby("month"):
        row = {"month": m}
        for name, (lo, hi) in brackets.items():
            row[name] = sub[(sub["view_count"] >= lo) & (sub["view_count"] < hi)].shape[0]
        out.append(row)
    return pd.DataFrame(out).sort_values("month")


def top_n(df: pd.DataFrame, n=20) -> pd.DataFrame:
    top = df.nlargest(n, "view_count")[["title", "view_count", "video_id", "month"]].copy()
    top["url"] = "https://youtu.be/" + top["video_id"]
    return top.reset_index(drop=True)


def to_excel(raw_df, summary_df, bracket_df, top_df) -> bytes:
    with BytesIO() as bio:
        with pd.ExcelWriter(bio, engine="xlsxwriter") as xw:
            raw_df.to_excel(xw, sheet_name="Raw Data", index=False)
            summary_df.to_excel(xw, sheet_name="Monthly Summary", index=False)
            bracket_df.to_excel(xw, sheet_name="View Brackets", index=False)
            top_df.to_excel(xw, sheet_name="Top 20 Videos", index=False)
        bio.seek(0)
        return bio.read()

# ---------- UI ----------
st.set_page_config(page_title="YT Analyzer", page_icon="📊", layout="centered")

st.title("📊 YouTube Channel Analyzer")

with st.sidebar:
    st.header("Inputs")
    api_key = st.text_input("API Key", type="password")
    channel_input = st.text_input("Channel ID / URL")
    full_hist = st.checkbox("Use entire history", value=True)
    if not full_hist:
        dr = st.date_input("Date range", [date(2010, 1, 1), date.today()])
        start_date, end_date = dr[0], dr[1]
    else:
        start_date, end_date = date(1970, 1, 1), date.today()

    short_limit = st.slider("Shorts max length (sec)", 15, 180, SHORTS_LIMIT_SEC_DEFAULT, 15)

    with st.expander("Advanced options"):
        vb_opt = st.checkbox("Custom view brackets")
        viewbr_text = st.text_area(
            "Bracket dict (name: [low, high])", value=str(DEFAULT_VIEW_BRACKETS), height=120
        )

    run = st.button("Run analysis")

if run:
    if not api_key or not channel_input:
        st.error("API key and channel ID/URL required")
        st.stop()

    cid = channel_input.strip()
    if "youtube.com" in cid:
        cid = cid.split("/")[-1]

    yt = build("youtube", "v3", developerKey=api_key)

    st.info("Fetching videos…")
    ids = get_all_video_ids(yt, cid)
    st.write(f"Total videos: {len(ids)}")

    st.info("Collecting details…")
    data = get_video_details(yt, ids, short_limit)
    data = data[(data["published_date"] >= start_date) & (data["published_date"] <= end_date)].reset_index(drop=True)

    if data.empty:
        st.warning("No videos in selected range")
        st.stop()

    # brackets
    brackets = DEFAULT_VIEW_BRACKETS
    if vb_opt:
        try:
            custom_br = eval(viewbr_text)
            if isinstance(custom_br, dict):
                brackets = {k: tuple(v) for k, v in custom_br.items()}
        except Exception:
            st.warning("Invalid custom brackets; using defaults")

    summary = monthly_summary(data)
    bracket_df = view_bracket_split(data, brackets)
    top20 = top_n(data)

    excel = to_excel(data, summary, bracket_df, top20)

    st.success("Done")
    st.download_button(
        "Download Excel report",
        data=excel,
        file_name="youtube_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.subheader("Preview")
    st.dataframe(summary.head())
