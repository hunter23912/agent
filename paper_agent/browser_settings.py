"""Restore and save model settings through the current browser's site storage."""
from uuid import uuid4

import streamlit as st


MODEL_MODES = ('本地证据模式', '大模型 Agent 模式')
FIELDS = ('mode', 'base_url', 'model', 'api_key')
COMMAND = 'model_browser_command'
FEEDBACK = 'model_browser_feedback'

STORAGE_SCRIPT = """
const storageKey = 'paper-atlas.model-settings.v1';
const handled = new WeakMap();

export default function({ data, parentElement, setTriggerValue }) {
    if (!data || handled.get(parentElement) === data.id) return;
    handled.set(parentElement, data.id);

    let settings = null;
    let ok = true;
    try {
        const storage = window.localStorage;
        if (data.action === 'load') {
            settings = JSON.parse(storage.getItem(storageKey));
        } else if (data.action === 'save') {
            storage.setItem(storageKey, JSON.stringify(data.settings));
        } else if (data.action === 'clear') {
            storage.removeItem(storageKey);
        }
    } catch {
        ok = false;
    }
    setTriggerValue('reply', { id: data.id, ok, settings });
}
"""


def sync_model_settings():
    if COMMAND not in st.session_state:
        st.session_state[COMMAND] = {'id': uuid4().hex, 'action': 'load'}
    command = st.session_state[COMMAND]
    if command is None:
        return
    # Each Streamlit runtime has its own component registry.
    browser_storage = st.components.v2.component('model_browser_storage', js=STORAGE_SCRIPT)
    response = browser_storage(data=command, on_reply_change=lambda: None, key='paper_atlas_browser_storage').reply
    if not isinstance(response, dict) or response.get('id') != command['id']:
        return
    st.session_state[COMMAND] = None
    action = command['action']
    if action == 'load' and response.get('ok'):
        settings = response.get('settings')
        if settings is None:
            return
        if (isinstance(settings, dict) and all(isinstance(settings.get(field), str) for field in FIELDS)
                and settings['mode'] in MODEL_MODES):
            st.session_state.update({field: settings[field] for field in FIELDS})
            return
        st.session_state[FEEDBACK] = ('load', False)
        return
    st.session_state[FEEDBACK] = (action, bool(response.get('ok')))


def apply_model_settings(settings, *, clear=False):
    st.session_state.update(settings)
    command = {'id': uuid4().hex, 'action': 'clear' if clear else 'save'}
    if clear:
        st.session_state.pop('model_catalog', None)
    else:
        command['settings'] = settings
    st.session_state[COMMAND] = command
    st.session_state.pop(FEEDBACK, None)


def switch_model_mode():
    settings = {field: st.session_state[field] for field in FIELDS}
    settings['mode'] = st.session_state.model_mode_choice
    apply_model_settings(settings)


def show_storage_feedback():
    feedback = st.session_state.get(FEEDBACK)
    if feedback is None:
        return
    action, ok = feedback
    if ok:
        st.success('设置已应用并保存到此浏览器。' if action == 'save' else '已清除此浏览器的模型配置。')
    else:
        messages = {
            'load': '无法读取此浏览器保存的模型配置，当前使用启动默认值。',
            'save': '设置已应用到当前会话。浏览器未能保存，刷新后可能丢失。',
            'clear': '当前会话已恢复默认值。浏览器未能清除配置，可在浏览器中清除此站点的数据。',
        }
        st.warning(messages[action])
