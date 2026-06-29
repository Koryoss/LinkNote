"""정밀 검색 파이프라인: HyDE 쿼리확장 + 하이브리드(벡터+키워드) + LLM 리랭킹.
의존성 추가 없음 (ChromaDB where_document $contains 로 키워드 채널 구성).
"""
import json
import re

from providers.hybrid_provider import embed_text, generate_answer

_STOP = set("그리고 그러나 무엇 어떤 어떻게 무엇인가 대해 관해 인가 인지 무슨 어디 언제 누구 "
            "about what which how why when who the of in on for and or is are to a an".split())


def _terms(q, limit=8):
    toks = re.findall(r"[A-Za-z]{2,}|[가-힣]{2,}", q or "")
    seen, out = set(), []
    for t in toks:
        if t.lower() in _STOP or t in seen:
            continue
        seen.add(t); out.append(t)
    return out[:limit]


def _hyde(question):
    """질문에 대한 '가상 답변'을 만들어 임베딩 적중률을 높인다 (LLM 1회)."""
    try:
        txt = generate_answer(
            "다음 질문에 강의자료에 나올 법한 한국어 설명을 2~3문장으로 써라. "
            f"확실치 않으면 핵심 용어만 나열.\n질문: {question}"
        )
        return (txt or "").strip()[:600]
    except Exception:
        return ""


def _vector(collection, qtext, where, k):
    args = {"query_embeddings": [embed_text(qtext)], "n_results": k,
            "include": ["documents", "metadatas", "distances"]}
    if where:
        args["where"] = where
    r = collection.query(**args)
    out = []
    if r.get("ids") and r["ids"][0]:
        for i, (id_, doc, meta, dist) in enumerate(
                zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0])):
            out.append({**meta, "id": id_, "text": doc, "distance": dist})
    return out


def _keyword(collection, terms, where, k):
    """키워드 채널: 약어·고유명사 정확 매칭 (임베딩 무관)."""
    hit = {}
    for t in terms:
        args = {"where_document": {"$contains": t}, "limit": k,
                "include": ["documents", "metadatas"]}
        if where:
            args["where"] = where
        try:
            r = collection.get(**args)
        except Exception:
            continue
        for id_, doc, meta in zip(r.get("ids", []), r.get("documents", []), r.get("metadatas", [])):
            e = hit.get(id_)
            if e:
                e["_kw"] += 1
            else:
                hit[id_] = {**(meta or {}), "id": id_, "text": doc, "_kw": 1}
    return sorted(hit.values(), key=lambda c: c["_kw"], reverse=True)[:k]


def _rrf(ranklists, k=60):
    """Reciprocal Rank Fusion: 여러 순위목록을 하나로 융합."""
    score, pick = {}, {}
    for lst in ranklists:
        for rank, c in enumerate(lst):
            cid = c["id"]
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + rank + 1)
            pick.setdefault(cid, c)
    return [pick[i] for i in sorted(score, key=lambda x: score[x], reverse=True)]


def _rerank(question, cands, n):
    """LLM 리랭킹: 상위 후보의 관련도를 0~10으로 재채점 (LLM 1회)."""
    pool = cands[:min(len(cands), 12)]
    if len(pool) <= 1:
        return cands[:n]
    listing = "\n".join(f"[{i}] {c['text'][:240]}" for i, c in enumerate(pool))
    try:
        raw = generate_answer(
            "아래 후보들이 질문에 답하는 데 얼마나 관련 있는지 0~10으로 채점해라. "
            'JSON 배열만 출력: [{"i":0,"s":8}, ...] 다른 말 금지.\n'
            f"질문: {question}\n후보:\n{listing}"
        )
        m = re.search(r"\[.*\]", raw, re.S)
        scores = {int(d["i"]): float(d["s"]) for d in json.loads(m.group(0))}
        ranked = sorted(range(len(pool)), key=lambda i: scores.get(i, 0), reverse=True)
        out = [pool[i] for i in ranked]
        out += [c for c in cands if c not in pool]
        return out[:n]
    except Exception:
        return cands[:n]


def retrieve(collection, question, where, n=8, use_hyde=True, use_keyword=True, use_rerank=True):
    pool = max(n * 3, 18)
    ranklists = [_vector(collection, question, where, pool)]
    if use_hyde and (h := _hyde(question)):
        ranklists.append(_vector(collection, f"{question}\n{h}", where, pool))
    if use_keyword and (terms := _terms(question)):
        ranklists.append(_keyword(collection, terms, where, pool))
    fused = _rrf(ranklists)
    if not fused:
        return []
    return _rerank(question, fused, n) if use_rerank else fused[:n]
