# Design: step-regime-choppy-range-pos-063

## 实现说明（tweak，无方案对比）

单值配置步进，复用已上线 `regime-aware-long-entry-guard` 机制，无新逻辑。

`config.yaml`：

```yaml
risk:
  # 体制感知多单位置门（regime-aware-long-entry-guard）
  long_live_max_range_pos_choppy: 0.63   # 0.70 → 0.63 缓进中间步（目标 0.55，见 memory regime-threshold-070-vs-055-comparison）
```

阈值经 `utils/config_loader.py` 已有的四段式映射读入（`long_live_max_range_pos_choppy` 键早已注册，本次不改 loader），在 `agents/trading/judge.py::_check_entry_position_policy` 的 choppy/mixed/bearish 分支用作多单过热门：`entry_range_pos_24h > 0.63` → 转 `deferred_pullback_overheat`。

## 为何 0.63

差异带 (0.55, 0.70] 逐笔最热的是 SUI rp=0.700 / HYPE rp=0.663 / WLD rp=0.653（亏损单集中在 0.66–0.70）。0.63 能罩住 HYPE/SUI 这类顶部追多，同时只把差异带行为改变量从 ~45%（收到 0.55）压到约一半，幅度可控、可观察。

## 验证

- 配置 YAML 合法、键值正确（0.63）。
- 现有相关测试不回归（`test_long_entry_position_guard.py` 等不依赖该生产值，应保持绿）。
- 生效需重启 live；重启后归因 `entry_range_pos_threshold` 应显示 0.63。

## 风险与回退

低风险。回退：改回 0.70 或总开关 `long_live_regime_aware_range_enabled=false`。
