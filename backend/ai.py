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

from .models import Language
from .authoring_checks import check_answers, diagnostic
from .authoring_repairs import merge_draft, run_case_generator, repair_operation_count, operation_input


SYSTEM_PROMPT = """你是程序设计训练课程的出题教师。根据用户的知识点、难度、约束设计一道原创 OJ 题。
只返回 JSON 对象，严格遵循结构：
{"problem":{"id":"Pxxxx","title":"...","description":"...","input_description":"...",
"output_description":"...","constraints":"...","samples":[{"input":"...","output":"..."}],
"testcases":[{"input":"...","output":"..."}],"tags":["..."],"time_limit":3,"memory_limit":128,
"difficulty":"...","hint":"...","source":"AI 辅助命题","author":"..."},
"reference_language":"python 或 cpp，按用户要求选择，默认 python",
"reference_solution":"对应语言的完整可执行标准解答，cpp 使用 C++14", "coverage":["每组测试覆盖什么边界/复杂度"],
"explanation":"知识点、预期算法、时间空间复杂度及与命题要求的对应关系"}。
至少生成 8 组不同输入的测试点，覆盖最小/最大/负数或零（若适用）/重复值/一般情况/复杂度差异。
输出必须由参考算法精确计算。用真实输入数据，不得使用省略号、生成器占位符或未展开的表达式。
参考解答从标准输入读取，写标准输出，不使用网络、文件、第三方库，不输出提示文字。
先推导清楚算法再编写代码与数据；C++ 严格使用 C++14，不使用结构化绑定等 C++17 语法。
系统会实际编译参考解答，并另行生成不查看参考代码的输入校验器和独立解答。
务必核对 n/m/q 与实际数据条数、输入消费完整性、操作合法性；标准输出将由两份解答交叉核对。
题意必须自洽，边界明确；题目 id 只包含字母、数字、下划线、连字符；不得生成 public_cases、constraints_full 等额外字段，完整数据范围放在 constraints 中。
如果参考已有题，按本次用户要求修改并保留题目 id。"""


GENERATOR_PROMPT = """你是 OJ 测试数据构造器。根据题面与输入规范，以及一组被判为“非法”的测试点，编写一个 Python 3 程序。
返回 JSON {"case_generator":"完整 Python 代码"}。
程序运行时只 print 一个 JSON 对象，不打印其他内容：
{"case_updates": [{"collection": "samples"或"testcases", "index": 从 1 开始的位置, "input": "修正后的完整输入"}]}
只修正列表中给出的非法测试点，其余位置不得改动。修正后的 input 必须严格符合输入规范：首行声明的 n/m/q 与实际条数一致、操作合法、输入被完全消费、无多余或缺失 token。
优先用循环/推导式按声明条数程序化构造输入，避免手写计数错误；可用 assert 自检条数。
只使用标准库，不访问网络或文件。
对于 ADD id u v / DEL id / ASK u v / COUNT 动态道路题，优先直接返回结构化操作：
{"operation_updates":[{"collection":"testcases","index":1,"n":2,"operations":[["ADD",1,1,2],["ADD",2,1,2],["COUNT"],["DEL",1],["ASK",1,2]]}]}。
后端会自动计算 q 并验证操作，平行边必须使用不同编号；不得手写 q，不得改变原题语义。"""


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
                   "events": ["已收到命题需求"], "result": None, "requirement": body.requirement, "problem_id": body.problem_id,
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
        previous_usage = copy.deepcopy(task["usage"])
        connection_failed = False
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
        except httpx.ConnectError:
            connection_failed = True
            raise
        finally:
            if connection_failed:
                task["usage"] = previous_usage
                task["usage"]["basis"] = "本轮连接未建立，未计入 Token 和费用；历史轮次用量保留"
            else:
                self.account(task, config, base, prompt, output, reported)
            await self.persist(task)
        if not output.strip():
            raise ValueError("模型未返回可解析的题目正文，请确认模型名称和 Chat Completions 流式接口配置正确")
        # A malicious upstream should not be able to echo the secret into a saved draft.
        if key and key in output:
            raise ValueError("模型输出包含敏感配置")
        return output

    async def validate_draft(self, task, draft, config, cache=None):
        problem = draft.problem.model_dump()
        inputs = [case["input"] for case in problem["testcases"]]
        if len(set(inputs)) < 8:
            return "至少需要 8 组不同输入的测试点", None
        await self.progress(task, "检查样例、边界测试及参考解答")
        checks = copy.deepcopy(problem)
        checks["testcases"] = checks["samples"] + checks["testcases"]
        async def case_progress(i, total):
            task["progress"] = f"验证参考解答 · {i} / {total}"
            await self.persist(task)
        async with self.judge.gate:
            language = (Language(name="cpp", file_ext=".cpp", compile_cmd="g++ {src} -O2 -std=c++14 -o {exe}", run_cmd="{exe}")
                        if draft.reference_language == "cpp" else Language(name="python", file_ext=".py", run_cmd="python3 {src}"))
            verdict = await self.judge.runner.judge(checks, language.model_dump(), draft.reference_solution, case_progress, capture_output=True)
        error = diagnostic(verdict, checks["testcases"], "参考解答")
        if error and (verdict.get("status") != "success" or
                      (verdict.get("compile_info") and verdict["compile_info"]["result"] != "success")):
            return error, None
        error, verification, invalid_targets = await check_answers(self, task, draft, config, verdict, cache)
        task["verification"] = verification
        return error, invalid_targets

    async def try_repair_inputs(self, task, draft, config, invalid_targets):
        """Run the model-written case generator and apply narrow input fixes; None if unavailable."""
        problem = draft.problem.model_dump()
        automatic = []
        for target in invalid_targets:
            original = problem[target["collection"]][target["index"] - 1]["input"]
            fixed = repair_operation_count(original, target["reason"])
            if fixed is not None:
                automatic.append({"collection": target["collection"], "index": target["index"], "input": fixed})
        if automatic:
            await self.progress(task, f"按真实操作序列自动修正 {len(automatic)} 个输入的条数或首行格式，保留所有操作并重新校验")
            try:
                return merge_draft(draft.model_dump(), {"case_updates": automatic}, targets=invalid_targets)
            except ValueError as error:
                task["input_repair_error"] = str(error)[:3000]
        spec = {
            "title": problem["title"],
            "description": problem["description"],
            "input_description": problem["input_description"],
            "constraints": problem["constraints"],
            "invalid_cases": [
                {"collection": t["collection"], "index": t["index"], "reason": t["reason"],
                 "current_input": problem[t["collection"]][t["index"] - 1]["input"]}
                for t in invalid_targets
            ],
            "previous_repair_error": task.get("input_repair_error"),
        }
        await self.progress(task, "输入非法，调用数据构造脚本自动修复")
        try:
            output = await self.completion(task, config, [
                {"role": "system", "content": GENERATOR_PROMPT},
                {"role": "user", "content": json.dumps(spec, ensure_ascii=False)},
            ])
            generator = json.loads(extract_json_object(output))
            if not isinstance(generator, dict):
                raise ValueError("数据构造器必须返回 JSON 对象")
            if "operation_updates" in generator:
                if set(generator) != {"operation_updates"} or not isinstance(generator["operation_updates"], list):
                    raise ValueError("operation_updates 必须为列表")
                patch = {"case_updates": []}
                for item in generator["operation_updates"]:
                    if not isinstance(item, dict) or set(item) != {"collection", "index", "n", "operations"}:
                        raise ValueError("结构化操作字段应为 collection/index/n/operations")
                    patch["case_updates"].append({"collection": item["collection"], "index": item["index"],
                                                  "input": operation_input(item["n"], item["operations"])})
                return merge_draft(draft.model_dump(), patch, targets=invalid_targets)
            code = generator.get("case_generator")
            if not isinstance(code, str) or not code.strip():
                raise ValueError("数据构造器未返回可执行的 Python 代码")
            patch = await run_case_generator(self, task, code)
            return merge_draft(draft.model_dump(), patch, targets=invalid_targets)
        except (ValueError, TypeError) as error:
            task["input_repair_error"] = str(error)[:3000]
            await self.progress(task, "数据修复未通过：" + task["input_repair_error"] + "；下一轮继续定点修复")
            return None

    async def execute(self, task, config, existing):
        try:
            # Per-request and per-program limits remain; no authoring round cap.
            await self.generate(task, config, existing)
        except asyncio.CancelledError:
            task.update(status="cancelled", progress="任务已中断", result=None)
            await self.persist(task)
            raise
        except httpx.HTTPStatusError as error:
            task.update(status="error", progress=f"模型服务请求失败（HTTP {error.response.status_code}），请检查配置或额度。")
            await self.persist(task)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            task.update(status="error", progress="单次模型请求超时，请检查模型服务后重新开始。")
            await self.persist(task)
        except httpx.RequestError as error:
            message = ("无法连接模型服务：连接被系统或网络权限拒绝（WinError 10013）。请在允许联网的环境中启动后端。"
                       if "10013" in str(error) else
                       f"模型服务连接失败（{type(error).__name__}），请检查网络、服务地址及代理配置后重新开始。")
            task.update(status="error", result=None)
            await self.progress(task, message)
        except ValueError as error:
            task.update(status="error", progress=f"模型响应无法使用：{error}")
            await self.persist(task)
        except Exception as error:
            task.update(status="error", result=None)
            await self.progress(task, f"命题过程异常（{type(error).__name__}），请检查模型响应格式或执行器。")

    async def generate(self, task, config, existing):
        task["status"] = "running"
        await self.progress(task, "正在分析知识点、难度与命题要求")
        content = task["requirement"]
        if existing:
            content += "\n参考题目：\n" + json.dumps(existing, ensure_ascii=False)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
        candidate = None
        attempt = 0
        error = None
        pending_targets = None
        verification_cache = {}
        while True:
            attempt += 1
            task.update(attempt=attempt)
            task.pop("verification", None)
            await self.progress(task, f"第 {attempt} 轮：" + ("正在命题" if attempt == 1 else "根据校验错误自动修订题目与参考解答"))
            output = None
            error = None
            try:
                if candidate is not None and task.pop("retry_stage", None) == "verification":
                    await self.progress(task, "保留题目和参考解答，仅修订独立审核程序")
                    draft = merge_draft(None, candidate)
                elif candidate is not None and pending_targets:
                    draft = merge_draft(None, candidate)
                    repaired = await self.try_repair_inputs(task, draft, config, pending_targets)
                    if repaired is None:
                        continue
                    draft = repaired
                else:
                    output = await self.completion(task, config, messages)
                    patch = json.loads(extract_json_object(output))
                    draft = merge_draft(candidate, patch)
                candidate = draft.model_dump()
                pending_targets = None
                if existing and draft.problem.id != existing["id"]:
                    error = "修改已有题目必须保留原 id"
                else:
                    error, invalid_targets = await self.validate_draft(task, draft, config, verification_cache)
                    pending_targets = invalid_targets
                    if error and invalid_targets:
                        repaired = await self.try_repair_inputs(task, draft, config, invalid_targets)
                        if repaired is not None:
                            draft = repaired
                            candidate = draft.model_dump()
                            error, invalid_targets = await self.validate_draft(task, draft, config, verification_cache)
                    pending_targets = invalid_targets
            except (ValueError, TypeError) as exc:
                error = f"返回内容不符合所要求的完整 JSON 结构或字段约束：{exc}"
            if not error:
                result = draft.model_dump()
                result["validation"] = {"reference_passed": True, **task.pop("verification"),
                                        "unique_testcases": len(set(c.input for c in draft.problem.testcases)),
                                        "note": "输入校验通过，参考解答与另行生成的解答在全部样例和测试点上输出一致。这不是数学证明；题意、样例解释及复杂度区分仍需审阅。"}
                task.update(status="success", result=result)
                await self.progress(task, "题目已生成并通过参考解答校验，可送入编辑器审阅")
                return
            await self.progress(task, f"校验发现问题：{error}，正在自动修订")
            # Keep the original requirement and latest failure without accumulating all drafts.
            messages = messages[:2] + [{"role": "assistant", "content": json.dumps(candidate, ensure_ascii=False) if candidate else output}, {"role": "user", "content":
                ("只返回需要修改的字段组成的 JSON 补丁，problem 内只需返回修改的字段，列表有改动时返回完整列表；保留其他正确内容。" if candidate else "重新返回完整 JSON。") +
                "保留原命题知识点、难度和要求；编译错误优先仅修正参考代码；输入条数错误修正输入；两解不一致需重新推导，"
                "不要通过删除失败测试或降低要求规避错误。reference_language 必须匹配代码语言。错误信息：" + error}]

    async def stop(self):
        handles = list(self.tasks.values())
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*handles, return_exceptions=True)
