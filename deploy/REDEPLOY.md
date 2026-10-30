# SAE redeploy (BrightNest API)

**Always stop the app before deploying a new image.**  
Deploying while it is still running (or mid-change-order) leaves SAE stuck.

```text
1. Abort any stuck change order (if LastChangeOrderRunning)
2. StopApplication → wait until STOPPED / 0 instances
3. docker build --provenance=false --sbom=false -f deploy/Dockerfile.backend -t …:vN .
4. docker push …:vN
5. DeployApplication with the new ImageUrl
6. Wait SUCCESS + RUNNING → GET /healthz → POST /admin/reindex
```

Or: `.\deploy\redeploy.ps1 -Tag v11` (does the above).

Frontend: push `developer-site` master (Vercel). API is this SAE flow only.
