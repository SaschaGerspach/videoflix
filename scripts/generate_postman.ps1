$ErrorActionPreference = "Stop"

Write-Host "Generating OpenAPI schema (YAML/JSON) ..."
python manage.py spectacular --file schema.yaml
python manage.py spectacular --format openapi-json --file schema.json

Write-Host "Ensuring postman output directory exists ..."
New-Item -ItemType Directory -Path "postman" -Force | Out-Null

Write-Host "Creating Postman collection via openapi-to-postmanv2 ..."
try {
    npx openapi-to-postmanv2 -s schema.json -o postman/collection.json -p -O folderStrategy=Tags --pretty
} catch {
    Write-Warning "openapi-to-postmanv2 does not recognise --pretty; falling back without it."
    npx openapi-to-postmanv2 -s schema.json -o postman/collection.json -p -O folderStrategy=Tags
    $json = Get-Content postman/collection.json -Raw | ConvertFrom-Json
    $json | ConvertTo-Json -Depth 100 | Set-Content postman/collection.json
}

Write-Host "Done. Files available under postman/ and schema.{yaml,json}."
