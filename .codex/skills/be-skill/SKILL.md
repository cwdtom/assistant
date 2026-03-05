---
name: be-skill
description: 从现有后端项目抽取可复用 skill 包（SKILL.md + references），用于沉淀稳定流程、模板与校验规范。
---

# be-skill

用于把“现有项目实现”沉淀成一个可复用的 Skill 包，目标是让其他项目按同一套指引稳定复现能力。

## 何时使用

- 用户要求“把当前项目做成 skill”或“抽取可复用实现”。
- 你需要交付可直接落盘的 skill 目录，且输出路径固定为项目根目录 `skills/{skill-name}`。

## 输入前置清单

在开始生成前，先确认这些信息已明确；若缺失，先补充再继续：

- 技能目标：要复用哪些能力，解决什么问题。
- 适用范围：目标框架、运行环境、依赖边界。
- 成果边界：哪些内容 in-scope，哪些 out-of-scope。
- 验收方式：至少包括结构检查与最小验证步骤。

## 执行流程

### 1) 收集项目上下文

- 查看当前分支近期提交，理解真实演进和关键改动。
- 定位核心实现文件、配置文件、测试样例、运行命令。
- 提取“可复用能力清单”：输入、输出、失败场景、依赖约束。

### 2) 设计 skill 目录

生成路径约束：

- 生成结果必须写入项目根目录：`skills/{skill-name}`。
- 不要把“生成出来的 skill”写到 `.codex/skills/`。

按以下最小结构组织：

```text
skills/{skill-name}/
├── SKILL.md
└── references/
    ├── skill-template.md
    ├── reference-template.md
    ├── quality-checklist.md
    ├── official-docs-fallback.md  # 官方文档索引
    └── official-docs/
        ├── {required-capability-1}.md
        ├── {required-capability-2}.md
        └── {optional-capability}.md

# 可选（仅目标平台为 OpenAI/Codex 时）
└── agents/
    └── openai.yaml
```

约束：

- `SKILL.md` 只放核心流程，不堆大段细节。
- 细节模板、接口样例、检查项放 `references/`。
- 禁止引用 skill 目录外的路径或文件。
- `official-docs/` 不允许写死依赖名称，需按当前技能实际依赖动态落盘并在索引登记。

### 3) 编写 SKILL.md 主流程

- 必须有 frontmatter：`name`、`description`。
- 必须包含可执行步骤，顺序清晰，可直接落地。
- 初始化步骤要明确默认值和“何时询问用户”。
- 若技能包含任何依赖第三方 API 凭据（如 `apiKey`/`clientSecret`）的能力，必须在“何时询问用户”中显式要求补充对应凭据，且注明未提供时停止该能力实现（禁止默认 mock）。
- 凡是“必须由用户提供”的信息（不仅限 API 凭据），反问时必须同步给出“如何获取”的建议或链接（至少一个）；优先提供 skill 内文档路径，其次提供官方 URL。
- 凡是命中“必须由用户提供”的信息，必须立即暂停后续流程并等待用户输入；在信息缺失状态下禁止继续实现、猜测填充或临时兜底。
- 输出步骤必须规定交付物和最小验证命令。

写作方式：

- 优先“动作 + 结果”句式，避免纯概念描述。
- 保留必要约束，不写与任务无关内容。
- 多方案场景只保留选择标准，把细节下沉到 `references/`。

### 4) 编写 references

- 以“每个功能/接口一份文档”组织，可独立读取。
- 每份文档建议包含：适用条件、输入输出、实现步骤、边界与失败处理、验证方式。
- 每份“功能/接口”文档必须包含至少一段代码示例（使用 fenced code block，如 `ts/js/bash`）。
- 官方文档按“一个依赖/能力一个 md 文件”维护在 `references/official-docs/`。
- `references/official-docs-fallback.md` 仅作为索引，列出 required/optional 文档文件路径。
- 其他文档引用索引或具体官方文档文件路径，不重复拷贝内容。
- 官方文档内容应由 agent 根据官方来源提炼（链接 + 能力用途 + 关键约束），不要求用户先粘贴原文。

模板与检查规则直接复用：

- `references/skill-template.md`
- `references/reference-template.md`
- `references/quality-checklist.md`
- `references/official-docs-fallback.md`

### 5) 按目标平台补充元数据（可选）

- 默认不生成 `agents/openai.yaml`。
- 仅当“目标运行平台明确为 OpenAI/Codex/ChatGPT 技能体系”时，才生成 `agents/openai.yaml`。
- 若生成 `openai.yaml`：
  - `display_name`：简洁表达用途。
  - `short_description`：一句话描述目标场景。
  - `default_prompt`：直接触发主流程，避免空泛描述。
  - 保持与 `SKILL.md` 行为一致，不写未实现能力。
- 非 OpenAI 平台使用对应平台的元数据文件，不要套用 `openai.yaml`。

### 6) 质量校验（必做）

至少完成以下检查：

- 文件结构完整：`SKILL.md`、`references/`（`agents/openai.yaml` 仅在 OpenAI/Codex 目标下可选）。
- `SKILL.md` 有 frontmatter，且流程可执行。
- `references/` 覆盖模板、质量清单、官方文档兜底。
- `references/official-docs-fallback.md` 中列出的路径与 `references/official-docs/*.md` 实体文件一一对应。
- 功能/接口类 reference 文档均包含代码示例，且可直接复用或改造。
- 若包含依赖第三方 API 凭据的能力，文档中必须明确：对应凭据为条件必需，且无 mock/console/空实现兜底。
- 若包含“必须由用户提供”的输入，文档中必须明确：反问时同步提供获取建议或参考链接（至少一个）。
- 若包含“必须由用户提供”的输入，文档中必须明确：缺失时必须暂停全部后续流程并等待用户输入后再继续。
- 若交付内容涉及新增页面或组件，文档中必须明确：视觉风格需与原项目保持一致，禁止引入冲突的独立风格。
- 无 skill 目录外引用（路径合规）。
- 调用检查脚本：`bash .codex/skills/be-skill/scripts/quick-check.sh <skill-name>`。
- 交付说明包含：变更文件、验证结果、剩余风险。

### 7) 前端一致性约束（条件触发）

- 若技能产物包含新增页面或新增组件（例如登录页、表单页、导航入口组件等），必须复用原项目现有视觉体系（样式 token/class/组件/排版规则）。
- 禁止交付与原项目视觉语言冲突的独立 UI 风格。
- 验收时必须显式检查并记录“页面/组件风格一致性”结果。

## 输出要求

- 先给结果摘要，再给改动清单和验证结论。
- 输出中明确 skill 路径（必须是 `skills/{skill-name}`）、主要文件用途、后续维护建议。
- 若发现需求与现状冲突，先记录冲突与影响，再给调整建议。
