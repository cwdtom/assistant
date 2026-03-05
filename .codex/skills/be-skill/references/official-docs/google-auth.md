# Google Identity / OAuth 2.0 (Example)

## Official Links

- https://developers.google.com/identity
- https://developers.google.com/identity/protocols/oauth2

## Capability Used

- 第三方登录（OAuth 2.0 授权码流程）或身份凭证校验。

## Integration Notes

1. 优先采用授权码模式与后端交换 token，避免在前端暴露敏感凭证。
2. 回调处理必须校验 `state`（以及启用 PKCE 时的 `code_verifier`）防止 CSRF。
3. 用户信息落库前统一做账号归一化与已存在账号合并策略。
4. 认证失败统一错误语义，避免泄露“账号是否存在”等枚举信息。

## Documentation Pattern

- 本文件是“按依赖拆分”的官方文档示例，不要求用户粘贴原文。
- 若技能实际未接入 Google Auth，可删除本文件并更新索引。
