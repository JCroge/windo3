# Tasks: pseudo-resonance-downweight（病根1a）

- [x] 1. comet-design：MA 块组成边界、cap 默认、config 键、归因、CF 验证方案；Design Doc + delta spec
- [x] 2. 重构 `_compute_score`：MA 趋势块合计 + 同向封顶（`_cap_ma_bloc` 单点收口）
- [x] 3. config_loader 四段式 `pseudo_resonance_downweight_enabled` + `ma_bloc_cap`；banner
- [x] 4. 归因 `ma_bloc_contribution`/`independent_contribution`/`ma_bloc_capped`
- [x] 5. 单元测试 10 + judge 回归 87 绿（默认 off 透传=旧行为）
- [x] 6. CF 回放验证（红线）：cap 安全值(45-50)翻转 0/190、confidence 仅 2% 微降→cap 不咬；报告落盘
- [x] 7. 据 CF 定 default：**默认 OFF 潜伏护栏**（cap 安全值不咬，启用须压到 ~30 并更强验证）；meta 结论真问题更底层（见报告）
