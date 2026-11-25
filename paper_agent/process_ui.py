"""Observable execution summaries, never internal model reasoning."""
from html import escape
import streamlit as st

NAMES={'recall_memory':'读取阅读记忆','search_papers':'检索论文证据','read_page':'核对原文页面','paper_outline':'查看章节目录','calculate':'计算实验数值','invalid_call':'检查工具参数'}

def describe(event):
    if event['tool']=='stage': return event['title'],event['detail']
    title=NAMES.get(event['tool'],event['tool'])
    if event.get('status')=='失败': return title,event.get('error','执行失败')
    args=event.get('arguments',{})
    n=event.get('results',0)
    if event['tool']=='search_papers': detail=f'检索到 {n} 个原文片段。'
    elif event['tool']=='recall_memory': detail=f'读取 {n} 条偏好或笔记。'
    elif event['tool']=='read_page': detail=f"读取第 {args.get('page','?')} 页，返回 {n} 页。"
    elif event['tool']=='calculate': detail=f"计算结果：{event.get('value','见回答')}。"
    elif event['tool']=='paper_outline': detail=f'识别 {n} 个章节标题。'
    else: detail=f'返回 {n} 项结果。'
    if 'ms' in event: detail+=f" 用时 {event['ms']} ms。"
    return title,detail

def render_process(trace):
    if not trace: return
    with st.expander('查看分析过程',expanded=False):
        blocks=[]
        for event in trace:
            title,detail=describe(event)
            status=event.get('status','成功')
            style='failed' if status=='失败' else 'warn' if status=='需核对' else ''
            blocks.append(f'<div class="process-step {style}"><div class="process-title">{escape(title)}<span class="process-badge">{escape(status)}</span></div><div class="process-detail">{escape(detail)}</div></div>')
        st.markdown('<div class="process-list">'+''.join(blocks)+'</div>',unsafe_allow_html=True)
        with st.expander('查看执行数据'):
            st.json(trace,expanded=False)
        st.caption('展示实际执行摘要，不是模型内部完整思维链。')

def run_visible(agent,question,selected,history=None,compare=False):
    phase = st.empty()
    # Streamlit treats a new status label as a new expander and resets its open state.
    with st.status('分析进度', expanded=True) as status:
        live=st.empty()
        completed=[]
        def update(event):
            title,detail=describe(event)
            if event.get('status')!='进行中': completed.append(event)
            with live.container():
                for item in completed[-8:]:
                    t,d=describe(item)
                    icon='✕' if item.get('status')=='失败' else '!' if item.get('status')=='需核对' else '✓'
                    st.write(f'{icon} **{t}** · {d}')
                if event.get('status')=='进行中': st.write(f'⏳ **{title}** · {detail}')
            phase.caption(title)
        try:
            result=agent.run(question,selected,history,compare,on_event=update)
        except Exception:
            phase.caption('处理未完成，请检查下方提示')
            status.update(state='error')
            raise
        warning=any(t.get('status') in ('失败','需核对') for t in result['trace'])
        phase.caption('处理完成 · 部分步骤需核对' if warning else '处理完成')
        status.update(state='complete')
        return result
