.PHONY: up down build rebuild restart logs test test-unit test-integration migrate makemigrations create-admin create-domain init bash

u ?= admin
p ?= admin123456

up:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose build app

rebuild:
	docker-compose up -d --build app

restart:
	docker-compose restart app

logs:
	docker-compose logs -f app

test:
	uv run pytest -v

test-unit:
	uv run pytest -v tests/test_auth.py tests/test_main.py tests/test_redirect.py tests/test_short_code.py tests/test_services_edge.py

test-integration:
	uv run pytest -v tests/test_api.py tests/test_blacklist.py tests/test_logs_extended.py

migrate:
	docker-compose exec app alembic upgrade head

makemigrations:
	docker-compose exec app alembic revision --autogenerate -m "$(m)"

create-admin:
	docker-compose exec app python scripts/create_admin.py $(u) $(p)

create-domain:
	docker-compose exec app python scripts/create_domain.py $(d)

init: up migrate create-domain create-admin

bash:
	docker-compose exec app bash
