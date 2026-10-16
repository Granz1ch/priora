# Альтернатива runtime: python — любой Docker-хостинг (Render runtime: docker, Fly.io, VPS).
FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# DB_PATH лучше вынести на том: -v simdata:/var/data -e DB_PATH=/var/data/simbot.sqlite3
CMD ["python", "bot.py"]
