from flask import Flask, request, redirect, session, url_for, render_template
from spotipy.oauth2 import SpotifyOAuth
import spotipy
import os

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev")

# Spotify OAuth Setup
sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri="https://www.dolphin-audio.com/callback",
    scope="user-read-private user-read-email user-top-read",
    show_dialog=True
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
    if not code:
        return "Missing code from Spotify", 400

    token_info = sp_oauth.get_access_token(code)
    print("🎯 Token scope granted:", token_info.get("scope"))  # <-- for debug
    session["token_info"] = token_info

    return redirect(url_for("profile"))

@app.route("/profile")
def profile():
    token_info = session.get("token_info")

    if not token_info:
        return redirect(url_for("login"))

    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
        session["token_info"] = token_info

    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()

    try:
        top_tracks = sp.current_user_top_tracks(limit=50, time_range="long_term")
        track_ids = [track["id"] for track in top_tracks["items"]]
        features = sp.audio_features(track_ids)
    except spotipy.SpotifyException as e:
        return f"Spotify API error: {e}", 500

    keys = [
        "danceability", "energy", "valence", "acousticness",
        "instrumentalness", "liveness", "speechiness",
        "tempo", "loudness"
    ]

    genome = {k: 0 for k in keys}
    count = 0

    for f in features:
        if f:
            for k in keys:
                genome[k] += f[k]
            count += 1

    if count > 0:
        for k in keys:
            genome[k] = round(genome[k] / count, 3)

    return render_template("profile.html", user=user, genome=genome)

if __name__ == "__main__":
    app.run(debug=True)
