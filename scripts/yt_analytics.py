"""Read-only YouTube Analytics pull.

First run opens the browser once for "Allow"; the token is cached next to client_secret.json (keys_dir in config.json).
Never share either file.
Usage:  .venv/Scripts/python scripts/yt_analytics.py [--start 2026-10-01]
Output: data/reports/<today>.json  (channel totals, per-video stats, retention curves, traffic, search terms, audience)
"""
import argparse, datetime as dt, json, os, sys
sys.stdout.reconfigure(encoding="utf-8")

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from settings import PROJECT, SCOPES, SECRET, TOKEN, TRACK_SINCE


def creds():
    c = Credentials.from_authorized_user_file(TOKEN, SCOPES) if os.path.exists(TOKEN) else None
    if c and c.expired and c.refresh_token:
        try:
            c.refresh(Request())
        except Exception:
            c = None
    if not c or not c.valid:
        if not os.path.exists(SECRET):
            sys.exit(f"missing {SECRET} - download the OAuth Desktop client JSON first (README, step 3)")
        c = InstalledAppFlow.from_client_secrets_file(SECRET, SCOPES).run_local_server(port=0, prompt="consent")
    open(TOKEN, "w").write(c.to_json())
    return c


def q(ya, errors, label, **kw):
    try:
        r = ya.reports().query(ids="channel==MINE", **kw).execute()
        cols = [h["name"] for h in r.get("columnHeaders", [])]
        return [dict(zip(cols, row)) for row in r.get("rows", [])]
    except Exception as e:
        errors.append(f"{label}: {str(e)[:300]}")
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=TRACK_SINCE)
    a = ap.parse_args()
    end = dt.date.today().isoformat()
    c = creds()
    yt, ya = build("youtube", "v3", credentials=c), build("youtubeAnalytics", "v2", credentials=c)
    err, out = [], {"pulled": dt.datetime.now().isoformat(timespec="seconds"), "start": a.start, "end": end}

    ch = yt.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()["items"][0]
    out["channel"] = {"title": ch["snippet"]["title"], "id": ch["id"], **ch["statistics"]}

    # every upload (title, publish time, duration, public counts)
    vids, tok = [], None
    while True:
        pl = yt.playlistItems().list(part="contentDetails", playlistId=ch["contentDetails"]["relatedPlaylists"]["uploads"],
                                     maxResults=50, pageToken=tok).execute()
        vids += [i["contentDetails"]["videoId"] for i in pl["items"]]
        tok = pl.get("nextPageToken")
        if not tok:
            break
    meta = {}
    for i in range(0, len(vids), 50):
        for v in yt.videos().list(part="snippet,contentDetails,statistics,status", id=",".join(vids[i:i + 50])).execute()["items"]:
            meta[v["id"]] = {"title": v["snippet"]["title"], "published": v["snippet"]["publishedAt"],
                             "duration": v["contentDetails"]["duration"], "privacy": v["status"]["privacyStatus"], **v["statistics"]}

    base = dict(startDate=a.start, endDate=end)
    M = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost,likes,comments,shares"
    out["channel_daily"] = q(ya, err, "daily", dimensions="day", metrics="views,estimatedMinutesWatched,subscribersGained", sort="day", **base)
    out["content_type"] = q(ya, err, "content_type", dimensions="creatorContentType", metrics=M, **base)
    out["traffic"] = q(ya, err, "traffic", dimensions="insightTrafficSourceType", metrics="views,estimatedMinutesWatched", sort="-views", **base)
    out["age_gender"] = q(ya, err, "age_gender", dimensions="ageGroup,gender", metrics="viewerPercentage", **base)
    out["country"] = q(ya, err, "country", dimensions="country", metrics="views,averageViewDuration", sort="-views", maxResults=15, **base)
    out["device"] = q(ya, err, "device", dimensions="deviceType", metrics="views,estimatedMinutesWatched", sort="-views", **base)
    out["subscribed"] = q(ya, err, "subscribed", dimensions="subscribedStatus", metrics="views,averageViewPercentage", **base)

    per = {r["video"]: r for r in q(ya, err, "per_video", dimensions="video", metrics=M, sort="-views", maxResults=200, **base)}
    out["videos"] = []
    for vid, m in meta.items():
        if m["published"] < TRACK_SINCE:  # older uploads only need totals
            continue
        f = f"video=={vid}"
        out["videos"].append({
            "id": vid, **m, "stats": per.get(vid, {}),
            "retention": q(ya, err, f"retention {vid}", dimensions="elapsedVideoTimeRatio",
                           metrics="audienceWatchRatio,relativeRetentionPerformance", filters=f, **base),
            "traffic": q(ya, err, f"traffic {vid}", dimensions="insightTrafficSourceType", metrics="views,averageViewDuration",
                         filters=f, sort="-views", **base),
            "search_terms": q(ya, err, f"search {vid}", dimensions="insightTrafficSourceDetail", metrics="views",
                              filters=f + ";insightTrafficSourceType==YT_SEARCH", sort="-views", maxResults=25, **base),
            "daily": q(ya, err, f"daily {vid}", dimensions="day", metrics="views,likes,subscribersGained",
                       filters=f, sort="day", endDate=end,  # analytics days are Pacific time -> start a day early
                       startDate=(dt.date.fromisoformat(m["published"][:10]) - dt.timedelta(days=1)).isoformat()),
            "suggested_from": q(ya, err, f"suggested {vid}", dimensions="insightTrafficSourceDetail", metrics="views",
                                filters=f + ";insightTrafficSourceType==RELATED_VIDEO", sort="-views", maxResults=15, **base),
        })
    out["videos"].sort(key=lambda v: v["published"])
    out["errors"] = err

    os.makedirs(os.path.join(PROJECT, "data", "reports"), exist_ok=True)
    path = os.path.join(PROJECT, "data", "reports", f"{end}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"{out['channel']['title']}: {out['channel'].get('subscriberCount')} subs, {len(out['videos'])} uploads -> {path}")
    for v in out["videos"]:
        s = v["stats"]
        print(f"  {v['published'][:10]}  {s.get('views', v.get('viewCount', 0)):>6} views  "
              f"avg {round(s['averageViewPercentage'], 1) if s else '-'}%  {v['title'][:60]}")
    if err:
        print(f"{len(err)} query errors (see report)")


if __name__ == "__main__":
    main()
