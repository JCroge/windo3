import importlib.util, json, os


def _load_module():
    path = os.path.join(os.path.dirname(__file__), "..", "cf_direction_recommendation.py")
    spec = importlib.util.spec_from_file_location("cf_direction_recommendation", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_load_records_filters_v1_and_empty_tech(tmp_path, monkeypatch):
    mod = _load_module()
    tape = tmp_path / "tape.jsonl"
    rows = [
        {"schema_version": "decision_replay_record.v1", "tech_analysis": {}, "replayable": True},
        {"schema_version": "decision_replay_record.v2", "tech_analysis": {}, "replayable": True},
        {"schema_version": "decision_replay_record.v2",
         "tech_analysis": {"rule_signal": {}}, "replayable": True},
    ]
    tape.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(mod, "TAPE", str(tape))
    recs = mod.load_records()
    assert len(recs) == 1
    assert recs[0]["tech_analysis"] == {"rule_signal": {}}
