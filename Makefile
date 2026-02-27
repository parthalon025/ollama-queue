.PHONY: lint lint-py lint-spa lint-sh lint-audit format

all: lint

lint: lint-py lint-spa lint-sh lint-audit

lint-py:
	ruff check .

lint-spa:
	cd ollama_queue/dashboard/spa && npm run lint

lint-sh:
	@shellcheck scripts/generate-embeddings.sh 2>&1

lint-audit:
	pip-audit --progress-spinner off -q 2>/dev/null || true

format:
	ruff format .
	ruff check --fix .
	cd ollama_queue/dashboard/spa && npx prettier --write src/
