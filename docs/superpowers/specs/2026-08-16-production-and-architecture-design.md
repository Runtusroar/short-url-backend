# 短链服务生产化与架构重构设计

## 1. 背景与目标

当前项目已经具备多域名短链、访问规则、目标 URL、访问日志、用户权限、IP 黑名单和限流能力，但开发配置、生产配置、HTTP 代理边界、数据库约束和模块职责仍处于 MVP 阶段。

本次设计目标：

1. 使用同一套代码和同一个 Docker Compose 定义运行开发与生产环境。
2. 明确 Nginx 与 FastAPI 之间的真实客户端 IP 信任边界。
3. 使用 MaxMind GeoIP Insights 按需识别匿名代理，并控制查询成本。
4. 修正数据库约束、索引、删除策略和日志审计语义。
5. 支持按短链、短链名称、时间、国家和结果组合查询访问日志。
6. 将当前扁平目录重构为基础设施与功能模块边界清晰的结构。

本阶段不引入 CI/CD、Kubernetes、Vault、日志表分区或完整 DDD。生产部署仍为单台 Ubuntu 服务器，宿主机 Nginx 反向代理 Docker 中的 FastAPI。

## 2. 环境与部署模型

### 2.1 一套代码，每台机器一份配置

Git 仓库只提交 `.env.example`。开发电脑和生产服务器各自保存一份不提交的 `.env`：

- 开发电脑：`APP_ENV=dev`
- Ubuntu 生产服务器：`APP_ENV=prod`

两台机器使用相同命令：

```bash
make check-config
make up
make migrate
make logs
make restart
make down
```

Makefile 是日常操作入口，内部统一调用 Docker Compose。生产配置以后可由 CI/CD 注入，无需改变应用配置模型。

### 2.2 应用配置

配置模型至少包含：

```dotenv
APP_ENV=dev
DATABASE_URL=postgresql+psycopg://...
REDIS_URL=redis://...
SECRET_KEY=...
GEOIP_DB_PATH=/usr/share/GeoIP/GeoLite2-Country.mmdb
COOKIE_SECURE=false
CORS_ORIGINS=http://localhost:3000
LOG_LEVEL=DEBUG
TRUST_PROXY_HEADERS=false
MAXMIND_ACCOUNT_ID=
MAXMIND_LICENSE_KEY=
MAXMIND_INSIGHTS_ENABLED=false
```

当 `APP_ENV=prod` 时，应用必须拒绝以下配置：

- 默认或过短的 `SECRET_KEY`
- `COOKIE_SECURE=false`
- `CORS_ORIGINS=*`
- 缺少数据库、Redis 等必要配置

### 2.3 Docker Compose

保留一个 `docker-compose.yml`。PostgreSQL、Redis 和 FastAPI 的宿主机端口均绑定到 `127.0.0.1`。FastAPI 使用 `127.0.0.1:18000:8000`，生产流量只能经过宿主机 Nginx。

Compose 还应：

- 修正遗留的 `asyncpg` 默认 URL，统一使用 `psycopg`
- 从 `.env` 读取数据库账号、密码和库名
- 为应用增加健康检查
- 为长期运行服务增加 `restart: unless-stopped`
- 保持现有数据卷名称，避免开发数据意外丢失

## 3. Nginx 与真实客户端 IP

生产环境只有一层 Nginx，且 FastAPI 端口仅监听宿主机回环地址。Nginx 应覆盖写入客户端地址，避免接受用户伪造的代理链：

```nginx
location / {
    proxy_pass http://127.0.0.1:18000;
    proxy_http_version 1.1;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

User-Agent 和 Referer 默认由 Nginx 透传。

应用增加唯一的客户端 IP 解析函数，供访问日志、黑名单、GeoIP 和限流共同使用：

- 开发环境默认忽略代理头，使用 `request.client.host`
- 生产环境且 `TRUST_PROXY_HEADERS=true` 时读取并校验 `X-Real-IP`
- 无效地址回退到 `request.client.host`
- 使用 Python `ipaddress` 校验 IPv4/IPv6

该信任模型的部署前提是 FastAPI 发布端口仅绑定回环地址，且不允许不受信任的容器加入应用 Docker 网络。

不得再根据 `X-Forwarded-For`、`X-Real-IP` 或 `Forwarded` 是否存在判断用户是否使用代理。

## 4. 国家与匿名代理识别

### 4.1 数据来源

- 国家：继续使用本地 `GeoLite2-Country.mmdb`
- 匿名代理：按需调用 MaxMind GeoIP Insights Web Service

GeoIP Insights 不在每次访问时无条件调用。只有代理结果会影响访问规则时才查询。

### 4.2 查询条件

处理顺序：

1. 解析真实 IP、UA、国家和 Referer。
2. 加载活动规则，先判断国家、平台、Referer、Bot 等免费条件。
3. 如果不存在会受代理状态影响的候选规则，跳过 Insights。
4. 如果 UA 被识别为 Bot，跳过 Insights，并写死为 `is_proxy=true`、`status=assumed_bot`。
5. 其他确实需要代理结果的请求先查 Redis；未命中才调用 Insights。

Bot 需要同时满足规则允许 Bot 和允许代理，才能匹配允许规则。UA 判断应优先检查 `is_bot`，避免 Bot 先被识别为 PC。

### 4.3 Redis 缓存

缓存键带版本号：

```text
geoip:insights:v1:{ip}
```

缓存内容为规范化后的代理检测字段。代理和非代理结果都缓存 24 小时。临时错误只缓存 1 至 5 分钟。

余额不足、认证错误等确定性故障使用 Redis 熔断键，避免每次访问重复请求失败服务：

```text
maxmind:insights:disabled
```

余额不足时熔断 30 分钟，并记录错误。

### 4.4 降级策略

MaxMind 超时、5xx、429、认证失败或余额不足时：

- `is_proxy=null`
- 规则按 fail-open 放行
- 日志明确记录错误类型
- 不能将未知状态伪装成 `false`

## 5. 数据模型

### 5.1 通用规范

- 状态值使用 Python `StrEnum` 和数据库 `CHECK`，不使用 PostgreSQL ENUM，以降低迁移成本。
- UUID、时间和布尔默认值同时提供 ORM 默认和数据库 `server_default`。
- 所有时间以 UTC 存储。
- IP 地址使用 PostgreSQL `INET`。
- 删除策略必须显式声明。
- 访问日志和审计日志为追加式数据，不级联删除。

### 5.2 users

- `username` 规范化为小写并保持唯一。
- `role` 限制为 `admin/operator/client`。
- 保留 `is_active`，用户删除操作改为停用，不硬删除。
- 保留 `created_at/updated_at`，补齐数据库默认值。

### 5.3 domains

- `name` 规范化为小写 Host，不允许路径和端口。
- `is_default=true` 使用部分唯一索引，保证最多一个默认域名。
- 增加 `updated_at`。
- 增加 `timezone`，默认 `Asia/Shanghai`，用于访问日期统计。
- 域名删除优先停用；存在短链或日志时禁止硬删除。

### 5.4 user_domains

- 保留 UUID 主键和 `(user_id, domain_id)` 唯一约束。
- 增加 `domain_id` 索引。
- 增加 `granted_by`，用于追踪授权来源。

### 5.5 short_links

- 将 `description` 重命名为 `name VARCHAR(128) NOT NULL`。
- `name` 是简短的人类可读名称，用于列表显示和日志关键词搜索。
- 迁移时使用原 `description`，为空时以 `short_code` 兜底。
- `default_action` 在 ORM、数据库和创建流程中统一为 `deny`。
- 增加 `deleted_at/deleted_by`，API 删除改为软删除。
- 保留 `is_active`，用于临时停用。
- 增加 `(domain_id, created_at DESC)` 和 `(domain_id, owner_id, created_at DESC)` 索引。
- 不允许删除用户或域名时级联删除短链。

### 5.6 short_link_permissions

- 保留活动授权映射和唯一约束。
- 增加 `granted_by`。
- 撤销授权时删除当前映射，同时写入审计事件。

### 5.7 target_urls

- 增加可选 `name VARCHAR(128)`，便于管理多个目标。
- `url_type` 限制为 `allowed/denied`。
- `weight >= 1`；禁用目标使用 `is_active=false`，不使用零权重表达禁用。
- 增加 `updated_at`。
- 日志保存 URL 快照，因此目标删除后仍可解释历史跳转。

### 5.8 access_rules

增加 `name VARCHAR(128) NOT NULL`，每条规则都有明确名称。

将语义含混的 `allow_bot/allow_proxy` 重构为匹配条件：

```text
action              allow / deny
client_requirement  any / human / bot
proxy_requirement   any / non_proxy / proxy
```

其他调整：

- `referer_pattern` 改为 `referer_patterns JSONB` 字符串数组
- `countries` 存储大写二位国家代码数组
- `ua_platforms` 继续使用小型 JSONB 数组
- 规则按 `priority, id` 稳定排序
- 增加 `(short_link_id, priority)` 唯一约束，保证同一短链内优先级唯一
- 增加 `created_at/updated_at`
- 增加 `(short_link_id, is_active, priority)` 索引

### 5.9 ip_blacklist

- `ip` 改为 PostgreSQL `INET`，自动规范化 IPv4/IPv6。
- `reason` 必填。
- 增加 `expires_at`，为空表示永久。
- 增加 `removed_at/removed_by/removal_reason`，移除黑名单采用状态变更而非物理删除。
- 用户只允许停用，因此 `created_by/removed_by` 使用 `RESTRICT` 并长期保留。

### 5.10 access_logs

访问日志为追加式审计记录，短链、域名、规则或目标变化不得导致历史日志删除。

`short_link_id/domain_id` 使用 `RESTRICT`，`target_url_id/matched_rule_id` 使用 `SET NULL`，并依靠快照字段保留当时语义。

保留并规范化：

```text
short_link_id
domain_id
target_url_id nullable
result
client_ip INET
country CHAR(2)
user_agent
ua_platform
referer
accessed_at
access_date
dedup_bucket
```

新增：

```text
decision_reason       # blacklist / matched_rule / default_action / no_target
matched_rule_id       # 可为空，删除规则后 SET NULL
matched_rule_name     # 当时规则名称快照
target_url_snapshot   # 当时实际目标 URL
request_host
request_method
proxy_check_status    # skipped / cached / checked / assumed_bot / error
is_anonymous          # true / false / null
proxy_types JSONB
proxy_source          # maxmind_insights / assumed_bot
```

`access_date` 表示域名时区下的业务日期，替代含义写死的 `accessed_at_plus8`。

索引：

```text
(domain_id, accessed_at DESC)
(short_link_id, accessed_at DESC)
(short_link_id, access_date DESC)
(short_link_id, client_ip, dedup_bucket)
(domain_id, result, accessed_at DESC)
(domain_id, country, accessed_at DESC)
```

暂不分区。日志达到数百万至千万级后，再评估按月分区和 BRIN 索引。

### 5.11 audit_events

新增管理审计表：

```text
id
actor_user_id nullable
action
resource_type
resource_id
reason nullable
before_data JSONB nullable
after_data JSONB nullable
created_at
```

访问日志记录访客行为；审计事件记录用户、域名、短链、规则、黑名单和授权的管理操作。

### 5.12 不新增 ip_intelligence 表

MaxMind 查询结果属于可重新获取的时效数据，保存在 Redis。每次访问实际采用的代理判断写入 `access_logs`，不维护永久增长的 IP 情报表。

## 6. 日志查询 API

日志接口支持组合过滤：

```http
GET /api/logs
    ?short_link_id=
    &keyword=
    &date_from=
    &date_to=
    &country=
    &result=
    &cursor=
    &limit=50
```

语义：

- `short_link_id`：精确短链
- `keyword`：搜索短码或短链 `name`
- `date_from/date_to`：按访问时间范围
- `country`：二位国家代码
- `result`：可重复参数或逗号分隔多选
- 默认最近 7 天
- 单次最大 90 天
- 使用 `(accessed_at, id)` 游标分页，替代大偏移量分页

短链数量较少时，关键词通过 `short_links` 关联和 `ILIKE` 完成；达到大规模后再增加 `pg_trgm` 索引。

## 7. 项目目录结构

采用“核心基础设施 + 功能模块”的混合结构，不引入完整 DDD：

```text
app/
├── main.py
├── core/
│   ├── config.py
│   ├── database.py
│   ├── security.py
│   ├── exceptions.py
│   ├── client_ip.py
│   └── rate_limit.py
├── models/
│   ├── __init__.py
│   ├── user.py
│   ├── domain.py
│   ├── short_link.py
│   ├── access_rule.py
│   ├── access_log.py
│   ├── blacklist.py
│   └── audit_event.py
├── features/
│   ├── auth/
│   │   ├── router.py
│   │   ├── schemas.py
│   │   └── service.py
│   ├── users/
│   ├── domains/
│   ├── short_links/
│   ├── redirect/
│   ├── access_logs/
│   └── blacklist/
└── integrations/
    ├── maxmind/
    │   ├── country.py
    │   └── insights.py
    └── redis.py
```

边界规则：

- Router 只处理 HTTP、依赖注入和响应转换。
- Service 负责业务流程和事务边界。
- ORM 模型集中在 `models` 包。
- 各功能的 Pydantic Schema 放在功能目录。
- 只有重复或复杂查询才创建 `queries.py`，不机械创建 Repository。
- MaxMind、Redis 等外部系统放在 `integrations`。
- 测试目录镜像功能目录。

## 8. 迁移策略

不修改已经应用的历史迁移。新增修复与重构迁移：

1. 创建 schema baseline repair，修正目标 URL 外键漂移并恢复索引。
2. 增加新字段和新表，先保持可空或提供服务器默认值。
3. 回填 `short_links.name`、访问日期等数据。
4. 转换规则语义和 IP 字段。
5. 添加非空、CHECK、唯一和外键约束。
6. 更新应用后再移除旧字段。

当前开发库数据量极小，但迁移仍需可重复执行并通过升级、降级和空库测试。生产部署前创建数据库备份。

## 9. 错误处理与可观测性

- 业务错误返回稳定错误码，不直接暴露底层异常。
- 通用异常处理器必须记录 traceback 和 request ID。
- MaxMind 调用记录状态、耗时和错误类型，但不记录密钥。
- 健康检查区分存活和就绪；就绪检查数据库、Redis，MaxMind 不作为启动阻断依赖。
- 访问规则的每次决定都能由 `decision_reason`、规则快照和代理状态解释。

## 10. 测试与验收

必须保持现有测试通过，并新增：

- dev/prod 配置校验测试
- Nginx 真实 IP 与伪造代理头测试
- Bot 优先识别并假定为代理测试
- 无代理限制时不调用 Insights
- Redis 正负结果缓存测试
- MaxMind 余额不足、认证失败、超时和熔断测试
- fail-open 测试
- 规则新语义与稳定优先级测试
- 软删除后日志保留测试
- 规则/目标修改后日志快照不变测试
- 全部日志组合筛选和游标分页测试
- 数据库 CHECK、唯一约束和关键索引检查
- Alembic 空库升级、现有库升级及 `alembic check`
- Docker Compose 配置展开和健康检查

## 11. 实施分阶段

为降低风险，按以下阶段实施并分别验证：

1. 环境配置、Compose、Makefile 和 Nginx 信任边界。
2. 目录重构，仅移动职责，不改变业务行为。
3. schema baseline repair 和关键索引。
4. 表结构、软删除、审计字段和规则语义迁移。
5. 日志查询与游标分页。
6. MaxMind Insights、Redis 缓存、Bot 策略和降级。
7. 审计事件与生产部署文档。

每个阶段独立测试并保持可部署状态，避免将目录移动、数据库迁移和业务行为变化压入同一个不可审查的提交。
