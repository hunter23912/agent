"""Additional research workflows, backed by the same local library."""
import json
import streamlit as st
from .agent import Agent, ModelError
from .calculator import calculate
from .process_ui import run_visible
from .source_ui import page_url

PRESETS={
    '快速精读':'请输出：一句话研究问题、三个关键贡献、方法步骤、实验设置、主要结论、局限性。每部分有原文引用，缺失则说明。检索 abstract introduction contribution method experiment limitation。',
    '方法拆解':'请用适合初学者的语言解释输入、输出、各模块、训练目标和推理过程。给出符号解释，不编造论文中没有的公式。检索 method architecture objective training inference。',
    '复现清单':'请整理数据准备、预处理、模型结构、损失、超参数、训练与评估步骤，以及论文未披露的信息。严格区分论文信息与建议。检索 implementation dataset hyperparameter training evaluation reproducibility。',
    '自测问答':'请基于论文制作五组自测题和参考答案，每个答案有来源引用。由研究问题、方法到实验局限逐步加深。检索 abstract method experiment limitation。'
}

def research_settings(paper_ids, focus, retrieval, **task):
    mode = st.session_state.mode
    return {
        'paper_ids': paper_ids, 'focus': focus.strip(), 'mode': mode,
        'model': st.session_state.model if mode == '大模型 Agent 模式' else '',
        'retrieval': retrieval, **task,
    }


def show_research_result(store, record, kind, show_answer):
    result = store.research_result(record['id'])
    settings = record['settings']
    task = settings['preset'] if kind == 'reading' else '论文对比 · ' + '、'.join(settings['dimensions'])
    st.subheader(task)
    details = [f"论文 · {'、'.join(record['papers'])}", f"关注点 · {settings['focus'] or '未填写'}",
               f"运行模式 · {settings['model'] or settings['mode']}", f"生成时间 · {record['created']} UTC"]
    for line in details:
        st.text(line)
    show_answer(result['answer'], result['sources'], result['trace'])
    evidence = '\n\n'.join(f"{item['citation']} {item['title']} · 第 {item['page']} 页\n{item['text']}" for item in result['sources'])
    export = '\n\n'.join([f'# {task}', '\n'.join(details), result['answer'], '## 原文证据\n' + evidence])
    left, right = st.columns(2)
    is_reading = kind == 'reading'
    left.download_button('导出精读材料' if is_reading else '下载对比报告', export,
                         file_name=f'{kind}_{record["id"]}.md', mime='text/markdown', key=f'download_research_{record["id"]}')
    if right.button('保存到研究笔记' if is_reading else '将报告存入长期记忆', key=f'save_research_{record["id"]}'):
        paper_ids = settings['paper_ids']
        available = {paper['id'] for paper in store.papers()}
        paper_id = paper_ids[0] if is_reading and paper_ids[0] in available else None
        store.save_memory(result['answer'], settings.get('preset', '对比报告'), paper_id)
        st.success('已保存到记忆中心。')


def show_research_results(store, kind, settings, show_answer):
    records = store.research_results(kind)
    current = next((record for record in records if record['settings'] == settings), None)
    if current:
        show_research_result(store, current, kind, show_answer)
    elif records and settings is not None:
        st.caption('当前条件还没有生成结果。可以运行当前任务，或打开下方的历史记录。')

    history = {record['id']: record for record in records if record != current}
    if history:
        def history_label(record_id):
            record = history[record_id]
            return f"{record['created']} UTC · {'、'.join(record['papers'])} · #{record_id}"

        with st.expander('历史精读材料' if kind == 'reading' else '历史对比报告'):
            choice = st.selectbox(
                '选择历史记录', list(history), index=None,
                format_func=history_label, key=f'{kind}_history_record',
                placeholder='选择历史记录',
            )
            if choice:
                show_research_result(store, history[choice], kind, show_answer)

def search_page(store,labels):
    st.title('证据检索')
    with st.container(border=True):
        query=st.text_input('检索问题或关键词',placeholder='例如：消融实验、模型训练、retrieval accuracy',max_chars=2000)
        scope_key = 'evidence_search_scope'
        scope_options = {'format_func': labels.get, 'key': scope_key, 'placeholder': '选择检索范围'}
        if scope_key not in st.session_state:
            scope_options['default'] = list(labels)
        selected=st.multiselect('检索范围',list(labels),**scope_options)
        a,b=st.columns([2,1])
        mode=a.selectbox('召回方式',['hybrid','tfidf','bm25'],format_func=lambda x:{'hybrid':'混合检索 · BM25 + TF-IDF','tfidf':'字符向量 · TF-IDF','bm25':'关键词 · BM25'}[x])
        limit=b.slider('返回片段数',1,12,6)
        a,b=st.columns(2)
        expand=a.checkbox('扩展常见中英文研究关键词',True)
        diversity=b.checkbox('优先选择内容不同的片段',True)
        submitted=st.button('检索原文证据',type='primary',disabled=not query.strip() or not selected)
    if submitted:
        with st.spinner('双路召回与排序中…'):
            result=store.search(query,selected,limit,mode,expand,diversity)
        st.session_state.search_result={'rows':result,'query':query,'papers':selected}
    if 'search_result' in st.session_state:
        bundle=st.session_state.search_result
        st.subheader('检索结果')
        st.caption('上次检索：'+bundle['query']+' · '+str(len(bundle['rows']))+' 条；参数修改后需重新检索。')
        if not bundle['rows']: st.info('没有匹配证据。试试论文原文中的术语或更短的关键词。')
        for i,r in enumerate(bundle['rows']):
            with st.container(border=True):
                st.markdown(f"**{i+1:02d} · {r['title']}**")
                st.caption(f"第 {r['page']} 页 · {r['citation']} · {r['retrieval'].upper()}")
                if r['paper_id'] in labels:
                    st.markdown(f"[打开第 {r['page']} 页]({page_url(r)})")
                st.write(r['text'])
                with st.expander('查看排序依据'):
                    st.write(f"BM25：{r['bm25']:.3f} · TF-IDF：{r['tfidf']:.3f} · 最终排序分数：{r['score']:.6f}")
                    st.caption('实际检索词：'+r['expanded_query'])
                if st.button('收藏原文片段',key='clip_'+r['id']):
                    if r['paper_id'] in labels:
                        store.save_memory(f"{r['title']} · 第{r['page']}页 {r['citation']}\n\n{r['text']}",'原文摘录',r['paper_id'])
                        st.success('已收藏到记忆中心。')
                    else: st.warning('该论文已被删除，请重新检索。')
        if bundle['rows']:
            st.download_button('导出检索结果 JSON',json.dumps(bundle,ensure_ascii=False,indent=2),file_name='evidence.json',mime='application/json')
    with st.expander('检索方法说明'):
        st.write('BM25 查找关键词，TF-IDF 查找相近字词。混合模式用 RRF 合并两路排名；MMR 在相关性与内容多样性之间取舍。关键词扩展来自固定中英术语表，并不是自动翻译或稠密语义模型。索引保留在进程缓存中，资料改变后自动重建。')

def reading_page(store,labels,get_model,show_answer,retrieval):
    st.title('精读工作台')
    if not labels:
        st.info('先在论文库导入论文，再开始精读。')
        show_research_results(store, 'reading', None, show_answer)
        return
    pid=st.selectbox('精读论文',list(labels),format_func=labels.get)
    preset=st.selectbox('阅读任务',list(PRESETS))
    focus=st.text_area('补充关注点',placeholder='例如：先用通俗语言解释算法步骤。',max_chars=2000,height=100)
    settings = research_settings([pid], focus, retrieval, preset=preset)
    if st.session_state.mode == '本地证据模式':
        st.info('本地模式仅显示原文；生成精读材料请先连接模型。')
    with st.expander('论文目录 · 自动识别'):
        headings=store.outline(pid)
        if headings:
            for h in headings: st.write(f"第 {h['page']} 页 · {h['heading']}")
        else: st.caption('没有识别出章节标题，可在论文库按页阅读原文。')
        st.caption('目录来自文本规则匹配，可能遗漏或误识别。')
    if st.button('开始精读',type='primary'):
        try:
            result = run_visible(Agent(store, get_model(), retrieval), PRESETS[preset] + '\n用户关注点：' + settings['focus'], [pid])
            store.save_research_result('reading', settings, [labels[pid]], result)
        except (ModelError,ValueError) as exc: st.error(str(exc))
    show_research_results(store, 'reading', settings, show_answer)


def comparison_page(store, labels, get_model, show_answer, retrieval):
    st.title('论文对比')
    selected = st.multiselect('选择 2–6 篇论文', list(labels), default=list(labels)[:2], format_func=labels.get, max_selections=6)
    dimensions = st.multiselect('对比维度', ['研究问题','核心方法','数据集','实验指标与条件','创新点','局限性','复现难度'],
                                default=['研究问题','核心方法','实验指标与条件','局限性'])
    focus = st.text_area('研究关注点（可选）', placeholder='例如：关注缺失数据处理，说明是否适合小显存设备复现。', max_chars=2000)
    settings = research_settings(selected, focus, retrieval, dimensions=dimensions)
    if st.button('生成对比报告', type='primary', disabled=len(selected) < 2 or not dimensions):
        prompt = ('请对比所选论文，逐篇覆盖以下维度：' + '、'.join(dimensions)
                  + '。同时检索英文关键词：abstract method dataset experiment results limitation。关注点：' + settings['focus'])
        try:
            result = run_visible(Agent(store, get_model(), retrieval), prompt, selected, compare=True)
            store.save_research_result('comparison', settings, [labels[pid] for pid in selected], result)
        except (ModelError, ValueError) as exc:
            st.error(str(exc))
    show_research_results(store, 'comparison', settings, show_answer)

PLAN_STATUSES = ['待读', '精读中', '已读']
PRIORITY_LABELS = {1: '高 · 优先阅读', 2: '中 · 正常安排', 3: '低 · 备用参考'}


def focus_plan_paper(pid):
    st.session_state['plan_focus_paper'] = pid


def planner_page(store, labels, open_paper):
    st.title('阅读计划')
    papers = store.papers()
    paper_by_id = {paper['id']: paper for paper in papers}
    states = {pid: store.reading_state(pid) for pid in labels}
    note_counts = {
        pid: len(store.rows('SELECT id FROM memories WHERE paper_id=?', (pid,)))
        for pid in labels
    }

    if labels:
        cols = st.columns(3)
        for col, status in zip(cols, PLAN_STATUSES):
            with col:
                st.metric(status, sum(state['status'] == status for state in states.values()))

        st.markdown('#### 论文清单')
        board = st.columns(3)
        for col, status in zip(board, PLAN_STATUSES):
            with col:
                st.markdown(f'**{status}**')
                group = [pid for pid in labels if states[pid]['status'] == status]
                if not group:
                    st.caption('暂无论文')
                for pid in group:
                    paper = paper_by_id.get(pid, {})
                    state = states[pid]
                    with st.container(border=True):
                        st.markdown(f"**{labels[pid]}**")
                        meta = [f"{paper.get('pages', 0)} 页",
                                f"{paper.get('chunks', 0)} 片段",
                                f"{note_counts[pid]} 条笔记"]
                        if state['tags']:
                            meta.append(f"标签：{state['tags']}")
                        meta.append(f"优先级：{PRIORITY_LABELS[state['priority']]}")
                        st.caption(' · '.join(meta))
                        status_cols = st.columns(3)
                        for index, option in enumerate(PLAN_STATUSES):
                            status_cols[index].button(
                                option, key=f'plan_status_{pid}_{option}',
                                type='primary' if state['status'] == option else 'secondary',
                                width='stretch',
                                on_click=store.set_reading_state,
                                args=(pid, option, state['tags'], state['priority']),
                            )
                        action_cols = st.columns(2)
                        action_cols[0].button(
                            '去提问', key=f'plan_ask_{pid}', type='primary',
                            width='stretch', on_click=open_paper, args=('阅读问答', pid),
                        )
                        action_cols[1].button(
                            '编辑', key=f'plan_edit_{pid}',
                            width='stretch', on_click=focus_plan_paper, args=(pid,),
                        )

        focus_key = 'plan_focus_paper'
        if st.session_state.get(focus_key) not in labels:
            st.session_state[focus_key] = list(labels)[0]
        focus_pid = st.session_state[focus_key]
        with st.container(border=True):
            st.markdown('#### 编辑阅读状态')
            pid = st.selectbox(
                '管理论文', list(labels),
                index=list(labels).index(focus_pid), format_func=labels.get,
            )
            if pid != focus_pid:
                focus_pid = pid
                st.session_state[focus_key] = pid
            state = states[focus_pid]
            with st.form('state_' + focus_pid):
                a, b, c = st.columns([1, 1, 2], vertical_alignment='bottom')
                status = a.selectbox(
                    '阅读状态', PLAN_STATUSES, index=PLAN_STATUSES.index(state['status']), width=150,
                )
                priority = b.selectbox(
                    '优先级', [1, 2, 3], index=state['priority'] - 1,
                    format_func=lambda value: PRIORITY_LABELS[value], width=160,
                )
                tags = c.text_input(
                    '研究标签', value=state['tags'], width=260,
                    placeholder='例如：RAG，低资源，待复现', max_chars=300,
                )
                if st.form_submit_button('更新阅读状态', type='primary'):
                    store.set_reading_state(focus_pid, status, tags, priority)
                    st.rerun()
    else:
        st.info('导入论文后，可以管理阅读状态和标签。')

    st.subheader('研究待办')
    with st.form('task_add', clear_on_submit=True):
        a, b, c = st.columns([3, 2, 1], vertical_alignment='bottom')
        title = a.text_input(
            '下一步行动', width=320,
            placeholder='例如：核对表 3 的实验设置', max_chars=300,
        )
        task_pid = b.selectbox(
            '关联资料', [None] + list(labels), width=220,
            format_func=lambda paper_id: labels.get(paper_id, '通用任务'),
        )
        add_task = c.form_submit_button('添加待办', type='primary')
        if add_task:
            if title.strip():
                store.add_task(title, task_pid)
                st.rerun()
            else:
                st.warning('请填写待办内容。')

    tasks = store.rows('SELECT * FROM tasks ORDER BY done,created DESC,id DESC')
    if not tasks:
        with st.container(border=True):
            st.caption('暂无待办。添加一条“下一步行动”，把阅读计划落到具体动作。')
    for task in tasks:
        with st.container(border=True):
            left, right = st.columns([6, 1], vertical_alignment='center')
            done = left.checkbox(task['title'], value=bool(task['done']), key='task_' + task['id'])
            left.caption(labels.get(task['paper_id'], '通用任务'))
            right.button(
                '删除', key=f'delete_task_{task["id"]}', width='stretch', type='tertiary',
                on_click=store.delete_task, args=(task['id'],),
            )
            if done != bool(task['done']):
                store.set_task(task['id'], done)
                st.rerun()
    if tasks:
        st.download_button('导出待办清单', '\n'.join(
            f"- [{'x' if task['done'] else ' '}] {task['title']} · "
            f"{labels.get(task['paper_id'], '通用任务')}" for task in tasks
        ), file_name='reading_tasks.md')

    with st.expander('实验数值计算器'):
        st.caption('支持 +、-、*、/、** 和括号；例如 (85.2 - 81.6) / 81.6 * 100 计算相对提升百分比。')
        expression = st.text_input('算式', max_chars=180)
        if st.button('计算', disabled=not expression.strip()):
            try:
                st.success('结果：' + str(calculate(expression)['value']))
            except ValueError as exc:
                st.error(str(exc))
