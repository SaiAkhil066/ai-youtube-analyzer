"""Your settings. Copy config.example.json to config.json and edit it (config.json stays on your PC - it is git-ignored).

Every script imports this, so a missing config.json still works with the defaults below.
"""
import datetime as dt, json, os

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the AI-YouTube-Analyzer folder

DEFAULTS = {
    "creator": "the creator",       # who the AI coach talks to, e.g. "Akhil"
    "channel_about": "",             # one or two sentences about the channel: topics, audience, video lengths
    "creator_notes": [],             # extra rules for the AI, e.g. "Keep the greeting at the start"
    "track_since": "",               # YYYY-MM-DD: only uploads from this day on are analysed ("" = last 90 days)
    "keys_dir": "keys",              # folder with client_secret.json (+ token.json after the first login)
    "videos_dir": "my_videos",       # your final video files, only needed for transcripts (optional)
    "video_patterns": ["*.mp4"],
    "model": "qwen2.5vl:7b",         # default Ollama model in the dropdown
    "num_ctx": 16384,                # how many tokens the model reads at once; the fact sheet grows with every upload
    "ollama_url": "http://127.0.0.1:11434",
    "brand": "MY",                   # dashboard logo: "<brand> ANALYTICS."
    "port": 8765,
}


def _load():
    cfg = dict(DEFAULTS)
    p = os.path.join(PROJECT, "config.json")
    if os.path.exists(p):
        cfg.update(json.load(open(p, encoding="utf-8")))
    return cfg


def path(p):
    """config paths may be relative to the project folder"""
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(PROJECT, p))


CFG = _load()
KEYS = path(CFG["keys_dir"])
SECRET, TOKEN = os.path.join(KEYS, "client_secret.json"), os.path.join(KEYS, "token.json")
TRACK_SINCE = CFG["track_since"] or (dt.date.today() - dt.timedelta(days=90)).isoformat()
SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly",
          "https://www.googleapis.com/auth/youtube.readonly"]
