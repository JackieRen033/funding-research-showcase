# Official API mapping

核对日期：2026-09-23。接口字段以以下官方文档为依据；样例字段也可能存在弃用项。程序仅使用公开 GET，未依赖第三方交易 SDK。

| Venue | Public endpoints | Funding mapping |
|---|---|---|
| Binance USDⓈ-M | `/fapi/v1/premiumIndex`, `/fundingInfo`, `/fundingRate`, `/depth`, `/openInterest`; spot `/api/v3/depth` | current=`lastFundingRate`; target=`nextFundingTime`; realized 来自 history |
| OKX V5 | `/api/v5/public/funding-rate`, `/funding-rate-history`, `/instruments`, `/mark-price`, `/open-interest`; `/api/v5/market/index-tickers`, `/books` | current-period fundingRate 对应 fundingTime；nextFundingTime 是再下期；history 用 realizedRate |
| Bybit V5 | `/v5/market/tickers`, `/instruments-info`, `/funding/history`, `/orderbook` | fundingRate / nextFundingTime；history fundingRateTimestamp |
| Gate V4 | `/api/v4/futures/usdt/contracts/{contract}`, `/funding_rate`, `/order_book`; `/api/v4/spot/order_book` | funding_rate / funding_next_apply；history r / t |

## Binance

[USDⓈ-M 官方市场数据文档](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

fundingInfo 仅覆盖 cap/floor/interval 调整过的合约。优先取 fundingIntervalHours × 3600；没有条目时用最近两个正常历史事件间隔，并标明 `historical_observed_not_guaranteed`。历史不够则 null，绝不固定 8 小时。历史 `Special` 类型不作为正常 funding。

lastFundingRate 文档只给出 latest 含义，因此 current 保留原值，predicted 暂留 null。markPrice/indexPrice 与 time 保留；两腿盘口 size 和 USDT 永续 OI 使用基础币单位。现货 depth 无源时间戳，不伪造。

[Spot order book 官方接口](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints)

## OKX

[官方 V5 API](https://app.okx.com/docs-v5/en/)

fundingTime 是当前周期目标，nextFundingTime 是再下一周期预告；差值用于 interval。current_period 的 fundingRate 作为目标估计；旧 nextFundingRate 已不支持，不用空字符串模拟 0。settFundingRate 的含义随 processing/settled 变化，单独保存；realized 只取历史 realizedRate。

线性合约盘口 size × ctVal 转为基础币；要求 ctValCcy=基础币且 ctType=linear。OI 使用 oiCcy。markPx、idxPx、books.ts 原样映射。

[官方变更日志的周期说明](https://www.okx.com/docs-v5/log_en/)

## Bybit

[Tickers](https://bybit-exchange.github.io/docs/v5/market/tickers) · [Instruments](https://bybit-exchange.github.io/docs/v5/market/instrument) · [Funding history](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate) · [Orderbook](https://bybit-exchange.github.io/docs/v5/market/orderbook)

linear tickers 给出 fundingRate、nextFundingTime、markPrice、indexPrice、openInterest。优先 tickers.fundingIntervalHour 转小时；缺失时 instruments.fundingInterval 转分钟。历史 fundingRateTimestamp 为毫秒。盘口 b/a 为基础币数量，ts 为生成时间；cts/seq/u 保留原始响应。

## Gate

[Futures API](https://www.gate.com/docs/developers/apiv4/en/futures/) · [Spot API](https://www.gate.com/docs/developers/apiv4/en/spot/)

funding_interval 为秒；funding_next_apply 与 history.t 为秒并转毫秒。funding_rate_indicative 是弃用字段，只保留在 raw。使用当前 funding_rate 作为目标估计。合约需 type=direct；盘口 s 和 position_size 乘 quanto_multiplier 转基础币；position_size 表示单边总 long 数量。期货 order_book.current 为秒，spot current 为毫秒，两者不能混用。

## Rules versus assumptions

本版不实现“何时开平仓一定能获得 funding”的资格判定，也不固化之前讨论过的秒级交易所结算边界。所有事件目标来自 API；默认 60 秒 guard 只是可调研究参数。固定周期、到账时点、最优入场窗口均不作为程序事实。

缺失、弃用、语义不明确的字段保留 null 或 provenance；预测与 realized 分开，完整原始数据始终可查。接入地区专用域名或新产品前需要重新核对对应官方文档和权限。
