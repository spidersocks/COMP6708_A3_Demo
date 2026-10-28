# Integrate BrightNest into `developer-site` at `/COMP6708_A3_Demo`

**Audience:** a Cursor agent (or human) on a machine that has both:

- this repo: `COMP6708_A3_Demo`
- the portfolio site: `developer-site` (Vite + React under `frontend/tailwindcss4`)

**Goal:** serve the BrightNest demo UI at:

`https://seanfontaine.dev/COMP6708_A3_Demo`

(or local `http://localhost:5173/COMP6708_A3_Demo`) talking to the Alibaba SAE API.

Do **not** modify the live Little Scholars tutoring backend (`unified_backend`).

---

## 0. Preconditions

1. Open a multi-root workspace with both repos, or work inside `developer-site` with this repo nearby.
2. Confirm Little Scholars pattern still exists (use as template):
   - `frontend/tailwindcss4/src/apps/LittleScholarsBot/`
   - `frontend/tailwindcss4/little-scholars-bot.html`
   - MPA entry in `vite.config.js`
   - rewrites in `vercel.json`
3. Alibaba SAE API is up, e.g. `http://47.242.157.72:8000/healthz` → `{"ok":true}`  
   (Update the URL if the EIP changed.)

---

## 1. Copy frontend app files into developer-site

From `COMP6708_A3_Demo` → `developer-site/frontend/tailwindcss4`:

| Source (this repo) | Destination |
|--------------------|-------------|
| `frontend/src/App.jsx` | `src/apps/BrightNestBot/BrightNestBotDemo.jsx` |
| `frontend/src/brightnest.css` | `src/apps/BrightNestBot/brightnest.css` |
| `frontend/public/logo.png` | `public/projects/brightnest/logo.png` |
| `frontend/public/icon.png` | `public/projects/brightnest/icon.png` |
| `frontend/public/send_icon.png` | `public/projects/brightnest/send_icon.png` (optional) |

Also create MPA HTML entry (new file):

`frontend/tailwindcss4/comp6708-a3-demo.html`

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <meta name="theme-color" content="#0F4C5C" />
    <link rel="icon" type="image/png" href="/projects/brightnest/icon.png" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Source+Sans+3:wght@400;500;600;700&display=swap"
      rel="stylesheet"
    />
    <title>BrightNest — COMP6708 A3 Demo</title>
  </head>
  <body>
    <div id="root"></div>
    <!-- Prefer a dedicated entry that mounts only BrightNest (see §2). -->
    <script type="module" src="/src/apps/BrightNestBot/main.jsx"></script>
  </body>
</html>
```

Create `src/apps/BrightNestBot/main.jsx`:

```jsx
import React from "react";
import { createRoot } from "react-dom/client";
import BrightNestBotDemo from "./BrightNestBotDemo.jsx";
import "./brightnest.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrightNestBotDemo />
  </React.StrictMode>
);
```

---

## 2. Patch `BrightNestBotDemo.jsx` for subpath + API

After copy, edit the demo component:

### 2a. API base URL

```js
const API_BASE = (
  import.meta.env.VITE_BRIGHTNEST_API_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  "http://47.242.157.72:8000"
).replace(/\/$/, "");
```

### 2b. Asset paths (must work under `/COMP6708_A3_Demo/`)

Replace absolute `/logo.png` with:

```js
const asset = (name) =>
  `${import.meta.env.BASE_URL}projects/brightnest/${name}`.replace(/\/{2,}/g, "/");
// usage:
<img src={asset("logo.png")} alt="BrightNest" ... />
```

If `BASE_URL` is `/COMP6708_A3_Demo/` and assets live at `/projects/brightnest/` (site root public/), use:

```js
const asset = (name) => `/projects/brightnest/${name}`;
```

**Preferred for this site:** put images in `public/projects/brightnest/` and reference `/projects/brightnest/logo.png` (site-root absolute), matching Little Scholars. Then you do **not** need Vite `base` for images.

### 2c. Import CSS

```js
import "./brightnest.css";
```

Remove any `from "./App"` coupling; default export the demo component.

---

## 3. Wire Vite MPA (`vite.config.js`)

Mirror the Little Scholars entry. Add to `build.rollupOptions.input`:

```js
comp6708A3Demo: path.resolve(__dirname, "comp6708-a3-demo.html"),
```

(Keep existing `littleScholarsBot` / main inputs.)

**Do not** set global `base: '/COMP6708_A3_Demo/'` on the whole developer-site — that would break the portfolio. Subpath is handled by Vercel rewrites → this HTML entry (same as Little Scholars).

---

## 4. Wire React Router (optional deep link from main SPA)

In `src/App.jsx`, if the main SPA lists project routes, add:

```jsx
import BrightNestBotDemo from "./apps/BrightNestBot/BrightNestBotDemo";
// ...
<Route path="/COMP6708_A3_Demo" element={<BrightNestBotDemo />} />
<Route path="/COMP6708_A3_Demo/*" element={<BrightNestBotDemo />} />
```

**Note:** Little Scholars often uses a **dedicated HTML entry + Vercel rewrite** rather than only the SPA router. Prefer that pattern for parity (see §5). If both exist, rewrite to the MPA HTML is authoritative on production.

---

## 5. Vercel rewrites (`vercel.json`)

Add (alongside existing little-scholars rules):

```json
{
  "source": "/COMP6708_A3_Demo",
  "destination": "/comp6708-a3-demo.html"
},
{
  "source": "/COMP6708_A3_Demo/:path*",
  "destination": "/comp6708-a3-demo.html"
}
```

Optional asset aliases (only if you want short paths):

```json
{
  "source": "/brightnest_logo.png",
  "destination": "/projects/brightnest/logo.png"
}
```

---

## 6. Env for production API

In `developer-site/frontend/tailwindcss4/.env.production` or Vercel project env:

```env
VITE_BRIGHTNEST_API_URL=http://47.242.157.72:8000
```

**Mixed content warning:** if `seanfontaine.dev` is **HTTPS** and the API is **HTTP**, browsers may block chat fetches. Mitigations (pick one):

1. Put HTTPS in front of SAE (CLB + certificate) and set `VITE_BRIGHTNEST_API_URL=https://…`
2. For classroom demo only, open an HTTP tunnel / temporary HTTP page (not ideal)
3. Proxy `/brightnest-api/*` through Vercel rewrites to the SAE host (Vercel server-side fetch avoids browser mixed-content)

Recommended long-term: **HTTPS on SAE** or **Vercel rewrite proxy**.

Example Vercel rewrite proxy (if you add a tiny serverless proxy later) — optional; not required for first local verify.

---

## 7. CORS on Alibaba SAE (`brightnext-api-2`)

Set environment variable:

```text
CORS_ORIGINS=https://seanfontaine.dev,http://localhost:5173
```

Or temporarily `*` for the live demo. Redeploy the SAE app after changing env.

---

## 8. Verify locally

```powershell
cd developer-site/frontend/tailwindcss4
npm install
npm run dev
```

Open:

- `http://localhost:5173/comp6708-a3-demo.html`  
  or after rewrites locally (if configured): `http://localhost:5173/COMP6708_A3_Demo`

Smoke tests:

1. Opening hours chip / message  
2. “How much is phonics?” → answer + **Sources used**  
3. Trial availability → **Book**  

---

## 9. Deploy developer-site

Commit only developer-site changes (and note API URL). Push so Vercel rebuilds.

Production URL:

`https://seanfontaine.dev/COMP6708_A3_Demo`

---

## 10. What NOT to do

- Do not set Vite `base: '/COMP6708_A3_Demo/'` on the **entire** developer-site build.
- Do not point this UI at production `unified-backend.fly.dev` (live tutoring).
- Do not commit Alibaba AccessKeys or DashScope keys into developer-site.
- Do not delete SAE API (`brightnext-api-2`) — that is the backend.

---

## Checklist for the agent

- [ ] Copied BrightNest sources + assets into `src/apps/BrightNestBot` + `public/projects/brightnest`
- [ ] Added `comp6708-a3-demo.html` + `main.jsx`
- [ ] Wired `vite.config.js` MPA input
- [ ] Added `vercel.json` rewrites for `/COMP6708_A3_Demo`
- [ ] Set `VITE_BRIGHTNEST_API_URL` to current SAE URL
- [ ] Updated SAE `CORS_ORIGINS` for `seanfontaine.dev`
- [ ] Local smoke test passed
- [ ] Deployed developer-site; production URL works

---

## Reference: Alibaba cleanup (after FE is on seanfontaine.dev)

**Keep**

- SAE app **brightnext-api-2** (API + RAG)
- Its EIP (outbound DashScope + inbound `:8000`)
- ACR images for `brightnest-api` (optional; keep latest working tag)
- DashScope / Model Studio keys & HK workspace that works for embeddings

**Safe to delete / stop**

- OSS bucket **brightnest-bucket** (unused if FE is on developer-site)
- SAE app **brightnest-web** (if created; FE not needed on SAE)
- Extra unused EIPs bound to deleted apps
- Old ACR tags you no longer deploy (optional hygiene)

**Do not delete**

- Live tutoring / `unified_backend` / production WhatsApp stack
