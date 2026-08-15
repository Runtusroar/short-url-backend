.PHONY: up down build rebuild restart logs test test-cov migrate makemigrations create-admin create-domain init bash

# 默认管理员账号密码（make create-admin 时可覆盖，如 make create-admin u=root p=123456）
u ?= admin
p ?= admin123456

# 启动所有 Docker 服务（PostgreSQL、Redis、app）
up:
	docker-compose up -d

# 停止并删除所有 Docker 服务
down:
	docker-compose down

# 只构建 app 镜像，不启动
build:
	docker-compose build app

# 重新构建 app 镜像并启动（修改了代码后用它）
rebuild:
	docker-compose up -d --build app

# 重启 app 容器（不重新构建镜像，仅重启进程）
restart:
	docker-compose restart app

# 实时查看 app 容器日志
logs:
	docker-compose logs -f app

# 运行本地单元测试（不需要 Docker）
test:
	pytest -v

# 运行测试并输出覆盖率报告
test-cov:
	pytest --cov=app --cov-report=term-missing

# 在 app 容器内执行数据库迁移（将 Alembic 脚本应用到最新版本）
migrate:
	docker-compose exec app alembic upgrade head

# 自动生成 Alembic 迁移文件，m 参数为迁移说明
# 用法：make makemigrations m=add_user_phone
makemigrations:
	docker-compose exec app alembic revision --autogenerate -m "$(m)"

# 在 app 容器内创建管理员账号
# 用法：make create-admin 或 make create-admin u=root p=123456
create-admin:
	docker-compose exec app python scripts/create_admin.py $(u) $(p)

# 在 app 容器内创建域名
# 用法：make create-domain d=example.com 或 make create-domain d=example.com --default
create-domain:
	docker-compose exec app python scripts/create_domain.py $(d)

# 一键初始化：启动服务 + 执行迁移 + 创建默认域名 + 创建默认管理员
init: up migrate create-domain create-admin

# 进入 app 容器内部的 bash
bash:
	docker-compose exec app bash
