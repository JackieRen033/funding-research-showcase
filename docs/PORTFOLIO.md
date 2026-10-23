# Funding research: business decisions and implementation boundaries

## Problem

Headline funding rates do not directly describe an economically attractive event. Settlement schedules, trading costs, sampled liquidity and holding assumptions can change the conclusion. Manual asset selection also limits research coverage.

## Contribution

The project owner proposed the research requirements, clearer outputs, automatic selection, capital-time comparison and investigation of fee eligibility. Earlier owner-provided discussion summaries also describe notional exposure and shorter event-based holding requirements; complete account/portfolio controls are not implemented. Codex assisted with the architecture, adapters, UI, tests and troubleshooting. The owner should not be described as independently writing all code or validating a profitable trading strategy.

## Iterations

1. Hourly funding comparisons became event-level cost scenarios after the owner questioned whether the hourly rate captured time and capital usage. Waiting and occupied capital are separated; future funding and exit prices remain uncertain.
2. Verification found stale mark/OI source data could pass despite a recent HTTP response. Source timestamps now block stale candidates; regression tests use synthetic fault inputs.
3. Fee research introduced exact-symbol, market, execution-type, eligibility and expiry checks. Maker discounts cannot silently apply to a taker execution model.
4. A practical usability issue was discovered: complete collection waited for the slowest venue scan. Independent scan workers and separate candidate refresh remove that barrier; they do not guarantee all observations remain fresh under slow networks.

## What the prototype proves

It demonstrates requirements iteration, market-data normalization, inspectable cost scenarios and reproducible offline checks. Historical local live runs establish short-period public API connectivity, not current availability or sustained uptime. It provides no backtest, actual positions, account settlements, orders or profit record.

## 中文摘要

本项目由个人发起，使用 Codex 辅助实现。项目负责人提出需求、参与金融逻辑与风险边界讨论；Codex 主要完成代码、测试和技术排障。成果是只读行情研究原型，用于比较单次结算的成本情景并保存证据。完整仓位管理、最优窗口和盈利能力仍未验证。
