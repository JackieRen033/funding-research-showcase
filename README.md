# Funding Research Collector

**An independent, Codex-assisted market research prototype. Read-only; no trading or account access.**

Compare funding events using explicit transaction-cost and capital-time assumptions instead of headline rates alone. Inspect the underlying evidence when an apparent opportunity fails the research screen.

## About this release

This is a curated release of an independent, AI-assisted research project. It starts with a new publication history; it does not reproduce the original development timeline. The source and tests are provided for inspection, with historical verification summaries clearly distinguished from current CI results.

## Run an offline demo

Python 3.9+; runtime and tests use the standard library. Download or clone this repository, then open a terminal in its root directory (containing `config.example.json`).

```sh
python3 -m funding_collector.dashboard --demo --open
```

Click “开始合成演示”. Demo data is synthetic and stored separately. No exchange connection is needed. Close the terminal to stop the server; closing the browser alone does not stop it.

```sh
python3 -m funding_collector --demo --once
python3 -m unittest discover -s tests -v
```

Optional installation: `python3 -m pip install .`. When running outside the repository root, pass `--config /path/to/config.example.json`; the configuration is not installed into the Python package. Relative database paths are resolved from the current working directory.

## Live research

```sh
python3 -m funding_collector.dashboard --open
```

Local UI: http://127.0.0.1:8765/. Start automatic observation in the browser. Public exchange access depends on network and service eligibility. This server is loopback-only and has not been prepared for public Internet exposure.

- Binance, OKX, Bybit and Gate public REST data, normalized to matching USDT spot/linear perpetual markets.
- Per-exchange scans run independently; selected candidate order books refresh separately.
- Funding forecasts, realized history, dynamic schedules, prices, depth, OI and source timestamps.
- Four taker fees, sampled round-trip book cost, extra buffer and explicit capital-time assumptions.
- Raw and normalized evidence stored in local SQLite; JSONL export.

## Boundaries

This is same-venue spot-long/perpetual-short research, not cross-exchange execution. Binance latest funding is not promoted to a confirmed forecast. Missing or stale data cannot become an executable signal. Only a bounded candidate pool receives full depth checks; this is not a global net-profit ranking. Future basis, exit liquidity, account fees, margin and settlement eligibility remain unverified. No orders, portfolio risk engine, paper trading or complete backtest.

## Role and evidence

The owner defined requirements and contributed financial reasoning, risk constraints and iteration feedback. Codex assisted with implementation, tests and troubleshooting. See [project case study](docs/PORTFOLIO.md) and [release verification and limitations](docs/RELEASE_READINESS.md).

The prepared version has 56 passing local offline tests. GitHub CI is configured; see the Actions tab for current results. Demo results are not live performance. A bounded live test on 2026-10-23 completed two Gate refresh batches (four snapshots) while OKX was still scanning; see verification/live-smoke-20261023.json. This does not establish full-market coverage or sustained uptime. Historical raw live records are kept privately, not redistributed as a public dataset.

## 中文说明

这是个人发起、Codex 辅助实现的资金费率监测与决策辅助工具。先看判断，再查成本依据和原始数据。已实现公开行情采集、自动筛选和研究存储；没有下单、完整仓位管理或盈利验证。刷新间隔是目标，慢接口仍可能导致数据过期。

## License

No license has been granted for reuse or redistribution. Public visibility does not make this an open-source licensed project. The dashboard runs locally; a public repository is not a hosted monitoring service.
