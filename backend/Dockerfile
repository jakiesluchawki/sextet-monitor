FROM python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock && useradd -u 10001 --create-home monitor
COPY backend/ /app/
COPY data/ /app/data/
USER monitor
EXPOSE 8000
CMD ["uvicorn","monitor.api:app","--host","0.0.0.0","--port","8000","--no-access-log"]
