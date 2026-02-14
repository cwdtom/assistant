# CLI AI Personal Assistant (MVP)

一个中文优先的本地 CLI 个人助手，支持：
- AI 对话（OpenAI 兼容接口）
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
# 编辑 .env，填入 OPENAI_API_KEY
```

3. 运行
```bash
python main.py
# 或 assistant
```

## 命令
- `/help`
- `/todo add <内容>`
- `/todo list`
- `/todo done <id>`
- `/schedule add <YYYY-MM-DD HH:MM> <标题>`
- `/schedule list`
- 直接输入任意文本：发送给 AI

## 测试
```bash
python -m unittest discover -s tests -p "test_*.py"
```
