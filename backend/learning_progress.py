"""Deterministic score comparisons, optionally accompanied by published sources."""
import re
from datetime import date

from .conversation import resolve
from .search import search
from .snapshots import table_data
from .student_query import measure

TREND = re.compile(r'进步|退步|变化|趋势|提升|对比|比较|变好|变差|历次|前后')
TEACHING = re.compile(r'教材|教学|教法|怎么讲|怎么教|课本|原文|资料|怎么练')


def named_skills(curriculum, question):
    upper = question.upper()
    matches = []
    for key, row in curriculum.items():
        name = row.get('观察指标') or key
        terms = [s for s in re.split(r'[,、，；。\s]|并', name) if len(s) >= 2]
        if re.search(rf'(?<![A-Z0-9]){re.escape(key.upper())}(?![A-Z0-9])', upper) or any(s in question for s in terms):
            matches.append((key, name))
    return matches


def dated_nodes(conn, sid, question):
    nodes = []
    explicit = set(re.findall(r'(?<![A-Z0-9])N\d{1,4}(?![A-Z0-9])', question.upper()))
    for row in conn.execute('SELECT node,date FROM snapshot_exam WHERE snapshot=? ORDER BY date,node', (sid,)):
        if explicit and row[0] not in explicit:
            continue
        try:
            day = date.fromisoformat(row[1])
        except (ValueError, TypeError):
            day = None
        nodes.append((row[0], day))
    return nodes


def citations(conn, query, label):
    # General words such as “教材” must not make an unrelated passage look like
    # evidence for a specific skill. Prefer the skill terms the teacher named.
    terms = [term for term in re.split(r'[,、，；。\s]|并', label) if len(term) >= 2]
    named = [term for term in terms if term in query]
    required = named or terms
    hits = search(conn, ' '.join(required) if required else label, limit=20)
    hits = [item for item in hits if any(term in item['text'] for term in required)][:3]
    return [{'quote': item['text'][:380], 'source': {'kind': 'material', 'title': item['title'],
             'locator': item['locator'], 'citation': item}} for item in hits]


def answer_progress(conn, snapshot, request):
    if not snapshot:
        return None
    asks_trend, asks_teaching = TREND.search(request.question), TEACHING.search(request.question)
    if not (asks_trend or asks_teaching):
        return None
    data = table_data(snapshot)
    scope = resolve(data, request.question, {'student': request.student, 'group': request.group, 'node': request.node})
    skills = named_skills(data['课程要求'], request.question)
    if not skills and re.search(r'刚才|这个|该知识点|前面|对应', request.question):
        for previous in reversed(request.history):
            skills = named_skills(data['课程要求'], previous if isinstance(previous, str) else previous.question)
            if skills:
                break
    if not skills:
        return None
    if not asks_trend and (not scope['student'] and not scope['group'] or
                          not re.search(r'教材|课本|怎么讲|怎么教|教法', request.question)):
        return None
    if asks_trend and len(skills) != 1:
        return {'answer': '请一次指定一个技能点：' + '、'.join(f'{name}（{key}）' for key, name in skills)}
    skill, label = skills[0]
    if asks_trend and (scope['student'] not in data['模拟学生'] or scope['candidates']):
        candidates = [item for item in scope['candidates'] if item['kind'] == 'student']
        return {'answer': '请选择一名学生后再比较历次作答。', 'cards': [
            {'kind': 'candidates', 'intent': 'progress', 'indicator': skill, 'teaching': bool(asks_teaching),
             'snapshot_id': snapshot['id'], 'prompt': '选择学生，也可直接输入学生编号。',
             'options': candidates or [
                {'kind': 'student', 'id': key, 'label': key} for key, r in data['模拟学生'].items()
                if not scope['group'] or r['模拟班级编号'] == scope['group']][:15]}]}
    sid, student = snapshot['id'], scope['student']
    lines, cards, parts, trace = [], [], [], []
    if asks_trend:
        attempts = []
        for node, day in dated_nodes(conn, sid, request.question):
            item = measure(conn, sid, student, node, skill=skill)
            if item['status'] == 'uncovered':
                continue
            attempts.append((item, day))
        scored = [(item, day) for item, day in attempts if item['count']]
        lines.append(f'{student} · {label}（{skill}）：')
        if not attempts:
            lines.append('所选测验未覆盖此技能点。')
        for item, _ in attempts:
            summary = (f"{item['score']}/{item['maximum']} 分，得分率 {item['rate']:.1f}%，已评分 {item['count']} 题"
                       if item['count'] else '无有效评分')
            if item['states'].get('待评分'):
                summary += f"，待评分 {item['states']['待评分']} 条"
            if item['states'].get('未作答'):
                summary += f"，未作答 {item['states']['未作答']} 条"
            elif item['status'] == 'missing':
                summary += '，未见作答记录'
            lines.append(item['node'] + '：' + summary + '。')
        if len(scored) >= 2 and all(day for _, day in scored) and len({day for _, day in scored}) == len(scored):
            change = scored[-1][0]['rate'] - scored[0][0]['rate']
            lines.append(f"从 {scored[0][0]['node']} 到 {scored[-1][0]['node']}，得分率变化 {change:+.1f} 个百分点。题目可能不同，仅比较本次样本。")
        else:
            lines.append('有效测验不足两次，或测验日期无法区分，不能判断变化。')
        cards.append({'kind': 'progress', 'student': student, 'indicator': skill, 'snapshot_id': sid,
                      'nodes': [item['node'] for item, _ in attempts if item['attempts']]})
        trace.append({'tool': 'read_progress', 'status': 'ok', 'student': student, 'snapshot_id': sid,
                      'indicator': skill, 'count': sum(item['count'] for item, _ in attempts)})
    if asks_teaching:
        parts = citations(conn, request.question, label)
        trace.append({'tool': 'search_materials', 'status': 'ok', 'count': len(parts)})
        lines.append('教学资料：以下是已发布资料中与该技能相关的原文。' if parts else '教学资料：暂无匹配的已发布资料。')
    return {'answer': '\n'.join(lines), 'cards': cards, 'parts': parts, 'trace': trace}
