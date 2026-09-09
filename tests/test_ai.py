import asyncio
import json

import httpx
import pytest

from backend.main import create_app
from conftest import PROBLEM


SECRET = "test-secret-do-not-leak"
CONFIG = {"provider_url": "https://provider.test/v1", "model": "teacher-model", "api_key": SECRET,
          "input_price": 1, "output_price": 2, "price_unit": 1000}
DRAFT = {"problem": dict(PROBLEM, testcases=[{"input": f"{i} 2\n", "output": f"{i + 2}\n"} for i in range(8)]),
         "reference_solution": "a,b=map(int,input().split()); print(a+b)",
         "coverage": ["零、正数和一般值"], "explanation": "整数运算入门题，O(1) 时间和空间。"}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, text, slow=False, usage=True):
        self.text, self.slow, self.usage = text, slow, usage
        self.closed = False

    async def __aiter__(self):
        for offset in range(0, len(self.text), 180):
            await asyncio.sleep(0.08 if self.slow else 0.001)
            value = {"choices": [{"delta": {"content": self.text[offset:offset + 180]}}]}
            yield ("data: " + json.dumps(value) + "\n\n").encode()
        if self.usage:
            yield b'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":50}}\n\n'
        yield b'data: [DONE]\n\n'

    async def aclose(self):
        self.closed = True


async def wait_task(client, tid):
    for _ in range(500):
        task = (await client.get(f"/api/ai/problem-tasks/{tid}")).json()["data"]
        if task["status"] not in {"pending", "running"}:
            return task
        await asyncio.sleep(0.01)
    pytest.fail("AI task did not finish")


async def login(client):
    await client.post("/api/auth/login", json={"username": "admin", "password": "admintestpassword"})


async def test_ai_stream_usage_config_encryption_and_editor_handoff(tmp_path):
    captured, streams = [], []
    async def handler(request):
        captured.append(request)
        stream = Chunks(json.dumps(DRAFT), slow=True)
        streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client:
            await login(client)
            config = await client.put("/api/ai/model-config", json=CONFIG)
            assert config.status_code == 200 and SECRET not in config.text
            stored = await app.state.store.get("ai_configs", "1")
            assert SECRET not in json.dumps(stored)
            assert app.state.ai.cipher.decrypt(stored["api_key_encrypted"].encode()).decode() == SECRET
            task = await client.post("/api/ai/problem-tasks/", json={"requirement": "整数加法入门练习，覆盖零与正数"})
            tid = task.json()["data"]["task_id"]
            await asyncio.sleep(0.35)
            progress = (await client.get(f"/api/ai/problem-tasks/{tid}")).json()["data"]
            assert progress["status"] == "running" and "字符" in progress["progress"]
            assert progress["usage"]["output_tokens"] > 0
            result = await wait_task(client, tid)
            assert result["status"] == "success", result
            assert result["usage"]["input_tokens"] == 100 and result["usage"]["output_tokens"] == 50
            assert result["usage"]["cost"] == 0.2
            assert not result["usage"]["estimated"]
            assert str(captured[0].url) == "https://provider.test/v1/chat/completions"
            assert json.loads(captured[0].content)["model"] == "teacher-model"
            assert captured[0].headers["authorization"] == "Bearer " + SECRET
            assert (await client.post("/api/problems/", json=result["result"]["problem"])).status_code == 200
            assert (await client.put(f"/api/ai/problem-tasks/{tid}/cancel")).status_code == 409
            assert streams[0].closed


async def test_ai_cancel_actually_closes_stream_and_enforces_owner(tmp_path):
    stream = Chunks(json.dumps(DRAFT) * 20, slow=True)
    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as other:
            await login(client)
            await other.post("/api/users/", json={"username": "student", "password": "password123"})
            await other.post("/api/auth/login", json={"username": "student", "password": "password123"})
            await client.put("/api/ai/model-config", json=CONFIG)
            tid = (await client.post("/api/ai/problem-tasks/", json={"requirement": "测试一个可中断的任务"})).json()["data"]["task_id"]
            await asyncio.sleep(0.15)
            assert (await other.get(f"/api/ai/problem-tasks/{tid}")).status_code == 403
            assert (await other.put(f"/api/ai/problem-tasks/{tid}/cancel")).status_code == 403
            assert (await client.post("/api/ai/problem-tasks/", json={"requirement": "不允许并行的第二个任务"})).status_code == 409
            assert (await client.put(f"/api/ai/problem-tasks/{tid}/cancel")).json()["data"]["status"] == "cancelled"
            assert stream.closed
            result = await wait_task(client, tid)
            assert result["status"] == "cancelled" and result["result"] is None
            assert result["usage"]["estimated"]


@pytest.mark.parametrize("failure", ["unauthorized", "invalid_json", "wrong_answer", "no_usage"])
async def test_ai_failure_repair_and_missing_usage(tmp_path, failure):
    calls = []
    async def handler(request):
        calls.append(request)
        if failure == "unauthorized":
            return httpx.Response(401, text=SECRET)
        draft = json.loads(json.dumps(DRAFT))
        if failure == "wrong_answer" and len(calls) == 1:
            draft["reference_solution"] = "print(-999)"
        text = "invalid json" if failure == "invalid_json" else json.dumps(draft)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks(text, usage=failure != "no_usage"))
    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client:
            await login(client)
            await client.put("/api/ai/model-config", json=CONFIG)
            tid = (await client.post("/api/ai/problem-tasks/", json={"requirement": "生成一道整数相加入门题"})).json()["data"]["task_id"]
            result = await wait_task(client, tid)
            assert SECRET not in json.dumps(result)
            if failure in {"invalid_json", "unauthorized"}:
                assert result["status"] == "error"
            else:
                assert result["status"] == "success", result
                if failure == "wrong_answer":
                    assert len(calls) == 2 and result["usage"]["total_tokens"] == 300
                else:
                    assert result["usage"]["estimated"] and result["usage"]["total_tokens"] > 0
