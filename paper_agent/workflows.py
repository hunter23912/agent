"""Additional research workflows, backed by the same local library."""
import json
import streamlit as st
from .agent import Agent, ModelError
from .calculator import calculate

PRESETS={
    '快速精读':'请输出：一句话研究问题、三个关键贡献、方法步骤、实验设置、主要结论、局限性。每部分有原文引用，缺失则说明。检索 abstract introduction contribution method experiment limitation。',
    '方法拆解':'请用适合初学者的语言解释输入、输出、各模块、训练目标和推理过程。给出符号解释，不编造论文中没有的公式。检索 method architecture objective training inference。',
    '复现清单':'请整理数据准备、预处理、模型结构、损失、超参数、训练与评估步骤，以及论文未披露的信息。严格区分论文信息与建议。检索 implementation dataset hyperparameter training evaluation reproducibility。',
    '自测问答':'请基于论文制作五组自测题和参考答案，每个答案有来源引用。由研究问题、方法到实验局限逐步加深。检索 abstract method experiment limitation。'
}

def export_result(result):
    return result['answer']+'\n\n## 原文证据\n'+'\n\n'.join(f"{s['citation']} {s['title']} · 第{s['page']}页\n{s['text']}" for s in result['sources'])

def search_page(store,labels):
    st.title('证据检索')
    with st.container(border=True):
        query=st.text_input('检索问题或关键词',placeholder='例如：消融实验、模型训练、retrieval accuracy',max_chars=2000)
        selected=st.multiselect('检索范围',list(labels),default=list(labels),format_func=labels.get)
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

def reading_page(store,labels,get_model,show_sources,retrieval):
    st.title('精读工作台')
    if not labels:
        st.info('先在论文库导入论文，再开始精读。')
        return
    pid=st.selectbox('精读论文',list(labels),format_func=labels.get)
    preset=st.selectbox('阅读任务',list(PRESETS))
    focus=st.text_area('补充关注点',placeholder='例如：先用通俗语言解释算法步骤。',max_chars=2000,height=100)
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
            from .process_ui import run_visible
            result=run_visible(Agent(store,get_model(),retrieval),PRESETS[preset]+'\n用户关注点：'+focus,[pid])
            st.session_state.reading_result={'paper':pid,'preset':preset,'result':result}
        except (ModelError,ValueError) as exc: st.error(str(exc))
    saved=st.session_state.get('reading_result')
    if saved and saved['paper']==pid:
        r=saved['result']
        st.subheader(saved['preset']+' · 阅读材料')
        st.markdown(r['answer'])
        show_sources(r['sources'],r['trace'])
        a,b=st.columns(2)
        a.download_button('导出精读材料',export_result(r),file_name='reading_guide.md',mime='text/markdown')
        if b.button('保存到研究笔记'):
            store.save_memory(r['answer'],saved['preset'],pid)
            st.success('已保存到记忆中心。')

def planner_page(store,labels):
    st.title('阅读计划')
    states={pid:store.reading_state(pid) for pid in labels}
    cols=st.columns(3)
    for col,status in zip(cols,['待读','精读中','已读']):
        col.metric(status,sum(s['status']==status for s in states.values()))
    if labels:
        with st.container(border=True):
            pid=st.selectbox('管理论文',list(labels),format_func=labels.get)
            state=states[pid]
            with st.form('state_'+pid):
                a,b=st.columns(2)
                status=a.selectbox('阅读状态',['待读','精读中','已读'],index=['待读','精读中','已读'].index(state['status']))
                priority=b.selectbox('优先级',[1,2,3],index=state['priority']-1,format_func=lambda i:{1:'高 · 优先阅读',2:'中 · 正常安排',3:'低 · 备用参考'}[i])
                tags=st.text_input('研究标签',value=state['tags'],placeholder='例如：RAG，低资源，待复现',max_chars=300)
                if st.form_submit_button('更新阅读状态',type='primary'):
                    store.set_reading_state(pid,status,tags,priority)
                    st.rerun()
    else: st.info('导入论文后，可以管理阅读状态和标签。')
    st.subheader('研究待办')
    with st.form('task_add',clear_on_submit=True):
        title=st.text_input('下一步行动',placeholder='例如：核对表 3 的实验设置',max_chars=300)
        task_pid=st.selectbox('关联资料',[None]+list(labels),format_func=lambda p:labels.get(p,'通用任务'))
        if st.form_submit_button('添加待办'):
            if title.strip():
                store.add_task(title,task_pid)
                st.rerun()
            else: st.warning('请填写待办内容。')
    tasks=store.rows('SELECT * FROM tasks ORDER BY done,created DESC,id DESC')
    if not tasks: st.caption('暂无待办。')
    for task in tasks:
        with st.container(border=True):
            done=st.checkbox(task['title'],value=bool(task['done']),key='task_'+task['id'])
            st.caption(labels.get(task['paper_id'],'通用任务'))
            if done!=bool(task['done']):
                store.set_task(task['id'],done)
                st.rerun()
    if tasks:
        st.download_button('导出待办清单','\n'.join(f"- [{'x' if t['done'] else ' '}] {t['title']} · {labels.get(t['paper_id'],'通用任务')}" for t in tasks),file_name='reading_tasks.md')
    with st.expander('实验数值计算器'):
        st.caption('支持 +、-、*、/、** 和括号；例如 (85.2 - 81.6) / 81.6 * 100 计算相对提升百分比。')
        expression=st.text_input('算式',max_chars=180)
        if st.button('计算',disabled=not expression.strip()):
            try: st.success('结果：'+str(calculate(expression)['value']))
            except ValueError as exc: st.error(str(exc))
