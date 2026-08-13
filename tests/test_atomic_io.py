import json
import threading
from concurrent.futures import ThreadPoolExecutor


def test_atomic_write_json_concurrent_writers_use_independent_temp_files(
    tmp_path, monkeypatch
):
    import utils.atomic_io as atomic_io

    path = tmp_path / "state.json"
    payloads = ({"writer": 1}, {"writer": 2})
    replace_barrier = threading.Barrier(len(payloads))
    real_replace = atomic_io.os.replace

    def synchronized_replace(source, destination):
        replace_barrier.wait(timeout=5)
        real_replace(source, destination)

    monkeypatch.setattr(atomic_io.os, "replace", synchronized_replace)

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        futures = [
            pool.submit(atomic_io.atomic_write_json, str(path), payload)
            for payload in payloads
        ]
        for future in futures:
            future.result(timeout=5)

    assert json.loads(path.read_text(encoding="utf-8")) in payloads
    assert list(tmp_path.glob("state.json*.tmp")) == []
