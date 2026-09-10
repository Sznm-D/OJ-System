"""Exchange one-use tickets for HttpOnly browser cookies without exposing session tokens to JS."""
import hashlib
import math
import os
import secrets
import time

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import Field

from .models import Model


class BrowserSession(Model):
    ticket: str | None = Field(default=None, min_length=20, max_length=200)


def install_browser_sessions(app, store, response):
    origins = [value.strip().rstrip('/') for value in os.getenv(
        'OJ_FRONTEND_ORIGINS', 'http://127.0.0.1:8501,http://localhost:8501').split(',') if value.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=['POST'], allow_headers=['Content-Type'])
    tickets = {}

    @app.post('/api/auth/browser-ticket')
    async def browser_ticket(request: Request):
        now = time.time()
        # This route uses the normal authenticated API middleware.
        async with store.lock:
            for key in list(tickets):
                if tickets[key]['expires'] <= now:
                    del tickets[key]
            token = request.cookies['session_id']
            session_key = hashlib.sha256(token.encode()).hexdigest()
            for key in list(tickets):
                if tickets[key]['session_key'] == session_key:
                    del tickets[key]
            ticket = secrets.token_urlsafe(32)
            tickets[hashlib.sha256(ticket.encode()).hexdigest()] = {
                'token': token, 'session_key': session_key, 'expires': now + 60}
        result = response({'ticket': ticket})
        result.headers['Cache-Control'] = 'no-store'
        return result

    @app.post('/auth/browser-session')
    async def browser_session(request: Request, body: BrowserSession):
        # Exact origin + JSON preflight protects binding and clearing against cross-site requests.
        if request.headers.get('origin') not in origins:
            return response(msg='浏览器来源不受信任', code=403)
        if body.ticket is None:
            result = response(msg='browser cookie cleared')
            result.delete_cookie('session_id', httponly=True, samesite='strict',
                                 secure=os.getenv('OJ_COOKIE_SECURE') == '1')
        else:
            async with store.lock:
                entry = tickets.pop(hashlib.sha256(body.ticket.encode()).hexdigest(), None)
                if not entry or entry['expires'] <= time.time():
                    return response(msg='登录同步凭证已失效，请重试', code=401)
                session = await store.get('sessions', entry['session_key'])
                if not session or session['expires_at'] <= time.time():
                    return response(msg='登录已失效', code=401)
                user = await store.get('users', session['user_id'])
                if not user or user['role'] == 'banned':
                    return response(msg='账户已失效或禁用', code=403)
                result = response(msg='browser session saved')
                result.set_cookie('session_id', entry['token'],
                                  max_age=max(1, math.floor(session['expires_at'] - time.time())),
                                  httponly=True, samesite='strict', secure=os.getenv('OJ_COOKIE_SECURE') == '1')
        result.headers['Cache-Control'] = 'no-store'
        return result
