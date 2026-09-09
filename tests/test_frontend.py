"""Streamlit AppTest against a real isolated FastAPI HTTP server."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def frontend_backend(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    env = dict(os.environ, OJ_DATA_DIR=str(tmp_path_factory.mktemp("frontend_db")))
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(port), "--host", "127.0.0.1"], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(200):
            try:
                if httpx.get(url + "/api/health", trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            pytest.fail("frontend test backend did not start")
        yield root, url
    finally:
        process.terminate()
        process.wait(timeout=10)


def click(app, label):
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception, [exc.message for exc in app.exception]


def test_frontend_admin_navigation_and_real_submission(frontend_backend, monkeypatch):
    root, url = frontend_backend
    monkeypatch.setenv("OJ_API_URL", url)
    app = AppTest.from_file(str(root / "app.py"), default_timeout=20).run()
    assert not app.exception
    app.text_input(key="login_name").set_value("admin")
    app.text_input(key="login_password").set_value("admintestpassword")
    click(app, "登录")
    assert app.title[0].value == "题库"
    next(area for area in app.text_area if area.label == "代码").set_value("a,b=map(int,input().split());print(a+b)")
    click(app, "提交评测")
    for _ in range(50):
        app.run()
        if any("AC" in s.value for s in app.success):
            break
        time.sleep(0.05)
    assert any("80 / 80" in s.value for s in app.success)
    for page in ["提交记录", "题目管理", "AI 智能命题", "我的账户", "用户管理", "日志审计"]:
        app.sidebar.radio(key="nav").set_value(page).run()
        assert not app.exception, (page, [exc.message for exc in app.exception])
        assert app.title[0].value == page
        assert not app.error, (page, [error.value for error in app.error])
    click(app, "退出登录")
    assert app.title[0].value == "从一道题开始"


def test_frontend_registration_and_non_admin_permissions(frontend_backend, monkeypatch):
    root, url = frontend_backend
    monkeypatch.setenv("OJ_API_URL", url)
    app = AppTest.from_file(str(root / "app.py"), default_timeout=20).run()
    next(field for field in app.text_input if field.label == "用户名（3–40 个字符）").set_value("frontend_student")
    next(field for field in app.text_input if field.label == "密码（至少 6 位）").set_value("password123")
    next(field for field in app.text_input if field.label == "再次输入密码").set_value("password123")
    click(app, "创建账户")
    assert any("注册成功" in s.value for s in app.success)
    app.text_input(key="login_name").set_value("frontend_student")
    app.text_input(key="login_password").set_value("password123")
    click(app, "登录")
    assert "用户管理" not in app.sidebar.radio(key="nav").options
    assert "日志审计" not in app.sidebar.radio(key="nav").options
