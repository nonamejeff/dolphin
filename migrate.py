# migrate.py
import os, psycopg
SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS isrc_feature (
  isrc              text        NOT NULL,
  feature_version   text        NOT NULL,
  extractor_version text        NOT NULL,
  vec               vector(62)  NOT NULL,
  feats             jsonb       NOT NULL,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (isrc, feature_version)
);

CREATE TABLE IF NOT EXISTS track_map (
  spotify_track_id  text PRIMARY KEY,
  isrc              text,
  title             text,
  artist            text
);
CREATE INDEX IF NOT EXISTS track_map_isrc_idx ON track_map (isrc);

CREATE TABLE IF NOT EXISTS user_used (
  user_id           text        NOT NULL,
  spotify_track_id  text        NOT NULL,
  used_at           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, spotify_track_id)
);

DROP INDEX IF EXISTS isrc_feature_vec_idx;
CREATE INDEX isrc_feature_vec_idx
  ON isrc_feature
  USING ivfflat (vec vector_l2_ops)
  WITH (lists = 200);
"""
conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
with conn.cursor() as cur:
    cur.execute(SQL)
print("Migration complete.")
