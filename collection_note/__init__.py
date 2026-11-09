"""兼容 shim：已迁移至 notes/ 包。"""
from notes.openai_client import OpenRouterOpenAIClient, VertexAIOpenAIClient  # noqa: F401
from notes.workflow import (  # noqa: F401
    CollectionAnalysisState,
    analyze_collection_dialogs,
    build_collection_note_graph,
)

__all__ = [
    "CollectionAnalysisState",
    "OpenRouterOpenAIClient",
    "VertexAIOpenAIClient",
    "analyze_collection_dialogs",
    "build_collection_note_graph",
]
