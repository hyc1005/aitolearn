import os
import re
from collections import defaultdict
from functools import lru_cache

import numpy as np

from .store import all_of


def terms(text):
    try:
        import jieba
        words = [w.lower() for w in jieba.cut(text) if re.search(r"\w", w)]
    except ImportError:
        words = re.findall(r"[a-zA-Z0-9]+|[\u3400-\u9fff]", text.lower())
    han = "".join(re.findall(r"[\u3400-\u9fff]", text))
    return words + [han[i:i+2] for i in range(len(han)-1)]


@lru_cache(maxsize=1)
def encoder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"),
                               device="cpu", local_files_only=True)


def encode(texts, query=False):
    if os.getenv("APP_KEYWORD_ONLY") == "1":
        return []
    if query:
        texts = ["为这个句子生成表示以用于检索相关文章：" + t for t in texts]
    return encoder().encode(texts, normalize_embeddings=True).tolist()


def search(conn, query, limit=5, material_ids=None):
    chunks = [block for doc in all_of(conn, "material")
              if (doc["state"] == "published" if material_ids is None else doc["id"] in material_ids)
              for block in doc["blocks"] if block["text"].strip()]
    if not chunks or not terms(query):
        return []
    by_id = {c["id"]: c for c in chunks}
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.query_fts USING fts5(id UNINDEXED,content)")
    conn.execute("DELETE FROM temp.query_fts")
    conn.executemany("INSERT INTO temp.query_fts VALUES(?,?)",
                     [(c["id"], " ".join(terms(c["text"] + " " + c["locator"]))) for c in chunks])
    expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in dict.fromkeys(terms(query)[:40]))
    lexical = [row["id"] for row in conn.execute(
        "SELECT id FROM temp.query_fts WHERE query_fts MATCH ? ORDER BY rank,id LIMIT 20", (expression,))]
    scores = defaultdict(float)
    for rank, key in enumerate(lexical, 1):
        scores[key] += 1 / (60 + rank)
    vectors = [(c["id"], c["vector"]) for c in chunks if c.get("vector")]
    if vectors and (query_vector := encode([query], query=True)):
        similarities = np.asarray([v for _, v in vectors], dtype=np.float32) @ query_vector[0]
        for rank, i in enumerate(np.argsort(-similarities)[:20], 1):
            if similarities[i] >= 0.48:
                scores[vectors[i][0]] += 1 / (60 + rank)
    for qid in re.findall(r"\b(?:Q|L|B|CR)\d+\b", query.upper()):
        for c in chunks:
            if re.search(rf"\b{qid}\b", c["locator"].upper()):
                scores[c["id"]] += 1
    return [{k: v for k, v in by_id[key].items() if k != "vector"}
            for key in sorted(scores, key=lambda id: (-scores[id], id))[:limit]]
