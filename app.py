from flask import Flask, request, redirect, session, url_for, render_template
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev")

# Spotify Auth setup
sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri="https://www.dolphin-audio.com/callback",
    scope="user-read-private user-read-email",
)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login")
def login():
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    token_info = sp_oauth.get_access_token(code)
    session["token_info"] = token_info
    return redirect(url_for("profile"))

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

    playlist_id = "37i9dQZF1DXc5e2bJhV6pu"  # Most Streamed Songs of All Time
    results = sp.playlist_items(playlist_id, limit=100)

    tracks = []
    for item in results.get("items", []):
        track = item.get("track")
        if not track:
            continue
        tracks.append(
            {
                "name": track.get("name"),
                "artist": ", ".join(a["name"] for a in track.get("artists", [])),
                "url": track["external_urls"]["spotify"],
            }
        )

    return render_template("top_tracks.html", tracks=tracks)
