<#
.SYNOPSIS
  Safe BrightNest SAE redeploy: abort → STOP → build/push → deploy → smoke.

.PARAMETER Tag
  Image tag to build and deploy (e.g. v11). Required.

.PARAMETER SkipBuild
  Only stop + deploy an image that is already in ACR.

.PARAMETER SkipSmoke
  Skip healthz / reindex / chat checks after deploy.

.EXAMPLE
  .\deploy\redeploy.ps1 -Tag v11
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Tag,

  [switch]$SkipBuild,
  [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Load-DotEnv {
  $path = Join-Path $Root ".env"
  if (-not (Test-Path $path)) {
    throw "Missing $path — copy .env.example and fill Alibaba / ACR / SAE values."
  }
  Get-Content $path | ForEach-Object {
    $t = $_.Trim()
    if ($t -match "^(#|$)" ) { return }
    if ($t -match "^([^=]+)=(.*)$") {
      Set-Item -Path "Env:$($Matches[1].Trim())" -Value $Matches[2].Trim()
    }
  }
}

function Require-Env([string[]]$names) {
  foreach ($n in $names) {
    if (-not (Get-Item "Env:$n" -ErrorAction SilentlyContinue)?.Value) {
      throw "Missing env var $n (set in repo-root .env)"
    }
  }
}

function Get-AppStatusRaw {
  aliyun sae DescribeApplicationStatus `
    --AppId $env:SAE_APP_ID `
    --region $env:ALIBABA_CLOUD_REGION 2>&1 | Out-String
}

function Get-Field([string]$raw, [string]$name) {
  if ($raw -match "`"$([regex]::Escape($name))`"\s*:\s*`"([^`"]*)`"") { return $Matches[1] }
  if ($raw -match "`"$([regex]::Escape($name))`"\s*:\s*(true|false|\d+)") { return $Matches[1] }
  return $null
}

function Wait-NoChangeOrder([int]$timeoutSec = 180) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  while ((Get-Date) -lt $deadline) {
    $raw = Get-AppStatusRaw
    $running = (Get-Field $raw "LastChangeOrderRunning") -eq "true"
    $cos = Get-Field $raw "LastChangeOrderStatus"
    $cur = Get-Field $raw "CurrentStatus"
    Write-Host "  changeRunning=$running status=$cos app=$cur"
    if (-not $running) { return $raw }
    Start-Sleep -Seconds 5
  }
  throw "Timed out waiting for change order to finish."
}

function Wait-Stopped([int]$timeoutSec = 240) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  while ((Get-Date) -lt $deadline) {
    $raw = Get-AppStatusRaw
    $running = (Get-Field $raw "LastChangeOrderRunning") -eq "true"
    $cur = Get-Field $raw "CurrentStatus"
    $inst = Get-Field $raw "RunningInstances"
    Write-Host "  app=$cur instances=$inst changeRunning=$running"
    if (-not $running -and ($cur -eq "STOPPED" -or $inst -eq "0")) { return $raw }
    Start-Sleep -Seconds 5
  }
  throw "Timed out waiting for app to STOP."
}

function Wait-RunningSuccess([int]$timeoutSec = 480) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  while ((Get-Date) -lt $deadline) {
    $raw = Get-AppStatusRaw
    $running = (Get-Field $raw "LastChangeOrderRunning") -eq "true"
    $cos = Get-Field $raw "LastChangeOrderStatus"
    $cur = Get-Field $raw "CurrentStatus"
    $inst = Get-Field $raw "RunningInstances"
    Write-Host "  change=$cos app=$cur instances=$inst"
    if (-not $running -and $cos -eq "SUCCESS" -and $cur -eq "RUNNING") { return $raw }
    if (-not $running -and $cos -in @("FAIL", "ABORT")) {
      throw "Deploy ended with status $cos"
    }
    Start-Sleep -Seconds 8
  }
  throw "Timed out waiting for SUCCESS + RUNNING."
}

Write-Host "=== BrightNest SAE redeploy ($Tag) ==="
Write-Host "Rule: abort → STOP → build/push → deploy (never deploy on a live/stuck app)"

Load-DotEnv
Require-Env @(
  "ALIBABA_CLOUD_ACCESS_KEY_ID",
  "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
  "ALIBABA_CLOUD_REGION",
  "SAE_APP_ID",
  "ACR_REGISTRY",
  "ACR_REGISTRY_USERNAME",
  "ACR_REGISTRY_PASSWORD"
)

aliyun configure set --profile default --mode AK `
  --region $env:ALIBABA_CLOUD_REGION `
  --access-key-id $env:ALIBABA_CLOUD_ACCESS_KEY_ID `
  --access-key-secret $env:ALIBABA_CLOUD_ACCESS_KEY_SECRET | Out-Null

$imgPublic = "$($env:ACR_REGISTRY)/comp6708_a3_demo/brightnest-api:$Tag"
# SAE pulls via VPC endpoint when available
if ($env:SAE_IMAGE_URL -and $env:SAE_IMAGE_URL -match "crpi-.+-vpc\.") {
  $imgVpc = ($env:SAE_IMAGE_URL -replace ":v\d+$", ":$Tag")
  if ($imgVpc -eq $env:SAE_IMAGE_URL -and $env:SAE_IMAGE_URL -notmatch [regex]::Escape(":$Tag")) {
    $imgVpc = ($env:SAE_IMAGE_URL -replace ":[^:]+$", ":$Tag")
  }
} else {
  $imgVpc = $imgPublic -replace (
    "crpi-k469v9qxq160v01s\.cn-hongkong\.personal\.cr\.aliyuncs\.com",
    "crpi-k469v9qxq160v01s-vpc.cn-hongkong.personal.cr.aliyuncs.com"
  )
}

Write-Host "`n[1/5] Abort any in-flight change order..."
$status = Get-AppStatusRaw
$co = Get-Field $status "LastChangeOrderId"
$coRunning = (Get-Field $status "LastChangeOrderRunning") -eq "true"
if ($co -and $coRunning) {
  Write-Host "  Aborting $co"
  aliyun sae AbortChangeOrder --ChangeOrderId $co --region $env:ALIBABA_CLOUD_REGION
  Wait-NoChangeOrder | Out-Null
} else {
  Write-Host "  No running change order."
}

Write-Host "`n[2/5] STOP application (required before deploy)..."
$cur = Get-Field (Get-AppStatusRaw) "CurrentStatus"
if ($cur -ne "STOPPED") {
  aliyun sae StopApplication --AppId $env:SAE_APP_ID --region $env:ALIBABA_CLOUD_REGION
  Wait-Stopped | Out-Null
} else {
  Write-Host "  Already STOPPED."
  Wait-NoChangeOrder | Out-Null
}

if (-not $SkipBuild) {
  Write-Host "`n[3/5] Build + push $imgPublic ..."
  docker info | Out-Null
  $env:ACR_REGISTRY_PASSWORD | docker login `
    --username $env:ACR_REGISTRY_USERNAME `
    --password-stdin $env:ACR_REGISTRY | Out-Null
  docker build --provenance=false --sbom=false `
    -f deploy/Dockerfile.backend `
    -t $imgPublic `
    -t "brightnest-api:$Tag" `
    .
  docker push $imgPublic
} else {
  Write-Host "`n[3/5] SkipBuild — using existing ACR image $imgPublic"
}

Write-Host "`n[4/5] Deploy $imgVpc ..."
# Confirm still stopped / idle before deploy
Wait-NoChangeOrder | Out-Null
$cur = Get-Field (Get-AppStatusRaw) "CurrentStatus"
if ($cur -ne "STOPPED") {
  throw "Refusing to deploy: app status is '$cur' (must be STOPPED)."
}

aliyun sae DeployApplication `
  --AppId $env:SAE_APP_ID `
  --PackageType Image `
  --ImageUrl $imgVpc `
  --region $env:ALIBABA_CLOUD_REGION

Wait-RunningSuccess | Out-Null
Write-Host "DEPLOY_OK"

# Persist tag hints in .env (local only)
$envPath = Join-Path $Root ".env"
$envText = Get-Content $envPath -Raw
$envText = [regex]::Replace($envText, "(?m)^SAE_IMAGE_URL=.*$", "SAE_IMAGE_URL=$imgVpc")
$envText = [regex]::Replace($envText, "(?m)^SAE_IMAGE_URL_PUBLIC=.*$", "SAE_IMAGE_URL_PUBLIC=$imgPublic")
Set-Content -Path $envPath -Value $envText -NoNewline

if (-not $SkipSmoke) {
  Write-Host "`n[5/5] Smoke checks..."
  Start-Sleep -Seconds 8
  $h = Invoke-WebRequest "http://47.242.157.72:8000/healthz" -UseBasicParsing -TimeoutSec 30
  Write-Host "  healthz $($h.StatusCode) $($h.Content)"
  $r = Invoke-WebRequest "http://47.242.157.72:8000/admin/reindex" -Method POST -UseBasicParsing -TimeoutSec 180
  Write-Host "  reindex $($r.StatusCode) $($r.Content.Substring(0, [Math]::Min(180, $r.Content.Length)))"
  $body = '{"message":"Can I speak to a real person please?","session_id":"redeploy-smoke","debug":true}'
  $c = Invoke-WebRequest "http://47.242.157.72:8000/chat" -Method POST -Body $body `
    -ContentType "application/json" -UseBasicParsing -TimeoutSec 60
  $j = $c.Content | ConvertFrom-Json
  Write-Host "  chat route=$($j.trace.route) staff_message=$([bool]$j.trace.staff_message)"
}

Write-Host "`nDone. Image=$imgVpc"
Write-Host "Frontend: push developer-site master if UI changed (Vercel)."
