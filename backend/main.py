"""Course API. Every route and authentication hook is asynchronous."""
import asyncio
import hashlib
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .judge import JudgeQueue, Runner, runtime_available
from .models import AIConfig, AIRequest, Credentials, Language, Problem, RoleChange, Submission, Visibility
from .store import Store


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def response(data=None, msg="success", code=200):
    return JSONResponse({"code": code, "msg": msg, "data": data}, status_code=code)


def fail(code, message):
    raise HTTPException(code, message)


def pagination(request):
    page, size = request.query_params.get("page"), request.query_params.get("page_size")
    if page is not None and size is None:
        fail(400, "page 必须与 page_size 一起使用")
    try:
        if size is None:
            return 0, None
        p, s = int(page or "1"), int(size)
        if p < 1 or s < 1 or s > 500:
            raise ValueError()
        return (p - 1) * s, s
    except ValueError:
        fail(400, "分页参数应为正整数，page_size 最大为 500")


def window(rows, offset, size):
    return rows[offset:] if size is None else rows[offset:offset + size]


def public_submission(row, brief=False):
    keys = ["submission_id", "status"]
    if row["status"] == "success" or not brief:
        keys += ["score", "counts"]
    if not brief:
        keys += ["compile_info", "run_info", "error_info", "problem_id", "language", "created_at"]
    return {key: row.get(key) for key in keys}


def create_app(data_dir=None, testing=False, runner=None, ai_transport=None):
    root = Path(data_dir or os.getenv("OJ_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
    store = Store(root)
    judge = JudgeQueue(store, runner or Runner(os.getenv("OJ_RUNNER", "local")))
    ai = None
    attempts = {}

    async def bootstrap(seed=True):
        if not await store.all("users"):
            password = await asyncio.to_thread(bcrypt.hashpw, b"admintestpassword", bcrypt.gensalt(rounds=4 if testing else 12))
            await store.put("users", "1", {"user_id": "1", "username": "admin", "password_hash": password.decode(), "role": "admin", "join_time": now()[:10]})
        if not await store.all("languages"):
            for config in [Language(name="python", file_ext=".py", run_cmd="python3 {src}"),
                           Language(name="cpp", file_ext=".cpp", compile_cmd="g++ {src} -O2 -std=c++14 -o {exe}", run_cmd="{exe}")]:
                await store.put("languages", config.name, config.model_dump())
        if seed and not await store.get("settings", "initialized"):
            for path in sorted((Path(__file__).resolve().parents[1] / "examples").glob("*.json")):
                problem = Problem.model_validate_json(await asyncio.to_thread(path.read_text, encoding="utf-8"))
                if not await store.get("problems", problem.id):
                    await store.save_problem(problem.model_dump())
        await store.put("settings", "initialized", {"value": True})

    @asynccontextmanager
    async def lifespan(app):
        nonlocal ai
        from .ai import AIService
        await store.open()
        await bootstrap()
        ai = AIService(store, judge, testing=testing, transport=ai_transport)
        app.state.ai = ai
        await ai.initialize()
        for submission in await store.all("submissions"):
            if submission["status"] == "pending":
                judge.start(submission)
        try:
            yield
        finally:
            await ai.stop()
            await judge.stop()
            await store.close()

    app = FastAPI(title="知行 OJ · 课程实验", version="1.0.0", lifespan=lifespan)
    app.state.store, app.state.judge = store, judge

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return response(msg=str(exc.detail), code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not serialize Pydantic's input field: it may contain a password/API key.
        fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors()[:5])
        return response(msg=f"参数缺失或格式错误：{fields}", code=400)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return response(msg="服务器内部错误", code=500)

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        path = request.url.path.rstrip("/")
        public = path == "/api/health" or (request.method == "POST" and path in {"/api/users", "/api/auth/login"})
        if path.startswith("/api") and not public:
            token = request.cookies.get("session_id", "")
            session = await store.get("sessions", hashlib.sha256(token.encode()).hexdigest()) if token else None
            if not session or session["expires_at"] <= time.time():
                return response(msg="请先登录", code=401)
            user = await store.get("users", session["user_id"])
            if not user:
                return response(msg="登录已失效", code=401)
            if user["role"] == "banned":
                return response(msg="账户已被禁用", code=403)
            request.state.user = user
            admin_only = ((path.startswith("/api/problems/") and (request.method == "DELETE" or path.endswith("/log_visibility")))
                          or path.endswith("/rejudge") or path.endswith("/role")
                          or path in {"/api/users/admin", "/api/logs/access", "/api/logs/roles", "/api/reset"}
                          or (path == "/api/users" and request.method == "GET"))
            if admin_only and user["role"] != "admin":
                return response(msg="仅管理员可执行此操作", code=403)
        # Check actual streamed size as well as Content-Length; no unbounded body buffering.
        if path.startswith("/api") and request.method in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 4_000_000:
                    return response(msg="请求内容超过 4 MB", code=400)
            request._body = bytes(body)
        return await call_next(request)

    async def get_required(kind, key, message):
        row = await store.get(kind, key)
        if row is None:
            fail(404, message)
        return row

    async def user_view(user):
        submissions = [s for s in await store.all("submissions") if s["user_id"] == user["user_id"]]
        solved = {s["problem_id"] for s in submissions if s["status"] == "success" and s.get("counts", 0) > 0 and s.get("score") == s.get("counts")}
        return {k: user[k] for k in ("user_id", "username", "role", "join_time")} | {"submit_count": len(submissions), "resolve_count": len(solved)}

    async def add_user(body, role):
        async with store.lock:
            users = await store.all("users")
            if any(u["username"] == body.username for u in users):
                fail(400, "用户名已存在")
            uid = str(max((int(u["user_id"]) for u in users), default=0) + 1)
            hashed = await asyncio.to_thread(bcrypt.hashpw, body.password.encode(), bcrypt.gensalt(rounds=4 if testing else 12))
            user = {"user_id": uid, "username": body.username, "password_hash": hashed.decode(), "role": role, "join_time": now()[:10]}
            await store.put("users", uid, user)
        return await user_view(user)

    async def submission_for(request, sid, allow_public_log=False):
        row = await get_required("submissions", sid, "评测不存在")
        user = request.state.user
        allowed = user["role"] == "admin" or row["user_id"] == user["user_id"]
        if allow_public_log:
            visibility = await store.get("visibility", row["problem_id"]) or {}
            allowed = allowed or visibility.get("public_cases", False)
            async with store.lock:
                await store.put("audit", uuid.uuid4().hex, {"user_id": user["user_id"], "problem_id": row["problem_id"],
                                "submission_id": sid, "action": "view_logs", "time": now(), "status": "200" if allowed else "403"})
        if not allowed:
            fail(403, "无权查看此评测")
        return row

    @app.get("/api/health")
    async def health():
        return response(runtime_available(judge.runner.mode))

    @app.post("/api/users/")
    async def register(body: Credentials):
        return response(await add_user(body, "user"), "register success")

    @app.post("/api/users/admin")
    async def create_admin(body: Credentials):
        user = await add_user(body, "admin")
        return response({"user_id": user["user_id"], "username": user["username"]})

    @app.post("/api/auth/login")
    async def login(request: Request, body: Credentials):
        address = request.client.host if request.client else "unknown"
        stamps = [stamp for stamp in attempts.get(address, []) if stamp > time.time() - 60]
        if len(stamps) >= 20:
            return response(msg="登录尝试过于频繁，请稍后再试", code=429)
        attempts[address] = stamps + [time.time()]
        user = next((u for u in await store.all("users") if u["username"] == body.username), None)
        if not user or not await asyncio.to_thread(bcrypt.checkpw, body.password.encode(), user["password_hash"].encode()):
            fail(401, "用户名或密码错误")
        if user["role"] == "banned":
            fail(403, "账户已被禁用")
        token = secrets.token_urlsafe(32)
        async with store.lock:
            old = request.cookies.get("session_id")
            if old:
                await store.delete("sessions", hashlib.sha256(old.encode()).hexdigest())
            await store.put("sessions", hashlib.sha256(token.encode()).hexdigest(), {"user_id": user["user_id"], "expires_at": time.time() + 86400})
        result = response({k: user[k] for k in ("user_id", "username", "role")}, "login success")
        result.set_cookie("session_id", token, max_age=86400, httponly=True, samesite="strict", secure=os.getenv("OJ_COOKIE_SECURE") == "1")
        return result

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        async with store.lock:
            await store.delete("sessions", hashlib.sha256(request.cookies["session_id"].encode()).hexdigest())
        result = response(msg="logout success")
        result.delete_cookie("session_id")
        return result

    @app.get("/api/auth/me")
    async def current_user(request: Request):
        return response(await user_view(request.state.user))

    @app.get("/api/users/")
    async def list_users(request: Request):
        offset, size = pagination(request)
        users = await store.all("users")
        query, role = request.query_params.get("username"), request.query_params.get("role")
        if role and role not in {"user", "admin", "banned"}:
            fail(400, "角色无效")
        users = [u for u in users if (not query or query in u["username"]) and (not role or u["role"] == role)]
        return response({"total": len(users), "users": [await user_view(u) for u in window(users, offset, size)]})

    @app.get("/api/users/{user_id}")
    async def get_user(request: Request, user_id: str):
        if request.state.user["role"] != "admin" and request.state.user["user_id"] != user_id:
            fail(403, "只能查询自己的用户信息")
        return response(await user_view(await get_required("users", user_id, "用户不存在")))

    @app.put("/api/users/{user_id}/role")
    async def update_role(request: Request, user_id: str, body: RoleChange):
        async with store.lock:
            user = await get_required("users", user_id, "用户不存在")
            if user["role"] == "admin" and body.role != "admin" and sum(u["role"] == "admin" for u in await store.all("users")) == 1:
                fail(409, "必须保留至少一个管理员")
            old_role = user["role"]
            user["role"] = body.role
            await store.put("users", user_id, user)
            await store.put("role_audit", uuid.uuid4().hex, {"operator_id": request.state.user["user_id"], "user_id": user_id, "old_role": old_role, "role": body.role, "time": now()})
        return response({"user_id": user_id, "role": body.role}, "role updated")

    @app.get("/api/problems/")
    async def list_problems():
        return response([{k: p[k] for k in ("id", "title")} for p in await store.all("problems")])

    @app.post("/api/problems/")
    async def add_problem(body: Problem):
        async with store.lock:
            if await store.get("problems", body.id):
                fail(409, "题目 id 已存在")
            await store.save_problem(body.model_dump())
        return response({"id": body.id}, "add success")

    @app.get("/api/problems/{problem_id}")
    async def get_problem(problem_id: str):
        problem = await get_required("problems", problem_id, "题目不存在")
        # The course API explicitly returns the complete problem configuration.
        return response(problem | {"time_limit": problem.get("time_limit") or 3, "memory_limit": problem.get("memory_limit") or 128})

    @app.put("/api/problems/{problem_id}")
    async def edit_problem(problem_id: str, body: Problem):
        if problem_id != body.id:
            fail(400, "请求体 id 必须与路径一致")
        async with store.lock:
            await get_required("problems", problem_id, "题目不存在")
            await store.save_problem(body.model_dump())
        return response({"id": problem_id}, "update success")

    @app.delete("/api/problems/{problem_id}")
    async def delete_problem(problem_id: str):
        async with store.lock:
            await get_required("problems", problem_id, "题目不存在")
            await store.delete_problem(problem_id)
            await store.delete("visibility", problem_id)
        return response({"id": problem_id}, "delete success")

    @app.get("/api/languages/")
    async def languages():
        return response({"name": [lang["name"] for lang in await store.all("languages")]})

    @app.post("/api/languages/")
    async def register_language(body: Language):
        if body.run_cmd == "{exe}" and not body.compile_cmd:
            fail(400, "运行 {exe} 时必须提供编译命令")
        async with store.lock:
            if await store.get("languages", body.name):
                fail(409, "语言名称已存在")
            await store.put("languages", body.name, body.model_dump())
        return response({"name": body.name}, "language registered")

    @app.post("/api/submissions/")
    async def submit(request: Request, body: Submission):
        uid = request.state.user["user_id"]
        async with store.lock:
            history = await store.all("submissions")
            if sum(s["user_id"] == uid and s["submitted_at"] > time.time() - 60 for s in history) >= 3:
                fail(429, "每分钟最多提交 3 次，请稍后再试")
            problem = await get_required("problems", body.problem_id, "题目不存在")
            language = await get_required("languages", body.language, "语言不存在")
            sid = uuid.uuid4().hex
            row = {"submission_id": sid, "user_id": uid, **body.model_dump(), "status": "pending", "created_at": now(),
                   "submitted_at": time.time(), "problem_snapshot": problem, "language_snapshot": language,
                   "score": None, "counts": len(problem["testcases"]) * 10, "details": []}
            await store.put("submissions", sid, row)
            judge.start(row)
        return response({"submission_id": sid, "status": "pending"})

    @app.get("/api/submissions/")
    async def list_submissions(request: Request):
        uid, pid, status = (request.query_params.get(k) for k in ("user_id", "problem_id", "status"))
        if uid and uid != request.state.user["user_id"] and request.state.user["role"] != "admin":
            fail(403, "只能查询自己的提交")
        offset, size = pagination(request)
        if not uid and not pid:
            fail(400, "至少提供 user_id 或 problem_id")
        if status and status not in {"pending", "success", "error"}:
            fail(400, "status 无效")
        if request.state.user["role"] != "admin":
            uid = request.state.user["user_id"]
        rows = [s for s in reversed(await store.all("submissions")) if (not uid or uid == s["user_id"]) and (not pid or pid == s["problem_id"]) and (not status or status == s["status"])]
        return response({"total": len(rows), "submissions": [public_submission(s, brief=True) for s in window(rows, offset, size)]})

    @app.get("/api/submissions/{submission_id}")
    async def get_submission(request: Request, submission_id: str):
        return response(public_submission(await submission_for(request, submission_id)))

    @app.put("/api/submissions/{submission_id}/rejudge")
    async def rejudge(submission_id: str):
        async with store.lock:
            row = await get_required("submissions", submission_id, "评测不存在")
            if row["status"] == "pending":
                fail(409, "评测正在进行中")
            problem = await get_required("problems", row["problem_id"], "题目不存在")
            language = await get_required("languages", row["language"], "语言不存在")
            row.update(status="pending", score=None, details=[], compile_info=None, run_info=None, error_info="", counts=len(problem["testcases"]) * 10,
                       problem_snapshot=problem, language_snapshot=language)
            await store.put("submissions", submission_id, row)
            judge.start(row)
        return response({"submission_id": submission_id, "status": "pending"}, "rejudge started")

    @app.get("/api/submissions/{submission_id}/log")
    async def submission_log(request: Request, submission_id: str):
        row = await submission_for(request, submission_id, allow_public_log=True)
        visibility = await store.get("visibility", row["problem_id"]) or {}
        result = {"score": row.get("score"), "counts": row.get("counts")}
        if request.state.user["role"] == "admin" or visibility.get("public_cases"):
            result["details"] = [{k: c[k] for k in ("id", "result", "time", "memory")} for c in row.get("details", [])]
        return response(result)

    @app.get("/api/problems/{problem_id}/log_visibility")
    async def get_visibility(problem_id: str):
        await get_required("problems", problem_id, "题目不存在")
        return response({"problem_id": problem_id, "public_cases": (await store.get("visibility", problem_id) or {}).get("public_cases", False)})

    @app.put("/api/problems/{problem_id}/log_visibility")
    async def log_visibility(problem_id: str, body: Visibility):
        async with store.lock:
            await get_required("problems", problem_id, "题目不存在")
            await store.put("visibility", problem_id, body.model_dump())
        return response({"problem_id": problem_id, **body.model_dump()}, "log visibility updated")

    @app.get("/api/logs/access/")
    async def access_logs(request: Request):
        offset, size = pagination(request)
        uid, pid = request.query_params.get("user_id"), request.query_params.get("problem_id")
        rows = [r for r in reversed(await store.all("audit")) if (not uid or uid == r["user_id"]) and (not pid or pid == r["problem_id"])]
        return response(window(rows, offset, size))

    @app.get("/api/logs/roles/")
    async def role_logs(request: Request):
        offset, size = pagination(request)
        return response(window(list(reversed(await store.all("role_audit"))), offset, size))

    @app.put("/api/ai/model-config")
    async def save_model(request: Request, body: AIConfig):
        return response(await ai.save_config(request.state.user["user_id"], body), "model config updated")

    @app.get("/api/ai/model-config")
    async def get_model(request: Request):
        return response(await ai.public_config(request.state.user["user_id"]))

    @app.post("/api/ai/problem-tasks/")
    async def create_ai_task(request: Request, body: AIRequest):
        return response(await ai.start(request.state.user, body), "task created")

    @app.get("/api/ai/problem-tasks/{task_id}")
    async def get_ai_task(request: Request, task_id: str):
        return response(await ai.get_task(request.state.user, task_id))

    @app.put("/api/ai/problem-tasks/{task_id}/cancel")
    async def cancel_ai_task(request: Request, task_id: str):
        return response(await ai.cancel(request.state.user, task_id), "task cancelled")

    @app.post("/api/reset/")
    async def reset():
        await ai.stop()
        await judge.stop()
        async with store.lock:
            await store.clear()
            await bootstrap(seed=False)
        attempts.clear()
        result = response(msg="system reset successfully")
        result.delete_cookie("session_id")
        return result

    return app


app = create_app()
