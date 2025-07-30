from flask import Flask, request, redirect, session, url_for, render_template
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import time
import secrets

app = Flask(__name__)
# Use a random secret if FLASK_SECRET_KEY is not provided. This prevents
# runtime errors when the environment variable is missing, but sessions will be
# reset on each restart unless a persistent secret is configured.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# Spotify Auth setup
sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI"),
    scope="user-read-private user-read-email user-top-read",
    cache_path=None,
    show_dialog=True,
)

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
    token_info = sp_oauth.get_access_token(code)
    session["token_info"] = token_info

    # Immediately fetch the user profile with the new token so the
    # session reflects the latest account that logged in. Storing this
    # separately ensures old data does not linger if a different user
    # authenticates afterwards.
    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()
    session["user_info"] = user
    session["spotify_id"] = user["id"]
    session.modified = True

    return redirect(url_for("profile"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/profile")
def profile():
    token_info = session.get("token_info")
    spotify_id = session.get("spotify_id")
    if not token_info or not spotify_id:
        return redirect(url_for("login"))

    sp = spotipy.Spotify(auth=token_info["access_token"])
    current_user = sp.current_user()

    if current_user.get("id") != spotify_id:
        session.clear()
        return redirect(url_for("login"))

    cached_user = session.get("user_info")
    if not cached_user or cached_user.get("id") != current_user.get("id"):
        session["user_info"] = current_user
        session.modified = True
        user = current_user
    else:
        user = cached_user

    return render_template("profile.html", user=user)


@app.route("/top_songs")
def top_songs():
    token_info = session.get("token_info", {})
    if not token_info:
        return redirect(url_for("login"))

    sp = spotipy.Spotify(auth=token_info["access_token"])

    tracks = []
    for offset in (0, 50):
        results = sp.current_user_top_tracks(
            limit=50, offset=offset, time_range="long_term"
        )
        for item in results.get("items", []):
            tracks.append(
                {
                    "name": item.get("name"),
                    "artist": ", ".join(a["name"] for a in item.get("artists", [])),
                    "url": item["external_urls"]["spotify"],
                }
            )
        if offset == 0:
            time.sleep(10)

    return render_template("top_tracks.html", tracks=tracks)

@app.route("/top_artists")
def top_artists():
    token_info = session.get("token_info", {})
    if not token_info:
        return redirect(url_for("login"))

    sp = spotipy.Spotify(auth=token_info["access_token"])

    artists = []
    for offset in (0, 50):
        results = sp.current_user_top_artists(
            limit=50, offset=offset, time_range="long_term"
        )
        for item in results.get("items", []):
            artists.append(
                {
                    "name": item.get("name"),
                    "url": item["external_urls"]["spotify"],
                }
            )
        if offset == 0:
            time.sleep(10)

    return {"artists": artists}
