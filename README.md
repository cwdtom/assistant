# CLI AI Personal Assistant (MVP)

一个中文优先的本地 CLI 个人助手，支持：
- AI 对话（DeepSeek 优先，兼容 OpenAI-compatible API）
- 待办管理
- 日程管理

## Quick Start

1. 创建虚拟环境并安装依赖
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

2. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

默认模型：
- `DEEPSEEK_MODEL=deepseek-chat`（通用对话）
- 可选 `deepseek-reasoner`（更强推理，延迟通常更高）

3. 运行
```bash
python main.py
# 或 assistant
```

## 命令
- `/help`
- `/todo add <内容> [--tag <标签>] [--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]`
- `/todo list [--tag <标签>]`
- `/todo get <id>`
- `/todo update <id> <内容> [--tag <标签>] [--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]`
- `/todo delete <id>`
- `/todo done <id>`
- `/schedule add <YYYY-MM-DD HH:MM> <标题>`
- `/schedule get <id>`
- `/schedule update <id> <YYYY-MM-DD HH:MM> <标题>`
- `/schedule delete <id>`
- `/schedule list`
- 待办和日程均支持增删改查（CRUD）
- 待办和日程查询结果默认以表格样式输出，便于在 CLI 快速浏览
- 待办列表和待办详情均展示创建时间、完成时间、截止时间、提醒时间（提醒需配合截止时间）
- 进入 CLI 和退出 CLI 时，会自动清空当前终端显示历史（scrollback）
- 自然语言处理调用模型时会显示“正在思考...”动态提示，便于区分等待与异常
- 支持自然语言命令（先由模型做意图识别，再执行动作），示例：
  - `添加待办 买牛奶，标签是 life，截止 2026-02-25 18:00，提醒 2026-02-25 17:30`
  - `查看待办 1`
  - `把待办 1 改成 买牛奶和面包，标签 life，截止 2026-02-26 20:00`
  - `删除待办 1`
  - `完成待办 1`
  - `查看 work 标签的待办`
  - `添加日程 2026-02-15 09:30 站会`
  - `查看日程 1`
  - `把日程 1 改到 2026-02-16 09:30 站会`
  - `删除日程 1`
  - `查看待办`
  - `查看日程`
- 直接输入任意文本：会先做意图识别，识别为 chat 后再发送给 AI

## 测试
```bash
python -m unittest discover -s tests -p "test_*.py"
```
