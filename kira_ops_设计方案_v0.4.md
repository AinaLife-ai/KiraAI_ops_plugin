# Kira Ops Console 设计方案 v0.4（定稿候选）

> 设计人：爱理奈 · 宿主：KiraAI v2.34.8
> v0.3 → v0.4 变更：
> ① **`high_risk_sessions` 改为必填、不继承**（留空 = 无人可执行高危动作；杜绝"白名单里的人顺带拿到高危权"）
> ② **重启 / 关机拆成两个独立开关**（`allow_restart` / `allow_shutdown`），**默认双关**
> 状态：待点头开 P0

---

## 0. 本轮改动摘要

| # | 意见 | v0.4 结论 |
|---|---|---|
| 1 | 高危会话留空不该继承默认黑白名单，应必填 | `risk.high_risk_sessions` **必填、无继承**；空 = 高危动作全禁用（fail-closed）。判定链第 6 步重写 |
| 2 | 重启和关机拆成两个选项，都默认关 | 新增 `control.allow_restart` / `control.allow_shutdown`，**默认 false / false**；两者独立，且**不随 level=full 自动放开** |

---

## 1. 定位（通用版）

给 **KiraAI 系 bot** 一个**框架自控台**：用最少的工具、最短的链路，把「插件 / 技能 / Provider / MCP / 会话 / 人设 / 配置 / 日志 / 商店」暴露成可读可写、可热载、可开关、可删除、可回滚的能力；权限只用三个旋钮说清；破坏性动作自动备份；全程留审计。

- 插件 ID：`kira_ops` ｜ 显示名：Kira 运行自控台
- **不针对任何特定 bot 人设**：谁装谁用，默认值中立，命名与文案不含个人数据
- 宿主能力探测：拿不到某 manager（旧版框架）→ 对应 domain 自动降级为「不可用」并在 `ops_status` 提示，不报错崩

---

## 2. 工具设计（7 个，合并式）

### 2.1 原则

- **一次调用取回一整屏**：省一轮 = 省整份上下文（最大的 token 优化）
- schema 越短越好（每轮都在请求里）→ 参数少、枚举承载语义
- 统一返回体 `{"ok":bool,"data":...,"hint":"...","backup":"..."}`
- `brief`（默认）只回摘要，`full=true` 才回明细
- 新增能力**不增加工具数**，只加 `domain`/`action` 枚举

### 2.2 清单

| 工具 | 参数 | 覆盖 |
|---|---|---|
| `ops_status` | `include`(默认 resources,plugins,providers) `detail` | 总览 + 自检 + 冲突提示 |
| `ops_read` | `domain` `action`(list/info/read/search/grep/tail) `query` `limit` `keyword` | 全部只读细节 |
| `ops_config` | `domain` `target` `patch`(json) `confirm` | 全部配置写（过保护策略） |
| `ops_action` | `domain` `action` `target` `args`(json) `confirm` | 生命周期：enable/disable/reload/refresh/install/update/uninstall/delete/create/set_active/sync/health/export/backup/restore |
| `ops_store` | `action`(search/install/update/sources) `keyword` `plugin_id` `source` | 插件商店（接管原插件） |
| `ops_confirm` | `token` | 确认破坏性操作 |
| `ops_panic` | `lock`(true/false) | 一键全锁 / 解锁 |

**`domain` 短枚举**：`plugin` `skill` `provider` `mcp` `session` `persona` `config` `log` `store` `agent` `control`

**明确不做**：`ops_file` / `ops_exec` —— 文件与命令由宿主内置 `agent` 插件执行，本插件只在 `domain=agent` **托管其策略**（不重复实现工具、不增 token；权限收敛到控制台一处配；agent 被关时 `ops_status` 提示「文件/命令能力不可用」）

> 原 17 个工具全部并入上表。**17 → 7**。

### 2.3 Cap 注册表（表驱动，可扩展）

```python
# caps/registry.py
class Capability:
    name: str
    def read(self, action, params) -> dict: ...
    def write(self, action, params) -> dict: ...
    def dangerous(self, action) -> bool: ...
    def describe(self) -> dict: ...          # 面板自动渲染

REGISTRY: dict[str, type[Capability]] = {}
def register(cls): REGISTRY[cls.name] = cls
```

域与方法：`plugin`(list/info/config_get/config_set/enable/disable/reload/install/update/uninstall) `skill`(list/info/refresh/enable/disable/scope/install/export/remove) `provider`(list/info/models/add_model/update_model/delete_model/set_provider/sync/health/fetch_remote) `mcp`(list/info/add/update/delete/enable/disable/tool_toggle/scope) `session`(list/info/title/caps/memory_clear/memory_count/delete/scope_clean) `persona`(list/info/get_active/set_active/create/update/delete) `config`(get/set) `log`(tail/grep/history) `store`(search/install/update/sources) `agent`(get/set) `control`(restart/shutdown)

第三方可注册：`@on.custom_event(event_name="kira_ops.register_capability")` —— 新增能力不碰核心代码

---

## 3. 权限设计（3 个旋钮 + 黑名单 + 高危必填）

### 3.1 配置

```jsonc
"access": {
  "allow_sessions": [],          // 允许名单：★留空 = 放行所有
  "deny_sessions": [],           // 黑名单：★优先级最高，命中直接拒
  "readonly_sessions": []        // 名单内会话强制只读；留空 = 不额外限制
},

"risk": {
  "level": "standard",           // readonly | standard | dangerous | full
  "high_risk_actions": [         // ★真正的高危动作（可勾选/可增删，不按工具粒度）
    "plugin.uninstall","plugin.disable","skill.remove","provider.delete_model",
    "provider.update_model","mcp.delete","session.delete","session.memory_clear",
    "persona.create","persona.update","persona.delete","persona.set_active",
    "config.set_secret","store.install","store.update","backup.restore",
    "file.delete","file.overwrite","exec.write"
  ],
  "high_risk_sessions": [],      // ★必填、无继承：留空 = 无人可执行高危动作（见 §3.4）
  "require_confirm": true,
  "confirm_ttl": 300
},

"control": {                     // ★重启/关机独立开关，默认双关，不随 level=full 放开
  "allow_restart": false,
  "allow_shutdown": false
},

"protected": {                   // 数据保护：优先级最高，任何域（含 agent）都绕不过
  "read_mask": ["api_key","access_token","secret","password","credential","token","private_key"],
  "write_deny": ["api_key","access_token","secret","password","credential","private_key"],
  "write_allow": ["base_url","api_base","endpoint","model_id","model_name","models",
                  "timeout","temperature","headers","enabled","remark","display_name"],
  "path_deny_read":   ["data/webui.json","data/.jwt_secret","data/data.db"],
  "path_deny_write":  ["core","webui","main.py","data/memory","data/webui.json","data/.jwt_secret"],
  "path_deny_delete": ["core","webui","data/memory","data/plugins","data/skills"],
  "persona_write": false
}
```

### 3.2 档位

| level | 放行 |
|---|---|
| `readonly` | 只读域全开，一切写拒 |
| `standard`（**默认**） | 只读 + 全部非高危写：配置热改、插件启停/热重载、技能刷新/启停、Provider 模型增改（非敏感字段）、MCP 启停、会话标题、商店搜索、**agent 策略读写** |
| `dangerous` | + 高危清单动作（仍需 confirm 令牌 **且** 会话在 `high_risk_sessions` 里） |
| `full` | 全开 **但重启/关机仍受 `control` 双开关约束** |

### 3.3 判定链（固定顺序，fail-closed）

```
1. master.enabled 且 非 panic_lock            → 否则退化为只读
2. deny_sessions 命中                          → 拒（最优先）
3. allow_sessions 非空 且 未命中                → 拒；为空 → 放行
4. readonly_sessions 命中 且 动作是写            → 拒
5. protected.* 命中（读掩码/写禁列/路径禁）       → 拒 ★任何域都拦得住
6. 动作 ∈ high_risk_actions ？
     是 → 需 level ∈ {dangerous, full}
          ★且 会话 ∈ high_risk_sessions（空名单 = 直接拒，不继承任何人）
          且 require_confirm → 有有效令牌
7. 动作 ∈ {control.restart, control.shutdown} ？
     是 → 需 control.allow_restart / allow_shutdown 为 true（默认 false）
          且 level = full，且需 confirm  ← 三重条件，缺一不可
8. 执行 → 写前快照备份 → 审计
```

### 3.4 为什么高危名单必填、不继承（v0.4 关键改动）

- 继承 = "允许用工具的人"顺带获得"删插件 / 清记忆 / 换人设"的权力，等于**高危没保护**
- 留空 = 显式声明「本机不给任何会话高危权」，这是 **fail-closed** 的正确姿势
- 面板上该项标注「必填」并给红色提示，未填时 `ops_status` 报**告警**（不报错，日常功能不受影响）
- 三件事仍然只有三件：**谁能用（白+黑）** + **开到什么档** + **谁有高危权（显式）**

---

## 4. 备份（不依赖 LLM、不污染、自动清理）

```
data/plugin_data/kira_ops/
  backups/
    20260930-041530_<target>_<reason>/
      <原文件副本>
      _meta.json      # {target, reason, tool, session, user, created_at, md5}
  audit/audit-YYYY-MM-DD.jsonl
  pending.json
```

- 专用目录，**不放 `data/temp`**（防被 VLM 描述 / 被 `<file>` 外发 / 被 temp 清理器扫掉）
- 写动作**前**同步落盘，纯代码，不做智能判断，不走 LLM
- `_meta.json` 存 md5；回滚时校验目标是否被外部改动，有则要求 `force`
- 自动清理（纯代码，写动作后顺手跑，无常驻定时器）：`keep_last:20 / max_age_days:7 / max_total_mb:200 / cleanup_on_start:true`
- **搭车告知**：不额外发消息、不占轮次，写成功返回体自带 `backup.{id,path}` + `hint`

---

## 5. 缓存与信息层级（不破前缀缓存）

| 信息 | 放哪 | 理由 |
|---|---|---|
| 能力清单 / 工具用法 | **不注入**，写进工具 description + Cap `describe()` | 零常驻成本 |
| 系统现状、审计、日志 | 只在 `ops_status` / `ops_read` 被调用时返回 | 按需 |
| 高危确认提示 | 工具返回体 | 按需 |
| 档位 / panic / **高危名单未填告警** | 仅异常态往 `chat_env` 追 1 行 | `chat_env` 属动态段，被挪到最新 user 消息，**不动 system 前缀** |
| 插件/技能变更通知 | `@on.custom_event` → 需要时才注 `chat_env` | 用完即弃 |

**硬规则**：①默认零 system 段注入（不碰 persona/tools/memory 稳定段）②必须注入只进 `chat_env` 或 `persist=False` 的 user 前缀消息 ③`@on.llm_request` 里禁止 I/O（信息一律懒加载到工具）④工具返回默认 brief ⑤不注入大表格，只给计数 + 按需明细。

---

## 6. 通用化（面向发布，全 KiraAI 系 bot）

- `schema.json` **不含任何**具体 QQ 号 / 群号 / 模型名 / 目录名 / 人设名
- 会话名单用 `multi_select(source=session, allow_custom=true)`，默认 `[]`
- 根目录经 `get_data_path()` 动态解析，不写死宿主目录名
- 不假设宿主 bot 有特定人格、不引用任何具体 bot 名称；文档举例用占位（`<bot>` / `<owner>`）
- 面板文案走 `locales`（中/英），可扩展
- "主人/所有者"概念**不做硬编码**，只作为名单里的可填项
- 交付物：`README.md`（安装/配置/能力表/回滚）、`CHANGELOG.md`、`manifest.json`（`core_version>=2.34.8`）、面板 `web/`
- 兼容：缺失 manager 自动降级；新增 domain 只加文件不破坏旧行为

---

## 7. 无斜杠命令

- 删除 `/ops`、`/store` 等一切斜杠命令与白名单
- 交互只有两条路：**LLM 工具** + **WebUI 面板**

---

## 8. 与市场插件的关系（完全接管 + 互斥）

```python
CONFLICT_PLUGINS = ("plugin_store_search",)      # 常量表，非个人数据

def conflicts(self) -> list[str]:
    pm = getattr(self.ctx, "plugin_mgr", None)
    if not pm or not hasattr(pm, "has_plugin"):
        return []
    return [pid for pid in CONFLICT_PLUGINS
            if pm.has_plugin(pid) and pm.is_plugin_enabled(pid)]

async def initialize(self):
    if self.settings.takeover_store and self.conflicts():
        for pid in self.conflicts():
            await self.ctx.plugin_mgr.set_plugin_enabled(pid, False)
            logger.info(f"[kira_ops] 已接管并关闭互斥插件: {pid}")
```

- **不卸载**，只关闭（`set_plugin_enabled(pid, False)`，实机已核该方法存在且会 `terminate`）
- 开关 `takeover_store` 默认 true；关掉则两不相干
- 接管后能力融合：数据源/缓存/代理测速/zip 安全检查（大小·条目·压缩比·Zip-Slip）/staging 安装/待确认删除 → 分别落 `store.sources`、`ops_store`、全局 `ops_confirm` 令牌池；斜杠命令删除；其 5 个工具归并

---

## 9. agent 域：策略托管（可写 + 保护优先）

- `read(action=get)` → 返回 agent 插件 `file_access` / `exec_access` 现状
- `write(action=set)` → 写回 agent 插件配置（`update_plugin_config("agent", ...)`，触发其重载）
- **可写 ≠ 能绕保护**：任何写先过 §3.3 第 5 步 `protected` —— `write_deny` 字段、`path_deny_*` 路径，agent 域照样改不动、读不到
- 高危映射：`file.delete` / `file.overwrite` / `exec.write` 进 `high_risk_actions`
- 面板可直接编辑 agent 会话名单、额外读写路径、命令黑名单 —— **用户只在一处配**
- `ops_status` 检测 agent 插件未启用 → 提示「文件/命令能力不可用」
- 预留 `strict_mode`：开启后若 agent 的 ACL 比控制台策略更宽，`ops_status` 给**告警**（不强行改别人配置）

---

## 10. control 域：重启 / 关机（拍板 7 落地，默认双关）

| 开关 | 默认 | 生效条件 |
|---|---|---|
| `control.allow_restart` | **false** | `allow_restart=true` **且** level=`full` **且** confirm 令牌 |
| `control.allow_shutdown` | **false** | `allow_shutdown=true` **且** level=`full` **且** confirm 令牌 |

- 两者**互不牵连**：可以只允许重启、不许关机
- 两个动作**不进** `high_risk_actions`（因为条件更严：开关 + full + confirm 三重），面板上单独一块展示，避免和其他高危混在一起被人一键放开
- `ops_action(domain=control, action=restart|shutdown)` 调用前返回体已带 `hint` 说明"这是不可逆操作"

---

## 11. 审计

- `data/plugin_data/kira_ops/audit/audit-YYYY-MM-DD.jsonl`
- 每条：`{ts, sid, uid, tool, domain, action, target, args_masked, ok, err, ms, backup}`
- 写动作 + 被拒操作**必记**；读动作由 `audit_reads` 控制（默认关，省体积）
- 按日切分 + `max_age_days` 自动清理

---

## 12. 结构与配置

```
data/plugins/kira_ops/
  manifest.json      # core_version>=2.34.8 / repo / locales / tags
  schema.json        # 7 个 section：master / access / risk / control / protected / backup / store
  main.py            # 7 工具 + 异常态 chat_env 注入 + 页面/API 注册 + 互斥
  caps/              # 见 §2.3
  core/              # permission / paths / fields / confirm / audit / backup / redact
  store/             # store_client / installer
  web/               # 面板
```

**面板全热改热重载**：所有 section 走 `@register.api` 读写，写后调 `plugin_mgr.update_plugin_config("kira_ops", ...)`（实机已核：写完即 `init_plugin` 重载实例）→ 配置项**全部热生效**；面板支持全量改配置 + 一键回滚 + 审计查看。内存态落 `plugin_data`，重载不丢。`high_risk_sessions` 标必填 + 未填告警。

---

## 13. 分阶段

| 阶段 | 内容 |
|---|---|
| P0 | 骨架 + 权限引擎（白/黑/只读 + 档位 + 必填高危名单 + 保护）+ 备份 + 审计 + `ops_status` / `ops_read(config/log)` |
| P1 | `ops_config` + `ops_action(plugin/skill)` + confirm 令牌 + 技能热载 |
| P2 | `domain=provider/mcp/session/persona` + 保护策略全生效 |
| P3 | `domain=agent` 策略托管 + 状态告警 |
| P4 | `ops_store` 接管 + 互斥 + `domain=control`（默认关） |
| P5 | WebUI 面板（全热改） |
| P6 | 自测 + 破坏性测试 + README/使用手册（通用版） |

---

## 14. 已核实的活对象（实机验证，无臆造）

```
ctx.message_processor.skills_manager   → SkillsManager（scan_skill_dir / set_skill_enabled / set_skill_scope / skills_info / skills_dir）★技能热载不用改框架
ctx.plugin_mgr                         → list / get_info / get_config / update_plugin_config / set_plugin_enabled / reload / uninstall_plugin
ctx.provider_mgr                       → get_all_providers / register_model / update_model / delete_model / sync_models / health_check
ctx.session_mgr                        → get_session_info / update_session_info / update_session_capabilities / delete_session
ctx.persona_mgr                        → get_persona / update_persona / set_active_persona / list_personas
ctx.message_processor.mcp_manager      → add_or_update_server_from_config / delete_server / set_tool_enabled / set_server_scope
互斥范式                                → alife_memory_z.conflicts() + plugin_mgr.set_plugin_enabled()
```
