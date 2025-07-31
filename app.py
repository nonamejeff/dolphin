from flask import Flask, request, redirect, session, url_for, render_template
from flask_session import Session
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import redis
import secrets
import time

app = Flask(__name__)

# Secure session configuration
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))
app.config.update(
    SESSION_TYPE='redis',
    SESSION_PERMANENT=False,
    SESSION_USE_SIGNER=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_REDIS=redis.from_url(os.environ.get("REDIS_URL"))
)

Session(app)

# Spotify Auth setup
sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI"),
    scope="user-read-private user-read-email user-top-read",
    cache_path=None,
    show_dialog=True,
)

def get_spotify_client():
    token_info = session.get("token_info")
    spotify_id = session.get("spotify_id")

    if not token_info or not spotify_id:
        return None, None

    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
        session["token_info"] = token_info

    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()

    if user.get("id") != spotify_id:
        session.clear()
        return None, None

    return sp, user

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
        return "Missing authorization code", 400

    try:
        token_info = sp_oauth.get_access_token(code)
    except Exception as e:
        print("Token exchange failed:", str(e))
        return "Spotify token exchange failed", 500

    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()

    session["token_info"] = token_info
    session["spotify_id"] = user["id"]

    return redirect(url_for("profile"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

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
        results = sp.current_user_top_tracks(limit=50, offset=offset, time_range="long_term")
        for item in results.get("items", []):
            tracks.append({
                "name": item.get("name"),
                "artist": ", ".join(a["name"] for a in item.get("artists", [])),
                "url": item["external_urls"]["spotify"],
            })
        if offset == 0:
            time.sleep(10)

    return render_template("top_tracks.html", tracks=tracks)

@app.route("/top_artists")
def top_artists():
    sp, _ = get_spotify_client()
    if not sp:
        return redirect(url_for("login"))

    artists = []
    for offset in (0, 50):
        results = sp.current_user_top_artists(limit=50, offset=offset, time_range="long_term")
        for item in results.get("items", []):
            artists.append({
                "name": item.get("name"),
                "url": item["external_urls"]["spotify"],
            })
        if offset == 0:
            time.sleep(10)

    return {"artists": artists}
