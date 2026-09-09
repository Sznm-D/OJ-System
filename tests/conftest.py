import httpx
import pytest

from backend.main import create_app


PROBLEM = {"id": "test_sum", "title": "两数之和", "description": "求和", "input_description": "两个整数", "output_description": "和", "constraints": "绝对值不超过 10", "samples": [{"input": "1 2\n", "output": "3\n"}], "testcases": [{"input": "1 2\n", "output": "3\n"}], "time_limit": 1, "memory_limit": 128}


@pytest.fixture
async def environment(tmp_path):
    app = create_app(tmp_path, testing=True)
    async with app.router.lifespan_context(app):
        clients = [httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://oj.test") for _ in range(4)]
        admin, alice, bob, guest = clients
        assert (await admin.post("/api/auth/login", json={"username": "admin", "password": "admintestpassword"})).status_code == 200
        for client, name in [(alice, "alice"), (bob, "bobby")]:
            assert (await client.post("/api/users/", json={"username": name, "password": "password123"})).status_code == 200
            await client.post("/api/auth/login", json={"username": name, "password": "password123"})
        await admin.post("/api/problems/", json=PROBLEM)
        yield app, admin, alice, bob, guest
        for client in clients:
            await client.aclose()
