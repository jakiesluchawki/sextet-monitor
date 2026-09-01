FROM postgres:17.11-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-17-postgis-3=3.6.4+dfsg-2.pgdg12+1 postgresql-17-postgis-3-scripts=3.6.4+dfsg-2.pgdg12+1 && rm -rf /var/lib/apt/lists/*
COPY 10-roles.sh /docker-entrypoint-initdb.d/10-roles.sh
