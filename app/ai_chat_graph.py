from __future__ import annotations

import asyncio
import base64
import json
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


MAX_CHAT_MESSAGE_LENGTH = 1_000
MAX_TOOL_ROUNDS = 4
CHAT_SYSTEM_INSTRUCTION = """Ти AI-помічник фінансового застосунку.

Відповідай українською мовою, стисло та зрозуміло. Поточна дата: {current_date}.

Ти маєш доступ до фінансових фактів тільки через дозволені read-only tools. Якщо
користувач питає про доходи, витрати, баланс, категорії, операції або період,
виклич релевантний tool перед відповіддю. Використовуй тільки дані з результатів
tools: не вигадуй суми, категорії, дати чи факти. Якщо даних недостатньо або
потрібний період неможливо визначити, прямо скажи це.

Не додавай, не видаляй і не редагуй фінансові операції. Не згадуй внутрішні
деталі реалізації, API keys, базу даних або повні технічні результати tools.
Після отримання результату tool сформулюй для користувача готову відповідь, а
не повідомлення про виконання tool.
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


class ChatState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    tool_rounds: int


class ChatResponder(Protocol):
    async def reply(self, messages: list[BaseMessage]) -> ChatModelResponse: ...


class ChatToolExecutor(Protocol):
    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


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
                                for tool in TOOL_SCHEMAS
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
        for tool_call in latest_message.tool_calls:
            tool_name = tool_call["name"]
            try:
                result = await self._tools.execute(tool_name, tool_call["args"])
            except FinanceToolError as error:
                result = {"error": str(error)}
            except Exception:
                result = {"error": "Не вдалося отримати фінансові дані."}

            tool_messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    name=tool_name,
                    tool_call_id=tool_call["id"],
                )
            )

        return {
            "messages": tool_messages,
            "tool_rounds": state.get("tool_rounds", 0) + 1,
        }

    async def respond(self, message: str, thread_id: str) -> str:
        state = await self._graph.ainvoke(
            {
                "messages": [HumanMessage(content=message)],
                "tool_rounds": 0,
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        latest_message = state["messages"][-1]
        if not isinstance(latest_message, AIMessage) or latest_message.tool_calls:
            raise ChatLLMError("Chat graph did not return an AI response.")

        answer = message_text(latest_message).strip()
        if not answer:
            raise ChatLLMError("Chat graph returned an empty answer.")

        return answer


def resolve_thread_id(thread_id: UUID | None) -> str:
    return str(thread_id or uuid4())
