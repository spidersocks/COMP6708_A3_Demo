FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Content lives one level up in the monorepo; copy at build via compose context
# For standalone image builds from repo root, see ../deploy/Dockerfile

ENV CONTENT_DIR=/app/content
ENV CHROMA_DIR=/app/data/chroma
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
