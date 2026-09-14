# One-command local run for judges:  docker build -t closer . && docker run -p 8000:8000 closer
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --silent
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 CLOSER_MODE=demo CLOSER_DB=/data/closer.db
COPY requirements.txt pyproject.toml README.md ./
COPY backend ./backend
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .
COPY --from=ui /ui/dist ./frontend/dist
RUN mkdir -p /data
EXPOSE 8000
CMD ["closer", "serve", "--host", "0.0.0.0", "--port", "8000"]
