import os
import tempfile
import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest

class UITests(unittest.TestCase):
    def test_pages_and_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get('PAPER_AGENT_DATA')
            os.environ['PAPER_AGENT_DATA']=tmp
            try:
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
                self.assertEqual(len(app.exception),0)
                next(b for b in app.button if b.label=='加载两篇演示资料').click().run()
                self.assertEqual(len(app.exception),0)
                for page in ['论文库','证据检索','阅读问答','精读工作台','论文对比','阅读计划','记忆中心','模型设置','总览']:
                    app.sidebar.radio[0].set_value(page).run()
                    self.assertEqual(len(app.exception),0,page)
                app.sidebar.radio[0].set_value('证据检索').run()
                next(t for t in app.text_input if t.label=='检索问题或关键词').set_value('method').run()
                next(b for b in app.button if b.label=='检索原文证据').click().run()
                self.assertEqual(len(app.exception),0)
                self.assertTrue(any('检索结果' in h.value for h in app.subheader))
                app.sidebar.radio[0].set_value('精读工作台').run()
                next(b for b in app.button if b.label=='开始精读').click().run()
                self.assertEqual(len(app.exception),0)
                self.assertTrue(any('本地证据模式' in m.value for m in app.markdown))
                app.sidebar.radio[0].set_value('阅读计划').run()
                next(t for t in app.text_input if t.label=='下一步行动').set_value('验证指标')
                next(b for b in app.button if b.label=='添加待办').click().run()
                self.assertEqual(len(app.exception),0)
                next(c for c in app.checkbox if c.label=='验证指标').check().run()
                self.assertEqual(len(app.exception),0)
                app.sidebar.radio[0].set_value('模型设置').run()
                next(t for t in app.text_input if t.label=='模型名称').set_value('test-persistent-model')
                next(b for b in app.button if b.label=='应用设置').click().run()
                app.sidebar.radio[0].set_value('总览').run()
                app.sidebar.radio[0].set_value('模型设置').run()
                self.assertEqual(next(t for t in app.text_input if t.label=='模型名称').value,'test-persistent-model')
                app.sidebar.radio[0].set_value('阅读问答').run()
                app.chat_input[0].set_value('method 方法').run()
                self.assertEqual(len(app.exception),0)
                self.assertEqual(len(app.chat_message),2)
                app.sidebar.radio[0].set_value('论文对比').run()
                next(b for b in app.button if b.label=='生成对比报告').click().run()
                self.assertEqual(len(app.exception),0)
                self.assertTrue(any('本地证据模式' in m.value for m in app.markdown))
            finally:
                if previous is None:
                    os.environ.pop('PAPER_AGENT_DATA',None)
                else:
                    os.environ['PAPER_AGENT_DATA']=previous

if __name__=='__main__':
    unittest.main()
