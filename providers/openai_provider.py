import os
import hashlib
from dotenv import load_dotenv
from openai import OpenAI

from analysis_cache import get_many_json, set_many_json


load_dotenv()

CHAT_MODEL = "gpt-4.1-mini"
EMBED_MODEL = "text-embedding-3-small"
DEFAULT_EMBED_BATCH_SIZE = 64


def _embed_batch_size(value=None) -> int:
    raw = value if value is not None else os.getenv("EMBED_BATCH_SIZE", DEFAULT_EMBED_BATCH_SIZE)
    try:
        return max(1, min(int(raw), 128))
    except (TypeError, ValueError):
        return DEFAULT_EMBED_BATCH_SIZE


def embed_texts(texts, batch_size=None):
    """Embed multiple chunks in ordered requests instead of one HTTP call per chunk."""
    inputs = [str(text) for text in texts]
    if not inputs:
        return []

    size = _embed_batch_size(batch_size)
    keys = [hashlib.sha256(f"embedding-v1\0{EMBED_MODEL}\0{text}".encode("utf-8")).hexdigest() for text in inputs]
    cached = get_many_json("embedding", keys)
    missing_keys = []
    missing_texts = []
    for key, text in zip(keys, inputs):
        if key not in cached and key not in missing_keys:
            missing_keys.append(key)
            missing_texts.append(text)

    generated = {}
    if missing_texts:
        client = _get_client()
        for start in range(0, len(missing_texts), size):
            batch_texts = missing_texts[start:start + size]
            batch_keys = missing_keys[start:start + size]
            response = client.embeddings.create(model=EMBED_MODEL, input=batch_texts)
            ordered = sorted(response.data, key=lambda item: item.index)
            if len(ordered) != len(batch_texts):
                raise RuntimeError("OpenAI 임베딩 응답 개수가 입력 청크 수와 다릅니다.")
            for key, item in zip(batch_keys, ordered):
                generated[key] = item.embedding
        set_many_json("embedding", generated)

    embeddings = [cached.get(key, generated.get(key)) for key in keys]
    if any(embedding is None for embedding in embeddings):
        raise RuntimeError("OpenAI 임베딩 응답 개수가 입력 청크 수와 다릅니다.")
    return embeddings


def embed_text(text: str):
    """OpenAI 단일 임베딩. 검색 질의 등 단건 호출과의 호환성을 유지한다."""
    return embed_texts([text], batch_size=1)[0]

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


def generate_answer(prompt: str, max_tokens: int = 800):
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
        max_tokens=max_tokens
    )

    return response.choices[0].message.content
