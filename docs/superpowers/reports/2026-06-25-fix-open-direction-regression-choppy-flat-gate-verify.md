# Verify 报告: fix-open-direction-regression-choppy-flat-gate

- **Change**: fix-open-direction-regression-choppy-flat-gate
- **Design Doc**: docs/superpowers/specs/2026-06-25-fix-open-direction-regression-choppy-flat-gate-design.md
- **类型**: 改 live 开仓决策(observability 证据驱动);config 可逆
- **日期**: 2026-06-25
- **结论**: PASS——体制空仓硬门实现完整、单点收口、零回归、全量 1460/0;改 live 需用户手动重启加载。

## 1. 三维验证

| 维度 | 结果 |
|---|---|
| Completeness | 15/15 tasks `[x]`;1 capability delta(`regime-flat-entry-gate`)5 requirement 全实现 |
| Correctness | 需求↔代码+测试齐;`test_regime_flat_gate.py` 23 测试;**task review Spec✅/质量 Approved(Critical C-1 已在 build 内修)**;全量 **1460 passed / 0 failed** |
| Coherence | design 落地;单点收口 `_classify_regime_flat_gate`(主+3 deferred 共调,不变量测试守);口径差异已记录 |

## 2. 实现核对(delta spec 全覆盖)

- **体制空仓硬门**(long-only):choppy/mixed + 无方向论据(非 aligned 非 ungated-path_evidence)→ 拒 open_long;趋势体制/short/非open/flag-off 放行。✅
- **path_evidence ungated**(关键):`_compute_directional_evidence` 返回 ungated `path_evidence_raw`(thesis 用);`_select_rr_floor` floor-grant 仍 `pe_raw AND lever1 AND not block_long AND abs(score)>=min_deferred`(**C-1 修复:恢复原 4 守卫,行为零变,含 lever1-ON 证明测试**)。✅
- **单点收口**:4 调用点(主 1728 + deferred 814/952/1086)调同一方法;不变量测试 ≥4 调用。✅
- **attribution**:accept(`_build_attribution`)+ 主 reject(`_rejection_attribution`)写 4 字段;deferred reject 经既有 `_publish_hold`(携 reason,结构化四字段属既有限制,已 spec 标注另起 change)。✅
- **event_backtest 同构**:`_check_entry_with_regime` 加同构门;**backtest 口径=aligned-only(无 entry_context 的 path_evidence 数据),比 live 严**,代码注释 + 本报告标注。✅
- **config**:`regime_flat_gate_enabled` 默认 True + env `REGIME_FLAT_GATE_ENABLED` 回滚;epoch `_EPOCH_FALLBACK['regime_flat_gate_enabled']=False`(旧磁带 replay 不带门,faithful)+ `_install_config_flags` 还原。✅

## 3. 关键验证发现

- **path_evidence 在 live 真正生效**:`tech_analyst.py:154` 无条件填充 `entry_context`(position_in_24h_range + pre_12h_return_pct,不受 lever1 门控)→ live flat gate 的 thesis = `aligned OR path_evidence_raw` **完整可用**,不退化为 aligned-only(仅 event_backtest 退化,已记)。被 regime 误判成 choppy 的趋势能被 path_evidence 救回——达成"不误伤趋势单"设计目标。
- **6 个既有测试因门改 live 行为被打挂 → 全部为"测试假设需更新"非 gate 逻辑回归**:event_backtest 3(TP/SL/force-close)、tape_capture flip、15m_e2e、decision_replay epoch-key——分别 opt-out 门(它们测别的机制)或注册 epoch 键修复;无一是 gate 逻辑 bug。全量回到 1460/0。
- **零回归**:`_select_rr_floor` 既有测试全绿;C-1(lever1-ON 时丢 block_long/score 守卫的潜在回归)在 build 内被 review 抓出并修+加证明测试。

## 4. 安全 / 风险

- 无硬编码密钥、无新 unsafe;observability 证据驱动。
- 改 live 开仓:**需用户手动 OS 重启** live 加载;env `REGIME_FLAT_GATE_ENABLED=false` 即时回滚。
- 预期开仓低频(衰减期趋势 setup 少=正确"choppy 空仓");前向监控:方向对% 是否回升 + 被拒 attribution 分布。
- 样本警告:方向分析改后桶 n=14(薄)——方向强、统计薄;默认开但可逆,前向验证非一次性赌。

## 5. 分支

build 完成于 `change/fix-open-direction-regression-choppy-flat-gate`(commits 532c717..HEAD),待 finishing-branch 处理。
