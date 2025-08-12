import os, json, secrets
from datetime import timedelta
from flask import Flask, session, redirect, request, url_for, render_template, abort
from flask_session import Session
from werkzeug.middleware.proxy_fix import ProxyFix
import redis
import spotipy
from spotipy.oauth2 import SpotifyOAuth, CacheHandler

# ---- Required env ----
for key in ["REDIS_URL", "SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"]:
    if not os.environ.get(key):
        raise RuntimeError(f"Missing env var: {key}")

REDIS_URL = os.environ["REDIS_URL"]
SPOTIPY_CLIENT_ID = os.environ["SPOTIPY_CLIENT_ID"]
SPOTIPY_CLIENT_SECRET = os.environ["SPOTIPY_CLIENT_SECRET"]
SPOTIPY_REDIRECT_URI = os.environ["SPOTIPY_REDIRECT_URI"]
SPOTIFY_SCOPE = "user-top-read"
ENV = os.environ.get("FLASK_ENV", "production").lower()
SECURE_COOKIES = ENV not in ("development", "dev", "local")

# ---- App & sessions ----
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

# ---- Spotipy token cache in Redis (per-user) ----
class RedisCache(CacheHandler):
    def __init__(self, redis_conn, sid):
        self.r = redis_conn
        self.key = f"spotipy_token:{sid}"

    def get_cached_token(self):
        data = self.r.get(self.key)
        return json.loads(data) if data else None

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
        show_dialog=True,   # force account chooser on shared machines
    )

def logged_in():
    return get_auth_manager().get_cached_token() is not None

# ---- Security headers / no caching of private pages ----
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

# ---- Routes ----
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
    auth.get_access_token(code)  # stores token in Redis via cache handler
    return redirect(url_for("me"))

@app.route("/me")
def me():
    if not logged_in():
        return redirect(url_for("index"))
    try:
        sp = spotipy.Spotify(auth_manager=get_auth_manager())
        profile = sp.me()
        time_range = request.args.get("range", "medium_term")  # short_term, medium_term, long_term
        tracks = sp.current_user_top_tracks(limit=20, time_range=time_range)["items"]
    except spotipy.SpotifyException:
        # refresh failed or revoked; clear and re-login
        RedisCache(redis_client, session.get("sid", "none")).delete()
        return redirect(url_for("login"))
    return render_template("me.html", profile=profile, tracks=tracks, time_range=time_range)

@app.route("/logout")
def logout():
    RedisCache(redis_client, session.get("sid", "none")).delete()
    session.clear()
    return redirect(url_for("index"))

@app.route("/healthz")
def healthz():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
