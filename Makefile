.PHONY: up down build rebuild restart logs test test-unit test-integration migrate makemigrations create-admin create-domain init bash geoip-update

# Auto-detect docker compose command (modern Docker uses "docker compose", older versions use "docker-compose")
DOCKER_COMPOSE := $(shell if docker compose version >/dev/null 2>&1; then echo 'docker compose'; else echo 'docker-compose'; fi)

u ?= admin
p ?= admin123456

up:
	$(DOCKER_COMPOSE) up -d

down:
	$(DOCKER_COMPOSE) down

build:
	$(DOCKER_COMPOSE) build app

rebuild:
	$(DOCKER_COMPOSE) up -d --build app

restart:
	$(DOCKER_COMPOSE) restart app

logs:
	$(DOCKER_COMPOSE) logs -f app

test:
	uv run pytest -v

test-unit:
	uv run pytest -v tests/test_auth.py tests/test_main.py tests/test_redirect.py tests/test_short_code.py tests/test_services_edge.py

test-integration:
	uv run pytest -v tests/test_api.py tests/test_blacklist.py tests/test_logs_extended.py

migrate:
	$(DOCKER_COMPOSE) exec app alembic upgrade head

makemigrations:
	$(DOCKER_COMPOSE) exec app alembic revision --autogenerate -m "$(m)"

create-admin:
	$(DOCKER_COMPOSE) exec app python scripts/create_admin.py $(u) $(p)

create-domain:
	$(DOCKER_COMPOSE) exec app python scripts/create_domain.py $(d)

init: up migrate create-domain create-admin

bash:
	$(DOCKER_COMPOSE) exec app bash

# Update MaxMind GeoIP database via geoipupdate container.
# If MaxMind blocks your server IP, pass a proxy:
#   make geoip-update HTTP_PROXY=http://127.0.0.1:7897 HTTPS_PROXY=http://127.0.0.1:7897
geoip-update:
	$(DOCKER_COMPOSE) --profile geoip run --rm \
		-e HTTP_PROXY=$(HTTP_PROXY) \
		-e HTTPS_PROXY=$(HTTPS_PROXY) \
		geoipupdate
