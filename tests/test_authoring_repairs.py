import copy
import json

import httpx
import pytest

from backend.authoring_repairs import merge_draft, operation_input, repair_operation_count
from backend.main import create_app
from test_ai import CONFIG, DRAFT, VERIFICATION, Chunks, login, wait_task


COUNT_ERRORS = [
    ("3 6\nCOUNT\nASK 1 2\nASK 2 3\nASK 1 3\nCOUNT\n", 5),
    ("5 12\nADD 1 1 2\nADD 2 2 3\nADD 3 3 4\nADD 4 4 1\nASK 1 4\nCOUNT\nDEL 1\nASK 2 4\nDEL 3\nASK 1 3\nCOUNT\n", 11),
    ("10 26\nADD 1 1 2\nADD 2 3 4\nADD 3 5 6\nADD 4 7 8\nADD 5 9 10\nCOUNT\nDEL 1\nADD 6 2 3\nASK 1 5\nCOUNT\nDEL 6\nADD 7 1 5\nASK 1 10\nCOUNT\nDEL 3\nDEL 5\nADD 8 6 10\nASK 1 10\nCOUNT\nDEL 7\nDEL 8\nASK 2 9\nDEL 4\nCOUNT\nDEL 2\nASK 3 8\nCOUNT\n", 27),
    ("1\n3\nASK 1 1\n", 1),
]


@pytest.mark.parametrize("text,count", COUNT_ERRORS)
def test_real_log_count_mismatches(text, count):
    fixed = repair_operation_count(text, "INVALID: line count mismatch or first line")
    assert int(fixed.split()[1]) == count
    assert fixed.split()[2:] == text.split()[2:]
    assert len(fixed.splitlines()) == count + 1


def test_structured_operations_reject_duplicate_id_and_invalid_delete():
    for operations in ([['ADD', 1, 1, 2], ['ADD', 1, 1, 2]], [['DEL', 1]]):
        with pytest.raises(ValueError):
            operation_input(2, operations)
    result = operation_input(2, [['ADD', 1, 1, 2], ['ADD', 2, 1, 2], ['DEL', 1], ['ASK', 1, 2]])
    assert result.startswith("2 4\n")


def test_input_patch_preserves_successful_cases_and_rejects_noop():
    candidate = merge_draft(None, DRAFT).model_dump()
    targets = [{"collection": "testcases", "index": 1, "reason": "invalid"}]
    for patch in ({"reference_solution": "print(1)"}, {"problem": {"testcases": []}},
                  {"case_updates": [{"collection": "testcases", "index": 1, "input": candidate['problem']['testcases'][0]['input']}]},
                  {"case_updates": [{"collection": "testcases", "index": 2, "input": "55 1\n"}]}):
        with pytest.raises(ValueError):
            merge_draft(candidate, patch, targets=targets)


VALIDATOR = '''import sys
lines = sys.stdin.read().strip().splitlines()
if len(lines[0].split()) != 2: raise ValueError("INVALID: first line should contain n and q")
n,q=map(int,lines[0].split())
if len(lines)-1 != q: raise ValueError("INVALID: line count mismatch")
active=set(); used=set()
for line in lines[1:]:
    a=line.split()
    if a[0]=='ADD':
        i,u,v=map(int,a[1:])
        if i in used: raise ValueError("INVALID: duplicate ADD id")
        used.add(i); active.add(i)
    elif a[0]=='DEL':
        i=int(a[1])
        if i not in active: raise ValueError("INVALID: invalid deletion")
        active.remove(i)
print('VALID')
'''

ORACLE = '''import sys
data=iter(sys.stdin.read().split()); n=int(next(data)); q=int(next(data)); edges={}
for _ in range(q):
    op=next(data)
    if op=='ADD':
        i,u,v=int(next(data)),int(next(data)),int(next(data)); edges[i]=(u,v)
    elif op=='DEL': del edges[int(next(data))]
    else:
        g=[[] for _ in range(n+1)]
        for u,v in edges.values(): g[u].append(v);g[v].append(u)
        component=[-1]*(n+1); count=0
        for root in range(1,n+1):
            if component[root]!=-1: continue
            component[root]=count; stack=[root]
            while stack:
                u=stack.pop()
                for v in g[u]:
                    if component[v]==-1: component[v]=count;stack.append(v)
            count+=1
        if op=='COUNT': print(count)
        else: print('YES' if component[int(next(data))]==component[int(next(data))] else 'NO')
'''


async def test_reported_failures_repair_without_rewriting_good_cases(tmp_path):
    draft = copy.deepcopy(DRAFT)
    duplicate = "2 6\nADD 1 1 2\nADD 1 1 2\nCOUNT\nDEL 1\nASK 1 2\nCOUNT\n"
    inputs = [COUNT_ERRORS[0][0], duplicate, COUNT_ERRORS[1][0]] + [f'{n} 1\nCOUNT\n' for n in range(4, 9)]
    draft['problem'].update(title='动态道路', description='支持 ADD id u v、DEL id、ASK u v 和 COUNT 的动态无向图。每个道路编号只能新增一次。',
                            input_description='首行 n q，接着 q 行操作。', output_description='连通性输出 YES/NO，COUNT 输出分量数。',
                            constraints='城市编号 1 到 n。', samples=[{'input':COUNT_ERRORS[3][0], 'output':'YES\n'}],
                            testcases=[{'input':text, 'output':'incorrect\n'} for text in inputs])
    draft['reference_solution'] = ORACLE
    calls = {'draft':0, 'check':0, 'repair':0}
    async def handler(request):
        messages = json.loads(request.content)['messages']
        system = messages[0]['content']
        if '独立的 OJ 数据审核员' in system:
            calls['check'] += 1
            body = {'input_validator':VALIDATOR, 'oracle_solution':ORACLE, 'explanation':'逐次重建图计算连通性'}
        elif '测试数据构造器' in system:
            calls['repair'] += 1
            payload = json.loads(messages[1]['content'])
            assert [(t['collection'],t['index']) for t in payload['invalid_cases']] == [('testcases',2)]
            if calls['repair'] == 1:
                # A no-op must produce actionable feedback, never return to wholesale rewriting.
                patch = {'case_updates':[{'collection':'testcases','index':2,'input':duplicate}]}
                body = {'case_generator':'import json\nprint(json.dumps('+repr(patch)+'))'}
            else:
                assert '未发生变化' in payload['previous_repair_error']
                body = {'operation_updates':[{'collection':'testcases','index':2,'n':2,'operations':
                        [['ADD',1,1,2],['ADD',2,1,2],['COUNT'],['DEL',1],['ASK',1,2],['COUNT']]}]}
        else:
            calls['draft'] += 1
            body = draft
        return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=Chunks(json.dumps(body)))
    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://oj.test') as client:
            await login(client)
            await client.put('/api/ai/model-config', json=CONFIG)
            tid = (await client.post('/api/ai/problem-tasks/', json={'requirement':'生成动态道路连通性题目'})).json()['data']['task_id']
            task = await wait_task(client,tid)
            assert task['status']=='success', task
            assert calls == {'draft':1,'check':1,'repair':2}
            result = task['result']['problem']
            assert len(result['testcases']) == 8
            assert result['samples'][0]['input']=='1 1\nASK 1 1\n'
            assert result['testcases'][1]['output'].split()==['1','YES','1']
            assert [c['input'] for c in result['testcases'][3:]]==inputs[3:]


@pytest.mark.parametrize('failure', ['schema', 'runtime'])
async def test_checker_fault_only_repairs_checker(tmp_path, failure):
    calls = {'draft':0,'check':0}
    async def handler(request):
        messages = json.loads(request.content)['messages']
        if '独立的 OJ 数据审核员' in messages[0]['content']:
            calls['check'] += 1
            body = copy.deepcopy(VERIFICATION)
            if calls['check'] == 1:
                if failure == 'schema':
                    body = {'unexpected':'wrong checker format'}
                else:
                    body['input_validator'] = 'print(undefined_checker_variable)'
            else:
                assert len(messages) == 4
                assert '只修正审核程序' in messages[-1]['content']
                assert ('unexpected' if failure=='schema' else 'undefined_checker_variable') in messages[-1]['content']
        else:
            calls['draft'] += 1
            body = DRAFT
        return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=Chunks(json.dumps(body)))
    app = create_app(tmp_path, testing=True, ai_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://oj.test') as client:
            await login(client)
            await client.put('/api/ai/model-config',json=CONFIG)
            tid=(await client.post('/api/ai/problem-tasks/',json={'requirement':'生成整数相加题目'})).json()['data']['task_id']
            task=await wait_task(client,tid)
            assert task['status']=='success',task
            assert calls=={'draft':1,'check':2}
