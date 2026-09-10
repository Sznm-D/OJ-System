"""Independent input and answer checks using the same bounded judge as submissions."""
import copy
import json
import re

from pydantic import Field

from .judge import normalize
from .models import Language, Model


class VerificationPrograms(Model):
    input_validator: str = Field(min_length=1, max_length=100000)
    oracle_solution: str = Field(min_length=1, max_length=100000)
    explanation: str = Field(min_length=1, max_length=10000)


VERIFICATION_PROMPT = """你是独立的 OJ 数据审核员。只根据题面与输入输出规范编写两个 Python 3 程序。
你不会看到参考解答和已有标准输出，必须独立推导，优先使用容易核对的直接算法。
只返回 JSON：{"input_validator":"完整 Python 3 代码", "oracle_solution":"完整 Python 3 代码", "explanation":"校验规则及独立求解算法"}。
input_validator 从标准输入读取一个测试点，检查所有字段的数据范围、声明的 n/m/q 与实际条数一致、
操作合法性、编号及删除状态、输入全部消费完毕等；合法只输出 VALID，非法只输出 INVALID: 具体原因。
oracle_solution 从标准输入读取同样的测试点并计算真正答案，逐行输出，无提示语。
只使用标准库，不访问网络或文件。校验器使用显式检查，不得忽略尾部输入。
输入不合法时必须捕获异常并打印 INVALID: 原因，不能让异常退出；这与校验器自身代码错误不同。
可使用小规模暴力算法，但需要考虑提供的输入规模：每个程序每个测试点最多 5 秒、256 MB。
若题面有歧义，不得偷偷改写规则。"""


def diagnostic(verdict, cases, label):
    compile_info = verdict.get("compile_info")
    if compile_info and compile_info["result"] != "success":
        return f"{label}编译失败：" + str(compile_info.get("message", ""))[:3000]
    details = verdict.get("details", [])
    if verdict.get("status") != "success" or len(details) != len(cases):
        return f"{label}未完成全部测试：" + str(verdict.get("error_info") or verdict.get("run_info"))[:1000]
    failures = []
    for case, result in zip(cases, details):
        # WA here means execution succeeded, but the provisional model-written answer differed.
        if result["result"] not in {"AC", "WA"}:
            failures.append({"id": result["id"], "result": result["result"],
                             "message": result.get("message", "")[:1200], "input": case["input"][:1000]})
    return f"{label}执行失败：" + json.dumps(failures[:3], ensure_ascii=False) if failures else None


async def check_answers(service, task, draft, config, reference, cache=None):
    problem = draft.problem.model_dump()
    cases = problem["samples"] + problem["testcases"]
    samples_count = len(problem["samples"])
    # Exclude model-written answers and reference code to avoid answer copying.
    specification = {key: problem[key] for key in (
        "title", "description", "input_description", "output_description", "constraints")}
    cache_key = json.dumps(specification, ensure_ascii=False, sort_keys=True)
    specification["input_examples"] = [case["input"][:2000] for case in cases[:3]]
    specification["largest_input_characters"] = max(len(case["input"]) for case in cases)
    programs = cache.get(cache_key) if cache is not None else None
    if programs is None:
        await service.progress(task, "独立审核：生成输入合法性检查器与第二份解答")
        messages = [
            {"role": "system", "content": VERIFICATION_PROMPT},
            {"role": "user", "content": json.dumps(specification, ensure_ascii=False)},
        ]
        if cache is not None and cache.get("repair"):
            messages += [{"role": "assistant", "content": cache["repair"]["output"]},
                         {"role": "user", "content": "只修正审核程序，题目不变。执行器反馈：" + cache["repair"]["error"]}]
        output = await service.completion(task, config, messages)
        from .ai import extract_json_object
        try:
            programs = VerificationPrograms.model_validate_json(extract_json_object(output))
        except (ValueError, TypeError) as error:
            if cache is not None:
                cache.clear()
                cache["repair"] = {"output": output, "error": str(error)[:2000]}
            task["retry_stage"] = "verification"
            return "独立审核程序格式错误，将仅修订审核程序：" + str(error)[:2000], None, None
        if cache is not None:
            cache.clear()
            cache[cache_key] = programs
    else:
        await service.progress(task, "题目规范未变，复用同一套独立校验程序")
    language = Language(name="python", file_ext=".py", run_cmd="python3 {src}").model_dump()
    checks = copy.deepcopy(problem)
    checks.update(time_limit=5, memory_limit=256, testcases=copy.deepcopy(cases))
    for case in checks["testcases"]:
        case["output"] = "VALID\n"

    async def run(code, label):
        await service.progress(task, label)
        async def progress(i, total):
            task["progress"] = f"{label} · {i}/{total}"
            await service.persist(task)
        async with service.judge.gate:
            return await service.judge.runner.judge(checks, language, code, progress, capture_output=True)

    validation = await run(programs.input_validator, "检查输入条数、范围与操作合法性")
    # Only a deliberately labelled INVALID exception is an input rejection, not arbitrary RE.
    for result in validation.get("details", []):
        match = re.search(r"^(?:ValueError|AssertionError): (INVALID:[^\r\n]*)\s*$",
                          result.get("message", "").strip(), re.MULTILINE)
        if result["result"] == "RE" and match:
            result.update(result="WA", stdout=match.group(1))
    error = diagnostic(validation, cases, "输入校验器")
    if error:
        if cache is not None:
            cache.clear()
            cache["repair"] = {"output": programs.model_dump_json(), "error": error}
        task["retry_stage"] = "verification"
        return error, None, None
    invalid = []
    invalid_targets = []
    for index, (case, result) in enumerate(zip(cases, validation["details"])):
        if normalize(result["stdout"]) == "VALID":
            continue
        if index < samples_count:
            invalid_targets.append({"collection": "samples", "index": index + 1, "reason": result["stdout"][:1500]})
        else:
            invalid_targets.append({"collection": "testcases", "index": index - samples_count + 1, "reason": result["stdout"][:1500]})
        invalid.append({**invalid_targets[-1], "input": case["input"][:1000]})
    if invalid:
        return "输入数据不符合题面，修复输入本身（不要仅修改参考解答）：" + json.dumps(invalid[:3], ensure_ascii=False), None, invalid_targets

    error = diagnostic(reference, cases, "参考解答")
    if error:
        return error, None, None
    oracle = await run(programs.oracle_solution, "独立解答与参考解答交叉验证")
    error = diagnostic(oracle, cases, "独立解答")
    if error:
        if cache is not None:
            cache.clear()
            cache["repair"] = {"output": programs.model_dump_json(), "error": error}
        task["retry_stage"] = "verification"
        return error, None, None
    disagreements = []
    for case, first, second in zip(cases, reference["details"], oracle["details"]):
        if normalize(first["stdout"]) != normalize(second["stdout"]):
            disagreements.append({"id": first["id"], "input": case["input"][:1000],
                                  "reference_output": first["stdout"][:1500],
                                  "independent_output": second["stdout"][:1500]})
    if disagreements:
        if cache is not None:
            cache.clear()
        return ("两份解答不一致，需根据题意定位错误，不得直接抄任一方答案：" +
                json.dumps(disagreements[:3], ensure_ascii=False) +
                "\n独立解答供定位错误：\n" + programs.oracle_solution), None, None

    # Only update expected outputs after BOTH programs agree on validated inputs.
    corrected = 0
    for case, result in zip(draft.problem.samples + draft.problem.testcases, reference["details"]):
        if normalize(case.output) != normalize(result["stdout"]):
            case.output = result["stdout"].replace("\r\n", "\n")
            corrected += 1
    if corrected:
        await service.progress(task, f"两份解答输出一致，已修正 {corrected} 处样例或测试点答案")
    return None, {"independent_passed": True, "input_validation_passed": True,
                  "corrected_outputs": corrected, "verification_explanation": programs.explanation,
                  "input_validator": programs.input_validator, "oracle_solution": programs.oracle_solution}, None
