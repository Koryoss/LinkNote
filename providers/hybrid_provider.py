import os

from providers.openai_provider import generate_answer

# 임베딩 제공자 선택:
#  - 기본(로컬): Ollama (무료, 로컬)
#  - 웹 배포(Render 등): EMBED_PROVIDER=openai  → OpenAI 임베딩 (Ollama 불필요)
if os.getenv("EMBED_PROVIDER", "ollama").lower() == "openai":
    from providers.openai_provider import embed_text
else:
    from providers.ollama_provider import embed_text
