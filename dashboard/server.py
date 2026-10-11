"""AI YouTube Analyzer dashboard: local server (127.0.0.1 only).

  /                 dashboard page
  /api/facts        numbers computed in Python from the latest data/reports/*.json (the AI never does maths)
  /api/refresh      re-pulls YouTube Analytics (yt_analytics.py), then returns fresh facts
  /api/coach?model= streams the local Ollama model's analysis of those facts (cached per report day)
  /api/ask (POST)   free question about the channel, answered from the same facts
"""
import datetime as dt, glob, json, os, re, subprocess, sys, urllib.request, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # AI-YouTube-Analyzer/
SCRIPTS = os.path.join(ROOT, "scripts")
REPORTS = os.path.join(ROOT, "data", "reports")
LIVE = os.path.join(ROOT, "data", "live")
sys.path.insert(0, SCRIPTS)
import live_tracker  # noqa: E402
from settings import CFG, TRACK_SINCE  # noqa: E402
PY = sys.executable
OLLAMA = CFG["ollama_url"].rstrip("/")
NEW_ERA = TRACK_SINCE  # uploads before this day are ignored (track_since in config.json)
PORT = int(CFG["port"])

TRAFFIC_NAMES = {"SUBSCRIBER": "Subscribers / home feed", "EXT_URL": "Outside links (WhatsApp etc.)", "SHORTS": "Shorts feed",
                 "YT_SEARCH": "YouTube search", "RELATED_VIDEO": "Suggested videos", "YT_CHANNEL": "Channel page",
                 "NO_LINK_OTHER": "Direct / unknown", "YT_OTHER_PAGE": "Other YouTube pages", "NOTIFICATION": "Notifications",
                 "PLAYLIST": "Playlists", "ADVERTISING": "Ads", "SHORTS_CONTENT_LINKS": "Links in Shorts", "END_SCREEN": "End screens",
                 "HASHTAGS": "Hashtag pages", "SOUND_PAGE": "Sound pages", "ANNOTATION": "Cards"}


def latest_report():
    files = sorted(glob.glob(os.path.join(REPORTS, "*.json")))
    if not files:
        return None, None
    return files[-1], json.load(open(files[-1], encoding="utf-8"))


def iso_dur(s):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def ret_at(curve, ratio):
    """audience still watching at a time ratio (linear interpolation of YouTube's 100-point curve)"""
    if not curve:
        return None
    pts = [(c["elapsedVideoTimeRatio"], c["audienceWatchRatio"]) for c in curve]
    if ratio <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= ratio <= x1:
            return y0 + (y1 - y0) * (ratio - x0) / (x1 - x0 or 1)
    return pts[-1][1]


def pct(a, b):
    return round(100 * a / b, 1) if b else 0.0


def mix(rows, key="insightTrafficSourceType"):
    tot = sum(r["views"] for r in rows) or 1
    return [{"source": TRAFFIC_NAMES.get(r[key], r[key]), "views": r["views"], "pct": pct(r["views"], tot)}
            for r in rows if r["views"] > 0]


def build_facts(rep):
    daily = rep.get("channel_daily", [])
    through = max((r["day"] for r in daily), default=rep["end"])
    vids, checks = [], []
    for v in rep["videos"]:
        if v["published"] < NEW_ERA or v.get("privacy") != "public":
            continue
        dur, s = iso_dur(v["duration"]), v.get("stats") or {}
        short = dur <= 180
        cur = v.get("retention") or []
        item = {
            "id": v["id"], "title": v["title"], "published": v["published"][:16].replace("T", " "), "type": "Short" if short else "Long",
            "duration_s": dur, "views_now": int(v.get("viewCount", 0)), "views_in_data": s.get("views", 0),
            "likes_now": int(v.get("likeCount", 0)), "comments_now": int(v.get("commentCount", 0)),
            "avg_view_pct": round(s.get("averageViewPercentage", 0), 1) if s else None,
            "avg_view_s": s.get("averageViewDuration"), "subs_gained": s.get("subscribersGained"), "shares": s.get("shares"),
            "traffic": mix(v.get("traffic") or [])[:5], "search_terms": [t["insightTrafficSourceDetail"] for t in (v.get("search_terms") or [])[:8]],
            "retention_curve": [[round(c["elapsedVideoTimeRatio"] * dur, 1), round(c["audienceWatchRatio"] * 100, 1)] for c in cur],
            "has_data": bool(s),
        }
        if item["views_now"]:
            item["likes_per_100"] = round(100 * item["likes_now"] / item["views_now"], 1)
        if cur:
            if short:
                item["kept_at_start_pct"] = round(100 * ret_at(cur, 0.03), 0)
                item["kept_at_end_pct"] = round(100 * ret_at(cur, 0.97), 0)
                item["rewatched"] = max(c["audienceWatchRatio"] for c in cur) > 1.0
            else:
                item["kept_10s_pct"] = round(100 * ret_at(cur, 10 / dur), 0)
                item["kept_30s_pct"] = round(100 * ret_at(cur, 30 / dur), 0)
                item["kept_60s_pct"] = round(100 * ret_at(cur, 60 / dur), 0)
                half = next((c["elapsedVideoTimeRatio"] * dur for c in cur if c["audienceWatchRatio"] < 0.5), None)
                item["half_gone_at_s"] = round(half) if half else None
        friends = sum(t["pct"] for t in item["traffic"] if t["source"] in (TRAFFIC_NAMES["EXT_URL"], TRAFFIC_NAMES["SUBSCRIBER"]))
        item["friends_share_pct"] = round(friends, 0)
        vids.append(item)

        # rule-of-thumb health checks (deterministic; thresholds are common creator benchmarks, not YouTube rules)
        t = item["title"][:48]
        if not item["has_data"]:
            checks.append({"video": t, "level": "wait", "text": f"No analytics yet (data runs up to {through}; {item['views_now']} public views so far)."})
            continue
        if short and item.get("avg_view_pct") is not None:
            lvl = "good" if item["avg_view_pct"] >= 70 else "ok" if item["avg_view_pct"] >= 50 else "bad"
            checks.append({"video": t, "level": lvl, "text": f"Shorts average viewed {item['avg_view_pct']}% (aim 70%+; loops push it past 100%)."})
        if not short and "kept_30s_pct" in item:
            lvl = "good" if item["kept_30s_pct"] >= 70 else "ok" if item["kept_30s_pct"] >= 55 else "bad"
            checks.append({"video": t, "level": lvl, "text": f"Hook: {item['kept_30s_pct']:.0f}% still watching at 0:30 (aim 70%+). {100 - item['kept_10s_pct']:.0f}% left in the first 10 s."})
            lvl = "good" if item["avg_view_pct"] >= 40 else "ok" if item["avg_view_pct"] >= 25 else "bad"
            checks.append({"video": t, "level": lvl, "text": f"Average viewed {item['avg_view_pct']}% of {dur // 60}:{dur % 60:02d} (aim 40%+ for an 8-min video)."})
        if friends >= 60:
            checks.append({"video": t, "level": "info", "text": f"{friends:.0f}% of its analysed views came from outside links + subscribers (mostly people who know you) — strangers may behave differently."})
        if item["views_now"] > 3 * max(item["views_in_data"], 1) and item["views_now"] > 300:
            checks.append({"video": t, "level": "good", "text": f"Public views jumped to {item['views_now']:,} vs {item['views_in_data']} in the analysed data — YouTube started recommending it after {through}."})

    ch = rep["channel"]
    ct = {r["creatorContentType"]: r for r in rep.get("content_type", []) if r["views"]}
    traffic = mix(rep.get("traffic", []))
    search = next((t["pct"] for t in traffic if t["source"] == TRAFFIC_NAMES["YT_SEARCH"]), 0)
    if search < 15:
        checks.append({"video": "Channel", "level": "ok", "text": f"Only {search}% of views come from YouTube search — SEO keeps compounding; recommendations + Shorts are the growth engine for now."})
    # deterministic wins (things clearly going right), so the AI never has to judge a number on its own
    wins = [c["video"] + ": " + c["text"] for c in checks if c["level"] == "good"]
    lg, sh = ct.get("videoOnDemand"), ct.get("shorts")
    tot_subs = sum(r["subscribersGained"] for r in rep.get("content_type", []))
    if lg and tot_subs and lg["subscribersGained"] >= 0.6 * tot_subs:
        wins.append(f"Long videos brought {lg['subscribersGained']} of the {tot_subs} new subscribers - long videos convert viewers into subscribers.")
    if lg and lg["shares"] >= 10:
        wins.append(f"Long videos were shared {lg['shares']} times - people pass them on.")
    rew = [v for v in vids if v.get("rewatched") and v["type"] == "Short"]
    if rew:
        wins.append(f"{len(rew)} Shorts were partly rewatched (curve above 100%) - loops work.")
    dev = rep.get("device", [])
    mob = pct(sum(r["views"] for r in dev if r["deviceType"] == "MOBILE"), sum(r["views"] for r in dev))
    return {
        "channel": {"name": ch["title"], "subscribers": int(ch["subscriberCount"]), "total_views": int(ch["viewCount"]),
                    "new_era_uploads": len(vids), "pulled": rep["pulled"], "data_through": through,
                    "note": "YouTube Analytics data lags 2-3 days; 'views_now' is the live public count."},
        "period": {"from": NEW_ERA, "to": through,
                   "views": sum(r["views"] for r in daily if r["day"] >= NEW_ERA),
                   "subs_gained": sum(r["subscribersGained"] for r in daily if r["day"] >= NEW_ERA),
                   "watch_hours": round(sum(r["estimatedMinutesWatched"] for r in daily if r["day"] >= NEW_ERA) / 60, 1)},
        "daily": [[r["day"], r["views"], r["subscribersGained"]] for r in daily if r["day"] >= NEW_ERA],
        "long_vs_shorts": {k: {"views": r["views"], "avg_view_pct": round(r["averageViewPercentage"], 1), "avg_view_s": r["averageViewDuration"],
                               "subs_gained": r["subscribersGained"], "likes": r["likes"], "shares": r["shares"]}
                           for k, r in (("long", ct.get("videoOnDemand")), ("shorts", ct.get("shorts"))) if r},
        "traffic": traffic,
        "audience": {"age_gender": [[r["ageGroup"].replace("age", ""), r["gender"], r["viewerPercentage"]] for r in rep.get("age_gender", [])],
                     "countries": [[r["country"], r["views"]] for r in rep.get("country", [])[:5]],
                     "mobile_pct": mob,
                     "subscribed_vs_not": [[r["subscribedStatus"], r["views"], round(r["averageViewPercentage"], 1)] for r in rep.get("subscribed", [])]},
        "videos": vids, "checks": checks, "wins": wins,
    }


def who(channel):
    """who the AI is coaching: config.json (creator, channel_about) + the channel name from the data"""
    s = f'{CFG["creator"]}, creator of the YouTube channel "{channel}".'
    return s + (" " + CFG["channel_about"] if CFG["channel_about"] else "")


SYSTEM = """You are a senior YouTube growth analyst coaching {WHO}
Rules:
- Use ONLY the numbers in FACTS. Never invent, estimate or recalculate numbers. If something is not in FACTS, say "not in the data yet".
- Analytics data lags 2-3 days; "views_now" is the live count, "views_in_data" is what the detailed analytics covers. Say so when it matters.
- Early views from outside links/subscribers are mostly people who already know the channel; don't over-read them.
- Copy numbers exactly as written in FACTS (same value, same unit). A number below its target is a problem, never a win.
- "What's working" may only use items from WINS. "Problems" may only use items from PROBLEMS. Never move an item between them.
- Every fix must be tied to a fact. Do not advise on posting days/times, thumbnails' CTR or anything else not in FACTS.
- Be direct, specific and practical, like a mentor. Short sentences. Plain English. No hype, no emojis.
Write in Markdown with exactly these sections:
## Score
One line: "X/10 - reason".
## What's working
3 bullets, each citing the number that proves it.
## Problems
3 bullets, each citing the number that proves it.
## Fix for the next video
5 numbered, concrete actions (hook, edit, title/thumbnail, Shorts, posting).
## Check next time
2 bullets: what to look at when fresh data arrives."""


def pt_offset(d):
    """US Pacific offset (YouTube Analytics days are Pacific-time days): -7 during DST (2nd Sun Mar -> 1st Sun Nov), else -8"""
    def nth_sunday(y, m, n):
        first = dt.date(y, m, 1)
        return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    return -7 if nth_sunday(d.year, 3, 2) <= d < nth_sunday(d.year, 11, 1) else -8


def build_live(rep):
    meta_p, snap_p, st_p = (os.path.join(LIVE, n) for n in ("videos.json", "snapshots.jsonl", "status.json"))
    meta = json.load(open(meta_p, encoding="utf-8")) if os.path.exists(meta_p) else {}
    snaps = [json.loads(l) for l in open(snap_p, encoding="utf-8") if l.strip()] if os.path.exists(snap_p) else []
    daily = {v["id"]: v.get("daily") or [] for v in (rep or {}).get("videos", [])}
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for vid, m in meta.items():
        pub = dt.datetime.fromisoformat(m["published"].replace("Z", "+00:00"))
        dur = iso_dur(m["duration"])
        series = [(dt.datetime.fromisoformat(s["t"]), s["v"][vid][0]) for s in snaps if vid in s["v"]]
        hourly = []
        for (t0, v0), (t1, v1) in zip(series, series[1:]):
            h = (t1 - t0).total_seconds() / 3600
            if h > 0.2:
                hourly.append([t1.isoformat(), round((v1 - v0) / h, 1), round(h, 2)])

        def gained(hours):
            if not series:
                return None
            t_last, v_last = series[-1]
            past = [p for p in series if p[0] <= t_last - dt.timedelta(hours=hours) + dt.timedelta(minutes=10)]
            return v_last - past[-1][1] if past else None
        race, cum = [], 0
        for r in daily.get(vid, []):  # official daily views, plotted at the END of each Pacific-time day
            day = dt.date.fromisoformat(r["day"])
            end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), dt.timezone(dt.timedelta(hours=pt_offset(day))))
            cum += r["views"]
            x = (end - pub).total_seconds() / 3600
            if 0 < x and end <= now:
                race.append([round(x, 2), cum, "daily"])
        race += [[round((t - pub).total_seconds() / 3600, 2), v, "live"] for t, v in series]
        race.sort()
        out.append({"id": vid, "title": re.sub(r"#\S+", "", m["title"]).strip(), "published": m["published"],
                    "type": "Short" if dur <= 180 else "Long", "age_h": round((now - pub).total_seconds() / 3600, 1),
                    "views": series[-1][1] if series else None, "gain_1h": gained(1), "gain_24h": gained(24),
                    "hourly": hourly, "race": race})
    out.sort(key=lambda v: v["published"], reverse=True)
    st = json.load(open(st_p)) if os.path.exists(st_p) else {"ok": False, "msg": "tracker has not run yet"}
    return {"videos": out, "status": st, "snapshots": len(snaps), "last": snaps[-1]["t"] if snaps else None,
            "subs_series": [[s["t"], s["subs"]] for s in snaps]}


def load_transcripts():
    return [json.load(open(p, encoding="utf-8")) for p in glob.glob(os.path.join(ROOT, "data", "transcripts", "*.json"))]


def match_transcript(v, trs, channel=""):
    """local final file <-> YouTube video: same duration (±1.5 s); Shorts also need a shared title word (many are ~47 s)"""
    stems = lambda s: {w[:5] for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) >= 3 or (w.isdigit() and len(w) >= 2)}
    skip = stems("short final youtube video videos the and for you your with how why what this that are not") | stems(channel)
    words = lambda s: stems(s) - skip  # generic words + the channel's own name would match every file
    cands = [t for t in trs if abs(t["duration"] - v["duration_s"]) <= 1.5]
    scored = sorted(((len(words(t["file"]) & words(v["title"])), t) for t in cands),
                    key=lambda x: (-x[0], abs(x[1]["duration"] - v["duration_s"])))
    if not scored:
        return None
    if v["type"] == "Short" and scored[0][0] == 0:
        return None
    return scored[0][1]


def said_between(tr, a, b):
    if not tr:
        return ""
    txt = " ".join(s[2] for s in tr["segments"] if s[1] > a and s[0] < b)
    return txt[:220]


def mmss(s):
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def drop_points(v, tr, n=3):
    """steepest audience losses (merged over ~4% of the video), skipping the very last 5% where every video drops"""
    pts = v["retention_curve"]  # [[sec, pct], ...]
    if len(pts) < 10:
        return []
    w = 4 if v["type"] == "Long" else 6
    cand = []
    for i in range(1, len(pts) - w):
        a, b = pts[i], pts[i + w]
        if b[0] > 0.95 * v["duration_s"]:
            break
        cand.append((a[1] - b[1], a, b))
    cand.sort(key=lambda c: -c[0])
    out = []
    for loss, a, b in cand:
        if loss < 2 or any(not (b[0] <= o["from_s"] or a[0] >= o["to_s"]) for o in out):
            continue
        out.append({"from_s": a[0], "to_s": b[0], "from_pct": a[1], "to_pct": b[1], "lost": round(loss, 1),
                    "said": said_between(tr, a[0] - 2, b[0] + 1)})
        if len(out) == n:
            break
    return sorted(out, key=lambda o: o["from_s"])


GUARD_RAILS = [l for l in SYSTEM.splitlines() if l.startswith(("- Copy numbers", '- "What', "- Every fix"))]
SYSTEM_RAW = "\n".join(l for l in SYSTEM.splitlines() if l not in GUARD_RAILS)


def raw_facts(f):
    """v1 input: the raw numbers as JSON (retention curves left out, as in v1)"""
    slim = {k: v for k, v in f.items() if k not in ("daily", "wins", "sheet", "coach_cached", "report_file")}
    slim["videos"] = [{k: v for k, v in x.items() if k != "retention_curve"} for x in f["videos"]]
    return json.dumps(slim, ensure_ascii=False)


SYSTEM_ASK = """You are a senior YouTube retention editor coaching {WHO}
Answer THEIR QUESTION directly, like a mentor sitting next to them. Plain English, short sentences, no emojis.
Rules:
- Use only numbers from FACTS and copy them exactly. If FACTS cannot answer something, say what data is missing.
- Explain causes using the PLAYBOOK below, and tie every cause to a specific moment, number or quote from FACTS (especially WHERE VIEWERS LEAVE).
{NOTES}- Do not repeat a "What's working / Problems" report unless he asks for one.
Format (Markdown), under 230 words:
**Short answer:** 1-2 sentences.
## Why it's happening
2-4 bullets: cause + proof (timestamp, %, or what he said there).
## What to do in the next video
3-5 numbered, concrete actions for the script, the edit or the title/thumbnail.

PLAYBOOK (common, well-known causes of viewers leaving):
- Steep drop in the first 10-30 s: the opening does not confirm the title/thumbnail promise fast enough (greeting, channel intro or backstory before the payoff). Fix: first sentence = the most surprising fact or the payoff that matches the title, then greet quickly, then say what they will get.
- Steady slide in the first 1-2 minutes: too much context before the point; no reason to stay. Fix: open loops ("the third reason surprised me"), preview the best moment, numbered structure, chapter cards.
- Sudden cliff mid-video: a slow, repeated or off-topic part, a long list, or words that sound like an ending ("so that's it", "to conclude"). Fix: cut it, shorten it, or add a visual change/pattern interrupt there.
- Big drop near the end is normal (outro); end right after the final payoff and tease the next video.
- Title/thumbnail promising something the video does not deliver early causes early drops.
- Rules of thumb: long videos aim for 70%+ still watching at 0:30 and 40%+ average viewed; Shorts aim for 70%+ average viewed, hook in the first 1-2 s, no intro, loop the ending.
- Early views from friends/WhatsApp links behave differently from strangers; judge with recommended/suggested traffic when it arrives."""


def system(template, channel):
    notes = "".join(f"- {n}\n" for n in CFG["creator_notes"])
    return template.replace("{WHO}", who(channel)).replace("{NOTES}", notes)


def facts_for_llm(f):
    """plain-English fact sheet: small models read labelled sentences far more accurately than raw JSON"""
    c, p, L = f["channel"], f["period"], []
    L.append(f"Channel: {c['name']}, {c['subscribers']} subscribers now. Detailed analytics cover {p['from']} to {p['to']} (data lags 2-3 days).")
    L.append(f"In that period: {p['views']} views, {p['watch_hours']} watch hours, {p['subs_gained']} new subscribers.")
    for k, lab in (("long", "Long videos"), ("shorts", "Shorts")):
        x = f["long_vs_shorts"].get(k)
        if x:
            L.append(f"{lab} combined: {x['views']} views, average viewed {x['avg_view_pct']}%, average watch {x['avg_view_s']} seconds, "
                     f"{x['subs_gained']} subscribers gained, {x['likes']} likes, {x['shares']} shares.")
    L.append("Where channel views came from: " + "; ".join(f"{t['source']} {t['pct']}%" for t in f["traffic"][:7]) + ".")
    a = f["audience"]
    L.append("Audience: " + "; ".join(f"{g} {s} {pp}%" for g, s, pp in a["age_gender"]) + f"; {a['mobile_pct']}% watch on mobile; top country "
             + (a["countries"][0][0] if a["countries"] else "?") + ".")
    L.append("")
    for v in f["videos"]:
        t = re.sub(r"#\S+", "", v["title"]).strip()
        s = f"- {v['type'].upper()} \"{t}\" ({v['duration_s']} s long, posted {v['published'][:10]}): {v['views_now']} live views"
        if not v["has_data"]:
            L.append(s + ". Detailed analytics not available yet.")
            continue
        s += f"; analytics cover {v['views_in_data']} of them: average viewed {v['avg_view_pct']}%, {v['subs_gained']} subscribers gained"
        if v["type"] == "Long" and "kept_30s_pct" in v:
            s += (f", {v['kept_10s_pct']:.0f}% still watching at 10 seconds, {v['kept_30s_pct']:.0f}% at 30 seconds, {v['kept_60s_pct']:.0f}% at 60 seconds,"
                  f" half the viewers gone by {v['half_gone_at_s']} seconds")
        if v["type"] == "Short" and "kept_at_end_pct" in v:
            s += f", {v['kept_at_end_pct']:.0f}% still watching at the end" + (", some viewers rewatched it" if v.get("rewatched") else "")
        s += f"; {v['friends_share_pct']:.0f}% of its views came from outside links + subscribers (people who already know the channel)"
        if v["traffic"]:
            s += "; top sources: " + ", ".join(f"{t2['source']} {t2['pct']}%" for t2 in v["traffic"][:3])
        L.append(s + ".")
    trs = load_transcripts()
    moments = []
    for v in f["videos"]:
        if not v["retention_curve"]:
            continue
        tr = match_transcript(v, trs, c["name"])
        t = re.sub(r"#\S+", "", v["title"]).strip()[:60]
        if tr:
            hook_end = 10 if v["type"] == "Long" else 4
            moments.append(f"- \"{t}\" first {hook_end} seconds {CFG['creator']} says: \"{said_between(tr, 0, hook_end)}\"")
        for d in drop_points(v, tr):
            moments.append(f"- \"{t}\" {mmss(d['from_s'])}-{mmss(d['to_s'])}: audience fell from {d['from_pct']:.0f}% to {d['to_pct']:.0f}%"
                           + (f"; there {CFG['creator']} says: \"{d['said']}\"" if d["said"] else ""))
    if moments:
        L.append("")
        L.append("WHERE VIEWERS LEAVE (steepest drops in each retention graph, with what was said at that moment):")
        L += moments
    L.append("")
    L.append("WINS (use these for What's working):")
    L += [f"- {w}" for w in f["wins"]] or ["- none yet"]
    L.append("PROBLEMS (below target - use these for Problems):")
    L += [f"- {c2['video']}: {c2['text']}" for c2 in f["checks"] if c2["level"] in ("bad", "ok")]
    L.append("CONTEXT (not good or bad):")
    L += [f"- {c2['video']}: {c2['text']}" for c2 in f["checks"] if c2["level"] in ("info", "wait")]
    return "\n".join(L)


def ollama_stream(model, messages, out):
    body = json.dumps({"model": model, "messages": messages, "stream": True, "think": False,
                       "options": {"temperature": 0.4, "num_ctx": 8192}}).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", body, {"Content-Type": "application/json"})
    text = []
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            if not line.strip():
                continue
            j = json.loads(line)
            piece = j.get("message", {}).get("content", "")
            if piece:
                text.append(piece)
                out(piece)
            if j.get("done"):
                break
    return "".join(text)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def stream_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")  # HTTP/1.0 server: stream raw text, end = connection close (no chunk framing)
        self.end_headers()

    def chunk(self, s):
        self.wfile.write(s.encode("utf-8"))
        self.wfile.flush()

    def stream_end(self):
        self.wfile.flush()

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            page = open(os.path.join(HERE, "index.html"), encoding="utf-8").read().replace("__BRAND__", CFG["brand"])
            return self.send(200, page, "text/html; charset=utf-8")
        if u.path == "/api/facts":
            path, rep = latest_report()
            if not rep:
                return self.send(404, json.dumps({"error": "no report yet - press Refresh"}))
            f = build_facts(rep)
            f["report_file"] = os.path.basename(path)
            f["sheet"] = facts_for_llm(f) + "\n" + raw_facts(f)  # every true number the AI could have been shown (fact-checker list)
            cache = path.replace(".json", ".coach.md")
            f["coach_cached"] = open(cache, encoding="utf-8").read() if os.path.exists(cache) else None
            sheet = cache.replace(".coach.md", ".coach.sheet.txt")  # the numbers that analysis was written from
            f["coach_sheet"] = open(sheet, encoding="utf-8").read() if os.path.exists(sheet) else None
            f["coach_time"] = dt.datetime.fromtimestamp(os.path.getmtime(cache)).strftime("%d %b %H:%M") if os.path.exists(cache) else None
            return self.send(200, json.dumps(f, ensure_ascii=False))
        if u.path == "/api/live":
            st = os.path.join(LIVE, "status.json")
            stale = not os.path.exists(st) or (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(json.load(open(st))["t"])).total_seconds() > 55 * 60
            if qs.get("snap") == ["1"] or stale:
                live_tracker.snap()
            return self.send(200, json.dumps(build_live(latest_report()[1]), ensure_ascii=False))
        if u.path == "/api/models":
            try:
                tags = json.load(urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5))
                names = sorted((m["name"] for m in tags["models"] if "embed" not in m["name"]), key=lambda n: n != CFG["model"])
            except Exception:
                names = []
            return self.send(200, json.dumps(names))
        if u.path == "/api/refresh":
            r = subprocess.run([PY, os.path.join(SCRIPTS, "yt_analytics.py")], capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
            live_tracker.snap()
            return self.send(200 if r.returncode == 0 else 500, json.dumps({"ok": r.returncode == 0, "log": (r.stdout + r.stderr)[-1500:]}))
        if u.path == "/api/coach":
            path, rep = latest_report()
            model = qs.get("model", [CFG["model"]])[0]
            f = build_facts(rep)
            self.stream_start()
            demo = qs.get("mode") == ["raw"]
            try:
                if demo:  # DEMO for the video: the original v1 approach (raw JSON, no guard-rails) - this is what made the AI invent numbers
                    txt = ollama_stream(model, [{"role": "system", "content": system(SYSTEM_RAW, f["channel"]["name"])},
                                                {"role": "user", "content": "FACTS:\n" + raw_facts(f) + "\n\nWrite the analysis now."}], self.chunk)
                    return self.stream_end()
                txt = ollama_stream(model, [{"role": "system", "content": system(SYSTEM, f["channel"]["name"])},
                                            {"role": "user", "content": "FACTS:\n" + facts_for_llm(f) + "\n\nWrite the analysis now."}], self.chunk)
                open(path.replace(".json", ".coach.md"), "w", encoding="utf-8").write(txt)
                open(path.replace(".json", ".coach.sheet.txt"), "w", encoding="utf-8").write(facts_for_llm(f) + "\n" + raw_facts(f))
            except Exception as e:
                self.chunk(f"\n\n**Ollama error:** {e}. Is Ollama running? (start it from the Start menu)")
            return self.stream_end()
        self.send(404, "{}")

    def do_POST(self):
        if urlparse(self.path).path != "/api/ask":
            return self.send(404, "{}")
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        _, rep = latest_report()
        f = build_facts(rep)
        self.stream_start()
        try:
            ollama_stream(req.get("model", CFG["model"]), [
                {"role": "system", "content": system(SYSTEM_ASK, f["channel"]["name"])},
                {"role": "user", "content": "FACTS:\n" + facts_for_llm(f) + f"\n\n{CFG['creator'].upper()}'S QUESTION: " + req.get("q", "")
                 + "\n\nAnswer this question only, in the format given."}], self.chunk)
        except Exception as e:
            self.chunk(f"\n\n**Ollama error:** {e}")
        self.stream_end()


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"AI YouTube Analyzer -> http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    srv.serve_forever()
