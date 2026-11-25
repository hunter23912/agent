from __future__ import annotations
import hashlib
import io
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from .chunking import split_page

ROOT = Path(__file__).resolve().parents[1]
CHUNK_SCHEMA_VERSION = 1

class Store:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.environ.get('PAPER_AGENT_DATA', ROOT / 'data'))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'library.sqlite3'
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY,title TEXT NOT NULL,filename TEXT,sha TEXT UNIQUE,pages INTEGER,created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS pages(paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,page INTEGER,text TEXT,PRIMARY KEY(paper_id,page));
            CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,page INTEGER,text TEXT);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,title TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,paper_ids TEXT);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,role TEXT,content TEXT,sources TEXT DEFAULT '[]',trace TEXT DEFAULT '[]',paper_ids TEXT);
            CREATE TABLE IF NOT EXISTS reading_state(paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,status TEXT DEFAULT '待读',tags TEXT DEFAULT '',priority INTEGER DEFAULT 2);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,title TEXT NOT NULL,paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,done INTEGER DEFAULT 0,created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,kind TEXT,content TEXT,paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS original_pdfs(paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,content BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS research_results(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,settings TEXT NOT NULL,papers TEXT NOT NULL,result TEXT NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS research_results_kind_index ON research_results(kind,id);
            ''')
            tables = ('sessions', 'messages')
            needs_migration = any(
                'paper_ids' not in {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                for table in tables
            )
            if needs_migration:
                db.execute('BEGIN IMMEDIATE')
                for table in tables:
                    if 'paper_ids' not in {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN paper_ids TEXT')
            self._upgrade_chunks(db)

    def _upgrade_chunks(self, db):
        if db.execute('PRAGMA user_version').fetchone()[0] >= CHUNK_SCHEMA_VERSION:
            return
        if not db.in_transaction:
            db.execute('BEGIN IMMEDIATE')
        # Another session may have rebuilt the index while this one waited for the lock.
        if db.execute('PRAGMA user_version').fetchone()[0] >= CHUNK_SCHEMA_VERSION:
            return
        # Only chunks are derived from pages and can be regenerated here.
        db.execute('DELETE FROM chunks')
        for row in db.execute('SELECT paper_id,page,text FROM pages ORDER BY paper_id,page'):
            self._insert_chunks(db, row['paper_id'], row['page'], row['text'])
        db.execute(f'PRAGMA user_version={CHUNK_SCHEMA_VERSION}')

    @staticmethod
    def _insert_chunks(db, paper_id, page, text):
        db.executemany('INSERT INTO chunks VALUES(?,?,?,?)', (
            # The suffix distinguishes rebuilt evidence from archived fixed-window excerpts.
            (f'{paper_id}-{page}-s{index}', paper_id, page, chunk)
            for index, chunk in enumerate(split_page(text))
        ))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args).fetchall()]

    def papers(self):
        return self.rows('SELECT p.*, (SELECT COUNT(*) FROM chunks c WHERE c.paper_id=p.id) AS chunks FROM papers p ORDER BY created DESC, title')

    def ingest(self, name, data):
        if len(data) > 30 * 1024 * 1024:
            raise ValueError('单个文件不能超过 30 MB。')
        sha = hashlib.sha256(data).hexdigest()
        suffix = Path(name).suffix.lower()
        existing = self.rows('SELECT id FROM papers WHERE sha=?', (sha,))
        if existing:
            if suffix == '.pdf':
                with self.connect() as db:
                    db.execute('INSERT OR IGNORE INTO original_pdfs VALUES(?,?)', (existing[0]['id'], data))
            return existing[0]['id'], False, []
        warnings = []
        if suffix == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(''):
                raise ValueError('PDF 已加密，请先解除密码保护。')
            if len(reader.pages) > 600:
                raise ValueError('当前版本支持最多 600 页的单篇论文。')
            pages = [(i + 1, p.extract_text() or '') for i, p in enumerate(reader.pages)]
            blanks = [i for i, t in pages if not t.strip()]
            if blanks:
                warnings.append(f'{len(blanks)} 页无可提取文字；扫描件需要先 OCR，图表不参与图像理解。')
        elif suffix in {'.txt', '.md'}:
            try:
                content = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                content = data.decode('gb18030')
            pages = [(i + 1, content[start:start + 4500]) for i, start in enumerate(range(0, len(content), 4500))]
            warnings.append('文本文件按每 4500 字符划分虚拟页。')
        else:
            raise ValueError('仅支持 PDF、TXT 和 Markdown 文件。')
        if not any(t.strip() for _, t in pages):
            raise ValueError('未提取到文字。扫描 PDF 请先 OCR。')
        pid = uuid.uuid4().hex[:12]
        title = Path(name).stem
        with self.connect() as db:
            inserted = db.execute('INSERT INTO papers(id,title,filename,sha,pages) VALUES(?,?,?,?,?) ON CONFLICT(sha) DO NOTHING', (pid,title,Path(name).name,sha,len(pages)))
            if not inserted.rowcount:
                existing_id = db.execute('SELECT id FROM papers WHERE sha=?', (sha,)).fetchone()['id']
                if suffix == '.pdf':
                    db.execute('INSERT OR IGNORE INTO original_pdfs VALUES(?,?)', (existing_id, data))
                return existing_id, False, []
            if suffix == '.pdf':
                db.execute('INSERT INTO original_pdfs VALUES(?,?)', (pid, data))
            for page, text in pages:
                text = re.sub(r'[ \t]+', ' ', text).strip()
                db.execute('INSERT INTO pages VALUES(?,?,?)', (pid,page,text))
                self._insert_chunks(db, pid, page, text)
        return pid, True, warnings

    def delete_paper(self, pid):
        with self.connect() as db:
            db.execute('DELETE FROM papers WHERE id=?', (pid,))

    def read_page(self, pid, page):
        return self.rows('SELECT p.title, g.paper_id, g.page, g.text FROM pages g JOIN papers p ON p.id=g.paper_id WHERE g.paper_id=? AND g.page=?', (pid,page))

    def original_pdf(self, pid):
        rows = self.rows('SELECT content FROM original_pdfs WHERE paper_id=?', (pid,))
        return rows[0]['content'] if rows else None

    def search(self, query, paper_ids, limit=6, mode='hybrid', expand=True, diversity=True):
        from .retrieval import retrieve
        if not paper_ids: return []
        placeholders = ','.join('?' for _ in paper_ids)
        rows = self.rows(f'SELECT c.*,p.title FROM chunks c JOIN papers p ON p.id=c.paper_id WHERE paper_id IN ({placeholders})', tuple(paper_ids))
        return retrieve(rows,query,limit,mode,expand,diversity)

    def outline(self, pid):
        pages = self.rows('SELECT page,text FROM pages WHERE paper_id=? ORDER BY page',(pid,))
        found=[]
        pattern=re.compile(r'^(?:#{1,6}\s+|(?:\d+(?:\.\d+)*[. ]+)?(?:abstract|introduction|related work|method|experiment|result|discussion|conclusion|limitation|references|摘要|引言|方法|实验|结论|参考文献))',re.I)
        for page in pages:
            for line in page['text'].splitlines():
                line=line.strip()
                if 2<len(line)<150 and pattern.search(line):
                    found.append({'page':page['page'],'heading':line})
        return found[:80]

    def reading_state(self, pid):
        rows=self.rows('SELECT * FROM reading_state WHERE paper_id=?',(pid,))
        return rows[0] if rows else {'paper_id':pid,'status':'待读','tags':'','priority':2}

    def set_reading_state(self,pid,status,tags,priority):
        if status not in ('待读','精读中','已读'): raise ValueError('未知阅读状态')
        if int(priority) not in (1,2,3): raise ValueError('未知优先级')
        with self.connect() as db:
            db.execute('INSERT INTO reading_state VALUES(?,?,?,?) ON CONFLICT(paper_id) DO UPDATE SET status=excluded.status,tags=excluded.tags,priority=excluded.priority',(pid,status,tags[:300],int(priority)))

    def add_task(self,title,paper_id=None):
        if not title.strip(): raise ValueError('任务标题不能为空')
        tid=uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute('INSERT INTO tasks(id,title,paper_id) VALUES(?,?,?)',(tid,title.strip()[:300],paper_id))
        return tid

    def set_task(self,tid,done):
        with self.connect() as db: db.execute('UPDATE tasks SET done=? WHERE id=?',(int(bool(done)),tid))

    def delete_task(self,tid):
        with self.connect() as db: db.execute('DELETE FROM tasks WHERE id=?',(tid,))

    def update_memory(self,mid,content):
        if not content.strip(): raise ValueError('记忆不能为空')
        with self.connect() as db: db.execute('UPDATE memories SET content=? WHERE id=?',(content.strip()[:10000],mid))

    def new_session(self, title='新的阅读对话'):
        sid = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute('INSERT INTO sessions(id,title) VALUES(?,?)', (sid,title[:80]))
        return sid

    def session_paper_ids(self, sid):
        rows = self.rows('SELECT paper_ids FROM sessions WHERE id=?', (sid,))
        value = rows[0]['paper_ids'] if rows else None
        return json.loads(value) if value is not None else None

    def set_session_paper_ids(self, sid, paper_ids):
        with self.connect() as db:
            db.execute('UPDATE sessions SET paper_ids=? WHERE id=?', (json.dumps(paper_ids), sid))

    def messages(self, sid):
        rows = self.rows('SELECT * FROM messages WHERE session_id=? ORDER BY id', (sid,))
        for row in rows:
            row['sources'] = json.loads(row['sources'])
            row['trace'] = json.loads(row['trace'])
            if row['paper_ids'] is not None:
                row['paper_ids'] = json.loads(row['paper_ids'])
        return rows

    def add_message(self, sid, role, content, sources=None, trace=None):
        with self.connect() as db:
            self._insert_message(db, sid, role, content, sources or [], trace or [], None)

    def add_exchange(self, sid, question, result, paper_ids):
        with self.connect() as db:
            scope = json.dumps(paper_ids)
            self._insert_message(db, sid, 'user', question, [], [], scope)
            self._insert_message(db, sid, 'assistant', result['answer'], result['sources'], result['trace'], scope)

    @staticmethod
    def _insert_message(db, sid, role, content, sources, trace, scope):
        db.execute(
            'INSERT INTO messages(session_id,role,content,sources,trace,paper_ids) VALUES(?,?,?,?,?,?)',
            (sid, role, content, json.dumps(sources, ensure_ascii=False), json.dumps(trace, ensure_ascii=False), scope),
        )
        if role == 'user':
            db.execute("UPDATE sessions SET title=? WHERE id=? AND title='新的阅读对话'", (content[:45], sid))

    def save_research_result(self, kind, settings, papers, result):
        with self.connect() as db:
            return db.execute(
                'INSERT INTO research_results(kind,settings,papers,result) VALUES(?,?,?,?)',
                (kind, json.dumps(settings, ensure_ascii=False, sort_keys=True),
                 json.dumps(papers, ensure_ascii=False), json.dumps(result, ensure_ascii=False)),
            ).lastrowid

    def research_results(self, kind):
        rows = self.rows('SELECT id,settings,papers,created FROM research_results WHERE kind=? ORDER BY id DESC', (kind,))
        for row in rows:
            row['settings'] = json.loads(row['settings'])
            row['papers'] = json.loads(row['papers'])
        return rows

    def research_result(self, result_id):
        rows = self.rows('SELECT result FROM research_results WHERE id=?', (result_id,))
        return json.loads(rows[0]['result']) if rows else None

    def memories(self, query='', paper_ids=None):
        rows = self.rows('SELECT * FROM memories ORDER BY created DESC, id DESC')
        if paper_ids is not None:
            rows = [r for r in rows if not r['paper_id'] or r['paper_id'] in paper_ids]
        if query:
            terms = set(re.findall(r'\w+|[\u4e00-\u9fff]', query.lower()))
            rows.sort(key=lambda r: sum(t in r['content'].lower() for t in terms), reverse=True)
        return rows[:30]

    def save_memory(self, content, kind='阅读笔记', paper_id=None):
        content = content.strip()
        if not content:
            raise ValueError('记忆不能为空。')
        mid = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute('INSERT INTO memories(id,kind,content,paper_id) VALUES(?,?,?,?)', (mid,kind,content[:10000],paper_id))
        return mid

    def delete_memory(self, mid):
        with self.connect() as db:
            db.execute('DELETE FROM memories WHERE id=?', (mid,))
