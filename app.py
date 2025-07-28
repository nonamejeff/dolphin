from flask import (
    Flask,
    request,
    redirect,
    session,
    url_for,
    render_template,
    jsonify,
)
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import time
from uuid import uuid4

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev")


def make_sp_oauth(cache_path=None, state=None):
    """Return a SpotifyOAuth instance with optional cache path."""
    return SpotifyOAuth(
        client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
        client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
        redirect_uri="https://www.dolphin-audio.com/callback",
        scope="user-read-private user-read-email user-top-read",
        cache_path=cache_path,
        state=state,
    )

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login")
def login():
    cache_path = f"/tmp/.cache-{uuid4().hex}"
    session["cache_path"] = cache_path
    oauth = make_sp_oauth(cache_path=cache_path)
    auth_url = oauth.get_authorize_url()
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    cache_path = session.get("cache_path")
    oauth = make_sp_oauth(cache_path=cache_path)
    token_info = oauth.get_access_token(code)
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


@app.route("/logout")
def logout():
    cache_path = session.pop("cache_path", None)
    if cache_path and os.path.exists(cache_path):
        try:
            os.remove(cache_path)
        except OSError:
            pass
    session.clear()
    return redirect(url_for("index"))


@app.route("/top_songs_data")
def top_songs_data():
    token_info = session.get("token_info", {})
    if not token_info:
        return jsonify({"error": "not authenticated"}), 401

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

    return jsonify({"tracks": tracks})
