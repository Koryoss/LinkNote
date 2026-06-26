import os
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

CHAT_MODEL = "gpt-4.1-mini"
EMBED_MODEL = "text-embedding-3-small"


def embed_text(text: str):
    """OpenAI 임베딩 (웹 배포용 — 서버에 Ollama가 없을 때)."""
    response = _get_client().embeddings.create(model=EMBED_MODEL, input=text)
    return response.data[0].embedding

# 클라이언트를 import 시점이 아니라 실제 호출 때 생성한다.
# 이렇게 하면 OPENAI_API_KEY가 없어도 서버는 정상 기동되고,
# OpenAI가 필요 없는 기능(/ingest, /units, /library, /timetable)은 그대로 동작한다.
_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY가 설정되지 않았습니다. 답변 생성(/ask)에만 필요합니다."
            )
        _client = OpenAI(api_key=api_key)
    return _client


def generate_answer(prompt: str):
    response = _get_client().chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "너는 강의자료 기반 학습 도우미다. "
                    "반드시 자연스러운 한국어로만 답변한다. "
                    "중국어, 일본어, 베트남어 문장을 섞지 않는다."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2,
        max_tokens=800
    )

    return response.choices[0].message.content
