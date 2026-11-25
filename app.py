import os
import streamlit as st
from paper_agent.store import Store, ROOT
from paper_agent.agent import Agent, Model, ModelError
from paper_agent.browser_settings import (
    MODEL_MODES, apply_model_settings, show_storage_feedback, switch_model_mode, sync_model_settings,
)
from paper_agent.pdf_preview import reflow_pdf_text
from paper_agent.source_ui import show_answer, show_pdf_page_image, show_source_page

st.set_page_config(page_title='Paper Atlas · 论文研究工作台',page_icon='📚',layout='wide',initial_sidebar_state='expanded')
st.markdown('<style>'+ (ROOT / 'assets' / 'theme.css').read_text(encoding='utf-8-sig') + '</style>',unsafe_allow_html=True)
chinese_toolbar = st.components.v2.component(
    'chinese_toolbar', js=(ROOT / 'assets' / 'toolbar.js').read_text(encoding='utf-8'),
)
chinese_toolbar(key='toolbar_zh')
store = Store()
if 'source' in st.query_params:
    show_source_page(store)
    st.stop()

preview_pdf_text = st.cache_data(max_entries=24, show_spinner=False)(reflow_pdf_text)

default_mode = '大模型 Agent 模式' if os.getenv('PAPER_AGENT_MODE') == 'agent' else '本地证据模式'
poke_api_base_url = 'https://www.poke2api.com/v1'
default_base_url = os.getenv('PAPER_AGENT_BASE_URL', poke_api_base_url).strip().rstrip('/')
default_model = os.getenv('PAPER_AGENT_MODEL', 'gpt-6.1-terra' if default_base_url == poke_api_base_url else '')
default_model_settings = {'mode': default_mode, 'base_url': default_base_url, 'model': default_model,
                          'api_key': os.getenv('PAPER_AGENT_API_KEY', '')}
for key, value in {**default_model_settings, 'retrieval_mode':'hybrid', 'retrieval_limit':5,
                   'retrieval_expand':True, 'retrieval_diversity':True}.items():
    st.session_state.setdefault(key,value)
sync_model_settings()
sessions = store.rows('SELECT * FROM sessions ORDER BY created DESC, id DESC')
if 'sid' not in st.session_state:
    st.session_state.sid = sessions[0]['id'] if sessions else store.new_session()
papers = store.papers()
labels = {p['id']:p['title'] for p in papers}


def open_workspace_page(section, target_page=None):
    st.session_state.workspace_section = section
    if target_page is not None:
        key = f'workspace_page_{section}'
        st.session_state[key] = target_page
        st.session_state.setdefault('workspace_pages', {})[section] = target_page


def open_paper_preview(paper_id, page_number=None):
    open_workspace_page('论文库', '论文库')
    st.session_state['library_focus_paper'] = paper_id
    if page_number is not None:
        st.session_state[f'read_page_{paper_id}'] = int(page_number)


if 'paper' in st.query_params and 'read_page' in st.query_params:
    try:
        jump_pid = st.query_params['paper']
        jump_page = int(st.query_params['read_page'])
        jump_paper = next((paper for paper in papers if paper['id'] == jump_pid), None)
        if jump_paper and 1 <= jump_page <= jump_paper['pages']:
            open_paper_preview(jump_pid, jump_page)
            st.query_params.clear()
    except (TypeError, ValueError):
        pass


sections = {
    '总览': ('总览',),
    '研究助手': ('阅读问答', '精读工作台', '论文对比'),
    '论文库': ('论文库', '证据检索'),
    '研究记录': ('阅读计划', '记忆中心'),
}
st.session_state.setdefault('workspace_section', '总览')
if st.session_state.workspace_section == '设置':
    st.session_state.workspace_section = None

with st.sidebar:
    st.markdown('<div class="brand"><span class="brand-mark">P<span>·</span></span><span class="brand-name">Paper Atlas</span></div>', unsafe_allow_html=True)
    st.divider()
    section = st.radio('工作台导航', tuple(sections), index=None, key='workspace_section', label_visibility='collapsed')
    st.button('设置', key='workspace_settings',
              type='tertiary' if section else 'secondary', width='stretch',
              on_click=lambda: st.session_state.update(workspace_section=None))
    st.divider()
    with st.container(gap='xsmall'):
        st.write(f'**{len(papers)}** 篇论文 · **{len(store.memories())}** 条近期记忆')
        st.caption('模式：' + st.session_state.mode)

page = '模型设置'
if section is not None:
    pages = sections[section]
    last_pages = st.session_state.setdefault('workspace_pages', {})
    control_key = f'workspace_page_{section}'
    if len(pages) == 1:
        page = pages[0]
        st.session_state[control_key] = page
        last_pages[section] = page
    else:
        options = {'key': control_key, 'label_visibility': 'collapsed', 'wrap': True}
        if control_key not in st.session_state:
            options['default'] = last_pages.get(section, pages[0])
        page = st.segmented_control(f'{section}页面', pages, required=True, **options)
        last_pages[section] = page


def render_answer(answer, sources, trace):
    show_answer(answer, sources, trace, labels)


def get_model():
    if st.session_state.mode == '本地证据模式':
        return None
    return Model(st.session_state.base_url,st.session_state.api_key,st.session_state.model)


def retrieval_options():
    return {'mode':st.session_state.retrieval_mode,'limit':st.session_state.retrieval_limit,'expand':st.session_state.retrieval_expand,'diversity':st.session_state.retrieval_diversity}


def ask(question, selected):
    if not selected:
        st.warning('请先选择论文。')
        return
    try:
        from paper_agent.process_ui import run_visible
        sid = st.session_state.sid
        history = store.messages(sid)
        result = run_visible(Agent(store, get_model(), retrieval_options()), question, selected, history)
        store.add_exchange(sid, question, result, selected)
        st.session_state.show_new_answer = True
        st.rerun()
    except (ValueError,ModelError) as exc:
        st.error(str(exc))
    except Exception:
        st.error('处理失败。请检查文件内容、模型配置或本地服务；已有论文和对话不会被覆盖。')


def open_with_paper(target, paper_id):
    """Button callback: carry the library paper into a downstream workflow."""
    if target == '阅读问答':
        sid = st.session_state.sid
        store.set_session_paper_ids(sid, [paper_id])
        st.session_state[f'reading_scope_{sid}'] = [paper_id]
        st.session_state.workspace_section = '研究助手'
        st.session_state['workspace_page_研究助手'] = '阅读问答'
        st.session_state.setdefault('workspace_pages', {})['研究助手'] = '阅读问答'
    else:
        st.session_state['evidence_search_scope'] = [paper_id]
        st.session_state.workspace_section = '论文库'
        st.session_state['workspace_page_论文库'] = '证据检索'
        st.session_state.setdefault('workspace_pages', {})['论文库'] = '证据检索'


def jump_to_preview_page(paper_id, max_page):
    """Chapter navigation callback: update the page number input before it is created."""
    page = st.session_state.get(f'outline_jump_{paper_id}')
    if isinstance(page, int) and 1 <= page <= max_page:
        st.session_state[f'read_page_{paper_id}'] = page


if page == '总览':
    st.markdown('<div class="hero"><h1>让每一次阅读，都连接下一次发现。</h1></div>',unsafe_allow_html=True)
    a,b,c,d = st.columns(4)
    a.metric('论文资料',len(papers))
    b.metric('原文片段',sum(p['chunks'] for p in papers))
    c.metric('阅读对话',len(store.rows('SELECT id FROM sessions')))
    d.metric('长期记忆',len(store.rows('SELECT id FROM memories')))
    st.markdown('### 开始你的研究')
    starter = [
        ('01 · 建立论文库', '导入本地 PDF / TXT / Markdown，自动分块并保留页码。', '去导入', ('论文库', '论文库')),
        ('02 · 带着问题阅读', '通过 RAG 查找原文证据，并结合阅读偏好和历史笔记回答问题。', '去提问', ('研究助手', '阅读问答')),
        ('03 · 对照不同研究', '比较方法、实验条件和局限，将有价值的结论保存为笔记。', '去对比', ('研究助手', '论文对比')),
    ]
    with st.container(key='starter_cards'):
        for col, (title, body, action, target) in zip(st.columns(3), starter):
            with col:
                with st.container(border=True):
                    st.subheader(title)
                    st.write(body)
                    st.button(action, key=f'home_{action}', type='primary', width='stretch',
                              on_click=open_workspace_page, args=target)

    st.markdown('### 最近导入')
    if papers:
        for p in papers[:5]:
            with st.container(border=True):
                left, right = st.columns([4, 1], vertical_alignment='center')
                left.markdown(f"**{p['title']}**")
                left.caption(f"{p['pages']} 页 · {p['chunks']} 片段")
                right.button('查看原文', key=f'home_open_{p["id"]}', width='stretch',
                             on_click=open_paper_preview, args=(p['id'], 1))
    else:
        st.info('论文库还是空的。前往「论文库」上传你本地的 PDF / TXT / Markdown。')
        st.button('去论文库导入', type='primary', width='stretch',
                  on_click=open_workspace_page, args=('论文库', '论文库'))

elif page == '证据检索':
    from paper_agent.workflows import search_page
    search_page(store,labels)

elif page == '精读工作台':
    from paper_agent.workflows import reading_page
    reading_page(store, labels, get_model, render_answer, retrieval_options())

elif page == '阅读计划':
    from paper_agent.workflows import planner_page
    planner_page(store,labels,open_with_paper)

elif page == '论文库':
    st.title('论文库')
    files = st.file_uploader(
        '导入论文', type=['pdf', 'txt', 'md'], accept_multiple_files=True,
        max_upload_size=30,
    )
    st.caption('支持 PDF / TXT / Markdown；单个文件最大 30 MB，PDF 最多 600 页。')
    if st.button('解析并加入论文库',disabled=not files,type='primary'):
        imported = []
        for file in files:
            try:
                with st.spinner('正在解析 ' + file.name):
                    _, created, warnings = store.ingest(file.name,file.getvalue())
                if created:
                    imported.append('已导入 ' + file.name)
                elif file.name.lower().endswith('.pdf'):
                    imported.append('已保留论文记录与 PDF 原件 · ' + file.name)
                else:
                    imported.append('已在论文库 · ' + file.name)
                imported.extend(warnings)
            except Exception as exc:
                imported.append(f'{file.name} 导入失败：{type(exc).__name__}。请确认文件可读取、未加密并含可提取文字。')
        st.session_state.import_report = imported
        st.rerun()
    for item in st.session_state.get('import_report',[]):
        st.info(item)
    if not papers:
        st.info('暂无论文，请先在「论文库」导入本地 PDF / TXT / Markdown。')
    else:
        focus_key = 'library_focus_paper'
        if st.session_state.get(focus_key) not in labels:
            st.session_state[focus_key] = list(labels)[0]
        selected = st.selectbox(
            '选择论文', list(labels),
            index=list(labels).index(st.session_state[focus_key]),
            format_func=labels.get,
        )
        if selected != st.session_state[focus_key]:
            st.session_state[focus_key] = selected
        p = next(p for p in papers if p['id'] == selected)
        a,b = st.columns([3,1])
        with a:
            st.subheader(p['title'])
            st.caption(f"{p['pages']} 页")
            actions = st.columns(2)
            with actions[0]:
                st.button(
                    '就这篇提问', key=f'ask_paper_{selected}', type='primary',
                    on_click=open_with_paper, args=('阅读问答', selected),
                )
            with actions[1]:
                st.button(
                    '只检索这篇', key=f'search_paper_{selected}',
                    on_click=open_with_paper, args=('证据检索', selected),
                )
            page_key = 'read_page_' + selected
            page_options = {
                'min_value': 1, 'max_value': p['pages'], 'step': 1,
                'key': page_key, 'width': 140,
            }
            if page_key not in st.session_state:
                page_options['value'] = 1
            number = st.number_input('原文页码', **page_options)
            rows = store.read_page(selected,number)
            raw_text = rows[0]['text'] if rows else ''
            is_pdf = p['filename'].lower().endswith('.pdf')

            headings = store.outline(selected)
            if headings:
                with st.expander('章节目录 · 自动识别', expanded=False):
                    page_labels = {}
                    for heading in headings:
                        page_labels.setdefault(heading['page'], heading['heading'])
                    chapter_pages = list(page_labels)
                    jump_key = f'outline_jump_{selected}'
                    jump_options = {
                        'format_func': lambda value: f'第 {value} 页 · {page_labels.get(value, "章节")}',
                        'key': jump_key,
                        'on_change': jump_to_preview_page,
                        'args': (selected, p['pages']),
                    }
                    if jump_key not in st.session_state:
                        jump_options['index'] = chapter_pages.index(number) if number in chapter_pages else 0
                    st.selectbox('跳到章节', chapter_pages, **jump_options)
                    st.caption('目录来自文本规则匹配，可能遗漏或误识别。')

            preview_mode = '提取文字'
            if is_pdf:
                preview_key = f'preview_mode_{selected}'
                preview_options = {'key': preview_key}
                if preview_key not in st.session_state:
                    preview_options['default'] = '提取文字'
                preview_mode = st.segmented_control(
                    '预览方式', ['提取文字', '原始页图'], **preview_options
                )
            st.markdown('#### 原文预览')
            if preview_mode == '原始页图':
                original = show_pdf_page_image(
                    store, selected, number, caption=f'原始 PDF · 第 {number} 页'
                )
                if original:
                    st.download_button(
                        '下载原始 PDF', original,
                        file_name=p['filename'], mime='application/pdf',
                    )
            else:
                preview = preview_pdf_text(raw_text, store.original_pdf(selected), number) if is_pdf and raw_text else raw_text
                with st.container(height=420, border=True, key=f'paper_preview_{selected}_{number}', autoscroll=False):
                    st.text(preview or '这一页没有可预览的文字。')
            text = '\n\n'.join(f"--- 第 {r['page']} 页 ---\n{r['text']}" for r in store.rows('SELECT page,text FROM pages WHERE paper_id=? ORDER BY page',(selected,)))
            st.download_button('下载提取的文字',text,file_name=f'{selected}.txt',mime='text/plain')
        with b:
            st.markdown('#### 阅读笔记')
            note = st.text_area('记录这篇论文的要点',height=180)
            st.caption('保存时会记录当前页码，方便回到原文核对。')
            if st.button('保存笔记',disabled=not note.strip()):
                store.save_memory(f'第 {number} 页 · {note.strip()}',paper_id=selected)
                st.success('笔记已保存到记忆中心，并标注了当前页码。')
            st.divider()
            sure = st.checkbox('确认删除此论文及关联笔记')
            if st.button('删除论文',disabled=not sure):
                store.delete_paper(selected)
                st.rerun()
            st.caption('删除论文后，历史回答和报告仍可查看当时的证据摘录。')

elif page == '阅读问答':
    st.title('阅读问答')
    cols = st.columns([4,1])
    with cols[0]:
        sessions = store.rows('SELECT * FROM sessions ORDER BY created DESC, id DESC')
        session_labels = {s['id']:s['title'] for s in sessions}
        sid = st.selectbox('阅读对话',list(session_labels),index=list(session_labels).index(st.session_state.sid),format_func=lambda x:session_labels[x])
        if sid != st.session_state.sid:
            st.session_state.sid = sid
            st.rerun()
    with cols[1]:
        st.write('')
        if st.button('＋ 新建对话'):
            st.session_state.sid = store.new_session()
            st.rerun()
    messages = store.messages(st.session_state.sid)
    saved_scope = store.session_paper_ids(st.session_state.sid)
    default_scope = saved_scope if saved_scope is not None else ([] if messages else list(labels)[:1])
    scope_key = f'reading_scope_{st.session_state.sid}'
    selected = []
    if papers:
        scope_options = {
            'format_func': labels.get, 'max_selections': 6, 'key': scope_key,
            'placeholder': '选择论文范围',
        }
        if scope_key not in st.session_state:
            scope_options['default'] = [pid for pid in default_scope if pid in labels]
        selected = st.multiselect('本次阅读范围（最多 6 篇）', list(labels), **scope_options)
    if (saved_scope is not None or selected) and saved_scope != selected:
        store.set_session_paper_ids(st.session_state.sid, selected)
    if not papers:
        st.info('在「论文库」导入本地论文后即可开始问答。')
        st.button('去论文库导入', type='primary', width='stretch',
                  on_click=open_workspace_page, args=('论文库', '论文库'))
    elif st.session_state.mode == '本地证据模式':
        st.info('当前仅展示原文检索结果。要生成论文解释，请在「设置」连接大模型。')
    if papers and messages and not selected:
        st.caption('请选择论文范围后继续追问。')
    if selected and messages and set(messages[-1]['paper_ids'] or []) != set(selected):
        st.caption('已切换论文范围。追问会参考这个范围内的历史消息。')
    if papers and not messages:
        st.markdown('#### 从一个具体的问题开始')
    for msg in messages:
        with st.chat_message(msg['role']):
            if msg['role'] == 'assistant':
                render_answer(msg['content'], msg['sources'], msg['trace'])
                if st.button('保存为阅读记忆',key='save_'+str(msg['id'])):
                    source_ids = list({r['paper_id'] for r in msg['sources'] if r['paper_id'] in labels})
                    store.save_memory(msg['content'],kind='回答摘录',paper_id=source_ids[0] if len(source_ids)==1 else None)
                    st.success('已保存。模型回答仍需原文核对。')
            else:
                if msg['paper_ids']:
                    names = '、'.join(labels.get(pid, '已移除论文') for pid in msg['paper_ids'])
                    st.caption('论文范围 · ' + names)
                st.markdown(msg['content'])
    if messages:
        export = '\n\n'.join(('## 我\n' if m['role']=='user' else '## Paper Atlas\n')+m['content'] for m in messages)
        st.download_button('导出本次对话',export,file_name='reading_session.md',mime='text/markdown')
    if st.session_state.pop('show_new_answer', False):
        st.html('''<script>
            requestAnimationFrame(() => {
                const main = document.querySelector('[data-testid="stMain"]');
                const messages = main?.querySelectorAll('[data-testid="stChatMessage"]');
                const latest = messages?.[messages.length - 1];
                if (!latest) return;
                const header = document.querySelector('[data-testid="stHeader"]');
                const top = Math.max(main.getBoundingClientRect().top, header?.getBoundingClientRect().bottom ?? 0);
                main.scrollBy(0, latest.getBoundingClientRect().top - top - 12);
            });
        </script>''', unsafe_allow_javascript=True)
    # Explicit placement keeps the composer fixed without following content height changes.
    with st.bottom:
        question = st.chat_input('向选中的论文提问…', disabled=not selected, max_chars=4000)
    if question:
        ask(question,selected)

elif page == '论文对比':
    from paper_agent.workflows import comparison_page
    comparison_page(store, labels, get_model, render_answer, retrieval_options())

elif page == '记忆中心':
    st.title('记忆中心')
    with st.form('memory_form',clear_on_submit=True):
        a,b = st.columns(2)
        kind = a.selectbox('记忆类型',['阅读偏好','研究方向','阅读笔记'])
        pid = b.selectbox('关联论文',[None]+list(labels),format_func=lambda p:labels.get(p,'全局记忆'))
        content = st.text_area('记忆内容',placeholder='例如：我希望先用通俗语言解释方法，再介绍公式；关注小样本学习。',max_chars=10000)
        if st.form_submit_button('保存记忆',type='primary'):
            if content.strip():
                store.save_memory(content,kind,pid)
                st.rerun()
            else:
                st.warning('请输入内容。')
    search = st.text_input('筛选记忆')
    memories = store.rows('SELECT * FROM memories ORDER BY created DESC')
    shown = [m for m in memories if search.lower() in m['content'].lower()]
    for m in shown:
        with st.container(border=True):
            a,b = st.columns([8,1])
            a.caption(m['kind']+' · '+labels.get(m['paper_id'],'全局'))
            a.markdown(m['content'])
            with a.expander('编辑这条记忆'):
                edited=st.text_area('记忆正文',value=m['content'],key='edit_'+m['id'],max_chars=10000)
                if st.button('保存修改',key='update_'+m['id'],disabled=not edited.strip()):
                    store.update_memory(m['id'],edited)
                    st.rerun()
            if b.button('删除',key='del_'+m['id']):
                store.delete_memory(m['id'])
                st.rerun()
    if not shown:
        st.info('暂无匹配记忆。保存阅读偏好后，Agent 会在后续提问时读取。')
    if memories:
        st.download_button('导出全部记忆','\n\n'.join('## '+m['kind']+'\n'+m['content'] for m in memories),file_name='research_memory.md')

elif page == '模型设置':
    st.title('模型设置')
    mode = st.session_state.mode
    st.session_state.model_mode_choice = mode
    st.segmented_control('运行模式', MODEL_MODES, required=True, key='model_mode_choice', on_change=switch_model_mode)
    if mode == MODEL_MODES[0]:
        st.info('**当前模式 · 本地证据模式**\n\n在本地论文库检索并返回带页码的原文片段。全程不调用模型接口。')
    else:
        st.info('**当前模式 · 大模型 Agent 模式**\n\n在本地检索原文，再由配置的模型按需查页、读取笔记，生成带引用的问答、精读材料和论文对比。')
    show_storage_feedback()
    if mode == MODEL_MODES[1]:
        st.subheader('模型连接')
        with st.form('settings'):
            base_value = st.text_input('API Base URL（结尾通常为 /v1）',value=st.session_state.base_url)
            model_value = st.text_input('模型名称',value=st.session_state.model,placeholder='填写你使用的服务所提供的模型 ID')
            key_value = st.text_input('API Key',type='password',value=st.session_state.api_key)
            st.caption('应用设置后，此浏览器会记住模型配置和 API Key。')
            catalog_connection = (base_value.strip().rstrip('/'), key_value.strip())
            if st.form_submit_button('应用设置',type='primary'):
                apply_model_settings({'mode': mode, 'base_url': catalog_connection[0],
                                      'model': model_value.strip(), 'api_key': catalog_connection[1]})
                st.rerun()
            if st.form_submit_button('查询可用模型'):
                st.session_state.pop('model_catalog', None)
                try:
                    with st.spinner('正在查询模型列表…'):
                        model_ids = Model.list_models(*catalog_connection)
                    st.session_state.model_catalog = (catalog_connection, model_ids)
                except ModelError as exc:
                    st.error(str(exc))
        catalog = st.session_state.get('model_catalog')
        if catalog and catalog[0] == catalog_connection:
            model_ids = catalog[1]
            if model_ids:
                st.caption(f'接口返回 {len(model_ids)} 个模型。复制 ID 到「模型名称」后可测试工具调用。')
                st.code('\n'.join(model_ids), language=None)
            else:
                st.info('接口返回的模型列表为空。')
        with st.expander('连接与数据说明'):
            st.write('本地 Ollama 地址：http://localhost:11434/v1。填写已安装且支持工具调用的模型。使用云模型时，相关的检索片段、近期对话与记忆会发送到所配置的接口。')
        if st.button('测试模型连接与工具调用'):
            try:
                from paper_agent.agent import TOOLS
                with st.spinner('测试接口…'):
                    response = get_model().complete([{'role':'user','content':'请调用 recall_memory 工具，query 为 test。'}],TOOLS)
                if response.get('tool_calls'):
                    st.success('模型已返回工具调用，连接成功。')
                else:
                    st.warning('接口可连接，但本次未返回工具调用。请确认模型支持 Function Calling。')
            except ModelError as exc:
                st.error(str(exc))
    st.button('清除此浏览器的模型配置', type='tertiary', on_click=apply_model_settings,
              args=(default_model_settings,), kwargs={'clear': True})
    st.subheader('检索偏好')
    with st.form('retrieval_settings'):
        modes=['hybrid','tfidf','bm25']
        rm=st.selectbox('默认检索方式',modes,index=modes.index(st.session_state.retrieval_mode),format_func=lambda x:{'hybrid':'混合检索（推荐）','tfidf':'TF-IDF 字符向量','bm25':'BM25 关键词'}[x])
        rk=st.slider('每篇论文召回片段数',2,8,st.session_state.retrieval_limit)
        rexp=st.checkbox('中英文关键词扩展',value=st.session_state.retrieval_expand)
        rdiv=st.checkbox('MMR 片段多样性排序',value=st.session_state.retrieval_diversity)
        if st.form_submit_button('保存检索偏好'):
            st.session_state.update(retrieval_mode=rm,retrieval_limit=rk,retrieval_expand=rexp,retrieval_diversity=rdiv)
            st.success('已应用到当前会话。')
    with st.expander('技术说明'):

        st.markdown('''- **RAG**：按页解析 → 优先沿段落或句尾切分，每片最多 1000 字符、重叠最多 200 字符 → BM25 + TF-IDF 双路召回 → RRF 融合 → MMR 多样性排序 → 携带引用生成。
- **Memory**：SQLite 保存偏好、研究笔记、会话历史；每次请求召回相关记忆。
- **Tools**：`search_papers`、`read_page`、`recall_memory`、`paper_outline`、`calculate`；模型可自主选择，最多四轮工具调用。
- **边界**：当前未接入稠密 embedding、OCR、图表视觉分析或网络文献搜索。跨语言检索建议使用原文关键词。''')
