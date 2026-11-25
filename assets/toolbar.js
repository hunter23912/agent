const toolbarSelector = '[data-testid="stHeader"], [data-testid="stMainMenuPopover"]';
const hintSelector = '[data-testid="InputInstructions"]';
const enterHint = /^Press (?:Enter|⌘|Ctrl|⌃|Control)|^按 Enter/;
const labels = new Map([
    ['Deploy', '部署'],
    ['Main menu', '主菜单'],
    ['Rerun', '重新运行'],
    ['Auto rerun', '自动重新运行'],
    ['Clear cache', '清除缓存'],
    ['Print', '打印'],
    ['Record screen', '录制屏幕'],
    ['Made with Streamlit v', '基于 Streamlit v'],
    ['Copy version to clipboard', '复制版本号'],
]);

export default function ({ parentElement }) {
    const page = parentElement.ownerDocument;

    function translateToolbar() {
        for (const toolbar of page.querySelectorAll(toolbarSelector)) {
            // Edit the existing labels so native controls keep their events and shortcuts.
            const text = page.createTreeWalker(toolbar, NodeFilter.SHOW_TEXT);
            while (text.nextNode()) {
                const translated = labels.get(text.currentNode.data.trim());
                if (translated) text.currentNode.data = translated;
            }
            for (const element of toolbar.querySelectorAll('[aria-label], [title]')) {
                for (const attribute of ['aria-label', 'title']) {
                    const translated = labels.get(element.getAttribute(attribute));
                    if (translated) element.setAttribute(attribute, translated);
                }
            }
        }
    }

    function stripEnterHints() {
        for (const hint of page.querySelectorAll(hintSelector)) {
            for (const span of Array.from(hint.children)) {
                if (enterHint.test(span.textContent.trim())) span.remove();
            }
        }
    }

    const elementOf = node => node?.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    const observer = new MutationObserver(records => {
        // Streamlit mounts menus and input hints after the initial render.
        if (records.some(({ target, addedNodes }) =>
            elementOf(target)?.closest?.(toolbarSelector) ||
            elementOf(target)?.closest?.(hintSelector) ||
            Array.from(addedNodes).some(node =>
                node.matches?.(toolbarSelector) || node.querySelector?.(toolbarSelector) ||
                node.matches?.(hintSelector) || node.querySelector?.(hintSelector))
        )) {
            translateToolbar();
            stripEnterHints();
        }
    });
    translateToolbar();
    stripEnterHints();
    observer.observe(page.body, { childList: true, characterData: true, subtree: true });
    return () => observer.disconnect();
}
