import requests


OLLAMA_BASE_URL = "http://localhost:11434"

CHAT_MODEL = "llama3.1:8b"
EMBED_MODEL = "nomic-embed-text"


def embed_text(text: str):
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={
            "model": EMBED_MODEL,
            "prompt": text
        },
        timeout=120
    )
    response.raise_for_status()
    return response.json()["embedding"]


def generate_answer(prompt: str):
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={
            "model": CHAT_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature":  0.1,
                "num_predict": 250
            }
        },
        timeout=300
    )
    response.raise_for_status()
    return response.json()["response"]
