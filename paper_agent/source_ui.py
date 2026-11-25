"""In-app links to the exact page behind an evidence citation."""
from io import BytesIO
from urllib.parse import urlencode

import streamlit as st

from .citations import CITATION
from .pdf_preview import PDFIUM_LOCK
from .process_ui import render_process


def page_url(source):
    """Link to the in-app library preview instead of a separate source page."""
    return '?' + urlencode({'paper': source['paper_id'], 'read_page': source['page']})


def show_answer(answer, sources, trace, available_papers):
    linked = {source['citation']: page_url(source) for source in sources if source['paper_id'] in available_papers}
    st.markdown(CITATION.sub(lambda match: f'[{match[1]}]({linked[match[0]]})' if match[0] in linked else match[0], answer))
    with st.expander(f'原文证据 · {len(sources)} 条'):
        if not sources:
            st.caption('本次未检索到证据。')
        for source in sources:
            st.text(f"{source['title']} · 第 {source['page']} 页")
            if source['paper_id'] in available_papers:
                st.markdown(f"[打开第 {source['page']} 页]({page_url(source)}) · `{source['citation']}`")
            else:
                st.caption(f"论文已移除 · 归档摘录 `{source['citation']}`")
            st.text(source['text'])
            if 'score' in source:
                st.caption(f"排序分数 {source['score']:.4f} · {source.get('retrieval', 'tfidf').upper()}")
            st.divider()
    render_process(trace)


@st.cache_data(max_entries=12, show_spinner=False)
def render_pdf_page(pdf_bytes, number):
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(pdf_bytes, password='')
        try:
            page = document[number - 1]
            scale = min(2.5, 1800 / max(page.get_size()))
            bitmap = page.render(scale=scale)
            try:
                with BytesIO() as output:
                    bitmap.to_pil().save(output, format='PNG')
                    return output.getvalue()
            finally:
                bitmap.close()
        finally:
            document.close()


def show_pdf_page_image(store, paper_id, number, caption=None):
    """Render the original PDF page in place; return its bytes when available."""
    original = store.original_pdf(paper_id)
    if original is None:
        st.info('这篇论文只保存了提取文字。重新上传同一份 PDF 可补回原件。')
        return None
    try:
        from pypdfium2 import PdfiumError
    except ImportError:
        st.warning('预览组件尚未安装。可以下载原始 PDF 核对这一页。')
        return original
    try:
        st.image(render_pdf_page(original, number), caption=caption or f'原始 PDF · 第 {number} 页')
    except (PdfiumError, OSError, ValueError, IndexError):
        st.warning('这页暂时无法预览。可以下载原始 PDF 核对。')
    return original


def show_source_page(store):
    st.markdown('[返回研究工作台](?)')
    paper_id = st.query_params.get('source', '')
    try:
        number = int(st.query_params.get('page', ''))
        if not 1 <= number < 2**63:
            raise ValueError
    except ValueError:
        st.error('页码无效。')
        return
    rows = store.read_page(paper_id, number)
    if not rows:
        st.warning('这一页已无法打开。可以返回报告查看保存的证据摘录。')
        return
    row = rows[0]
    paper = store.rows('SELECT filename FROM papers WHERE id=?', (paper_id,))
    filename = paper[0]['filename'] if paper else '论文.pdf'
    st.title(row['title'])
    st.subheader(f'第 {number} 页')
    original = store.original_pdf(paper_id)
    if original is not None:
        try:
            from pypdfium2 import PdfiumError
        except ImportError:
            st.warning('预览组件尚未安装。可以下载原始 PDF 核对这一页。')
        else:
            try:
                st.image(render_pdf_page(original, number), caption=f'原始 PDF · 第 {number} 页')
            except (PdfiumError, OSError, ValueError, IndexError):
                st.warning('这页暂时无法预览。可以下载原始 PDF 核对。')
        st.download_button('下载原始 PDF', original, filename, mime='application/pdf')
        with st.expander('提取的文字'):
            st.text(row['text'])
    else:
        if filename.lower().endswith('.pdf'):
            st.info('这篇论文只保存了提取文字。重新上传同一份 PDF 可补回原件。')
        st.text(row['text'])
