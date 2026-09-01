from __future__ import annotations
import argparse
import json
from pathlib import Path
from sqlalchemy import text
from monitor.config import Settings
from monitor.db import get_engine, get_source_health, load_countries, seed_sources

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("seed", "status", "check", "detach-source"))
    parser.add_argument("--event-id")
    parser.add_argument("--source-id")
    parser.add_argument("--reason", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    engine = get_engine(settings.database_url)
    with engine.begin() as conn:
        if args.command == "seed":
            load_countries(conn, Path(settings.data_dir) / "countries.geojson")
            # Admin seeding keeps Radar off. Worker enables only with a token.
            existing_radar = conn.execute(text("SELECT enabled FROM sources WHERE id='cloudflare_radar'")).scalar_one_or_none()
            seed_sources(conn, radar_enabled=bool(settings.radar_token) or bool(existing_radar))
            print("Załadowano lokalne granice państw i konfigurację sześciu źródeł.")
        elif args.command == "detach-source":
            from .identity_review import preview_split, split_source
            if not args.event_id or not args.source_id:
                raise SystemExit("Podaj --event-id i --source-id.")
            result = (split_source(conn, args.event_id, args.source_id, args.reason) if args.apply
                      else preview_split(conn, args.event_id, args.source_id))
            print(json.dumps(result, default=str, ensure_ascii=False))
        elif args.command == "status":
            counts = {table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                      for table in ("events", "observations", "event_revisions", "countries")}
            print(json.dumps({"counts": counts, "sources": get_source_health(conn)}, default=str, ensure_ascii=False))
        else:
            version = conn.execute(text("SELECT PostGIS_Full_Version()")).scalar_one()
            # Warsaw to Krakow is about 252 km; exercise geography units.
            near = conn.execute(text("""
                SELECT ST_DWithin(
                  ST_SetSRID(ST_MakePoint(21.0122,52.2297),4326)::geography,
                  ST_SetSRID(ST_MakePoint(19.9450,50.0647),4326)::geography,300000)
                AND NOT ST_DWithin(
                  ST_SetSRID(ST_MakePoint(21.0122,52.2297),4326)::geography,
                  ST_SetSRID(ST_MakePoint(19.9450,50.0647),4326)::geography,200000)
            """)).scalar_one()
            assert near, "PostGIS geography unit test failed"
            print(json.dumps({"postgis": version, "geography_metres": bool(near)}, ensure_ascii=False))
if __name__ == "__main__":
    main()
