## 修复方案

`_EPOCH_FALLBACK` 是 `{config_key: 纪元回退默认值}` dict;守卫测试要求所有 `_PROD_DEFAULTS` 键在 v3 磁带缺失时,要么落 `_EPOCH_FALLBACK`(用回退值还原旧纪元判定),要么落 `_GATE_IRRELEVANT`(对判定无关)。

这 4 键属"真翻转/防御性 no-op"类(同 521dad5 的 regime-aware 键),应登记进 `_EPOCH_FALLBACK`,值取**纪元前(功能未引入)的等效默认**:
- `llm_rsi_reversal_veto_enabled: False` — 真翻转:纪元前无反转否决=off,replay 用 off 还原旧判定。
- `reversal_veto_min_llm_confidence: 0` — 子门,off 时无效,0=不启用。
- `pseudo_resonance_downweight_enabled: False` — 真翻转:纪元前无伪共振降权=off。
- `ma_bloc_cap: 50` — 防御性 no-op:仅 downweight enabled=True 生效,旧纪元 enabled=False 不影响判定(同 521dad5 max_range_pos no-op 注释口径)。

不动 `_GATE_IRRELEVANT`、不动判定逻辑、不动 config。

## 风险

- [值取错致 replay 失真] → 取纪元前等效默认(全 OFF/默认),与功能引入前的真实行为一致;enabled=False 路径不触判定分支,no-op 安全。
