# Feature Reference Template

用于描述某个“可复用功能/接口”的标准模板，建议每个功能单独一份文档。
说明：接口文档必须包含至少一段 fenced code block 代码示例。

## 1. Feature

- Name:
- Purpose:
- Applicable scenarios:
- Out of scope:

## 2. Contract

- Input:
- Output:
- Validation:
- Error mapping:

## 3. Dependencies

- Runtime/library dependencies:
- Environment variables:
- External services:
- Skill output path: `skills/<skill-name>/`
- Constraint（当外部能力依赖 `apiKey` 或同类密钥时）: 必须先收集所需凭据再实现；禁止默认回退到 mock/console 发送。
- Constraint（当存在“必须由用户提供”的输入时）: 反问必须同步给出“如何获取”的建议或参考链接（至少一个，优先引用本 skill 内 `references/official-docs*.md` 路径）。
- Constraint（当存在“必须由用户提供”的输入时）: 一旦信息缺失，必须暂停全部后续流程，等待用户输入后再继续；禁止猜测填充或临时兜底。
- Constraint（当交付涉及新增页面/组件时）: 新页面和组件必须与原项目视觉风格保持一致，复用既有样式体系。

## 4. Implementation Steps

1. Prepare context and prerequisites.
2. Implement core flow.
3. Add failure handling and retries/rollback if needed.
4. Add/adjust tests.
5. Verify with concrete commands.

## 5. Edge Cases

- Edge case 1:
- Edge case 2:
- Security/privacy constraints:

## 6. Verification

- Unit tests:
- Manual checks:
- Expected logs/events:

## 7. Official Docs Fallback

- Official docs index: `references/official-docs-fallback.md`
- Official docs files: `references/official-docs/<integration>.md` (one file per integration/capability)
- If new official sources are needed, add a new file under `references/official-docs/` and register it in the index.
- Prefer classifying docs as required/optional in the index for execution-time trimming.
- Notes on version compatibility:

## 8. Example Code

```ts
type RegisterInput = { email: string; password: string };

export async function registerPassword(input: RegisterInput) {
  const email = input.email.trim().toLowerCase();
  if (input.password.length < 6) {
    throw new Error("PASSWORD_TOO_SHORT");
  }
  // replace this stub with real repository/service implementation
  return { email, registered: true };
}
```
