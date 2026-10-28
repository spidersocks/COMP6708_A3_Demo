# Agent handoff — COMP6708 A3 BrightNest → developer-site

## Read this first

1. **[`docs/DEVELOPER_SITE_INTEGRATION.md`](docs/DEVELOPER_SITE_INTEGRATION.md)** — full step-by-step for Cursor agents  
2. **[`integration/developer-site/`](integration/developer-site/)** — ready-to-copy files + snippets  

## One-sentence goal

Mount the BrightNest chat UI on **seanfontaine.dev/COMP6708_A3_Demo** (Vercel MPA + rewrite), calling Alibaba SAE API `http://47.242.157.72:8000` (update if EIP changed).

## Quick copy map

From this repo’s `integration/developer-site/` into `developer-site/frontend/tailwindcss4/`:

```
comp6708-a3-demo.html          → ./comp6708-a3-demo.html
src/apps/BrightNestBot/        → ./src/apps/BrightNestBot/
public/projects/brightnest/    → ./public/projects/brightnest/
```

Then merge:

- `snippets/vite.config.fragment.js` → `vite.config.js` input  
- `snippets/vercel.json.rewrites.json` → `vercel.json` rewrites array  
- `snippets/env.example` → `.env.production` / Vercel env  
- optional `snippets/App.jsx.route.fragment.jsx` → SPA routes  

## Pattern to mirror

Existing **Little Scholars** bot: dedicated HTML entry + `vite` MPA input + `vercel.json` rewrite. Do the same; do **not** set global Vite `base` to `/COMP6708_A3_Demo/`.

## Backend (already done — leave alone)

- SAE: `brightnext-api-2` @ `http://47.242.157.72:8000`  
- RAG + DashScope native multimodal generation for `qwen3.8-flash`  
- Set `CORS_ORIGINS=https://seanfontaine.dev,http://localhost:5173`  

## Alibaba cleanup after FE is on developer-site

**Delete/stop:** OSS `brightnest-bucket`; SAE `brightnest-web` if created; unused EIPs.  
**Keep:** SAE API app + its EIP + working ACR `brightnest-api` image + DashScope HK workspace/key.

## Mixed content

HTTPS site + HTTP API may block browser fetches. Prefer HTTPS on SAE or a Vercel proxy before the live demo.
