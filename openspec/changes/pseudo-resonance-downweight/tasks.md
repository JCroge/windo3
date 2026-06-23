# Tasks: pseudo-resonance-downweight（病根1a）

> 高层任务。comet-design 细化 + delta spec 后更新。

- [ ] 1. comet-design：定稿 MA 块组成边界、cap 默认值（CF 回放）、config 键、归因字段、CF 验证方案与通过标准；Design Doc + delta spec
- [ ] 2. 重构 `_compute_score`：抽 MA 趋势块合计 + 同向封顶（单点收口）
- [ ] 3. config_loader 四段式：`pseudo_resonance_downweight_enabled` + `ma_bloc_cap`（+ 可选分量权重）；banner
- [ ] 4. 归因字段 `ma_bloc_contribution`/`independent_contribution`/`ma_bloc_capped`
- [ ] 5. 单元测试：封顶数学/同向反向/开关off回退/独立信号不变/归因
- [ ] 6. CF 回放验证（红线）：off vs on，被 cap 子集翻转方向 + PnL 分布 + 全量无回归；报告落盘
- [ ] 7. 据 CF 结果定 cap 默认值与上线缓进策略
