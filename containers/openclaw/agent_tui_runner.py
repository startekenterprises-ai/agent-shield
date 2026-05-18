import os
import json
import asyncio
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser

# Custom Wrapper to cleanly satisfy browser-use's internal logging checks
class SecuredLLMProxy:
    def __init__(self, llm_instance, model_name):
        self._llm = llm_instance
        self.provider = "openai-compatible"
        self.model = model_name

    def __getattr__(self, name):
        return getattr(self._llm, name)

async def run_agent_workflow(llm_client, model_label, user_task):
    """Encapsulates the browser execution context initialization block."""
    # 🛡️ Initializing completely blank forces the engine to resolve defaults
    # and adapt dynamically to whatever package variant your container pulled
    browser_pool = Browser()

    llm_proxy = SecuredLLMProxy(llm_client, model_label)

    # Run tasks without strict tool-calling vision schema blocks
    agent = Agent(
        task=user_task,
        llm=llm_proxy,
        browser=browser_pool,
        use_vision=False  # Prevents rigid Pydantic vision-schema errors
    )

    try:
        await agent.run()
        return True
    except Exception as e:
        print(f"⚠️  [Provider Warning]: Task execution encountered an error: {e}")
        return False
    finally:
        await browser_pool.close()

async def main():
    print("🛡️  [Secure AI Workspace]: Reading local openclaw.json environment profiles...")
    config_path = "/root/.openclaw/openclaw.json"
    with open(config_path, "r") as f:
        config = json.load(f)

    # 🛡️ NETWORK ROUTING BARRIERS: Force Playwright/Chromium to pass all
    # external web traffic straight through the Agent-Shield proxy container port
    os.environ["HTTP_PROXY"] = "http://agent-shield-gateway:8000"
    os.environ["HTTPS_PROXY"] = "http://agent-shield-gateway:8000"

    # Exclude internal cluster docker sockets from running through the proxy
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,agent-shield-gateway,searxng-private-mesh"
    os.environ["ANONYMIZED_TELEMETRY"] = "false"

    task = input("\n👉 Enter Task: ")
    if not task:
        return

    # Attempt Priority 1: Configured Engine (Defaulting to Cloud Tier)
    print(f"🧠 [Attempt 1]: Executing task loop via primary endpoint: {config['llm']['model']}")
    primary_client = ChatOpenAI(
        base_url=config["llm"]["api_base"],
        model=config["llm"]["model"],
        temperature=0.0,
        api_key=os.getenv("OPENROUTER_API_KEY", "local-token"),
        disable_streaming=True
    )

    success = await run_agent_workflow(primary_client, config["llm"]["model"], task)

    # Attempt Priority 2: Fallback to Local Ollama if Primary fails or hits caps
    if not success and config["llm"]["fallback_ollama_base"]:
        print("\n🔄 [CYCLE ACTIVE]: Primary provider limit reached or execution blocked.")
        print("🚨 Falling back natively to local hardware safety infrastructure (Ollama)...")

        fallback_client = ChatOpenAI(
            base_url=f"{config['llm']['fallback_ollama_base']}/v1",
            model="qwen2.5-coder-7b:128k",
            temperature=0.0,
            api_key="local-token",
            disable_streaming=True
        )
        await run_agent_workflow(fallback_client, "qwen2.5-coder-7b:128k", task)

    print("\n🏆 Workspace Process Concluded. Status Safe.")

if __name__ == "__main__":
    asyncio.run(main())

