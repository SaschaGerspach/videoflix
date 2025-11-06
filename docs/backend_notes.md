# Backend Notes

Dieses Dokument enthält technische Hintergrundinformationen und Architekturhinweise zum Videoflix-Backend.  
Die Inhalte sind nicht für die öffentliche README bestimmt, sondern dienen der internen Nachvollziehbarkeit von Designentscheidungen, Workarounds und technischen Besonderheiten.

---

## 1. Cookie-basierte Authentifizierung bei HLS-Streams (`.m3u8` / `.ts`)

### Problemstellung

Beim Schutz der Streaming-Endpunkte (`index.m3u8`, `.ts`) traten mehrere Besonderheiten auf, die vom Standardverhalten des Django REST Frameworks abwichen:

1. **Leere Cookies bei WSGI-Streaming-Requests**  
    HLS-Aufrufe wie  
   /api/video/<id>/<resolution>/index.m3u8
   /api/video/<id>/<resolution>/<segment>.ts

werden auf niedrigerer Ebene über das WSGI-Interface verarbeitet.  
Dabei bleibt `request.COOKIES` oft leer, obwohl der `Cookie:`-Header tatsächlich vom Client gesendet wird.  
Dadurch konnte `CookieJWTAuthentication` kein `access_token` extrahieren, was zu wiederkehrenden  
**401 Unauthorized**-Antworten führte.

2. **Middleware-Redirects durch `APPEND_SLASH`**  
   Django leitete bei Segment-Routen wie `/000.ts` automatisch per **301 Redirect** auf `/000.ts/` um.  
   Diese Weiterleitung erfolgte **vor** der Authentifizierung, wodurch Cookies nicht mehr ausgewertet wurden.

3. **Inkonsequente Hostnamen & PowerShell-Testprobleme**  
   Unterschiede zwischen `localhost` und `127.0.0.1` sowie fehlerhafte Header-Übergaben  
   (z. B. `Accept:`-Syntax in PowerShell) führten dazu, dass Cookies in Tests nicht korrekt gesendet wurden.

---

### Lösung

1. **Flexible Regex-Routen ohne Redirects**  
   Die Segment-Endpunkte wurden auf `re_path`-Patterns mit optionalem Slash umgestellt:

re_path(r"^video/(?P<movie_id>\d+)/(?P<resolution>[^/]+)/(?P<segment>.+?)/?$", ...)
Dadurch entfällt der APPEND_SLASH-Redirect und Requests erreichen DRF direkt.

2. **Erweiterte CookieJWTAuthentication**
   Wenn request.COOKIES leer ist, wird nun der rohe Header aus
   request.META["HTTP_COOKIE"] ausgewertet (inkl. Quote-Cleanup).
   So kann das JWT selbst bei nicht-standardisierten WSGI-Aufrufen korrekt dekodiert werden.

3. **Debug-Hilfen**

Ein temporärer Endpunkt

/api/\_debug/auth

liefert Cookie-Inhalt, Roh-Header und Authentifizierungsstatus, um Fehlerdiagnosen zu erleichtern.

---

### Ergebnis

HLS-Manifeste und -Segmente werden nun mit denselben HttpOnly-Cookies geschützt wie normale API-Routen.

401-Fehler treten nur auf, wenn tatsächlich kein gültiges Token vorhanden ist.

Keine Weiterleitungen mehr über APPEND_SLASH, somit stabiler Streaming-Zugriff.

Lösung bleibt DRF-konform und benötigt keine Header-Manipulation im Frontend.

Tests
Ergänzende Tests wurden hinzugefügt, um das Verhalten abzusichern:

Datei Zweck
videos/api/tests/test_cookie_auth_nonjson.py Testet Login → Manifest- & Segmentzugriff (mit/ohne Slash), 401 für anonyme Nutzer
videos/api/tests/test_hls_auth.py Überprüft Cookie-Handling und Accept-Header-Verhalten
videos/api/tests/test_video_segment.py Stellt sicher, dass HLS-Segmente korrekt und authentifiziert ausgeliefert werden

Alle relevanten Testläufe (pytest) wurden erfolgreich abgeschlossen.

---

## 2. Renditions & Auto-Transcode

### Dynamische Standard-Profile

Uploads werden automatisch in mehrere Auflösungen transkodiert.  
Die Auswahl erfolgt seit dem 1080p-Upgrade dynamisch anhand der Quellhöhe (`probe_source_height` via ffprobe):

| Quellhöhe (`src_h`) | Geplante Renditions |
|---------------------|---------------------|
| < 720 px            | 480p                |
| 720 px – 1079 px    | 480p, 720p          |
| ≥ 1080 px           | 480p, 720p, 1080p   |

Konnte die Höhe nicht ermittelt werden, fällt das System auf 480p/720p zurück.  
Das Mapping wird von `videos.domain.services_autotranscode.schedule_default_transcodes` genutzt und kann über folgende Werkzeuge geprüft werden:

- `python manage.py upload_video <file.mp4> --publish`  
  (legt die Quelle unter `MEDIA_ROOT/sources/<id>.mp4` ab und stößt den Auto-Transcode an)
- `python manage.py rqworker_transcode`  
  (Windows-sicherer Worker dank `rq.SimpleWorker`; Log nennt Queue, Burst-Flag und Worker-Klasse)
- `python manage.py check_renditions --public <id> --res 480p 720p 1080p`
- `python manage.py heal_hls_index --public <id> --res 1080p --write --rebuild-master`
- Django-Admin-Aktionen: „Enqueue 480p“, „Enqueue 720p“, „Enqueue 1080p“, „Purge HLS renditions“

### HLS-Endpunkte & Clients

- Master-Manifest: `Accept: application/vnd.apple.mpegurl`
- Segmente: `Accept: video/MP2T`
- Optionaler Slash am Ende der Routen ist erlaubt, es gibt keine 301-Redirects mehr.

### Erwartetes Verhalten

- Upload eines 4K-Clips → Worker erzeugt 480p, 720p und 1080p.
- Upload eines 720p-Clips → Worker erzeugt 480p und 720p.
- Upload eines 540p-Clips → Worker erzeugt nur 480p.
- `check_renditions --res 1080p` zeigt den Status der 1080p-Rendition an.
- `heal_hls_index --res 1080p --write` ergänzt fehlende Streams/Segmente direkt aus dem Dateisystem.

Tests decken die neuen Pfade ab (`videos/domain/tests/test_autotranscode_signal.py`,  
`videos/api/tests/test_hls_delivery.py`, `videos/tests/management/test_*`).  
Ein vollständiger Durchlauf mit `python -m pytest -q` stellt sicher, dass Backfill und CLI-Flows grün bleiben.
