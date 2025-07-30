from flask import Flask, request, redirect, session, url_for, render_template
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import time

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev")

# Spotify Auth setup
sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI"),
    scope="user-read-private user-read-email user-top-read",
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
    return redirect(url_for("profile"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/profile")
def profile():
    token_info = session.get("token_info", {})
    if not token_info:
        return redirect(url_for("login"))

    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()
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
