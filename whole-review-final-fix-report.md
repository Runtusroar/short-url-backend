# 最终审查修复报告

本次仅处理最终审查标出的 3 个 Important 与 1 个 Minor 项，不包含 single-flight 建议。

## 修复内容

1. `/api/auth/login-cookie` 与 `/api/auth/me` 复用同一个当前用户响应构建器。子账户均返回按 `domain_id` 排序的授权列表；管理员始终返回空列表，且不查询授权表。
2. Compose 的 `app` 服务显式透传 `PUBLIC_SHORT_URL_SCHEME`，`.env.example` 记录本地默认值与生产 HTTPS 要求。生产环境渲染后的 Compose 配置可被 `Settings` 接受。
3. `scripts/create_domain.py` 复用 `normalize_dns_hostname`：尾点会标准化，非法输入会写入 stderr 并以状态码 1 退出。
4. `scripts/create_admin.py` 在同一数据库会话与提交中，将已有子账户提升为管理员时删除其所有 `UserDomainAccess` 记录。

## 回归覆盖

- cookie 登录响应与 `/me` 的子账户响应严格一致；
- Compose 生产环境变量契约；
- 域名脚本的尾点标准化与非法输入失败路径；
- 管理员提升后的授权记录清理。

## 验证

- `uv run pytest -q tests/api` — 146 passed
- `uv run pytest -q` — 333 passed
- `make test` — 333 passed
- `uv run python -m compileall -q app scripts`
- `APP_ENV=production ... docker compose config --format json`
- `git diff --check`
