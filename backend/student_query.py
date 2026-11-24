"""Indexed projections of immutable, teacher-confirmed snapshots."""
import json
from collections import Counter

from .data import ALLOWED, split
from .snapshots import table_data
from .store import get


DDL = (
    """CREATE TABLE IF NOT EXISTS indexed_snapshot (
     id TEXT PRIMARY KEY, attempts INTEGER, scored INTEGER, earned INTEGER, possible INTEGER)""",
    """CREATE TABLE IF NOT EXISTS snapshot_student (
     snapshot TEXT, student TEXT, class_id TEXT, PRIMARY KEY(snapshot,student)) WITHOUT ROWID""",
    "CREATE INDEX IF NOT EXISTS snapshot_class ON snapshot_student(snapshot,class_id,student)",
    """CREATE TABLE IF NOT EXISTS snapshot_exam (
     snapshot TEXT, node TEXT, date TEXT, PRIMARY KEY(snapshot,node)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS snapshot_question (
     snapshot TEXT, item TEXT, topic TEXT, form TEXT, payload TEXT, PRIMARY KEY(snapshot,item)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS exam_question (
     snapshot TEXT, node TEXT, item TEXT, PRIMARY KEY(snapshot,node,item)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS question_skill (
     snapshot TEXT, item TEXT, skill TEXT, PRIMARY KEY(snapshot,item,skill)) WITHOUT ROWID""",
    "CREATE INDEX IF NOT EXISTS skill_items ON question_skill(snapshot,skill,item)",
    """CREATE TABLE IF NOT EXISTS snapshot_attempt (
     snapshot TEXT, event TEXT, student TEXT, node TEXT, item TEXT, revision TEXT, state TEXT,
     earned INTEGER, possible INTEGER, source TEXT, sheet TEXT, line INTEGER, payload TEXT,
     PRIMARY KEY(snapshot,event)) WITHOUT ROWID""",
    "CREATE INDEX IF NOT EXISTS attempt_student ON snapshot_attempt(snapshot,student,node,item)",
    "CREATE INDEX IF NOT EXISTS attempt_exam ON snapshot_attempt(snapshot,node,item,student)",
)


def init(conn):
    for sql in DDL:
        conn.execute(sql)


def metadata(conn, sid):
    """Read the snapshot dimensions without transferring the large answer archive."""
    if not sid:
        return table_data(None)
    row = conn.execute("""SELECT json_remove(data,'$.tables.作答记录','$.provenance')
                          FROM records WHERE kind='snapshot' AND id=?""", (sid,)).fetchone()
    return table_data(json.loads(row[0])) if row else table_data(None)


def is_indexed(conn, sid):
    return bool(conn.execute('SELECT 1 FROM indexed_snapshot WHERE id=?', (sid,)).fetchone())


def index_snapshot(conn, snapshot):
    sid = snapshot['id']
    if conn.execute('SELECT 1 FROM indexed_snapshot WHERE id=?', (sid,)).fetchone():
        return
    t = table_data(snapshot)
    conn.executemany('INSERT INTO snapshot_student VALUES (?,?,?)',
                     ((sid, key, r['模拟班级编号']) for key, r in t['模拟学生'].items()))
    conn.executemany('INSERT INTO snapshot_exam VALUES (?,?,?)',
                     ((sid, key, r.get('模拟测验日期', '')) for key, r in t['测验节点'].items()))
    conn.executemany('INSERT INTO exam_question VALUES (?,?,?)',
                     ((sid, key, q) for key, r in t['测验节点'].items() for q in set(split(r['可用题目编号']))))
    conn.executemany('INSERT INTO snapshot_question VALUES (?,?,?,?,?)',
                     ((sid, key, r.get('主题编号', ''), r.get('题型', ''), json.dumps(r, ensure_ascii=False))
                      for key, r in t['题目与材料'].items()))
    conn.executemany('INSERT INTO question_skill VALUES (?,?,?)',
                     ((sid, key, skill) for key, r in t['题目与材料'].items() for skill in set(split(r.get('指标编号', '')))))
    attempts = t['作答记录']
    lineage = {'教师修订-' + r['id'][:8]: r['revision_of'] for r in snapshot.get('manifest', [])
               if r.get('revision_of')}
    for event, r in attempts.items():
        source = r.get('来源', {})
        initial = source
        seen = set()
        while initial.get('source') in lineage and initial['source'] not in seen:
            seen.add(initial['source'])
            initial = lineage[initial['source']]
        if initial is not source:
            r = {**r, '原始来源': initial}
        conn.execute('INSERT INTO snapshot_attempt VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (sid, event, r['学生编号'], r['节点编号'], r['材料编号'], r['修订版本'],
                      r['评分状态'], int(r['得分']) if r['评分状态'] == '已评分' else None, int(r['满分']),
                      source.get('source'), source.get('sheet'), source.get('line'), json.dumps(r, ensure_ascii=False)))
    scored = [r for r in attempts.values() if r['评分状态'] == '已评分']
    expected = (len(attempts), len(scored), sum(int(r['得分']) for r in scored), sum(int(r['满分']) for r in scored))
    actual = tuple(conn.execute('''SELECT count(*),count(earned),coalesce(sum(earned),0),
                                    coalesce(sum(CASE WHEN earned IS NOT NULL THEN possible END),0)
                                    FROM snapshot_attempt WHERE snapshot=?''', (sid,)).fetchone())
    if actual != expected or snapshot.get('report', {}).get('valid_scored', expected[1]) != actual[1]:
        raise ValueError(f'快照 {sid} 作答数或评分合计校对失败')
    conn.execute('INSERT INTO indexed_snapshot VALUES (?,?,?,?,?)', (sid, *actual))


def backfill(db):
    with db() as conn:
        init(conn)
        ids = {row[0] for row in conn.execute('''SELECT json_extract(data,'$.snapshot_id') FROM records
                                               WHERE kind='import' AND json_extract(data,'$.confirmed')=1
                                               UNION SELECT json_extract(data,'$.result.snapshot_id') FROM records
                                               WHERE kind='job' AND json_extract(data,'$.stage')='published' ''') if row[0]}
        active = get(conn, 'settings', 'active_snapshot') or {}
        if active.get('id'):
            ids.add(active['id'])
    for sid in sorted(ids):
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM indexed_snapshot WHERE id=?', (sid,)).fetchone():
                continue
            snap = get(conn, 'snapshot', sid)
            if snap:
                index_snapshot(conn, snap)


def qfilter(alias, skill='', topics=(), form=''):
    where, args = [], []
    if skill:
        where.append(f'EXISTS (SELECT 1 FROM question_skill k WHERE k.snapshot={alias}.snapshot AND k.item={alias}.item AND k.skill=?)')
        args.append(skill)
    if topics:
        where.append(f'{alias}.topic IN ({",".join("?" for _ in topics)})')
        args.extend(topics)
    if form:
        where.append(f'{alias}.form=?')
        args.append(form)
    return where, args


def scope(sid, student='', group='', node='', item='', **question):
    where, args = ['a.snapshot=?'], [sid]
    for field, value in [('a.student', student), ('s.class_id', group), ('a.node', node), ('a.item', item)]:
        if value:
            where.append(field + '=?')
            args.append(value)
    extra, params = qfilter('q', **question)
    return (' FROM snapshot_attempt a JOIN snapshot_question q USING(snapshot,item)'
            ' JOIN snapshot_student s USING(snapshot,student) WHERE ' + ' AND '.join(where + extra), args + params)


def answer_rows(conn, sid, student='', group='', node='', item='', scored_only=False, limit=None, **question):
    clause, args = scope(sid, student, group, node, item, **question)
    sql = 'SELECT a.payload, q.payload' + clause + (' AND a.earned IS NOT NULL' if scored_only else '') + ' ORDER BY a.event'
    if limit is not None:
        sql += ' LIMIT ?'
        args.append(limit)
    return [{**json.loads(row[0]), '题目': json.loads(row[1])} for row in conn.execute(sql, args)]


def offered(conn, sid, node, **question):
    extra, args = qfilter('q', **question)
    where = ['q.snapshot=?'] + (['e.node=?'] if node else []) + extra
    return [row[0] for row in conn.execute('SELECT DISTINCT q.item FROM exam_question e JOIN snapshot_question q '
                                          'USING(snapshot,item) WHERE ' + ' AND '.join(where) + ' ORDER BY q.item',
                                          [sid] + ([node] if node else []) + args)]


def aggregate(conn, sid, student='', group='', node='', **question):
    clause, args = scope(sid, student, group, node, **question)
    states = Counter(dict.fromkeys(ALLOWED, 0))
    rates, events = [], []
    earned = possible = 0
    for r in conn.execute('''SELECT a.student,a.state,count(*),sum(a.earned),sum(a.possible),json_group_array(a.event)'''
                          + clause + ' GROUP BY a.student,a.state', args):
        states[r[1]] += r[2]
        if r[3] is not None:
            earned += r[3]
            possible += r[4]
            rates.append(r[3] * 100 / r[4])
            events.extend(json.loads(r[5]))
    return states, rates, events, earned, possible


def profile(conn, sid, metadata, student='', group='', node='', include_question_scope=False):
    population = conn.execute('SELECT count(*) FROM snapshot_student WHERE snapshot=?'
                              + (' AND student=?' if student else '') + (' AND class_id=?' if group else ''),
                              [sid] + ([student] if student else []) + ([group] if group else [])).fetchone()[0]

    def axis(key, label, question):
        states, rates, events, earned, possible = aggregate(conn, sid, student, group, node, **question)
        questions = offered(conn, sid, node, **question)
        return {'id': key, 'label': label, 'score': round(sum(rates) / len(rates), 1) if rates else None,
                'earned': earned, 'possible': possible, 'n': len(events), 'coverage': len(rates),
                'population': population, 'distribution': sorted(round(r, 1) for r in rates),
                'status': '已评分' if rates else '未教学' if not questions else '无有效评分',
                'states': dict(states), 'attempt_ids': sorted(events),
                **({'question_ids': questions} if include_question_scope else {})}
    indicators = metadata['课程要求']
    topics = {r['主题编号']: r.get('主题', r['主题编号']) for r in indicators.values()}
    return {'student': student, 'group': group, 'node': node, 'size': population,
            'axes': [axis(key, r.get('观察指标', key), {'skill': key}) for key, r in indicators.items()],
            'topics': [axis(key, label, {'topics': [key]}) for key, label in topics.items()],
            'totals': [r for r in metadata['总分'].values() if (not student or student == r['学生编号'])
                       and (not group or group == metadata['模拟学生'].get(r['学生编号'], {}).get('模拟班级编号'))
                       and (not node or node == r.get('节点编号'))]}


def observed_nodes(conn, sid, student='', scored_only=False):
    clause, args = scope(sid, student=student)
    return {row[0] for row in conn.execute('SELECT DISTINCT a.node' + clause +
                                           (' AND a.earned IS NOT NULL' if scored_only else ''), args)}


def measure(conn, sid, student, node, **question):
    states, _, events, earned, possible = aggregate(conn, sid, student, node=node, **question)
    attempts = answer_rows(conn, sid, student, node=node, **question)
    exam = conn.execute('SELECT date FROM snapshot_exam WHERE snapshot=? AND node=?', (sid, node)).fetchone()
    return {'node': node, 'date': exam[0] if exam else '', 'score': earned, 'maximum': possible,
            'rate': earned * 100 / possible if possible else None, 'count': len(events),
            'states': {state: n for state, n in states.items() if n},
            'status': 'scored' if events else 'unscored' if states['待评分'] else
                      'missing' if attempts or offered(conn, sid, node, **question) else 'uncovered',
            'attempts': attempts}
