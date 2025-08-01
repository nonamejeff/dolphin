from flask import Flask, request, redirect, session, url_for, render_template, jsonify
from flask_session import Session
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import time
import secrets
import redis

# === App Setup ===
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))
app.config.update(
    SESSION_TYPE="redis",
    SESSION_REDIS=redis.from_url(os.environ.get("REDIS_URL")),
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
Session(app)

# === Spotify OAuth setup ===
sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI"),
    scope="user-read-private user-read-email user-top-read",
    cache_path=None,
    show_dialog=True,
)

# === Retry wrapper for Spotify API calls ===
def retry_spotify_call(call, retries=3, delay=2):
    last_exception = None
    for attempt in range(retries):
        try:
            return call()
        except spotipy.SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get("Retry-After", delay)) if hasattr(e, "headers") else delay
                print(f"⚠️ Rate limited. Retrying after {retry_after}s...")
                time.sleep(retry_after)
                last_exception = e
            elif 500 <= e.http_status < 600:
                print(f"⚠️ Spotify {e.http_status} server error. Retrying in {delay}s (attempt {attempt + 1})...")
                time.sleep(delay)
                last_exception = e
            else:
                raise e
        except Exception as e:
            print(f"⚠️ Retryable error: {e}")
            time.sleep(delay)
            last_exception = e
    raise last_exception

# === Get Spotify Client (fresh every request) ===
def get_spotify_client():
    token_info = session.get("token_info")
    spotify_id = session.get("spotify_id")

    if not token_info or not spotify_id:
        print("⚠️ Missing token or spotify_id in session")
        return None, None

    try:
        sp = spotipy.Spotify(auth=token_info["access_token"])
        user = sp.current_user()
        if user.get("id") != spotify_id:
            print(f"⚠️ Mismatched user session: expected {spotify_id}, got {user.get('id')}")
            session.clear()
            return None, None
    except Exception as e:
        print("❌ Failed to verify user or token:", e)
        session.clear()
        return None, None

    # Refresh if expired
    if sp_oauth.is_token_expired(token_info):
        try:
            token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
            session["token_info"] = token_info
            session.modified = True
            sp = spotipy.Spotify(auth=token_info["access_token"])
        except Exception as e:
            print("❌ Token refresh failed:", e)
            session.clear()
            return None, None

    return sp, user

# === Routes ===

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login")
def login():
    session.clear()
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "❌ Missing authorization code from Spotify", 400

    try:
        token_info = sp_oauth.get_access_token(code)
    except Exception as e:
        print("❌ Token exchange failed:", e)
        return "❌ Spotify token exchange failed", 500

    try:
        sp = spotipy.Spotify(auth=token_info["access_token"])
        user = sp.current_user()
        print("✅ Logged in as:", user["id"])
    except Exception as e:
        print("❌ Failed to fetch user after login:", e)
        return "❌ Failed to verify Spotify user", 500

    session["token_info"] = token_info
    session["spotify_id"] = user["id"]
    session.modified = True
    time.sleep(0.1)

    return redirect(url_for("profile"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/clear_session")
def clear_session():
    session.clear()
    return "✅ Session cleared", 200

@app.route("/profile")
def profile():
    sp, user = get_spotify_client()
    if not sp:
        return redirect(url_for("login"))
    return render_template("profile.html", user=user)

@app.route("/top_songs")
def top_songs():
    sp, _ = get_spotify_client()
    if not sp:
        return redirect(url_for("login"))

    tracks = []
    for offset in (0, 50):
        time.sleep(1.5)
        results = retry_spotify_call(lambda: sp.current_user_top_tracks(limit=50, offset=offset, time_range="long_term"))
        for item in results.get("items", []):
            tracks.append({
                "name": item.get("name"),
                "artist": ", ".join(a["name"] for a in item.get("artists", [])),
                "url": item["external_urls"]["spotify"]
            })

    return render_template("top_tracks.html", tracks=tracks)

@app.route("/top_artists")
def top_artists():
    sp, _ = get_spotify_client()
    if not sp:
        return jsonify({"error": "Not authenticated"}), 401

    artists = []
    try:
        first_page = retry_spotify_call(lambda: sp.current_user_top_artists(limit=50, offset=0, time_range="long_term"))
        items = first_page.get("items", [])
        artists.extend([{
            "name": a["name"],
            "url": a["external_urls"]["spotify"]
        } for a in items])

        if first_page.get("total", 0) > len(items):
            time.sleep(1.5)
            second_page = retry_spotify_call(lambda: sp.current_user_top_artists(limit=50, offset=50, time_range="long_term"))
            for a in second_page.get("items", []):
                artists.append({
                    "name": a["name"],
                    "url": a["external_urls"]["spotify"]
                })
    except Exception as e:
        print(f"❌ Error fetching top artists: {e}")
        return jsonify({"error": "Failed to load top artists"}), 500

    return jsonify({"artists": artists})
