# stop_videoflix.ps1
param([string]$ComposeFile = "compose.yml")

Write-Host "Stopping Videoflix stack..." -ForegroundColor Yellow

docker compose -f $ComposeFile down --remove-orphans | Out-Null

# Prüfen, ob noch Container laufen
$runningContainers = docker compose -f $ComposeFile ps -q

if ($runningContainers -eq $null -or $runningContainers.Count -eq 0) {
    Write-Host "All containers stopped successfully." -ForegroundColor Green
}
else {
    Write-Host "Some containers are still running:" -ForegroundColor Red
    docker compose -f $ComposeFile ps
}
