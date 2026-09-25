import logging
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from langchain_core.messages import SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

from app.ai_tools import (
    get_category_totals,
    get_top_expenses,
    get_transactions_summary,
    prepare_create_transaction,
    prepare_delete_transaction,
    prepare_update_transaction,
)
from app.check_gemini_api_key import get_gemini_api_key
from app.prompts import get_chat_assistant_prompt

logger = logging.getLogger("uvicorn.error")


class GeminiChatError(RuntimeError):
    """Gemini не зміг повернути відповідь у чаті."""


# ── Інструменти ──────────────────────────────────────────────────

_tools = [
    get_transactions_summary,
    get_category_totals,
    get_top_expenses,
    prepare_create_transaction,
    prepare_update_transaction,
    prepare_delete_transaction,
]


# ── LangGraph State ──────────────────────────────────────────────

class ChatState(TypedDict):
    """Стан графу: список повідомлень з автоматичним append."""
    messages: Annotated[list, add_messages]


# ── LLM з fallback та інструментами ──────────────────────────────

def _build_llm():
    """Створює ChatGoogleGenerativeAI з fallback-моделями та прив'язаними tools."""
    api_key = get_gemini_api_key()

    primary = ChatGoogleGenerativeAI(
        model="gemini-3-flash-preview",
        google_api_key=api_key,
        temperature=0.4,
    )
    fallback_1 = ChatGoogleGenerativeAI(
        model="gemini-3.6-flash",
        google_api_key=api_key,
        temperature=0.4,
    )
    fallback_2 = ChatGoogleGenerativeAI(
        model="gemini-3.5-flash",
        google_api_key=api_key,
        temperature=0.4,
    )

    llm_with_fallbacks = primary.with_fallbacks([fallback_1, fallback_2])
    return llm_with_fallbacks.bind_tools(_tools)


# ── Граф ─────────────────────────────────────────────────────────

_memory = MemorySaver()
_llm = _build_llm()

# Ми більше не потребуємо кешувати системний промпт, бо він статичний 
# (окрім поточної дати, яка оновлюється).


def _chatbot_node(state: ChatState) -> dict:
    """Нода графу: викликає LLM з усією історією повідомлень."""
    return {"messages": [_llm.invoke(state["messages"])]}


def _build_graph() -> StateGraph:
    """Будує та компілює граф з інструментами та checkpointer."""
    graph_builder = StateGraph(ChatState)
    
    # Додаємо ноди
    graph_builder.add_node("chatbot", _chatbot_node)
    graph_builder.add_node("tools", ToolNode(_tools))
    
    # Будуємо маршрути
    graph_builder.add_edge(START, "chatbot")
    
    # Якщо модель викликала tool, йдемо в "tools", інакше в END
    graph_builder.add_conditional_edges(
        "chatbot",
        tools_condition,
        {"tools": "tools", END: END},
    )
    
    # Після виконання tool завжди повертаємось до chatbot для генерації фінальної відповіді
    graph_builder.add_edge("tools", "chatbot")
    
    return graph_builder.compile(checkpointer=_memory)


_graph = _build_graph()


# ── Публічний API ────────────────────────────────────────────────

async def generate_chat_response(
    thread_id: str,
    user_message: str,
    telegram_id: int,
) -> tuple[str, dict | None]:
    """Генерує відповідь AI-асистента через LangGraph з інструментами.

    Returns:
        Tuple of (response_text, pending_action_data | None).
    """
    import json as _json

    from app.ai_actions import get_pending_action

    # Формуємо актуальний системний промпт із поточною датою
    current_date = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d")
    system_prompt = get_chat_assistant_prompt(current_date)

    messages_to_send = [
        SystemMessage(content=system_prompt),
        ("user", user_message),
    ]

    # Передаємо telegram_id в RunnableConfig, щоб tools могли його дістати
    config = {
        "configurable": {
            "thread_id": thread_id,
            "telegram_id": telegram_id,
        }
    }

    try:
        result = await _graph.ainvoke(
            {"messages": messages_to_send},
            config=config,
        )

        assistant_message = result["messages"][-1]
        
        # У нових версіях LangChain content може бути списком блоків, якщо є thoughts
        raw_content = assistant_message.content
        if isinstance(raw_content, list):
            # Шукаємо блок з типом 'text'
            text_blocks = [block.get("text", "") for block in raw_content if isinstance(block, dict) and "text" in block]
            response_text = "\n".join(text_blocks)
        else:
            response_text = str(raw_content) if raw_content else ""

        # Шукаємо pending action у tool messages
        pending_action_data = None
        for msg in result["messages"]:
            if msg.type == "tool" and isinstance(msg.content, str):
                try:
                    tool_result = _json.loads(msg.content)
                    if isinstance(tool_result, dict) and "action_id" in tool_result:
                        action = get_pending_action(tool_result["action_id"])
                        if action is not None:
                            pending_action_data = {
                                "action_id": action.action_id,
                                "type": action.action_type.value,
                                "payload": action.payload,
                            }
                except (_json.JSONDecodeError, KeyError):
                    pass

        logger.info("Chat: LangGraph responded for thread_id=%s with tools", thread_id)
        return response_text, pending_action_data

    except Exception as error:
        logger.error(
            "Chat: LangGraph failed: error_type=%s, detail=%s",
            type(error).__name__,
            str(error)[:200],
        )
        raise GeminiChatError from error

