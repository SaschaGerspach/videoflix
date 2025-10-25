.PHONY: schema postman

schema:
	python manage.py spectacular --file schema.yaml
	python manage.py spectacular --format openapi-json --file schema.json

postman: schema
	python -c "import os; os.makedirs('postman', exist_ok=True)"
	npx openapi-to-postmanv2 -s schema.json -o postman/collection.json -p -O folderStrategy=Tags --pretty || \
	( npx openapi-to-postmanv2 -s schema.json -o postman/collection.json -p -O folderStrategy=Tags && \
python - <<'PY'
import json, pathlib
path = pathlib.Path("postman/collection.json")
data = json.loads(path.read_text())
path.write_text(json.dumps(data, indent=2) + "\n")
PY \
	)
