# AI YouTube Analyzer

A free dashboard that reads your YouTube channel's analytics, tracks every video's views every hour, and has an AI
**running on your own PC** tell you what's working, what isn't, and what to fix. You can also ask it questions like
*"why do people stop watching my videos?"*.

No subscription, no paid API, and your data never leaves your computer.

- **Analytics:** views, watch time, retention graphs, traffic sources, search terms, audience and devices, from Google's free YouTube APIs
- **Live tracker:** saves view, like and comment counts every hour, with a "launch race" chart comparing your videos hour by hour
- **AI coach:** a local model (Ollama, `qwen3:8b`) gives a score, what's working, the problems and 5 fixes
- **Ask box:** ask anything about your channel; the answers use your retention drops and what you said at those moments
- **Fact-checker:** every number the AI writes is checked against your real data. Green means verified, red means made up

## Why the AI never does the maths

In the first version, the AI read the raw data directly. It wrote that **4.1%** of views came from YouTube search.
The real number was **9.2%**: 4.1% belonged to a different row ("Direct / unknown"). It also listed numbers that were
below target under *"What's working"*.

So now the work is split:

```
YouTube Analytics API (read-only)  ──►  Python computes every number,       ──►  Local AI (Ollama)
Live view counts (every hour)           decides what is a WIN or a PROBLEM,       only explains them
Transcripts of your videos              finds where viewers leave                       │
                                                                                         ▼
                                        Dashboard  ◄──  Fact-checker: every number the AI wrote
                                                        is looked up in your data (red = made up)
```

Tick **"Demo: raw data"** in the AI Coach to see the first version (raw data, no guard-rails) and watch for red numbers.
The fact-checker catches wrong numbers, but it can't catch a wrong opinion. That's why Python, not the AI, decides
what counts as good or bad.

## What you need

- A Windows PC (Mac/Linux also works, see the end)
- Python 3.10 or newer: [python.org/downloads](https://www.python.org/downloads/) (tick **"Add python.exe to PATH"** during the install)
- A Google account that owns a YouTube channel
- About 6 GB of free disk space for the AI model. A graphics card helps: one analysis takes about 2 minutes on a GTX 1650 laptop GPU

## Setup (about 15 minutes, one time)

### 1. Download the code
Click **Code → Download ZIP** on this page and unzip it, or run `git clone https://github.com/SaiAkhil066/ai-youtube-analyzer.git`.

### 2. Install the Python packages
Double-click **`setup.bat`**. It creates a `.venv` folder, installs the packages, and creates your `config.json` and
an empty `keys` folder.

### 3. Get your free Google login file (`client_secret.json`)
1. Open [console.cloud.google.com](https://console.cloud.google.com/) and create a new project (any name).
2. Go to **APIs & Services → Library**. Search for **YouTube Data API v3** and click **Enable**. Then search for
   **YouTube Analytics API** and click **Enable**.
3. Go to **Google Auth Platform** (called "OAuth consent screen" in some menus) and click **Get started**. Enter an
   app name and your email, choose **External** as the audience, and finish.
4. Under **Audience → Test users**, add the Google account that owns your channel.
5. Go to **Clients → Create client**, choose **Desktop app** as the application type, and click **Create**.
6. Click **Download JSON**. Rename the file to `client_secret.json` and put it in the `keys` folder.

> **Never share `client_secret.json` or `token.json`, and never put them on screen.** They are your login.
> The `keys` folder is in `.gitignore`, so git will never upload it.

### 4. Install the local AI
1. Install Ollama from [ollama.com](https://ollama.com/download).
2. Open a terminal and run `ollama pull qwen3:8b` (about 5 GB).
   No graphics card? Use `ollama pull qwen3:4b` (about 2.5 GB) and set `"model": "qwen3:4b"` in `config.json`.

### 5. Tell the AI about your channel
Open `config.json` in Notepad:

| Setting | What it does |
|---|---|
| `creator` | Your name. The AI coach talks to you. |
| `channel_about` | One or two sentences: your topics, audience and video lengths. |
| `creator_notes` | Your own rules for the AI, e.g. "Keep the greeting at the start". |
| `track_since` | `YYYY-MM-DD`. Only uploads from this day on are analysed. Empty = the last 90 days. |
| `model` | The Ollama model selected by default. |
| `brand` | The word in the dashboard logo ("MY ANALYTICS."). |
| `keys_dir`, `videos_dir`, `video_patterns` | Where your login file and your video files are (you can usually leave these). |

### 6. Open the dashboard
Double-click **`Open Dashboard.bat`**. It opens http://127.0.0.1:8765 in your browser.
1. Click **↻ Refresh data**. A Google window opens. Pick your channel's account. If your channel is a Brand Account,
   pick the channel and not your personal profile.
2. You'll see *"Google hasn't verified this app"*. That's normal, because it's your own app: click **Continue**,
   tick both permissions and click **Continue**.
3. When you see *"The authentication flow has completed"*, go back to the dashboard. Your numbers are loaded.
4. Start Ollama, choose a model, and click **Analyse my channel**.

### 7. Optional extras
- **Hourly live tracker:** double-click `install_hourly_tracker.bat` once. Windows then saves your view counts every
  hour while the PC is on. The dashboard also saves a snapshot whenever you open it.
- **Transcripts** (so the AI knows what you said at the moment viewers left): put your final video files in
  `my_videos/`, then run:
  ```
  .venv\Scripts\python -m pip install faster-whisper
  .venv\Scripts\python scripts\transcribe_finals.py
  ```
  Transcripts are matched to your YouTube videos by length, so use the exact files you uploaded.

## Costs

₹0 / $0. The YouTube APIs are free: the Data API allows 10,000 units a day and the hourly tracker uses about 3 per run.
Ollama and Python are free too.

## Limits

- YouTube's detailed analytics arrive 1–3 days late. The live counts are instant.
- The API doesn't give thumbnail CTR, impressions or Shorts "viewed vs swiped away". Those stay in YouTube Studio.
- While your Google app is in "Testing" mode, the login lasts about 7 days. When the dashboard says the login
  expired, click **Refresh data** and allow it again.
- A local 8B model can still be wrong about opinions. The fact-checker only catches wrong numbers.

## Safety

- **Read-only:** it asks only for `yt-analytics.readonly` and `youtube.readonly`, so it can't upload, edit or delete anything.
- The dashboard runs on `127.0.0.1` only, so other devices on your network can't open it.
- Your login (`keys/`), your settings (`config.json`) and your channel data (`data/`) are git-ignored and stay on your PC.

## What's in this folder

| File / folder | What it is |
|---|---|
| `Open Dashboard.bat` | Starts the dashboard |
| `setup.bat` | One-time install |
| `install_hourly_tracker.bat` | Optional: saves live view counts every hour (Windows Task Scheduler) |
| `config.example.json` | Template for your `config.json` |
| `dashboard/server.py` | Local server: computes the facts, talks to Ollama, streams the answers |
| `dashboard/index.html` | The dashboard page, including charts and the fact-checker |
| `scripts/yt_analytics.py` | Pulls the detailed analytics: retention, traffic, audience, search terms |
| `scripts/live_tracker.py` | Saves live views, likes and comments |
| `scripts/transcribe_finals.py` | Optional: transcribes your videos with faster-whisper |
| `scripts/settings.py` | Reads `config.json` |

## Mac / Linux

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json && mkdir -p keys
.venv/bin/python dashboard/server.py
```
For the hourly tracker, add `.venv/bin/python scripts/live_tracker.py` to `crontab -e` with `0 * * * *`.

## Credits

Built by **N Sai Akhil** for the YouTube channel
[Akki The Tecki](https://www.youtube.com/channel/UCNij5m_WT_SOUdoolNiYGtw), with Claude (Anthropic's AI) as the
coding partner. MIT licence: use it, change it, share it.
