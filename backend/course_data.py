"""Course-scoped, teacher-reviewed spreadsheet imports.

This store is deliberately separate from legacy confirmed snapshots; records are
not advertised as available to the legacy diagnosis engine.
"""
import csv
import hashlib
import io
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import BadZipFile
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .data import open_import_workbook
from .store import db, new_id

router = APIRouter(prefix='/api/course-data', tags=['course-data'])
DDL = (
    'CREATE TABLE IF NOT EXISTS cd_course (id TEXT PRIMARY KEY, name TEXT NOT NULL, subject TEXT NOT NULL, grade TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS cd_class (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE)',
    'CREATE TABLE IF NOT EXISTS cd_student (id TEXT PRIMARY KEY, class_id TEXT NOT NULL REFERENCES cd_class(id), external_id TEXT, name TEXT NOT NULL, UNIQUE(class_id, external_id))',
    'CREATE TABLE IF NOT EXISTS cd_enrollment (course_id TEXT NOT NULL REFERENCES cd_course(id), student_id TEXT NOT NULL REFERENCES cd_student(id), PRIMARY KEY(course_id, student_id))',
    'CREATE TABLE IF NOT EXISTS cd_batch (id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES cd_course(id), class_id TEXT NOT NULL REFERENCES cd_class(id), filename TEXT NOT NULL, sha256 TEXT NOT NULL, source BLOB NOT NULL, mapping TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)',
    'CREATE TABLE IF NOT EXISTS cd_record (id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES cd_batch(id), student_id TEXT NOT NULL REFERENCES cd_student(id), kind TEXT NOT NULL, activity TEXT NOT NULL, question TEXT NOT NULL, score TEXT, maximum TEXT, comment TEXT NOT NULL, sheet TEXT NOT NULL, line INTEGER NOT NULL)',
)


def init(conn):
    for sql in DDL:
        conn.execute(sql)


def migrate_comments():
    with db() as conn:
        init(conn)
        conn.execute("UPDATE cd_record SET kind='comment' WHERE kind='total' AND score IS NULL AND comment <> ''")


class Course(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    subject: str = Field(default='', max_length=100)
    grade: str = Field(default='', max_length=100)


class MappingSuggestion(BaseModel):
    mapping: dict[str, str]


class MappingRequest(BaseModel):
    headers: list[str] = Field(max_length=100)


@router.post('/suggest-mapping')
def suggest_mapping(request: MappingRequest):
    from .cleaning_agent import structured_review
    headers = [header.strip() for header in request.headers if header.strip() and len(header) <= 100]
    if not headers:
        return {'mapping': {}}
    try:
        suggestion, _ = structured_review(
            MappingSuggestion,
            '根据表头建议字段映射。仅使用给定表头与字段，无法确定时省略。mapping 的键是字段名，值是原表头。',
            {'headers': headers, 'fields': ALIASES}, new_id())
    except ValueError as error:
        raise HTTPException(502, str(error)) from None
    result = {key: value for key, value in suggestion.mapping.items() if key in ALIASES and value in headers}
    if len(set(result.values())) != len(result):
        raise HTTPException(502, '模型建议中存在重复映射，请手动核对')
    return {'mapping': result}


@router.get('/courses')
def courses():
    with db() as conn:
        init(conn)
        return [dict(row) for row in conn.execute('SELECT * FROM cd_course ORDER BY name, id')]


@router.post('/courses')
def create_course(course: Course):
    name = course.name.strip()
    if not name:
        raise HTTPException(422, '课程名称不能为空')
    result = {'id': new_id(), 'name': name, 'subject': course.subject.strip(), 'grade': course.grade.strip()}
    with db() as conn:
        init(conn)
        conn.execute('INSERT INTO cd_course VALUES (:id,:name,:subject,:grade)', result)
    return result


ALIASES = {
    'student_id': ('学号', '学生编号', '学生ID'), 'student_name': ('姓名', '学生姓名', '学生'),
    'score': ('得分', '成绩', '分数', '总分', '测验总分'), 'maximum': ('满分', '总分上限'),
    'question': ('题号', '题目编号', '题目'), 'activity': ('测验', '测验名称', '作业', '活动'),
    'comment': ('评价', '评语', '文字评价', '教师评价'),
}


def suggest(headers):
    return {key: matches[0] for key, aliases in ALIASES.items()
            if len(matches := [h for h in headers if h in aliases]) == 1}


def number(value):
    if not value:
        return None
    try:
        value = Decimal(value)
    except InvalidOperation:
        raise ValueError('分数不是有效数字') from None
    if not value.is_finite() or value < 0 or value > 1000000 or value.as_tuple().exponent < -6:
        raise ValueError('分数须为非负数，最多保留六位小数')
    return value


def parse(filename, raw, mapping=None, sheet_name=None, wide_columns=None, wide_maximums=None, default_activity=''):
    suffix = Path(filename).suffix.lower()
    if suffix == '.csv':
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            content = raw.decode('gb18030')
        sheets = [('', list(csv.reader(io.StringIO(content, newline=''))))]
    elif suffix == '.xlsx':
        book = open_import_workbook(raw)
        try:
            sheets = [(sheet.title, list(sheet.values)) for sheet in book]
        finally:
            book.close()
    else:
        raise ValueError('仅支持 CSV 和 XLSX')
    populated = [(name, rows) for name, rows in sheets if rows and any(v is not None and str(v).strip() for v in rows[0])]
    if not populated:
        raise ValueError('文件没有可用工作表')
    if sheet_name is None:
        if len(populated) != 1:
            raise ValueError('请选择工作表')
        sheet, rows = populated[0]
    else:
        selected = [(name, rows) for name, rows in populated if name == sheet_name]
        if not selected:
            raise ValueError('工作表不存在')
        sheet, rows = selected[0]
    headers = [str(v).strip() if v is not None else '' for v in rows[0]]
    if len(set(h for h in headers if h)) != sum(bool(h) for h in headers):
        raise ValueError('存在重复表头，请先调整文件')
    mapping = suggest(headers) if mapping is None else mapping
    if not isinstance(mapping, dict) or any(k not in ALIASES or not isinstance(v, str) or v not in headers for k, v in mapping.items()):
        raise ValueError('列映射无效')
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('同一列不能映射到多个字段')
    issues, valid = [], []
    missing = []
    if not ({'student_id', 'student_name'} & mapping.keys()):
        missing.append('学生编号或姓名')
    wide_columns = wide_columns or []
    wide_maximums = wide_maximums or {}
    if not isinstance(wide_maximums, dict) or set(wide_maximums) - set(wide_columns):
        raise ValueError('题目满分配置无效')
    maximums = {key: number(str(value)) for key, value in wide_maximums.items() if str(value).strip()}
    if any(value is None or value <= 0 for value in maximums.values()):
        raise ValueError('题目满分须大于零')
    if not isinstance(wide_columns, list) or len(set(wide_columns)) != len(wide_columns) or any(column not in headers or column in mapping.values() for column in wide_columns):
        raise ValueError('题目列选择无效')
    if not ({'score', 'comment'} & mapping.keys()) and not wide_columns:
        missing.append('成绩或文字评价')
    if missing:
        return {'headers': headers, 'mapping': mapping, 'missing': missing, 'issues': [], 'rows': [], 'sheet': sheet}
    positions = {key: headers.index(value) for key, value in mapping.items()}
    for line, row in enumerate(rows[1:], 2):
        if not any(v is not None and str(v).strip() for v in row):
            continue
        data = {key: str(row[index]).strip() if index < len(row) and row[index] is not None else '' for key, index in positions.items()}
        if not data.get('activity'):
            data['activity'] = default_activity.strip()
        try:
            if not (data.get('student_id') or data.get('student_name')):
                raise ValueError('缺少学生编号和姓名')
            score, maximum = number(data.get('score')), number(data.get('maximum'))
            if maximum is not None and maximum <= 0:
                raise ValueError('满分须大于零')
            if score is not None and maximum is not None and score > maximum:
                raise ValueError('得分不能超过满分')
            expanded = []
            if score is not None or data.get('comment'):
                expanded.append({**data, 'score': str(score) if score is not None else None,
                                 'maximum': str(maximum) if maximum is not None else None, 'sheet': sheet, 'line': line})
            for column in wide_columns:
                index = headers.index(column)
                value = str(row[index]).strip() if index < len(row) and row[index] is not None else ''
                mark = number(value)
                if mark is not None:
                    if column in maximums and mark > maximums[column]:
                        raise ValueError(f'{column} 得分超过满分')
                    expanded.append({**data, 'score': str(mark), 'maximum': str(maximums[column]) if column in maximums else None, 'question': column,
                                     'comment': '', 'sheet': sheet, 'line': line})
            if not expanded:
                raise ValueError('缺少成绩和文字评价')
            valid.extend(expanded)
        except ValueError as error:
            issues.append({'line': line, 'reason': str(error)})
    return {'headers': headers, 'mapping': mapping, 'missing': [], 'issues': issues, 'rows': valid, 'sheet': sheet}


def sheet_names(filename, raw):
    if Path(filename).suffix.lower() == '.csv':
        return ['']
    book = open_import_workbook(raw)
    try:
        return [sheet.title for sheet in book
                if any(value is not None and str(value).strip()
                       for value in next(sheet.values, ()))]
    finally:
        book.close()


def parse_selection(filename, raw, config):
    if not isinstance(config, dict) or not isinstance(config.get('sheets'), list) or not config['sheets']:
        raise ValueError('请选择要导入的工作表')
    names = [item.get('sheet') for item in config['sheets']]
    if len(set(names)) != len(names):
        raise ValueError('工作表不能重复选择')
    parsed = [parse(filename, raw, item.get('mapping'), item['sheet'], item.get('wide_columns', []),
                    item.get('wide_maximums', {}), item.get('activity', ''))
              for item in config['sheets']]
    return {'rows': [row for sheet in parsed for row in sheet['rows']],
            'issues': [{**issue, 'sheet': sheet['sheet']} for sheet in parsed for issue in sheet['issues']],
            'missing': [{'sheet': sheet['sheet'], 'fields': sheet['missing']} for sheet in parsed if sheet['missing']],
            'mapping': config}


def identity_candidates(conn, class_name, rows):
    cls = conn.execute('SELECT id FROM cd_class WHERE name=?', (class_name.strip(),)).fetchone()
    result = []
    seen = set()
    for row in rows:
        key = json.dumps([row['sheet'], row['line']], ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        ext, name = row.get('student_id', ''), row.get('student_name', '')
        candidates = []
        if cls:
            if ext:
                candidates = conn.execute('SELECT id,external_id,name FROM cd_student WHERE class_id=? AND external_id=?', (cls['id'], ext)).fetchall()
            elif name:
                candidates = conn.execute('SELECT id,external_id,name FROM cd_student WHERE class_id=? AND name=?', (cls['id'], name)).fetchall()
        result.append({'key': key, 'sheet': row['sheet'], 'line': row['line'], 'student_id': ext,
                       'student_name': name, 'candidates': [dict(candidate) for candidate in candidates]})
    return result


async def read_sheet(file):
    raw = await file.read(20_000_001)
    if not raw or len(raw) > 20_000_000:
        raise HTTPException(400, '文件为空或超过 20 MB')
    return Path(file.filename or '').name[:180], raw


@router.post('/preview')
async def preview(file: UploadFile = File(...), mapping: str = Form('')):
    filename, raw = await read_sheet(file)
    try:
        result = parse(filename, raw, json.loads(mapping) if mapping else None)
    except (ValueError, UnicodeError, OSError, BadZipFile) as error:
        raise HTTPException(400, str(error)) from None
    return {**result, 'rows': result['rows'][:20], 'valid_count': len(result['rows']), 'sha256': hashlib.sha256(raw).hexdigest()}


@router.post('/inspect-workbook')
async def inspect_workbook(file: UploadFile = File(...)):
    filename, raw = await read_sheet(file)
    try:
        sheets = []
        for name in sheet_names(filename, raw):
            data = parse(filename, raw, sheet_name=name)
            sheets.append({'sheet': name, 'headers': data['headers'], 'mapping': data['mapping']})
        return {'sheets': sheets, 'sha256': hashlib.sha256(raw).hexdigest()}
    except (ValueError, UnicodeError, OSError, BadZipFile) as error:
        raise HTTPException(400, str(error)) from None


@router.post('/preview-selection')
async def preview_selection(file: UploadFile = File(...), config: str = Form(...), class_name: str = Form(...)):
    filename, raw = await read_sheet(file)
    try:
        parsed = parse_selection(filename, raw, json.loads(config))
    except (ValueError, UnicodeError, OSError, BadZipFile, KeyError, TypeError) as error:
        raise HTTPException(400, str(error)) from None
    with db() as conn:
        init(conn)
        identities = identity_candidates(conn, class_name, parsed['rows'])
    return {**parsed, 'rows': parsed['rows'][:100], 'valid_count': len(parsed['rows']),
            'identities': identities, 'sha256': hashlib.sha256(raw).hexdigest()}


@router.post('/imports')
async def import_file(course_id: str = Form(...), class_name: str = Form(...), mapping: str = Form(...),
                      sha256: str = Form(...), file: UploadFile = File(...), identities: Annotated[str, Form()] = '{}'):
    filename, raw = await read_sheet(file)
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise HTTPException(409, '文件与预览不一致，请重新预览')
    try:
        config = json.loads(mapping)
        choices = json.loads(identities)
        parsed = parse_selection(filename, raw, config) if isinstance(config, dict) and 'sheets' in config else parse(filename, raw, config)
    except (ValueError, UnicodeError, OSError, BadZipFile) as error:
        raise HTTPException(400, str(error)) from None
    if parsed['missing'] or parsed['issues'] or not parsed['rows'] or not class_name.strip():
        raise HTTPException(422, '请先解决预览中的缺失字段和异常，并填写班级')
    with db() as conn:
        init(conn)
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM cd_course WHERE id=?', (course_id,)).fetchone():
            raise HTTPException(404, '课程不存在')
        name = class_name.strip()
        cls = conn.execute('SELECT id FROM cd_class WHERE name=?', (name,)).fetchone()
        class_id = cls['id'] if cls else new_id()
        if not cls:
            conn.execute('INSERT INTO cd_class VALUES (?,?)', (class_id, name))
        normalized_mapping = json.dumps(parsed['mapping'], ensure_ascii=False, sort_keys=True)
        existing = conn.execute('SELECT id, mapping, deleted FROM cd_batch WHERE course_id=? AND class_id=? AND sha256=?',
                                (course_id, class_id, sha256)).fetchall()
        for prior in existing:
            if json.dumps(json.loads(prior['mapping']), ensure_ascii=False, sort_keys=True) == normalized_mapping:
                if prior['deleted']:
                    raise HTTPException(409, '相同文件已在回收站，请先恢复已有批次')
                count = conn.execute('SELECT count(*) FROM cd_record WHERE batch_id=?', (prior['id'],)).fetchone()[0]
                return {'batch_id': prior['id'], 'count': count, 'duplicate': True}
        batch = new_id()
        conn.execute('INSERT INTO cd_batch (id,course_id,class_id,filename,sha256,source,mapping) VALUES (?,?,?,?,?,?,?)',
                     (batch, course_id, class_id, filename, sha256, raw, json.dumps(parsed['mapping'], ensure_ascii=False)))
        resolved = {}
        for row in parsed['rows']:
            ext, student_name = row.get('student_id', ''), row.get('student_name', '')
            row_key = json.dumps([row['sheet'], row['line']], ensure_ascii=False)
            selected_id = choices.get(row_key) if isinstance(choices, dict) else None
            if row_key in resolved:
                candidates = conn.execute('SELECT * FROM cd_student WHERE id=?', (resolved[row_key],)).fetchall()
            elif selected_id and selected_id != 'new':
                candidates = conn.execute('SELECT * FROM cd_student WHERE class_id=? AND id=?', (class_id, selected_id)).fetchall()
                if not candidates or (ext and candidates[0]['external_id'] and candidates[0]['external_id'] != ext):
                    raise HTTPException(409, '所选学生已变化或学号不一致，请重新预览')
            elif ext:
                candidates = conn.execute('SELECT * FROM cd_student WHERE class_id=? AND external_id=?', (class_id, ext)).fetchall()
                if candidates and selected_id == 'new':
                    raise HTTPException(409, '该学号已存在，请重新预览并选择已有学生')
            else:
                candidates = conn.execute('SELECT * FROM cd_student WHERE class_id=? AND name=?', (class_id, student_name)).fetchall()
                if candidates and selected_id != 'new':
                    raise HTTPException(409, f"第 {row['line']} 行姓名与已有学生重复，请核对身份")
                if selected_id == 'new':
                    candidates = []
            if len(candidates) > 1 or (candidates and student_name and candidates[0]['name'] and candidates[0]['name'] != student_name and not selected_id):
                raise HTTPException(409, f"第 {row['line']} 行学生身份存在歧义，请核对学号和姓名")
            student = candidates[0]['id'] if candidates else new_id()
            if not candidates:
                conn.execute('INSERT INTO cd_student VALUES (?,?,?,?)', (student, class_id, ext or None, student_name))
            elif student_name and not candidates[0]['name']:
                conn.execute('UPDATE cd_student SET name=? WHERE id=?', (student_name, student))
            resolved[row_key] = student
            conn.execute('INSERT OR IGNORE INTO cd_enrollment VALUES (?,?)', (course_id, student))
            kind = 'comment' if row['score'] is None else ('question' if row.get('question') else 'total')
            conn.execute('INSERT INTO cd_record VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (new_id(), batch, student, kind, row.get('activity', ''), row.get('question', ''),
                          row['score'], row['maximum'], row.get('comment', ''), row['sheet'], row['line']))
    return {'batch_id': batch, 'count': len(parsed['rows'])}


@router.get('/courses/{course_id}/records')
def records(course_id: str):
    with db() as conn:
        init(conn)
        rows = conn.execute('''SELECT r.*, s.external_id, s.name AS student_name, c.name AS class_name
            FROM cd_record r JOIN cd_batch b ON b.id=r.batch_id JOIN cd_student s ON s.id=r.student_id
            JOIN cd_class c ON c.id=s.class_id WHERE b.course_id=? AND b.deleted=0 ORDER BY b.created DESC,r.line''', (course_id,))
        return [dict(row) for row in rows]


def profile_in_connection(conn, course_id, student_id='', class_name='', activity=''):
    course = conn.execute('SELECT * FROM cd_course WHERE id=?', (course_id,)).fetchone()
    if course is None:
        raise HTTPException(404, '课程不存在')
    students = [dict(row) for row in conn.execute('''SELECT DISTINCT s.id, s.name, s.external_id, c.name AS class_name
        FROM cd_record r JOIN cd_batch b ON b.id=r.batch_id
        JOIN cd_student s ON s.id=r.student_id JOIN cd_class c ON c.id=s.class_id
        WHERE b.course_id=? AND b.deleted=0 ORDER BY c.name,s.name,s.external_id''', (course_id,))]
    if student_id and not any(student['id'] == student_id for student in students):
        raise HTTPException(404, '该课程中没有此学生的有效记录')
    rows = conn.execute('''SELECT r.id,r.kind,r.activity,r.question,r.score,r.maximum,r.comment,r.sheet,r.line,
        s.id AS student_id,s.name AS student_name,s.external_id,c.name AS class_name,b.filename
        FROM cd_record r JOIN cd_batch b ON b.id=r.batch_id JOIN cd_student s ON s.id=r.student_id
        JOIN cd_class c ON c.id=s.class_id WHERE b.course_id=? AND b.deleted=0
        AND (?='' OR s.id=?) AND (?='' OR c.name=?) AND (?='' OR r.activity=?)
        ORDER BY b.created DESC,r.sheet,r.line,r.id''',
        (course_id, student_id, student_id, class_name, class_name, activity, activity))
    return {'course': dict(course), 'students': students, 'records': [dict(row) for row in rows]}



@router.get('/courses/{course_id}/profile')
def course_profile(course_id: str, student_id: str = '', class_name: str = '', activity: str = ''):
    with db() as conn:
        init(conn)
        return profile_in_connection(conn, course_id, student_id, class_name, activity)


def course_chat_answer(course_id, student_id, question):
    data = course_profile(course_id, student_id)
    mentioned = [item for item in data['students'] if item['name'] and len(item['name']) >= 2 and item['name'] in question]
    if mentioned and (len(mentioned) != 1 or mentioned[0]['id'] != student_id):
        return '问题中的学生与当前选择不一致或存在同名，请先选择要查询的学生。', 0
    if not student_id:
        return '请选择课程中的学生后查询成绩或评价。', 0
    student = next(item for item in data['students'] if item['id'] == student_id)
    rows = data['records']
    lines = []
    for row in rows[:30]:
        fields = [row['activity'], row['question'] or ('总分' if row['kind'] == 'total' else '评价')]
        if row['score'] is not None:
            fields.append('得分 ' + row['score'] + (('/' + row['maximum']) if row['maximum'] is not None else '（未提供满分）'))
        if row['comment']:
            fields.append('评价：' + row['comment'])
        lines.append(' · '.join(filter(None, fields)))
    label = student['name'] or student['external_id'] or '该学生'
    answer = f"{data['course']['name']} · {student['class_name']} · {label}，共有 {len(rows)} 条记录。"
    if lines:
        answer += '\n' + '\n'.join(lines)
    if len(rows) > len(lines):
        answer += '\n仅显示前 30 条，请在画像中查看全部记录。'
    return answer, len(rows)


@router.get('/courses/{course_id}/batches')
def batches(course_id: str):
    with db() as conn:
        init(conn)
        return [dict(row) for row in conn.execute('''SELECT b.id,b.filename,b.created,b.deleted,c.name AS class_name,
            count(r.id) AS count FROM cd_batch b JOIN cd_class c ON c.id=b.class_id
            LEFT JOIN cd_record r ON r.batch_id=b.id WHERE b.course_id=? GROUP BY b.id ORDER BY b.created DESC''', (course_id,))]


@router.post('/batches/{batch_id}/{action}')
def change_batch(batch_id: str, action: str):
    if action not in ('trash', 'restore'):
        raise HTTPException(404, '操作不存在')
    with db() as conn:
        init(conn)
        changed = conn.execute('UPDATE cd_batch SET deleted=? WHERE id=?', (int(action == 'trash'), batch_id)).rowcount
        if not changed:
            raise HTTPException(404, '导入批次不存在')
    return {'id': batch_id, 'deleted': action == 'trash'}
