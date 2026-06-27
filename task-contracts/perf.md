# 任务契约:性能优化(perf)

copy 到项目填写。coordinator 映射为 `perf-opt.js` 的 `args.contract`。缺必填字段 precheck fail-closed。

## 必填字段(contract)

- `summary`:优化目标,一句话。
- `benchmark_command`:测量命令,输出里能取到 `target_metric`。
- `target_metric`:被测指标名(如 `p95_ms`、`throughput`)。
- `validation_command`:**正确性套件**命令。baseline 阶段它必须 green,否则 precheck fail
  ("先把正确性弄绿再谈优化")。
- `strategies`:候选策略列表(`"名字: 怎么做"`)。每个策略在**各自隔离的 worktree** 里试,互不 clobber。
- `boundaries`:允许改动范围。

## 可选字段

- `lower_is_better`:默认 `true`(指标越小越好,如延迟)。吞吐类设 `false`。

## 硬门(模板强制,不可关闭)

- **正确性-vs-速度硬停**:一个候选只有在**正确性 green 且 beat baseline** 时才 eligible;
  没有这种候选 → 整体 `failed`,绝不接受牺牲正确性换来的提速。
- cross-review 额外查 benchmark gaming(测错对象)、隐藏回归。

## 示例

```yaml
summary: "把 checkout 的 p95 延迟降到 120ms 以下"
benchmark_command: "python bench/checkout_bench.py --metric p95_ms"
target_metric: "p95_ms"
validation_command: "python -m pytest tests/checkout -q"
strategies:
  - "algorithmic: 用前缀和替换内层重复求和"
  - "caching: 缓存不变的定价查询"
  - "io: 批量化 DB 往返"
boundaries: "只改 src/checkout/;不动 tests/、不改 benchmark"
lower_is_better: true
```
