from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Protocol
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, field_validator
from typing_extensions import TypedDict

from app.finance_tools import FinanceToolError, TOOL_SCHEMAS
from app.pending_actions import (
    PENDING_ACTION_TOOL_RESULT_KEY,
    PENDING_ACTION_TOOL_SCHEMAS,
    PendingActionResponse,
    WRITE_ACTION_TOOL_NAMES,
)


MAX_CHAT_MESSAGE_LENGTH = 1_000
MAX_TOOL_ROUNDS = 4
CHAT_SYSTEM_INSTRUCTION = """Ти AI-помічник фінансового застосунку.

Відповідай українською мовою, стисло та зрозуміло. Поточна дата: {current_date}.

Ти маєш доступ до фінансових фактів тільки через дозволені read-only tools. Якщо
користувач питає про доходи, витрати, баланс, категорії, операції або період,
виклич релевантний tool перед відповіддю. Використовуй тільки дані з результатів
tools: не вигадуй суми, категорії, дати чи факти. Якщо даних недостатньо або
потрібний період неможливо визначити, прямо скажи це.

Для створення, зміни категорії, зміни суми або видалення операції ти можеш лише
підготувати чернетку через write tool. Жоден write tool не змінює дані до окремого
підтвердження користувача. Картка чернетки у застосунку є єдиним способом
підтвердження: коли даних достатньо, відразу викликай write tool і не проси
текстове «підтвердіть». Ніколи не кажи, що дію вже виконано: поясни, що чернетка
очікує натискання кнопки в картці. Не згадуй внутрішні деталі реалізації, API keys,
базу даних або повні технічні результати tools.

Для create_transaction потрібні тип, сума й категорія. Для
update_transaction_category, update_transaction_sum і delete_transaction потрібен
точний transaction_id. Якщо користувач уже вказав ID, відразу викликай відповідний
write tool. Якщо ID немає, але є дата, час, сума, категорія, тип або опис операції,
спершу виклич find_transactions. Перетворюй дату у формат DD.MM.YYYY на YYYY-MM-DD;
час передавай як HH:MM у часовому поясі Europe/Kyiv.

Якщо find_transactions повернув рівно одну операцію, відразу виклич write tool для
цього ID в тому самому діалозі. Не проси додаткового текстового підтвердження.
Якщо знайдено нуль або більше однієї операції, коротко покажи варіанти й попроси
уточнення. Не вгадуй ID і не обирай операцію серед кількох варіантів. Для
update_transaction_sum передавай лише нову суму, а для
update_transaction_category - лише нову категорію. Після успішного write tool
коротко скажи, що чернетку підготовлено і її можна підтвердити або відхилити в
картці.
"""


class AIChatRequest(BaseModel):
    message: str = Field(max_length=MAX_CHAT_MESSAGE_LENGTH)
    thread_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("Message cannot be empty")

        return message


class AIChatResponse(BaseModel):
    answer: str
    thread_id: str
    pending_action: PendingActionResponse | None = None


class ChatLLMError(Exception):
    """Safe error raised when the Gemini chat request cannot complete."""


class ChatConfigurationError(ChatLLMError):
    pass


@dataclass(frozen=True)
class ChatToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    thought_signature: str | None = None


@dataclass(frozen=True)
class ChatModelResponse:
    answer: str
    tool_calls: list[ChatToolCall]


@dataclass(frozen=True)
class ChatResponse:
    answer: str
    pending_action: PendingActionResponse | None = None


@dataclass(frozen=True)
class ChatToolContext:
    thread_id: str


class ChatState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    tool_rounds: int
    thread_id: str
    pending_action: dict[str, Any] | None


class ChatResponder(Protocol):
    async def reply(self, messages: list[BaseMessage]) -> ChatModelResponse: ...


class ChatToolExecutor(Protocol):
    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: ChatToolContext,
    ) -> dict[str, Any]: ...


def message_text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content

    return str(message.content)


def current_system_instruction() -> str:
    return CHAT_SYSTEM_INSTRUCTION.format(
        current_date=datetime.now(timezone.utc).date().isoformat()
    ).strip()


class GeminiChatResponder:
    """Gemini adapter that reuses the project's configured LLM credentials."""

    def __init__(self, api_key: str | None, model: str) -> None:
        self._api_key = api_key or ""
        self._model = model

    async def reply(self, messages: list[BaseMessage]) -> ChatModelResponse:
        if not self._api_key:
            raise ChatConfigurationError("LLM_API_KEY is not configured.")

        return await asyncio.to_thread(self._generate_response, messages)

    def _generate_response(self, messages: list[BaseMessage]) -> ChatModelResponse:
        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise ChatLLMError("google-genai is not installed.") from error

        client = genai.Client(api_key=self._api_key)
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=self._to_gemini_contents(messages, types),
                config=types.GenerateContentConfig(
                    system_instruction=current_system_instruction(),
                    tools=[
                        types.Tool(
                            function_declarations=[
                                types.FunctionDeclaration(
                                    name=tool["name"],
                                    description=tool["description"],
                                    parameters_json_schema=tool["parameters"],
                                )
                                for tool in (*TOOL_SCHEMAS, *PENDING_ACTION_TOOL_SCHEMAS)
                            ]
                        )
                    ],
                ),
            )
        except Exception as error:
            raise ChatLLMError("Gemini chat request failed.") from error

        tool_calls = self._response_tool_calls(response)
        answer = self._response_text(response)

        if not tool_calls and not answer:
            raise ChatLLMError("Gemini returned an empty chat response.")

        return ChatModelResponse(answer=answer, tool_calls=tool_calls)

    @staticmethod
    def _to_gemini_contents(messages: list[BaseMessage], types: Any) -> list[Any]:
        contents: list[Any] = []

        for message in messages:
            if isinstance(message, HumanMessage):
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=message_text(message))],
                    )
                )
                continue

            if isinstance(message, ToolMessage):
                try:
                    result = json.loads(message_text(message))
                except json.JSONDecodeError:
                    result = {"error": "Некоректний результат фінансового tool."}

                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    id=message.tool_call_id,
                                    name=message.name or "unknown_tool",
                                    response=result,
                                )
                            )
                        ],
                    )
                )
                continue

            if isinstance(message, AIMessage) and message.tool_calls:
                thought_signatures = message.additional_kwargs.get(
                    "gemini_thought_signatures",
                    {},
                )
                contents.append(
                    types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    id=tool_call["id"],
                                    name=tool_call["name"],
                                    args=tool_call["args"],
                                ),
                                thought_signature=GeminiChatResponder._decode_signature(
                                    thought_signatures.get(tool_call["id"])
                                ),
                            )
                            for tool_call in message.tool_calls
                        ],
                    )
                )
                continue

            if isinstance(message, AIMessage):
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=message_text(message))],
                    )
                )

        return contents

    @staticmethod
    def _response_text(response: Any) -> str:
        text_parts: list[str] = []
        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                if part.text:
                    text_parts.append(part.text)
        return "\n".join(text_parts).strip()

    @staticmethod
    def _response_tool_calls(response: Any) -> list[ChatToolCall]:
        tool_calls: list[ChatToolCall] = []
        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                function_call = part.function_call
                if not function_call or not function_call.name:
                    continue

                signature = part.thought_signature
                tool_calls.append(
                    ChatToolCall(
                        id=function_call.id or str(uuid4()),
                        name=function_call.name,
                        arguments=dict(function_call.args or {}),
                        thought_signature=(
                            base64.b64encode(signature).decode("ascii")
                            if signature
                            else None
                        ),
                    )
                )
        return tool_calls

    @staticmethod
    def _decode_signature(signature: object) -> bytes | None:
        if not isinstance(signature, str):
            return None
        try:
            return base64.b64decode(signature.encode("ascii"), validate=True)
        except ValueError:
            return None


class FinanceChatGraph:
    """LangGraph chat with per-thread history and controlled finance tool calls."""

    def __init__(self, responder: ChatResponder, tools: ChatToolExecutor) -> None:
        self._responder = responder
        self._tools = tools
        self._checkpointer = InMemorySaver()
        self._graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(ChatState)
        workflow.add_node("model", self._model_node)
        workflow.add_node("tools", self._tools_node)
        workflow.add_edge(START, "model")
        workflow.add_conditional_edges(
            "model",
            self._route_after_model,
            {"tools": "tools", END: END},
        )
        workflow.add_edge("tools", "model")
        return workflow.compile(checkpointer=self._checkpointer)

    async def _model_node(self, state: ChatState) -> dict[str, list[AIMessage]]:
        response = await self._responder.reply(state["messages"])
        if response.tool_calls:
            return {
                "messages": [
                    AIMessage(
                        content=response.answer,
                        tool_calls=[
                            {
                                "id": tool_call.id,
                                "name": tool_call.name,
                                "args": tool_call.arguments,
                            }
                            for tool_call in response.tool_calls
                        ],
                        additional_kwargs={
                            "gemini_thought_signatures": {
                                tool_call.id: tool_call.thought_signature
                                for tool_call in response.tool_calls
                                if tool_call.thought_signature
                            }
                        },
                    )
                ]
            }

        return {"messages": [AIMessage(content=response.answer)]}

    def _route_after_model(self, state: ChatState) -> str:
        latest_message = state["messages"][-1]
        if not isinstance(latest_message, AIMessage) or not latest_message.tool_calls:
            return END
        if state.get("tool_rounds", 0) >= MAX_TOOL_ROUNDS:
            return END
        return "tools"

    async def _tools_node(self, state: ChatState) -> dict[str, Any]:
        latest_message = state["messages"][-1]
        if not isinstance(latest_message, AIMessage):
            raise ChatLLMError("Chat graph did not return a tool call.")

        tool_messages: list[ToolMessage] = []
        pending_action: dict[str, Any] | None = None
        proposal_already_prepared = state.get("pending_action") is not None
        for tool_call in latest_message.tool_calls:
            tool_name = tool_call["name"]
            try:
                if (
                    tool_name in WRITE_ACTION_TOOL_NAMES
                    and proposal_already_prepared
                ):
                    result = {"error": "Для цього повідомлення вже є чернетка дії."}
                else:
                    result = await self._tools.execute(
                        tool_name,
                        tool_call["args"],
                        context=ChatToolContext(thread_id=state["thread_id"]),
                    )
            except FinanceToolError as error:
                result = {"error": str(error)}
            except Exception as error:
                logging.warning(
                    "AI chat tool failed: tool_name=%s error_type=%s",
                    tool_name,
                    type(error).__name__,
                )
                result = {
                    "error": (
                        "Не вдалося підготувати чернетку дії."
                        if tool_name in WRITE_ACTION_TOOL_NAMES
                        else "Не вдалося отримати фінансові дані."
                    )
                }

            result = dict(result)
            proposed_action = result.pop(PENDING_ACTION_TOOL_RESULT_KEY, None)
            if pending_action is None and isinstance(proposed_action, dict):
                pending_action = proposed_action
                proposal_already_prepared = True

            tool_messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    name=tool_name,
                    tool_call_id=tool_call["id"],
                )
            )

        update: dict[str, Any] = {
            "messages": tool_messages,
            "tool_rounds": state.get("tool_rounds", 0) + 1,
        }
        if pending_action is not None:
            update["pending_action"] = pending_action
        return update

    async def respond(self, message: str, thread_id: str) -> ChatResponse:
        state = await self._graph.ainvoke(
            {
                "messages": [HumanMessage(content=message)],
                "tool_rounds": 0,
                "thread_id": thread_id,
                "pending_action": None,
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        latest_message = state["messages"][-1]
        if not isinstance(latest_message, AIMessage) or latest_message.tool_calls:
            raise ChatLLMError("Chat graph did not return an AI response.")

        answer = message_text(latest_message).strip()
        if not answer:
            raise ChatLLMError("Chat graph returned an empty answer.")

        pending_action_data = state.get("pending_action")
        pending_action = (
            PendingActionResponse.model_validate(pending_action_data)
            if pending_action_data
            else None
        )
        return ChatResponse(answer=answer, pending_action=pending_action)


def resolve_thread_id(thread_id: UUID | None) -> str:
    return str(thread_id or uuid4())
