# Switchyard — Control Plane Makefile

# Configuration
API_DIR     = switchyard-api
SSH_HOST    ?= trainbox.lan
DOCKER_PORT ?= 2375
API_PORT    ?= 8000

# Environment
# pydantic-settings auto-loads .env from the API directory,
# so we don't need to include it here explicitly.

# Docker SSH Tunnel
tunnel:
	@echo "🔌 SSH tunnel to $(SSH_HOST):$(DOCKER_PORT)..."
	@ssh -L $(DOCKER_PORT):/var/run/docker.sock $(SSH_HOST) -N

# Development Server
dev:
	@echo "🚀 Switchyard control plane (http://localhost:$(API_PORT))"
	cd $(API_DIR) && uv run uvicorn switchyard.app:create_app --factory --host 0.0.0.0 --port $(API_PORT)

# Testing
test:
	cd $(API_DIR) && uv run pytest tests/

lint:
	cd $(API_DIR) && uv run ruff check src tests

typecheck:
	cd $(API_DIR) && uv run mypy src

quality: lint typecheck test
	@echo "✅ All quality gates passed"

# Docker
docker-ps:
	docker ps --filter "network=model-runtime" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

docker-clean:
	@echo "🧹 Stopping and removing switchyard containers..."
	docker rm -f $(shell docker ps -aq --filter "name=switchyard") 2>/dev/null || true
	@echo "✅ Cleaned"

# Service Management
stop:
	-@echo "🛑 Stopping services..."
	-@lsof -ti tcp:$(API_PORT) | xargs kill 2>/dev/null || true
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

# Help
help:
	@echo "Switchyard Control Plane Commands:"
	@echo "  tunnel         - Start SSH tunnel to remote Docker ($(SSH_HOST))"
	@echo "  dev            - Start FastAPI development server"
	@echo "  test           - Run pytest suite"
	@echo "  lint           - Run ruff linter"
	@echo "  typecheck      - Run mypy type checking"
	@echo "  quality        - Run lint + typecheck + tests (full gates)"
	@echo "  status         - Check tunnel and API server status"
	@echo "  docker-ps      - List containers on the model-runtime network"
	@echo "  docker-clean   - Remove orphan switchyard containers"
	@echo "  stop           - Stop the API server"
	@echo ""
	@echo "Configuration:"
	@echo "  SSH_HOST=$(SSH_HOST)   - Remote Docker host"
	@echo "  DOCKER_PORT=$(DOCKER_PORT) - Local port for SSH tunnel"
	@echo "  API_PORT=$(API_PORT)   - FastAPI server port"
	@echo ""
	@echo "Quick start (two terminals):"
	@echo "  Terminal 1: make tunnel"
	@echo "  Terminal 2: make dev"

.PHONY: tunnel dev test lint typecheck quality docker-ps docker-clean stop status help
.DEFAULT_GOAL := help
