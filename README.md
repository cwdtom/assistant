# CLI AI Personal Assistant (MVP)

一个中文优先的本地 CLI 个人助手，支持：
- AI 对话（DeepSeek API）
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
- `/todo add <内容> [--tag <标签>]`
- `/todo list [--tag <标签>]`
- `/todo done <id>`
- `/schedule add <YYYY-MM-DD HH:MM> <标题>`
- `/schedule list`
- 自然语言处理调用模型时会显示“正在思考...”动态提示，便于区分等待与异常
- 支持自然语言命令（先由模型做意图识别，再执行动作），示例：
  - `添加待办 买牛奶，标签是 life`
  - `完成待办 1`
  - `查看 work 标签的待办`
  - `添加日程 2026-02-15 09:30 站会`
  - `查看待办`
  - `查看日程`
- 直接输入任意文本：发送给 AI

## 测试
```bash
python -m unittest discover -s tests -p "test_*.py"
```
