.PHONY: setup dev backend frontend gen-api test lint

setup:            ## Install backend and frontend dependencies
	cd backend && uv sync
	cd frontend && npm install

backend:          ## Run the API on http://127.0.0.1:8000 (auto-reload)
	cd backend && uv run iris serve --reload

frontend:         ## Run the UI on http://localhost:5173 (proxies /api to the backend)
	cd frontend && npm run dev

dev:              ## Run backend and frontend together
	$(MAKE) -j2 backend frontend

gen-api:          ## Regenerate the UI's typed API client (backend must be running)
	cd frontend && npm run gen:api

test:             ## Run backend/frontend tests and frontend typecheck
	cd backend && uv run pytest
	cd frontend && npm test && npm run typecheck

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint
