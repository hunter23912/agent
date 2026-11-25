import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from paper_agent.store import Store

class UITests(unittest.TestCase):
    @staticmethod
    def sample_pdf():
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=400, height=400)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})
        })
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 20 300 Td (1. Introduction method result) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def open_page(self, app, section, page=None):
        if section == '设置':
            app.sidebar.button(key='workspace_settings').click().run()
            return
        navigation = app.sidebar.radio(key='workspace_section')
        if navigation.value != section:
            navigation.set_value(section).run()
        if page is not None:
            control = next((item for item in app.segmented_control if page in item.options), None)
            if control is not None and control.value != page:
                control.set_value(page).run()

    def test_model_catalog_uses_form_credentials_without_applying(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {'PAPER_AGENT_DATA': tmp}),
            patch('paper_agent.agent.requests.get') as get,
        ):
            get.return_value.status_code = 200
            get.return_value.json.return_value = {'data': [{'id': 'model-b'}, {'id': 'model-a'}, {'id': 'model-b'}]}
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            self.open_page(app, '设置')
            app.segmented_control(key='model_mode_choice').set_value('大模型 Agent 模式').run()
            previous_model = app.session_state['model']
            next(t for t in app.text_input if 'API Base URL' in t.label).set_value('https://example.org/v1/')
            next(t for t in app.text_input if t.label == 'API Key').set_value('test-key')
            next(t for t in app.text_input if t.label == '模型名称').set_value('')
            next(b for b in app.button if b.label == '查询可用模型').click().run()

            get.assert_called_once_with(
                'https://example.org/v1/models', headers={'Authorization': 'Bearer test-key'},
                timeout=(10, 20), allow_redirects=False,
            )
            self.assertFalse(app.exception)
            self.assertEqual(app.code[0].value, 'model-a\nmodel-b')
            self.assertEqual(app.session_state['model'], previous_model)

            get.return_value.status_code = 403
            next(b for b in app.button if b.label == '查询可用模型').click().run()
            self.assertFalse(app.code)
            self.assertIn('HTTP 403', app.error[0].value)

    def test_reading_scope_survives_page_and_browser_session_changes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PAPER_AGENT_DATA': tmp, 'PAPER_AGENT_MODE': 'local'}):
            store = Store(tmp)
            store.ingest('first.txt', b'First paper method')
            second = store.ingest('second.txt', b'Second paper method')[0]
            path = str(Path(__file__).resolve().parents[1] / 'app.py')
            app = AppTest.from_file(path).run()
            self.open_page(app, '研究助手', '阅读问答')
            app.multiselect[0].set_value([second]).run()
            self.open_page(app, '论文库')
            self.open_page(app, '研究助手', '阅读问答')
            self.assertEqual(app.multiselect[0].value, [second])
            app = AppTest.from_file(path).run()
            self.open_page(app, '研究助手', '阅读问答')
            self.assertEqual(app.multiselect[0].value, [second])
            app.chat_input[0].set_value('method').run()
            messages = store.messages(app.session_state['sid'])
            self.assertEqual([message['paper_ids'] for message in messages], [[second], [second]])
            self.assertTrue(any(f'?paper={second}&read_page=1' in item.value for item in app.markdown))

    def test_research_results_are_scoped_to_inputs_and_archived(self):
        env = {'PAPER_AGENT_MODE': 'local'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env | {'PAPER_AGENT_DATA': tmp}):
            store = Store(tmp)
            papers = [store.ingest(f'{name}.txt', f'Method and dataset for paper {name}'.encode())[0] for name in ('A', 'B', 'C')]
            path = str(Path(__file__).resolve().parents[1] / 'app.py')
            app = AppTest.from_file(path).run()

            def downloads():
                return [item.proto.label for item in app.get('download_button')]

            self.open_page(app, '研究助手', '论文对比')
            app.multiselect[0].set_value(papers[:2]).run()
            app.text_area[0].set_value('检查数据划分').run()
            next(b for b in app.button if b.label == '生成对比报告').click().run()
            self.assertIn('下载对比报告', downloads())
            app.multiselect[0].set_value(papers[1:]).run()
            self.assertNotIn('下载对比报告', downloads())
            comparison = store.research_results('comparison')[0]
            self.assertEqual(comparison['settings']['paper_ids'], papers[:2])
            self.assertEqual(comparison['settings']['focus'], '检查数据划分')

            self.open_page(app, '研究助手', '精读工作台')
            app.text_area[0].set_value('检查模型输入').run()
            next(b for b in app.button if b.label == '开始精读').click().run()
            self.assertIn('导出精读材料', downloads())
            app.selectbox[1].set_value('复现清单').run()
            self.assertNotIn('导出精读材料', downloads())
            reading = store.research_results('reading')[0]

            app = AppTest.from_file(path).run()
            for page, key, record, label in (
                ('论文对比', 'comparison_history_record', comparison, '下载对比报告'),
                ('精读工作台', 'reading_history_record', reading, '导出精读材料'),
            ):
                self.open_page(app, '研究助手', page)
                self.assertNotIn(label, downloads())
                app.selectbox(key=key).set_value(record['id']).run()
                self.assertIn(label, downloads())
                self.assertTrue(any(record['settings']['focus'] in item.value for item in app.text))

    def test_library_actions_carry_paper_into_downstream_pages(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PAPER_AGENT_DATA': tmp}):
            store = Store(tmp)
            store.ingest('first.txt', b'First paper method')
            store.ingest('second.txt', b'Second paper method')
            path = str(Path(__file__).resolve().parents[1] / 'app.py')
            app = AppTest.from_file(path, default_timeout=30).run()
            self.open_page(app, '论文库')
            target = app.selectbox[0].value

            uploader = app.get('file_uploader')[0]
            self.assertEqual(uploader.proto.max_upload_size_mb, 30)

            next(b for b in app.button if b.label == '就这篇提问').click().run()
            sid = app.session_state['sid']
            scope_key = f'reading_scope_{sid}'
            self.assertEqual(app.session_state['workspace_section'], '研究助手')
            self.assertEqual(app.session_state['workspace_page_研究助手'], '阅读问答')
            self.assertEqual(app.session_state[scope_key], [target])
            self.assertEqual(store.session_paper_ids(sid), [target])
            self.assertEqual(app.multiselect(key=scope_key).value, [target])
            self.assertFalse(app.chat_input[0].disabled)
            self.assertFalse(app.exception)

            self.open_page(app, '论文库', '论文库')
            next(b for b in app.button if b.label == '只检索这篇').click().run()
            self.assertEqual(app.session_state['workspace_section'], '论文库')
            self.assertEqual(app.session_state['workspace_page_论文库'], '证据检索')
            self.assertEqual(app.multiselect(key='evidence_search_scope').value, [target])
            self.assertFalse(app.exception)

    def test_library_preview_and_page_notes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PAPER_AGENT_DATA': tmp}):
            store = Store(tmp)
            content = '1. Introduction\n' + 'a' * 4500 + '\n2. Method\nMore text.'
            paper_id = store.ingest('notes.txt', content.encode())[0]
            pdf_id = store.ingest('paper.pdf', self.sample_pdf())[0]
            path = str(Path(__file__).resolve().parents[1] / 'app.py')
            app = AppTest.from_file(path, default_timeout=30).run()
            self.open_page(app, '论文库')

            jump = app.selectbox(key=f'outline_jump_{paper_id}')
            self.assertEqual(jump.value, 1)
            self.assertEqual(jump.options, ['第 1 页 · 1. Introduction', '第 2 页 · 2. Method'])
            jump.set_value(2).run()
            self.assertEqual(app.session_state[f'read_page_{paper_id}'], 2)
            self.assertFalse(app.exception)

            app.selectbox[0].set_value(paper_id).run()
            app.text_area[0].set_value('复核方法').run()
            next(b for b in app.button if b.label == '保存笔记').click().run()
            self.assertEqual(store.memories()[0]['content'], '第 2 页 · 复核方法')

            app.selectbox[0].set_value(pdf_id).run()
            preview = app.segmented_control(key=f'preview_mode_{pdf_id}')
            self.assertEqual(preview.value, '提取文字')
            preview.set_value('原始页图').run()
            self.assertFalse(app.exception)
            self.assertTrue(app.get('image'))
            self.assertTrue(any(d.label == '下载原始 PDF' for d in app.get('download_button')))

    def test_planner_board_jump_and_task_deletion(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PAPER_AGENT_DATA': tmp}):
            store = Store(tmp)
            first = store.ingest('first.txt', b'First paper method')[0]
            second = store.ingest('second.txt', b'Second paper method')[0]
            path = str(Path(__file__).resolve().parents[1] / 'app.py')
            app = AppTest.from_file(path, default_timeout=30).run()
            self.open_page(app, '研究记录', '阅读计划')

            self.assertEqual([metric.value for metric in app.metric], ['2', '0', '0'])
            next(b for b in app.button if b.key == f'plan_status_{second}_精读中').click().run()
            self.assertEqual(store.reading_state(second)['status'], '精读中')
            self.assertFalse(app.exception)

            next(b for b in app.button if b.key == f'plan_ask_{first}').click().run()
            self.assertEqual(app.session_state['workspace_section'], '研究助手')
            self.assertEqual(app.session_state['workspace_page_研究助手'], '阅读问答')
            sid = app.session_state['sid']
            self.assertEqual(app.multiselect(key=f'reading_scope_{sid}').value, [first])
            self.assertFalse(app.exception)

            self.open_page(app, '研究记录', '阅读计划')
            next(t for t in app.text_input if t.label == '下一步行动').set_value('核对实验')
            next(b for b in app.button if b.label == '添加待办').click().run()
            tasks = store.rows('SELECT * FROM tasks')
            self.assertEqual(len(tasks), 1)
            next(b for b in app.button if b.key == f'delete_task_{tasks[0]["id"]}').click().run()
            self.assertEqual(store.rows('SELECT * FROM tasks'), [])
            self.assertFalse(app.exception)

    def test_evidence_link_query_opens_library_preview(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PAPER_AGENT_DATA': tmp}):
            store = Store(tmp)
            paper_id = store.ingest('paper.txt', b'x' * 4600 + b'2. Method more')[0]
            path = str(Path(__file__).resolve().parents[1] / 'app.py')
            app = AppTest.from_file(path, default_timeout=30).run()
            app.query_params['paper'] = paper_id
            app.query_params['read_page'] = '2'
            app.run()
            self.assertEqual(app.session_state['workspace_section'], '论文库')
            self.assertEqual(app.session_state['workspace_page_论文库'], '论文库')
            self.assertEqual(app.selectbox[0].value, paper_id)
            self.assertEqual(app.session_state[f'read_page_{paper_id}'], 2)
            self.assertFalse(app.query_params)
            self.assertFalse(app.exception)

    def test_pages_and_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get('PAPER_AGENT_DATA')
            os.environ['PAPER_AGENT_DATA']=tmp
            store = Store(tmp)
            store.ingest('first.txt', b'First real paper method')
            store.ingest('second.txt', b'Second real paper method')
            try:
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
                self.assertEqual(len(app.exception),0)
                self.assertEqual(app.sidebar.radio[0].options, ['总览', '研究助手', '论文库', '研究记录'])
                self.assertEqual(app.sidebar.radio[0].value, '总览')
                self.assertFalse(any(item.label == '存储位置' for item in app.sidebar.expander))
                self.assertFalse(any(item.label == '加载两篇演示资料' for item in app.button))
                self.open_page(app, '研究助手', '阅读问答')
                self.assertFalse(app.chat_input[0].disabled)
                for section, page in (
                    ('总览', None), ('论文库', '论文库'), ('论文库', '证据检索'),
                    ('研究助手', '阅读问答'), ('研究助手', '精读工作台'),
                    ('研究助手', '论文对比'), ('研究记录', '阅读计划'),
                    ('研究记录', '记忆中心'), ('设置', None),
                ):
                    self.open_page(app, section, page)
                    self.assertEqual(len(app.exception),0,page)
                self.open_page(app, '论文库')
                self.assertEqual(app.segmented_control(key='workspace_page_论文库').value, '证据检索')
                next(t for t in app.text_input if t.label=='检索问题或关键词').set_value('method').run()
                next(b for b in app.button if b.label=='检索原文证据').click().run()
                self.assertEqual(len(app.exception),0)
                self.assertTrue(any('检索结果' in h.value for h in app.subheader))
                self.open_page(app, '研究助手', '精读工作台')
                next(b for b in app.button if b.label=='开始精读').click().run()
                self.assertEqual(len(app.exception),0)
                self.assertTrue(any('本地证据模式' in m.value for m in app.markdown))
                self.open_page(app, '研究记录', '阅读计划')
                next(t for t in app.text_input if t.label=='下一步行动').set_value('验证指标')
                next(b for b in app.button if b.label=='添加待办').click().run()
                self.assertEqual(len(app.exception),0)
                next(c for c in app.checkbox if c.label=='验证指标').check().run()
                self.assertEqual(len(app.exception),0)
                self.open_page(app, '设置')
                mode = lambda: app.segmented_control(key='model_mode_choice')
                self.assertEqual(mode().value, '本地证据模式')
                self.assertFalse(app.text_input)
                mode().set_value('大模型 Agent 模式').run()
                self.assertEqual(app.session_state['mode'], '大模型 Agent 模式')
                next(t for t in app.text_input if t.label=='模型名称').set_value('test-persistent-model')
                next(b for b in app.button if b.label=='应用设置').click().run()
                mode().set_value('本地证据模式').run()
                self.assertEqual(app.session_state['mode'], '本地证据模式')
                self.assertFalse(app.text_input)
                self.assertFalse(any(b.label == '测试模型连接与工具调用' for b in app.button))
                self.open_page(app, '研究助手', '总览')
                self.open_page(app, '设置')
                mode().set_value('大模型 Agent 模式').run()
                self.assertEqual(next(t for t in app.text_input if t.label=='模型名称').value,'test-persistent-model')
                mode().set_value('本地证据模式').run()
                self.open_page(app, '研究助手', '阅读问答')
                app.chat_input[0].set_value('method 方法').run()
                self.assertEqual(len(app.exception),0)
                self.assertEqual(len(app.chat_message),2)
                self.open_page(app, '研究助手', '论文对比')
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
