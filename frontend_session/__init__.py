"""Browser cookie handshake for the Streamlit session, with no third-party JS dependency."""
import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import streamlit as st
import streamlit.components.v1 as components


cookie_bridge = components.declare_component('oj_browser_session', path=str(Path(__file__).parent / 'component'))


def browser_api_url():
    explicit = os.getenv('OJ_BROWSER_API_URL')
    if explicit:
        return explicit.rstrip('/')
    backend = urlparse(os.getenv('OJ_API_URL', 'http://127.0.0.1:8001'))
    browser_url = st.context.url
    browser = urlparse(browser_url if isinstance(browser_url, str) else '')
    # Cookies are shared across ports but not between localhost and 127.0.0.1.
    if backend.hostname in {'127.0.0.1', 'localhost'} and browser.hostname in {'127.0.0.1', 'localhost'}:
        return urlunparse(backend._replace(netloc=f'{browser.hostname}:{backend.port or 8001}')).rstrip('/')
    return backend.geturl().rstrip('/')


def sync_browser_cookie(api):
    pending = st.session_state.get('browser_cookie_pending')
    if pending is None:
        return
    result = cookie_bridge(endpoint=browser_api_url() + '/auth/browser-session',
                           ticket=pending.get('ticket'), key='browser_cookie_' + pending['id'], default=None)
    if result and result.get('ok'):
        st.session_state.pop('browser_cookie_pending', None)
        st.rerun()
    if result:
        st.warning('浏览器登录状态尚未保存，刷新可能需要重新登录。请检查浏览器与后端连接。')
        if st.button('重试同步登录状态'):
            ticket = api('POST', '/auth/browser-ticket')['ticket'] if 'user' in st.session_state else None
            import uuid
            st.session_state.browser_cookie_pending = {'ticket': ticket, 'id': uuid.uuid4().hex}
            st.rerun()
