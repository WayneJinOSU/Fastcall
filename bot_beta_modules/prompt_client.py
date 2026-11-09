"""兼容 shim：已迁移至 integrations/harness.py。"""
from integrations.harness import (  # noqa: F401
    CollectionHarnessPrompt,
    CollectionHarnessPromptError,
    extract_prompt_uuid,
    fetch_collection_harness_prompt,
    resolve_collection_harness_llm_prompt,
)
