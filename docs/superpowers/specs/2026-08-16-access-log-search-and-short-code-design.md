# 访问日志组合查询与短码规范设计

## 1. 背景与目标

当前 `GET /api/logs` 只支持短链 UUID、日期和 `offset` 分页。它还存在两个需要同时解决的问题：

1. 日志持续写入时，offset 分页可能重复或遗漏记录，深分页成本也会持续增加。
2. operator/client 在未指定 `short_link_id` 时只按域名过滤，查询边界没有收敛到自己拥有或获授权的短链。

本阶段目标：

- 支持按短码、短链名称、域名自然日期、国家和结果组合筛选。
- 使用稳定、不可篡改的游标分页替换 offset 分页。
- 让列表查询在所有筛选组合下都遵守短链级权限。
- 将短码统一为小写存储，并让访问与查询不受用户输入大小写影响。
- 为新查询补齐 PostgreSQL 索引和 schema preflight 契约。

本阶段不修改 `/api/logs/daily` 和 `/api/logs/daily-summary`，不实现导出、自由文本日志搜索、总数统计、Elasticsearch，也不实现 MaxMind Insights 查询。

## 2. 已确认的产品决策

### 2.1 筛选字段保持独立

短码和短链名称使用两个独立参数，而不是一个含义模糊的统一关键词：

- `short_code`：短码前缀匹配。
- `name`：短链当前名称的不区分大小写包含匹配。

所有不同种类的筛选条件使用 AND 组合。同一种类的多值条件使用 OR 组合，例如 `country=CN&country=US` 表示中国或美国。

### 2.2 日期使用域名时区

`date_from` 和 `date_to` 都表示目标域名所在时区的自然日期，并且都包含当天。查询直接使用写日志时已经按域名时区计算的 `access_date`，不把日期重新解释为 UTC。

必须验证 `date_from <= date_to`。不提供日期时返回当前筛选条件下的最新日志，不施加隐藏日期范围。

### 2.3 使用稳定游标分页

列表固定按以下顺序排列：

```sql
ORDER BY accessed_at DESC, id DESC
```

游标保存上一页末尾记录的 `accessed_at` 和 `id`。下一页使用 tuple keyset 条件：

```sql
WHERE (accessed_at, id) < (:last_accessed_at, :last_id)
```

服务查询 `limit + 1` 条记录判断是否还有下一页，不执行昂贵的 `COUNT(*)`。

### 2.4 短码统一小写

虽然 URL path 在技术上可以区分大小写，本产品不使用这种差异：

- 数据库存储的短码全部为小写。
- 自动短码使用 `a-z0-9`，长度从 6 位调整为 7 位。
- 自动生成使用 `secrets.choice`，不再使用 `random.choices`。
- 自定义短码先执行 `strip().lower()`，再按 `[a-z0-9_-]{3,32}` 校验。
- 重定向和日志查询收到短码后都规范化为小写。
- `/Promo7` 与 `/promo7` 访问同一条短链，canonical URL 使用小写形式。

7 位 base36 共有 `36^7 = 78,364,164,096` 种组合，高于当前 6 位 base62 的 `62^6 = 56,800,235,584` 种组合。

## 3. HTTP API 合同

### 3.1 请求

```http
GET /api/logs
```

查询参数：

| 参数 | 类型 | 规则 |
|---|---|---|
| `short_link_id` | UUID，可选 | 保留现有精确短链筛选 |
| `short_code` | string，可选 | trim、转小写，按 `[a-z0-9_-]{1,32}` 校验并作字面量前缀匹配 |
| `name` | string，可选 | trim 后长度 1–128，忽略大小写的包含匹配 |
| `date_from` | date，可选 | 域名本地自然日期，包含当天 |
| `date_to` | date，可选 | 域名本地自然日期，包含当天 |
| `country` | repeated string，可选 | ISO 3166-1 alpha-2，两位大写，可多选 |
| `result` | repeated enum，可选 | `allowed`、`denied`、`blocked`，可多选 |
| `limit` | integer | 默认 50，最小 1，最大 200 |
| `cursor` | string，可选 | 第一页不传，后续传上一页的 `next_cursor` |
| `domain_id` | UUID，可选 | 仅管理员可切换到其他活动域名 |

示例：

```http
GET /api/logs?short_code=promo&name=八月&country=CN&country=US&result=allowed&date_from=2026-08-01&date_to=2026-08-07&limit=50
```

### 3.2 响应

现有裸数组响应改为分页对象：

```json
{
  "items": [
    {
      "id": "00000000-0000-0000-0000-000000000000",
      "short_link_id": "00000000-0000-0000-0000-000000000000",
      "short_code": "promo7x",
      "short_link_name": "八月推广",
      "domain_id": "00000000-0000-0000-0000-000000000000",
      "result": "allowed",
      "country": "CN",
      "accessed_at": "2026-08-16T02:30:00Z"
    }
  ],
  "next_cursor": "opaque-signed-cursor",
  "has_more": true
}
```

`items` 中继续返回当前 `AccessLogResponse` 的全部解释性字段，并增加当前短链的 `short_code` 和 `short_link_name`。短链名称是管理元数据，展示和筛选使用短链的当前名称；它不是访问发生时的历史快照。

最后一页返回：

```json
{
  "items": [],
  "next_cursor": null,
  "has_more": false
}
```

实际最后一页的 `items` 可以非空；只要没有后续记录，`next_cursor` 就为 null，`has_more` 为 false。

### 3.3 游标格式与校验

游标是 URL-safe Base64 编码的不透明值，内部包含：

- 格式版本。
- 最后一条记录的 UTC `accessed_at`。
- 最后一条记录的 UUID `id`。
- 当前规范化筛选条件的摘要。
- 使用应用 `SECRET_KEY` 计算的 HMAC-SHA256 签名。

服务必须使用恒定时间比较验证签名，并严格解析时间、UUID 和版本。以下情况返回 `400 INVALID_CURSOR`：

- Base64、结构、时间或 UUID 无效。
- 签名不匹配。
- 游标版本不支持。
- 游标中的筛选摘要与当前请求不一致。

游标不携带权限授权。每一页都重新执行当前用户、域名和短链权限检查。

## 4. 权限模型

所有筛选条件必须在可访问短链集合之内执行，不能先查出其他用户的日志再在应用层过滤。

- `admin`：可查看有效域名下的全部日志；显式 `domain_id` 可选择其他有效域名。
- `operator`：只能查看 `short_links.owner_id == current_user.id` 的日志。
- `client`：只能查看存在 `short_link_permissions(user_id, short_link_id)` 授权的短链日志。

未传 `short_link_id` 时同样应用上述边界。显式指定无权访问的 `short_link_id` 保持明确的 403；宽泛筛选只返回可访问集合，不泄露其他短链是否存在。

软删除短链仍保留物理行和历史日志，因此其拥有者或仍持有授权的 client 可以查询历史记录。重定向仍排除软删除短链。

## 5. 查询与服务结构

Router 只负责：

- FastAPI 查询参数提取。
- 请求模型验证与依赖注入。
- 将 service 结果转换为分页响应。

Service 负责：

1. 解析管理员有效域名。
2. 生成角色对应的可访问短链 SQL 条件。
3. 规范化筛选条件并生成稳定筛选摘要。
4. 验证并解析游标。
5. 在一条 SQL 查询中 join `short_links`、应用权限与筛选条件。
6. 执行 `(accessed_at, id)` keyset 条件和 `limit + 1` 查询。
7. 生成下一页签名游标。

不得把 SQLAlchemy query builder、权限判断或游标签名逻辑放入 Router。

## 6. 数据库迁移与索引

在当前唯一 Alembic head `a73f0b9d4216` 之后增加一个 append-only revision。不得修改既有 revision。

### 6.1 短码迁移

任意写操作前执行只读 preflight：

- 拒绝 trim 后为空或不符合允许字符/长度的旧短码。
- 拒绝同一域名内 `lower(btrim(short_code))` 后发生冲突的行。
- 错误只报告稳定 invariant，不输出短码或行内容。

通过 preflight 后：

1. 将已有短码更新为 `lower(btrim(short_code))`。
2. 增加命名检查约束，保证存储值已经小写且符合 `[a-z0-9_-]{3,32}`。
3. 保留 `(domain_id, short_code)` 唯一约束。

因为入口查询统一转小写且冲突会在写前拒绝，已有混合大小写 URL 在迁移后仍能解析到原短链。

### 6.2 搜索索引

- 启用 PostgreSQL `pg_trgm` 扩展。
- 新增 `idx_short_links_name_trgm`：`GIN (lower(name) gin_trgm_ops)`，支持包含搜索。
- 新增 `idx_short_links_domain_code_pattern`：`(domain_id, short_code varchar_pattern_ops)`，支持域名内短码字面量前缀搜索。
- 用包含 `id DESC` 的同名定义替换以下四个日志索引：
  - `idx_access_logs_domain_accessed_at (domain_id, accessed_at DESC, id DESC)`。
  - `idx_access_logs_link_accessed_at (short_link_id, accessed_at DESC, id DESC)`。
  - `idx_access_logs_domain_result_accessed_at (domain_id, result, accessed_at DESC, id DESC)`。
  - `idx_access_logs_domain_country_accessed_at (domain_id, country, accessed_at DESC, id DESC)`。
- `idx_access_logs_link_access_date` 和 `idx_access_logs_link_client_ip_dedup` 保持当前定义，不为游标改写。

索引名称、列顺序、唯一性、DESC、operator class 和 predicate 必须进入现有 schema preflight 与独立 final schema contract。`pg_trgm` 扩展本身也属于 postflight 必须存在的合同。迁移 downgrade 恢复四个日志索引的 Phase 4 定义，移除本 revision 拥有的短链搜索索引和检查，但不主动删除可能被其他对象共享的 `pg_trgm` 扩展。短码原始字母大小写无法无损恢复，downgrade 后数据继续保持小写。

## 7. 错误和边界行为

- `date_from > date_to`：请求验证错误。
- 空白 `short_code` 或 `name`：请求验证错误，而不是解释为未筛选。
- 非法国家代码或结果：请求验证错误。
- 重复国家或结果值：规范化去重，不改变筛选摘要。
- 不同排列顺序的等价多值筛选：排序后生成相同摘要，旧游标仍可继续使用。
- 名称搜索保留用户输入的内部空格，只 trim 首尾空格。
- 不返回总记录数；如果未来确实需要总数，使用独立统计接口，不阻塞日志翻页。

## 8. 测试策略

实现遵循 TDD，至少覆盖：

### 8.1 API 与权限

- 单独及组合使用短码、名称、日期、国家和结果。
- 多国家、多结果及重复值规范化。
- admin/operator/client 的宽泛查询和精确短链查询。
- operator/client 未指定短链时不能读取其他人的日志。
- 软删除短链历史日志仍可按当前短码和名称查询。
- `/daily` 和 `/daily-summary` 合同不变。

### 8.2 游标

- 相同 `accessed_at` 的多条日志按 UUID 稳定分页且无重复、无遗漏。
- 第一页返回后在顶部插入新日志，后续页仍从原末尾继续。
- 篡改签名、损坏结构、未知版本和无效时间/UUID 均被拒绝。
- 修改任意筛选条件后复用旧游标被拒绝。
- 等价的多值顺序和重复值不会错误拒绝游标。
- `limit` 不进入筛选摘要，因此翻页时可以调小 limit；排序和数据范围保持不变。

### 8.3 迁移与 schema contract

- disposable PostgreSQL 数据库验证混合大小写短码迁移、旧 URL 兼容和 collision preflight 原子失败。
- 验证新建短码只包含 7 位 `a-z0-9`，自定义短码被规范化。
- 验证 `pg_trgm`、短码检查和全部索引定义。
- 对同名错误列序、ASC 替换 DESC、错误 operator class、缺失索引做 fail-closed schema-check 测试。
- empty、Phase 4 head、Phase 5 head、downgrade/re-upgrade 路径保持一条线性迁移链。

## 9. 验收标准

- `/api/logs` 支持五类已确认筛选，并使用签名 keyset cursor。
- 宽泛日志查询不存在 operator/client 越权。
- 分页期间新增日志不会导致旧结果重复或漏项。
- 短码存储统一小写，新自动短码为 7 位 `a-z0-9`，请求大小写不影响重定向。
- 旧短码规范化冲突在迁移任何写操作前安全拒绝。
- 新索引与 ORM、真实 PostgreSQL、schema preflight 的独立字面量合同一致。
- 全量测试串行通过，Alembic 保持唯一 head，Compose 配置和生产容器依赖保持有效。
