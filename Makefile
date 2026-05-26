.PHONY: install test lint fmt doctor bootstrap-local dev backend-install frontend-install host-install

PYTHON := python
NODE := node
NPM := npm

install: backend-install frontend-install host-install
	@echo "All components installed."

backend-install:
	cd backend && $(PYTHON) -m pip install -e ".[dev]"

frontend-install:
	cd frontend && $(NPM) install

host-install:
	cd host-agent && $(PYTHON) -m pip install -e ".[dev]"

test:
	cd backend && $(PYTHON) -m pytest
	cd host-agent && $(PYTHON) -m pytest
	@echo "Frontend tests not yet implemented (T-003)."

lint:
	cd backend && $(PYTHON) -m ruff check .
	cd host-agent && $(PYTHON) -m ruff check .
	cd frontend && $(NPM) run lint || true

fmt:
	cd backend && $(PYTHON) -m ruff format .
	cd host-agent && $(PYTHON) -m ruff format .

doctor:
	@echo "=== Agent Command Center Doctor ==="
	@$(PYTHON) --version || echo "FAIL: Python not found"
	@$(NODE) --version || echo "FAIL: Node not found"
	@$(NPM) --version || echo "FAIL: npm not found"
	@echo "--- Docker ---"
	@docker --version || echo "WARN: Docker not found"
	@echo "--- PostgreSQL ---"
	@docker compose ps postgres 2>/dev/null || echo "WARN: postgres container not running"
	@echo "--- Valkey ---"
	@docker compose ps valkey 2>/dev/null || echo "WARN: valkey container not running"
	@echo "--- NTFY ---"
	@docker compose ps ntfy 2>/dev/null || echo "WARN: ntfy container not running"
	@echo "=== Doctor Complete ==="

bootstrap-local:
	@echo "Creating local dev stack..."
	docker compose up -d postgres valkey ntfy
	@echo "Run migrations next: cd backend && python manage.py migrate"
	@echo "Create superuser next: cd backend && python manage.py createsuperuser"
	@echo "Start backend next: cd backend && python manage.py runserver"
	@echo "Start frontend next: cd frontend && npm run dev"

dev:
	@echo "Start dev services in separate terminals:"
	@echo "  Terminal 1: make bootstrap-local"
	@echo "  Terminal 2: cd backend && python manage.py runserver"
	@echo "  Terminal 3: cd frontend && npm run dev"
