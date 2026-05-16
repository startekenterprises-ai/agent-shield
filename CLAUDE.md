# CLAUDE.md — gstack-lite Engineering Discipline

## Project Vibe
- INTERMEDIARY PROXY: This repository is a lightweight security middleware (`fastapi` / `uvicorn`).
- NO BLOAT: Keep code dense, relying primarily on high-speed regex processing loops.

## Plan-Before-Code Protocol (gstack-lite)
Before altering any logic in `sanitizer.py` or adding rules:
1. **Define the Attack Vector**: State clearly what exploit (Indirect Prompt Injection, Jailbreak, PII leak) is being targeted.
2. **Write the Test Cases First**: Define a list of malicious strings that must successfully trigger your pattern.
3. **Draft the Pattern**: Provide the regex or keyword set before writing any Python code.

## Verification & Execution Commands
- Run Server: `uvicorn sanitizer:app --reload`
- Check Code: `python3 -m py_compile sanitizer.py`
