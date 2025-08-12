import os, json, secrets, time
from datetime import timedelta

from flask import (
    Flask, session, redirect, request, url_for, render_template, abort, jsonify
)
from flask_session import Session
from werkzeug.middleware.proxy_fix import ProxyFix
import redis
import spotipy
from spotipy.oauth2 import SpotifyOAuth, CacheHandler

# ---- ML / data ----
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


# =============================================================================
# Environment
# =============================================================================
REQUIRED_ENV = [
    "REDIS_URL",
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "SPOTIPY_REDIRECT_URI",
]
missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
if missing:
    raise RuntimeError(f"Missing env var(s): {', '.join(missing)}")

REDIS_URL = os.environ["REDIS_URL"]
SPOTIPY_CLIENT_ID = os.environ["SPOTIPY_CLIENT_ID"]
SPOTIPY_CLIENT_SECRET = os.environ["SPOTIPY_CLIENT_SECRET"]
SPOTIPY_REDIRECT_URI = os.environ["SPOTIPY_REDIRECT_URI"]

# Scopes: tops + recently played + let us create private playlists
SPOTIFY_SCOPE = "user-top-read user-read-recently-played playlist-modify-private user-library-read"

ENV = os.environ.get("FLASK_ENV", "production").lower()
SECURE_COOKIES = ENV not in ("development", "dev", "local")


# =============================================================================
# Flask & Sessions (server-side) + Redis
# =============================================================================
redis_client = redis.from_url(REDIS_URL)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
    SESSION_TYPE="redis",
    SESSION_REDIS=redis_client,
    SESSION_USE_SIGNER=True,
    SESSION_PERMANENT=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    SESSION_COOKIE_SECURE=SECURE_COOKIES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

Session(app)


# =============================================================================
# Spotipy token cache per user (stored in Redis)
# =============================================================================
class RedisCache(CacheHandler):
    def __init__(self, redis_conn, sid):
        self.r = redis_conn
        self.key = f"spotipy_token:{sid}"

    def get_cached_token(self):
        raw = self.r.get(self.key)
        return json.loads(raw) if raw else None

    def save_token_to_cache(self, token_info):
        self.r.set(self.key, json.dumps(token_info))

    def delete(self):
        self.r.delete(self.key)


def _ensure_sid():
    if "sid" not in session:
        session["sid"] = secrets.token_urlsafe(16)
    return session["sid"]


def get_auth_manager():
    sid = _ensure_sid()
    cache_handler = RedisCache(redis_client, sid)
    return SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        cache_handler=cache_handler,
        show_dialog=True,  # force account chooser on shared machines
    )


def sp_client():
    """Spotipy client bound to the current user session (auto-refreshing)."""
    return spotipy.Spotify(auth_manager=get_auth_manager())


def logged_in():
    return get_auth_manager().get_cached_token() is not None


# =============================================================================
# Security headers (no caching of private pages)
# =============================================================================
@app.after_request
def add_security_headers(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if SECURE_COOKIES:
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return resp


# =============================================================================
# Small Redis JSON helpers
# =============================================================================
def rget_json(key):
    raw = redis_client.get(key)
    return json.loads(raw) if raw else None


def rset_json(key, obj, ex=None):
    redis_client.set(key, json.dumps(obj), ex=ex)


def current_user_id(sp=None):
    sp = sp or sp_client()
    return sp.me()["id"]


# =============================================================================
# Ingestion & feature caching
# =============================================================================
FEATURE_KEYS = [
    "danceability", "energy", "loudness", "speechiness", "acousticness",
    "instrumentalness", "liveness", "valence", "tempo"
]

def ensure_audio_features(sp, track_ids):
    """Cache Spotify audio features for track_ids not yet cached."""
    to_fetch = [tid for tid in track_ids if redis_client.get(f"track:{tid}:features") is None]
    for i in range(0, len(to_fetch), 100):
        chunk = to_fetch[i:i+100]
        feats = sp.audio_features(chunk)
        for f in feats:
            if f and f.get("id"):
                rset_json(f"track:{f['id']}:features", f)
        # back off a hair to be polite
        time.sleep(0.1)


def collect_user_corpus():
    """
    Gather a per-user corpus from:
      - Top tracks across short/medium/long windows (max 150)
      - Last ~50 recently played tracks
    Cache audio features for all unique tracks.
    """
    sp = sp_client()
    uid = current_user_id(sp)
    key_corpus = f"u:{uid}:tracks"

    all_ids = set()

    for rng in ["short_term", "medium_term", "long_term"]:
        items = sp.current_user_top_tracks(limit=50, time_range=rng)["items"]
        all_ids.update([t["id"] for t in items if t and t.get("id")])

    recent = sp.current_user_recently_played(limit=50)["items"]
    all_ids.update([it["track"]["id"] for it in recent if it.get("track") and it["track"].get("id")])

    all_ids = [tid for tid in all_ids if tid]  # clean
    ensure_audio_features(sp, all_ids)

    if all_ids:
        redis_client.sadd(key_corpus, *all_ids)

    return uid, len(all_ids)


# =============================================================================
# Clustering into ~4 taste genomes
# =============================================================================
def build_feature_df(track_ids):
    rows = []
    for tid in track_ids:
        f = rget_json(f"track:{tid}:features")
        if not f:
            continue
        row = {k: f.get(k) for k in FEATURE_KEYS}
        row["id"] = tid
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=["id"] + FEATURE_KEYS)
    return pd.DataFrame(rows)


def cluster_user(uid, k=4):
    corpus_key = f"u:{uid}:tracks"
    corpus_ids = [tid.decode() for tid in redis_client.smembers(corpus_key)]
    df = build_feature_df(corpus_ids)

    # need at least >k to make this meaningful; use 10 as a minimum threshold
    if len(df) < max(k, 10):
        return None

    X = df[FEATURE_KEYS].copy()

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X.values)

    km = KMeans(n_clusters=k, n_init="auto", random_state=42)
    labels = km.fit_predict(Xs)

    centroids = km.cluster_centers_
    cluster_obj = {
        "k": k,
        "feature_keys": FEATURE_KEYS,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "centroids_standard": centroids.tolist(),
        "labels": {df["id"].iloc[i]: int(labels[i]) for i in range(len(df))}
    }
    rset_json(f"u:{uid}:clusters", cluster_obj)
    return cluster_obj


# =============================================================================
# Seeding 4 playlists from centroids (private)
# =============================================================================
def unscale(centroid_std, mean, scale):
    c = np.array(centroid_std)
    return (c * np.array(scale)) + np.array(mean)


def centroid_to_targets(centroid_real, keys=FEATURE_KEYS):
    # Spotify param ranges (approx)
    clip = {
        "danceability": (0, 1), "energy": (0, 1), "speechiness": (0, 1),
        "acousticness": (0, 1), "instrumentalness": (0, 1), "liveness": (0, 1),
        "valence": (0, 1), "loudness": (-60, 0), "tempo": (0, 250)
    }
    targets = {}
    for k, v in zip(keys, centroid_real):
        lo, hi = clip[k]
        v = float(np.clip(v, lo, hi))
        targets[f"target_{k}"] = v
    return targets


def ensure_user_playlists(sp, uid, k):
    ids = []
    for i in range(k):
        key = f"u:{uid}:playlist:{i}"
        pid = redis_client.get(key)
        if pid:
            ids.append(pid.decode())
            continue
        pl = sp.user_playlist_create(
            uid,
            name=f"Genome {i+1}",
            public=False,
            description="Evolving mix based on your listening 'genome'"
        )
        redis_client.set(key, pl["id"])
        ids.append(pl["id"])
    return ids


# =============================================================================
# Routes (pages)
# =============================================================================
@app.route("/")
def index():
    return render_template("index.html", logged_in=logged_in())


@app.route("/login")
def login():
    _ensure_sid()  # create CSRF state + session id
    auth = get_auth_manager()
    return redirect(auth.get_authorize_url(state=session["sid"]))


@app.route("/callback")
def callback():
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state or state != session.get("sid"):
        abort(400, description="Invalid OAuth state")
    auth = get_auth_manager()
    # stores token in Redis via our cache handler
    auth.get_access_token(code)
    return redirect(url_for("me"))


@app.route("/me")
def me():
    if not logged_in():
        return redirect(url_for("index"))
    try:
        sp = sp_client()
        profile = sp.me()
        # short_term (~4 weeks), medium_term (~6 months), long_term (years)
        time_range = request.args.get("range", "medium_term")
        tracks = sp.current_user_top_tracks(limit=20, time_range=time_range)["items"]
    except spotipy.SpotifyException:
        # refresh failed or revoked; clear and re-login
        RedisCache(redis_client, session.get("sid", "none")).delete()
        return redirect(url_for("login"))
    return render_template("me.html", profile=profile, tracks=tracks, time_range=time_range)


@app.route("/logout")
def logout():
    # Remove only Spotify token + session cookie (privacy-first)
    RedisCache(redis_client, session.get("sid", "none")).delete()
    session.clear()
    return redirect(url_for("index"))


@app.route("/healthz")
def healthz():
    return "ok", 200


# =============================================================================
# Routes (API for GA pipeline)
# =============================================================================
@app.route("/bootstrap")
def bootstrap():
    """Collect corpus + cache audio features for the current user."""
    if not logged_in():
        return abort(401)
    uid, n = collect_user_corpus()
    return jsonify({"user": uid, "tracks_cached": n})


@app.route("/cluster")
def cluster_route():
    """Cluster the user's corpus into 4 'genomes' and store centroids."""
    if not logged_in():
        return abort(401)
    sp = sp_client()
    uid = current_user_id(sp)
    obj = cluster_user(uid, k=4)
    if not obj:
        return jsonify({"status": "need_more_data"}), 200
    return jsonify({"status": "ok", "clusters": obj})


@app.route("/seed_playlists")
def seed_playlists():
    """Create/refresh 4 private playlists using centroid → recommendations."""
    if not logged_in():
        return abort(401)

    sp = sp_client()
    uid = current_user_id(sp)
    clusters = rget_json(f"u:{uid}:clusters")
    if not clusters:
        return abort(400, "Run /cluster first")

    k = clusters["k"]
    mean = clusters["scaler_mean"]
    scale = clusters["scaler_scale"]
    cents_std = clusters["centroids_standard"]

    playlist_ids = ensure_user_playlists(sp, uid, k)

    # Use user's top tracks & top artists as seeds to keep it personal
    try:
        tops_tracks = sp.current_user_top_tracks(limit=5, time_range="short_term")["items"]
    except spotipy.SpotifyException:
        tops_tracks = []

    try:
        tops_artists = sp.current_user_top_artists(limit=5, time_range="short_term")["items"]
    except Exception:
        tops_artists = []

    seed_tracks_all = [t["id"] for t in tops_tracks if t.get("id")]
    seed_artists_all = [a["id"] for a in tops_artists if a.get("id")]

    for i in range(k):
        real = unscale(cents_std[i], mean, scale)
        targets = centroid_to_targets(real)

        # up to 5 seeds total across tracks+artists+genres
        seed_tracks = seed_tracks_all[:3]
        seed_artists = seed_artists_all[:2] if not seed_tracks else seed_artists_all[:(5 - len(seed_tracks))]

        # Fallback: if no seeds, grab a couple from corpus
        if not seed_tracks and not seed_artists:
            corpus_ids = [tid.decode() for tid in redis_client.smembers(f"u:{uid}:tracks")]
            seed_tracks = corpus_ids[:3]

        rec = sp.recommendations(
            seed_tracks=seed_tracks or None,
            seed_artists=seed_artists or None,
            limit=50,
            **targets
        )
        rec_ids = [t["id"] for t in rec["tracks"] if t.get("id")]

        # Replace playlist contents (keep it deterministic per seed run)
        if rec_ids:
            sp.playlist_replace_items(playlist_ids[i], rec_ids[:30])

        # tiny pause to be kind to rate limits
        time.sleep(0.1)

    return jsonify({"status": "ok", "playlists": playlist_ids})


# =============================================================================
# Entrypoint
# =============================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
