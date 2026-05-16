#!/bin/bash
echo "🛡️ Initializing Agent-Shield Workspace..."
pip install pytest
pip install -e .

echo "Checking Ollama connectivity..."
curl -s http://localhost:11434/api/tags > /dev/null
if [ $? -eq 0 ]; then
    echo "✅ Ollama is responsive on port 11434."
else
    echo "⚠️ Warning: Ollama not detected. Ensure it is running on port 11434 for semantic filtering."
fi
echo "🚀 Initialization complete. Run 'pytest' to verify."

