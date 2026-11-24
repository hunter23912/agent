import tempfile
import unittest
from paper_agent.store import Store,ROOT
from paper_agent.agent import Agent
from paper_agent.calculator import calculate
from paper_agent.retrieval import build_index

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(self.tmp.name)
        self.pid=self.store.ingest('methods.txt',b'Abstract\nA retrieval method with memory.\nMethod\nBM25 ranks documents.\nExperiments\nThe benchmark evaluates retrieval accuracy.')[0]
    def tearDown(self): self.tmp.cleanup()
    def test_hybrid_modes_and_scope(self):
        other=self.store.ingest('other.txt',b'An unrelated paper about birds and feathers.')[0]
        for mode in ['hybrid','tfidf','bm25']:
            hits=self.store.search('retrieval',[self.pid],mode=mode)
            self.assertTrue(hits)
            self.assertTrue(all(h['paper_id']==self.pid for h in hits))
        self.assertEqual(self.store.search('zzzzzz',[self.pid,other]),[])
        self.assertTrue(self.store.search('方法',[self.pid],mode='bm25',expand=True))
        self.assertEqual(self.store.search('方法',[self.pid],mode='bm25',expand=False),[])
    def test_index_cache_and_delete(self):
        self.store.search('method',[self.pid]); before=build_index.cache_info().hits
        self.store.search('method',[self.pid])
        self.assertGreater(build_index.cache_info().hits,before)
        self.store.delete_paper(self.pid)
        self.assertEqual(self.store.search('method',[self.pid]),[])
    def test_state_tasks_memory(self):
        self.store.set_reading_state(self.pid,'精读中','RAG',1)
        task=self.store.add_task('核对实验',self.pid)
        self.store.set_task(task,True)
        mid=self.store.save_memory('旧笔记',paper_id=self.pid)
        self.store.update_memory(mid,'新笔记')
        reopened=Store(self.tmp.name)
        self.assertEqual(reopened.reading_state(self.pid)['status'],'精读中')
        self.assertEqual(reopened.rows('SELECT done FROM tasks')[0]['done'],1)
        self.assertEqual(reopened.memories()[0]['content'],'新笔记')
        self.store.delete_paper(self.pid)
        self.assertFalse(self.store.rows('SELECT * FROM tasks'))
        self.assertFalse(self.store.rows('SELECT * FROM reading_state'))
    def test_safe_calculation(self):
        self.assertAlmostEqual(calculate('(85.2-81.6)/81.6*100')['value'],4.41176470588)
        for expr in ['__import__("os")','1/0','2**1000','[1,2]','(1).__class__','True+1']:
            with self.assertRaises(ValueError): calculate(expr)
    def test_outline_and_tools(self):
        self.assertTrue(self.store.outline(self.pid))
        pid=self.pid
        class FakeModel:
            count=0
            def complete(inner,messages,tools=None):
                inner.count+=1
                if inner.count==1:
                    return {'content':None,'reasoning_content':'protocol-test','tool_calls':[
                        {'id':'a','type':'function','function':{'name':'paper_outline','arguments':'{"paper_id":"outside"}'}},
                        {'id':'b','type':'function','function':{'name':'calculate','arguments':'{"expression":"2+3"}'}}]}
                assert messages[-3]['reasoning_content']=='protocol-test'
                return {'content':'计算结果为 5，论文证据不足。'}
        r=Agent(self.store,FakeModel()).run('method',[pid])
        self.assertTrue(any(t['tool']=='paper_outline' and t['status']=='失败' for t in r['trace']))
        self.assertTrue(any(t['tool']=='calculate' and t['status']=='成功' for t in r['trace']))

    def test_live_events_and_persistence(self):
        events=[]
        result=Agent(self.store).run('method',[self.pid],on_event=events.append)
        self.assertEqual(events,result['trace'])
        self.assertEqual(events[0]['title'],'准备阅读任务')
        self.assertEqual(events[-1]['title'],'整理原文证据')
        sid=self.store.new_session()
        self.store.add_message(sid,'assistant',result['answer'],result['sources'],result['trace'])
        self.assertEqual(self.store.messages(sid)[0]['trace'],events)

    def test_model_failure_event(self):
        from paper_agent.agent import ModelError
        class Broken:
            def complete(self,*args): raise ModelError('连接异常')
        events=[]
        with self.assertRaises(ModelError):
            Agent(self.store,Broken()).run('method',[self.pid],on_event=events.append)
        self.assertEqual(events[-1]['status'],'失败')
        self.assertTrue(any(e['status']=='进行中' for e in events))
