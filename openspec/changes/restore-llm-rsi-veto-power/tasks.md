# Tasks: restore-llm-rsi-veto-power

- [x] 1. comet-design：插入点定稿（主路径单点收口 + deferred 边界）、config 键、归因字段、验证方案；Design Doc + delta spec
- [x] 2. 实现反转合流检测 helper（LLM_counter AND RSI_div_against），单点收口
- [x] 3. 触发时路由到 deferred（`_route_reversal_veto_defer`）；写归因字段
- [x] 4. config_loader 四段式 `llm_rsi_reversal_veto_enabled` + `reversal_veto_min_llm_confidence`；banner
- [x] 5. 单元测试：合流触发/仅LLM反向/仅RSI背离/开关off/边界/放行归因（14 passed）
- [x] 6. 验证（红线）：event_backtest 不适用→真实磁带口径，发现 0% 触发（LLM 从无 reverse）；报告落盘
- [x] 7. 据验证定 default：**默认 OFF 潜伏护栏**（不改线上行为，启用前须 CF 回放验证）
- [x] 8. deferred 再分发覆盖核定：语义边界（价格回调达标即 veto 目标 + 无新鲜 LLM）→ 不重复挂，注释+spec 记录（守 P1-03 红线）
