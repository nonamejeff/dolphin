import os, json, secrets, time, random, string, math
from datetime import timedelta

from flask import Flask, session, redirect, request, url_for, render_template, abort, jsonify
from flask_session import Session
from werkzeug.middleware.proxy_fix import ProxyFix
import redis, requests, spotipy
from spotipy.oauth2 import SpotifyOAuth, CacheHandler
import psycopg

# ========= App/session/redis =========
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
    SESSION_TYPE="redis",
    SESSION_REDIS=redis.from_url(os.environ["REDIS_URL"]),
    SESSION_USE_SIGNER=True,
    SESSION_PERMANENT=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
Session(app)
r = app.config["SESSION_REDIS"]

# ========= Spotify auth =========
for key in ["SPOTIPY_CLIENT_ID","SPOTIPY_CLIENT_SECRET","SPOTIPY_REDIRECT_URI","DATABASE_URL"]:
    if not os.environ.get(key):
        raise RuntimeError(f"Missing env var: {key}")

SPOTIFY_SCOPE = "user-top-read user-read-recently-played playlist-modify-private user-library-read"

class RedisCache(CacheHandler):
    def __init__(self, redis_conn, sid): self.r, self.key = redis_conn, f"spotipy_token:{sid}"
    def get_cached_token(self): raw=self.r.get(self.key); return json.loads(raw) if raw else None
    def save_token_to_cache(self, token): self.r.set(self.key, json.dumps(token))
    def delete(self): self.r.delete(self.key)

def _sid(): 
    if "sid" not in session: session["sid"]=secrets.token_urlsafe(16)
    return session["sid"]

def _auth():
    return SpotifyOAuth(
        client_id=os.environ["SPOTIPY_CLIENT_ID"],
        client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
        redirect_uri=os.environ["SPOTIPY_REDIRECT_URI"],
        scope=SPOTIFY_SCOPE,
        cache_handler=RedisCache(r, _sid()),
        show_dialog=True,
    )

def sp_client(): return spotipy.Spotify(auth_manager=_auth())
def logged_in(): return _auth().get_cached_token() is not None

@app.after_request
def _sec(resp):
    resp.headers["Cache-Control"]="no-store"; resp.headers["Pragma"]="no-cache"
    resp.headers["X-Frame-Options"]="DENY"; resp.headers["X-Content-Type-Options"]="nosniff"
    resp.headers["Referrer-Policy"]="same-origin"
    resp.headers["Strict-Transport-Security"]="max-age=63072000; includeSubDomains; preload"
    return resp

# ========= Postgres =========
def pg():
    # psycopg3 auto-reconnect is simple; keep a single global connection
    if not hasattr(app, "_pg"):
        app._pg = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    return app._pg

def vec_literal(arr):
    # pgvector textual input
    return "[" + ",".join(f"{float(x):.6f}" for x in arr) + "]"

def upsert_track_map(spid, isrc, title, artist):
    with pg().cursor() as cur:
        cur.execute("""
            INSERT INTO track_map (spotify_track_id, isrc, title, artist)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (spotify_track_id) DO UPDATE SET
              isrc = EXCLUDED.isrc, title = EXCLUDED.title, artist = EXCLUDED.artist;
        """, (spid, isrc, title, artist))

def nearest_tracks(qvec, uid, limit=120, feature_version="essentia_v1"):
    with pg().cursor() as cur:
        cur.execute(f"""
            WITH nn AS (
              SELECT isrc
              FROM isrc_feature
              WHERE feature_version = %s
              ORDER BY vec <-> %s
              LIMIT 400
            )
            SELECT tm.spotify_track_id
            FROM nn
            JOIN track_map tm USING (isrc)
            LEFT JOIN user_used uu
              ON uu.user_id = %s AND uu.spotify_track_id = tm.spotify_track_id
            WHERE uu.spotify_track_id IS NULL
            LIMIT %s;
        """, (feature_version, vec_literal(qvec), uid, limit))
        return [row[0] for row in cur.fetchall()]

# ========= Pages =========
@app.route("/")
def index(): return render_template("index.html", logged_in=logged_in())

@app.route("/ga")
def ga():
    if not logged_in(): return redirect(url_for("index"))
    return render_template("ga.html")

@app.route("/login")
def login():
    _sid(); return redirect(_auth().get_authorize_url(state=session["sid"]))

@app.route("/callback")
def callback():
    code, state = request.args.get("code"), request.args.get("state")
    if not code or state != session.get("sid"): abort(400, "Invalid OAuth state")
    _auth().get_access_token(code); return redirect(url_for("me"))

@app.route("/me")
def me():
    if not logged_in(): return redirect(url_for("index"))
    sp = sp_client(); profile = sp.me()
    rng = request.args.get("range","medium_term")
    tracks = sp.current_user_top_tracks(limit=20, time_range=rng)["items"]
    return render_template("me.html", profile=profile, tracks=tracks, time_range=rng)

@app.route("/logout")
def logout():
    RedisCache(r, session.get("sid","none")).delete(); session.clear()
    return redirect(url_for("index"))

@app.route("/debug_token")
def debug_token():
    if not logged_in(): return abort(401)
    am = _auth(); tok = am.get_cached_token() or {}
    me = sp_client().me()
    return jsonify({"user_id":me.get("id"), "scopes":tok.get("scope"),
                    "token_type":tok.get("token_type"), "expires_at":tok.get("expires_at")})

# ========= Corpus/cluster placeholders (unchanged UI) =========
# You already have /bootstrap and /cluster in earlier versions.
# Keep using those to label your user's own tracks into k=4 clusters.
# Here we only need centroids (Essentia space). We'll compute on demand
# from tracks that have features (via ISRC in Postgres).

# ---- Feature vector assembler (matches worker layout: 62 dims) ----
KEY_IDX = {"C":0,"C#":1,"D":2,"D#":3,"E":4,"F":5,"F#":6,"G":7,"G#":8,"A":9,"A#":10,"B":11}
def key_onehot(k, scale):
    vec=[0]*24; idx=KEY_IDX.get((k or "").upper()); mj=0 if (scale or "").lower()=="major" else 1
    if idx is not None: vec[idx*2 + mj] = 1
    return vec

def assemble_vec(feats):
    # feats is the JSON we store in isrc_feature.feats
    v=[]
    v.append(float(feats.get("bpm",0.0)))
    v.append(float(feats.get("onset_rate",0.0)))
    v.extend(key_onehot(feats.get("key_key"), feats.get("key_scale")))
    v.extend(feats.get("tonnetz_mean",[0.0]*6))
    v.extend(feats.get("hpcp_mean",[0.0]*12))
    v.extend(feats.get("mfcc_mean",[0.0]*13))
    v.append(float(feats.get("spectral_centroid_mean",0.0)))
    v.append(float(feats.get("spectral_flatness_mean",0.0)))
    v.append(float(feats.get("loudness_integrated",0.0)))
    v.append(float(feats.get("valence_pred",0.5)))  # placeholder if you add DEAM
    v.append(float(feats.get("arousal_pred",0.5)))
    return v

def centroid_for_cluster(uid, cluster_id):
    # gather user's labeled tracks from Redis (from your /cluster step)
    labels = json.loads(r.get(f"u:{uid}:clusters:labels") or b"{}")  # {spotify_tid: label}
    tids = [tid for tid, lab in labels.items() if int(lab)==int(cluster_id)]
    if not tids: return None

    # map to ISRC, fetch feats from Postgres
    vecs=[]
    with pg().cursor() as cur:
        for i in range(0,len(tids),100):
            chunk = tids[i:i+100]
            cur.execute("SELECT spotify_track_id,isrc FROM track_map WHERE spotify_track_id = ANY(%s)", (chunk,))
            mp = {row[0]:row[1] for row in cur.fetchall()}
            isrcs = [mp.get(t) for t in chunk if mp.get(t)]
            if not isrcs: continue
            cur.execute("""
                SELECT isrc, feats FROM isrc_feature 
                WHERE isrc = ANY(%s) AND feature_version='essentia_v1'
            """, (isrcs,))
            for _, feats in cur.fetchall():
                vecs.append(assemble_vec(feats))
    if not vecs: return None
    import numpy as np
    return np.mean(np.array(vecs, dtype=float), axis=0).tolist()

# ========= Harvesters (graph-free) =========
MB_HEADERS={"User-Agent":"genome-app/1.0 (you@domain.com)"}
def mb_random(batch=25):
    letter=random.choice(string.ascii_lowercase+string.digits)
    y0=random.randint(1960,2023); y1=min(y0+random.choice([1,2,5,10]),2024)
    q=f"recording:{letter} AND isrc:* AND date:[{y0} TO {y1}]"
    rqs=requests.get("https://musicbrainz.org/ws/2/recording",
        params={"query":q,"fmt":"json","limit":batch,"offset":random.randint(0,200)},
        headers=MB_HEADERS, timeout=20)
    rqs.raise_for_status(); return rqs.json().get("recordings",[]) or []

def mb_by_tags(tags, limit=50, offset=0, year_slice=None):
    tq=" AND ".join([f'tag:"{t}"' for t in tags]); dq=""
    if year_slice: dq=f" AND date:[{year_slice[0]} TO {year_slice[1]}]"
    q=f"{tq} AND isrc:*{dq}"
    rqs=requests.get("https://musicbrainz.org/ws/2/recording",
        params={"query":q,"fmt":"json","limit":limit,"offset":offset},
        headers=MB_HEADERS, timeout=20)
    rqs.raise_for_status(); return rqs.json().get("recordings",[]) or []

def spid_by_isrc(sp, isrc):
    try:
        res=sp.search(q=f"isrc:{isrc}", type="track", limit=1)
        it=res.get("tracks",{}).get("items",[])
        return it[0]["id"] if it else None
    except spotipy.SpotifyException: return None

def dz_preview(isrc):
    try:
        rqs=requests.get(f"https://api.deezer.com/track/isrc:{isrc}", timeout=15)
        if rqs.ok:
            j=rqs.json()
            if isinstance(j,dict) and j.get("preview"):
                return j["preview"]
    except Exception: pass
    return None

def itunes_preview(title, artist):
    try:
        term=requests.utils.quote(f"{title} {artist}")
        rqs=requests.get(f"https://itunes.apple.com/search?entity=song&limit=1&term={term}", timeout=15)
        if rqs.ok and rqs.json().get("results"):
            return rqs.json()["results"][0].get("previewUrl")
    except Exception: pass
    return None

def enqueue_job(spid, isrc, title, artist, previews):
    job={"spotify_id":spid,"isrc":isrc,"title":title,"artist":artist,"previews":previews}
    r.rpush("essentia:jobs", json.dumps(job))

@app.route("/harvest_random")
def harvest_random():
    if not logged_in(): return abort(401)
    sp=sp_client(); uid=sp.me()["id"]; want=int(request.args.get("count", 80))
    added=0; seen=set()
    while added<want:
        page=mb_random(25)
        for rec in page:
            title=rec.get("title") or ""
            ac=rec.get("artist-credit") or []
            artist=" ".join([a.get("name","") for a in ac]).strip()
            isrcs=[x.get("id") for x in (rec.get("isrcs") or []) if x.get("id")] or []
            for isrc in isrcs:
                if isrc in seen: continue
                seen.add(isrc)
                spid=spid_by_isrc(sp, isrc)
                if not spid: continue
                cat=f"u:{uid}:catalog"
                if r.sismember(cat, spid): continue
                previews=[]
                dp=dz_preview(isrc)
                if dp: previews.append(dp)
                if not previews:
                    ip=itunes_preview(title, artist)
                    if ip: previews.append(ip)
                if not previews: continue
                enqueue_job(spid, isrc, title, artist, previews)
                r.sadd(cat, spid)
                # persist map now so we don't re-fetch later
                upsert_track_map(spid, isrc, title, artist)
                added+=1
                if added>=want: break
        time.sleep(1.0)
    return jsonify({"status":"ok","added":added,"catalog_size":r.scard(f'u:{uid}:catalog')})

@app.route("/harvest_by_tags")
def harvest_by_tags():
    if not logged_in(): return abort(401)
    sp=sp_client(); uid=sp.me()["id"]
    tags=[t.strip() for t in (request.args.get("tags","").split(",")) if t.strip()]
    if not tags: return abort(400,"?tags=instrumental,piano")
    want=int(request.args.get("count",120)); added=0; offset=0
    y0=1960 + int(time.time())%40; y1=min(y0+10,2024)
    while added<want:
        page=mb_by_tags(tags, 50, offset, (y0,y1)); offset+=50
        if not page: break
        for rec in page:
            title=rec.get("title") or ""
            ac=rec.get("artist-credit") or []
            artist=" ".join([a.get("name","") for a in ac]).strip()
            isrcs=[x.get("id") for x in (rec.get("isrcs") or []) if x.get("id")] or []
            for isrc in isrcs:
                spid=spid_by_isrc(sp, isrc)
                if not spid: continue
                cat=f"u:{uid}:catalog"
                if r.sismember(cat, spid): continue
                previews=[]
                dp=dz_preview(isrc)
                if dp: previews.append(dp)
                if not previews:
                    ip=itunes_preview(title, artist)
                    if ip: previews.append(ip)
                if not previews: continue
                enqueue_job(spid, isrc, title, artist, previews)
                r.sadd(cat, spid)
                upsert_track_map(spid, isrc, title, artist)
                added+=1
                if added>=want: break
        time.sleep(1.0)
    return jsonify({"status":"ok","tags":tags,"added":added,"catalog_size":r.scard(f'u:{uid}:catalog')})

# ========= Enrich (enqueue for any missing features) =========
@app.route("/enrich_missing")
def enrich_missing():
    if not logged_in(): return abort(401)
    sp=sp_client(); uid=sp.me()["id"]; cat=f"u:{uid}:catalog"
    tids=[t.decode() for t in r.smembers(cat)]
    added=0
    for i in range(0,len(tids),50):
        chunk=tids[i:i+50]
        # get ISRC/title/artist from track_map; if missing, fetch from Spotify
        with pg().cursor() as cur:
            cur.execute("SELECT spotify_track_id,isrc,title,artist FROM track_map WHERE spotify_track_id = ANY(%s)", (chunk,))
            mp={row[0]:(row[1],row[2],row[3]) for row in cur.fetchall()}
        need=[t for t in chunk if t not in mp]
        if need:
            res=sp.tracks(need)
            for tr in res.get("tracks",[]):
                if not tr: continue
                isrc=(tr.get("external_ids") or {}).get("isrc")
                title=tr.get("name"); artist=", ".join([a["name"] for a in tr.get("artists",[])])
                if isrc: upsert_track_map(tr["id"], isrc, title, artist)
                mp[tr["id"]] = (isrc, title, artist)

        # skip those already in Postgres
        with pg().cursor() as cur:
            have=set()
            isrcs=[v[0] for v in mp.values() if v[0]]
            if isrcs:
                cur.execute("SELECT isrc FROM isrc_feature WHERE isrc = ANY(%s) AND feature_version='essentia_v1'", (isrcs,))
                have={row[0] for row in cur.fetchall()}
        for tid,(isrc,title,artist) in mp.items():
            if not isrc or isrc in have: continue
            prev=[]; d=dz_preview(isrc); 
            if d: prev.append(d)
            if not prev:
                ip=itunes_preview(title or "", artist or "")
                if ip: prev.append(ip)
            if not prev: continue
            enqueue_job(tid, isrc, title, artist, prev); added+=1
    return jsonify({"status":"ok","jobs_enqueued":added})

# ========= Populate (pgvector NN) =========
def softmax_sample(cands, k=30, tau=0.15):
    picks=[]; items=list(cands)
    while items and len(picks)<k:
        ws=[math.exp(-d/max(tau,1e-6)) for _,d in items]; s=sum(ws); ws=[w/s for w in ws]
        rnum=random.random(); cum=0.0
        for i,w in enumerate(ws):
            cum+=w
            if rnum<=cum:
                picks.append(items[i][0]); items.pop(i); break
    return picks

@app.route("/populate")
def populate():
    if not logged_in(): return abort(401)
    sp=sp_client(); uid=sp.me()["id"]

    # ensure we have 4 playlists (Genome 1..4)
    pls=[]
    for i in range(4):
        key=f"u:{uid}:playlist:{i}"
        pid=r.get(key)
        if not pid:
            pl=sp.user_playlist_create(uid, name=f"Genome {i+1}", public=False, description="Acoustic genome")
            pid=pl["id"]; r.set(key, pid)
        else:
            pid=pid.decode()
        pls.append(pid)

    # build centroids from user's cluster labels in Redis (expects you stored them after /cluster)
    for i in range(4):
        cent=centroid_for_cluster(uid, i)
        if not cent: continue
        # fetch nearest unseen tracks globally
        nns = nearest_tracks(cent, uid, limit=120)
        # estimate “distance” by re-querying Postgres with vec <->; simpler: treat listed order as close → assign pseudo distances
        cands=[(tid, idx+1) for idx, tid in enumerate(nns)]
        picks=softmax_sample(cands, k=30, tau=0.15) if cands else []
        if picks:
            sp.playlist_replace_items(pls[i], picks)
            r.set(f"u:{uid}:pl:{i}:last", json.dumps(picks))
            # mark used
            with pg().cursor() as cur:
                cur.executemany("INSERT INTO user_used (user_id, spotify_track_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                                [(uid,t) for t in picks])
        time.sleep(0.1)

    return jsonify({"status":"ok","playlists":pls})

# ========= Iterate (fitness) =========
@app.route("/iterate")
def iterate():
    if not logged_in(): return abort(401)
    sp=sp_client(); uid=sp.me()["id"]

    # recent plays (last ~50)
    played=set()
    try:
        rec=sp.current_user_recently_played(limit=50)
        for it in rec.get("items",[]): 
            if it.get("track") and it["track"].get("id"): played.add(it["track"]["id"])
    except spotipy.SpotifyException: pass

    # iterate per genome playlist
    for i in range(4):
        pid=r.get(f"u:{uid}:playlist:{i}")
        if not pid: continue
        pid=pid.decode()
        last=json.loads(r.get(f"u:{uid}:pl:{i}:last") or b"[]")
        if not last: continue

        # saved flags
        saved=[]
        for j in range(0,len(last),50):
            chunk=last[j:j+50]
            try: saved+=sp.current_user_saved_tracks_contains(chunk)
            except spotipy.SpotifyException: saved+=[False]*len(chunk)

        # current playlist to detect removals
        try:
            items = sp.playlist_items(pid, limit=100).get("items",[])
            now_ids = {t["track"]["id"] for t in items if t.get("track")}
        except Exception:
            now_ids=set(last)

        # score & elitism
        fitness={}
        for idx,tid in enumerate(last):
            s=0
            if saved[idx]: s+=3
            if tid in played: s+=2
            if tid not in now_ids: s-=4
            if (tid not in played) and (not saved[idx]): s-=2
            fitness[tid]=s

        keep_n=max(1,int(len(last)*0.7))
        elite=[t for t,_ in sorted(fitness.items(), key=lambda kv: kv[1], reverse=True)][:keep_n]

        # centroid (for jittered refill)
        cent=centroid_for_cluster(uid, i)
        nns=nearest_tracks(cent, uid, limit=180) if cent else []
        # avoid repeats
        nns=[t for t in nns if t not in elite]
        # pick farther ones for mutation
        cands=[(tid, idx+1) for idx,tid in enumerate(nns[30:])]  # skip the closest 30
        mutants=softmax_sample(cands, k=len(last)-len(elite), tau=0.25)

        newlist=(elite+mutants)[:30]
        if newlist:
            sp.playlist_replace_items(pid, newlist)
            r.set(f"u:{uid}:pl:{i}:last", json.dumps(newlist))
            with pg().cursor() as cur:
                cur.executemany("INSERT INTO user_used (user_id, spotify_track_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                                [(uid,t) for t in newlist])

    return jsonify({"status":"ok"})
