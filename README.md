# Kira 运行自控台（kira_ops）

给 **KiraAI** 用的运行自控台插件：让你（或者你的 AI）用一个统一入口，看懂并管理整个 KiraAI 的运行状态——
插件、技能、Provider、MCP、会话、人设、系统配置、日志、插件商店，全都能查、能改、能热生效，
而且**该拦的都拦得住、改坏了能回滚、干了什么都留痕**。

面向所有 KiraAI 部署，不绑定任何特定群、特定用户或特定目录，装上就能用。

---

## 一、它能干什么（30 秒版）

| 能力 | 说明 |
| --- | --- |
| 看状态 | 插件/技能/Provider/MCP/会话数量、启用情况、加载错误、权限档位、告警 |
| 管插件 | 启停、热重载、改配置、从商店安装/更新/卸载（带备份） |
| 管技能 | 刷新、启停、会话范围、查看 SKILL.md、安装/删除 |
| 管 Provider | 查看（密钥打码）、增改删模型、同步远端模型、健康检查 |
| 管 MCP | 增改删服务器、启停、单工具开关、会话范围 |
| 管会话 | 标题、能力覆盖、记忆条数、清记忆、删除（联动清理技能/MCP 范围） |
| 管人设 | 查看列表与正文、切换激活（改内容默认只读，可放开） |
| 改配置 | 系统配置与插件配置热修改，敏感字段一律拒绝写入 |
| 看日志 | 最近日志、关键词搜索、日志文件概况、按关键词读文件尾部 |
| 插件商店 | 搜索/安装/更新，自动选择最快的 GitHub 加速代理 |
| 重启/关机 | 两个**独立开关**，默认都关，开启后还要"full 档位 + 确认令牌" |
| 文件/命令 | **不重复实现**：只托管内置 `agent` 插件的文件/命令策略，执行仍走 agent 插件 |
| 自保机制 | 自动备份 + 一键回滚、审计留痕、全锁（紧急只读）、数据保护 |

---

## 二、三步上手（小白版）

### 第 1 步：确认插件在跑

1. 打开 KiraAI 的 WebUI（默认 `http://127.0.0.1:5267`）。
2. 左侧进入「插件」，找到 **Kira 运行自控台（Kira Ops Console）**，确认是「启用」状态。
3. 如果看到「冲突处理」提示：本插件会自动把旧的独立商店插件（plugin_store_search）**关闭**
   （不会删除），避免功能重复。想保留旧插件互不干扰，就去本插件面板把「接管插件商店」关掉。

### 第 2 步：配置"谁能用、能用到什么程度"（重要！）

左侧进入插件自带的「**运行自控台**」面板（或点插件卡片上的入口），主要看三块：

1. **访问控制**
   - `允许名单`：**留空 = 所有会话都能用**；想收紧就填会话 ID（如 `qq:gm:123456`）。
   - `禁止名单`：优先级最高，填进去的会话一律拒绝。
   - `只读名单`：名单内的会话只能看，不能改。
2. **风险分级**
   - `能力档位`：默认 `standard`（日常读 + 常规写，够用）。想更保守改 `readonly`。
   - `高危动作清单`：哪些操作算"高危"（删插件、删模型、清记忆等），默认已列好，可增删。
   - `高危会话名单`：**必填、不继承**——只有名单里的会话才能执行高危动作；**留空 = 谁都不能执行**（安全默认）。
   - `确认令牌`：高危动作要先拿令牌再确认，默认 300 秒有效，防止手滑。
3. **重启与关机**：两个开关默认都关。真要开，还得切 `full` 档位 + 拿令牌才能执行。

> 面板里改任何设置，**保存即热生效**，不用重启 KiraAI。

### 第 3 步：开始用

直接对你的 AI 说人话就行，例如：

- 「看看系统状态」→ AI 调用 `ops_status`
- 「列出所有插件，看看有没有加载失败的」→ `ops_read(domain=plugin)`
- 「把 xxx 插件停用/重载」→ `ops_action(domain=plugin, action=disable/reload)`
- 「帮我搜一下商店里有没有语音插件」→ `ops_store(action=search, keyword=语音)`
- 「把某个 Provider 的模型列表拉下来同步一下」→ `ops_action(domain=provider, action=sync)`
- 「回滚刚才那次配置修改」→ `ops_read(domain=backup)` 找到回滚点 → `ops_action(domain=backup, action=restore)`

高危动作的流程：AI 第一次调用会拿到一个**确认令牌**，再调 `ops_confirm(token=...)` 才真正执行。

---

## 三、安全设计（为什么可以放心）

- **三旋钮权限**：谁能用（允许/禁止/只读名单）＋ 档位（readonly/standard/dangerous/full）＋
  高危动作清单。判定顺序固定、失败即拒绝（fail-closed）：
  禁止名单最优先 → 允许名单 → 只读 → 档位 → 高危门槛 → 数据保护。
- **高危必填**：`高危会话名单` 留空时，**任何会话都不能执行高危动作**——这是故意的，
  防止"能用工具的人"顺带获得删插件/清记忆的权力。面板里会看到这条告警，不是故障。
- **数据保护**（任何域都绕不过，包括 agent 策略）：
  - 读取时对 `api_key`、`token`、`secret` 等字段自动打码；
  - 禁止写入敏感字段（改密钥请走 KiraAI 官方界面）；
  - `core/`、`webui/`、记忆库等关键路径禁止读写删。
- **自动备份**：每个写动作执行前，自动把目标文件快照到
  `data/plugin_data/kira_ops/backups/`，保留份数/天数/总大小都可配，自动清理。
  改坏了用 `ops_action(domain=backup, action=restore, target=回滚点ID)` 一键恢复。
  写成功的返回值里会"搭车"带上回滚点路径。
- **审计**：所有写动作和被拒操作，记录到 `data/plugin_data/kira_ops/audit/audit-日期.jsonl`。
- **全锁**：`ops_panic(lock=true)` 一键把全部写操作降级成只读，紧急刹车用。
- **重启/关机**：独立开关 + full 档位 + 确认令牌，三重条件；实现走的是 KiraAI 官方同款
  进程退出流程（supervisor 自动拉起）。

---

## 四、常见问题（FAQ）

**Q：AI 说"高危动作被拒绝"？**
A：三种可能：档位不是 dangerous/full；高危会话名单没加这个会话；没带确认令牌。
按提示补齐即可（高危名单填好后立即生效）。

**Q：我想改人设，为什么被拒？**
A：人设默认只读。到面板「数据保护」里打开「允许修改人设」，并且需要 dangerous 档位 + 确认令牌。

**Q：为什么搜到的旧商店插件被关了？**
A：本插件默认"接管"旧商店插件（只关不删）。不需要接管就把「接管插件商店」关掉。

**Q：备份在哪？会占空间吗？**
A：`data/plugin_data/kira_ops/backups/`。默认每目标保留 20 份、最长 7 天、总量 200MB，
自动清理；全都可以在面板调。

**Q：文件/命令呢？**
A：本插件**不重复实现**。文件读写、命令执行由 KiraAI 内置的 `agent` 插件负责；
本插件提供 `agent` 域来统一查看和修改它的访问策略（还能阻止把受保护路径加进白名单），
避免"两个地方配权限"的混乱。

**Q：怎么卸载？**
A：WebUI → 插件 → Kira Ops Console → 卸载。建议先看一眼 README 里的注意事项：
它不会动你的插件数据；`data/plugin_data/kira_ops/` 可留作存档，也可手动删。

**Q：面板打不开/接口 404？**
A：确认插件「启用」，然后点一次「重载」。插件页面由框架用 no-store 提供，无需清缓存。

---

## 五、给开发者

- 新增一个能力域：在 `caps/` 里加一个文件，继承 `Capability`（声明 `name` 和 `ACTIONS`），
  在 `caps/__init__.py` 的 `load_all()` 里补一行 import 即可——**工具数量永远不变**（7 个）。
- 第三方插件想扩展本控制台：发 `kira_ops.register_capability` 自定义事件即可接入
  （预留接口，文档见代码注释）。
- 自测（共 **47** 项，全部离线可跑）：
  - `python data/plugins/kira_ops/tests/test_kira_ops.py` — 17 项：权限引擎全链路、
    打码与写保护、确认令牌、备份快照/冲突/清理、导入冒烟、12 域注册表。
  - `python data/plugins/kira_ops/tests/test_live_ops.py` — 14 项：用桩上下文真实驱动
    ops_status / ops_read（plugin·skill·backup·log·config·session）/ ops_config 拒密钥 /
    高危令牌与会话绑定 / ops_panic / 审计落盘 / 商店搜索。
  - `python data/plugins/kira_ops/tests/test_live_ops2.py` — 16 项：provider（列表/详情打码/
    加模型拒密钥/加模型放行/远端拉取）、persona（读 + 开关拦截 + 令牌放行）、mcp（列表/详情/
    高危删除需令牌）、config 写入、agent 策略路径守卫、control 开关与令牌门。
  - `python data/plugins/kira_ops/tests/validate_schema.py` — schema 与 core_version 校验。

```
kira_ops/
  main.py          # 7 个工具 + 面板 API + 冲突接管 + 异常态提示
  caps/            # 能力域（plugin/skill/provider/mcp/session/persona/
                   #           config/log/store/agent/control/backup）
  core/            # 权限引擎 / 保护 / 备份 / 审计 / 确认令牌 / 路径守卫
  store/           # 商店客户端 + 安装器（代理测速 / SSRF 防护 / Zip-Slip 防护）
  web/             # WebUI 面板（全设置热改）
  tests/           # 离线自测
```

---

## 六、版本与兼容

- 框架要求：`core_version >= 2.33.1`（技能/ MCP 管理器自该版本可用；内置 `agent` 插件自 2.34.5 起提供，缺失时相关域自动降级并给出提示）
- 版本：1.0.0
- 作者：AiriLife-ai ｜ 仓库：https://github.com/AiriLife-ai/kira_ops （发布用占位，可自行修改 manifest）
