import asyncio
import hashlib
import inspect
import json
import time

import httpx
import pytest
from fastapi.routing import APIRoute

from backend.main import create_app
from conftest import PROBLEM


async def submit(client, code="a,b=map(int,input().split()); print(a+b)", language="python"):
    result = await client.post("/api/submissions/", json={"problem_id": "test_sum", "language": language, "code": code})
    assert result.status_code == 200, result.text
    return result.json()["data"]["submission_id"]


async def finished(client, sid):
    for _ in range(300):
        result = (await client.get(f"/api/submissions/{sid}")).json()["data"]
        if result["status"] != "pending":
            return result
        await asyncio.sleep(0.02)
    pytest.fail("judge did not finish")


async def test_async_routes_and_error_priority(environment):
    app, admin, alice, bob, guest = environment
    assert all(inspect.iscoroutinefunction(route.endpoint) for route in app.routes if isinstance(route, APIRoute))
    for path in ["/api/problems/", "/api/languages/", "/api/submissions/", "/api/users/", "/api/logs/access/"]:
        result = await guest.get(path)
        assert result.status_code == result.json()["code"] == 401
    assert (await guest.post("/api/problems/", content="not json")).status_code == 401
    assert (await alice.put("/api/users/1/role", json={"role": "invalid"})).status_code == 403
    assert (await admin.post("/api/problems/", json={})).status_code == 400
    assert (await admin.get("/api/no-such-route")).json()["code"] == 404


async def test_problem_crud_defaults_and_disk(environment):
    app, admin, alice, bob, guest = environment
    body = dict(PROBLEM, id="new_one")
    del body["time_limit"]
    del body["memory_limit"]
    assert (await alice.post("/api/problems/", json=body)).status_code == 200
    assert (await alice.post("/api/problems/", json=body)).status_code == 409
    problem = (await bob.get("/api/problems/new_one")).json()["data"]
    assert problem["time_limit"] == 3 and problem["memory_limit"] == 128
    assert problem["hint"] == "" and problem["tags"] == []
    assert (await alice.put("/api/problems/new_one", json=PROBLEM)).status_code == 400
    assert (await alice.put("/api/problems/new_one", json=body | {"title": "更新题面"})).status_code == 200
    disk = json.loads((app.state.store.problem_dir / "new_one.json").read_text(encoding="utf-8"))
    assert disk["title"] == "更新题面"
    assert (await alice.delete("/api/problems/new_one")).status_code == 403
    assert (await admin.delete("/api/problems/new_one")).status_code == 200
    assert not (app.state.store.problem_dir / "new_one.json").exists()
    assert (await admin.get("/api/problems/new_one")).status_code == 404
    assert (await admin.post("/api/problems/", json=body | {"id": "../escape"})).status_code == 400
    assert (await admin.post("/api/problems/", json=body | {"testcases": []})).status_code == 400


async def test_users_sessions_roles_and_bcrypt(environment):
    app, admin, alice, bob, guest = environment
    own = (await alice.get("/api/auth/me")).json()["data"]
    assert own["user_id"] == "2" and "password_hash" not in own
    stored = await app.state.store.get("users", "2")
    assert stored["password_hash"].startswith("$2") and stored["password_hash"] != "password123"
    assert (await alice.get("/api/users/3")).status_code == 403
    assert (await alice.get("/api/users/")).status_code == 403
    assert (await guest.post("/api/users/", json={"username": "alice", "password": "password123"})).status_code == 400
    assert (await admin.put("/api/users/2/role", json={"role": "banned"})).status_code == 200
    assert (await alice.get("/api/problems/")).status_code == 403
    assert (await guest.post("/api/auth/login", json={"username": "alice", "password": "password123"})).status_code == 403
    assert (await admin.put("/api/users/2/role", json={"role": "user"})).status_code == 200
    cookie = alice.cookies.get("session_id")
    assert (await alice.post("/api/auth/logout")).status_code == 200
    assert await app.state.store.get("sessions", hashlib.sha256(cookie.encode()).hexdigest()) is None
    alice.cookies.set("session_id", cookie)
    assert (await alice.get("/api/problems/")).status_code == 401
    assert (await admin.put("/api/users/1/role", json={"role": "user"})).status_code == 409
    assert (await admin.post("/api/users/admin", json={"username": "teacher", "password": "teacher123"})).status_code == 200
    assert (await admin.get("/api/logs/roles/")).json()["data"]


async def test_sessions_expire(environment):
    app, admin, alice, bob, guest = environment
    digest = hashlib.sha256(alice.cookies.get("session_id").encode()).hexdigest()
    await app.state.store.put("sessions", digest, {"user_id": "2", "expires_at": time.time() - 1})
    assert (await alice.get("/api/auth/me")).status_code == 401


async def test_pagination_and_filter_rules(environment):
    app, admin, alice, bob, guest = environment
    assert (await alice.get("/api/submissions/")).status_code == 400
    assert (await alice.get("/api/submissions/", params={"user_id": "3", "page": -1})).status_code == 403
    assert (await admin.get("/api/users/", params={"page": 1})).status_code == 400
    assert (await admin.get("/api/users/", params={"page_size": 1})).json()["data"]["total"] == 3
    assert len((await admin.get("/api/users/", params={"page_size": 1})).json()["data"]["users"]) == 1
    assert (await admin.get("/api/users/", params={"page_size": 0})).status_code == 400
    assert (await admin.get("/api/users/", params={"username": "alice"})).json()["data"]["total"] == 1


async def test_judge_permissions_public_logs_rejudge_and_statistics(environment):
    app, admin, alice, bob, guest = environment
    sid = await submit(alice)
    row = await finished(alice, sid)
    assert row["status"] == "success" and row["score"] == row["counts"] == 10
    assert row["run_info"]["result"] == "AC"
    assert "details" not in row and "code" not in row
    assert (await bob.get(f"/api/submissions/{sid}")).status_code == 403
    assert "details" not in (await alice.get(f"/api/submissions/{sid}/log")).json()["data"]
    assert (await bob.get(f"/api/submissions/{sid}/log")).status_code == 403
    assert (await admin.put("/api/problems/test_sum/log_visibility", json={"public_cases": "true"})).status_code == 400
    await admin.put("/api/problems/test_sum/log_visibility", json={"public_cases": True})
    public = (await bob.get(f"/api/submissions/{sid}/log")).json()["data"]
    assert public["details"][0]["result"] == "AC"
    assert (await bob.get(f"/api/submissions/{sid}")).status_code == 403
    assert (await bob.get("/api/submissions/", params={"problem_id": "test_sum"})).json()["data"]["total"] == 0
    audit = (await admin.get("/api/logs/access/", params={"user_id": "3"})).json()["data"]
    assert {row["status"] for row in audit} == {"200", "403"}
    assert all(row["action"] == "view_logs" for row in audit)
    assert (await alice.put(f"/api/submissions/{sid}/rejudge")).status_code == 403
    wrong = dict(PROBLEM, testcases=[{"input": "1 2", "output": "7"}])
    await admin.put("/api/problems/test_sum", json=wrong)
    result = await admin.put(f"/api/submissions/{sid}/rejudge")
    assert result.json()["data"] == {"submission_id": sid, "status": "pending"}
    assert (await finished(alice, sid))["score"] == 0
    stats = (await alice.get("/api/auth/me")).json()["data"]
    assert stats["submit_count"] == 1 and stats["resolve_count"] == 0


async def test_rate_limit_atomic_and_precedes_missing_resource(environment):
    app, admin, alice, bob, guest = environment
    payload = {"problem_id": "test_sum", "language": "python", "code": "print(3)"}
    results = await asyncio.gather(*[alice.post("/api/submissions/", json=payload) for _ in range(4)])
    assert sorted(r.status_code for r in results) == [200, 200, 200, 429]
    assert (await alice.post("/api/submissions/", json=payload | {"problem_id": "missing"})).status_code == 429
    assert (await alice.post("/api/submissions/", json={})).status_code == 400


async def test_languages_and_command_injection(environment):
    app, admin, alice, bob, guest = environment
    payload = {"name": "py3", "file_ext": ".py", "run_cmd": "python3 {src}", "time_limit": 2, "memory_limit": 128}
    assert (await alice.post("/api/languages/", json=payload)).status_code == 200
    assert "py3" in (await bob.get("/api/languages/")).json()["data"]["name"]
    for command in ["python3 {src}; whoami", "cmd /c echo x", "python3 -c x", "python3 ../../secret", "{exe} ../secret"]:
        assert (await alice.post("/api/languages/", json=payload | {"name": "evil", "run_cmd": command})).status_code == 400
    assert (await finished(alice, await submit(alice, language="py3")))["score"] == 10


async def test_persistence_and_reset(tmp_path):
    for iteration in range(2):
        app = create_app(tmp_path, testing=True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") as client:
                await client.post("/api/auth/login", json={"username": "admin", "password": "admintestpassword"})
                if iteration == 0:
                    await client.post("/api/problems/", json=PROBLEM)
                else:
                    assert (await client.get("/api/problems/test_sum")).status_code == 200
                    assert (await client.post("/api/reset/")).status_code == 200
                    assert (await client.get("/api/problems/")).status_code == 401
                    await client.post("/api/auth/login", json={"username": "admin", "password": "admintestpassword"})
                    assert (await client.get("/api/problems/")).json()["data"] == []
                    assert len(await app.state.store.all("users")) == 1
