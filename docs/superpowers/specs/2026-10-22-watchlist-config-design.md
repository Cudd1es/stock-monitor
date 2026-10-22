# Watchlist 配置设计

子项目 ①,共 5 个(① watchlist 配置 → ② 波动率阈值 → ③ 事件提醒 + 盘前盘后 → ④ 行业 ETF → ⑤ Discord bot)。
② 和 ⑤ 都依赖本项目。

## 目标

- 监控程序计划部署到常驻服务器。启动和运行都不依赖 LLM。
- 每只股票可以单独设置:提醒阈值、是否参与盘中提醒、是否抓新闻和研究数据。
- 一个进程里同时运行盘中异动检查和收盘后日报。
- 自然语言不再作为启动参数,而是用来编辑 watchlist。⑤ 的 Discord bot 复用同一条路径。

不在本项目范围内:按股票区分推送渠道或报告语言;Agent 模式(`main_agent.py`);自动迁移旧的自然语言需求。

## 文件格式

### `config.yaml`:全局设置和密钥,手动编辑,只在启动时读取

```yaml
timezone: America/Toronto
schedule:
  daily_brief: "16:10"     # HH:MM 收盘后日报;null 关闭
  intraday_minutes: 10     # 盘中检查间隔(分钟);null 关闭
notify_method: discord     # console | discord
report_language: zh
defaults:                  # 每只股票的默认值
  alert_threshold: 5.0
  intraday: true
  research: true
discord:
  webhook_url: "..."
  mention_id: "..."
sec:
  user_agent: "..."
```

- 缺少的字段使用代码里的默认值:`daily_brief` 为 `"16:10"`,`intraday_minutes` 为 `null`,`notify_method` 为 `console`,`report_language` 为 `zh`,`defaults` 依次为 `5.0`、`true`、`true`。
- `daily_brief` 和 `intraday_minutes` 都为 `null` 时,启动报错。
- 以下情况会报错:时间格式不合法、间隔不在 1 到 390 之间、`notify_method` 不在允许范围内、`defaults` 下出现未知字段。

### `watchlist.yaml`:股票列表,程序读写,每次检查时热加载

```yaml
# 由程序维护(python watchlist.py)。手动编辑也可以,但注释会在下次写入时丢失。
tickers:
  - symbol: MSFT
  - symbol: TSLA
    alert_threshold: 8
  - symbol: SPY
    intraday: false
    research: false
```

- 每只股票只允许三个可选覆盖字段:`alert_threshold`(必须大于 0)、`intraday`(布尔值)、`research`(布尔值)。
- `symbol` 用现有的代码清洗逻辑处理并转成大写。出现重复代码报错。
- 出现未知字段报错。
- `research: false` 表示不抓新闻、8-K、分析师数据和相对成交量。

## 组件

### `settings.py`(新增)

- `Settings`:dataclass,字段包括 `timezone`、`daily_brief: Optional[str]`、`intraday_minutes: Optional[int]`、`notify_method`、`report_language`、`defaults: TickerDefaults`、`discord_webhook`、`discord_mention_id`。
- `TickerRule`:dataclass,字段为 `symbol`、`alert_threshold: float`、`intraday: bool`、`research: bool`,已经和默认值合并好。
- `load_settings(path="config.yaml") -> Settings`:读取并校验;不合法时抛出 `ValueError`。
- `load_watchlist(path, defaults) -> List[TickerRule]`:读取并校验;文件不存在或内容不合法时抛出 `ValueError`。
- `dump_watchlist(rules, path, defaults)`:写入时只保存和默认值不同的字段,带上文件头注释,用临时文件加 `replace` 原子写入。
- `WatchlistReloader(path, defaults, on_error)`:`get()` 每次调用都重新读文件。读取失败时返回上一份有效结果,并对同一个错误只调用一次 `on_error(msg)`(用来发 Discord 通知)。第一次读取就失败时直接抛出异常。

`core.load_discord_config` 由 `Settings` 取代。`parser.py` 里的 `DEFAULTS`、`report_style` 和 `lookback_days` 删除。

### `monitor.py`(修改)

- `next_run(settings, after) -> Tuple[datetime, List[str]]`:分别算出严格晚于 `after` 的下一次盘中检查(沿用现有的 `_next_interval`)和下一次日报(沿用现有的 `_next_daily`),返回其中较早的时间,以及在这个时间到期的全部任务类型。两个同时到期时返回 `["intraday", "daily"]`,盘中在前。关闭的任务不参与计算。
- `run_loop(settings, tick: Callable[[str], Any], ...)`:时间到了以后,按顺序对每个任务类型调用 `tick(kind)`。每个 `tick` 单独做异常隔离,一个失败不影响另一个。防休眠逻辑保持不变。
- `AlertStore`:`filter_new(rows, thresholds, day)` 和 `record(rows, thresholds, day)` 的第二个参数从单个 `threshold: float` 改为 `thresholds: Dict[str, float]`(代码 → 阈值),每行按自己股票的阈值计算档位。存储格式不变。

### `main_workflow.py`(修改)

- `AgentState`:去掉 `rules`、`alerts_only` 和 `requirement`,新增 `settings: Settings`、`tickers: List[TickerRule]` 和 `kind: str`。
- 删除 `parse` 节点。
- `price`:`kind == "intraday"` 时只取 `intraday` 为真的股票;`daily` 时取全部股票。
- `judge`:每行使用对应股票的 `alert_threshold`;`intraday` 时用 `AlertStore` 去重,`daily` 时不去重(和现在一致)。
- `news` / `research`:只处理报告范围内且 `research` 为真的股票。报告范围:`intraday` 时是触发提醒的股票,`daily` 时是全部股票。
- `brief` / `notify`:沿用现有逻辑。`kind` 取代 `alerts_only`,推送配置从 `Settings` 读取。
- `main()`:读取 `Settings`,创建 `WatchlistReloader`,常驻运行 `run_loop`;每次 `tick(kind)` 时调用 `reloader.get()`。
- 命令行参数:`--once daily|intraday` 立即运行一次对应任务后退出;`--config` 和 `--watchlist` 用来指定文件路径。

### `watchlist.py`(新增,命令行)

- `python watchlist.py list`:打印合并默认值之后的完整列表。
- `python watchlist.py "<自然语言>" [--yes]`:
  1. `parser.parse_watchlist_edit(text, current_rules) -> List[Dict]`:LLM 返回操作列表 `[{"op": "add"|"update"|"remove", "symbol": ..., <字段>...}]`。沿用现有的 JSON 提取逻辑;返回内容无效时抛出 `ValueError`。
  2. `apply_ops(rules, ops, defaults) -> List[TickerRule]`:纯函数,不修改传入的 `rules`。以下情况都会抛出 `ValueError`:`add` 一个已存在的代码、`update` 或 `remove` 一个不存在的代码、未知的操作类型、未知字段、字段值不合法。
  3. 新加的代码用 `ticker_checker.get_previous_close` 检查,返回 `None` 时报错。
  4. 打印改动前后的差异;确认 `y/N` 之后再调用 `dump_watchlist`。加 `--yes` 跳过确认。
  5. 任何一步失败都不写文件,以非零状态码退出。
- `watchlist.yaml` 不存在时,从空列表开始(这是生成第一份 watchlist 的方式)。

## 错误处理

| 情况 | 行为 |
|------|------|
| 启动时 `config.yaml` 不合法 | 报错退出 |
| 启动时 `watchlist.yaml` 不存在或不合法 | 报错退出,提示用 `watchlist.py` 生成 |
| 运行中 `watchlist.yaml` 不合法 | 继续用上一份有效配置,打警告日志,同一个错误只发一次通知 |
| 编辑时 LLM 失败或返回无效内容 | 不写文件,非零状态码退出 |
| 编辑时新代码查不到行情 | 同上 |

## 迁移

- 不做自动迁移。运行一次 `python watchlist.py "<原来的需求>"` 即可生成 watchlist。
- 更新 `config_example.yaml`,新增 `watchlist_example.yaml`,更新 README 的安装和用法部分。
- `state/alerts.json` 的格式不变。当天中途修改某只股票的阈值后,提醒档位会按新阈值计算,最多多发一次提醒,这是可以接受的。

## 测试(TDD)

- `tests/test_settings.py`:默认值合并;未知字段、重复代码、阈值小于等于 0、时间格式不合法、两个调度都为 null 时报错;`dump` 和 `load` 往返一致,而且只写入非默认值;`WatchlistReloader` 在读取失败时返回上一份有效结果,同一个错误只回调一次;第一次读取失败时抛出异常。
- `tests/test_monitor.py`:两类任务合并排期;同一时刻先 `intraday` 后 `daily`;单独关闭某一类任务;非交易日;`run_loop` 对同时到期的两个任务依次调用 `tick`,其中一个抛异常不影响另一个。
- `tests/test_watchlist.py`:`apply_ops` 的增、改、删以及各种报错情况;不修改传入的列表;命令行在 mock 掉 LLM 和行情后能成功写入;LLM 返回无效内容时不写文件;新代码查不到行情时不写文件;没有 `--yes` 且确认输入 `n` 时不写文件。
- `tests/test_parser.py`:改写为测试 `parse_watchlist_edit`。
- `tests/test_workflow.py`:每只股票用自己的阈值;`intraday: false` 的股票不进盘中检查;`research: false` 的股票不抓新闻和研究数据;`daily` 覆盖全部股票;删除 `parse` 节点相关的测试。
