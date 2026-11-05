.PHONY: setup dev backend frontend gen-api test lint usage desktop app

setup:            ## Install backend, frontend and desktop app dependencies
	cd backend && uv sync
	cd frontend && npm install
	cd desktop && npm install

backend:          ## Run the API on http://127.0.0.1:8000 (auto-reload)
	cd backend && uv run iris serve --reload

frontend:         ## Run the UI on http://localhost:5173 (proxies /api to the backend)
	cd frontend && npm run dev

dev:              ## Run backend and frontend together
	$(MAKE) -j2 backend frontend

gen-api:          ## Regenerate the UI's typed API client (backend must be running)
	cd frontend && npm run gen:api

test:             ## Run backend/frontend/desktop tests and frontend typecheck
	cd backend && uv run pytest
	cd frontend && npm test && npm run typecheck
	cd desktop && npm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint

app:              ## Run the desktop app from the code: rebuilds the UI, no packaging (Dock: "Electron")
	cd desktop && npm run start:fresh

desktop:          ## Build the macOS app: desktop/dist/mac-arm64/Iris.app and a .dmg (Apple Silicon)
	desktop/scripts/build-app.sh

usage:            ## Model usage and cost for a session: make usage [<session_id>] (default: most recent)
	cd backend && uv run iris cost $(USAGE_ARGS)

# `make usage <session_id>`: take the word after `usage` as its argument, and make that word a
# do-nothing target so make doesn't try to build it. Only applies when `usage` is the first goal,
# so a mistyped target elsewhere still fails as usual. Defined after every rule, so an argument
# that happens to be a target name (`make usage test`) is ignored, not built.
ifeq (usage,$(firstword $(MAKECMDGOALS)))
  USAGE_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  ifneq ($(USAGE_ARGS),)
    $(eval $(USAGE_ARGS):;@:)
  endif
endif
