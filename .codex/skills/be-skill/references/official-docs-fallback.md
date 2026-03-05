本文件是官方文档索引，按“一个依赖/能力一个 md 文件”维护。

使用规则：
- 具体官方文档放在 `references/official-docs/*.md`。
- 每新增一个依赖能力，就新增一个 md 文件，并在本索引登记。
- 其他文档优先引用本索引或对应官方文档文件路径。
- 推荐按 required/optional 两层列出，便于执行时做“默认能力 + 按需能力”裁剪。

官方文档索引（示例）：
- Required (always needed)
  - `references/official-docs/{required-capability-1}.md`
  - `references/official-docs/{required-capability-2}.md`
- Optional (only when needed)
  - `references/official-docs/{optional-capability}.md`
