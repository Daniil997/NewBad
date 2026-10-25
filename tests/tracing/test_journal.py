from app.db import connect, init_db
from app.tracing.journal import log_llm_call


def test_llm_metadata_is_logged_without_prompts():
    init_db()
    log_llm_call("configured-model", "local", "local_only", tokens=17)
    with connect() as connection:
        row = dict(connection.execute("SELECT * FROM llm_calls").fetchone())
    assert row["model"] == "configured-model"
    assert row["tokens"] == 17
    assert row["prompt_hash"] is None
