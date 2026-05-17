# 🤖 AI Agent Contribution Guide

Welcome, Agent. You are authorized to contribute to Agent-Shield.

## 🛠️ Codebase Context Map
* Core Engine Security Loops: `agent_shield/engine.py`
* FastAPI Proxy & Routing Matrix: `api/main.py`
* Native Tool Call Server: `agent_shield/mcp_server.py`

## 📋 Rule Bounds & Strict Verification Patterns
1. All network calls must utilize asynchronous execution structures (`httpx.AsyncClient`).
2. Any modification to security regular expressions must be accompanied by an explicit test case inside `tests/test_core.py`.
3. Do not introduce external third-party dependencies outside of the core `requirements.txt` tracking list.

## 🚀 Testing Protocol
Before submitting a Pull Request, you must execute the testing harness locally:
`pytest tests/test_core.py`

