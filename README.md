# Oldman EPG Dashboard

这是一个完整的 Oldman 业务 Dashboard 示例，也是可以独立克隆、运行和发布的 Git 仓库。后端只使用公开的 `oldman.*` API，前端只使用 `oldman-web` 的公开入口。

## 安装

```bash
# 前置条件：bootstrap 会直接调用 uv 和 pnpm，先把两者装好
#   uv：       curl -LsSf https://astral.sh/uv/install.sh | sh
#   pnpm：     安装 Node.js 20 及以上后执行 corepack enable
python3 scripts/bootstrap.py
# 初次克隆时为所有已定义服务创建配置；已有文件绝不覆盖。
for service in web task_worker task_scheduler nats_a nats_b; do
  test -e "data/${service}_settings.yaml" || cp "data/${service}_settings.example.yaml" "data/${service}_settings.yaml"
done
```

仓库与框架源码目录（`oldman_framwork` 或 `oldman`）同级时，bootstrap 会自动把 Oldman Python 源码 editable 安装到本仓库的 `.venv`，并让 Vite 直接读取本地 `oldman-web` 源码。链接和依赖均位于 Git 忽略目录，不需要修改依赖清单，也不需要反复构建框架前端。

没有同级源码仓库时，同一命令会安装 `pyproject.toml` 和 `frontend/package.json` 中声明的 PyPI/npm 正式包，因此公开仓库不依赖本机目录结构。

默认配置使用本地 SQLite，并通过独立的 Redis 连接别名提供 Session、SSE、示例缓存和任务结果。
分布式任务示例使用启用 JetStream 的 NATS。请先按配置地址启动 Redis 和 NATS；Demo 不会代替使用者管理它们。
例如已安装 `nats-server` 后可在独立终端运行 `nats-server -js -sd data/nats`。
不试用任务时，可设 `taskiq.enabled: false`，不需要 Worker 或 Scheduler。
只有 Web 的 `taskiq.enabled` 和 `nats_bus.enabled` **都为 false** 时，Web 才不需要 NATS。
Core 事件/RPC 本身不使用 JetStream；与任务共用这台 NATS 时仍使用完全独立的连接和地址。

首次运行按以下顺序初始化：

```bash
./run.sh web settings sync
./run.sh task_worker settings sync
./run.sh task_scheduler settings sync
./run.sh nats_a settings sync
./run.sh nats_b settings sync
./run.sh db migrate
./run.sh web loaddata demo
./run.sh web createsuperuser
pnpm --dir frontend build
./run.sh web static collect
./run.sh web start
```

`settings sync` 只补齐缺失的托管配置；数据库结构统一由 `db migrate` 建立。
项目级迁移读取全部服务配置，因此先准备上述五份 YAML，再迁移；所有服务使用同一个数据库和 Auth User 模型。
`loaddata demo` 一次导入组件示例和 EPG 业务页面的真实数据，可以重复执行，但不会删除之后新增的数据。创建超级用户时，命令会交互式读取账号和密码。

## 本地源码调试

```bash
python3 scripts/dev.py
```

这个入口同时管理 Vite 和 Web 服务。修改 Dashboard 或同级 Oldman 的 Python/TypeScript 源码即可直接调试，不需要在“本地依赖”和“发布依赖”之间手工切换。打开 <http://127.0.0.1:17998/>。

## 产品模式

```bash
pnpm --dir frontend build
./run.sh web start
```

产品模式读取 `static/dist` 的构建产物，不连接 Vite。服务启动和管理员脚本都不会自动迁移数据库。

## JSON Fixture

`dumpdata` 和 `loaddata` 用于制作、检查和载入可重复的 JSON 示例数据，不是数据库备份或 Schema 迁移工具。导出时必须明确指定应用或模型，避免误导出 Auth 等无关数据：

```bash
./run.sh web dumpdata examples --output /tmp/examples.json
./run.sh web dumpdata examples.ExampleProject --output /tmp/projects.json
./run.sh web loaddata /tmp/examples.json
```

项目自带的 `apps/examples/fixtures/demo.json` 是完整 Dashboard 的统一预设数据，可通过 Fixture 名 `demo` 载入。它可以包含多个已安装 App 的记录，只保存数据库字段，不复制上传文件。

## Feedback 的真实确认与输入

完成上述配置、迁移、`loaddata demo` 和前端构建后，启动 Web、用 staff 账户登录，打开
`/examples/messages/feedback`。“提交审核”确认后才修改所选 ExampleProject 的状态；取消不发请求，
重复审核返回可见业务错误。“重命名项目”提供旧值、空白/长度校验和真实保存结果，不改变 slug。
在 `/examples/tables/json` 可以对照同一条数据库记录。这些按钮会修改 Demo 数据，不是静态提示。

私有组件为 `frontend/src/components/examples/feedback-workflow.ts`，只在 ExamplesPage 注册 loader；
服务端写入口为 `apps/examples/views/messages.py::example_feedback_project`，结果使用相邻模板目录的
`_project_result.html`。复用 Page Feedback、局部 Preloader、公共 runAction/CSRF，不另写提交引擎。
断网/403 会提示失败并恢复按钮；离开页面放弃旧请求与 UI 结果，但不撤销已经提交的数据库修改。

## 自定义 App 命令

`apps/examples/commands.py` 提供 `project-stats`，和网页共用 ExampleProject/ExampleTeam。
先完成配置及数据库迁移；`loaddata demo` 可提供演示数据。统计本身不需要前端构建、登录或启动 Web。

```bash
./run.sh web --help
./run.sh web project-stats --help
./run.sh web project-stats
./run.sh web project-stats --team-id 1
```

命令列在“Dashboard Examples”的本地化帮助分组中，默认输出全部项目总数和各状态数量。
`--team-id` 筛选一个真实团队，省略则统计全部；只输出实际存在的状态分组。
每次都读数据库，不读写缓存或投递任务。可在 Table 页面增改项目后再次运行，对照变化。

非法 ID（例如 0、负数或文本）由 Typer 拒绝，退出码 2；团队不存在则报错、退出 1。
团队存在但没有项目，或整个项目表为空时，正常显示 0、退出 0。数据库未迁移或不可访问时失败退出，
不会自动建表、导入数据或输出假统计。命令结束（含查询失败）会关闭本次使用的数据库连接池。

和其他异步 App 命令一样，Demo 配置启用 NATS/Taskiq 时，入口会先连接它们；请按上面的安装说明准备基础设施。
不需要另外启动 nats_a/nats_b、Worker 或 Scheduler。不试用这两种能力时，可以在本机 YAML 将
`nats_bus.enabled` 与 `taskiq.enabled` 都设为 false；统计自身只依赖数据库。

App 已在 `settings.apps` 注册，AppConfig 默认从 `commands.py` 的公开类名发现 Command，
不需要在 WebService 再注册一次。入口使用 `async handle()` 和 `typer.Option(min=1)`；具体实现可直接复制参考。

`./run.sh web background-stats [--team-id 1]` 是本地后台协程示例，代码在 `apps/examples/background.py`。
它使用 BackgroundTaskManager 每秒查询项目数量，得到两次真实样本后显式取消，输出 running/stopped
状态及 finally 清理标记。取消任务、停止监控、移除登记后关闭数据库，不启动常驻采样服务、不写记录。
不存在的团队令协程失败并退出 1，参数 0 退出 2，清理仍执行。不要在活 Web 请求里调用这个拥有整个
单例管理器的命令；长期任务由服务统一拥有，持久排队使用后面的 Taskiq。

`./run.sh web python-process --scenario success|error|timeout|cancel` 展示短期 Python spawn 子进程。
实际命令每次只传一种 scenario；省略默认 success。父进程读取项目状态，`process_jobs.py` 顶层函数
处理纯 list 并输出 PID/统计，不继承连接。error 故意抛异常，框架打印堆栈并返回 None，命令退出 1；
timeout 在 1 秒上限回收等待中的子进程并退出 1；cancel 等子函数进入后取消，显示 CancelledError、退出 0。
成功也回收子进程，所有路径显示 `reaped=true` 并清理自有临时 PID 文件。运行
`.venv/bin/python scripts/verify-process-demos.py` 可串行检查两个进程 Demo 的各四种模式。它不修改数据库，
不自动建表；普通简单统计仍推荐 project-stats，而不是为了几行计数额外创建进程。

`./run.sh web subprocess-demo` 展示受控外部程序，`--scenario` 同样接受 success、error、timeout、cancel。
固定执行 `python -m apps.examples.external_job`，通过 stdin JSON bytes 传项目状态、读取 stdout/stderr。
error 固定子程序退出 7，CLI 保留错误输出并退出 1；timeout 1 秒后退出 1；cancel 读到启动行后取消等待、退出 0。
等待分支还创建一个同组 sleep 子进程；框架回收整个组，验证脚本检查 command/group/descendant，
不是只检查父程序结束。没有任意命令执行入口或 shell 字符串拼接，子模块不加载数据库配置。
`./run.sh web worker-demo --scenario success|error|stop` 展示固定 BaseManager/Worker/Task，代码在
`apps/examples/worker_examples.py` 和 `worker_jobs.py`。父进程读取真实项目，子 Worker 生成临时 JSON 快照，
读到报告才输出数量/PID，不把任务 ID 当成功。error 在传输副本加入非法 id、转换失败后退出 1；
stop 等实际停止指令和 execute finally 后才结束。最后回收 Worker、数据库连接和自有文件。
`.venv/bin/python scripts/verify-worker-demo.py` 串行验证这三种模式，要求先准备数据库，仍不启动长期服务。

只检查 project-stats 可运行 `.venv/bin/python -m unittest tests.test_examples_commands -v`，它会创建并清理自己的临时数据库，
不会读写本机业务 YAML/数据库，不启动 Web、Redis 或 NATS。

`./run.sh web cache-levels` 展示 MemoryCache 命中/过期，以及另一个真实进程更新 Redis 后，
TwoLevelCache 仍读到本进程旧副本的现有行为。源码 `apps/examples/cache_levels.py` 共用
cache_example.py 的真实项目查询，输出实际计算时间、父/子 PID、回填/过期/删除结果。
只操作本次 UUID namespace；完成或失败后关闭自己的连接，不改变数据库。需要 CACHE Redis；
不需要 Web、前端构建或长期 Worker。命令内检查均针对实际结果，不是静态占位。

可选命令 `django-cache` 使用真实 Django Redis backend 与 Oldman 双向交换项目统计，并检查
整数/bool/None/文本/bytes、TTL、版本和损坏数据。Django 不加入项目依赖，只为本次示例隔离安装：

```bash
demo_django_dir=$(mktemp -d /tmp/oldman-django-demo-XXXXXX)
uv pip install --python .venv/bin/python --target "$demo_django_dir" 'Django>=5.2,<5.3'
PYTHONPATH="$demo_django_dir" ./run.sh web django-cache
# 命令结束后只删除本次创建的目录。
rm -r -- "$demo_django_dir"
```

仍需当前配置的 CACHE Redis 和数据库；无需 Django 网站。只支持 Django 默认空前缀、VERSION=1、
默认 codec；Pickle 只读可信 Redis。源码 `apps/examples/django_cache.py` 使用随机 key，并清理
已知 key/版本及双方连接，不执行清库、不修改业务记录。缺少可选依赖只影响这个命令。

`./run.sh web image-cache` 使用自有 PNG bytes 和临时 FileSystemStorage，实际生成原图/WebP，
观察命中续期、过期读 miss、下一次写入清理旧文件。最后故意输入损坏图片，出现预期的 WebP
错误日志并仅保留 original；这不是上传验证器。源码 `apps/examples/image_cache.py`，只需要 CACHE
Redis、不查询数据库。命令结束会删除自己的随机集合和临时目录，不碰用户上传文件。

`./run.sh web cached-stats` 用 `cache_async_response` 装饰同一真实统计查询：连续两次结果和
计算时间相同，1 秒到期后重新计算。源码 `apps/examples/cache_functions.py`；使用根 cache
配置指向的 Redis，finally 只删除本次随机 prefix 并关闭连接。数据库不写入，不启动 Web。

## 模板预览和验证

### Core NATS 接收服务

侧栏“服务通信”有三张独立页面，不会在打开页面时发送消息：

- `/examples/communication/rpc`：选择真实项目和 monitor_a/monitor_b，即时显示接收端查询结果与 PID；“查询并发布”还会发出一条报告事件。
- `/examples/communication/events`：分别发送 10 条竞争事件或广播事件，然后手动查询计数。竞争计数合计增加 10，广播在每个在线节点各增加 10；不要求竞争恰好分成 5/5。
- `/examples/communication/failures`：实际无接收者、0.5 秒超时和接收函数异常。后两项需要 nats_a 在线；其故意异常写在接收服务日志中，调用者仍只看到超时，不自动重试。

所有按钮走现有 staff 权限、CSRF、普通 Form 和 Actions。通信关闭时仍可打开页面，但操作禁用。
没有数据时按安装步骤加载 fixture，不会因页面展示偷偷生成记录。停止 nats_b 后，RPC 显示无接收者，
计数页仍保留 monitor_a 的实际结果；公共 Demo 多人操作会共同累计，不能用它保证独占的计数实验。

`services/nats_a.py` 和 `services/nats_b.py` 是独立的 Simple 服务，安装同一份
`apps.communication.events`。它们用现有数据库的 ExampleProject/ExampleTask 做即时查询，
不创建表、不生成数据、不安装 Web 路由。`nats_a` 的 peer 是 `monitor_a`，`nats_b` 是 `monitor_b`。
Web/Worker/Scheduler 不安装接收 App，只调用 `apps/examples/nats_example.py` 的共享发送函数。

完成上面的配置、迁移及 fixture 后，在两个独立终端分别启动：

```bash
./run.sh nats_a start
./run.sh nats_b start
```

停止也分别执行，不需要关掉整台 NATS：

```bash
./run.sh nats_a stop
./run.sh nats_b stop
```

`run.sh` 只是命令入口，不会偷偷启动其他服务。只演示 Core 查询/事件时，无需启动任务 Worker 或 Scheduler。
所有通信服务必须设置相同的 `nats_bus.namespace: epg_demo` 和 NATS 地址；`nats_alias: TASKIQ`
只是引用 Demo 已有连接配置，不代表 Core 使用 Taskiq 的 socket、subject 或 ACK。
不同项目请修改 namespace；它防止命名冲突，不代替服务器权限控制。

数据格式默认 `nats_bus.serializer_mode: msgpack`。需要 JSON bytes 时把通信双方都改为
`msgspec_json` 并重启；不要只改一方，也不要在每次调用里临时换编码。
来源 `peer_id` 由发送服务明确配置，与目标参数、连接日志名及进程 PID 不同。

事件只统计接收服务本次启动以来的三项计数和最近来源；所有操作用户共享，重启归零。
同 queue 的接收者竞争处理一份，无 queue 的在线接收者各处理一份。
发布完成不等于已处理，RPC 超时也不表示接收函数已取消；Core 离线期间的事件不持久重放。
需要可持久排队的后台作业使用下面的 Taskiq 示例。

### 分布式任务示例

侧栏“分布式任务”有三页，使用 `apps/examples/tasks.py` 中的真实任务，不会因为打开页面就投递任务。

- `/examples/tasks/results`：选择数据库项目，投递摘要、Storage JSON 导出、故意失败或不保存结果的任务；响应先显示任务 ID，再点击“查询此结果”。
- `/examples/tasks/schedules`：20 秒后执行一次、取消计划、显式失败重试、60 秒 interval 和两分钟 cron。动态计划由按钮创建，取消按钮和“您的示例计划”列表负责删除，关闭页面不会删除。
- `/examples/tasks/queues`：reports／exports 两队列共享执行进程；普通刷新只在一个进程执行，广播刷新所有在线订阅进程，日志中可以看到不同 PID。选择业务任务记录重复完成，第一次条件 UPDATE 改变状态，后续返回 `changed=false`。

升级本地框架源码后先重新运行 `python3 scripts/bootstrap.py`，确保 Demo 环境装入 Taskiq 等新增依赖。
首次配置已在安装步骤中创建；已有文件只按 example 核对差异，再运行该服务的 `settings sync`，不要覆盖。
三个任务相关 YAML（Web、Worker、Scheduler）必须使用同一个 `taskiq.namespace`、NATS 地址和 Redis TASKIQ 地址。
它们与两份 Core 接收服务的数据库、Auth User 模型也必须一致。
Worker 的 Storage 目录也要与 Web 指向同一位置；配置中的相对路径以项目根为准。
默认 namespace 是 `epg_demo`，NATS 为 `127.0.0.1:4222`，Redis TASKIQ 为 `127.0.0.1:6379/7`。
其他独立项目请改 namespace；它避免命名冲突，不是权限隔离。

在独立终端分别运行（Web 使用前面的启动命令）：

```bash
./run.sh task_worker start
./run.sh task_scheduler start
```

该结果页的“任务内 RPC”另外执行 `apps.examples.tasks.project_rpc`：Worker 调用共享
`query_project_status(..., "monitor_a")`，结果中同时展示接收服务和任务 Worker 的实际 PID。
先启动 nats_a，并在 Web 和 Worker 配置中开启 nats_bus；未启动接收服务时该任务失败，沿用原结果查询界面。
这个任务没有默认重试，不改变其他任务的依赖和行为；普通 Core 通信不需要启动 Worker。

`run.sh` 不会自动启动其他服务。只运行 Worker 就能执行普通任务；延迟、周期和业务重试还需要 Scheduler。
一个 namespace 只运行一个 Scheduler。两队列共用两个执行进程，每进程三个执行槽，总并发是六，不是十二。
任务中直接 `await project_summary(...)` 是当前任务内调用；`.kiq(...)` 才是另外入队，不会自动等待子任务结果。

先停止 Worker，再在页面投递摘要，能观察到“暂无保存结果”；重新启动 Worker 后查询会得到真实项目数据。
“暂无结果”不表示一定在运行，也可能已过期、未保存或无法访问。Taskiq 结果默认写入后 24 小时自动过期，读取不续期；
Demo 的用户归属记录从投递前开始计时，使用同样的 TTL，因此特别长的排队可能先失去页面查询权限。
结果查询只允许当前用户的任务；页面刷新不保留上一次结果区域，本示例不是持久任务控制台。
不保存结果的任务和广播查看 Worker 日志，不等待结果。导出只返回 Storage 逻辑文件名，文件位于
`media/task-exports/<user_id>/`，没有公开下载接口，也不自动删除导出文件。

重试示例第一次主动失败，五秒后才允许重试，实际还受 Scheduler 刷新和队列负载影响；最终结果中的 `attempts` 应为 2。
固定的 300 秒缓存刷新定义在装饰器 `schedule` 中，无数据库写入，修改代码后重启 Scheduler。
周期任务首次可能立即执行；取消计划不能撤回已经缓存或入队的执行。试完请取消自己添加的动态计划。
失败投递保留任务 ID：确认丢失时任务仍可能已经入队，不应盲目再次提交。
完成业务记录的防重示例只保护数据库状态变更，不保证文件写入或外部 API 副作用也只发生一次。

停止时使用正式入口，先停止新增调度，再让 Worker 收尾；到配置的停止期限后由框架终止整个进程组：

```bash
./run.sh task_scheduler stop
./run.sh task_worker stop
```

实现对应 `services/task_worker.py`、`services/task_scheduler.py`、`apps/examples/tasks.py`、
`apps/examples/views/tasks.py` 和 `templates/pages/examples/tasks/`。页面只复用现有 Form 和有序 Response Actions，没有专用 TS、SSE 或任务状态表。

### Redis 缓存示例

登录后从侧栏“缓存 → Redis 缓存”进入 `/examples/cache/redis`。本页查询已有
`ExampleProject` 表，显示各状态的项目数量和 UTC 计算时间，不写入项目数据。

- **读取统计**：未命中时查数据库，保存 30 秒 JSON 快照；命中时不查询项目表，也不延长 TTL。
- **重新计算**：立即查数据库并覆盖快照，重新计时。可以先在 HTML/JSON Table 示例中编辑一个项目，再回来看变化。
- **清除本例缓存**：只删除本例的一个 key，下次读取会重新计算，不清库。

页面刚打开不查询统计；没有项目时会缓存并显示零结果。所有 staff 用户共享同一份统计，
因此清除操作也影响其他用户的下一次读取。CRUD 不自动更新本例缓存；它演示的是允许短暂陈旧的统计。
Redis 或数据库故障会走公共错误提示，不静默伪装成成功。

示例使用 `redis.CACHE.redis_url`，公开配置默认为 `redis://localhost:6379/2`。
已有本地配置请核对该别名的地址，尤其不要把测试配置指向生产库。
业务实现见 `apps/examples/cache_example.py`，路由见 `apps/examples/views/cache.py`，
模板见 `templates/pages/examples/cache/`；浏览器复用普通 ExamplesPage 和 `data-om-action`，没有专用 TS。

同页下方的 **HTTP 响应缓存** 是独立示例：读取按钮 GET `/examples/cache/response`，
`cache_response` 缓存整份 JSON action 响应 5 秒，再读时计算时间不变；过期再读重新查询。
“失效响应缓存”按钮 POST 同一路径，由装饰器清理其全部查询参数/语言版本，再读得到新时间。
这些按钮不修改数据库，也不清除上方的 30 秒统计值缓存。

这两个装饰器使用根 cache.client/namespace；公开 YAML 没有覆盖 cache，默认 client 为 CACHE。GET 先检查
staff，缓存按当前语言隔离，不含个人数据、CSRF 或 Cookie；POST 仍须通过 CSRF。
装饰器缓存读写故障会 warning 并继续业务，数据库失败则正常报错。这与上方直接 RedisCache
操作失败不自动降级的示例不同，不能把降级误称为命中。

### 后端 HTTP 示例

登录后从“HTTP → 后端 HTTP 客户端”进入 `/examples/http/client`。
浏览器请求 Demo，Python 使用框架 `MultiHttpClient` 联系上游；不是浏览器直接访问第三方。

- **读取 JSON**：发送固定演示参数，显示真实响应、状态与耗时。
- **观察上游 404**：显示上游的 404，不将它混同为 Demo 路由不存在。
- **观察超时**：请求延迟 10 秒的接口，本例最多等待 5 秒；若公共上游提前返回，就显示实际结果，不伪造超时。
- **读取字节流**：请求 65536 字节，Python 逐块统计和计算 SHA-256，不保存文件。
  浏览器只显示最终结果，不是 SSE 进度；超过 1 MiB 的流会被关闭。

上游默认 `https://httpbin.org`，仅点击时访问。它能看到服务器出口地址、Demo User-Agent 和固定参数，
不会收到浏览器登录 Cookie、用户输入或数据库记录。公共服务可能不可达或限流；需要稳定环境时，
在已有 `data/web_settings.yaml` 中将 `app_settings.examples.http_base_url` 改为自己部署的兼容实例地址。
此设置只接受 HTTP(S) 基地址，可以带路径前缀，但不能带账号、密码、查询参数或 fragment。
旧配置未填写时使用默认值；可通过现有 `./run.sh web settings sync` 补齐，不需要创建另一套配置。

本例不重试、不跟随重定向，保留 TLS 验证。网络失败在结果区域明确展示；Demo 返回诊断片段的
HTTP 200 不等于上游成功。Demo 自身的权限、CSRF 或程序异常仍由公共错误链路处理。
所有用户复用本服务 worker 的无用户凭据客户端，由 `services/web.py` 初始化和关闭，不在每次请求结束时关池。

实现见 `apps/examples/http_example.py`、`apps/examples/views/http.py`、
`templates/pages/examples/http/`；设置类型见 `apps/examples/settings.py`。
页面使用现有 ExamplesPage、`data-om-action` 和 `replace_html_response`，没有新增 TS 或后台服务。

### 验证入口

```bash
.venv/bin/python scripts/verify-template-preview.py
.venv/bin/python scripts/run-python-tests.py
pnpm --dir frontend typecheck
pnpm --dir frontend test
pnpm --dir frontend build
.venv/bin/python scripts/verify-notifications-browser-with-server.py --browser chrome
.venv/bin/python scripts/verify-notifications-browser-with-server.py --browser firefox
.venv/bin/python scripts/verify-dashboard-browser-with-server.py
```

浏览器脚本会启动隔离的 Redis、数据库和服务，只清理自己创建的进程与临时文件。完整 Dashboard 验收使用真实 Chrome；通知接入同时覆盖 Chrome 和 Firefox。重任务应按上面的顺序执行，不要并发运行。

## 翻译

后端翻译命令从仓库根运行：

```bash
./run.sh i18n extract
./run.sh i18n update
./run.sh i18n compile
```

Python、Jinja、CLI 和前端只维护这一套 `messages.po`。`pnpm --dir frontend build`
会通过 `scripts/compile_js_messages.py` 从同一份 PO 生成浏览器 JSON；`.mo` 和 JSON
只是同一翻译的两种运行产物，不再维护 `js_messages.po`。

## 对照框架文档

- [用户教程与示例索引](https://github.com/alexliyu7352/oldman/blob/master/docs/users/README.md)：按本仓库真实数据和页面逐步操作。
- [开发者参考](https://github.com/alexliyu7352/oldman/blob/master/docs/developers/README.md)：接口、配置、同步/异步与资源生命周期。
- [Agent 应用开发指南](https://github.com/alexliyu7352/oldman/blob/master/docs/agents/README.md)：按需求找到本 Demo 的具体文件，完成注册、接线和验证。

使用与框架源码和 Demo 提交相配的文档；上述仓库链接不表示尚未推送的本地改动已经发布，也不表示当前构建已完成生产部署验收。
