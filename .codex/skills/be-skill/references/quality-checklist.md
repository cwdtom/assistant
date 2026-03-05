# Quality Checklist for Generated Skill

在交付 skill 前，逐项检查并记录结果。

## A. Structure

- [ ] 存在 `SKILL.md`。
- [ ] 存在 `references/` 且包含模板与官方文档兜底。
- [ ] 目录命名符合 `lowercase-hyphen`。
- [ ] 生成结果位于项目根目录 `skills/<skill-name>/`。
- [ ] 若目标平台为 OpenAI/Codex，才包含 `agents/openai.yaml`；非 OpenAI 目标不生成该文件。

## B. SKILL.md Quality

- [ ] frontmatter 包含 `name` 与 `description`。
- [ ] 步骤是可执行动作，不是纯概念描述。
- [ ] 包含输入前置、实施步骤、验收标准。
- [ ] 明确“何时询问用户、何时可默认”。
- [ ] 若包含依赖第三方 API 凭据（如 `apiKey`）的能力，已明确需要反问并收集对应凭据。
- [ ] 若存在“必须由用户提供”的信息，已明确反问时同步提供获取建议或参考链接。
- [ ] 若存在“必须由用户提供”的信息，已明确缺失时必须暂停全部后续流程并等待用户输入后再继续。
- [ ] 若交付涉及新增页面/组件，已明确必须与原项目视觉风格保持一致。

## C. References Quality

- [ ] 每个功能/接口单独文档，边界清晰。
- [ ] 含输入输出、失败场景、验证方式。
- [ ] 每个功能/接口文档至少包含一段 fenced code block 代码示例。
- [ ] 关键决策有官方文档兜底来源。
- [ ] 官方文档按当前技能实际依赖拆分为多个 md（禁止写死特定依赖名）。
- [ ] `references/official-docs-fallback.md` 包含 required/optional 官方文档文件索引路径。
- [ ] 索引文件中列出的路径与 `references/official-docs/*.md` 实体文件一致。
- [ ] 官方文档内容由 agent 基于官方来源提炼，不依赖“用户粘贴原文”。

## D. Compliance

- [ ] 未引用 skill 目录外文件。
- [ ] 未引入不必要的额外文档（README/CHANGELOG 等）。
- [ ] 与现有项目约束一致（依赖、环境变量、日志策略）。
- [ ] 若包含依赖第三方 API 凭据的能力，已明确凭据为条件必需，且未使用 mock/console/空实现兜底。
- [ ] 对强制用户补充信息的场景，交付文档已给出可执行获取建议或链接（优先 skill 内文档路径）。
- [ ] 对强制用户补充信息的场景，交付流程已在缺失信息时暂停并等待用户输入，不存在跳过或猜测填充。
- [ ] 若交付包含页面/组件改动，页面与组件风格与原项目一致，未引入冲突视觉样式。

## E. Quick Script

```bash
bash .codex/skills/be-skill/scripts/quick-check.sh <skill-name>
```

说明：
- `<skill-name>` 对应 `skills/<skill-name>/` 目录名。
- 脚本会执行结构检查、路径合规扫描、frontmatter 必要字段检查、接口文档代码示例检查。

如果某项未通过，先修复再交付，不带病提交。
