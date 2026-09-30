# Changelog

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
