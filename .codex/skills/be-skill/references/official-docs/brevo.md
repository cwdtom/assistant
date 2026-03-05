# Brevo Transactional Email API (Example)

## Official Link

- https://developers.brevo.com/reference/sendtransacemail

## Capability Used

- 发送 transactional email（如验证码、通知邮件）。

## Integration Notes

1. 使用官方 SDK 或官方 REST API。
2. API Key 必须通过环境变量注入，禁止硬编码。
3. 业务层只做参数准备与错误映射，不把供应商 SDK 细节扩散到业务调用方。
4. 记录请求关联标识（如 `requestId`），但日志不得输出明文敏感字段。

## Error Mapping Example

- 供应商请求超时/5xx -> 映射业务错误 `SEND_FAILED`（HTTP 500）
- 参数非法/发件人未验证 -> 映射业务错误 `INVALID_PROVIDER_CONFIG`（HTTP 500/400，按业务策略）

## Documentation Pattern

- 本文件只保留“链接 + 能力用途 + 关键约束 + 错误映射”。
- 如果技能实际未使用 Brevo，可删除本文件并在索引中移除对应条目。
