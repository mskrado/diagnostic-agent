"""Model snapshot helpers for eval/audit reference."""
from __future__ import annotations

from app.config import Settings


def test_settings_model_snapshot_keys():
    snap = Settings(
        chat_provider="bedrock_converse",
        chat_model="amazon.nova-micro-v1:0",
        embed_provider="bedrock",
        embed_model="amazon.titan-embed-text-v2:0",
    ).model_snapshot()
    assert snap == {
        "chat_provider": "bedrock_converse",
        "chat_model": "amazon.nova-micro-v1:0",
        "embed_provider": "bedrock",
        "embed_model": "amazon.titan-embed-text-v2:0",
    }
