# Switchyard — Control Plane Makefile
#
# Reads API_HOST and API_PORT from switchyard-api/.env when present.
# Override any variable on the command line (e.g., make API_PORT=9000 dev).

API_DIR    := switchyard-api
ENV_FILE   := $(API_DIR)/.env

# -------------------------------------------------------------------
# Source values from .env (opt-in remote Docker, local Docker is default)
# -------------------------------------------------------------------
_env_docker   := $(shell grep -s '^SWITCHYARD_DOCKER_HOST=' $(ENV_FILE) | cut -d= -f2- | tr -d '[:space:]')
_env_api_host := $(shell grep -s '^SWITCHYARD_API_HOST=' $(ENV_FILE) | cut -d= -f2- | tr -d '[:space:]')
_env_api_port := $(shell grep -s '^SWITCHYARD_API_PORT=' $(ENV_FILE) | cut -d= -f2- | tr -d '[:space:]')

# Command-line ?= takes precedence over .env-sourced values
SSH_HOST       ?= trainbox
DOCKER_HOST    ?= $(or $(_env_docker),)
DOCKER_NETWORK ?= model-runtime
API_HOST       ?= $(or $(_env_api_host),0.0.0.0)
API_PORT       ?= $(or $(_env_api_port),8000)

# Export DOCKER_HOST only when non-empty (remote Docker is opt-in via .env)
ifeq ($(strip $(DOCKER_HOST)),)
unexport DOCKER_HOST
else
export DOCKER_HOST
endif

# Derive SSH tunnel port from Docker host URL (tcp://127.0.0.1:2375 → 2375)
DOCKER_PORT := $(or $(shell echo $(DOCKER_HOST) | sed -n 's/.*:\([0-9]\+\)$$/\1/p'),2375)

# -------------------------------------------------------------------
# Docker SSH Tunnel
# -------------------------------------------------------------------
tunnel:
	@echo "🔌 SSH tunnel to $(SSH_HOST):$(DOCKER_PORT)..."
	ssh -L $(DOCKER_PORT):/var/run/docker.sock $(SSH_HOST) -N

# -------------------------------------------------------------------
# Development Server
# -------------------------------------------------------------------
dev:
	@echo "🚀 Switchyard control plane (http://$(API_HOST):$(API_PORT))"
	cd $(API_DIR) && uv run uvicorn switchyard.app:create_app --factory --host $(API_HOST) --port $(API_PORT)

# -------------------------------------------------------------------
# Testing & Quality
# -------------------------------------------------------------------
test:
	cd $(API_DIR) && uv run pytest tests/

lint:
	cd $(API_DIR) && uv run ruff check src tests

typecheck:
	cd $(API_DIR) && uv run mypy src

quality: lint typecheck test
	@echo "✅ All quality gates passed"

# -------------------------------------------------------------------
# Deployment Smoke Targets
# -------------------------------------------------------------------
load-tinyllama-cpu:
	curl -s -X POST http://localhost:$(API_PORT)/deployments/load \
		-H 'Content-Type: application/json' \
		-d '{"deployment":"tinyllama-1.1b-chat-vllm-cpu-trainbox"}'

unload-tinyllama-cpu:
	curl -s -X POST http://localhost:$(API_PORT)/deployments/unload \
		-H 'Content-Type: application/json' \
		-d '{"deployment":"tinyllama-1.1b-chat-vllm-cpu-trainbox"}'

# -------------------------------------------------------------------
# Docker
# -------------------------------------------------------------------
docker-ps:
	docker ps --filter "network=$(DOCKER_NETWORK)" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

docker-clean:
	@echo "🧹 Stopping and removing switchyard containers..."
	-docker rm -f $(shell docker ps -aq --filter "name=switchyard") 2>/dev/null || true
	@echo "✅ Cleaned"

# -------------------------------------------------------------------
# Service Management
# -------------------------------------------------------------------
stop:
	@echo "🛑 Stopping services..."
	-lsof -ti tcp:$(API_PORT) | xargs kill 2>/dev/null || true
	@echo "✅ API server stopped"

status:
	@echo "Checking Switchyard services..."
	@if lsof -Pi :$(DOCKER_PORT) -sTCP:LISTEN -t >/dev/null 2>&1; then \
		echo "✅ SSH tunnel listening on port $(DOCKER_PORT)"; \
	else \
		echo "❌ SSH tunnel not active"; \
	fi
	@if lsof -Pi :$(API_PORT) -sTCP:LISTEN -t >/dev/null 2>&1; then \
		echo "✅ API server running on port $(API_PORT)"; \
	else \
		echo "❌ API server not running"; \
	fi

# -------------------------------------------------------------------
# Help
# -------------------------------------------------------------------
help:
	@echo "Switchyard Control Plane Commands:"
	@echo "  tunnel                 - Start SSH tunnel to remote Docker ($(SSH_HOST))"
	@echo "  dev                    - Start FastAPI development server"
	@echo "  test                   - Run pytest suite"
	@echo "  lint                   - Run ruff linter"
	@echo "  typecheck              - Run mypy type checking"
	@echo "  quality                - Run lint + typecheck + tests (full gates)"
	@echo "  load-tinyllama-cpu     - Load TinyLlama CPU deployment via API"
	@echo "  unload-tinyllama-cpu   - Unload TinyLlama CPU deployment via API"
	@echo "  status                 - Check tunnel and API server status"
	@echo "  docker-ps              - List containers on $(DOCKER_NETWORK) network"
	@echo "  docker-clean           - Remove orphan switchyard containers"
	@echo "  stop                   - Stop the API server"
	@echo ""
	@echo "Configuration (from $(ENV_FILE):)"
	@echo "  SSH_HOST=$(SSH_HOST)        - Remote Docker host"
	@echo "  DOCKER_PORT=$(DOCKER_PORT)  - Local port for SSH tunnel"
	@echo "  DOCKER_HOST=$(DOCKER_HOST)  - Docker SDK endpoint (empty = local Docker)"
	@echo "  DOCKER_NETWORK=$(DOCKER_NETWORK) - Container network"
	@echo "  API_HOST=$(API_HOST)        - FastAPI server bind address"
	@echo "  API_PORT=$(API_PORT)        - FastAPI server port"
	@echo ""
	@echo "Quick start (two terminals):"
	@echo "  Terminal 1: make tunnel"
	@echo "  Terminal 2: make dev"

.PHONY: tunnel dev test lint typecheck quality \
	load-tinyllama-cpu unload-tinyllama-cpu \
	docker-ps docker-clean stop status help
.DEFAULT_GOAL := help
