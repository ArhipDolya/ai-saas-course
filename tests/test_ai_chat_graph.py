from __future__ import annotations

from unittest import IsolatedAsyncioTestCase, TestCase
from uuid import UUID

from langchain_core.messages import BaseMessage
from pydantic import ValidationError

from app.ai_chat_graph import (
    AIChatRequest,
    ChatModelResponse,
    ChatToolCall,
    FinanceChatGraph,
    message_text,
    resolve_thread_id,
)


class MemoryFakeResponder:
    async def reply(self, messages: list[BaseMessage]) -> ChatModelResponse:
        user_messages = [
            message_text(message)
            for message in messages
            if message.type == "human"
        ]
        latest_message = user_messages[-1]

        if "Який місяць" in latest_message:
            if any("червень" in message.lower() for message in user_messages[:-1]):
                return ChatModelResponse(
                    answer="Ми аналізуємо червень.",
                    tool_calls=[],
                )
            return ChatModelResponse(
                answer="У цьому діалозі місяць не зазначено.",
                tool_calls=[],
            )

        return ChatModelResponse(
            answer="Домовились: аналізуємо червень.",
            tool_calls=[],
        )


class UnusedTools:
    async def execute(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError(f"Unexpected tool call: {name}({arguments})")


class ToolFakeResponder:
    async def reply(self, messages: list[BaseMessage]) -> ChatModelResponse:
        if messages[-1].type == "human":
            return ChatModelResponse(
                answer="",
                tool_calls=[
                    ChatToolCall(
                        id=f"summary-call-{len([message for message in messages if message.type == 'tool'])}",
                        name="get_transactions_summary",
                        arguments={"period": "current_month"},
                    )
                ],
            )

        return ChatModelResponse(
            answer="За поточний місяць витрати становлять 120.00.",
            tool_calls=[],
        )


class SummaryTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, arguments))
        return {"total_expense": "120.00", "transactions_count": 1}


class AIChatRequestTests(TestCase):
    def test_new_thread_id_is_uuid4(self) -> None:
        thread_id = resolve_thread_id(AIChatRequest(message="Привіт").thread_id)
        self.assertEqual(UUID(thread_id).version, 4)

    def test_empty_message_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AIChatRequest(message="   ")


class AIChatMemoryTests(IsolatedAsyncioTestCase):
    async def test_thread_history_is_isolated(self) -> None:
        graph = FinanceChatGraph(MemoryFakeResponder(), UnusedTools())

        await graph.respond("Ми аналізуємо витрати за червень.", "thread-a")
        thread_a_answer = await graph.respond("Який місяць ми аналізуємо?", "thread-a")
        thread_b_answer = await graph.respond("Який місяць ми аналізуємо?", "thread-b")

        self.assertEqual(thread_a_answer, "Ми аналізуємо червень.")
        self.assertEqual(thread_b_answer, "У цьому діалозі місяць не зазначено.")

    async def test_graph_executes_allowed_tool_before_final_answer(self) -> None:
        tools = SummaryTools()
        graph = FinanceChatGraph(ToolFakeResponder(), tools)

        answer = await graph.respond("Покажи витрати за місяць", "thread-a")

        self.assertEqual(answer, "За поточний місяць витрати становлять 120.00.")
        self.assertEqual(
            tools.calls,
            [("get_transactions_summary", {"period": "current_month"})],
        )

    async def test_each_new_message_can_execute_tools_in_same_thread(self) -> None:
        tools = SummaryTools()
        graph = FinanceChatGraph(ToolFakeResponder(), tools)

        await graph.respond("Покажи витрати за місяць", "thread-a")
        await graph.respond("А тепер покажи ще раз", "thread-a")

        self.assertEqual(len(tools.calls), 2)
