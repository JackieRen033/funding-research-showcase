# Schema 0.1

模型定义：`funding_collector/schema.py`。新增字段向后兼容；破坏性修改需升级 schema_version。

## FundingSnapshot

| 字段 | 类型 / 单位 / 含义 |
|---|---|
| schema_version, snapshot_id | 版本；UUID，每次采集独立身份 |
| exchange, symbol, contract | 交易所、统一 BTC/USDT、交易所原始合约名 |
| mode | live / synthetic；演示也使用独立数据库 |
| current_rate | 比率，0.0001 = 1 bps = 0.01%；保留交易所 current/latest 语义 |
| current_rate_time_ms | current 对应事件；API 语义不明确则 null |
| predicted_rate, predicted_rate_time_ms | 针对明确目标事件的尚未最终确定的估计；不可用则 null |
| realized_rate, realized_time_ms | 已观察到的最新公开历史事件，不代表目标事件未来结果 |
| interval_seconds, interval_source | 秒，动态读取或带标签的历史推断；未知 null |
| next_funding_time_ms | 即将到来的目标事件；OKX 对应 fundingTime，不是其 nextFundingTime |
| settlement_state, settlement_rate | OKX 原始 settlement 语义；其他所默认 unknown / null |
| mark, index, spot | USDT / 基础币；spot 是同期本所现货盘口中间价 |
| perp_book, spot_book | bids/asks 为 [价格, 基础币数量]；best_bid/ask 显式导出；exchange_time_ms 可空 |
| open_interest_base | 基础币数量；每所映射见 API_MAPPING |
| history | FundingEvent 数组，仅保留非未来历史；完整响应仍在 raw |
| collected_at_ms | 一市场本轮采集/标准化完成的 UTC Unix 毫秒，整条快照可用时点 |
| exchange_time_ms | funding API 的源时间；接口不提供则 null |
| sources | 需特别解释的字段来源与语义 |
| raw | 按 endpoint 分类的原始 JSON envelope |
| connection | CONNECTED / DEGRADED / DISCONNECTED |
| issues | 完整数据质量问题；默认 UI 只显示前三条 |

raw envelope 包含 `url, requested_at_ms, received_at_ms, latency_ms, exchange_time_ms, attempts, data, error`。latency_ms 用 monotonic 计时，包括重试和限速等待；这是观测取得的耗时，不只网络 RTT。源数据中额外的序列号/更新时间全部保留。OKX 的 mark/index/OI 与 Binance 的 OI 源时间戳也记录到对应 envelope，并参与新鲜度校验。

标准化数值使用有限浮点数用于研究筛选；原始 JSON 中的交易所十进制字符串保留精度。不是资金账本。空字符串转换 null；NaN / infinity 拒绝。盘口价格必须正、数量非负；交叉盘口阻断筛选。只支持 USDT 线性合约，不适用于 inverse / quanto / USDC。

## FundingEvent

`exchange, symbol, funding_time_ms` 形成稳定事件身份。`realized_rate` 可空；`observed_at_ms` 是第一次在对应响应可用时的本地时间；`source` 为响应 URL 或 schedule；`funding_model=DISCRETE`。

`settlement_state=scheduled` 表示看到计划；`published` 表示公开历史已给出结果。不会通过 `now >= funding_time` 把事件自动标记为结算完成。多个 event_observations 保留历史修订和可知时间。

## Decision

`WAIT / READY / HOLD / EXIT / REJECT / EMERGENCY`。前三层输出共享同一决策对象，UI 不访问交易所或执行收益计算。HOLD/EXIT 是未来持仓模块预留值。EMERGENCY 是数据异常，不是仓位风险结论。

筛选针对同所 spot-long / perp-short：q = 配置现货腿金额 / spot midpoint；funding = predicted rate × mark × q。假设两腿各开平一次，当前双向盘口计算往返成本，两腿各自的买卖 VWAP 名义金额计算手续费，加 extra_cost。净 bps 的分母是现货腿情景金额，不是资本收益率。未来 basis 变化未建模。
