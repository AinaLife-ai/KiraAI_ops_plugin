# Changelog

## 1.1.0

全面审计（对照 KiraAI **v2.34.8** 真实源码 + 真实框架运行期）后的修复与优化版本。
所有修复都有「反向验证」：新增测试在 1.0.0 代码上精确变红，在本版本上转绿。

### 严重（会谎报成功 / 会爆上下文 / 不生效）

- **修复：原地更新后旧子模块仍在运行（谎报成功）**
  `store/installer.py` 之前只做「移动目录 + `load_plugin_from_dir()`」，
  没有调用框架的 `prepare_plugin_reload()`；多文件插件因此只重新执行 `main.py`，
  `caps/`、`core/`、`store/` 仍是缓存里的旧代码（框架自己的 WebUI 更新流程是先调它的）。
  复现：v1 插件换成 v2 后子模块常量仍是 1（框架路径是 2）。
  ⇒ 安装/更新现在走「框架安装器 → `prepare_plugin_reload` → `load_plugin_from_dir`」，
  并**校验加载后的 plugin_id 一致**，失败即报错（不再只 warning）。
- **修复：安装包没有任何大小/条目/压缩比限制**
  改为**复用框架官方安装器** `core/plugin/plugin_installer.py`
  （50 MiB / 10000 条目 / 100:1 压缩比 / 512 KiB 中央目录 / Zip-Slip 防护，
  GitHub 镜像自动测速、`commit_sha` 固定版本）。自研的下载/解压/测速代码全部删除；
  直链安装仍保留 https-only + 私网地址拦截 + `max_bytes`。
  技能安装（框架没有对应安装器）复用同一组阈值，写在自己的受保护解压器里。
- **新增：更新失败自动回滚**：更新前把插件目录整体备份，新的加载失败则恢复旧目录并重新激活。
- **修复：`master.enabled=false` 不生效 + 日志说谎**
  框架在 `initialize()` 之后无条件注册插件工具，所以原来那句
  "tool surface not registered" 是假的，`ops_status` 也照常可用。
  现在 `ops_status` 走 enabled 闸门，日志改为如实说明（工具仍注册，由权限引擎全拒；
  要从提示词里拿掉请在插件列表里禁用）。
- **修复：返回体可爆上下文**
  - `log.tail` / `log.search`：单条消息默认截断到 400 字符（尾部标注 `…(+N chars)`），
    默认条数 25、上限 100。修复前 30 条 × 4000 字符 = **121,896 字符**（≈6 万 token）。
  - `config.get` 不带 path：改为返回**顶层概况**（键名/类型/规模），不再整棵系统配置
    （40 个 Provider 实测 43,877 字符）；带 path 时超过 12,000 字符会截断并提示再往下指定。
  - `log.read_file`：limit 补上下限（500 ~ 20,000），之前只设下限，可传 1e8 读整个文件。

### 高

- **修复：agent 域谎报"可用"**：`is_plugin_enabled()` 对未知 id 默认返回 True，
  于是 agent 插件没装时 `agent.get` 也回 `plugin_enabled: true`。
  现在统一 `has_plugin(pid) and is_plugin_enabled(pid)`，并新增 `installed` 字段；
  `ops_status` 里那句"agent 插件未启用"的提示也从死代码变成真正按安装状态判断。
- **修复：`core_version` 声明过宽**：`>=2.33.1` → **`>=2.34.6`**
  （实际用到 v2.34.0 的 multi_select 动态源、v2.34.4 的 MCP 单工具开关、
  v2.34.5 的内置 agent 插件、v2.34.6 的 `download_file(max_bytes=)`）。
- **修复：manifest/README 指向不存在的仓库** → `https://github.com/znq19/KiraAI_ops_plugin`；
  作者改为 `AinaLife-ai +znq19`。

### 中

- **修复：`ops_read` 查询不存在的会话会凭空创建会话**（读动作产生副作用，污染 `chat_memory.json`）。
  现在先判存在性，未知会话直接报 `session not found`；
  `session.memory_count` 同样处理。
- **实现：第三方扩展点 `kira_ops.register_capability`**（此前 README / 设计文档 / caps docstring 都承诺了，
  但代码里没有）。现在真的订阅该自定义事件，带完整校验：必须是 `Capability` 子类、
  `name` 合法且未被占用、`ACTIONS` 形状正确，否则拒绝并写警告。
- **实现：`ops_status` 的 `detail=brief/full`**（此前是只回显的空参数）。
  `brief`（默认）把长列表裁成「前 5 条 + 总数 + truncated」，`full` 给完整明细。
- **修复：被拒操作未留痕**：`ops_read` 的权限拒绝、`ops_confirm` 的令牌失败现在都写审计
  （`kind="deny"`，不受 `audit_reads` 开关影响）；令牌从 32 bit 提到 64 bit。
- **修复：`limit` 在多数 list 上无效**：plugin/skill/provider/mcp/session/persona/backup 统一支持
  `limit`，并额外返回 `total` / `truncated`。
- **修复：`provider.models` 对不存在的 Provider 返回 ok + 空**（`models is None` 是死判断）。
  现在先判 Provider 是否存在，并给出各类型模型数量。
- **修复：生命周期句柄依赖私有实现**：`app.state.lifecycle` 在现行框架里永远是 None
  （框架只设了 `lifecycle.webui_app`，没有反向引用）。新增第三条解析路径
  （WebUI 路由对象上的 lifecycle），加缓存，并在全部失败时**告警一次**，
  让"重启/关机为什么拒绝执行"可见。

### 低 / 打磨

- `mcp.tool_toggle` 补 try/except：框架对未知工具抛 ValueError，之前会打全栈日志；
  现在返回可读错误。
- 删除死动作键 `config.set_secret`（schema options + 设计文档 + `redact.py` docstring 都提到，
  但 config 域只有 get/set；敏感字段是硬拦，改密钥请走 KiraAI 界面），
  并在高危清单 hint 里建议把 `agent.set` 也勾上。
- 审计的拒绝记录统一为 `kind="deny"`（之前借用 `kind="write"`，语义含糊）。
- 备份/安装的同步文件操作改走 `asyncio.to_thread`，不再阻塞事件循环；
  `ops_status` 与面板改用 `BackupManager.count()`（不再为了一个数字去读几百个 `_meta.json`）。
- 启动告警（高危名单为空 / 人设只读）改成"每条只告警一次"，避免每次改配置刷屏。
- 面板：支持中英切换（跟随 `ctx.get_lang()`，标题栏可手动切换）、
  概览新增 agent 插件状态卡片、备份一键回滚支持 `force` 勾选。
- 新增 `requirements.txt`；`schema.json` 文案补充说明。
- 文档：README 与设计文档全面修订（仓库地址、作者、core_version、安全说明、
  安装/更新与回滚流程、扩展点用法、测试清单）。

### 测试（47 → 90 项）

- 新增 `tests/test_integration_real.py`（**39 项**）：**用真实框架对象**（KiraConfig /
  DatabaseService / ProviderManager / FuncToolManager / PersonaManager / SessionManager /
  MCPManager / SkillsManager / PluginManager）加载插件并逐域驱动，
  覆盖 12 域全部只读动作 + 上述每一条修复，含「更新后子模块必须是新代码」和
  「恶意压缩包必须被拒绝」两条契约级断言。
  其余三套旧测试全是桩件（FakeCtx/FakeMcpMgr/...）——桩件能证明逻辑，
  **证明不了契约**，class-vs-instance 与陈旧子模块两个 bug 正是这样漏过去的。
- `tests/test_kira_ops.py` 17 → **21 项**（新增 `apply_limit` / 日志截断 / brief 裁剪 / 能力名校验）。
- 反向验证：把新测试跑在 1.0.0 代码上，**16 项精确变红**。

## 1.0.0

首个正式版本。

### 工具（合并式，共 7 个）

- `ops_status`：运行总览 + 自检 + 告警（resources/plugins/providers/mcp/sessions/store/audit/permission）
- `ops_read`：12 个域的只读明细（plugin/skill/provider/mcp/session/persona/config/log/store/backup/agent/control）
- `ops_config`：写配置（config/plugin/agent 三个入口，黑名单 + 密钥保护）
- `ops_action`：生命周期动作（启停/重载/安装/更新/卸载/增改删/同步/恢复/重启/关机）
- `ops_store`：插件商店搜索 / 安装 / 更新 / 源信息
- `ops_confirm`：高危动作一次性确认令牌（会话+用户绑定，用后即焚，默认 300s）
- `ops_panic`：紧急全锁（写操作立即降级为只读）

### 权限（三旋钮 + 黑名单，fail-closed）

- 允许名单**留空 = 放行所有**；禁止名单优先级最高；只读名单限制写。
- 档位：readonly / standard（默认）/ dangerous / full。
- 高危动作清单可勾选可扩展；**高危会话名单必填、不继承，留空 = 无人可执行**。
- 数据保护：读取打码、写入拦截敏感字段、关键路径读写删守卫（任何域都绕不过）。
- 重启 / 关机拆为两个独立开关，默认关闭；执行需 full + 开关 + 令牌三重条件。

### 备份 / 审计 / 回滚

- 写动作前自动快照（纯代码，不依赖 LLM），专用目录 + 自动清理（份数/天数/总量）。
- 回滚前校验目标是否被外部改动，冲突需显式 force。
- 回滚点"搭车"写进工具返回，无需额外消息。
- 全量审计 JSONL（写动作 + 被拒操作必记，读操作可选）。

### 商店接管

- 默认接管旧独立商店插件（检测到已启用即关闭、不卸载），能力全量融合。
- 保留代理自动测速、失败剔除、SSRF 防护、Zip-Slip 防护、staging 安装。

### WebUI 面板

- 全设置热改热重载；概览卡片、备份列表一键回滚、审计查看、全锁开关。

### 通用化

- 不含任何具体 QQ 号 / 群号 / 目录名 / 人设名；路径全部由框架解析，面向所有 KiraAI 部署。

### 自测

- 47 项离线测试全过：
  - 核心 17 项：权限引擎全链路、打码与写保护、确认令牌、备份快照/冲突/清理、
    导入冒烟（7 工具 + API + 页面注册）、12 域注册表。
  - 运行期 14 项：桩上下文真实驱动 ops_status / ops_read 六域 / ops_config 拒密钥 /
    高危令牌与会话绑定校验 / ops_panic / 审计落盘 / 商店搜索容错。
  - 运行期 16 项：provider 五条路径、persona 三条（含 persona_write 开关在令牌之前拦截）、
    mcp 三条、config 写入、agent 策略路径守卫、control 开关与令牌门。

### 修复

- 能力类被当作实例调用导致列表类接口全挂（`handle_read() missing 1 required positional
  argument: 'params'`）。改为经由 `_cap()` 取实例，并去掉会妨碍热重载的全局注册缓存。
- 补 `Capability.preflight()`：像"人设写入被开关关闭"这类前置拒绝，现在会在发放确认令牌
  **之前**就否掉，避免"给你令牌但确认后照样失败"的尴尬。
