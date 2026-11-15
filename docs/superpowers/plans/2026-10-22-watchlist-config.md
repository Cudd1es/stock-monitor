# Watchlist 配置实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 `config.yaml`(全局设置)加 `watchlist.yaml`(每只股票的规则)取代"自然语言 → 全局规则",在同一个进程里同时运行盘中检查和日报,并提供一个用自然语言编辑 watchlist 的命令行工具。

**Architecture:** 新增 `settings.py` 负责读取、校验、写入和热加载两个文件。`monitor.py` 合并两类任务的排期,`AlertStore` 改为按股票各自的阈值计算。`main_workflow.py` 的图状态从 `rules` 改为 `settings + tickers + kind`。新增 `watchlist.py` 命令行:LLM 只产出操作列表,由纯函数 `apply_ops` 应用。

**Tech Stack:** Python 3、PyYAML、dataclasses、LangGraph、pytest(带 `monkeypatch`),运行环境是 `/Users/ansel/miniconda3/envs/stock`。

**Spec:** `docs/superpowers/specs/2026-10-22-watchlist-config-design.md`

## Global Constraints

- 测试命令:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest -q`。每个任务结束时全部测试都必须通过。
- 测试不能访问网络:yfinance、SEC、LLM 和 Discord 一律用 `monkeypatch` 替换。
- 默认值:`daily_brief="16:10"`,`intraday_minutes=None`,`notify_method="console"`,`report_language="zh"`,`alert_threshold=5.0`,`intraday=True`,`research=True`,`timezone="America/Toronto"`。
- `intraday_minutes` 的取值范围是 1 到 390;`notify_method` 只能是 `console` 或 `discord`;`report_language` 只能是 `en`、`zh`、`jp`。
- 每只股票只允许三个覆盖字段:`alert_threshold`、`intraday`、`research`。
- `watchlist.yaml` 的文件头注释:`# 由程序维护(python watchlist.py)。手动编辑也可以,但注释会在下次写入时丢失。`
- Agent 模式(`main_agent.py`)不改,所以 `core.load_discord_config` 要保留。
- commit message 不加 Co-Authored-By(用户要求)。
- 沿用仓库现有的代码风格:模块级函数、`[DEBUG]`/`[WARN]` 前缀的 print、简短的 docstring。

## Review Focus

1. **watchlist 为空**(`tickers: []`,或者删掉最后一只股票后变空):`load_watchlist` 应该接受空列表,监控在这一轮打印 `[MONITOR] watchlist is empty` 然后跳过,不抓数据也不推送。测试放在 Task 1 和 Task 4。
2. **YAML 类型混淆**:`alert_threshold: true`(Python 里 bool 是 int 的子类)、`alert_threshold: "8"`、`intraday: "no"` 都应该报错,不能被悄悄转换。测试放在 Task 1。
3. **带连字符的代码**:yfinance 用 `BRK-B` 表示 B 类股。现有的 `_normalize_tickers` 会把它清洗成 `BRKB`。新的 `normalize_symbol` 要保留 `-` 和 `.`。这里偏离了 spec 里"沿用现有清洗逻辑"的说法,是有意的修正。测试放在 Task 1。
4. **盘中没有可检查的股票**(全部是 `intraday: false`):这一轮不抓价格,也不推送。测试放在 Task 4。
5. **热加载时反复出现同一个错误**:每次检查都会读到同一个坏文件,但 `on_error` 只调用一次;文件修好之后再坏,应该再通知一次。测试放在 Task 2。

---

### Task 1: `settings.py`:数据模型、读取、写入

**Files:**
- Create: `settings.py`
- Create: `tests/test_settings.py`
- Create: `watchlist_example.yaml`
- Modify: `config_example.yaml`(改成 spec 里的新格式)
- Modify: `.gitignore`(加 `watchlist.yaml`,和 `config.yaml` 一样不提交)

**Interfaces:**
- Produces:
  - `normalize_symbol(raw: str) -> str`:去掉空白并转大写,只保留 `[A-Z0-9.-]`;清洗后为空时抛出 `ValueError`。
  - `@dataclass(frozen=True) TickerDefaults(alert_threshold: float = 5.0, intraday: bool = True, research: bool = True)`
  - `@dataclass(frozen=True) TickerRule(symbol: str, alert_threshold: float, intraday: bool, research: bool)`
  - `@dataclass(frozen=True) Settings(timezone: str, daily_brief: Optional[str], intraday_minutes: Optional[int], notify_method: str, report_language: str, defaults: TickerDefaults, discord_webhook: Optional[str], discord_mention_id: Optional[str])`
  - `load_settings(path: str = "config.yaml") -> Settings`
  - `parse_rules(entries: Any, defaults: TickerDefaults) -> List[TickerRule]`:校验 `tickers` 下的列表。
  - `load_watchlist(path: str, defaults: TickerDefaults) -> List[TickerRule]`
  - `dump_watchlist(rules: List[TickerRule], path: str, defaults: TickerDefaults) -> None`
  - `HEADER: str`:Global Constraints 里那行文件头注释。

- [ ] **Step 1: 写失败的测试 `tests/test_settings.py`**

用一个辅助函数 `write(tmp_path, name, text)` 写出 YAML 文件。测试如下:

```python
def test_settings_defaults_when_fields_missing(tmp_path):
    s = settings.load_settings(write(tmp_path, "config.yaml", "{}"))
    assert (s.timezone, s.daily_brief, s.intraday_minutes) == ("America/Toronto", "16:10", None)
    assert (s.notify_method, s.report_language) == ("console", "zh")
    assert s.defaults == settings.TickerDefaults(5.0, True, True)

def test_settings_reads_schedule_defaults_and_discord(tmp_path): ...
    # schedule: {daily_brief: "16:30", intraday_minutes: 10}, defaults: {alert_threshold: 3},
    # discord: {webhook_url: "https://hook", mention_id: "42"} → 对应字段相等

@pytest.mark.parametrize("text", [
    "schedule: {daily_brief: null, intraday_minutes: null}",
    "schedule: {daily_brief: '25:00'}",
    "schedule: {intraday_minutes: 0}",
    "schedule: {intraday_minutes: 391}",
    "notify_method: email",
    "report_language: fr",
    "defaults: {alert_treshold: 3}",   # 拼写错误
])
def test_invalid_settings_raise(tmp_path, text):
    with pytest.raises(ValueError):
        settings.load_settings(write(tmp_path, "config.yaml", text))

def test_watchlist_merges_defaults(tmp_path):
    # tickers: [{symbol: msft}, {symbol: TSLA, alert_threshold: 8}, {symbol: SPY, intraday: false, research: false}]
    assert rules == [TickerRule("MSFT", 5.0, True, True), TickerRule("TSLA", 8.0, True, True),
                     TickerRule("SPY", 5.0, False, False)]

def test_empty_watchlist_is_valid(tmp_path):
    assert settings.load_watchlist(write(tmp_path, "w.yaml", "tickers: []"), D) == []

def test_missing_watchlist_raises(tmp_path): ...   # 文件不存在 → ValueError,消息里包含 "watchlist.py"

@pytest.mark.parametrize("entry", [
    "{symbol: MSFT, colour: red}", "{alert_threshold: 3}", "{symbol: MSFT, alert_threshold: 0}",
    "{symbol: MSFT, alert_threshold: true}", "{symbol: MSFT, alert_threshold: '8'}",
    "{symbol: MSFT, intraday: 'no'}", "MSFT",
])
def test_invalid_entry_raises(tmp_path, entry): ...

def test_duplicate_symbol_raises(tmp_path): ...    # [{symbol: MSFT}, {symbol: msft}]

def test_normalize_symbol_keeps_share_class():
    assert settings.normalize_symbol(" brk-b ") == "BRK-B"
    assert settings.normalize_symbol("brk.b") == "BRK.B"

def test_dump_then_load_round_trips_and_writes_only_overrides(tmp_path):
    rules = [TickerRule("MSFT", 5.0, True, True), TickerRule("TSLA", 8.0, True, False)]
    settings.dump_watchlist(rules, path, D)
    text = path.read_text(encoding="utf-8")
    assert text.startswith(settings.HEADER)
    assert "alert_threshold: 5" not in text and "research: false" in text
    assert settings.load_watchlist(path, D) == rules
```

`D = settings.TickerDefaults()`。

- [ ] **Step 2: 运行测试,确认失败**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest tests/test_settings.py -q`
预期:因为 `ModuleNotFoundError: No module named 'settings'` 而失败。

- [ ] **Step 3: 实现 `settings.py`**

- 时间格式的校验沿用 `parser._is_valid_time` 的正则,把它复制过来,不要 import parser,因为 parser 会连带加载 LLM 客户端。
- bool 和数字的类型检查要严格:阈值必须是 `isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0`;两个开关必须是 `isinstance(x, bool)`。
- 校验失败统一抛出 `ValueError`,消息里写明是哪只股票、哪个字段。
- `dump_watchlist` 先写 `path + ".tmp"`,再调用 `os.replace`;同时创建父目录。用 `yaml.safe_dump(..., sort_keys=False, allow_unicode=True)`,条目顺序是 `symbol` 在前,然后是和默认值不同的字段。

`config_example.yaml` 改成 spec 里 `config.yaml` 的样例,保留 `discord` 和 `sec` 两段的说明注释。`watchlist_example.yaml` 用 spec 里的样例。

- [ ] **Step 4: 运行测试,确认通过**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest -q`
预期:全部通过。

- [ ] **Step 5: Commit**

```bash
git add settings.py tests/test_settings.py config_example.yaml watchlist_example.yaml .gitignore
git commit -m "Add settings module for config.yaml and watchlist.yaml"
```

---

### Task 2: `WatchlistReloader`:热加载

**Files:**
- Modify: `settings.py`
- Modify: `tests/test_settings.py`

**Interfaces:**
- Consumes: `load_watchlist`、`TickerDefaults`(Task 1)
- Produces: `WatchlistReloader(path: str, defaults: TickerDefaults, on_error: Callable[[str], Any] = print)`,带一个方法 `get() -> List[TickerRule]`。

- [ ] **Step 1: 写失败的测试**

```python
def test_reloader_picks_up_edits(tmp_path): ...
    # 第一次 get() 得到 [MSFT];改写文件为 [MSFT, NVDA];第二次 get() 得到两只

def test_reloader_keeps_last_good_and_reports_once(tmp_path):
    errors = []
    r = settings.WatchlistReloader(str(path), D, on_error=errors.append)
    good = r.get()
    path.write_text("tickers: [{symbol: MSFT, colour: red}]")
    assert r.get() == good and r.get() == good
    assert len(errors) == 1

def test_reloader_reports_again_after_recovering(tmp_path): ...
    # 坏 → 好 → 坏 ⇒ len(errors) == 2

def test_reloader_first_load_failure_raises(tmp_path):
    with pytest.raises(ValueError):
        settings.WatchlistReloader(str(tmp_path / "missing.yaml"), D).get()
```

- [ ] **Step 2: 运行测试,确认失败**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest tests/test_settings.py -q -k reloader`
预期:因为 `AttributeError: ... no attribute 'WatchlistReloader'` 而失败。

- [ ] **Step 3: 实现 `WatchlistReloader`**

记住上一份有效结果和上一次报过的错误消息。读取成功时清空"上一次报过的错误";读取失败时,只有错误消息和上一次不同,才调用 `on_error(f"[WARN][watchlist] {e}; still using the last valid watchlist")`。

- [ ] **Step 4: 运行测试,确认通过**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest -q`
预期:全部通过。

- [ ] **Step 5: Commit**

```bash
git add settings.py tests/test_settings.py
git commit -m "Hot-reload watchlist, keeping the last valid one on errors"
```

---

### Task 3: `monitor.py`:合并排期,按股票阈值去重

**Files:**
- Modify: `monitor.py`(`next_run_time` → `next_run`、`run_loop`、`AlertStore`)
- Modify: `core.py`(`find_alert_rows` 和 `find_alerts` 改为接收按股票的阈值)
- Modify: `tests/test_monitor.py`、`tests/test_core.py`

**Interfaces:**
- Consumes: `Settings`(Task 1)
- Produces:
  - `monitor.next_run(settings: Settings, after: datetime) -> Tuple[datetime, List[str]]`
  - `monitor.run_loop(settings: Settings, tick: Callable[[str], Any], now=None, sleep=_time.sleep, max_runs: Optional[int] = None) -> None`。`max_runs` 按时间点计数:一个时间点上即使同时跑了两类任务,也只算一次。
  - `AlertStore.filter_new(rows, thresholds: Dict[str, float], day: str)` 和 `AlertStore.record(rows, thresholds: Dict[str, float], day: str)`
  - `core.find_alert_rows(snapshot, thresholds: Dict[str, float])` 和 `core.find_alerts(snapshot, thresholds: Dict[str, float])`。快照里如果有股票不在字典中,就跳过它。

- [ ] **Step 1: 改写测试**

- 辅助函数 `daily()` 和 `interval(n)` 改为构造 `Settings`:`daily()` 设置 `daily_brief="16:30", intraday_minutes=None`;`interval(n)` 设置 `daily_brief=None, intraday_minutes=n`。新增 `both(daily_brief, intraday_minutes)`,两个都设置。其余字段用默认值。
- 现有的 `next_run_time(...) == X` 断言全部改成 `next_run(...) == (X, ["daily"])` 或 `(X, ["intraday"])`。
- 新增测试:

```python
def test_both_kinds_interleave():
    s = both(daily_brief="16:10", intraday_minutes=30)
    assert monitor.next_run(s, at(21, "15:45")) == (at(21, "16:00"), ["intraday"])
    assert monitor.next_run(s, at(21, "16:00")) == (at(21, "16:10"), ["daily"])

def test_same_time_runs_intraday_then_daily():
    s = both(daily_brief="16:00", intraday_minutes=30)
    assert monitor.next_run(s, at(21, "15:45")) == (at(21, "16:00"), ["intraday", "daily"])

def test_run_loop_passes_kind_and_isolates_failures():
    # both(daily_brief="16:00", intraday_minutes=30),时钟从 15:45 开始,max_runs=1
    # tick 遇到 "intraday" 时记录后抛出 RuntimeError,遇到 "daily" 时只记录
    assert ran == ["intraday", "daily"]
```

- `run_loop` 现有的两个测试改成 `tick=lambda kind: ...`。
- `AlertStore` 的测试把 `5.0` 换成 `{"MSFT": 5.0}`。新增 `test_thresholds_are_per_ticker`:`{"MSFT": 5.0, "TSLA": 8.0}`,MSFT 涨 6%、TSLA 涨 6% 时只有 MSFT 是新提醒。
- `test_core.py` 里 `find_alerts(rows, 5.0)` 改成按股票的字典。

- [ ] **Step 2: 运行测试,确认失败**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest tests/test_monitor.py tests/test_core.py -q`
预期:因为 `AttributeError: module 'monitor' has no attribute 'next_run'` 等原因失败。

- [ ] **Step 3: 实现**

- `next_run`:把 `after` 转换到 `settings.timezone`;已开启的任务分别用 `_next_interval` / `_next_daily` 算出时间,取最小值;返回在这个时间到期的任务类型,顺序固定为 `intraday`、`daily`。删除 `next_run_time`。
- `run_loop`:对返回的每个任务类型分别 `try: tick(kind)`,异常处理和现在一样打印 traceback。
- `AlertStore._level(row, threshold)` 保持不变,`filter_new` 和 `record` 改为用 `thresholds[row["ticker"]]` 取阈值。
- `main_workflow.py` 做最小改动,保证现有测试通过:`node_judge` 和 `record_sent_alerts` 里的三处调用改为传 `{t: threshold for t in state["rules"]["tickers"]}`。这里的 `threshold` 仍然是 `float(state["rules"]["alert_threshold"])`。`main()` 暂时不改,Task 4 会重写它。

- [ ] **Step 4: 运行全部测试**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest -q`
预期:全部通过。

- [ ] **Step 5: Commit**

```bash
git add monitor.py core.py main_workflow.py tests/test_monitor.py tests/test_core.py
git commit -m "Schedule intraday checks and daily brief together; per-ticker alert thresholds"
```

---

### Task 4: `main_workflow.py`:按 `settings + tickers + kind` 运行

**Files:**
- Modify: `main_workflow.py`
- Modify: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `Settings`、`TickerRule`、`WatchlistReloader`、`load_settings`(Task 1、2);`monitor.next_run`、`monitor.run_loop`、`AlertStore(...thresholds...)`、`core.find_alert_rows(snapshot, thresholds)`(Task 3)
- Produces:
  - `AgentState` 的新字段:`settings: Settings`、`tickers: List[TickerRule]`、`kind: str`(`"daily"` 或 `"intraday"`);删除 `requirement`、`rules`、`alerts_only`;其余字段不变。
  - `run_once(app, settings: Settings, tickers: List[TickerRule], kind: str, alert_store: Optional[str] = None) -> AgentState`
  - 命令行:`python main_workflow.py [--once daily|intraday] [--config config.yaml] [--watchlist watchlist.yaml]`

- [ ] **Step 1: 改写测试**

- 把 `_rules(...)` 换成 `_state(tickers=..., kind="daily", **kw)`,构造出 `Settings`(`notify_method="discord"`,`discord_webhook="https://hook"`)和 `TickerRule` 列表。
- `_capture_notify` 不再替换 `core.load_discord_config`,webhook 直接从 `state["settings"]` 读。
- `alerts_only=True` 全部改成 `kind="intraday"`。删掉 `parse` 节点相关的测试。
- 新增测试:

```python
def test_judge_uses_each_tickers_threshold(tmp_path):
    # MSFT 阈值 5、TSLA 阈值 8;快照里 MSFT -6%、TSLA -6% ⇒ alerts 只有 MSFT

def test_intraday_prices_only_intraday_tickers(monkeypatch):
    # MSFT intraday=True,SPY intraday=False;替换 core.fetch_snapshot 并记录调用
    # node_price(kind="intraday") ⇒ 只抓了 ["MSFT"];kind="daily" ⇒ 抓了 ["MSFT", "SPY"]

def test_intraday_with_no_intraday_tickers_stays_quiet():
    # 全部 intraday=False, kind="intraday";从 supervisor 开始依次执行 → 不调用 fetch_snapshot,不调用 notify

def test_research_false_skips_news_and_research(monkeypatch):
    # daily 任务,MSFT research=True、SPY research=False ⇒ 新闻和研究数据只抓了 MSFT

def test_empty_watchlist_tick_is_skipped(monkeypatch, capsys):
    # main_workflow.tick(app, settings, reloader_returning_[], "daily") 不调用 app.invoke,
    # 输出里包含 "[MONITOR] watchlist is empty"
```

- [ ] **Step 2: 运行测试,确认失败**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest tests/test_workflow.py -q`
预期:因为 `KeyError: 'settings'` 等原因失败。

- [ ] **Step 3: 实现**

- 删除 `node_parse`、`import parser` 和 `DEFAULT_REQUIREMENT`;`build_graph` 里去掉 `parse` 节点和对应的边。
- 新增 `_thresholds(state) -> Dict[str, float]`。
- 新增 `_scope(state) -> List[TickerRule]`:`intraday` 时只返回 `intraday` 为真的股票,`daily` 时返回全部股票。`node_price` 只抓 `_scope` 里的股票。
- `_report_tickers` 改为基于 `kind` 判断。`node_news` 和 `node_research` 只处理报告范围内且 `research` 为真的股票。
- 时区、语言、推送方式、webhook、mention id 都从 `state["settings"]` 读。`node_notify` 不再调用 `core.load_discord_config`。
- `node_supervisor` 里所有 `alerts_only` 的判断改成 `kind == "intraday"`。`_scope` 为空的 `intraday` 任务:`price` 得到空快照,`judge` 得到空提醒,然后按现有的"没有新提醒就安静"逻辑结束。
- 新增 `tick(app, settings, reloader, kind)`:先调用 `reloader.get()`,结果为空时打印 `[MONITOR] watchlist is empty` 并返回;否则调用 `run_once(app, settings, tickers, kind, ALERT_STORE_PATH if kind == "intraday" else None)`。
- `main()`:用 `--config` 调 `load_settings`;创建 `WatchlistReloader(--watchlist, settings.defaults, on_error=...)`,其中 `on_error` 先 print,`notify_method` 为 `discord` 时再调用 `notifier.notify` 发送同一条消息;启动时调用一次 `reloader.get()`,文件缺失时异常直接抛出。有 `--once kind` 时调用一次 `tick` 后退出,否则运行 `monitor.run_loop(settings, lambda kind: tick(app, settings, reloader, kind))`。

- [ ] **Step 4: 运行全部测试**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest -q`
预期:全部通过,包括 Task 3 遗留的失败用例。

- [ ] **Step 5: 本地冒烟测试(不推送)**

在临时目录里写一份 `config.yaml`(`notify_method: console`)和一份只包含 MSFT 的 `watchlist.yaml`,然后运行:
`/Users/ansel/miniconda3/envs/stock/bin/python main_workflow.py --once daily --config <tmp>/config.yaml --watchlist <tmp>/watchlist.yaml`
预期:控制台打印 `[DAILY BRIEF]`,没有异常。这一步会调用真实的 yfinance 和 LLM;LLM 失败时会打印原始数据,也算通过。

- [ ] **Step 6: Commit**

```bash
git add main_workflow.py tests/test_workflow.py
git commit -m "Run workflow from config.yaml and a hot-reloaded watchlist"
```

---

### Task 5: `parser.py`:把自然语言解析成编辑操作

**Files:**
- Modify: `parser.py`(重写)
- Modify: `tests/test_parser.py`(重写)

**Interfaces:**
- Consumes: `TickerRule`、`normalize_symbol`(Task 1)
- Produces: `parser.parse_watchlist_edit(text: str, current: List[TickerRule], model: Optional[str] = None) -> List[Dict[str, Any]]`。返回的每个 op 至少包含 `op` 和 `symbol`,`symbol` 已经过 `normalize_symbol` 处理;其余字段原样保留,由 `apply_ops` 校验。

- [ ] **Step 1: 写失败的测试**

```python
def test_parse_edit_returns_ops(monkeypatch):
    monkeypatch.setattr(parser, "ask_llm", lambda p, model=None:
        '[{"op": "add", "symbol": "nvda", "alert_threshold": 3}]')
    assert parser.parse_watchlist_edit("加上 NVDA 跌 3% 提醒", []) == \
        [{"op": "add", "symbol": "NVDA", "alert_threshold": 3}]

def test_parse_edit_ignores_text_around_json(monkeypatch): ...   # "Sure:\n[...]\nDone"

def test_prompt_includes_current_watchlist(monkeypatch):
    # 捕获传给 ask_llm 的 prompt;current=[TickerRule("TSLA", 8.0, True, True)] ⇒ prompt 里包含 "TSLA" 和 "8"

@pytest.mark.parametrize("raw", ["", "not json", '{"op": "add"}', '[{"symbol": "MSFT"}]', '["MSFT"]'])
def test_invalid_llm_output_raises(monkeypatch, raw): ...   # ValueError
```

- [ ] **Step 2: 运行测试,确认失败**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest tests/test_parser.py -q`
预期:因为 `AttributeError: ... 'parse_watchlist_edit'` 而失败。

- [ ] **Step 3: 实现**

- 删除 `DEFAULTS`、`ALLOWED_*`、`PARSER_PROMPT`、`_normalize_tickers`、`_apply_defaults`、`_validate_and_fix` 和 `parse_user_requirement`。
- 保留 `_coerce_*` 的前提是还有地方用到它,否则一并删除。
- 新增 `EDIT_PROMPT`,内容包括:
  - 当前 watchlist,用 YAML 表示;
  - 三种 op 的格式;
  - 三个允许的字段及含义,其中百分比要转成数字,例如 "3%" → 3;
  - 规则:只返回 JSON 数组;没提到的字段不要输出;用户说"不用盯盘"对应 `intraday: false`,"不要新闻"对应 `research: false`。
- JSON 提取:把现有逻辑里匹配 `{...}` 的正则改成匹配 `[...]`。

- [ ] **Step 4: 运行全部测试**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest -q`
预期:全部通过。

- [ ] **Step 5: Commit**

```bash
git add parser.py tests/test_parser.py
git commit -m "Parse natural language into watchlist edit operations"
```

---

### Task 6: `watchlist.py`:`apply_ops` 和命令行

**Files:**
- Create: `watchlist.py`
- Create: `tests/test_watchlist.py`

**Interfaces:**
- Consumes: `load_settings`、`load_watchlist`、`dump_watchlist`、`parse_rules`、`normalize_symbol`、`TickerRule`、`TickerDefaults`(Task 1);`parser.parse_watchlist_edit`(Task 5);`ticker_checker.get_previous_close`
- Produces:
  - `apply_ops(rules: List[TickerRule], ops: List[Dict[str, Any]], defaults: TickerDefaults) -> List[TickerRule]`:纯函数,⑤ 的 Discord bot 会复用。
  - `main(argv: Optional[List[str]] = None, input_fn=input) -> int`:返回退出码。

- [ ] **Step 1: 写失败的测试**

```python
def test_apply_add_update_remove():
    rules = [R("MSFT"), R("SPY")]
    out = watchlist.apply_ops(rules, [
        {"op": "add", "symbol": "NVDA", "alert_threshold": 3},
        {"op": "update", "symbol": "SPY", "intraday": False},
        {"op": "remove", "symbol": "MSFT"}], D)
    assert out == [R("SPY", intraday=False), R("NVDA", alert_threshold=3.0)]
    assert rules == [R("MSFT"), R("SPY")]   # 不修改传入的列表

@pytest.mark.parametrize("op", [
    {"op": "add", "symbol": "MSFT"}, {"op": "update", "symbol": "AAPL", "intraday": False},
    {"op": "remove", "symbol": "AAPL"}, {"op": "rename", "symbol": "MSFT"},
    {"op": "update", "symbol": "MSFT", "colour": "red"}, {"op": "update", "symbol": "MSFT", "alert_threshold": -1},
])
def test_apply_rejects_bad_ops(op): ...   # ValueError

def test_cli_writes_after_confirm(monkeypatch, tmp_path):
    # 替换 parser.parse_watchlist_edit,返回 [add NVDA];替换 ticker_checker.get_previous_close,返回 100.0
    # main([text, "--config", c, "--watchlist", w], input_fn=lambda _: "y") == 0;watchlist 里出现 NVDA

def test_cli_creates_watchlist_when_missing(...): ...   # w 原本不存在 ⇒ 写入后只有 NVDA

@pytest.mark.parametrize("case", ["declined", "llm_invalid", "unknown_symbol"])
def test_cli_leaves_file_untouched(monkeypatch, tmp_path, case):
    # declined: input_fn 返回 "n";llm_invalid: parse_watchlist_edit 抛出 ValueError;
    # unknown_symbol: get_previous_close 返回 None
    # ⇒ declined 返回 0,其余两种返回非 0;文件内容和执行前逐字节相同

def test_cli_yes_skips_prompt(...): ...   # input_fn 被调用时抛异常,带上 "--yes" 后仍然返回 0

def test_cli_list_prints_merged_rules(capsys, tmp_path): ...   # 输出里有 "TSLA" 和 "8.0",以及默认阈值 "5.0"
```

`R(symbol, **kw)` 用 `D` 里的默认值加上 `kw` 构造一个 `TickerRule`。

- [ ] **Step 2: 运行测试,确认失败**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest tests/test_watchlist.py -q`
预期:因为 `ModuleNotFoundError: No module named 'watchlist'` 而失败。

- [ ] **Step 3: 实现**

- `apply_ops`:把规则复制成 `symbol → 字段字典` 的有序结构,逐个应用 op。op 里的字段先合并,最后整体交给 `parse_rules` 校验,这样字段校验和读取文件时完全一致。新增的代码追加到列表末尾。
- 命令行:
  - 用 argparse:第一个位置参数是 `list` 或一段自然语言;另有 `--yes`、`--config`、`--watchlist`。
  - watchlist 文件不存在时从 `[]` 开始,但 `list` 子命令遇到这种情况要打印提示。
  - 新加的代码逐个用 `get_previous_close` 检查。
  - 差异按行打印:`+ NVDA threshold=3.0 intraday=True research=True`、`- MSFT`、`~ SPY intraday: True → False`。
  - 出错时打印 `[ERROR] ...` 到 stderr,返回 1。

- [ ] **Step 4: 运行全部测试**

运行:`/Users/ansel/miniconda3/envs/stock/bin/python -m pytest -q`
预期:全部通过。

- [ ] **Step 5: Commit**

```bash
git add watchlist.py tests/test_watchlist.py
git commit -m "Add watchlist CLI that edits the watchlist from natural language"
```

---

### Task 7: README

**Files:**
- Modify: `README.md`(Workflow Mode 的介绍、mermaid 拓扑图里的 `parse` 节点、Setup 和用法部分)

- [ ] **Step 1: 更新 README**

- 拓扑图里去掉 `P[parse]`,加上 `research` 节点(现有代码里有,但图里漏了)。
- 介绍里写明:盘中检查和日报在同一个进程里同时运行;每只股票的设置在 `watchlist.yaml` 里。
- Setup 部分:
  1. 复制 `config_example.yaml` 为 `config.yaml`;
  2. 用 `python watchlist.py "Check MSFT and META, alert me at 3% for META"` 生成 watchlist;
  3. 用 `python main_workflow.py` 启动,或用 `--once daily` 先试跑一次。
- Example 一节里的 prompt 改成 `watchlist.py` 的用法。

- [ ] **Step 2: 检查 README 里没有残留的旧用法**

运行:`grep -n "parse_user_requirement\|schedule_mode\|requirement" README.md`
预期:没有输出。

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document config.yaml + watchlist.yaml setup"
```
