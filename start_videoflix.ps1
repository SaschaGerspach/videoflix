# start_videoflix.ps1
# Startet den kompletten Stack (down -> build -> up) und wartet,
# bis die Healthchecks von postgres, redis und web grün sind.

param(
  [string]$ComposeFile = "compose.yml",
  [int]$HealthTimeoutSec = 240,
  [switch]$SkipBuild   # optional: ./start_videoflix.ps1 -SkipBuild
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host $msg -ForegroundColor Green }
function Write-Warn($msg) { Write-Host $msg -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host $msg -ForegroundColor Red }

function Get-ServiceContainerId([string]$svc) {
  # gibt die Container-ID des Compose-Services zurück (oder $null)
  $id = (docker compose -f $ComposeFile ps -q $svc 2>$null)
  if ([string]::IsNullOrWhiteSpace($id)) { return $null }
  return $id.Trim()
}

function Wait-ContainerHealthy([string]$containerId, [int]$timeoutSec) {
  # wartet bis .State.Health.Status = healthy | unhealthy | running
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  do {
    $status = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $containerId 2>$null
    if ($LASTEXITCODE -ne 0) { Start-Sleep -Seconds 2; continue }

    switch ($status) {
      "healthy" { return $true }
      "running" { return $true }   # falls kein Healthcheck vorhanden, aber Container läuft
      "unhealthy" { return $false }
      default { Start-Sleep -Seconds 2 }
    }
  } while ((Get-Date) -lt $deadline)

  return $false
}

Write-Info "Stopping old stack (if any)…"
docker compose -f $ComposeFile down --remove-orphans | Out-Null

if (-not $SkipBuild) {
  Write-Info "Building images (cached layers keep this fast if nothing changed)…"
  docker compose -f $ComposeFile build | Out-Null
}
else {
  Write-Warn "Skipping build (you passed -SkipBuild)."
}

Write-Info "Starting containers in background…"
docker compose -f $ComposeFile up -d | Out-Null

Write-Info "Running Django migrations…"
docker compose -f $ComposeFile exec web python manage.py migrate --noinput

# Dienste, deren Health wir abwarten (names = compose services)
$servicesToCheck = @("postgres", "redis", "web", "nginx")

foreach ($svc in $servicesToCheck) {
  $cid = Get-ServiceContainerId $svc
  if (-not $cid) {
    Write-Err "Service '$svc' not found in compose (check compose.yml)."
    exit 1
  }

  Write-Info "Waiting for health: $svc …"
  if (Wait-ContainerHealthy -containerId $cid -timeoutSec $HealthTimeoutSec) {
    Write-Ok "✓ $svc is healthy."
  }
  else {
    Write-Err "✗ $svc did not become healthy within $HealthTimeoutSec s."
    Write-Warn "Recent logs:"
    docker logs --tail 120 $cid
    exit 1
  }
}

Write-Ok  "All core services are healthy."
Write-Info "Stack status:"
docker compose -f $ComposeFile ps

# Kleine Laufzeit-Checks (optional, aber praktisch)
# Wenn nginx im Compose ist, lauscht i. d. R. Port 80 -> /healthz
# sonst direkt der web-Container auf 8000
try {
  $healthUrls = @("http://localhost:8000/healthz/")
  foreach ($u in $healthUrls) {
    try {
      $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 5
      if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400) {
        Write-Ok "Health endpoint reachable: $u (HTTP $($r.StatusCode))"
        break
      }
    }
    catch { }
  }
}
catch { Write-Warn "Health probe skipped (Invoke-WebRequest not available)." }

Write-Ok "Videoflix stack is up. Open:"
Write-Host "  - Web via nginx:  http://localhost:8000/" -ForegroundColor Gray
