# MaxMind Insights 与代理情报设计

## 1. 目标与范围

本阶段为短链重定向增加按需匿名代理识别。国家判断继续使用本地
GeoLite2 Country 数据库；只有代理状态会改变访问规则或命中规则时，才调用
MaxMind GeoIP Insights。

本阶段必须同时满足：

- 控制付费查询次数，不在每次访问时无条件调用 Insights。
- Bot 不调用 Insights，固定按代理处理。
- Redis 或 MaxMind 不可用时使用三态结果和 fail-open，不把未知伪装成非代理。
- 每次访问都能从访问日志解释采用了什么代理事实以及失败原因。
- 外部网络请求不占用数据库行锁或长事务。
- 不新增永久增长的 IP 情报表。

本阶段不实现管理后台余额页面、minFraud、自定义风险评分、后台重试队列、
日志导出、Phase 7 审计事件或 CI/CD。

## 2. 核心决策

采用同步按需查询和独立代理情报编排器，不把 Redis、MaxMind HTTP 或错误映射
直接写入重定向服务。

不采用以下方案：

- 重定向服务直接访问 Redis 和 MaxMind：会让已经承担规则、目标和日志事务的
  服务继续膨胀。
- 后台异步查询：第一次请求无法应用代理规则，不符合当前业务目标。

代理状态统一为三态：

- `true`：检测为代理。
- `false`：成功检测为非代理。
- `null`：未查询或查询失败，结果未知。

任何未知状态都不得写成 `false`。

## 3. 组件边界

### 3.1 MaxMind 适配器

`app/integrations/maxmind/insights.py` 只负责：

- 使用账号 ID 和许可证密钥调用 GeoIP Insights。
- 应用总超时。
- 解析当前 `anonymizer` 对象。
- 将供应商异常映射为稳定的内部错误。
- 返回不包含供应商对象的规范化结果。

适配器不知道短链、访问规则、Redis 键、缓存期限或 fail-open 策略。
异步 MaxMind 客户端按应用进程复用，并由应用 lifespan 统一关闭；不得为每次
访问创建新的连接池。

MaxMind 当前将匿名网络字段放在 `anonymizer` 对象，并将 `traits` 中的同名字段
标记为兼容用途的弃用字段，因此新实现只读取 `anonymizer`：

- [官方 Anonymizer 响应文档](https://dev.maxmind.com/geoip/docs/web-services/responses/#anonymizer)
- [官方错误合同](https://dev.maxmind.com/geoip/docs/web-services/responses/#errors)

### 3.2 代理情报编排器

`app/features/redirect/proxy_intelligence.py` 负责：

- 判断功能是否启用。
- Bot 固定策略。
- Redis 成功缓存、错误缓存、全局熔断和防击穿锁。
- 决定是否调用 MaxMind。
- 将所有路径收敛为不可变 `ProxyAssessment`。
- Redis 或 MaxMind 故障时执行 fail-open。

建议的稳定接口：

```python
@dataclass(frozen=True)
class ProxyAssessment:
    is_anonymous: bool | None
    proxy_types: tuple[str, ...]
    status: ProxyCheckStatus
    source: ProxySource | None
    error_code: ProxyErrorCode | None
```

编排器接收规范化 IP、平台和 `required` 标志。是否需要代理结果由业务规则层
计算；编排器不读取数据库规则。

### 3.3 重定向服务

`app/features/redirect/service.py` 继续拥有：

- 域名与短链解析。
- IP 黑名单。
- 国家、平台和 Referer 免费条件。
- 活动规则查询与稳定优先级。
- 代理与非代理规则预演。
- 三态决策、目标选择和访问日志事务。

Router 仍只负责 HTTP 输入、依赖注入和 302 响应，不接触 Redis 或 MaxMind。

## 4. 请求数据流

每次重定向按以下顺序执行：

1. 解析可信客户端 IP、UA、国家和 Referer。
2. 查询 IP 黑名单。命中黑名单时直接进入阻止流程，不调用 Insights。
3. 读取活动规则，并分别以“检测为非代理”和“检测为代理”预演规则。
4. 比较两次预演的动作和命中规则 ID。
5. 动作和命中规则都相同时，传入 `required=false`，跳过 Insights。
6. Bot 直接返回 `assumed_bot + true`，不访问 Redis 结果缓存或 MaxMind。
7. 只有动作或命中规则会变化时，才查询 Redis 或 Insights。
8. 获得三态 `ProxyAssessment` 后重新确认活动短链和规则。
9. 计算最终动作，选择目标并写入访问日志。
10. 即使最终返回 403 或 404，也先提交解释日志。

只比较 allow/deny 不足以决定是否跳过查询；当动作相同但命中规则不同，仍需
查询，以免记录错误的规则快照。

## 5. 三态规则语义

### 5.1 已知状态

检测为代理或非代理时，只用对应状态执行一次规则匹配。Bot 等价于已知代理，
同时仍必须满足 `client_requirement=bot|any` 和
`proxy_requirement=proxy|any`。

### 5.2 未知状态与 fail-open

未知状态不直接转换为 `false`。规则引擎分别计算代理和非代理结果：

- 一边允许、一边拒绝：选择允许。
- 两边都允许：选择稳定优先级更高的允许规则。
- 两边都拒绝：维持拒绝。
- 默认动作参与两次正常规则计算，不获得额外优先级。

黑名单独立于代理情报，始终优先阻止。国家、UA、Referer、Bot 等免费条件仍
正常生效。fail-open 只保证代理检测失败本身不能成为拒绝原因。

## 6. 配置合同

新增配置：

```text
MAXMIND_INSIGHTS_ENABLED=false
MAXMIND_ACCOUNT_ID=
MAXMIND_LICENSE_KEY=
MAXMIND_TIMEOUT_SECONDS=1.5
```

规则：

- 默认关闭，开发环境无需付费凭据。
- 只有显式启用时，账号 ID 和许可证密钥才是必填配置。
- 启用但缺失凭据属于启动配置错误，不进入运行时 fail-open。
- 超时必须为有限正数。
- public summary、日志、异常、文档和测试快照不得包含许可证密钥。
- Insights 不作为 readiness 阻断依赖。
- 单次调用不自动重试，避免放大响应时间和费用。

## 7. MaxMind 结果归一化

只使用 `anonymizer.is_anonymous` 作为供应商的匿名结论，并记录值为 true 的
下列类型：

```text
anonymous_vpn
hosting_provider
public_proxy
residential_proxy
tor_exit_node
```

不存在的布尔字段按 false 处理。`proxy_types` 只包含允许的稳定值，排序固定，
不保存 provider 名称、置信度、网络范围或完整供应商响应。

Insights 返回的国家字段不覆盖本地 GeoLite2 国家结果，避免同一次请求存在两套
国家规则语义。

## 8. Redis 缓存、熔断与并发

### 8.1 键和期限

```text
geoip:insights:v1:{canonical_ip}       成功结果，86400 秒
geoip:insights:error:v1:{canonical_ip} 临时错误，120 秒
maxmind:insights:disabled:v1           全局熔断
geoip:insights:lock:v1:{canonical_ip}  防击穿锁，5 秒
```

检测为代理和检测为非代理都缓存 24 小时。成功缓存只保存版本、三态布尔值和稳定
类型列表，不保存凭据或原始响应。

### 8.2 熔断规则

- `INSUFFICIENT_FUNDS`、认证失败、无服务权限：全局熔断 30 分钟。
- HTTP 429：全局熔断 60 秒。
- 超时、5xx、响应格式错误、IP 未找到：按 IP 缓存错误 2 分钟。
- 功能关闭：不写 Redis，直接返回 `disabled` 错误事实。
- IP 无法解析或不是公网地址：不调用 MaxMind，分别返回 `invalid_ip` 或
  `non_global_ip`。
- Redis 不可用：不绕过缓存调用付费服务，立即返回
  `redis_unavailable` 并 fail-open。

### 8.3 防击穿

同一 IP 缓存未命中时，使用 `SET NX EX` 取得 5 秒锁。只有持锁请求可以调用
MaxMind。未取得锁的请求短暂等待并重读成功或错误缓存；仍未命中时返回未知，
错误码为 `lookup_contended`，不得自行再调用 MaxMind。

锁必须以不可猜测令牌持有，并通过“值相等才删除”的原子操作释放，不能删除
其他请求后来取得的锁。

## 9. 错误合同与访问日志

新增可空字段：

```text
access_logs.proxy_error_code VARCHAR(32)
```

稳定值：

```text
disabled
redis_unavailable
auth_failed
insufficient_funds
permission_denied
rate_limited
timeout
upstream_error
invalid_response
ip_not_found
invalid_ip
non_global_ip
lookup_contended
```

数据库使用命名 CHECK 约束。历史行回填为 null，不新增 `ip_intelligence` 表。

访问日志组合如下：

| 路径 | status | is_anonymous | source | error_code |
|---|---|---:|---|---|
| 不需要查询 | skipped | null | null | null |
| Bot | assumed_bot | true | assumed_bot | null |
| 成功缓存 | cached | true/false | maxmind_insights | null |
| 实时成功 | checked | true/false | maxmind_insights | null |
| 任意不可用 | error | null | 按实际来源或 null | 稳定错误码 |

应用结构化日志记录状态、耗时、缓存命中和稳定错误码，但不记录许可证密钥，也
不记录 MaxMind 返回的可变人类错误文本。

新增迁移必须追加在当前 Alembic head 之后。升级添加字段和 CHECK；历史数据不
伪造错误。降级在存在非空 `proxy_error_code` 时先以净化错误拒绝，避免静默
丢失解释数据。应用可先关闭开关或回滚代码，而不必立即降级数据库。

## 10. 事务与一致性

初始域名、短链和规则读取使用短事务。完成预演后结束读事务，再访问 Redis 和
MaxMind，避免外部网络延迟占用数据库行锁或长事务。

取得代理事实后，在最终事务中重新确认：

- 域名和短链仍活动。
- 活动规则和稳定优先级。
- 目标 URL 仍可用。

最终规则、目标快照与访问日志使用现有锁语义和同一提交边界。Redis 缓存不是
数据库事务的一部分。若 MaxMind 已返回有效结果后缓存写入才失败，本次请求仍
使用该有效结果并记录为 `checked`；结构化应用日志记录缓存写失败。缓存写失败
不能把已知结果改成未知，也不能阻止访问日志提交。

在极小概率的规则并发修改中，最终规则若不再需要代理事实，可以忽略已取得的
事实；若最终规则新近开始需要代理事实而本次 assessment 为未知，则按既定
fail-open 处理，不在持锁事务内再次访问外部服务。

## 11. 测试与验收

### 11.1 单元测试

- 配置默认关闭、启用时凭据必填、密钥不出现在 public summary。
- 官方 `anonymizer` 响应到稳定类型的规范化。
- 认证、余额、权限、429、超时、5xx、无效响应和未找到映射。
- Bot 优先且不调用 MaxMind。
- 规则动作和命中规则相同时跳过查询。
- 未知状态双分支 fail-open 和稳定规则选择。

### 11.2 Redis 集成测试

- 检测为代理和非代理均缓存 24 小时。
- 临时错误缓存 2 分钟。
- 余额、认证和权限熔断 30 分钟。
- 429 熔断 60 秒。
- 同一 IP 并发请求只调用一次 MaxMind。
- 锁令牌不匹配时不得删除锁。
- Redis 故障时不得调用 MaxMind。

Redis 测试使用唯一键前缀并精确清理测试键，不执行 `FLUSHDB`。

### 11.3 PostgreSQL 与重定向测试

- `proxy_error_code` 升级、CHECK、历史 null 和安全降级。
- skipped、assumed_bot、cached、checked、error 的完整日志事实。
- 一侧允许即放行、两侧拒绝才拒绝、黑名单优先。
- 403/404 前访问日志已提交。
- MaxMind 等待期间不持有规则或目标行锁。
- 规则或目标并发删除后快照与外键仍符合现有合同。

迁移测试继续使用 UUID 命名临时数据库，绝不对 `shorturl_test` 运行 Alembic。

### 11.4 部署验收

- `.env.example` 只包含空凭据和关闭开关。
- Compose 能展开新增配置。
- readiness 不调用 MaxMind。
- 关闭功能时现有短链行为保持可部署。
- 聚焦测试、完整串行测试、唯一 Alembic head、线性 history、
  `alembic check`、Compose、compileall 和 diff check 全部通过。

测试不调用真实付费 MaxMind 端点；使用官方响应形状的固定 fixture 和可控适配器。

## 12. 发布与回退

1. 先部署数据库字段和应用代码，保持 `MAXMIND_INSIGHTS_ENABLED=false`。
2. 验证现有短链、Bot、规则、访问日志和 readiness 行为。
3. 在生产服务器的私有 `.env` 配置凭据并显式开启。
4. 观察 checked、cached、error、调用耗时和 MaxMind 返回的剩余额度指标。
5. 异常时关闭开关；请求立即进入可解释的 fail-open，不需要回滚数据库。

Phase 7 再处理管理审计事件和完整生产运维收尾；本阶段不提前实现。
