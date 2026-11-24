from pathlib import Path
import os
import streamlit as st
from paper_agent.store import Store, ROOT
from paper_agent.agent import Agent, Model, ModelError

st.set_page_config(page_title='Paper Atlas · 论文研究工作台',page_icon='📚',layout='wide',initial_sidebar_state='expanded')
st.markdown('<style>'+ (ROOT / 'assets' / 'theme.css').read_text(encoding='utf-8-sig') + '</style>',unsafe_allow_html=True)
store = Store()
for key, value in {'mode':'本地证据模式','base_url':os.getenv('PAPER_AGENT_BASE_URL','http://localhost:11434/v1'),'model':os.getenv('PAPER_AGENT_MODEL',''),'api_key':os.getenv('PAPER_AGENT_API_KEY',''),'retrieval_mode':'hybrid','retrieval_limit':5,'retrieval_expand':True,'retrieval_diversity':True}.items():
    st.session_state.setdefault(key,value)
sessions = store.rows('SELECT * FROM sessions ORDER BY created DESC, id DESC')
if 'sid' not in st.session_state:
    st.session_state.sid = sessions[0]['id'] if sessions else store.new_session()
papers = store.papers()
labels = {p['id']:p['title'] for p in papers}

with st.sidebar:
    st.markdown('<div class="brand"><span class="brand-mark">P<span>·</span></span><span class="brand-name">Paper Atlas</span></div>', unsafe_allow_html=True)
    st.divider()
    page = st.radio('研究工作台',['总览','论文库','证据检索','阅读问答','精读工作台','论文对比','阅读计划','记忆中心','模型设置'],label_visibility='collapsed')
    st.divider()
    st.write(f'**{len(papers)}** 篇论文 · **{len(store.memories())}** 条近期记忆')
    st.caption('模式：' + st.session_state.mode)
    with st.expander('存储位置'):
        st.caption(str(store.directory))


def show_sources(sources, trace):
    with st.expander(f'原文证据 · {len(sources)} 条'):
        if not sources:
            st.caption('本次未检索到证据。')
        for r in sources:
            st.markdown(f"**{r['title']} · 第 {r['page']} 页**")
            st.code(r['citation'],language=None)
            st.text(r['text'])
            if 'score' in r:
                st.caption(f"排序分数 {r['score']:.4f} · {r.get('retrieval','tfidf').upper()}（不是答案置信度）")
            st.divider()
    from paper_agent.process_ui import render_process
    render_process(trace)


def get_model():
    if st.session_state.mode == '本地证据模式':
        return None
    return Model(st.session_state.base_url,st.session_state.api_key,st.session_state.model)


def retrieval_options():
    return {'mode':st.session_state.retrieval_mode,'limit':st.session_state.retrieval_limit,'expand':st.session_state.retrieval_expand,'diversity':st.session_state.retrieval_diversity}


def ask(question, selected, compare=False):
    if not selected:
        st.warning('请先选择论文。')
        return
    try:
        from paper_agent.process_ui import run_visible
        history = [] if compare else store.messages(st.session_state.sid)
        result = run_visible(Agent(store,get_model(),retrieval_options()),question,selected,history,compare)
        if compare:
            st.session_state.comparison = result
        else:
            store.add_message(st.session_state.sid,'user',question)
            store.add_message(st.session_state.sid,'assistant',result['answer'],result['sources'],result['trace'])
        st.rerun()
    except (ValueError,ModelError) as exc:
        st.error(str(exc))
    except Exception:
        st.error('处理失败。请检查文件内容、模型配置或本地服务；已有论文和对话不会被覆盖。')


if page == '总览':
    st.markdown('<div class="hero"><h1>让每一次阅读，都连接下一次发现。</h1></div>',unsafe_allow_html=True)
    a,b,c,d = st.columns(4)
    a.metric('论文资料',len(papers))
    b.metric('原文片段',sum(p['chunks'] for p in papers))
    c.metric('阅读对话',len(store.rows('SELECT id FROM sessions')))
    d.metric('长期记忆',len(store.rows('SELECT id FROM memories')))
    st.markdown('### 开始你的研究')
    for col,title,body in zip(st.columns(3),['01 · 建立论文库','02 · 带着问题阅读','03 · 对照不同研究'],['导入 PDF / TXT / Markdown，自动分块并保留页码。','通过 RAG 查找原文，结合阅读偏好回答问题。','比较方法、实验条件和局限，将有价值的结论保存为笔记。']):
        with col:
            with st.container(border=True):
                st.subheader(title)
                st.write(body)
    st.markdown('### 最近导入')
    if papers:
        for p in papers[:5]:
            with st.container(border=True):
                st.markdown(f"**{p['title']}**")
                st.caption(f"{p['pages']} 页")
    else:
        st.info('论文库还是空的。前往「论文库」上传论文，或先加载演示资料。')
    if st.button('加载两篇演示资料',type='primary'):
        for file in (ROOT / 'examples').glob('*.md'):
            store.ingest(file.name,file.read_bytes())
        st.rerun()
    st.caption('演示资料为虚构文本。')

elif page == '证据检索':
    from paper_agent.workflows import search_page
    search_page(store,labels)

elif page == '精读工作台':
    from paper_agent.workflows import reading_page
    reading_page(store,labels,get_model,show_sources,retrieval_options())

elif page == '阅读计划':
    from paper_agent.workflows import planner_page
    planner_page(store,labels)

elif page == '论文库':
    st.title('论文库')
    files = st.file_uploader('导入论文（每个文件最大 30 MB）',type=['pdf','txt','md'],accept_multiple_files=True)
    if st.button('解析并加入论文库',disabled=not files,type='primary'):
        imported = []
        for file in files:
            try:
                with st.spinner('正在解析 ' + file.name):
                    _, created, warnings = store.ingest(file.name,file.getvalue())
                imported.append(('已导入：' if created else '已存在，跳过：') + file.name)
                imported.extend(warnings)
            except Exception as exc:
                imported.append(f'{file.name} 导入失败：{type(exc).__name__}。请确认文件可读取、未加密并含可提取文字。')
        st.session_state.import_report = imported
        st.rerun()
    for item in st.session_state.get('import_report',[]):
        st.info(item)
    if not papers:
        st.info('暂无论文，可从总览加载演示资料。')
    else:
        selected = st.selectbox('选择论文',list(labels),format_func=labels.get)
        p = next(p for p in papers if p['id'] == selected)
        a,b = st.columns([3,1])
        with a:
            st.subheader(p['title'])
            st.caption(f"{p['pages']} 页")
            number = st.number_input('原文页码',min_value=1,max_value=p['pages'],value=1,step=1,key='read_page_'+selected)
            rows = store.read_page(selected,number)
            st.text_area('原文预览',rows[0]['text'] if rows else '',height=420,disabled=True)
            text = '\n\n'.join(f"--- 第 {r['page']} 页 ---\n{r['text']}" for r in store.rows('SELECT page,text FROM pages WHERE paper_id=? ORDER BY page',(selected,)))
            st.download_button('下载提取的文字',text,file_name=f'{selected}.txt',mime='text/plain')
        with b:
            st.markdown('#### 阅读笔记')
            note = st.text_area('记录这篇论文的要点',height=180)
            if st.button('保存笔记',disabled=not note.strip()):
                store.save_memory(note,paper_id=selected)
                st.success('笔记已保存到记忆中心。')
            st.divider()
            sure = st.checkbox('确认删除此论文及关联笔记')
            if st.button('删除论文',disabled=not sure):
                store.delete_paper(selected)
                st.session_state.pop('comparison',None)
                st.rerun()
            st.caption('历史回答中的证据快照会保留。当前版本存储提取文字，不保留上传的原始 PDF。')

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
    selected = st.multiselect('本次阅读范围（最多 6 篇）',list(labels),default=list(labels)[:1],format_func=labels.get,max_selections=6)
    if st.session_state.mode == '本地证据模式':
        st.info('当前仅展示原文检索结果。要生成论文解释，请在「模型设置」连接大模型。')
    messages = store.messages(st.session_state.sid)
    if not messages:
        st.markdown('#### 从一个具体的问题开始')
    for msg in messages:
        with st.chat_message(msg['role']):
            st.markdown(msg['content'])
            if msg['role'] == 'assistant':
                show_sources(msg['sources'],msg['trace'])
                if st.button('保存为阅读记忆',key='save_'+str(msg['id'])):
                    source_ids = list({r['paper_id'] for r in msg['sources'] if r['paper_id'] in labels})
                    store.save_memory(msg['content'],kind='回答摘录',paper_id=source_ids[0] if len(source_ids)==1 else None)
                    st.success('已保存。模型回答仍需原文核对。')
    if messages:
        export = '\n\n'.join(('## 我\n' if m['role']=='user' else '## Paper Atlas\n')+m['content'] for m in messages)
        st.download_button('导出本次对话',export,file_name='reading_session.md',mime='text/markdown')
    question = st.chat_input('向选中的论文提问…',disabled=not selected,max_chars=4000)
    if question:
        ask(question,selected)

elif page == '论文对比':
    st.title('论文对比')
    selected = st.multiselect('选择 2–6 篇论文',list(labels),default=list(labels)[:2],format_func=labels.get,max_selections=6)
    dimensions = st.multiselect('对比维度',['研究问题','核心方法','数据集','实验指标与条件','创新点','局限性','复现难度'],default=['研究问题','核心方法','实验指标与条件','局限性'])
    focus = st.text_area('研究关注点（可选）',placeholder='例如：关注缺失数据处理，说明是否适合小显存设备复现。',max_chars=2000)
    if st.button('生成对比报告',type='primary',disabled=len(selected)<2 or not dimensions):
        ask('请对比所选论文，逐篇覆盖以下维度：'+ '、'.join(dimensions) + '。同时检索英文关键词：abstract method dataset experiment results limitation。关注点：' + focus, selected,True)
    if 'comparison' in st.session_state:
        r = st.session_state.comparison
        st.divider()
        st.markdown(r['answer'])
        show_sources(r['sources'],r['trace'])
        a,b = st.columns(2)
        a.download_button('下载对比报告',r['answer']+'\n\n## 原文证据\n'+'\n\n'.join(x['citation']+' '+x['title']+f" 第{x['page']}页\n"+x['text'] for x in r['sources']),file_name='paper_comparison.md',mime='text/markdown')
        if b.button('将报告存入长期记忆'):
            store.save_memory(r['answer'],kind='对比报告')
            st.success('对比报告已保存。')

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
    with st.form('settings'):
        mode_value = st.radio('运行模式',['本地证据模式','大模型 Agent 模式'],index=0 if st.session_state.mode=='本地证据模式' else 1)
        base_value = st.text_input('API Base URL（结尾通常为 /v1）',value=st.session_state.base_url)
        model_value = st.text_input('模型名称',value=st.session_state.model,placeholder='填写你使用的服务所提供的模型 ID')
        key_value = st.text_input('API Key',type='password',value=st.session_state.api_key)
        st.caption('Key 仅用于当前会话，不保存到磁盘。')
        if st.form_submit_button('应用设置',type='primary'):
            st.session_state.update(mode=mode_value,base_url=base_value.strip(),model=model_value.strip(),api_key=key_value.strip())
            st.success('设置已应用。')
    with st.expander('连接与数据说明'):
        st.write('本地 Ollama 地址：http://localhost:11434/v1。填写已安装且支持工具调用的模型。使用云模型时，检索片段、近期对话与相关记忆会发送到所配置的接口。')
    if st.button('测试模型连接与工具调用'):
        try:
            from paper_agent.agent import TOOLS
            with st.spinner('测试接口…'):
                model = Model(st.session_state.base_url,st.session_state.api_key,st.session_state.model)
                response = model.complete([{'role':'user','content':'请调用 recall_memory 工具，query 为 test。'}],TOOLS)
            if response.get('tool_calls'):
                st.success('模型已返回工具调用，连接成功。')
            else:
                st.warning('接口可连接，但本次未返回工具调用。请确认模型支持 Function Calling。')
        except ModelError as exc:
            st.error(str(exc))
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

        st.markdown('''- **RAG**：按页解析 → 1000 字符分块 / 200 字符重叠 → BM25 + TF-IDF 双路召回 → RRF 融合 → MMR 多样性排序 → 携带引用生成。
- **Memory**：SQLite 保存偏好、研究笔记、会话历史；每次请求召回相关记忆。
- **Tools**：`search_papers`、`read_page`、`recall_memory`、`paper_outline`、`calculate`；模型可自主选择，最多四轮工具调用。
- **边界**：当前未接入稠密 embedding、OCR、图表视觉分析或网络文献搜索。跨语言检索建议使用原文关键词。''')
