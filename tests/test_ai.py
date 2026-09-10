import asyncio
import json
import shutil

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
VERIFICATION = {"input_validator": "import sys\na=sys.stdin.read().split()\nprint('VALID' if len(a)==2 and all(x.lstrip('-').isdigit() for x in a) else 'INVALID: expected two integers')",
                "oracle_solution": "import sys\nprint(sum(map(int,sys.stdin.read().split())))",
                "explanation": "逐个累加整数，与直接相加实现交叉验证。"}


def verification_response(request):
    if "独立的 OJ 数据审核员" in json.loads(request.content)["messages"][0]["content"]:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks(json.dumps(VERIFICATION)))


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
    for _ in range(2000):
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
        if response := verification_response(request):
            return response
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
            assert result["usage"]["input_tokens"] == 200 and result["usage"]["output_tokens"] == 100
            assert result["usage"]["cost"] == 0.4
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


@pytest.mark.parametrize("failure", ["unauthorized", "invalid_json", "wrong_answer", "no_usage", "connection_denied"])
async def test_ai_failure_repair_and_missing_usage(tmp_path, failure):
    calls = []
    async def handler(request):
        if response := verification_response(request):
            return response
        calls.append(request)
        if failure == "connection_denied":
            raise httpx.ConnectError("[WinError 10013] " + SECRET, request=request)
        if failure == "unauthorized":
            return httpx.Response(401, text=SECRET)
        draft = json.loads(json.dumps(DRAFT))
        if failure == "wrong_answer" and len(calls) == 1:
            draft["reference_solution"] = "print(-999)"
        text = "invalid json" if failure == "invalid_json" and len(calls) <= 6 else json.dumps(draft)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks(text, usage=failure != "no_usage"))
    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client:
            await login(client)
            await client.put("/api/ai/model-config", json=CONFIG)
            tid = (await client.post("/api/ai/problem-tasks/", json={"requirement": "生成一道整数相加入门题"})).json()["data"]["task_id"]
            result = await wait_task(client, tid)
            assert SECRET not in json.dumps(result)
            if failure in {"unauthorized", "connection_denied"}:
                assert result["status"] == "error"
                assert len(calls) == 1
                if failure == "connection_denied":
                    assert "10013" in result["progress"]
                    assert result["progress"] in result["events"]
                    assert result["usage"]["total_tokens"] == result["usage"]["cost"] == 0
            else:
                assert result["status"] == "success", result
                if failure == "wrong_answer":
                    assert len(calls) == 2 and result["usage"]["total_tokens"] == 600
                elif failure == "invalid_json":
                    assert len(calls) == result["attempt"] == 7
                    assert result["usage"]["total_tokens"] == 1200
                else:
                    assert result["usage"]["estimated"] and result["usage"]["total_tokens"] > 0


@pytest.mark.parametrize("language", ["python", "cpp"])
async def test_ai_repairs_schema_then_execution_with_diagnostics(tmp_path, language):
    if language == "cpp" and not shutil.which("g++"):
        pytest.skip("g++ is required for real C++ reference validation")
    calls = []

    async def handler(request):
        if response := verification_response(request):
            return response
        calls.append(json.loads(request.content))
        draft = json.loads(json.dumps(DRAFT))
        draft["reference_language"] = language
        if language == "cpp":
            draft["reference_solution"] = '#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b;}'
        if len(calls) == 1:
            draft["problem"]["constraints_full"] = "unexpected field"
        elif len(calls) == 2:
            draft["reference_solution"] = 'raise RuntimeError("repair_me")' if language == "python" else 'int main(){ repair_me; }'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks(json.dumps(draft)))

    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client:
            await login(client)
            await client.put("/api/ai/model-config", json=CONFIG)
            tid = (await client.post("/api/ai/problem-tasks/", json={"requirement": "生成一道整数相加题目"})).json()["data"]["task_id"]
            result = await wait_task(client, tid)
            assert result["status"] == "success", result
            assert len(calls) == 3
            assert "constraints_full" in calls[1]["messages"][-1]["content"]
            assert "repair_me" in calls[2]["messages"][-1]["content"]
            assert result["attempt"] == 3 and "max_attempts" not in result
            assert result["usage"]["total_tokens"] == 600
            assert result["result"]["reference_language"] == language


@pytest.mark.parametrize("defect", ["wrong_expected", "wrong_solution_and_expected"])
async def test_independent_checks_repair_data_without_trusting_reference(tmp_path, defect):
    generation, verification = [], []
    async def handler(request):
        messages = json.loads(request.content)["messages"]
        if response := verification_response(request):
            verification.append(messages)
            return response
        generation.append(messages)
        draft = json.loads(json.dumps(DRAFT))
        if len(generation) == 1:
            for case in draft["problem"]["samples"] + draft["problem"]["testcases"]:
                case["output"] = "-999\n"
            if defect == "wrong_solution_and_expected":
                draft["reference_solution"] = "print(-999)"
            body = draft
        else:
            body = {"reference_solution": DRAFT["reference_solution"]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks(json.dumps(body)))

    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client:
            await login(client)
            await client.put("/api/ai/model-config", json=CONFIG)
            tid = (await client.post("/api/ai/problem-tasks/", json={"requirement": "设计整数相加的练习题"})).json()["data"]["task_id"]
            task = await wait_task(client, tid)
            assert task["status"] == "success", task
            result = task["result"]
            assert result["validation"]["independent_passed"]
            assert result["validation"]["input_validation_passed"]
            assert result["problem"]["testcases"] == DRAFT["problem"]["testcases"]
            assert len(generation) == (1 if defect == "wrong_expected" else 2)
            if defect == "wrong_solution_and_expected":
                assert "两份解答不一致" in generation[1][-1]["content"]
            for messages in verification:
                payload = json.loads(messages[1]["content"])
                assert "reference_solution" not in payload and "testcases" not in payload
                assert "-999" not in messages[1]["content"]


async def test_ai_repairs_input_via_generator(tmp_path):
    generation, verification = [], []
    async def handler(request):
        messages = json.loads(request.content)["messages"]
        if response := verification_response(request):
            verification.append(messages)
            return response
        generation.append(messages)
        draft = json.loads(json.dumps(DRAFT))
        if len(generation) == 1:
            draft["problem"]["testcases"][0]["input"] += "777\n"
            body = draft
        elif "测试数据构造" in messages[0]["content"]:
            # The model answers the generator prompt with a script that fixes the invalid case.
            body = {"case_generator": "import json\nprint(json.dumps({'case_updates': [{'collection': 'testcases', 'index': 1, 'input': '0 2\\n'}]}))",
                    "explanation": "修正多余的数字"}
        else:
            body = draft
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks(json.dumps(body)))

    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client:
            await login(client)
            await client.put("/api/ai/model-config", json=CONFIG)
            tid = (await client.post("/api/ai/problem-tasks/", json={"requirement": "设计整数相加的练习题"})).json()["data"]["task_id"]
            task = await wait_task(client, tid)
            assert task["status"] == "success", task
            result = task["result"]
            assert result["validation"]["input_validation_passed"]
            assert result["problem"]["testcases"] == DRAFT["problem"]["testcases"]
            assert len(generation) == 2
            assert "测试数据构造" in generation[1][0]["content"]


async def test_cancel_during_independent_generation(tmp_path):
    independent = Chunks(json.dumps(VERIFICATION) * 30, slow=True)
    async def handler(request):
        if verification_response(request):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=independent)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks(json.dumps(DRAFT)))
    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client:
            await login(client)
            await client.put("/api/ai/model-config", json=CONFIG)
            tid = (await client.post("/api/ai/problem-tasks/", json={"requirement": "测试独立校验阶段的中断"})).json()["data"]["task_id"]
            for _ in range(1000):
                task = (await client.get(f"/api/ai/problem-tasks/{tid}")).json()["data"]
                if any("独立审核" in event for event in task["events"]):
                    break
                await asyncio.sleep(.01)
            else:
                pytest.fail("Independent verification did not start")
            await asyncio.sleep(.1)
            assert (await client.put(f"/api/ai/problem-tasks/{tid}/cancel")).status_code == 200
            assert independent.closed
            task = await wait_task(client, tid)
            assert task["status"] == "cancelled" and task["result"] is None


async def test_ai_continues_until_cancelled(tmp_path):
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks("invalid json"))
    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client:
            await login(client)
            await client.put("/api/ai/model-config", json=CONFIG)
            tid = (await client.post("/api/ai/problem-tasks/", json={"requirement": "持续返回非法内容，直到用户主动中断"})).json()["data"]["task_id"]
            for _ in range(1000):
                if len(calls) >= 10:
                    break
                await asyncio.sleep(.01)
            assert len(calls) >= 10
            assert (await client.put(f"/api/ai/problem-tasks/{tid}/cancel")).status_code == 200
            count = len(calls)
            await asyncio.sleep(.05)
            assert len(calls) == count
            task = await wait_task(client, tid)
            assert task["status"] == "cancelled"


async def test_ai_repairs_nested_reference_solution(tmp_path):
    calls = []
    async def handler(request):
        if response := verification_response(request):
            return response
        calls.append(request)
        draft = json.loads(json.dumps(DRAFT))
        if len(calls) == 1:
            # The model nests the solution inside `problem`; the repair lifts it back to the top level.
            draft["problem"]["reference_solution"] = draft.pop("reference_solution")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks(json.dumps(draft)))
    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client:
            await login(client)
            await client.put("/api/ai/model-config", json=CONFIG)
            tid = (await client.post("/api/ai/problem-tasks/", json={"requirement": "生成整数相加题目"})).json()["data"]["task_id"]
            task = await wait_task(client, tid)
            assert task["status"] == "success", task
            assert task["result"]["reference_solution"] == DRAFT["reference_solution"]
            assert task["attempt"] == 1
