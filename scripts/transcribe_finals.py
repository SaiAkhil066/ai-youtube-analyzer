"""Optional: transcribe your final video files so retention drops can be matched to what was said at that moment.

Needs faster-whisper:  .venv/Scripts/python -m pip install faster-whisper
Run:                   .venv/Scripts/python scripts/transcribe_finals.py
Reads videos_dir + video_patterns from config.json (default: my_videos/*.mp4); already transcribed files are skipped.
Output: data/transcripts/<file stem>.json = {"file", "duration", "segments": [[start, end, text], ...]}
The dashboard matches a transcript to a YouTube video by duration (within 1.5 s).
"""
import glob, json, os

from faster_whisper import WhisperModel

from settings import CFG, PROJECT, path

VIDEOS = path(CFG["videos_dir"])
OUT = os.path.join(PROJECT, "data", "transcripts")
os.makedirs(OUT, exist_ok=True)

files = sorted({f for p in CFG["video_patterns"] for f in glob.glob(os.path.join(VIDEOS, p))})
if not files:
    print(f"no videos found in {VIDEOS} matching {CFG['video_patterns']}")
model = WhisperModel("small", device="cpu", compute_type="int8")
for f in files:
    stem = os.path.splitext(os.path.basename(f))[0]
    dst = os.path.join(OUT, stem + ".json")
    if os.path.exists(dst):
        continue
    segs, info = model.transcribe(f, language="en", vad_filter=True)
    rows = [[round(s.start, 2), round(s.end, 2), s.text.strip()] for s in segs]
    json.dump({"file": os.path.basename(f), "duration": round(info.duration, 3), "segments": rows},
              open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"{stem}: {len(rows)} segments", flush=True)
print("ALL DONE")
