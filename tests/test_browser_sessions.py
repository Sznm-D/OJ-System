import hashlib
import time

import pytest


ORIGIN = {'Origin': 'http://127.0.0.1:8501'}


async def test_browser_cookie_exchange_refresh_and_logout(environment):
    app, admin, alice, bob, browser = environment
    assert (await browser.post('/api/auth/browser-ticket')).status_code == 401
    ticket = (await alice.post('/api/auth/browser-ticket')).json()['data']['ticket']
    assert (await browser.post('/auth/browser-session', json={'ticket':ticket}, headers={'Origin':'https://untrusted.test'})).status_code == 403
    response = await browser.post('/auth/browser-session', json={'ticket':ticket}, headers=ORIGIN)
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == ORIGIN['Origin']
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    assert not response.json()['data']
    assert (await browser.get('/api/auth/me')).json()['data']['username'] == 'alice'
    assert (await browser.post('/auth/browser-session', json={'ticket':ticket}, headers=ORIGIN)).status_code == 401
    await alice.post('/api/auth/logout')
    assert (await browser.get('/api/auth/me')).status_code == 401
    assert (await browser.post('/auth/browser-session', json={}, headers=ORIGIN)).status_code == 200
    assert not browser.cookies.get('session_id')


@pytest.mark.parametrize('reason', ['expired', 'banned'])
async def test_browser_cookie_cannot_restore_invalid_session(environment, reason):
    app, admin, alice, bob, browser = environment
    ticket = (await alice.post('/api/auth/browser-ticket')).json()['data']['ticket']
    if reason == 'expired':
        key = hashlib.sha256(alice.cookies.get('session_id').encode()).hexdigest()
        session = await app.state.store.get('sessions', key)
        session['expires_at'] = time.time() - 1
        await app.state.store.put('sessions', key, session)
    else:
        uid = (await alice.get('/api/auth/me')).json()['data']['user_id']
        await admin.put(f'/api/users/{uid}/role', json={'role':'banned'})
    assert (await browser.post('/auth/browser-session', json={'ticket':ticket}, headers=ORIGIN)).status_code == (401 if reason=='expired' else 403)
    assert not browser.cookies.get('session_id')
