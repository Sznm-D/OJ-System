# 知行 OJ · 程序设计训练实验

FastAPI 异步后端 + Streamlit 前端，覆盖 Step 1–6 和 AI 智能命题。界面采用与课程文档相近的蓝色强调色、浅色侧栏和简洁内容排版。

## 启动

需要 Python 3.10+；C++ 评测需要 `g++`（GCC 9+）。本次在 Windows、Python 3.12、GCC 16 上完成实际运行测试。

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Linux / macOS
# source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

- 前端：http://127.0.0.1:8501
- 后端文档：http://127.0.0.1:8001/docs
- 初始管理员：`admin` / `admintestpassword`
- 首次启动自动导入 3 道自编示例题，每题 8 个测试点。
- 在运行 `run.py` 的终端按 Ctrl+C，可停止前后端。

也可分别启动：

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
python -m streamlit run app.py --server.port 8501
```

端口 8000 在本次环境已被其他服务占用，因此默认使用 8001。自定义后端端口时，同时设置前端的 `OJ_API_URL`。

## 使用流程

1. 用初始管理员登录，或注册自己的普通账户。
2. 在「题库」选择题目，阅读样例并提交 Python / C++ 代码。页面自动更新评测结果；任务结束后停止轮询。
3. 在「提交记录」按用户、题目、状态和页码查询。管理员可重新评测，沿用原提交编号。
4. 在「题目管理」新增、导入、编辑题目。管理员可删除题目、公开或关闭日志可见性。语言管理可注册 Python/C++ 的命令模板变体，例如 C++17。
5. 在「用户管理」新增管理员或调整角色。被禁用的账户会被后端立即拦截。最后一位管理员不能被降级或禁用。
6. 在「日志审计」查看日志访问的成功/拒绝记录，以及独立的权限变更记录。

示例 Python 解答（P1001）：

```python
a, b = map(int, input().split())
print(a + b)
```

示例 C++ 解答（P1001）：

```cpp
#include <iostream>
int main() {
    long long a, b;
    std::cin >> a >> b;
    std::cout << a + b << '\n';
}
```

## AI 智能命题

在「AI 智能命题 → 模型配置」填写提供商 URL、模型名称、密钥，以及该模型的实际输入/输出单价和计价单位。当前实现使用兼容 Chat Completions 的流式接口；URL 填到 `/v1`，程序追加 `/chat/completions`。服务需要支持 `stream=true`；用量可缺省。

例如填写一段需求：

> 面向学习循环与列表的学生，设计一道中等难度的前缀和题。输入规模最高 100000，覆盖负数、单点区间、完整区间和大量查询。题面要说明区间端点是否包含，参考解答采用 O(n+q) 算法。

完整流程：

- 后端在后台发起真实模型请求，持续保存已接收字符数、阶段进度及用量；前端每 1.5 秒轮询。
- 模型生成完整题目 JSON、至少 8 组不同输入的测试数据、Python 参考解答、测试覆盖说明和算法解释。
- 用与普通提交相同的受限执行器运行参考解答，校验全部样例和测试点。
- 首轮 JSON 或答案校验失败时，自动带着校验问题请求模型修订一次。仍失败则结束任务，不写入题库。
- 点击「中断任务」会取消后端任务、关闭流式连接，并终止正在执行的验证程序。不能仅靠关闭页面中断任务。
- 成功后点击「送入题目编辑器」；在「新增题目」中审阅保存，或在「编辑与权限」选择现有题并勾选用 AI 草稿填充。
- 需要迭代改题时，在下一次命题填写已有题目编号和修改要求。

密钥加密后保存在 SQLite 中，接口只返回 `api_key_configured`。加密主密钥默认保存在 `data/model-secret.key`，也可通过 `OJ_ENCRYPTION_KEY` 注入。不要把数据目录或主密钥提交到 Git。Windows 上应由部署者使用 NTFS 权限保护此目录；`chmod` 不能替代 NTFS ACL。

费用计算：

```text
费用 = 输入 Token / price_unit × input_price
     + 输出 Token / price_unit × output_price
```

输入/输出 Token、计价单位、单价、币种和当前任务费用均会显示。两轮调用累计计费。提供商返回完整用量时采用原始用量；流式过程中、缺少用量或中断时使用字符数除以 4 向上取整，并明确标注估算。中文及隐藏推理 Token 的偏差可能较大，最终账单以提供商为准。价格由用户按实际报价配置，默认 0 不表示模型免费。

本项目没有内置或代填付费模型密钥。自动测试使用可控的模拟流式服务验证调用协议、中断、重试和费用；真实命题需用户配置自己的提供商后使用。

## 结构与实验对应

```text
app.py                  Streamlit 界面，所有业务操作调用 REST API
run.py                  一次启动前后端
backend/
  main.py               所有异步 API、认证、权限、频率控制
  models.py             请求、题目、语言与 AI 输出字段校验
  store.py              异步 SQLite 持久化与 JSON 题库加载
  judge.py              异步队列、编译、运行、输出比较、进程清理
  limit_exec.py         Linux 资源限制子进程启动器
  windows_job.py        Windows Job Object 资源限制
  ai.py                 加密配置、流式调用、校验、重试和中断
examples/               3 道完整示例题
tests/                  API、评测、AI 和真实 HTTP 前端测试
docs/                   设计说明与验收说明
runner.Dockerfile       可选隔离评测镜像
```

| 实验模块 | 实现内容 |
|---|---|
| Step 1 | JSON 加载、完整字段校验、增删改查、默认字段、原子写文件 |
| Step 2 | Python/C++、动态模板注册、时间/内存限制、AC/WA/TLE/MLE/RE/CE/UNK |
| Step 3 | 持久化提交、异步 pending/success/error、筛选与分页、管理员重测 |
| Step 4 | bcrypt、随机服务端 Session、Cookie、初始管理员、角色管理与统计 |
| Step 5 | 测试点结果、可见性裁剪、公开日志、成功/拒绝访问审计 |
| Step 6 | Streamlit 用户/题目/提交页面、真实 API 对接、会话与异常处理 |
| Advance | 模型配置、流式进度、中断、费用统计、参考解答校验、编辑器衔接 |

所有 API 路由均为 `async def`。SQLite 使用 `aiosqlite`，密码计算和文件操作转交线程，用户程序使用异步子进程；评测任务用 `asyncio.create_task` 调度。一个信号量串行执行普通评测和 AI 参考解答，API 仍能并发响应。

## API 约定

按 [课程 API 文档](https://dbg-course.github.io/python-docs/oj/api/) 实现。所有业务 JSON 响应为 `{code, msg, data}`，HTTP 状态与 `code` 一致。参数错误返回 400，未登录 401，无权限/禁用 403，不存在 404，状态冲突 409，提交超频 429。

主要接口：

| 路径 | 方法 | 说明 |
|---|---|---|
| `/api/problems/` | GET / POST | 题目列表、创建 |
| `/api/problems/{id}` | GET / PUT / DELETE | 完整配置、更新、管理员删除 |
| `/api/languages/` | GET / POST | 语言列表、受限模板注册 |
| `/api/submissions/` | GET / POST | 筛选列表、提交 |
| `/api/submissions/{id}` | GET | 本人/管理员查询详情 |
| `/api/submissions/{id}/rejudge` | PUT | 管理员重测 |
| `/api/auth/login`、`/api/auth/logout` | POST | 登录、登出 |
| `/api/users/` | POST / GET | 注册、管理员查询列表 |
| `/api/users/admin` | POST | 创建管理员 |
| `/api/users/{id}`、`/api/users/{id}/role` | GET / PUT | 个人信息、管理员改角色 |
| `/api/submissions/{id}/log` | GET | 经权限裁剪的评测日志 |
| `/api/problems/{id}/log_visibility` | PUT | 管理员设置公开日志 |
| `/api/logs/access/` | GET | 管理员查询访问审计 |
| `/api/ai/model-config` | GET / PUT | 个人模型配置 |
| `/api/ai/problem-tasks/` | POST | 创建命题任务 |
| `/api/ai/problem-tasks/{id}` | GET | 状态、进度、结果和用量 |
| `/api/ai/problem-tasks/{id}/cancel` | PUT | 中断任务 |
| `/api/reset/` | POST | 管理员重置测试数据、退出会话、重建管理员 |

扩展接口：`GET /api/auth/me` 返回当前用户；`GET /api/health` 返回执行器信息；`GET /api/logs/roles/` 返回权限变更审计；管理员可 `GET /api/problems/{id}/log_visibility` 读取配置。

分页严格遵循课程特殊规则：不传 `page` 和 `page_size` 查询全部；仅传 `page_size` 查询第一页；仅传 `page` 返回 400。页码从 1 开始，每页最大 500 条。提交列表必须提供 `user_id` 或 `problem_id` 至少一个；普通用户不能查询其他用户的提交。

日志权限与提交详情权限独立：公开日志可由所有登录用户查询，但不会开放别人的提交详情。私有日志的所有者可查总分，只有管理员或公开策略允许时才返回 `details`。

按课程接口要求，题目详情向已登录用户返回完整配置（包含 testcases），普通用户可以编辑任意题目。因此这里的日志权限不是竞赛级隐藏测试数据机制；如用于正式比赛，应另设题目作者权限和不含测试数据的公开题面接口。

## 运行限制与安全边界

默认 `OJ_RUNNER=local` 用于本机课程实验，仅运行可信练习代码。资源限制不能隔离文件系统和网络；它不是可直接公开给陌生人使用的安全沙箱。服务默认仅监听回环地址。

- Windows：先挂起创建进程，再加入 Job Object 后恢复；约束进程树总内存和进程数，关闭 Job 时清理所有后代。同时监控 RSS 和墙钟时间。
- Linux：子启动器设置 CPU、地址空间、文件大小、core dump 限制；墙钟超时和进程组清理由父进程处理。
- 标准输出/错误流累计超过 512 KB 时终止并按 RE 处理。响应仅保留有限的编译/错误信息，不返回测试输入输出。
- 题目时间/内存限制分别按「题目 → 语言 → 3 秒/128 MB」取值。运行时间不包含创建子进程和 C++ 编译阶段。
- 语言模板使用参数数组执行，不调用 shell。仅允许 Python、g++、clang++ 及已生成的可执行文件；拒绝 shell 符号、任意路径、解释器内联命令和不在白名单内的编译参数。扩展新的执行器需由部署者审核白名单。
- Session 在服务器存储，Cookie 使用 HttpOnly、SameSite=Strict、24 小时有效期，登出立即删除。HTTPS 部署时设置 `OJ_COOKIE_SECURE=1`。
- 模型服务默认要求 HTTPS，并拒绝解析到内网/保留地址的 URL，不跟随重定向。可信本地模型可显式配置 `OJ_AI_ALLOW_LOCAL=1`。公网部署仍应使用网络出口策略防止 DNS 重绑定等问题。
- SQLite 持久化用户、提交、语言、日志与 AI 任务；题目同步到 `data/problems/<id>.json`。适用于单进程课程规模，请勿开启多个 Uvicorn worker。

可选 Docker 隔离模式（需先安装 Docker）：

```bash
docker build -f runner.Dockerfile -t oj-lab-runner:latest .
# Linux
export OJ_RUNNER=docker
# PowerShell
# $env:OJ_RUNNER = "docker"
python run.py
```

容器禁止网络、采用非 root 用户、只读根文件系统、最小权限、内存/CPU/进程数约束，只挂载本次评测目录。容器模式不采集精确内存峰值，日志 `memory` 返回 null；仍由容器内存限额约束。当前机器无 Docker，该可选模式没有完成实际运行验收。若需公开部署，仍需独立评测主机/隔离加固与安全审查。

## 验证与提交

```bash
python -m pytest -q
python -m ruff check .
```

测试使用临时数据库，不改动演示数据。前端测试临时启动自己的后端，通过真实 HTTP 验证登录、提交及页面跳转。C++ 测试在没有 g++ 时标记为跳过。

详细验收见 [docs/验收说明.md](docs/验收说明.md)，架构与设计取舍见 [docs/设计说明.md](docs/设计说明.md)。Git 提交使用 Conventional Commits，如 `feat(judge): add asynchronous resource-limited evaluation`。本地仓库包含可查看的规范提交记录。
