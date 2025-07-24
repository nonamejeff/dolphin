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

    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
        session["token_info"] = token_info

    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()
    
    # Get user's top tracks
    top_tracks = sp.current_user_top_tracks(limit=50, time_range="long_term")
    track_ids = [track["id"] for track in top_tracks["items"]]
    audio_features = sp.audio_features(track_ids)

    # Calculate average for full genome
    keys = [
        "danceability", "energy", "valence", "acousticness",
        "instrumentalness", "liveness", "speechiness",
        "tempo", "loudness"
    ]
    genome = {k: 0 for k in keys}
    count = 0

    for f in audio_features:
        if f:
            for k in keys:
                genome[k] += f[k]
            count += 1

    if count > 0:
        for k in keys:
            genome[k] = round(genome[k] / count, 3)

    return render_template("profile.html", user=user, genome=genome)


    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()
    return f"Logged in as: {user['display_name']} ({user['email']})"
