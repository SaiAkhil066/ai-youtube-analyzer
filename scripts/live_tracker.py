"""Hourly live tracker: snapshots public view/like/comment counts of every upload since track_since (config.json).

Costs ~3 of the 10,000 free YouTube API units per run (channels + 1 playlist page + 1 videos call).
Run by Windows Task Scheduler (install_hourly_tracker.bat) with pythonw, or by the dashboard when stale.
Never opens a browser: if the login expired, it writes data/live/status.json and the dashboard asks for a re-login.
Output: data/live/snapshots.jsonl  (one line per run: {"t": utc iso, "subs": n, "v": {id: [views, likes, comments]}})
        data/live/videos.json      (id -> title, published, duration)
"""
import datetime as dt, json, os, sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from settings import PROJECT, SCOPES, TOKEN, TRACK_SINCE

LIVE = os.path.join(PROJECT, "data", "live")


def status(ok, msg):
    json.dump({"ok": ok, "msg": msg, "t": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
              open(os.path.join(LIVE, "status.json"), "w"))


def snap():
    os.makedirs(LIVE, exist_ok=True)
    if not os.path.exists(TOKEN):
        status(False, "not logged in yet - press Refresh data in the dashboard to log in")
        return False
    try:
        c = Credentials.from_authorized_user_file(TOKEN, SCOPES)
        if not c.valid:
            c.refresh(Request())
            open(TOKEN, "w").write(c.to_json())
    except Exception as e:
        status(False, f"login expired - press Refresh data in the dashboard to log in again ({str(e)[:120]})")
        return False
    try:
        yt = build("youtube", "v3", credentials=c, cache_discovery=False)
        ch = yt.channels().list(part="statistics,contentDetails", mine=True).execute()["items"][0]
        ids = [i["contentDetails"]["videoId"] for i in yt.playlistItems().list(
            part="contentDetails", playlistId=ch["contentDetails"]["relatedPlaylists"]["uploads"], maxResults=50).execute()["items"]]
        meta_p = os.path.join(LIVE, "videos.json")
        meta = json.load(open(meta_p, encoding="utf-8")) if os.path.exists(meta_p) else {}
        row = {"t": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "subs": int(ch["statistics"]["subscriberCount"]), "v": {}}
        for v in yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids)).execute()["items"]:
            if v["snippet"]["publishedAt"] < TRACK_SINCE:
                continue
            s = v["statistics"]
            row["v"][v["id"]] = [int(s.get("viewCount", 0)), int(s.get("likeCount", 0)), int(s.get("commentCount", 0))]
            meta[v["id"]] = {"title": v["snippet"]["title"], "published": v["snippet"]["publishedAt"], "duration": v["contentDetails"]["duration"]}
        with open(os.path.join(LIVE, "snapshots.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        json.dump(meta, open(meta_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        status(True, f"tracked {len(row['v'])} videos")
        return True
    except Exception as e:
        status(False, f"tracker error: {str(e)[:200]}")
        return False


if __name__ == "__main__":
    ok = snap()
    if sys.stdout:  # pythonw has no console
        print("ok" if ok else open(os.path.join(LIVE, "status.json")).read())
