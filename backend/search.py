import os
import re
from collections import defaultdict
from functools import lru_cache

import numpy as np

import json
import logging

logger = logging.getLogger(__name__)


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


def optional_encode(texts, query=False):
    try:
        return encode(texts, query=query)
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        logger.warning("向量检索不可用，保留关键词检索 (%s)", type(error).__name__)
        return []


def init_index(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS material_index_version (
                    id TEXT PRIMARY KEY, current INTEGER NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS material_fragment (
                    rowid INTEGER PRIMARY KEY, version TEXT NOT NULL, fragment TEXT NOT NULL,
                    locator TEXT NOT NULL, page INTEGER, payload TEXT NOT NULL, vector TEXT,
                    UNIQUE(version,fragment))''')
    conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS material_fts USING fts5(tokens)')


def record_publication(conn, doc):
    if doc['state'] not in {'published', 'replaced'}:
        return
    init_index(conn)
    version = doc['id']
    current = int(doc['state'] == 'published')
    if conn.execute('SELECT 1 FROM material_index_version WHERE id=?', (version,)).fetchone():
        conn.execute('UPDATE material_index_version SET current=? WHERE id=?', (current, version))
        return
    for block in doc.get('blocks', []):
        if not block['text'].strip():
            continue
        locator = block['locator']
        match = re.search(r'第\s*(\d+)\s*页', locator)
        page = int(match[1]) if match else None
        payload = {**{k: v for k, v in block.items() if k != 'vector'}, 'material': version,
                   'material_version': version, 'page': page}
        rowid = conn.execute('INSERT INTO material_fragment(version,fragment,locator,page,payload,vector) VALUES (?,?,?,?,?,?)',
                             (version, block['id'], locator, page, json.dumps(payload, ensure_ascii=False),
                              json.dumps(block['vector']) if block.get('vector') else None)).lastrowid
        conn.execute('INSERT INTO material_fts(rowid,tokens) VALUES (?,?)',
                     (rowid, ' '.join(terms(block['text'] + ' ' + locator))))
    conn.execute('INSERT INTO material_index_version VALUES (?,?)', (version, current))


def backfill_index(db):
    with db() as conn:
        init_index(conn)
        ids = [r[0] for r in conn.execute('''SELECT r.id FROM records r LEFT JOIN material_index_version v
                 ON v.id=r.id WHERE r.kind='material' AND v.id IS NULL AND
                 json_extract(r.data,'$.state') IN ('published','replaced')''')]
    from .store import get
    for key in ids:
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            doc = get(conn, 'material', key)
            if doc:
                record_publication(conn, doc)


def search(conn, query, limit=5, material_ids=None):
    tokens = terms(query)
    if not tokens or material_ids == [] or not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='material_fts'").fetchone():
        return []
    args = [] if material_ids is None else list(material_ids)
    scope = ('v.current=1' if material_ids is None else 'f.version IN (' + ','.join('?' for _ in args) + ')')
    join = ' JOIN material_index_version v ON f.version=v.id WHERE ' + scope
    expression = ' OR '.join('"' + token.replace('"', '""') + '"' for token in dict.fromkeys(tokens[:40]))
    lexical = [r[0] for r in conn.execute('SELECT f.rowid FROM material_fts JOIN material_fragment f ON f.rowid=material_fts.rowid'
                                         + join + ' AND material_fts MATCH ? ORDER BY rank,f.fragment LIMIT 20',
                                         [*args, expression])]
    scores = defaultdict(float)
    for rank, key in enumerate(lexical, 1):
        scores[key] += 1 / (60 + rank)
    if os.getenv('APP_KEYWORD_ONLY') != '1':
        vectors = [(r[0], json.loads(r[1])) for r in conn.execute(
            'SELECT f.rowid,f.vector FROM material_fragment f' + join + ' AND f.vector IS NOT NULL', args)]
        if vectors and (query_vector := optional_encode([query], query=True)):
            vectors = [(key, v) for key, v in vectors if len(v) == len(query_vector[0])]
            if vectors:
                similarities = np.asarray([v for _, v in vectors], dtype=np.float32) @ query_vector[0]
                for rank, i in enumerate(np.argsort(-similarities)[:20], 1):
                    if similarities[i] >= 0.48:
                        scores[vectors[i][0]] += 1 / (60 + rank)
    for qid in set(re.findall(r'\b(?:Q|L|B|CR)\d+\b', query.upper())):
        for row in conn.execute('SELECT f.rowid,f.locator FROM material_fragment f' + join + ' AND upper(f.locator) LIKE ?',
                                [*args, '%' + qid + '%']):
            if re.search(rf'\b{qid}\b', row[1].upper()):
                scores[row[0]] += 1
    ranked = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
    return [json.loads(conn.execute('SELECT payload FROM material_fragment WHERE rowid=?', (key,)).fetchone()[0])
            for key in ranked]
