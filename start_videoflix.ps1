# start_videoflix.ps1
Write-Host "Starting Videoflix services..."

# Redis starten (wenn gestoppt)
docker start videoflix-redis 2>$null | Out-Null

# Postgres starten (wenn gestoppt)
docker start videoflix-pg 2>$null | Out-Null

# Kleinen Moment warten, damit Redis/PG hochfahren
Start-Sleep -Seconds 3

# Worker starten (Windows-sicher)
Write-Host "Starting RQ worker for transcode queue..."
python manage.py rqworker_transcode
