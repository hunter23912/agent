import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfWriter
from paper_agent.store import Store, ROOT
from paper_agent.agent import Agent, Model, ModelError

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
        self.assertIn('second page',self.store.read_page(pid,2)[0]['text'])
        result = self.store.search('experiment',[pid])
        self.assertEqual(result[0]['page'],2)

    def test_agent_function_call_scope_and_citations(self):
        pid = self.ids[0]
        class Fake:
            count=0
            def complete(inner,messages,tools=None):
                inner.count+=1
                if inner.count==1:
                    return {'content':None,'tool_calls':[{'id':'t1','type':'function','function':{'name':'read_page','arguments':'{"paper_id":"outside","page":1}'}},{'id':'t2','type':'function','function':{'name':'read_page','arguments':f'{{"paper_id":"{pid}","page":1}}'}}]}
                return {'content':f'方法见 [{pid}-p1]。伪造引用 [fake-1]。'}
        result = Agent(self.store,Fake()).run('方法',self.ids)
        self.assertTrue(any(t['status']=='失败' for t in result['trace']))
        self.assertIn('[fake-1]（引用未验证）',result['answer'])
        self.assertIn(f'[{pid}-p1]',result['answer'])

    def test_model_http_error_does_not_leak_secret(self):
        with patch('paper_agent.agent.requests.post') as post:
            post.return_value.status_code=401
            post.return_value.text='private-secret'
            with self.assertRaises(ModelError) as caught:
                Model('https://example.org/v1','private-secret','test').complete([])
            self.assertNotIn('private-secret',str(caught.exception))

if __name__=='__main__':
    unittest.main()
