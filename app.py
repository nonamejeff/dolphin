from flask import Flask, redirect, request
from spotipy.oauth2 import SpotifyOAuth
import os

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev")

# Redirect URI must exactly match what's in your Spotify dashboard
redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI", "https://dolphin-audio.com/callback")
print("🔧 Redirect URI:", redirect_uri)

sp_oauth = SpotifyOAuth(
    client_id=os.environ.get("SPOTIPY_CLIENT_ID"),
    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=redirect_uri,
    scope="user-read-private user-read-email"
)

@app.route("/")
def home():
    return '<a href="/login">Login with Spotify</a>'

@app.route("/login")
def login():
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    print("🔁 Callback hit. Code from Spotify:", code)

    if not code:
        return "❌ Missing authorization code from Spotify", 400

    return f"✅ Callback success! Code: {code}"

# Don't include app.run() — Render handles that.


