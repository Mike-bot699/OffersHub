# Single image: builds the UI, serves it from the API. Configure with EGI_* environment variables.
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/ ./
COPY --from=ui /ui/dist /app/frontend/dist
ENV EGI_ENV=hosted EGI_FRONTEND_DIST=/app/frontend/dist
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-dev python -m app.cli migrate && uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port 8000"]
