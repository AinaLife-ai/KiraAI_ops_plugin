/* Kira Ops Console panel logic - vanilla JS, talks to the plugin API. */
(function () {
  'use strict'
  try {
    var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  } catch (e) { /* ignore */ }
  var ctx = null
  var cfg = {}
  var warnings = []

  function $(sel) { return document.querySelector(sel) }
  function el(tag, cls, html) {
    var n = document.createElement(tag)
    if (cls) n.className = cls
    if (html !== undefined) n.innerHTML = html
    return n
  }
  function token() {
    try { return localStorage.getItem('jwt_token') } catch (e) { return null }
  }
  function api(path, body) {
    var base = '/api/plugin/kira_ops' + path
    var headers = { 'Content-Type': 'application/json' }
    var t = token()
    if (t) headers['Authorization'] = 'Bearer ' + t
    return fetch(base, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: headers,
      body: body === undefined ? undefined : JSON.stringify(body || {})
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status)
      return r.json()
    })
  }
  function toast(msg) {
    var t = $('#toast')
    t.textContent = msg
    t.classList.remove('hide')
    clearTimeout(toast._h)
    toast._h = setTimeout(function () { t.classList.add('hide') }, 2600)
  }

  // ---------------------------------------------------------------
  // form helpers: cfg is a nested object; inputs bind to a dotted path
  // ---------------------------------------------------------------

  function getPath(obj, path) {
    var parts = path.split('.')
    var cur = obj
    for (var i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== 'object') return undefined
      cur = cur[parts[i]]
    }
    return cur
  }
  function setPath(obj, path, value) {
    var parts = path.split('.')
    var cur = obj
    for (var i = 0; i < parts.length - 1; i++) {
      if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) cur[parts[i]] = {}
      cur = cur[parts[i]]
    }
    cur[parts[parts.length - 1]] = value
  }

  function fieldText(path, label, hint, kind) {
    var wrap = el('label')
    wrap.appendChild(el('span', 'k', label))
    var input = el('input', '', '')
    input.type = kind === 'number' ? 'number' : 'text'
    var v = getPath(cfg, path)
    input.value = v == null ? '' : v
    input.dataset.path = path
    input.dataset.kind = kind || 'text'
    wrap.appendChild(input)
    if (hint) wrap.appendChild(el('span', 'hint', hint))
    return wrap
  }

  function fieldSwitch(path, label, hint) {
    var wrap = el('label', 'switch')
    var input = el('input', '', '')
    input.type = 'checkbox'
    input.checked = !!getPath(cfg, path)
    input.dataset.path = path
    input.dataset.kind = 'switch'
    wrap.appendChild(input)
    wrap.appendChild(el('span', 'k', label))
    if (hint) wrap.appendChild(el('span', 'hint', hint))
    return wrap
  }

  function fieldList(path, label, hint) {
    var wrap = el('label')
    wrap.appendChild(el('span', 'k', label))
    var ta = el('textarea', '', '')
    var v = getPath(cfg, path) || []
    ta.value = Array.isArray(v) ? v.join('\n') : String(v)
    ta.dataset.path = path
    ta.dataset.kind = 'list'
    wrap.appendChild(ta)
    if (hint) wrap.appendChild(el('span', 'hint', hint))
    return wrap
  }

  function fieldSelect(path, label, options, hint) {
    var wrap = el('label')
    wrap.appendChild(el('span', 'k', label))
    var sel = el('select', '', '')
    options.forEach(function (o) {
      var op = el('option', '', o)
      op.value = o
      sel.appendChild(op)
    })
    sel.value = getPath(cfg, path) || options[0]
    sel.dataset.path = path
    sel.dataset.kind = 'select'
    wrap.appendChild(sel)
    if (hint) wrap.appendChild(el('span', 'hint', hint))
    return wrap
  }

  function collect() {
    var patch = {}
    document.querySelectorAll('[data-path]').forEach(function (n) {
      var path = n.dataset.path
      var kind = n.dataset.kind
      var value
      if (kind === 'switch') value = !!n.checked
      else if (kind === 'number') value = n.value === '' ? 0 : Number(n.value)
      else if (kind === 'list') value = n.value.split('\n').map(function (s) { return s.trim() }).filter(Boolean)
      else value = n.value
      setPath(patch, path, value)
    })
    return patch
  }

  // ---------------------------------------------------------------
  // render
  // ---------------------------------------------------------------

  function renderWarnings() {
    var box = $('#warnings')
    if (!warnings || !warnings.length) { box.classList.add('hide'); return }
    box.classList.remove('hide')
    box.innerHTML = warnings.map(function (w) { return '⚠ ' + w }).join('<br>')
  }

  function renderCards(overview) {
    var box = $('#cards')
    box.innerHTML = ''
    var counts = overview.counts || {}
    var perm = overview.permission || {}
    var rows = [
      ['插件', (counts.plugins_enabled || 0) + '/' + (counts.plugins || 0)],
      ['技能', counts.skills || 0],
      ['Provider', counts.providers || 0],
      ['MCP', counts.mcp || 0],
      ['会话', counts.sessions || 0],
      ['当前档位', perm.level || '-'],
      ['待确认', counts.pending_confirms || 0],
      ['备份', counts.backups || 0]
    ]
    rows.forEach(function (r) {
      var c = el('div', 'card')
      c.appendChild(el('b', '', String(r[1])))
      c.appendChild(el('span', '', r[0]))
      box.appendChild(c)
    })
    if (overview.conflicts && overview.conflicts.length) {
      var c2 = el('div', 'card')
      c2.appendChild(el('b', '', String(overview.conflicts.length)))
      c2.appendChild(el('span', '', '被接管的商店插件'))
      box.appendChild(c2)
    }
  }

  function renderAccess() {
    var pane = $('#pane-access')
    pane.innerHTML = ''
    var fs = el('fieldset')
    fs.appendChild(el('legend', '', '会话名单（允许名单留空 = 放行所有；黑名单优先）'))
    fs.appendChild(fieldList('access.allow_sessions', '允许名单 allow_sessions', '每行一个会话 ID，如 qq:gm:123456；留空=放行所有'))
    fs.appendChild(fieldList('access.deny_sessions', '禁止名单 deny_sessions', '命中直接拒绝，优先级最高'))
    fs.appendChild(fieldList('access.readonly_sessions', '只读名单 readonly_sessions', '名单内会话只能读，写操作一律拒绝'))
    pane.appendChild(fs)

    var fs2 = el('fieldset')
    fs2.appendChild(el('legend', '', '总闸'))
    fs2.appendChild(fieldSwitch('master.enabled', '启用插件'))
    fs2.appendChild(fieldSwitch('master.panic_lock', '全锁（紧急只读）'))
    pane.appendChild(fs2)
  }

  function renderRisk() {
    var pane = $('#pane-risk')
    pane.innerHTML = ''
    var fs = el('fieldset')
    fs.appendChild(el('legend', '', '风险分级'))
    fs.appendChild(fieldSelect('risk.level', '能力档位', ['readonly', 'standard', 'dangerous', 'full'],
      'readonly=只读；standard=常规（默认）；dangerous=高危需确认；full=全开'))
    fs.appendChild(fieldList('risk.high_risk_actions', '高危动作清单', '每行一个动作键，如 plugin.uninstall'))
    fs.appendChild(fieldList('risk.high_risk_sessions', '高危会话名单（必填，不继承）',
      '只有名单内的会话能执行高危动作；留空 = 无人可执行（安全默认）'))
    fs.appendChild(fieldSwitch('risk.require_confirm', '高危动作需确认令牌'))
    fs.appendChild(fieldText('risk.confirm_ttl', '确认令牌有效期（秒）', '默认 300', 'number'))
    pane.appendChild(fs)
  }

  function renderControl() {
    var pane = $('#pane-control')
    pane.innerHTML = ''
    var fs = el('fieldset')
    fs.appendChild(el('legend', '', '重启与关机（默认双关，互不牵连）'))
    fs.appendChild(fieldSwitch('control.allow_restart', '允许重启 KiraAI', '仍需 full 档位 + 确认令牌'))
    fs.appendChild(fieldSwitch('control.allow_shutdown', '允许关闭 KiraAI', '仍需 full 档位 + 确认令牌'))
    pane.appendChild(fs)
  }

  function renderProtected() {
    var pane = $('#pane-protected')
    pane.innerHTML = ''
    var fs = el('fieldset')
    fs.appendChild(el('legend', '', '数据保护（任何域都绕不过）'))
    fs.appendChild(fieldList('protected.read_mask', '读取打码字段关键词'))
    fs.appendChild(fieldList('protected.write_deny', '禁止写入字段关键词'))
    fs.appendChild(fieldList('protected.write_allow', '允许写入字段白名单'))
    fs.appendChild(fieldList('protected.path_deny_read', '禁止读取路径'))
    fs.appendChild(fieldList('protected.path_deny_write', '禁止写入路径'))
    fs.appendChild(fieldList('protected.path_deny_delete', '禁止删除路径'))
    fs.appendChild(fieldSwitch('protected.persona_write', '允许修改人设'))
    pane.appendChild(fs)

    var fs2 = el('fieldset')
    fs2.appendChild(el('legend', '', '备份与审计'))
    fs2.appendChild(fieldSwitch('backup.enabled', '启用自动备份'))
    fs2.appendChild(fieldText('backup.keep_last', '每目标保留份数', '', 'number'))
    fs2.appendChild(fieldText('backup.max_age_days', '最长保留天数', '', 'number'))
    fs2.appendChild(fieldText('backup.max_total_mb', '总大小上限（MB）', '', 'number'))
    fs2.appendChild(fieldSwitch('backup.cleanup_on_start', '启动时清理旧备份'))
    fs2.appendChild(fieldSwitch('audit.enabled', '启用审计'))
    fs2.appendChild(fieldSwitch('audit.audit_reads', '记录读取操作'))
    fs2.appendChild(fieldText('audit.max_age_days', '审计保留天数', '', 'number'))
    pane.appendChild(fs2)
  }

  function renderStore() {
    var pane = $('#pane-store')
    pane.innerHTML = ''
    var fs = el('fieldset')
    fs.appendChild(el('legend', '', '插件商店'))
    fs.appendChild(fieldSwitch('store.takeover_store', '接管插件商店（互斥，检测到原插件即关闭它）'))
    fs.appendChild(fieldText('store.store_url', '商店数据源URL'))
    fs.appendChild(fieldText('store.github_proxy', 'GitHub 加速代理（留空自动测速）'))
    fs.appendChild(fieldText('store.request_timeout', '请求超时（秒）', '', 'number'))
    fs.appendChild(fieldText('store.cache_ttl', '列表缓存秒数', '', 'number'))
    fs.appendChild(fieldText('store.max_results', '单次搜索结果上限', '', 'number'))
    pane.appendChild(fs)
  }

  function renderBackups() {
    var pane = $('#pane-backup')
    pane.innerHTML = ''
    var fs = el('fieldset')
    fs.appendChild(el('legend', '', '自动备份（写动作前的快照）'))
    var table = el('table')
    table.innerHTML = '<thead><tr><th>回滚点</th><th>时间</th><th>目标</th><th>文件</th><th></th></tr></thead>'
    var tbody = el('tbody')
    table.appendChild(tbody)
    fs.appendChild(table)
    pane.appendChild(fs)
    api('/backups').then(function (res) {
      (res.items || []).forEach(function (b) {
        var tr = el('tr')
        tr.innerHTML = '<td>' + b.id + '</td><td>' + (b.created || '') + '</td><td>' +
          (b.label || '') + '</td><td>' + (b.files || 0) + '</td>'
        var td = el('td')
        var btn = el('button', 'ghost', '回滚')
        btn.onclick = function () {
          if (!confirm('确定要恢复 ' + b.id + ' 吗？')) return
          api('/backups/restore', { id: b.id }).then(function (r) {
            toast(r.ok ? '已回滚' : ('回滚失败: ' + (r.error || '需要 force')))
          })
        }
        td.appendChild(btn)
        tr.appendChild(td)
        tbody.appendChild(tr)
      })
      if (!tbody.children.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="muted">暂无备份</td></tr>'
      }
    }).catch(function (e) { toast('读取备份失败: ' + e.message) })
  }

  function renderAudit() {
    var pane = $('#pane-audit')
    pane.innerHTML = ''
    var fs = el('fieldset')
    fs.appendChild(el('legend', '', '审计（最近记录）'))
    var table = el('table')
    table.innerHTML = '<thead><tr><th>时间</th><th>域</th><th>动作</th><th>结果</th><th>说明</th></tr></thead>'
    var tbody = el('tbody')
    table.appendChild(tbody)
    fs.appendChild(table)
    pane.appendChild(fs)
    api('/audit?limit=50').then(function (res) {
      (res.items || []).slice().reverse().forEach(function (r) {
        var tr = el('tr')
        tr.innerHTML = '<td>' + (r.ts || '') + '</td><td>' + (r.domain || '') + '</td><td>' +
          (r.action || '') + '</td><td>' + (r.ok ? '✓' : '✗') + '</td><td>' +
          ((r.err || r.note || '')).toString().slice(0, 60) + '</td>'
        tbody.appendChild(tr)
      })
      if (!tbody.children.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="muted">暂无记录</td></tr>'
      }
    }).catch(function (e) { toast('读取审计失败: ' + e.message) })
  }

  async function load() {
    var overview = await api('/overview')
    var conf = await api('/config')
    cfg = conf.config || {}
    warnings = (conf.warnings || []).concat(overview.warnings || [])
    $('#ver').textContent = overview.version ? ('v' + overview.version) : ''
    renderWarnings()
    renderCards(overview)
    renderAccess()
    renderRisk()
    renderControl()
    renderProtected()
    renderStore()
    renderBackups()
    renderAudit()
  }

  function save() {
    var patch = collect()
    api('/config', { config: patch }).then(function (r) {
      if (r.ok) {
        toast('已保存并热生效')
        cfg = r.config || cfg
      } else {
        toast('保存失败: ' + (r.error || '未知错误'))
      }
    }).catch(function (e) { toast('保存失败: ' + e.message) })
  }

  function panicToggle() {
    var locked = !!getPath(cfg, 'master.panic_lock')
    api('/panic', { lock: !locked }).then(function (r) {
      if (r.ok) {
        toast(r.panic_lock ? '已全锁（只读）' : '已解除全锁')
        setPath(cfg, 'master.panic_lock', r.panic_lock)
        load()
      }
    })
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.tab').forEach(function (tab) {
      tab.onclick = function () {
        document.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active') })
        document.querySelectorAll('.pane').forEach(function (p) { p.classList.remove('active') })
        tab.classList.add('active')
        var pane = $('#pane-' + tab.dataset.tab)
        if (pane) pane.classList.add('active')
      }
    })
    $('#btnSave').onclick = save
    $('#btnReload').onclick = function () { load().then(function () { toast('已重载') }) }
    $('#btnPanic').onclick = panicToggle
    load().catch(function (e) { toast('加载失败: ' + e.message) })
  })
})()
