import os, io, json, time, tempfile, requests
from redis import Redis
import psycopg
import numpy as np

# Essentia
import essentia.standard as es

REDIS_URL = os.environ["REDIS_URL"]
DATABASE_URL = os.environ["DATABASE_URL"]

r = Redis.from_url(REDIS_URL)
pg = psycopg.connect(DATABASE_URL, autocommit=True)

FEATURE_VERSION = "essentia_v1"
EXTRACTOR_VERSION = "essentia2.1b6+deam0"  # update if/when you add DEAM models

def vec_literal(arr): return "[" + ",".join(f"{float(x):.6f}" for x in arr) + "]"

def music_features_from_preview(mp3_bytes):
    # write to temp file (Essentia loader expects a filename)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as tmp:
        tmp.write(mp3_bytes); tmp.flush()
        audio = es.MonoLoader(filename=tmp.name, sampleRate=44100)()
        # MusicExtractor (aggregated stats)
        extractor = es.MusicExtractor(lowlevelStats=['mean','var'], rhythmStats=['mean'], tonalStats=['mean'])
        feats, _ = extractor(audio)

    # pull the fields we care about; be robust to missing keys
    def g(k, default=None):
        try: return feats[k]
        except Exception: return default

    # HPCP mean might be at tonal.hpcp.mean or tonal.hpcp
    hpcp = g('tonal.hpcp.mean', None)
    if hpcp is None: hpcp = g('tonal.hpcp', [0.0]*12)

    tonnetz = g('tonal.centroid.mean', [0.0]*6)  # 6D tonal centroid

    obj = {
        "bpm": float(g('rhythm.bpm', 0.0)),
        "onset_rate": float(g('rhythm.onset_rate', 0.0)),
        "key_key": g('tonal.key_key', None),
        "key_scale": g('tonal.key_scale', None),
        "hpcp_mean": [float(x) for x in hpcp],
        "tonnetz_mean": [float(x) for x in tonnetz],
        "mfcc_mean": [float(x) for x in g('lowlevel.mfcc.mean', [0.0]*13)],
        "spectral_centroid_mean": float(g('lowlevel.spectral_centroid.mean', 0.0)),
        "spectral_flatness_mean": float(g('lowlevel.spectral_flatness.mean', 0.0)),
        "loudness_integrated": float(g('lowlevel.loudness_ebu128.integrated', 0.0)),
        # place-holders for DEAM until you add a model
        "valence_pred": 0.5, "arousal_pred": 0.5
    }
    # assemble 62D vector (must match app’s assembler)
    def key_onehot(k, scale):
        idx_map={"C":0,"C#":1,"D":2,"D#":3,"E":4,"F":5,"F#":6,"G":7,"G#":8,"A":9,"A#":10,"B":11}
        vec=[0]*24; idx=idx_map.get((k or "").upper()); mj=0 if (scale or "").lower()=="major" else 1
        if idx is not None: vec[idx*2 + mj]=1
        return vec

    v=[]
    v.append(obj["bpm"]); v.append(obj["onset_rate"])
    v.extend(key_onehot(obj["key_key"], obj["key_scale"]))
    v.extend(obj["tonnetz_mean"]); v.extend(obj["hpcp_mean"]); v.extend(obj["mfcc_mean"])
    v.append(obj["spectral_centroid_mean"]); v.append(obj["spectral_flatness_mean"]); v.append(obj["loudness_integrated"])
    v.append(obj["valence_pred"]); v.append(obj["arousal_pred"])
    return obj, v

def upsert_isrc_feature(isrc, vec, feats):
    with pg.cursor() as cur:
        cur.execute("""
            INSERT INTO isrc_feature (isrc, feature_version, extractor_version, vec, feats)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (isrc, feature_version)
            DO UPDATE SET vec = EXCLUDED.vec, feats = EXCLUDED.feats,
                          extractor_version = EXCLUDED.extractor_version, updated_at = now();
        """, (isrc, FEATURE_VERSION, EXTRACTOR_VERSION, vec_literal(vec), json.dumps(feats)))

def run_once():
    job = r.lpop("essentia:jobs")
    if not job:
        time.sleep(1); return
    job = json.loads(job)
    previews = job.get("previews", [])
    mp3 = None
    for url in previews:
        try:
            resp = requests.get(url, timeout=20)
            if resp.ok and resp.content and len(resp.content) > 10000:
                mp3 = resp.content; break
        except Exception:
            continue
    if not mp3:
        # mark as tried; don't spam
        return

    feats, vec = music_features_from_preview(mp3)
    # store feature row (by ISRC)
    upsert_isrc_feature(job["isrc"], vec, feats)
    # ensure track_map row exists for this spotify id
    with pg.cursor() as cur:
        cur.execute("""
            INSERT INTO track_map (spotify_track_id,isrc,title,artist)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (spotify_track_id) DO UPDATE SET isrc=EXCLUDED.isrc, title=EXCLUDED.title, artist=EXCLUDED.artist;
        """, (job["spotify_id"], job["isrc"], job.get("title"), job.get("artist")))

if __name__ == "__main__":
    while True:
        try:
            run_once()
        except Exception as e:
            # don't crash the worker on single failures
            print("worker error:", e)
            time.sleep(2)
