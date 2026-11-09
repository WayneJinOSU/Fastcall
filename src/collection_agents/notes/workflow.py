import asyncio
import datetime as dt
import json
import logging
import os
import re
from typing import Any, Literal, TypedDict

from collection_agents.text.time_utils import country_tz_mapping

try:
    from .openai_client import OpenRouterOpenAIClient, VertexAIOpenAIClient
    from .prompts import (
        BASIC_INTENT_PROMPT,
        NEGATIVE_INTENT_PROMPT,
        NEGATIVE_REMARK_PROMPT,
        POSITIVE_INTENT_PROMPT,
        POSITIVE_REMARK_PROMPT,
        PROMISE_DATE_PROMPT,
        ROUTING_SUMMARY_PROMPT,
        VOICEMAIL_PROMPT,
    )
except ImportError:  # pragma: no cover - allows running this file directly
    from collection_agents.notes.openai_client import OpenRouterOpenAIClient, VertexAIOpenAIClient
    from collection_agents.notes.prompts import (
        BASIC_INTENT_PROMPT,
        NEGATIVE_INTENT_PROMPT,
        NEGATIVE_REMARK_PROMPT,
        POSITIVE_INTENT_PROMPT,
        POSITIVE_REMARK_PROMPT,
        PROMISE_DATE_PROMPT,
        ROUTING_SUMMARY_PROMPT,
        VOICEMAIL_PROMPT,
    )

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - optional runtime dependency during local editing
    END = START = StateGraph = None


LOGGER = logging.getLogger(__name__)
DEFAULT_COLLECTION_COUNTRY = "mexico"
COUNTRY_ALIASES = {
    "mx": "mexico",
    "co": "colombia",
    "cl": "chile",
    "pk": "pakistan",
    "pe": "peru",
    "ph": "philippines",
    "vn": "vietnam",
    "ec": "ecuador",
}

DEFAULT_VOICEMAIL_RESULT = {
    "contactSuccess": True,
    "repaymentWish": "no",
    "collectionResult": "no_voicemail",
    "promiseRepayment": False,
    "promiseRepaymentTime": "",
    "repeatBorrowingWish": "low",
    "remark": "voicemail",
}

VOICEMAIL_KEYWORDS = (
    "correo de voz",
    "tu mensaje",
    "área fuera de servicio",
    "area fuera de servicio",
    "pulsa la tecla de almohadilla",
    "uzón de voz",
    "su mensaje",
    "vicio de buzón",
    "usted es el cliente número",
    "servicio de",
    "de servicio",
    "el número marcado",
    "el numero marcado",
)

PROMISE_REPAYMENT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

POSITIVE_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "repaymentWish": {"type": "string", "enum": ["yes"]},
        "collectionResult": {
            "type": "string",
            "enum": [
                "yes_partial_repayment",
                "yes_willing_no_money",
                "yes_settled_not_updated",
                "yes_promise_today",
                "yes_promise_future_days",
                "yes_contact_not_know_user",
                "yes_contact_will_inform",
            ],
        },
        "repeatBorrowingWish": {"type": "string", "enum": ["high", "low"]},
    },
    "required": ["repaymentWish", "collectionResult", "repeatBorrowingWish"],
}

NEGATIVE_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "repaymentWish": {"type": "string", "enum": ["no"]},
        "collectionResult": {
            "type": "string",
            "enum": [
                "no_no_money",
                "no_not_received_loan",
                "no_cannot_operate_repayment",
                "no_no_salary",
                "no_explicit_refusal",
                "not_answered_no_service",
                "not_answered_number_not_exist",
                "not_answered_power_off",
                "not_answered_no_answer",
                "no_other",
            ],
        },
        "repeatBorrowingWish": {"type": "string", "enum": ["high", "low"]},
    },
    "required": ["repaymentWish", "collectionResult", "repeatBorrowingWish"],
}

MERGE_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "repaymentWish": {"type": "string", "enum": ["yes", "no"]},
        "collectionResult": {"type": "string"},
        "repeatBorrowingWish": {"type": "string", "enum": ["high", "low"]},
        "remark": {"type": "string"},
    },
    "required": [
        "repaymentWish",
        "collectionResult",
        "repeatBorrowingWish",
        "remark",
    ],
}


class CollectionAnalysisState(TypedDict, total=False):
    language: str
    country: str
    now: dt.datetime
    raw_dialogs: Any
    dialogs: list[dict[str, Any]]
    user_history: str
    full_dialog_text: str
    extracted_history_present: bool
    keyword_voicemail_hit: bool
    routing_summary: str
    voicemail_llm_result: str
    basic_intent_result: str
    positive_intent: dict[str, str]
    negative_intent: dict[str, str]
    pay_day_text: str
    promise_repayment: str
    promise_repayment_time: str
    positive_remark: str
    negative_remark: str
    final_result: dict[str, Any]
    llm_client: Any


def build_collection_note_graph():
    if StateGraph is None:
        raise RuntimeError("langgraph is not installed in the current environment")

    graph = StateGraph(CollectionAnalysisState)
    graph.add_node("prepare_inputs", node_prepare_inputs)
    graph.add_node("extract_user_history", node_extract_user_history)
    graph.add_node("finalize_voicemail", node_finalize_voicemail)
    graph.add_node("routing_summary", node_routing_summary)
    graph.add_node("voicemail_llm_check", node_voicemail_llm_check)
    graph.add_node("basic_intent", node_basic_intent)
    graph.add_node("positive_intent", node_positive_intent)
    graph.add_node("promise_date", node_promise_date)
    graph.add_node("promise_time_code", node_promise_time_code)
    graph.add_node("positive_remark", node_positive_remark)
    graph.add_node("positive_merge", node_positive_merge)
    graph.add_node("negative_intent", node_negative_intent)
    graph.add_node("negative_remark", node_negative_remark)
    graph.add_node("negative_merge", node_negative_merge)

    graph.add_edge(START, "prepare_inputs")
    graph.add_edge("prepare_inputs", "extract_user_history")
    graph.add_conditional_edges(
        "extract_user_history",
        route_after_history_extraction,
        {
            "voicemail": "finalize_voicemail",
            "summary": "routing_summary",
        },
    )
    graph.add_edge("finalize_voicemail", END)
    graph.add_edge("routing_summary", "voicemail_llm_check")
    graph.add_conditional_edges(
        "voicemail_llm_check",
        route_after_voicemail_llm,
        {
            "voicemail": "finalize_voicemail",
            "intent": "basic_intent",
        },
    )
    graph.add_conditional_edges(
        "basic_intent",
        route_after_basic_intent,
        {
            "positive": "positive_intent",
            "negative": "negative_intent",
        },
    )
    graph.add_edge("positive_intent", "promise_date")
    graph.add_edge("promise_date", "promise_time_code")
    graph.add_edge("promise_time_code", "positive_remark")
    graph.add_edge("positive_remark", "positive_merge")
    graph.add_edge("positive_merge", END)
    graph.add_edge("negative_intent", "negative_remark")
    graph.add_edge("negative_remark", "negative_merge")
    graph.add_edge("negative_merge", END)
    return graph.compile()


async def analyze_collection_dialogs(
    dialogs: Any,
    language: str = "Spanish",
    country: str = DEFAULT_COLLECTION_COUNTRY,
    now: dt.datetime | None = None,
    llm_client: VertexAIOpenAIClient | None = None,
) -> dict[str, Any]:
    normalized_country = _normalize_country(country)
    initial_state: CollectionAnalysisState = {
        "language": language,
        "country": normalized_country,
        "now": _resolve_reference_now(now=now, country=normalized_country),
        "raw_dialogs": dialogs,
        "llm_client": llm_client or _build_default_llm_client(),
    }

    if StateGraph is None:
        LOGGER.warning("langgraph is not installed; using sequential execution fallback")
        return await _run_without_langgraph(initial_state)

    graph = build_collection_note_graph()
    result = await graph.ainvoke(initial_state)
    return result["final_result"]


def _build_default_llm_client():
    return OpenRouterOpenAIClient.from_env()


async def _run_without_langgraph(state: CollectionAnalysisState) -> dict[str, Any]:
    state.update(await node_prepare_inputs(state))
    state.update(await node_extract_user_history(state))
    if route_after_history_extraction(state) == "voicemail":
        state.update(await node_finalize_voicemail(state))
        return state["final_result"]

    state.update(await node_routing_summary(state))
    state.update(await node_voicemail_llm_check(state))
    if route_after_voicemail_llm(state) == "voicemail":
        state.update(await node_finalize_voicemail(state))
        return state["final_result"]

    state.update(await node_basic_intent(state))
    if route_after_basic_intent(state) == "positive":
        state.update(await node_positive_intent(state))
        state.update(await node_promise_date(state))
        state.update(await node_promise_time_code(state))
        state.update(await node_positive_remark(state))
        state.update(await node_positive_merge(state))
    else:
        state.update(await node_negative_intent(state))
        state.update(await node_negative_remark(state))
        state.update(await node_negative_merge(state))
    return state["final_result"]


async def node_prepare_inputs(state: CollectionAnalysisState) -> dict[str, Any]:
    dialogs = _coerce_dialogs(state.get("raw_dialogs"))
    full_dialog_text = _stringify_dialogs(dialogs)
    return {
        "dialogs": dialogs,
        "full_dialog_text": full_dialog_text,
    }


async def node_extract_user_history(state: CollectionAnalysisState) -> dict[str, Any]:
    user_parts: list[str] = []
    for dialog in state.get("dialogs", []):
        if str(dialog.get("speaker", "")).lower() == "user":
            content = str(dialog.get("content", "")).strip()
            if content:
                user_parts.append(content)

    user_history = "\n".join(user_parts).lower()
    keyword_voicemail_hit = _contains_voicemail_keyword(user_history)
    return {
        "user_history": user_history,
        "extracted_history_present": bool(user_history.strip()),
        "keyword_voicemail_hit": keyword_voicemail_hit,
    }


async def node_finalize_voicemail(state: CollectionAnalysisState) -> dict[str, Any]:
    result = dict(DEFAULT_VOICEMAIL_RESULT)
    return {"final_result": result}


async def node_routing_summary(state: CollectionAnalysisState) -> dict[str, Any]:
    client = state["llm_client"]
    summary = await client.generate_text(
        model=client.flash_model,
        system_prompt=ROUTING_SUMMARY_PROMPT.format(language=state["language"]),
        user_prompt=state["full_dialog_text"],
        temperature=0,
    )
    return {"routing_summary": summary.strip()}


async def node_voicemail_llm_check(state: CollectionAnalysisState) -> dict[str, Any]:
    client = state["llm_client"]
    result = await client.generate_text(
        model=client.flash_model,
        system_prompt=VOICEMAIL_PROMPT,
        user_prompt=state["user_history"],
        temperature=0,
        response_mime_type="text/x.enum",
        response_schema={"type": "string", "enum": ["JG-GOOD", "JG-BAD"]},
    )
    return {"voicemail_llm_result": result.strip()}


async def node_basic_intent(state: CollectionAnalysisState) -> dict[str, Any]:
    client = state["llm_client"]
    result = await client.generate_text(
        model=client.flash_model,
        system_prompt=BASIC_INTENT_PROMPT,
        user_prompt=state["routing_summary"],
        temperature=0,
        response_mime_type="text/x.enum",
        response_schema={"type": "string", "enum": ["yes", "no"]},
    )
    return {"basic_intent_result": result.strip().lower()}


async def node_positive_intent(state: CollectionAnalysisState) -> dict[str, Any]:
    client = state["llm_client"]
    result = await client.generate_json(
        model=client.flash_model,
        system_prompt=POSITIVE_INTENT_PROMPT,
        user_prompt=f"完整对话如下：\n{state['full_dialog_text']}",
        schema=POSITIVE_INTENT_SCHEMA,
        temperature=0,
    )
    return {"positive_intent": result}


async def node_promise_date(state: CollectionAnalysisState) -> dict[str, Any]:
    client = state["llm_client"]
    result = await client.generate_text(
        model=client.flash_model,
        system_prompt=PROMISE_DATE_PROMPT,
        user_prompt=(
            f"Reference current datetime: {state['now'].strftime(PROMISE_REPAYMENT_TIME_FORMAT)}\n"
            f"{state['full_dialog_text']}"
        ),
        temperature=0,
    )
    return {"pay_day_text": result.strip()}


async def node_promise_time_code(state: CollectionAnalysisState) -> dict[str, Any]:
    pay_hours_text = state.get("pay_day_text", "")
    match = re.search(r"[-+]?(?:\d+(?:[.,]\d+)?|\.\d+)", pay_hours_text)
    if not match:
        return {
            "promise_repayment": "false",
            "promise_repayment_time": "",
        }

    try:
        promise_hours = float(match.group().replace(",", "."))
    except ValueError:
        promise_hours = -1

    # The model returns -100 for tomorrow/later, unknown expressions, and
    # anything outside the same-day scope. Reject negative and unreasonable
    # values before doing any datetime arithmetic.
    if promise_hours < 0 or promise_hours > 24:
        return {
            "promise_repayment": "false",
            "promise_repayment_time": "",
        }

    promise_hours = round(promise_hours, 1)
    promise_timestamp = state["now"] + dt.timedelta(
        minutes=round(promise_hours * 60)
    )
    tzinfo = getattr(promise_timestamp, "tzinfo", None)
    if tzinfo is not None and hasattr(tzinfo, "normalize"):
        promise_timestamp = tzinfo.normalize(promise_timestamp)

    # Only today's local promise times are in scope. This also handles a
    # same-day offset that crosses midnight.
    if promise_timestamp.date() != state["now"].date():
        return {
            "promise_repayment": "false",
            "promise_repayment_time": "",
        }

    return {
        "promise_repayment": "true",
        "promise_repayment_time": promise_timestamp.strftime(PROMISE_REPAYMENT_TIME_FORMAT),
    }


async def node_positive_remark(state: CollectionAnalysisState) -> dict[str, Any]:
    client = state["llm_client"]
    remark = await client.generate_text(
        model=client.lite_model,
        system_prompt=POSITIVE_REMARK_PROMPT.format(language=state["language"]),
        user_prompt=(
            f"{json.dumps(state['positive_intent'], ensure_ascii=False)}\n"
            f"{state['routing_summary']}\n"
            f"{state['pay_day_text']}"
        ),
        temperature=0,
    )
    return {"positive_remark": remark.strip()}


async def node_positive_merge(state: CollectionAnalysisState) -> dict[str, Any]:
    client = state["llm_client"]
    merged = await client.generate_json(
        model=client.lite_model,
        system_prompt=(
            "You are responsible for merging the results.\n"
            "The results will be returned in the following format:\n"
            "{\n"
            '  "repaymentWish": "yes",\n'
            '  "collectionResult": "yes_partial_repayment",\n'
            '  "repeatBorrowingWish": "high",\n'
            '  "remark": ""\n'
            "}\n"
            "Do not output promiseRepayment or promiseRepaymentTime. "
            "Those fields are calculated by the workflow code."
        ),
        user_prompt=(
            f"{json.dumps(state['positive_intent'], ensure_ascii=False)}\n"
            f"remark:{state.get('positive_remark', '')}"
        ),
        schema=MERGE_RESULT_SCHEMA,
        temperature=0,
    )
    final_result = _normalize_final_result(merged)
    final_result["promiseRepayment"] = state.get("promise_repayment", "false") == "true"
    final_result["promiseRepaymentTime"] = state.get("promise_repayment_time", "")
    return {"final_result": final_result}


async def node_negative_intent(state: CollectionAnalysisState) -> dict[str, Any]:
    client = state["llm_client"]
    result = await client.generate_json(
        model=client.flash_model,
        system_prompt=NEGATIVE_INTENT_PROMPT,
        user_prompt=f"完整对话如下：\n{state['full_dialog_text']}",
        schema=NEGATIVE_INTENT_SCHEMA,
        temperature=0,
    )
    return {"negative_intent": result}


async def node_negative_remark(state: CollectionAnalysisState) -> dict[str, Any]:
    client = state["llm_client"]
    remark = await client.generate_text(
        model=client.lite_model,
        system_prompt=NEGATIVE_REMARK_PROMPT.format(language=state["language"]),
        user_prompt=(
            f"{json.dumps(state['negative_intent'], ensure_ascii=False)}\n"
            f"{state['routing_summary']}\n"
        ),
        temperature=0,
    )
    return {"negative_remark": remark.strip()}


async def node_negative_merge(state: CollectionAnalysisState) -> dict[str, Any]:
    client = state["llm_client"]
    merged = await client.generate_json(
        model=client.lite_model,
        system_prompt=(
            "You are responsible for merging the results.\n"
            "The results will be returned in the following format:\n"
            "{\n"
            '  "repaymentWish": "no",\n'
            '  "collectionResult": "no_other",\n'
            '  "repeatBorrowingWish": "low",\n'
            '  "remark": ""\n'
            "}\n"
            "Do not output promiseRepayment or promiseRepaymentTime."
        ),
        user_prompt=(
            f"{json.dumps(state['negative_intent'], ensure_ascii=False)}\n"
            "promiseRepayment:false\n"
            f"remark:{state.get('negative_remark', '')}"
        ),
        schema=MERGE_RESULT_SCHEMA,
        temperature=0,
    )
    final_result = _normalize_final_result(merged)
    final_result["promiseRepayment"] = False
    final_result["promiseRepaymentTime"] = ""
    return {"final_result": final_result}


def route_after_history_extraction(state: CollectionAnalysisState) -> str:
    if not state.get("extracted_history_present", False):
        return "voicemail"
    if state.get("keyword_voicemail_hit", False):
        return "voicemail"
    return "summary"


def route_after_voicemail_llm(state: CollectionAnalysisState) -> str:
    if "JG-BAD" in state.get("voicemail_llm_result", ""):
        return "intent"
    return "voicemail"


def route_after_basic_intent(state: CollectionAnalysisState) -> str:
    if "yes" in state.get("basic_intent_result", ""):
        return "positive"
    return "negative"


def _coerce_dialogs(raw_dialogs: Any) -> list[dict[str, Any]]:
    if isinstance(raw_dialogs, list):
        return [item for item in raw_dialogs if isinstance(item, dict)]

    if isinstance(raw_dialogs, str):
        text = raw_dialogs.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = json.loads(text.replace("'", '"'))
            except json.JSONDecodeError:
                return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []


def _stringify_dialogs(dialogs: list[dict[str, Any]]) -> str:
    return json.dumps(dialogs, ensure_ascii=False)


def _contains_voicemail_keyword(history: str) -> bool:
    lower_text = history.lower()
    return any(keyword in lower_text for keyword in VOICEMAIL_KEYWORDS)


def _normalize_country(country: str | None) -> str:
    normalized = str(country or "").strip().lower()
    if not normalized:
        return DEFAULT_COLLECTION_COUNTRY
    return COUNTRY_ALIASES.get(normalized, normalized)


def _resolve_reference_now(now: dt.datetime | None, country: str) -> dt.datetime:
    tz = country_tz_mapping.get(_normalize_country(country))
    if tz is None:
        return now or dt.datetime.now()

    if now is None:
        return dt.datetime.now(tz)

    if now.tzinfo is None:
        return tz.localize(now)

    return now.astimezone(tz)


def _normalize_final_result(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    normalized["promiseRepayment"] = str(normalized.get("promiseRepayment", "false")).lower() == "true"
    normalized["contactSuccess"] = True
    return normalized


async def _run_demo() -> None:
    sample_dialogs = [
        {
            "speaker": "bot",
            "content": "Hola, le llamamos por su pago pendiente. Queremos confirmar su plan de pago.",
            "timestamp": 1744520400000,
        },
        {
            "speaker": "user",
            "content": "No tengo dinero hoy, pero manana voy a pagar todo.",
        },
        {
            "speaker": "bot",
            "content": "Entendido, registraremos su compromiso de pago.",
            "timestamp": 1744520406000,
        },
    ]

    result = await analyze_collection_dialogs(
        dialogs=sample_dialogs,
        language="Spanish",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(_run_demo())
    except Exception as exc:
        print(f"Workflow demo failed: {exc}")
        print("Check `.env.temp` Vertex AI settings and ensure the current environment can access Google OAuth and Vertex AI endpoints.")
