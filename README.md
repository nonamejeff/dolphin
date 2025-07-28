# Dolphin Spotify Genome App

This Flask application lets users log in with Spotify and view a genome of
their top tracks.

## Setup

1. Create a Spotify developer application at [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/).
2. Add a redirect URI to that application. It must exactly match the
   `SPOTIPY_REDIRECT_URI` environment variable you set for this app.
3. Set the following environment variables when running the app:

   - `SPOTIPY_CLIENT_ID` – your Spotify client ID
   - `SPOTIPY_CLIENT_SECRET` – your Spotify client secret
   - `SPOTIPY_REDIRECT_URI` – the URL Spotify should redirect back to
     after authentication (e.g. `https://yourdomain.com/callback`)
   - `FLASK_SECRET_KEY` – any random secret string

If you encounter the error `INVALID_CLIENT: Invalid redirect URI`, the
redirect URI from the login request did not match any of the URIs listed in your
Spotify application. Ensure `SPOTIPY_REDIRECT_URI` matches exactly and that it is
added in the Spotify dashboard.

Run locally with:

```bash
pip install -r requirements.txt
python app.py
```

The app will listen on port 5000 by default.
