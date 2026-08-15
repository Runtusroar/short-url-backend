# 短链系统设计文档

## 1. 项目目标
构建一个支持访问控制、访问日志记录与多目标跳转的短链系统。

## 2. 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI |
| ORM / 迁移 | SQLAlchemy 2.0 + Alembic |
| 数据库 | PostgreSQL 15+（驱动：psycopg） |
| 缓存 / 限流 | Redis + fastapi-limiter |
| 认证 | JWT（PyJWT）+ bcrypt |
| UA 解析 | user-agents |
| IP 地理位置 | geoip2 + MaxMind GeoLite2-City |
| 测试 | pytest、pytest-asyncio、httpx |
| 部署 | Docker Compose |
| 前端 | React + Ant Design + Ant Design Charts |

## 3. 目录结构

```
short_url/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置（Pydantic Settings）
│   ├── database.py             # 引擎、Session、依赖
│   ├── models.py               # SQLAlchemy 模型
│   ├── schemas.py              # Pydantic 输入输出模型
│   ├── auth.py                 # JWT、密码哈希、权限校验
│   ├── dependencies.py         # 公共依赖（DB、当前用户等）
│   ├── routers/
│   │   ├── auth.py             # 登录 / 当前用户
│   │   ├── short_links.py      # 短链 CRUD
│   │   ├── redirect.py         # 短链跳转 + 日志记录
│   │   ├── logs.py             # 日志与每日统计
│   │   └── admin.py            # 用户管理（admin 专属）
│   └── services/
│       ├── short_code.py       # 短码生成与冲突处理
│       ├── redirect.py         # 规则匹配、URL 选择
│       ├── geoip.py            # IP 解析国家
│       └── ua.py               # UA 解析平台
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_short_links.py
│   └── test_redirect.py
├── alembic/                    # 迁移脚本
├── frontend/                   # React 前端
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── requirements.txt
```

## 4. 数据库设计

### 4.1 users（用户）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| username | varchar(64) unique | 登录名 |
| password_hash | varchar(255) | bcrypt |
| role | varchar(16) | admin / operator / client |
| is_active | boolean | |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### 4.2 domains（域名/租户）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| name | varchar(255) unique | 域名，如 `a.com` |
| is_active | boolean | |
| is_default | boolean | 是否默认域名 |
| created_at | timestamptz | |

### 4.3 user_domains（用户可访问的域名）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| user_id | UUID FK -> users.id | |
| domain_id | UUID FK -> domains.id | |
| created_at | timestamptz | |
| 唯一索引 | (user_id, domain_id) | |

### 4.4 short_links（短链）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| domain_id | UUID FK -> domains.id | 所属域名 |
| short_code | varchar(32) | 6 位随机，或自定义别名 |
| is_custom_alias | boolean | 是否为自定义别名 |
| description | text | 描述 |
| owner_id | UUID FK -> users.id | 创建者 |
| is_active | boolean | 是否启用 |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| 唯一索引 | (domain_id, short_code) | 同一域名下短码唯一 |

### 4.5 short_link_permissions（短链查看授权）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| short_link_id | UUID FK -> short_links.id | |
| user_id | UUID FK -> users.id | 被授权的 client |
| created_at | timestamptz | |

### 4.6 target_urls（目标链接）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| short_link_id | UUID FK -> short_links.id | |
| url | text | 跳转地址 |
| url_type | varchar(16) | allowed / denied |
| weight | int default 1 | 随机权重 |
| is_active | boolean | |
| created_at | timestamptz | |

### 4.7 access_rules（访问规则）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| short_link_id | UUID FK -> short_links.id | |
| action | varchar(16) | allow / deny |
| priority | int | 越小越优先 |
| countries | jsonb | 国家代码列表，空表示不限 |
| ua_platforms | jsonb | 平台列表，空表示不限 |
| referer_pattern | varchar(255) | 通配符或正则，空表示不限 |
| is_active | boolean | |

### 4.8 ip_blacklist（IP 黑名单）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| ip | varchar(64) unique | |
| reason | text | |
| created_by | UUID FK -> users.id | |
| created_at | timestamptz | |

### 4.9 access_logs（访问日志）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| short_link_id | UUID FK -> short_links.id | |
| domain_id | UUID FK -> domains.id | 所属域名 |
| target_url_id | UUID FK -> target_urls.id nullable | 实际跳转的 URL |
| result | varchar(16) | allowed / denied / blocked |
| ip | varchar(64) | |
| country | varchar(8) | |
| ua_string | text | 原始 UA |
| ua_platform | varchar(64) | |
| referer | text | |
| accessed_at | timestamptz | UTC 原始时间 |
| accessed_at_plus8 | date | +8 时区日期，用于日统计 |
| dedup_bucket | bigint | 30 秒去重窗口的 Unix 时间戳 |

> 索引：`CREATE INDEX idx_access_logs_dedup ON access_logs (short_link_id, ip, dedup_bucket);`

## 5. 短码生成

- 字符集：`a-zA-Z0-9`。
- 默认 6 位随机字符串。
- 生成后在**同一域名内**检查是否重复，重复则重试（最多 5 次）。
- 自定义别名直接存入 `short_code`，`is_custom_alias = true`。
- 自定义别名需符合正则 `^[a-zA-Z0-9_-]{3,32}$`，且不能为 `api`、`admin`、`static` 等保留前缀。
- 用户创建短链时，`custom_alias` 字段可选；留空则系统自动生成 6 位随机码。
- 创建短链时必须指定 `domain_id`。
- 对外展示完整短链地址由前端根据当前域名拼接：`https://当前域名/{short_code}`。

## 6. 跳转与访问控制流程

```
请求 /{short_code}
  │
  ▼
根据 Host 头识别当前域名（无匹配则回退默认域名）
  │
  ▼
在当前域名下检查短链是否存在且启用
  │
  ▼
解析请求元数据
  - IP
  - country（MaxMind geoip2）
  - UA 平台（user-agents）
  - referer
  │
  ▼
检查全局 IP 黑名单
  - 命中 → result = blocked，跳转 denied URL 池
  │
  ▼
按 priority 排序执行 access_rules
  - 第一条完全匹配 country / ua_platform / referer 的规则生效
  - 未命中规则 → 默认 allow
  │
  ▼
根据结果选择 target_urls
  - allow  → 从 url_type = allowed 且 active 的 URL 中按 weight 随机
  - deny / blocked → 从 url_type = denied 且 active 的 URL 中按 weight 随机
  │
  ▼
记录 access_logs，返回 302 跳转
```

## 7. 限流设计

- 使用 `slowapi` 基于 Redis 做全局限流。
- 限流策略：
  - 短链跳转：`60 次 / 分钟 / IP`
  - 管理后台登录：`60 次 / 分钟 / IP`
  - 其他管理接口：`120 次 / 分钟 / 用户`
- 超过限制返回 `429 Too Many Requests`，并在响应头中附带 `Retry-After`。
- 黑名单 IP 直接拦截，不计入限流。

## 8. 访问日志与日统计

### 8.1 写入策略
- 每次跳转同步写入 `access_logs`。
- `dedup_bucket` 在写入时根据 `accessed_at` 计算：
  ```python
  bucket = int(accessed_at.timestamp() // 30) * 30
  ```

### 8.2 访问记录去重展示
- 按 **30 秒窗口 + IP + 短链** 去重，展示每个窗口内的第一条记录：
  ```sql
  SELECT DISTINCT ON (short_link_id, ip, dedup_bucket)
      id, ip, country, ua_platform, referer, accessed_at
  FROM access_logs
  WHERE short_link_id = :id
  ORDER BY short_link_id, ip, dedup_bucket, accessed_at ASC;
  ```

### 8.3 日统计
- 日统计仍按 `accessed_at_plus8`（+8 时区日期）分组：
  ```sql
  SELECT accessed_at_plus8,
         COUNT(*) AS total,
         COUNT(*) FILTER (WHERE result = 'allowed') AS allowed,
         COUNT(*) FILTER (WHERE result = 'denied' OR result = 'blocked') AS denied,
         COUNT(DISTINCT ip) AS unique_ips
  FROM access_logs
  WHERE short_link_id = :id
  GROUP BY accessed_at_plus8
  ORDER BY accessed_at_plus8 DESC;
  ```
- 默认展示最近 30 天。

## 9. 权限设计

| 功能 | admin | operator | client |
|------|-------|----------|--------|
| 查看所有短链 | ✅ | ❌ | ❌ |
| 管理自己的短链 | ✅ | ✅ | ❌ |
| 查看授权给自己的短链 | ✅ | ✅ | ✅ |
| 管理用户 | ✅ | ❌ | ❌ |
| 管理 IP 黑名单 | ✅ | ✅ | ❌ |
| 查看所有日志 | ✅ | ❌ | ❌ |
| 查看自己短链的日志 | ✅ | ✅ | ✅ |

> 短链对 client 的授权通过 `short_link_permissions` 表实现：admin/operator 可将任意短链授权给指定 client 查看，client 无管理权限。

## 10. API 概览

### 10.1 Auth
- `POST /api/auth/login` — 登录，返回 JWT
- `GET  /api/auth/me` — 当前用户信息

### 10.2 Short Links（需认证）
- `GET    /api/short-links` — 列表（按角色过滤）
- `POST   /api/short-links` — 创建
- `GET    /api/short-links/{id}` — 详情
- `PUT    /api/short-links/{id}` — 更新
- `DELETE /api/short-links/{id}` — 删除
- `POST /api/short-links/{id}/permissions` — 授权 client 查看（admin 或短链 owner）
- `DELETE /api/short-links/{id}/permissions/{user_id}` — 撤销 client 授权

### 10.3 Redirect（公开）
- `GET /{short_code}` — 跳转并记录日志

### 10.4 Logs（需认证）
- `GET /api/logs?short_link_id=&date_from=&date_to=` — 原始日志
- `GET /api/logs/daily?short_link_id=` — 日统计

### 10.5 Domains
- `GET  /api/domains` — 列表（admin 看全部，其他看有权限的）
- `POST /api/domains` — 创建（admin）
- `GET  /api/domains/{id}` — 详情（admin）
- `PUT  /api/domains/{id}` — 更新（admin）
- `DELETE /api/domains/{id}` — 删除（admin）

### 10.6 IP 黑名单（admin / operator）
- `GET  /api/ip-blacklist`
- `POST /api/ip-blacklist`
- `DELETE /api/ip-blacklist/{id}`

### 10.7 Admin（仅 admin）
- `GET  /api/admin/users`
- `POST /api/admin/users` — body 可带 `domain_ids`
- `PUT  /api/admin/users/{id}`

## 11. Docker Compose 部署

```yaml
# docker-compose.yml 概要
services:
  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: shorturl
      POSTGRES_PASSWORD: shorturl
      POSTGRES_DB: shorturl
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redisdata:/data

  geoipupdate:
    image: maxmindinc/geoipupdate
    environment:
      GEOIPUPDATE_ACCOUNT_ID: ${GEOIPUPDATE_ACCOUNT_ID}
      GEOIPUPDATE_LICENSE_KEY: ${GEOIPUPDATE_LICENSE_KEY}
      GEOIPUPDATE_EDITION_IDS: GeoLite2-City
      GEOIPUPDATE_FREQUENCY: 24
    volumes:
      - geoipdata:/usr/share/GeoIP

  app:
    build: .
    environment:
      DATABASE_URL: postgresql+asyncpg://shorturl:shorturl@db/shorturl
      REDIS_URL: redis://redis:6379/0
      SECRET_KEY: ${SECRET_KEY}
      GEOIP_DB_PATH: /usr/share/GeoIP/GeoLite2-City.mmdb
    volumes:
      - geoipdata:/usr/share/GeoIP:ro
    depends_on:
      - db
      - redis
    ports:
      - "8000:8000"

volumes:
  pgdata:
  redisdata:
  geoipdata:
```

> MaxMind 方案：使用免费的 **GeoLite2-City**，通过官方 `geoipupdate` 容器每天自动更新。需要在 MaxMind 官网注册账号并生成 License Key，填入 `.env` 的 `GEOIPUPDATE_ACCOUNT_ID` 与 `GEOIPUPDATE_LICENSE_KEY`。

## 12. Makefile 快捷命令

```makefile
.PHONY: up down test migrate test-cov

up:
	docker-compose up -d

down:
	docker-compose down

test:
	pytest -v

test-cov:
	pytest --cov=app --cov-report=term-missing

migrate:
	alembic upgrade head

makemigrations:
	alembic revision --autogenerate -m "$(m)"
```

## 13. 测试策略

- 单元测试：短码生成、规则匹配、URL 加权随机选择、密码/JWT。
- 集成测试：短链 CRUD、登录、跳转流程、日志写入（待补充）。
- 测试环境通过 `REDIS_URL=` 关闭限流，避免依赖 Redis。
- 覆盖率目标：核心服务层 ≥ 80%。

## 14. 设计建议与注意事项

1. **短码与自定义别名冲突**：`short_code` 在**同一域名内**唯一。自定义别名需额外校验字符（建议 `a-zA-Z0-9_-`），避免与 6 位随机码规则重叠或出现 `/api` 等保留前缀。
2. **路由冲突**：API 统一挂载在 `/api/*`，短链跳转使用 `/{short_code}`。开发环境下前端代理 `/api` 到后端；生产环境由用户自行通过宝塔反向代理到 Docker 端口，本项目的 `docker-compose.yml` 不内置 Nginx。
3. **空 URL 池处理**：
   - 若结果判定为 allow，但 `allowed` URL 池为空，返回 404 或展示默认提示页。
   - 若结果判定为 deny/blocked，但 `denied` URL 池为空，返回 403。
4. **日志写入性能**：访问量较大时，同步写入日志可能成为瓶颈。V1 先同步写入，后续可改为 Celery / RQ 异步批量写入。
5. **国家代码格式**：统一使用 ISO-3166-1 alpha-2（如 CN、US），与 MaxMind 输出保持一致。

## 15. 已确认的设计决策

1. **前端 UI 组件库**：Ant Design + Ant Design Charts。
2. **生产部署**：不内置 Nginx，由用户通过宝塔反向代理到 Docker 端口。
3. **运营后台统计图表**：需要，包括每日访问量折线图、国家/平台/Referer 分布饼图等。

## 16. 运行方式

### 本地开发

1. 准备 `.env` 文件（可参考 `.env.example`），至少设置 `SECRET_KEY`。
2. 启动 PostgreSQL 与 Redis：
   ```bash
   docker-compose up -d db redis
   ```
3. 执行数据库迁移：
   ```bash
   make migrate
   ```
4. 创建默认域名：
   ```bash
   make create-domain d=localhost
   ```
5. 创建初始管理员：
   ```bash
   make create-admin u=admin p=password123
   ```
6. 启动后端：
   ```bash
   docker-compose up -d app
   ```

### 生产部署

1. 在 MaxMind 官网注册账号并生成 License Key。
2. 将 `GEOIPUPDATE_ACCOUNT_ID`、`GEOIPUPDATE_LICENSE_KEY`、`SECRET_KEY` 写入 `.env`。
3. 启动 geoipupdate 以下载 IP 数据库：
   ```bash
   docker-compose --profile geoip up -d
   ```
4. 执行 `make migrate` 完成迁移。
5. 创建业务域名：
   ```bash
   make create-domain d=yourdomain.com
   ```
5. 通过宝塔反向代理到 `http://localhost:8000`。

> 若暂不需要 IP 地理定位，可省略 geoipupdate 启动步骤，国家字段会显示为 null。

### 测试

```bash
make test
```

> 当前单元测试不依赖 PostgreSQL 与 Redis；集成测试需要在 `docker-compose up -d db redis` 之后运行。

## 17. 后端实现状态

- [x] FastAPI 项目结构与配置
- [x] PostgreSQL + Redis + Alembic 配置
- [x] 多租户域名模型（domains / user_domains）
- [x] 用户认证（JWT + bcrypt）与角色权限
- [x] 短链 CRUD、目标 URL、访问规则、client 授权
- [x] 短链跳转、访问控制、日志记录
- [x] 日志查询与日统计接口
- [x] IP 黑名单
- [x] 基于 Redis 的限流（开发环境可关闭）
- [x] 单元测试
- [ ] 前端 React 界面
- [ ] 集成测试覆盖主要接口
