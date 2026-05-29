"""P1-G: LLM JSON schema 校验 + 审计留痕 测试"""
import sys
import os
import json
import datetime
sys.path.insert(0, '.')


def test_schema_pass_when_all_fields_present():
    """完整字段 → 无 errors"""
    from agents.llm_client import validate_against_schema, JUDGE_DECISION_SCHEMA
    data = {
        'action': 'open_long',
        'confidence': 75,
        'reasoning': '趋势明确',
        'key_factors': ['MA金叉', 'OI正背离'],
        'risk_warnings': ['RSI接近70'],
    }
    cleaned, errors = validate_against_schema(data, JUDGE_DECISION_SCHEMA)
    assert errors == [], f"应无 errors，实际 {errors}"
    assert cleaned['action'] == 'open_long'
    assert cleaned['confidence'] == 75
    print("  ✅ Case 1: 完整字段通过校验")


def test_schema_fills_missing_fields():
    """缺字段 → 用默认值填充"""
    from agents.llm_client import validate_against_schema, JUDGE_DECISION_SCHEMA
    data = {'action': 'open_short'}  # 只有一个字段
    cleaned, errors = validate_against_schema(data, JUDGE_DECISION_SCHEMA)
    assert 'missing:confidence' in errors
    assert 'missing:reasoning' in errors
    assert cleaned['confidence'] == 40  # 默认值
    assert cleaned['reasoning'] == ''
    assert cleaned['key_factors'] == []
    print(f"  ✅ Case 2: 缺字段填充默认值 (errors={len(errors)})")


def test_schema_rejects_invalid_action():
    """非法 action → 改为默认 hold"""
    from agents.llm_client import validate_against_schema, JUDGE_DECISION_SCHEMA
    data = {
        'action': 'yolo_long',  # 非白名单
        'confidence': 80,
        'reasoning': 'test',
        'key_factors': [],
        'risk_warnings': [],
    }
    cleaned, errors = validate_against_schema(data, JUDGE_DECISION_SCHEMA)
    assert cleaned['action'] == 'hold'
    assert any('not_allowed:action' in e for e in errors)
    print(f"  ✅ Case 3: 非法 action='yolo_long' → hold")


def test_schema_clamps_confidence_range():
    """confidence 越界 → clamp"""
    from agents.llm_client import validate_against_schema, JUDGE_DECISION_SCHEMA
    data = {
        'action': 'open_long',
        'confidence': 150,  # 超出 [0,100]
        'reasoning': '',
        'key_factors': [],
        'risk_warnings': [],
    }
    cleaned, errors = validate_against_schema(data, JUDGE_DECISION_SCHEMA)
    assert cleaned['confidence'] == 100, f"应 clamp 到 100，实际 {cleaned['confidence']}"
    print(f"  ✅ Case 4: confidence=150 → clamp=100")


def test_schema_handles_non_dict_root():
    """LLM 返回了非 dict（如 list） → 全字段用默认值"""
    from agents.llm_client import validate_against_schema, JUDGE_DECISION_SCHEMA
    cleaned, errors = validate_against_schema(['not', 'a', 'dict'], JUDGE_DECISION_SCHEMA)
    assert 'bad_root' in errors[0]
    assert cleaned['action'] == 'hold'
    assert cleaned['confidence'] == 40
    print("  ✅ Case 5: 非 dict 根节点 → 全部默认值")


def test_schema_type_coercion():
    """类型可转换 → 自动转，否则用默认值"""
    from agents.llm_client import validate_against_schema, JUDGE_DECISION_SCHEMA
    data = {
        'action': 'hold',
        'confidence': '65',  # 字符串数字
        'reasoning': 123,  # 数字应该是 str
        'key_factors': [],
        'risk_warnings': [],
    }
    cleaned, errors = validate_against_schema(data, JUDGE_DECISION_SCHEMA)
    assert cleaned['confidence'] == 65, f"字符串数字应转 int=65，实际 {cleaned['confidence']}"
    # reasoning 是 int → str 类型转换可能成功（str(123)='123'）
    assert isinstance(cleaned['reasoning'], str)
    print(f"  ✅ Case 6: 类型转换 confidence='65'→65, reasoning=123→'123'")


def test_audit_log_written():
    """触发 _audit_log 后应有文件产出"""
    from agents.llm_client import LLMClient

    today = datetime.datetime.utcnow().strftime('%Y%m%d')
    path = f'logs/llm_audit_{today}.jsonl'
    # 清理已有文件以便测试干净
    if os.path.exists(path):
        existing_size = os.path.getsize(path)
    else:
        existing_size = 0

    client = LLMClient.__new__(LLMClient)
    client.logger = __import__('logging').getLogger('test')
    client._audit_log({
        'ts': 1234567890,
        'caller': 'test_p1g',
        'model': 'test-model',
        'latency_ms': 100,
        'system_hash': 'abc12345',
        'user_msg': 'test message',
        'raw_response': '{"action":"hold"}',
        'parsed': {'action': 'hold'},
        'parse_error': None,
        'validation_errors': [],
    })

    assert os.path.exists(path), f"审计日志文件应存在: {path}"
    new_size = os.path.getsize(path)
    assert new_size > existing_size, "审计日志应有新行写入"

    # 读最后一行确认内容
    with open(path, 'r', encoding='utf-8') as f:
        last_line = f.readlines()[-1]
    rec = json.loads(last_line)
    assert rec['caller'] == 'test_p1g'
    assert rec['parsed']['action'] == 'hold'
    print(f"  ✅ Case 7: 审计日志写入 {path}")


def main():
    print("=" * 60)
    print("P1-G: LLM JSON schema 校验 + 审计留痕 测试")
    print("=" * 60)
    test_schema_pass_when_all_fields_present()
    test_schema_fills_missing_fields()
    test_schema_rejects_invalid_action()
    test_schema_clamps_confidence_range()
    test_schema_handles_non_dict_root()
    test_schema_type_coercion()
    test_audit_log_written()
    print("\n" + "=" * 60)
    print("✅ 全部 7 个测试通过")
    print("=" * 60)


def test_synthesis_schema_fills_defaults():
    """AC-P1-011: Synthesizer schema 缺字段填充"""
    from agents.llm_client import validate_against_schema, SYNTHESIS_SCHEMA
    data = {}
    cleaned, errors = validate_against_schema(data, SYNTHESIS_SCHEMA)
    assert cleaned['selected_symbols'] == []
    assert cleaned['reasoning'] == ''
    assert 'missing:selected_symbols' in errors


def test_censor_schema_fills_defaults():
    """AC-P1-012: Censor schema 缺字段填充"""
    from agents.llm_client import validate_against_schema, CENSOR_SCHEMA
    data = {'challenges': [{'symbol': 'BTC', 'recommendation': 'reject'}]}
    cleaned, errors = validate_against_schema(data, CENSOR_SCHEMA)
    assert len(cleaned['challenges']) == 1
    assert cleaned['systemic_risks'] == []
    assert cleaned['overall_verdict'] == ''


def test_tech_analyst_schema_invalid_direction():
    """AC-P1-012: TechAnalyst schema 非法 direction → neutral"""
    from agents.llm_client import validate_against_schema, TECH_ANALYST_SCHEMA
    data = {
        'direction': 'moon',
        'confidence': 80,
        'reasoning': 'test',
        'key_factors': [],
        'risk_warnings': [],
    }
    cleaned, errors = validate_against_schema(data, TECH_ANALYST_SCHEMA)
    assert cleaned['direction'] == 'neutral'
    assert any('not_allowed:direction' in e for e in errors)


def test_tech_analyst_schema_clamps_confidence():
    """AC-P1-012: TechAnalyst confidence 越界 clamp"""
    from agents.llm_client import validate_against_schema, TECH_ANALYST_SCHEMA
    data = {
        'direction': 'bullish',
        'confidence': -10,
        'reasoning': '',
        'key_factors': [],
        'risk_warnings': [],
    }
    cleaned, errors = validate_against_schema(data, TECH_ANALYST_SCHEMA)
    assert cleaned['confidence'] == 0


def test_behavioral_critic_schema_fills_defaults():
    """AC-P1-012: BehavioralCritic schema 缺字段填充"""
    from agents.llm_client import validate_against_schema, BEHAVIORAL_CRITIC_SCHEMA
    data = {'bias_detected': 'loss_aversion'}
    cleaned, errors = validate_against_schema(data, BEHAVIORAL_CRITIC_SCHEMA)
    assert cleaned['bias_detected'] == 'loss_aversion'
    assert cleaned['severity'] == 'none'
    assert cleaned['confidence_in_challenge'] == 0
    assert cleaned['counter_recommendation'] == ''
    assert 'missing:severity' in errors


def test_behavioral_critic_schema_invalid_severity():
    """AC-P1-012: BehavioralCritic 非法 severity → none"""
    from agents.llm_client import validate_against_schema, BEHAVIORAL_CRITIC_SCHEMA
    data = {
        'bias_detected': 'fomo',
        'severity': 'extreme',
        'challenge': 'test',
        'counter_recommendation': 'hold',
        'confidence_in_challenge': 70,
    }
    cleaned, errors = validate_against_schema(data, BEHAVIORAL_CRITIC_SCHEMA)
    assert cleaned['severity'] == 'none'
    assert any('not_allowed:severity' in e for e in errors)
    assert cleaned['counter_recommendation'] == 'hold'
    assert cleaned['confidence_in_challenge'] == 70


def test_final_synthesis_schema():
    """AC-P1-011: Final synthesis schema"""
    from agents.llm_client import validate_against_schema, FINAL_SYNTHESIS_SCHEMA
    data = {'final_symbols': ['BTC-USDT', 'ETH-USDT']}
    cleaned, errors = validate_against_schema(data, FINAL_SYNTHESIS_SCHEMA)
    assert cleaned['final_symbols'] == ['BTC-USDT', 'ETH-USDT']
    assert cleaned['market_regime'] == 'unknown'
    assert cleaned['censor_response'] == ''


if __name__ == '__main__':
    main()
