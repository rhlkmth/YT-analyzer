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
    hours = minutes = seconds = 0
    d = duration_iso.replace("PT", "")
    if "H" in d:
        hours, d = d.split("H")
        hours = int(hours)
    if "M" in d:
        minutes, d = d.split("M")
        minutes = int(minutes)
    if "S" in d:
        seconds = int(d.replace("S", ""))
    return hours * 3600 + minutes * 60 + seconds <= limit_sec


def get_all_video_ids(api, channel_id: str) -> list[str]:
    uploads_pl = (
        api.channels()
        .list(part="contentDetails", id=channel_id)
        .execute()["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    )
    vids, nxt = [], None
    while True:
        resp = (
            api.playlistItems()
            .list(part="contentDetails", playlistId=uploads_pl, maxResults=50, pageToken=nxt)
            .execute()
        )
        vids.extend(i["contentDetails"]["videoId"] for i in resp["items"])
        nxt = resp.get("nextPageToken")
        if not nxt:
            break
    return vids


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
            rows.append(
                {
                    "video_id": itm["id"],
                    "title": itm["snippet"]["title"],
                    "published_date": pub_dt.date(),
                    "month": pub_dt.strftime("%B %Y"),
                    "view_count": int(itm["statistics"].get("viewCount", 0)),
                    "duration_sec": None,  # filled below
                    "form": None,  # filled below
                }
            )
            rows[-1]["duration_sec"] = (
                lambda d=itm["contentDetails"]["duration"]: (
                    check_if_short(d, short_limit),
                    d,
                )
            )()[1]  # keep ISO duration for table
            rows[-1]["form"] = "Short" if check_if_short(itm["contentDetails"]["duration"], short_limit) else "Long"
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
                "median_views": g["view_count"].median().astype(int),
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


def to_excel(
    raw_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    bracket_df: pd.DataFrame,
    top_df: pd.DataFrame,
) -> bytes:
    with BytesIO() as bio:
        with pd.ExcelWriter(bio, engine="xlsxwriter") as xw:
            raw_df.to_excel(xw, sheet_name="Raw Data", index=False)
            summary_df.to_excel(xw, sheet_name="Monthly Summary", index=False)
            bracket_df.to_excel(xw, sheet_name="View Brackets", index=False)
            top_df.to_excel(xw, sheet_name="Top 20 Videos", index=False)

            # overview sheet
            ov = xw.book.add_worksheet("Overview")
            ov.write_row(
                0,
                0,
                ["Total videos", len(raw_df), "Total views", int(raw_df["view_count"].sum())],
            )
            ov.write_row(
                2,
                0,
                ["Shorts", (raw_df["form"] == "Short").sum(), "Longs", (raw_df["form"] == "Long").sum()],
            )

        bio.seek(0)
        return bio.read()

# ---------- STREAMLIT UI ----------
st.title("YouTube Channel Analyzer")

api_key = st.text_input("YouTube API Key", type="password")
channel_input = st.text_input("Channel ID or URL")
analyze_all = st.checkbox("Analyze entire history", value=True)

col1, col2 = st.columns(2)
if not analyze_all:
    start_date = col1.date_input("Start date", value=date(2010, 1, 1))
    end_date = col2.date_input("End date", value=date.today())
else:
    start_date = date(1970, 1, 1)
    end_date = date.today()

short_limit = st.slider("Shorts length threshold (sec)", 1, 180, SHORTS_LIMIT_SEC_DEFAULT)

viewbr_json = st.text_area(
    "Custom view-bracket JSON (optional)",
    value=str(DEFAULT_VIEW_BRACKETS),
    height=150,
)

run_btn = st.button("Run analysis")

if run_btn:
    if not api_key or not channel_input:
        st.error("API key and channel ID/URL are required.")
        st.stop()

    channel_id = channel_input.strip()
    if "youtube.com" in channel_id:
        # simplistic parse
        channel_id = channel_id.split("/")[-1]

    yt = build("youtube", "v3", developerKey=api_key)
    with st.spinner("Fetching video list…"):
        ids = get_all_video_ids(yt, channel_id)
    with st.spinner("Fetching video details…"):
        df = get_video_details(yt, ids, short_limit)

    df = df[(df["published_date"] >= start_date) & (df["published_date"] <= end_date)].reset_index(drop=True)

    summary = monthly_summary(df)
    brackets_dict = DEFAULT_VIEW_BRACKETS
    try:
        custom_br = eval(viewbr_json)
        if isinstance(custom_br, dict):
            brackets_dict = custom_br
    except Exception:
        st.warning("Invalid custom bracket JSON; using defaults.")

    bracket = view_bracket_split(df, brackets_dict)
    top20 = top_n(df)

    excel_bytes = to_excel(df, summary, bracket, top20)
    st.success("Analysis complete.")
    st.download_button(
        "Download Excel",
        data=excel_bytes,
        file_name="youtube_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

