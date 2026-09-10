"""Apply narrow AI repairs without discarding tests that already passed."""
import copy
import json

from .models import AIDraft, Language
from .authoring_checks import diagnostic


def operation_input(n, operations):
    """Render the ADD/DEL/ASK/COUNT format; derive q, validate road lifetimes."""
    if type(n) is not int or n < 1 or not isinstance(operations, list) or not operations:
        raise ValueError("n 和 operations 无效")
    active, used, rows = set(), set(), []
    sizes = {"ADD": 4, "DEL": 2, "ASK": 3, "COUNT": 1}
    for operation in operations:
        if not isinstance(operation, list) or not operation or not isinstance(operation[0], str):
            raise ValueError("每个操作必须为列表，如 ['ADD', 1, 1, 2]")
        name = operation[0]
        if name not in sizes or len(operation) != sizes[name] or any(type(x) is not int for x in operation[1:]):
            raise ValueError("操作名、参数数量或类型错误")
        if name in {"ADD", "ASK"} and any(not 1 <= x <= n for x in operation[-2:]):
            raise ValueError("城市编号越界")
        if name == "ADD":
            road = operation[1]
            if road <= 0 or road in used:
                raise ValueError("duplicate ADD id：每个道路编号只能新增一次，请为平行边分配不同编号")
            used.add(road)
            active.add(road)
        elif name == "DEL":
            if operation[1] not in active:
                raise ValueError("DEL 指向不存在的道路")
            active.remove(operation[1])
        rows.append(" ".join(map(str, operation)))
    return f"{n} {len(rows)}\n" + "\n".join(rows) + "\n"


def repair_operation_count(text, reason):
    """Narrow recovery for a validator-reported count/header mismatch; never change operations."""
    if not any(word in reason.lower() for word in ("count", "tokens", "operations", "first line", "条数", "首行")):
        return None
    tokens = text.split()
    try:
        n, declared = int(tokens[0]), int(tokens[1])
        if declared < 0:
            return None
        operations, position = [], 2
        sizes = {"ADD": 4, "DEL": 2, "ASK": 3, "COUNT": 1}
        while position < len(tokens):
            size = sizes[tokens[position]]
            args = [int(value) for value in tokens[position + 1:position + size]]
            operations.append([tokens[position], *args])
            position += size
        repaired = operation_input(n, operations)
        return repaired if repaired != text else None
    except (ValueError, KeyError, IndexError):
        return None


def merge_draft(candidate, patch, targets=()):
    if not isinstance(patch, dict):
        raise ValueError("修订补丁必须为 JSON 对象")
    patch = copy.deepcopy(patch)
    problem_patch = patch.get("problem")
    if isinstance(problem_patch, dict):
        # Recover a common, unambiguous model nesting mistake; retain strict validation otherwise.
        for key in ("reference_solution", "reference_language", "coverage", "explanation"):
            if key in problem_patch:
                nested = problem_patch.pop(key)
                if key in patch and patch[key] != nested:
                    raise ValueError(f"{key} 在顶层与 problem 内有冲突，请只放在顶层")
                patch[key] = nested
    updates = patch.pop("case_updates", [])
    if not isinstance(updates, list):
        raise ValueError("case_updates 必须为列表")
    merged = copy.deepcopy(candidate) if candidate else {}
    for key, value in patch.items():
        if key == "problem" and isinstance(value, dict) and "problem" in merged:
            merged["problem"].update(value)
        else:
            merged[key] = value
    seen = set()
    for update in updates:
        if not isinstance(update, dict) or set(update) != {"collection", "index", "input"}:
            raise ValueError("case_updates 每项仅包含 collection、index、input")
        group, index = update["collection"], update["index"]
        if group not in {"samples", "testcases"} or type(index) is not int or not candidate:
            raise ValueError("测试点位置无效，collection 为 samples/testcases，index 从 1 开始")
        if not 1 <= index <= len(candidate["problem"][group]) or (group, index) in seen:
            raise ValueError("测试点编号重复或越界")
        seen.add((group, index))
        merged["problem"][group][index - 1]["input"] = update["input"]
    draft = AIDraft.model_validate(merged)
    if len({case.input for case in draft.problem.testcases}) < 8:
        raise ValueError("至少需要 8 组不同输入的测试点；补丁不能只返回失败点替换整组 testcases，请使用 case_updates")
    if candidate:
        if targets:
            for key in candidate:
                if key != "problem" and merged[key] != candidate[key]:
                    raise ValueError("输入修复阶段只能修正指定输入，不得改写参考解答或说明")
        for group in ("samples", "testcases"):
            if len(merged["problem"][group]) < len(candidate["problem"][group]):
                raise ValueError("修订不能减少样例或测试点数量，请仅修改失败位置")
        if targets:
            allowed = {(item["collection"], item["index"]) for item in targets}
            for group in ("samples", "testcases"):
                old = candidate["problem"][group]
                new = merged["problem"][group]
                if len(new) != len(old):
                    raise ValueError("输入修复阶段必须保持测试点数量，使用 case_updates 指定失败位置")
                for index, (before, after) in enumerate(zip(old, new), 1):
                    if (group, index) not in allowed and before != after:
                        raise ValueError(f"{group}[{index}] 已通过输入校验，不应改写")
            if all(merged["problem"][group][index - 1]["input"] == candidate["problem"][group][index - 1]["input"]
                   for group, index in allowed):
                raise ValueError("失败输入未发生变化。请实际修复指定位置，不能只修改参考解答或解释")
            for key, value in candidate["problem"].items():
                if key not in {"samples", "testcases"} and merged["problem"][key] != value:
                    raise ValueError("输入修复阶段不得改写题意或约束以绕过错误")
    return draft


async def run_case_generator(service, task, code):
    if not isinstance(code, str) or not 1 <= len(code) <= 100000:
        raise ValueError("case_generator 必须为完整 Python 代码")
    await service.progress(task, "执行数据构造脚本，自动计算条数并生成测试点修复补丁")
    problem = {"time_limit": 5, "memory_limit": 256, "testcases": [{"input": "", "output": ""}]}
    language = Language(name="python", file_ext=".py", run_cmd="python3 {src}").model_dump()
    async with service.judge.gate:
        result = await service.judge.runner.judge(problem, language, code, capture_output=True)
    error = diagnostic(result, problem["testcases"], "数据构造脚本")
    if error:
        raise ValueError(error)
    patch = json.loads(result["details"][0]["stdout"])
    if not isinstance(patch, dict) or set(patch) != {"case_updates"}:
        raise ValueError("数据构造脚本只能输出含 case_updates 的 JSON 对象")
    return patch
