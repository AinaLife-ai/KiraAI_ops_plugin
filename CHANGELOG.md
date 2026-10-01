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

### 图标（第六轮）

- **侧边栏图标**：不再用 Element Plus 的 `Monitor`（太通用，且已被其它插件占用），
  改为插件自带 SVG `assets/icon.svg`（`PageMenu.icon` 支持 SVG 文件路径，v2.34.5+）。
  造型是**圆角面板 + 仪表盘指针**——"控制台/仪表"语义，与 Monitor 完全不同；
  用 `currentColor` 描边，明暗主题与高亮态自动适配。
  选型过程：做了 9 版候选（控制台窗/提示符/六边形/仪表盘/二合一…），
  全部渲染成 16/18/20/24/32px 在明暗两种底色下对比，淘汰了 16px 下会糊掉的方案，
  最后按"18px 菜单实际尺寸"定稿。
- **插件图标（最终）**：采用用户自备的立绘（银白侧马尾 · 白/深蓝科技外套 · 黑过膝袜 · 坐在发光
  OPS 面板上），裁正方形 → **512×512 PNG** 写入 `assets/icon.png`，`manifest.icon` 指向它。
  验证：`/api/plugins/kira_ops/icon` → **200 image/png (247 KB)**，`PluginInfo.icon` 正确解析。
  原图与 256/512 版本归档在共享区 `kira_ops_icons_prompts/chosen/`。
- 过程中共产出 **62 张候选**（Agnes 2.5 Flash，含 2K 档），全部保留在
  `/var/minis/attachments/kira_ops_icons/`；其中 **47–54 的提示词已归档**到共享区
  `kira_ops_icons_prompts/`（含纯角色段、专属设计手法、逐字节一致的原始请求体、256px 缩略图）。
- 新增回归测试：菜单图标 SVG 必须随插件存在、不能被路径穿越、必须是 SVG 且带 `currentColor`。

### 多实例实测（第五轮：按"每个实例一份完整目录树"的真实部署形态）

多实例的真实形态是**每个实例各自一份完整的 KiraAI 目录树**（KiraAI5 / KiraAI7 / KiraAI9 / ...），
各有自己的 `data/`。框架的 `get_root_path()` 取的是**进程 CWD**，所以只要从自己的目录启动，
路径天然隔离。这一轮实测了两件事：

- **隔离性（真并行跑两个实例）**：两个进程各在自己的树里跑配置写入、备份、读取、回滚 ——
  各自只写自己的文件，互不干扰；B 实例能正确识别出"随文件夹复制过来的"旧回滚点（47 条）。
- **★ 复制目录树带来的跨实例覆写（严重）**：
  回滚点原本记录的是**绝对路径**。整套复制（KiraAI9 → KiraAI10）之后，
  在新实例点"回滚"实际改写的是**老实例的文件**——实测：在 B 里 restore，
  `restored=['.../kira_fw/data/config/system_config.json']`（A 的文件），A 的配置被改回，
  而 **B 自己毫无变化**（用户会以为"回滚没生效"，其实旧实例被悄悄改了）。
  修法：快照改为记录**相对 data 目录**的 `origin_rel`（恢复时按当前实例解析），
  同时保留绝对 `origin` 供排查；老格式（绝对路径）若指向另一棵 KiraAI 树则**拒绝写入**并说明原因，
  列表里也标出 `foreign`（面板显示"⚠ 其它实例"）。
  普通外部文件（不在任何树里的路径）仍然可以正常回滚。
- **同目录双开**（误双击同一份）：实测无损坏——审计 6110 行 0 坏、快照 243 个 0 重名、
  配置仍可解析；但配置写入是"最后写入者胜"，且框架的写文件是
  `open("w")` 先截断再写（3763 次并发读中出现 15 次读到 0 字节窗口）。
  正常部署（各自独立目录）完全不受影响。

### 模糊测试 + 真实浏览器端到端（第四轮）

**模糊测试**（`fuzz_tools.py`，固定种子 2000 次调用，参数从"空串/超长/Unicode/控制字符/
路径穿越/超大整数/嵌套 dict/None/bool"里随机生成）：

- **抓到一个真问题：工具层参数没做类型转换。** `ops_status(include=123)`、`ops_store(action=True)`、
  `ops_read(action={})` 这类调用直接 `AttributeError`（`.split()`/`.strip()` 打在了 int/bool/dict 上），
  实测 **2000 次里有 177 次异常逃逸**。现在所有字符串参数统一走 `_text()` 强制转换，
  布尔/数字也给出稳定表示；复跑 **2000/2000 全部通过**（0 异常、0 空错误信息、0 超尺寸返回）。
- 顺带：审计的自由文本字段（`target`/`err`/`note`/`sid`）也加上 300 字符上限
  （模糊测试里一个 `target` 写出了 4 kB 的行）。

**真实浏览器端到端**（真 FastAPI 服务器 + 真 JWT 鉴权 + 真 WebView 点击）：

- 面板在真浏览器里加载、渲染、交互全部正常：9 张卡片读到真实计数、7 个页签切换、
  33 个输入框绑定、保存热生效（改 `persona_write` + `audit.max_age_days` → 落盘校验通过）、
  全锁/解锁往返一致、备份**强制回滚**成功、中英切换整页生效、
  审计表 50 行渲染、**0 个 JS 错误、4 个接口全部 200**。
- **抓到 3 个只有真浏览器才能发现的问题**：
  1. **警告重复显示两遍**（`/config` 与 `/overview` 都带 warnings，前端 concat 后没去重）；
  2. 保存成功后不刷新，服务端重算的告警要等下次手动重载才更新；
  3. 数字框里输入非法值时，前端会把 `NaN`/空值当成 `0` 写回配置（静默重置成默认值），
     现在改成"空/非法 = 不动这一项"。
- **最值钱的一条**：我自己在修 1、2、3 时引入了一个 JS 语法错误（`continue` 写在了
  `forEach` 回调里）——Python 测试、桩件、接口契约比对全绿，**页面直接白屏**。
  只有真浏览器立刻暴露它。现在 `node --check` 与元素/字段/接口路径交叉校验
  已固化成 `tests/test_panel_contract.py`（7 项，含反向验证：注入该语法错误后精确变红）。

### 安全与一致性（第三轮：对抗性审查，非测试套件驱动）

这一轮不依赖已有测试，改用「能读到什么 / 并发会怎样 / 失败留下什么 / 契约是否对得上」
四类探针去打，抓到下面这些：

- **严重 · `log.read_file` 曾是「data/ 下任意文件读取」**：`config.get` 有打码，但同一个会话可以
  用 `read_file` 直接把 `data/config/system_config.json`（明文 `api_key`）和
  `data/memory/chat_memory.json`（全部聊天记录）读进上下文，**打码形同虚设**。
  现在该动作**只允许读日志**（文件名匹配 `*.log` / `log.log*`），并叠加 `path_deny_read` 检查。
- **严重 · 打码盲区**：`Authorization` / `Bearer` / `Cookie` 不在关键词表里，于是
  `mcp.info` 会把 `headers: {"Authorization": "Bearer …"}` 明文吐给模型。
  现在新增**内置底线 `ALWAYS_MASK`**（api_key/authorization/bearer/cookie/secret/password/
  credential/token/private_key），**配置里删不掉**，读打码与写禁止都生效。
- **严重 · 一个畸形 session id 能永久写坏 `chat_memory.json`**：
  `session.title` 等写动作原先接受任意 target，写进一个不合法的 key 之后，
  框架 `SessionManager.get_session_info()`（它按 `:` 切分并取第 3 段）会**永久抛 IndexError**，
  连带**内置 session_tools 插件**与 WebUI 的会话枚举一起挂掉（含内存态的 `session.list` 与面板计数）。
  现在：① 写入/查询前校验 `adapter:type:id`；② `session.list` 走容错枚举（回落到原始 store），
  并把脏键列在 `malformed_keys` 里；③ `session.delete` 是唯一接受畸形 id 的动作，
  专门用来**修复历史脏数据**（1.0.0 部署可能存在）。
- **中 · 并发同名快照会互相覆盖**：框架同一轮会并行跑工具，快照又在工作线程里执行，
  5 个并发同名快照实测只生成 3 个目录，`_meta.json` 被后写者覆盖 ⇒ 回滚点丢失。
  现在目录用 `exist_ok=False` **原子占位** + 线程锁，重试加后缀。
- **中 · 写失败仍把备份标成 `applied`**：语义反了（没生效的写被记成已应用）⇒ 只在成功时标记。
- **中 · 审计 `args` 不截断**：一条 200 键的配置写入实测记了 **43,180 字符**，
  审计文件与后续 `audit.tail` 都被拖累 ⇒ 上限 2000 字符 + 400 字符预览。
- **中 · 回滚备份目录放在 `data/plugins/` 里**：框架启动会遍历该目录并尝试加载，
  一次崩溃残留就会在插件列表里冒出一个「加载失败」的假插件 ⇒ 移到 `data/temp/kira_ops_rollback/`。
- **低 · 模型传畸形参数会抛异常**：`args` 传字符串/数组、`limit` 传 `"abc"` 会直接 ValueError
  ⇒ 新增 `to_int()` 与「非 dict args 一律忽略」，全部动作对畸形输入免疫。
- **低 · 错误信息可能为空**（`fetch_remote_models failed: `）⇒ 异常插值统一用 `{exc!r}`。

**审计方法（可复现）**：`adversarial_probe.py`（读取逃逸 / 并发 / 状态残留）、
`action_matrix.py`（12 域 × 全部动作在真实框架上逐个真跑，含高危令牌流程）、
面板↔API 契约比对、框架 API 存在性核对。
**结论**：全动作矩阵 **0 异常、0 缺错误信息**；面板用到的字段与 API 路径 100% 对齐；
并发压测无损坏记录。

### 返回体精简与格式（同一批修复内的第二轮）

- **工具返回值改为紧凑 JSON**：框架构建 tool 消息时会 `str(result)`，所以原来模型看到的是
  Python 字典的 repr（`{'ok': True, ...}`，每个 `:` 和 `,` 后面都带空格、布尔是 `True`/`None`）。
  现在工具统一返回 `Payload`（dict 子类，`__str__` = 紧凑 JSON），实测**整体再省 ~7%**，
  而且是合法 JSON（`true`/`null`），内部与测试仍按 dict 使用。
- **列表类去掉重复键、只回必要字段**：`truncated` 仅在真被截断时出现；
  `backup.list` 不再回绝对 `path`（`id` 就是回滚句柄，`hint` 里也带）；
  `mcp.list` 省略空的 `disabled_tools` 与 0 的 `tools`；
  `plugin.info` 的 `description` 截 120 字符、去掉 `manifest_keys`。
- **大对象默认只给摘要、要明细显式开**：
  `provider.models` 默认只回 `{类型: [模型ID]}`（`args={"full": true}` 才给完整配置，实测 −42%）；
  `session.info` 的 `capabilities` 默认只回 `{能力: 是否启用}`，有会话级覆盖时额外给 `overrides`
  （`args={"full": true}` 才给完整树，实测 −36%）。
- 实测同一批 34 次代表性调用：**9,405 → 8,383 字符（−11%）**；重灾区降幅更大
  （backup.list −37%、plugin.info −38%、session.info −36%、provider.models −42%）。
- 仍然**保留 JSON 结构**：列式/TSV 之类的格式还能再省 10~13%，但值里会含分隔符与换行
  （日志、描述、错误串），需要转义规则；一次误读要多花一整轮上下文，得不偿失。设计文档 §5 有完整对比。

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

### 测试（47 → 109 项）

- 新增 `tests/test_integration_real.py`（**48 项**）：**用真实框架对象**（KiraConfig /
  DatabaseService / ProviderManager / FuncToolManager / PersonaManager / SessionManager /
  MCPManager / SkillsManager / PluginManager）加载插件并逐域驱动，
  覆盖 12 域全部只读动作 + 上述每一条修复，含「更新后子模块必须是新代码」和
  「恶意压缩包必须被拒绝」「工具结果必须是紧凑 JSON」三条契约级断言。
  其余三套旧测试全是桩件（FakeCtx/FakeMcpMgr/...）——桩件能证明逻辑，
  **证明不了契约**，class-vs-instance 与陈旧子模块两个 bug 正是这样漏过去的。
- `tests/test_kira_ops.py` 17 → **21 项**（新增 `apply_limit` / 日志截断 / brief 裁剪 / 能力名校验）。
- 集成套件新增「回滚点绝不写入别的实例」断言（含把回滚点伪造成另一棵树的反向验证）。
- 新增 `tests/test_panel_contract.py`（**9 项**，含菜单 SVG 与 manifest 图标的资产校验）：面板静态契约（JS 可解析、元素 id/页签/
  API 路径/配置路径/载荷字段交叉校验、表格行字段），用 node --check + 正则自动跟随代码演进。
- 集成套件 40 → **48 项**：新增 `read_file` 范围、打码底线、并发快照唯一性、审计体积上限、
  畸形参数免疫、畸形 session id 拒写与脏键修复（含对历史脏数据的自动修复断言）。
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
