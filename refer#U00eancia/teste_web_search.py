import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("AZURE_OPENAI_API_KEY")
deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.2-chat-2")

if not api_key:
    raise ValueError("AZURE_OPENAI_API_KEY não configurada.")

url = "https://thall-mj3g48g6-eastus2.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview"

payload = {
    "model": deployment,
    "tools": [{"type": "web_search"}],
    "tool_choice": "auto",
    "include": ["web_search_call.action.sources"],
    "input": """
Pesquise na web por fontes oficiais e me diga:
o edital PIPE FAPESP está aberto ou encerrado?
Responda em português e cite as fontes encontradas.
"""
}

headers = {
    "Content-Type": "application/json",
    "api-key": api_key,
}

resp = requests.post(url, headers=headers, json=payload, timeout=120)
resp.raise_for_status()

data = resp.json()

print("=== RESPOSTA ===")
print(data)