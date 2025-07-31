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
   - `FLASK_SECRET_KEY` – any random secret string. Optional, but providing one
     ensures sessions persist across restarts. If omitted, the app generates a
     temporary secret at runtime.

The login flow now forces Spotify to display the account selection dialog
every time. This allows you to easily switch between Spotify accounts after
logging out of the app.

Token caching is also disabled so each login retrieves a fresh access token
instead of reusing the one stored on disk. This prevents the previous user's
profile from being displayed when switching accounts.

No tokens or user data are stored globally. Everything is kept in the Flask
session so each user's information is isolated from others.

Each login stores the Spotify user's ID in the session and all profile
requests verify that the ID matches the token being used. This ensures a
completely isolated session for every user.

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
