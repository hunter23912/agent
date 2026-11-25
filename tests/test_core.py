import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfWriter
from paper_agent.store import Store, ROOT
from paper_agent.agent import Agent, Model, ModelError
from paper_agent.chunking import split_page
from paper_agent.citations import CITATION
from paper_agent.pdf_preview import reflow_pdf_text

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.ids = [self.store.ingest(p.name,p.read_bytes())[0] for p in (ROOT/'examples').glob('*.md')]

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_and_search_scope(self):
        p = next((ROOT/'examples').glob('*.md'))
        self.assertFalse(self.store.ingest(p.name,p.read_bytes())[1])
        results = self.store.search('method 检索',self.ids[:1])
        self.assertTrue(results)
        self.assertTrue(all(r['paper_id']==self.ids[0] for r in results))
        self.assertEqual(self.store.search('zzzzzzzzzz',self.ids),[])

    def test_chunking_prefers_paragraphs_and_overlaps_last_sentence(self):
        last_sentence = '对照实验使用统一的数据划分' * 8 + '。'
        first_paragraph = '这里说明论文的方法与实验条件' * 52 + '。' + last_sentence
        second_paragraph = '这些实验分别报告模型精度和召回率' * 10 + '。' + '应用同一评测条件' * 15 + '。'
        chunks = split_page(first_paragraph + '\n\n' + second_paragraph)
        self.assertEqual(chunks, [first_paragraph, last_sentence + '\n\n' + second_paragraph])

    def test_chunking_skips_abbreviations_and_bounds_long_unbroken_text(self):
        first_sentence = 'Measurements used fixed camera settings across all experiments. ' * 12
        first_sentence = first_sentence.strip()
        second_sentence = ('In the published analysis of that benchmark, Fig. 2 shows results from Yu et al. using a value of 0.825 '
                           + 'without relying on a different experimental setup ' * 6 + 'and verifies the conclusion.')
        chunks = split_page(first_sentence + ' ' + second_sentence)
        self.assertEqual(chunks[0], first_sentence)

        unbroken = '连续文本用于测试' * 330
        chunks = split_page(unbroken)
        self.assertTrue(all(0 < len(chunk) <= 1000 for chunk in chunks))
        self.assertEqual(chunks[0] + ''.join(chunk[200:] for chunk in chunks[1:]), unbroken)
        self.assertEqual(split_page('  \n\t '), [])

    def test_existing_chunk_index_is_rebuilt_without_changing_saved_excerpts(self):
        page_text = ('The previous encoder was evaluated under a fixed benchmark. ' * 14
                     + '\n\nA unique retrieval metric was reported. ' + 'Additional context about the metric. ' * 9).strip()
        pid = self.store.ingest('existing-paper.txt', page_text.encode())[0]
        raw_page = self.store.read_page(pid, 1)[0]
        legacy_id = f'{pid}-1-0'
        legacy = {'id': legacy_id, 'paper_id': pid, 'page': 1, 'text': raw_page['text'][:1000], 'citation': f'[{legacy_id}]'}
        sid = self.store.new_session()
        self.store.add_message(sid, 'assistant', legacy['citation'], [legacy])
        with self.store.connect() as db:
            db.execute('DELETE FROM chunks WHERE paper_id=?', (pid,))
            db.execute('INSERT INTO chunks VALUES(?,?,?,?)', (legacy_id, pid, 1, legacy['text']))
            db.execute('PRAGMA user_version=0')

        upgraded = Store(self.tmp.name)
        self.assertEqual(upgraded.read_page(pid, 1)[0], raw_page)
        self.assertEqual(upgraded.messages(sid)[0]['sources'], [legacy])
        results = upgraded.search('unique retrieval metric', [pid])
        self.assertTrue(results)
        self.assertIn('unique retrieval metric', results[0]['text'])
        self.assertRegex(results[0]['id'], rf'^{pid}-1-s\d+$')
        self.assertEqual(CITATION.findall(results[0]['citation'] + ' ' + legacy['citation']), [results[0]['id'], legacy_id])
        self.assertEqual(Store(self.tmp.name).search('unique retrieval metric', [pid]), results)

    def test_memory_and_conversation_persistence(self):
        mid = self.store.save_memory('请用通俗中文解释','阅读偏好')
        sid = self.store.new_session()
        self.store.add_message(sid,'user','方法是什么？')
        other = Store(self.tmp.name)
        self.assertEqual(other.messages(sid)[0]['content'],'方法是什么？')
        self.assertEqual(other.memories()[0]['id'],mid)
        other.delete_memory(mid)
        self.assertEqual(other.memories(),[])

    def test_deletion_cascades(self):
        self.store.save_memory('笔记',paper_id=self.ids[0])
        self.store.delete_paper(self.ids[0])
        self.assertEqual(self.store.read_page(self.ids[0],1),[])
        self.assertEqual(self.store.memories(),[])
        self.assertEqual(self.store.search('检索',[self.ids[0]]),[])

    def test_local_comparison_balanced(self):
        result = Agent(self.store).run('method 方法 局限性',self.ids,compare=True)
        self.assertEqual({r['paper_id'] for r in result['sources']},set(self.ids))
        self.assertIn('未调用大模型',result['answer'])
        self.assertEqual(len([t for t in result['trace'] if t['tool']!='stage']),3)

    def test_blank_pdf_rejected(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100,height=100)
        buffer = io.BytesIO()
        writer.write(buffer)
        with self.assertRaisesRegex(ValueError,'未提取到文字'):
            self.store.ingest('blank.pdf',buffer.getvalue())

    def test_pdf_preview_reflows_prose_but_preserves_fixed_lines(self):
        raw = ('1. Introduction\n'
               'This method uses simple comparisons and funda\u00ad\n'
               'mentally consistent measures in research.\n\n'
               'Typical experiments with human participants use non-\n'
               'invasive sensors to measure brain signals.\n\n'
               'Table 1. Accuracy\n'
               'Baseline 70.0 0.10\n'
               'Encoder 72.6 0.08\n'
               'y = x + 1')
        # Old imports and a PDF that cannot be re-opened use the stored text.
        for original in (None, b'Not a PDF'):
            preview = reflow_pdf_text(raw, original)
            self.assertIn('fundamentally consistent', preview)
            self.assertIn('non-invasive sensors', preview)
            self.assertIn('1. Introduction\n\nThis method', preview)
            self.assertIn('Table 1. Accuracy\nBaseline 70.0 0.10\nEncoder 72.6 0.08\ny = x + 1', preview)

    def test_pdf_preview_preserves_column_and_paragraph_boundaries(self):
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        content = DecodedStreamObject()
        content.set_data(b'BT /F1 10 Tf '
                         b'1 0 0 1 60 700 Tm (Visual retrieval combines two representations) Tj '
                         b'1 0 0 1 60 688 Tm (when modeling semantic features.) Tj '
                         b'1 0 0 1 70 669 Tm (The next paragraph starts here.) Tj '
                         b'1 0 0 1 320 700 Tm (The second column starts here.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(content)
        output = io.BytesIO()
        writer.write(output)
        original = output.getvalue()
        pid = self.store.ingest('columns.pdf', original)[0]
        raw = self.store.read_page(pid, 1)[0]['text']
        preview = reflow_pdf_text(raw, original)
        self.assertIn('representations when modeling semantic features.\n\nThe next paragraph', preview)
        self.assertIn('starts here.\n\nThe second column', preview)

    def test_pdf_page_provenance(self):
        # Valid, minimal text PDF with a standard font, generated in memory.
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        writer = PdfWriter()
        for line in ['method retrieval first page','experiment second page']:
            page = writer.add_blank_page(width=400,height=400)
            font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f'BT /F1 12 Tf 20 300 Td ({line}) Tj ET'.encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
        buffer=io.BytesIO()
        writer.write(buffer)
        pid,_,_ = self.store.ingest('sample.pdf',buffer.getvalue())
        self.assertEqual(self.store.original_pdf(pid), buffer.getvalue())
        self.assertIn('second page',self.store.read_page(pid,2)[0]['text'])
        result = self.store.search('experiment',[pid])
        self.assertEqual(result[0]['page'],2)
        with self.store.connect() as db:
            db.execute('DELETE FROM original_pdfs WHERE paper_id=?', (pid,))
        self.assertEqual(self.store.ingest('sample.pdf', buffer.getvalue())[:2], (pid, False))
        self.assertEqual(self.store.original_pdf(pid), buffer.getvalue())
        self.store.delete_paper(pid)
        self.assertIsNone(self.store.original_pdf(pid))

    def test_old_database_preserves_history_when_adding_paper_scopes(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite3.connect(Path(directory) / 'library.sqlite3')
            connection.executescript('''
                CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,sources TEXT DEFAULT '[]',trace TEXT DEFAULT '[]');
                INSERT INTO sessions(id,title) VALUES('old','旧论文对话');
                INSERT INTO messages(session_id,role,content) VALUES('old','user','旧问题');
            ''')
            connection.close()
            store = Store(directory)
            self.assertIsNone(store.session_paper_ids('old'))
            self.assertEqual(store.messages('old')[0]['content'], '旧问题')
            self.assertIsNone(store.messages('old')[0]['paper_ids'])
            store.set_session_paper_ids('old', ['paper'])
            store.add_exchange('old', '新问题', {'answer': '新回答', 'sources': [], 'trace': []}, ['paper'])
            self.assertEqual(Store(directory).session_paper_ids('old'), ['paper'])
            self.assertEqual([row['paper_ids'] for row in store.messages('old')], [None, ['paper'], ['paper']])

    def test_agent_function_call_scope_and_citations(self):
        pid = self.ids[0]
        class Fake:
            count=0
            def complete(inner,messages,tools=None):
                inner.count+=1
                if inner.count==1:
                    return {'content':None,'tool_calls':[{'id':'t1','type':'function','function':{'name':'read_page','arguments':'{"paper_id":"outside","page":1}'}},{'id':'t2','type':'function','function':{'name':'read_page','arguments':f'{{"paper_id":"{pid}","page":1}}'}}]}
                if inner.count == 2:
                    return {'content':f'方法见 [{pid}-p1]。伪造引用 [000000000000-p99]。'}
                return {'content':'{"issues": []}'}
        result = Agent(self.store,Fake()).run('方法',self.ids)
        self.assertTrue(any(t['status']=='失败' for t in result['trace']))
        self.assertIn('引用编号 [000000000000-p99] 不在本次证据中', result['answer'])
        self.assertIn(f'[{pid}-p1]',result['answer'])

    def test_followup_search_filters_history_by_paper_scope(self):
        pid = self.store.ingest('encoder.txt', b'Method: Falcon encoder. Accuracy 72.6 percent.')[0]
        requests = []

        class Fake:
            def complete(self, messages, tools=None):
                requests.append(messages)
                return {'content': '已收到。'}

        history = [
            {'role': 'user', 'content': '另一篇论文的旧问题', 'paper_ids': self.ids},
            {'role': 'user', 'content': '范围未知的旧问题', 'paper_ids': None},
            {'role': 'user', 'content': 'Falcon encoder method 与 accuracy 是什么？', 'paper_ids': [pid]},
        ]
        result = Agent(self.store, Fake()).run('它呢？', [pid], history=history)
        self.assertEqual([message['content'] for message in requests[0][1:-1]], [history[-1]['content']])
        self.assertTrue(any('Falcon encoder' in source['text'] for source in result['sources']))
        self.assertTrue(any(event.get('title') == '补充追问上下文' for event in result['trace']))

    def test_citation_review_detects_unfounded_values_and_keeps_math_notation(self):
        pid = self.store.ingest('metrics.txt', b'Evaluation: accuracy 72.6 percent. Dataset Alpha.')[0]

        class Fake:
            def complete(self, messages, tools=None):
                if messages[0]['content'].startswith('核对论文回答'):
                    return {'content': '{"issues":[{"claim":"数据集为 Beta","reason":"原文写的是 Alpha"}]}'}
                raw = messages[-1]['content'].split('本次可用资料（JSON）：\n', 1)[1]
                cite = json.loads(raw)['evidence'][0]['citation']
                return {'content': f'准确率为 99% {cite}。数据集为 Beta {cite}。值域是 [0, 1]。'}

        result = Agent(self.store, Fake()).run('evaluation accuracy dataset', [pid])
        self.assertIn('数值 99% 未在', result['answer'])
        self.assertIn('原文写的是 Alpha', result['answer'])
        self.assertNotIn('引用编号 [0, 1]', result['answer'])
        self.assertEqual(result['trace'][-1]['status'], '需核对')

    def test_model_http_error_does_not_leak_secret(self):
        with patch('paper_agent.agent.requests.post') as post:
            post.return_value.status_code=401
            post.return_value.text='private-secret'
            with self.assertRaises(ModelError) as caught:
                Model('https://example.org/v1','private-secret','test').complete([])
            self.assertNotIn('private-secret',str(caught.exception))

if __name__=='__main__':
    unittest.main()
