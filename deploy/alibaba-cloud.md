# Alibaba Cloud deployment guide (BrightNest COMP6708)

This demo is designed to run on Alibaba Cloud with **DashScope (Qwen)** for embeddings + generation.

## 1. DashScope (Model Studio)

1. Open [Alibaba Cloud Model Studio / DashScope](https://dashscope.console.aliyun.com/).
2. Create an API key (same region as the base URL).
3. Set on the backend (native DashScope `/api/v1`, via official `dashscope` SDK — not OpenAI compatible-mode):
   - `DASHSCOPE_API_KEY=sk-...`
   - `LLM_MODEL=qwen-plus` (or your Model Studio chat model id, e.g. `qwen-flash`)
   - `EMBEDDING_MODEL=text-embedding-v4` (or `text-embedding-v3`)
   - `DASHSCOPE_BASE_URL` — native API root, for example:
     - HK workspace: `https://<WorkspaceId>.cn-hongkong.maas.aliyuncs.com/api/v1`
     - HK shared: `https://cn-hongkong.dashscope.aliyuncs.com/api/v1`
     - Beijing shared: `https://dashscope.aliyuncs.com/api/v1`

Without a key, the backend still runs in **local fallback** mode (hash embeddings + extractive answers) so the UI remains demoable offline.

## 2. Backend — SAE or ECS / ACK

Setting env vars in a local `.env` is **not** enough for Step 2. You must: **build → push to ACR → run the container on Alibaba → set the same env vars on that cloud service**.

### 2a. Build the image (local)

From repo root (a `.dockerignore` excludes `.venv` / `node_modules` so the context stays small):

```bash
docker build -f deploy/Dockerfile.backend -t brightnest-api:latest .
```

Smoke-test locally before pushing:

```bash
docker run --rm -p 8000:8000 ^
  -e DASHSCOPE_API_KEY=sk-... ^
  -e CORS_ORIGINS=* ^
  -e CONTENT_DIR=/app/content ^
  -e CHROMA_DIR=/app/data/chroma ^
  brightnest-api:latest
```

Then `GET http://localhost:8000/healthz`.

### 2b. Push to Alibaba Container Registry (ACR)

1. Console → **Container Registry** → create a personal/enterprise instance (or use the default registry) in your preferred region (e.g. `cn-hongkong` or `cn-hangzhou`).
2. Create a namespace + repository named e.g. `brightnest-api`.
3. Log in and push (replace region/namespace; password is the ACR registry password, not your Aliyun login):

```bash
docker login --username=<acr-username> registry.<region>.aliyuncs.com
docker tag brightnest-api:latest registry.<region>.aliyuncs.com/<namespace>/brightnest-api:latest
docker push registry.<region>.aliyuncs.com/<namespace>/brightnest-api:latest
```

### 2c. Deploy the container (pick one)

**Option A — SAE (recommended for the class demo)**

1. Console → **Serverless App Engine (SAE)** → create application → **Image** deploy.
2. Select the ACR image from 2b.
3. Port: **8000**.
4. Health check path: `/healthz`.
5. In **Environment variables**, set the table below (these are SAE env vars, not your laptop `.env`).
6. Add a small persistent NAS/volume mounted at `/app/data` so Chroma + bookings survive restarts.
7. Enable public access / bind a domain → note the public URL (this becomes `VITE_API_BASE_URL` for the frontend).

**Option B — ECS**

1. Create an ECS instance with Docker installed (same region as ACR if possible).
2. `docker login` + `docker pull` the ACR image.
3. Run with `-p 8000:8000`, the env vars below, and a host bind mount for `/app/data`.
4. Open security-group TCP **8000** (or put Nginx/SLB in front on 443).

**Option C — ACK** — only if you already have a cluster; same image + env + volume pattern via Deployment/Service.

### Required env vars (on SAE / ECS / ACK — not only local)

| Variable | Example |
|----------|---------|
| `DASHSCOPE_API_KEY` | `sk-...` |
| `CORS_ORIGINS` | `https://your-oss-cdn-domain` (or `*` until FE is live) |
| `CONTENT_DIR` | `/app/content` |
| `CHROMA_DIR` | `/app/data/chroma` |
| `DASHSCOPE_BASE_URL` | optional intl endpoint if needed |
| `LLM_MODEL` | `qwen-plus` |
| `EMBEDDING_MODEL` | `text-embedding-v3` |

Health check: `GET /healthz`

Suggested region: **cn-hongkong** when available on your account; otherwise the nearest region that can reach DashScope.

After the app is up, optionally warm the index: `POST https://<api-host>/admin/reindex`.

## 3. Frontend — OSS + CDN

1. Build with the public API URL:

```bash
cd frontend
VITE_API_BASE_URL=https://api.your-domain.com npm run build
```

2. Upload `frontend/dist/**` to an **OSS** bucket configured for static website hosting.
3. Attach **CDN** domain + HTTPS certificate.
4. Ensure backend `CORS_ORIGINS` includes the CDN origin.

Alternatively use the frontend container:

```bash
docker build -f deploy/Dockerfile.frontend \
  --build-arg VITE_API_BASE_URL=https://api.your-domain.com \
  -t brightnest-web:latest .
```

## 4. Redeploy

See **[REDEPLOY.md](./REDEPLOY.md)** — stop first, then deploy (`.\deploy\redeploy.ps1 -Tag vN`).

## 5. Post-deploy checklist

- [ ] `GET https://api.../healthz` → `{ "ok": true }`
- [ ] `POST /chat` with `{ "message": "opening hours" }` returns hours
- [ ] Ask a KB question and expand **Sources used** in the UI
- [ ] `GET /availability?course_key=trial` returns slots; Book creates a booking id
- [ ] Re-ingest after KB edits: `POST /admin/reindex`

## 6. Cost / demo tips

- Prefer `qwen-plus` over `qwen-max` for the live demo to reduce latency/cost.
- Pre-warm the SAE instance a few minutes before the presentation.
- Keep a laptop localhost fallback (`npm run dev` + `uvicorn`) if campus Wi-Fi blocks cloud endpoints.
