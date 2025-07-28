from flask import Flask, request, redirect, session, url_for, render_template
from spotipy.oauth2 import SpotifyOAuth
import spotipy
import os

# === Initialize App ===
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev")

# === Spotify OAuth Setup ===
sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI", "http://localhost:5000/callback"),
    scope="user-read-private user-read-email user-top-read",
    show_dialog=True
)

# === Home route with version check ===
@app.route("/")
def index():
    return render_template("index.html", version="v1.4")

# === Spotify login flow ===
@app.route("/login")
def login():
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    print("🔁 Callback hit. Code received from Spotify:", code)

    if not code:
        return "❌ Missing authorization code from Spotify", 400

    try:
        token_info = sp_oauth.get_access_token(code)
        print("✅ Access token received:", token_info)
    except Exception as e:
        print("❌ Error exchanging code for token:", str(e))
        return "❌ Spotify token exchange failed", 500

    session["token_info"] = token_info
    return redirect(url_for("profile"))


# === Profile & Genome route ===
@app.route("/profile")
def profile():
    token_info = session.get("token_info")
    if not token_info:
        return redirect(url_for("login"))

    # Refresh expired tokens
    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
        session["token_info"] = token_info

    # Use Spotipy client
    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()

    try:
        top_tracks = sp.current_user_top_tracks(limit=50, time_range="long_term")
        track_ids = [track["id"] for track in top_tracks["items"]]
        features = []
        chunk_size = 50
        for i in range(0, len(track_ids), chunk_size):
            chunk = track_ids[i:i+chunk_size]
            try:
                chunk_features = sp.audio_features(chunk)
                # Filter out None responses (bad track IDs)
                valid_features = [f for f in chunk_features if f is not None]
                features.extend(valid_features)
            except spotipy.SpotifyException as e:
                print(f"⚠️  Chunk fetch failed: {e}")
                if e.http_status == 403:
                    session.pop("token_info", None)
                    return redirect(url_for("login"))


    except spotipy.SpotifyException as e:
        print(f"Spotify API error: {e}")
        if e.http_status == 403:
            session.pop("token_info", None)
            return redirect(url_for("login"))
        return f"Spotify API error: {e}", e.http_status

    # Calculate genome
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

# === Local Debug Run ===
if __name__ == "__main__":
    app.run(debug=True)
