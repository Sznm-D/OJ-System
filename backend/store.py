"""SQLite persistence; problem files are the editable source for the problem bank."""
import asyncio
import json
from pathlib import Path

import aiosqlite

from .models import Problem


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.problem_dir = root / "problems"
        self.lock = asyncio.Lock()

    async def open(self):
        self.problem_dir.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.root / "oj.sqlite3")
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, key TEXT, data TEXT, PRIMARY KEY(kind,key))")
        await self.db.commit()
        # A malformed config is a startup error, never a silently skipped problem.
        for path in sorted(self.problem_dir.glob("*.json")):
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
            problem = Problem.model_validate_json(raw)
            if path.stem != problem.id:
                raise ValueError(f"Problem filename must match id: {path.name}")
            await self.put("problems", problem.id, problem.model_dump())

    async def get(self, kind, key):
        async with self.db.execute("SELECT data FROM records WHERE kind=? AND key=?", (kind, str(key))) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def all(self, kind):
        async with self.db.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid", (kind,)) as cur:
            return [json.loads(row[0]) for row in await cur.fetchall()]

    async def put(self, kind, key, data):
        await self.db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, str(key), json.dumps(data, ensure_ascii=False)))
        await self.db.commit()

    async def delete(self, kind, key):
        await self.db.execute("DELETE FROM records WHERE kind=? AND key=?", (kind, str(key)))
        await self.db.commit()

    async def save_problem(self, problem):
        path = self.problem_dir / f"{problem['id']}.json"
        def write():
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(problem, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        await asyncio.to_thread(write)
        await self.put("problems", problem["id"], problem)

    async def delete_problem(self, problem_id):
        await asyncio.to_thread((self.problem_dir / f"{problem_id}.json").unlink, missing_ok=True)
        await self.delete("problems", problem_id)

    async def clear(self):
        await self.db.execute("DELETE FROM records")
        await self.db.commit()
        # Only validated bank records, never recurse through caller-controlled paths.
        for path in self.problem_dir.glob("*.json"):
            await asyncio.to_thread(path.unlink)

    async def close(self):
        await self.db.close()
