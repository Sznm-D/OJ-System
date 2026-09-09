"""Configurable streaming model client, cancellable authoring and reference-solution checks."""
import asyncio
import copy
import ipaddress
import json
import math
import os
import socket
import time
import uuid
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException

from .models import AIDraft, Language


SYSTEM_PROMPT = """你是程序设计训练课程的出题教师。根据用户的知识点、难度、约束设计一道原创 OJ 题。
只返回 JSON 对象，严格遵循结构：
{"problem":{"id":"Pxxxx","title":"...","description":"...","input_description":"...",
"output_description":"...","constraints":"...","samples":[{"input":"...","output":"..."}],
"testcases":[{"input":"...","output":"..."}],"tags":["..."],"time_limit":3,"memory_limit":128,
"difficulty":"...","hint":"...","source":"AI 辅助命题","author":"..."},
"reference_solution":"完整可执行的 Python 3 标准解答", "coverage":["每组测试覆盖什么边界/复杂度"],
"explanation":"知识点、预期算法、时间空间复杂度及与命题要求的对应关系"}。
至少生成 8 组不同输入的测试点，覆盖最小/最大/负数或零（若适用）/重复值/一般情况/复杂度差异。
输出必须由参考算法精确计算。用真实输入数据，不得使用省略号、生成器占位符或未展开的表达式。
参考解答从标准输入读取，写标准输出，不使用网络、文件、第三方库，不输出提示文字。
题意必须自洽，边界明确；题目 id 只包含字母、数字、下划线、连字符；不得生成 public_cases 等额外字段。
如果参考已有题，按本次用户要求修改并保留题目 id。"""


def extract_json_object(text):
    """Extract the first complete JSON object from a model response."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if "```" in cleaned:
            cleaned = cleaned.rsplit("```", 1)[0].strip()
    decoder = json.JSONDecoder()
    for index, character in enumerate(cleaned):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and not cleaned[index + end:].strip():
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
    raise ValueError("返回内容不包含完整 JSON 对象")


class AIService:
    def __init__(self, store, judge, testing=False, transport=None):
        self.store, self.judge = store, judge
        self.testing, self.transport = testing, transport
        self.tasks = {}

    async def initialize(self):
        key_file = self.store.root / "model-secret.key"
        key = os.getenv("OJ_ENCRYPTION_KEY")
        if not key:
            if not key_file.exists():
                key_file.write_bytes(Fernet.generate_key())
                key_file.chmod(0o600)
            key = key_file.read_bytes()
        self.cipher = Fernet(key)
        for task in await self.store.all("ai_tasks"):
            if task["status"] in {"pending", "running"}:
                task.update(status="error", progress="服务重启，任务未完成，请重新发起。")
                await self.store.put("ai_tasks", task["task_id"], task)

    async def validate_provider(self, value):
        parsed = urlparse(value)
        if self.testing:
            return
        if os.getenv("OJ_AI_ALLOW_LOCAL") == "1":
            return
        if parsed.scheme != "https":
            raise HTTPException(400, "模型服务默认要求 HTTPS；本地模型需显式设置 OJ_AI_ALLOW_LOCAL=1")
        try:
            addresses = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError:
            raise HTTPException(400, "模型服务地址无法解析") from None
        if any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
            raise HTTPException(400, "模型服务不能指向内网或保留地址")

    async def save_config(self, uid, body):
        await self.validate_provider(body.provider_url)
        config = body.model_dump()
        config["api_key_encrypted"] = self.cipher.encrypt(config.pop("api_key").encode()).decode()
        async with self.store.lock:
            await self.store.put("ai_configs", uid, config)
        return await self.public_config(uid)

    async def public_config(self, uid):
        config = await self.store.get("ai_configs", uid)
        if not config:
            return None
        return {k: v for k, v in config.items() if k != "api_key_encrypted"} | {"api_key_configured": True}

    async def persist(self, task):
        async with self.store.lock:
            await self.store.put("ai_tasks", task["task_id"], task)

    async def start(self, user, body):
        config = await self.store.get("ai_configs", user["user_id"])
        if not config:
            raise HTTPException(400, "请先配置模型服务")
        problem = None
        if body.problem_id:
            problem = await self.store.get("problems", body.problem_id)
            if not problem:
                raise HTTPException(404, "参考题目不存在")
        async with self.store.lock:
            active = []
            for task in await self.store.all("ai_tasks"):
                if task["user_id"] != user["user_id"] or task["status"] not in {"pending", "running"}:
                    continue
                if task["task_id"] in self.tasks and not self.tasks[task["task_id"]].done():
                    active.append(task)
                else:
                    task.update(status="error", progress="任务句柄已失效，请重新发起。", result=None)
                    await self.store.put("ai_tasks", task["task_id"], task)
            if active:
                raise HTTPException(409, "已有命题任务正在执行")
            tid = uuid.uuid4().hex
            row = {"task_id": tid, "user_id": user["user_id"], "status": "pending", "progress": "等待开始",
                   "events": ["已收到命题需求"], "result": None, "requirement": body.requirement,
                   "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": 0,
                             "currency": config["currency"], "basis": "尚未调用模型", "input_price": config["input_price"],
                             "output_price": config["output_price"], "price_unit": config["price_unit"]}}
            await self.store.put("ai_tasks", tid, row)
            handle = asyncio.create_task(self.execute(row, config, problem), name=f"ai-{tid}")
            self.tasks[tid] = handle
            handle.add_done_callback(lambda t: self.tasks.pop(tid, None) if self.tasks.get(tid) is t else None)
        return {"task_id": tid, "status": "pending"}

    async def get_task(self, user, tid):
        task = await self.store.get("ai_tasks", tid)
        if task is None:
            raise HTTPException(404, "命题任务不存在")
        if task["user_id"] != user["user_id"] and user["role"] != "admin":
            raise HTTPException(403, "无权查看此命题任务")
        return {k: v for k, v in task.items() if k != "user_id"}

    async def cancel(self, user, tid):
        task = await self.get_task(user, tid)
        if task["status"] not in {"pending", "running"}:
            raise HTTPException(409, "任务已经结束")
        handle = self.tasks.get(tid)
        if handle:
            handle.cancel()
            await asyncio.gather(handle, return_exceptions=True)
        # A coroutine cancelled before its first instruction has no finally block to run.
        row = await self.store.get("ai_tasks", tid)
        row.update(status="cancelled", progress="任务已中断", result=None)
        await self.persist(row)
        return {"task_id": tid, "status": "cancelled"}

    async def progress(self, task, message):
        task["progress"] = message
        task["events"] = (task["events"] + [message])[-30:]
        await self.persist(task)

    def account(self, task, config, base, prompt, output, reported=None):
        complete = reported and reported.get("prompt_tokens") is not None and reported.get("completion_tokens") is not None
        incoming = max(0, int(reported["prompt_tokens"])) if complete else math.ceil(len(prompt) / 4)
        outgoing = max(0, int(reported["completion_tokens"])) if complete else math.ceil(len(output) / 4)
        usage = task["usage"]
        usage["input_tokens"], usage["output_tokens"] = base[0] + incoming, base[1] + outgoing
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        usage["cost"] = round((usage["input_tokens"] * config["input_price"] + usage["output_tokens"] * config["output_price"]) / config["price_unit"], 8)
        usage["basis"] = "提供商返回用量" if complete and base[2] != "estimated" else "含估算：字符数 ÷ 4 向上取整；中文及中断任务可能有较大偏差"
        usage["estimated"] = not complete or base[2] == "estimated"

    async def completion(self, task, config, messages):
        await self.validate_provider(config["provider_url"])
        key = self.cipher.decrypt(config["api_key_encrypted"].encode()).decode()
        prompt = json.dumps(messages, ensure_ascii=False)
        base = (task["usage"]["input_tokens"], task["usage"]["output_tokens"], "estimated" if task["usage"].get("estimated") else "exact")
        output, reported, updated = "", None, 0
        self.account(task, config, base, prompt, output)
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(90, connect=15), follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", config["provider_url"] + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}"}, json={"model": config["model"], "messages": messages,
                        "stream": True, "stream_options": {"include_usage": True},
                        "thinking": {"type": "disabled"}}) as stream:
                    stream.raise_for_status()
                    if "text/event-stream" not in stream.headers.get("content-type", ""):
                        raise ValueError("模型提供商未返回流式响应")
                    async for line in stream.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        if chunk.get("error"):
                            raise ValueError("模型返回错误")
                        if chunk.get("usage"):
                            reported = chunk["usage"]
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta", {})
                            content = delta.get("content")
                            if isinstance(content, str):
                                output += content
                            reasoning = delta.get("reasoning_content")
                            if isinstance(reasoning, str) and reasoning:
                                task["reasoning_chars"] = task.get("reasoning_chars", 0) + len(reasoning)
                        if len(output) > 1_000_000:
                            raise ValueError("模型输出过大")
                        self.account(task, config, base, prompt, output, reported)
                        if time.monotonic() - updated > 0.25:
                            reasoning_chars = task.get("reasoning_chars", 0)
                            task["progress"] = f"正在生成题面与测试数据 · 已接收 {len(output):,} 字符" + (f" · 思考 {reasoning_chars:,} 字符" if reasoning_chars else "")
                            await self.persist(task)
                            updated = time.monotonic()
        finally:
            self.account(task, config, base, prompt, output, reported)
            await self.persist(task)
        if not output.strip():
            raise ValueError("模型未返回可解析的题目正文，请确认模型名称和 Chat Completions 流式接口配置正确")
        # A malicious upstream should not be able to echo the secret into a saved draft.
        if key and key in output:
            raise ValueError("模型输出包含敏感配置")
        return output

    async def validate_draft(self, task, draft):
        problem = draft.problem.model_dump()
        inputs = [case["input"] for case in problem["testcases"]]
        if len(set(inputs)) < 8:
            return "至少需要 8 组不同输入的测试点"
        await self.progress(task, "检查样例、边界测试及参考解答")
        checks = copy.deepcopy(problem)
        checks["testcases"] = checks["samples"] + checks["testcases"]
        async def case_progress(i, total):
            task["progress"] = f"验证参考解答 · {i} / {total}"
            await self.persist(task)
        async with self.judge.gate:
            verdict = await self.judge.runner.judge(checks, Language(name="python", file_ext=".py", run_cmd="python3 {src}").model_dump(), draft.reference_solution, case_progress)
        bad = [{"id": c["id"], "result": c["result"]} for c in verdict["details"] if c["result"] != "AC"]
        if bad:
            return "参考解答未通过样例或测试点（样例在前）：" + json.dumps(bad)
        return None

    async def execute(self, task, config, existing):
        try:
            # Both streaming and verification are bounded, including a provider that never ends.
            await asyncio.wait_for(self.generate(task, config, existing), timeout=300)
        except asyncio.CancelledError:
            task.update(status="cancelled", progress="任务已中断", result=None)
            await self.persist(task)
            raise
        except httpx.HTTPStatusError as error:
            task.update(status="error", progress=f"模型服务请求失败（HTTP {error.response.status_code}），请检查配置或额度。")
            await self.persist(task)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            task.update(status="error", progress="命题任务超时，请简化要求或检查模型服务。")
            await self.persist(task)
        except ValueError as error:
            task.update(status="error", progress=f"模型响应无法使用：{error}")
            await self.persist(task)
        except Exception:
            task.update(status="error", progress="未能生成通过校验的题目，请检查模型配置、执行器或调整命题要求。")
            await self.persist(task)

    async def generate(self, task, config, existing):
        task["status"] = "running"
        await self.progress(task, "正在分析知识点、难度与命题要求")
        content = task["requirement"]
        if existing:
            content += "\n参考题目：\n" + json.dumps(existing, ensure_ascii=False)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
        for attempt in range(2):
            output = await self.completion(task, config, messages)
            error = None
            try:
                draft = AIDraft.model_validate_json(extract_json_object(output))
                if existing and draft.problem.id != existing["id"]:
                    error = "修改已有题目必须保留原 id"
                else:
                    error = await self.validate_draft(task, draft)
            except (ValueError, TypeError) as exc:
                error = f"返回内容不符合所要求的完整 JSON 结构或字段约束：{exc}"
            if not error:
                result = draft.model_dump()
                result["validation"] = {"reference_passed": True, "unique_testcases": len(set(c.input for c in draft.problem.testcases)),
                                        "note": "参考解答已通过全部样例和测试点；知识点匹配、独立正确性及复杂度区分仍需出题人审阅。"}
                task.update(status="success", result=result)
                await self.progress(task, "题目已生成并通过参考解答校验，可送入编辑器审阅")
                return
            await self.progress(task, f"校验发现问题：{error}" + ("，正在修订" if attempt == 0 else ""))
            messages += [{"role": "assistant", "content": output}, {"role": "user", "content": "修正以下问题并重新返回完整 JSON：" + error}]
        task.update(status="error", progress="两轮生成后仍未通过校验，请调整要求后重试。")
        await self.persist(task)

    async def stop(self):
        handles = list(self.tasks.values())
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*handles, return_exceptions=True)
