# 验证报告：raise-consecutive-loss-limit

- **Change**: raise-consecutive-loss-limit
- **Workflow**: tweak
- **Verify mode**: light（规模评估初判 full 系计入 5 个 openspec 簿记文件所致计数假象；真实代码改动 2 文件/3 行/0 delta spec/0 新 capability，用户确认覆盖为 light）
- **日期**: 2026-06-18

## 改动摘要

连亏熔断阈值 3 → 5，并使 `consecutive_loss_limit` 可经 config.yaml 配置。

- `utils/config_loader.py` · `_load_yaml`：新增 `consecutive_loss_limit` 的 yaml→config 映射（int 转换）
- `config.yaml` · risk 节点：新增 `consecutive_loss_limit: 5`

## 轻量验证结果（5 项）

| # | 检查项 | 结果 | 证据 |
|---|--------|------|------|
| 1 | tasks.md 全部完成 | PASS | 3/3 勾选，无 `- [ ]` |
| 2 | 改动文件与 tasks 一致 | PASS | `git diff --stat base...HEAD` 仅 config.yaml(+1)、config_loader.py(+2) |
| 3 | 编译通过 | PASS | `python3 -m py_compile utils/config_loader.py` OK |
| 4 | 相关测试通过 | PASS | `test_config_clamp_fallback.py` + `tests/test_cf_foundation_config.py` 6 passed；`load_config()` 读到 `consecutive_loss_limit=5`、`daily_pnl_hard_stop=-300.0` |
| 5 | 无安全问题 | PASS | diff 无硬编码密钥/eval/exec 新增 |

**结论**：5/5 PASS，无 CRITICAL 问题。

## 不变量确认

- `daily_pnl_hard_stop = -300` 独立并行兜底不变。
- 连亏统计逻辑 `_track_consecutive_losses` 未改动。
- 合并优先级不变：RISK_DEFAULTS(3) < config.yaml(5) < 环境变量 `CONSECUTIVE_LOSS_LIMIT`。
- Reviewer 实例化时读取（reviewer.py:55），需重启交易进程生效。

## 备注

- 无 delta spec：`counterfactual-portfolio-sim` spec 以「Reviewer 阈值常数」参数化引用该值，不锁定字面量 3，故该 change 不触及任何已有 spec 的验收场景。
