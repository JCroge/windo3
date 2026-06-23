# Tasks: restore-llm-rsi-veto-power

> 高层任务清单。comet-design 阶段细化 + 产出 delta spec 后会更新。

- [ ] 1. comet-design：对现行 judge.py 定稿插入点（主路径 + 3 条 deferred 路径单点收口）、config 键、归因字段、event_backtest 验证方案与通过标准；产出 Design Doc + delta spec
- [ ] 2. 实现反转合流检测 helper（读 LLM action + rsi_divergence 原始布尔，判 `LLM_counter AND RSI_div_against`），单点收口
- [ ] 3. 触发时路由到 deferred_pullback；放行/defer 双路径写归因字段
- [ ] 4. config_loader 接入总开关 `llm_rsi_reversal_veto_enabled` + 阈值（四段式：DEFAULTS/HARD_LIMITS/env/yaml），banner 显示
- [ ] 5. 单元测试：合流触发 defer / 仅 LLM 反向不触发 / 仅 RSI 背离不触发 / 开关 off 回退 / 主路径与 deferred parity
- [ ] 6. event_backtest 验证（红线）：追势买在反转点样本 pre/post 分布对比，达通过标准
- [ ] 7. 确定上线 default 与缓进策略（据 event_backtest 结果）
