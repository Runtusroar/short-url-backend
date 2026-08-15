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

PYTEST = .venv/bin/pytest

# 运行全部测试（单元测试 + 集成测试，集成测试需要本地 PostgreSQL 5432 可访问）
test:
	$(PYTEST) -v

# 仅运行不依赖数据库的单元测试
test-unit:
	$(PYTEST) -v tests/test_auth.py tests/test_main.py tests/test_redirect.py tests/test_short_code.py

# 仅运行集成测试（需要 PostgreSQL 测试库）
test-integration:
	$(PYTEST) -v tests/test_api.py

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
