.PHONY: env check-config check-schema up down build rebuild restart logs test test-unit test-integration migrate makemigrations create-admin create-domain init bash geoip-update

# Auto-detect docker compose command (modern Docker uses "docker compose", older versions use "docker-compose")
DOCKER_COMPOSE := $(shell if docker compose version >/dev/null 2>&1; then echo 'docker compose'; else echo 'docker-compose'; fi)

u ?= admin
p ?= admin123456

env:
	@$(DOCKER_COMPOSE) run --rm --no-deps app python scripts/check_config.py

check-config:
	@$(DOCKER_COMPOSE) config --quiet
	@$(DOCKER_COMPOSE) run --rm --build --no-deps app python scripts/check_config.py

up: check-config
	$(DOCKER_COMPOSE) up -d

down:
	$(DOCKER_COMPOSE) down

build:
	$(DOCKER_COMPOSE) build app

rebuild:
	$(DOCKER_COMPOSE) up -d --build app

restart: check-config
	$(DOCKER_COMPOSE) up -d --force-recreate app

logs:
	$(DOCKER_COMPOSE) logs -f app

test:
	uv run pytest -v

test-unit:
	uv run pytest -v \
		tests/core/test_auth.py \
		tests/core/test_client_ip.py \
		tests/core/test_config.py \
		tests/core/test_main.py \
		tests/core/test_rate_limit.py \
		tests/features/auth/test_auth_cookie.py \
		tests/features/redirect/test_redirect.py \
		tests/features/redirect/test_redirect_service_edge.py \
		tests/features/short_links/test_short_code.py \
		tests/features/short_links/test_short_code_edge.py \
		tests/integrations/test_geoip.py

test-integration:
	uv run pytest -v \
		tests/features/test_api.py \
		tests/features/blacklist/test_blacklist.py \
		tests/features/access_logs/test_logs_extended.py

check-schema:
	$(DOCKER_COMPOSE) run --rm app python scripts/check_schema.py --mode pre

migrate: check-schema
	$(DOCKER_COMPOSE) run --rm app alembic upgrade head
	$(DOCKER_COMPOSE) run --rm app python scripts/check_schema.py --mode post

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
