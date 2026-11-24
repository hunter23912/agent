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

ROOT = Path(__file__).resolve().parents[1]

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
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,title TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,role TEXT,content TEXT,sources TEXT DEFAULT '[]',trace TEXT DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS reading_state(paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,status TEXT DEFAULT '待读',tags TEXT DEFAULT '',priority INTEGER DEFAULT 2);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,title TEXT NOT NULL,paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,done INTEGER DEFAULT 0,created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,kind TEXT,content TEXT,paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,created TEXT DEFAULT CURRENT_TIMESTAMP);
            ''')

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
        existing = self.rows('SELECT id FROM papers WHERE sha=?', (sha,))
        if existing:
            return existing[0]['id'], False, []
        suffix = Path(name).suffix.lower()
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
            db.execute('INSERT INTO papers(id,title,filename,sha,pages) VALUES(?,?,?,?,?)', (pid,title,Path(name).name,sha,len(pages)))
            for page, text in pages:
                text = re.sub(r'[ \t]+', ' ', text).strip()
                db.execute('INSERT INTO pages VALUES(?,?,?)', (pid,page,text))
                for index, start in enumerate(range(0, len(text), 800)):
                    chunk = text[start:start + 1000]
                    if chunk.strip():
                        db.execute('INSERT INTO chunks VALUES(?,?,?,?)', (f'{pid}-{page}-{index}',pid,page,chunk))
        return pid, True, warnings

    def delete_paper(self, pid):
        with self.connect() as db:
            db.execute('DELETE FROM papers WHERE id=?', (pid,))

    def read_page(self, pid, page):
        return self.rows('SELECT p.title, g.paper_id, g.page, g.text FROM pages g JOIN papers p ON p.id=g.paper_id WHERE g.paper_id=? AND g.page=?', (pid,page))

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

    def update_memory(self,mid,content):
        if not content.strip(): raise ValueError('记忆不能为空')
        with self.connect() as db: db.execute('UPDATE memories SET content=? WHERE id=?',(content.strip()[:10000],mid))

    def new_session(self, title='新的阅读对话'):
        sid = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute('INSERT INTO sessions(id,title) VALUES(?,?)', (sid,title[:80]))
        return sid

    def messages(self, sid):
        rows = self.rows('SELECT * FROM messages WHERE session_id=? ORDER BY id', (sid,))
        for row in rows:
            row['sources'] = json.loads(row['sources'])
            row['trace'] = json.loads(row['trace'])
        return rows

    def add_message(self, sid, role, content, sources=None, trace=None):
        with self.connect() as db:
            db.execute('INSERT INTO messages(session_id,role,content,sources,trace) VALUES(?,?,?,?,?)', (sid,role,content,json.dumps(sources or [],ensure_ascii=False),json.dumps(trace or [],ensure_ascii=False)))
            if role == 'user':
                db.execute("UPDATE sessions SET title=? WHERE id=? AND title='新的阅读对话'", (content[:45],sid))

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
