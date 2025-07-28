from flask import Flask, request, redirect, session, url_for, render_template
from spotipy.oauth2 import SpotifyOAuth
import spotipy
import os

# === Initialize App ===
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev")

# === Force HTTPS in production ===
if os.environ.get("FLASK_ENV") != "development":
    from flask_sslify import SSLify
    sslify = SSLify(app)

# === Spotify OAuth Setup ===
redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI", "https://dolphin-audio.com/callback")
print(f"⚙️ Using redirect URI: {redirect_uri}")
sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=redirect_uri,
    scope="user-read-private user-read-email user-top-read",
    show_dialog=True
)

# === Home route with version ===
@app.route("/")
def index():
    return render_template("index.html", version="v1.4")

# === Login ===
@app.route("/login")
def login():
    auth_url = sp_oauth.get_authorize_url()
    print("🔗 Spotify auth URL:", auth_url)
    return redirect(auth_url)

# === OAuth Callback ===
@app.route("/callback")
def callback():
    print("🔁 Callback hit. Raw query string:", request.query_string.decode())
    print("🔍 request.args:", request.args)

    code = request.args.get("code")
    print("📥 Code received from Spotify:", code)

    if not code:
        return "❌ Missing authorization code from Spotify", 400

    try:
        token_info = sp_oauth.get_access_token(code)
        print("✅ Access token received:", token_info)
    except Exception as e:
        print("❌ Token exchange error:", str(e))
        return "❌ Spotify token exchange failed", 500

    session["token_info"] = token_info
    return redirect(url_for("profile"))

# === Profile & Genome ===
@app.route("/profile")
def profile():
    token_info = session.get("token_info")
    if not token_info:
        return redirect(url_for("login"))

    # Refresh expired tokens
    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
        session["token_info"] = token_info

    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()

    try:
        top_tracks = sp.current_user_top_tracks(limit=50, time_range="long_term")
        track_ids = [track["id"] for track in top_tracks["items"]]
        features = []

        for i in range(0, len(track_ids), 50):
            chunk = track_ids[i:i+50]
            try:
                chunk_features = sp.audio_features(chunk)
                valid_features = [f for f in chunk_features if f is not None]
                features.extend(valid_features)
            except spotipy.SpotifyException as e:
                print(f"⚠️ Failed chunk: {e}")
                if e.http_status == 403:
                    session.pop("token_info", None)
                    return redirect(url_for("login"))

    except spotipy.SpotifyException as e:
        print(f"Spotify API error: {e}")
        if e.http_status == 403:
            session.pop("token_info", None)
            return redirect(url_for("login"))
        return f"Spotify API error: {e}", e.http_status

    # Compute genome
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


