import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from google.genai import errors
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from app.ai_actions import ActionType, create_pending_action
from app.ai_analysis import (
    GEMINI_MODELS,
    GEMINI_REQUEST_TIMEOUT_SECONDS,
    GeminiAnalysisError,
    GeminiUnavailableError,
    generate_transaction_analysis,
)
from app.ai_chat import generate_chat_response
from app.schemas import TransactionAnalysisResponse


def fake_gemini_client(*responses):
    generate_content = AsyncMock(side_effect=responses)
    async_client = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content),
        aclose=AsyncMock(),
    )
    client = SimpleNamespace(aio=async_client, close=MagicMock())
    return client, generate_content, async_client.aclose


class GeminiAnalysisBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_is_immediate_and_clients_are_closed(self):
        expected = TransactionAnalysisResponse(
            summary="Даних замало.",
            top_expense_categories=[],
            risks=[],
            advice=[],
        )
        response = SimpleNamespace(
            parsed=expected,
            text=expected.model_dump_json(),
            usage_metadata=None,
        )
        client, generate_content, aclose = fake_gemini_client(
            OSError("primary unavailable"),
            response,
        )

        with (
            patch("app.ai_analysis.get_gemini_api_key", return_value="test-key"),
            patch("app.ai_analysis.genai.Client", return_value=client),
        ):
            result = await generate_transaction_analysis([])

        self.assertEqual(result, expected)
        self.assertEqual(
            [call.kwargs["model"] for call in generate_content.await_args_list],
            list(GEMINI_MODELS[:2]),
        )
        config = generate_content.await_args_list[0].kwargs["config"]
        self.assertEqual(
            config.http_options.timeout,
            GEMINI_REQUEST_TIMEOUT_SECONDS * 1_000,
        )
        self.assertEqual(config.http_options.retry_options.attempts, 1)
        aclose.assert_awaited_once()
        client.close.assert_called_once()

    async def test_all_model_failures_raise_domain_error_and_close_clients(self):
        attempt_count = len(GEMINI_MODELS) * 3
        client, generate_content, aclose = fake_gemini_client(
            *(OSError(f"failure-{index}") for index in range(attempt_count))
        )

        with (
            patch("app.ai_analysis.get_gemini_api_key", return_value="test-key"),
            patch("app.ai_analysis.genai.Client", return_value=client),
            patch("app.ai_analysis.asyncio.sleep", new=AsyncMock()) as sleep,
            self.assertRaises(GeminiUnavailableError),
        ):
            await generate_transaction_analysis([])

        self.assertEqual(generate_content.await_count, attempt_count)
        self.assertEqual(sleep.await_count, 2)
        aclose.assert_awaited_once()
        client.close.assert_called_once()

    async def test_transient_errors_fall_back_to_locally_validated_plain_json(self):
        expected = TransactionAnalysisResponse(
            summary="Баланс стабільний.",
            top_expense_categories=["продукти"],
            risks=[],
            advice=["Зберігай резерв."],
        )
        unavailable = errors.ServerError(
            503,
            {"error": {"message": "high demand", "status": "UNAVAILABLE"}},
        )
        response = SimpleNamespace(
            parsed=None,
            text=f"```json\n{expected.model_dump_json()}\n```",
            usage_metadata=None,
        )
        client, generate_content, _ = fake_gemini_client(
            *(unavailable for _ in GEMINI_MODELS),
            response,
        )

        with (
            patch("app.ai_analysis.get_gemini_api_key", return_value="test-key"),
            patch("app.ai_analysis.genai.Client", return_value=client),
            patch("app.ai_analysis.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            result = await generate_transaction_analysis([])

        self.assertEqual(result, expected)
        self.assertEqual(generate_content.await_count, len(GEMINI_MODELS) + 1)
        structured_config = generate_content.await_args_list[0].kwargs["config"]
        plain_config = generate_content.await_args_list[-1].kwargs["config"]
        self.assertIsNotNone(structured_config.response_json_schema)
        self.assertIsNone(plain_config.response_json_schema)
        self.assertIsNone(plain_config.response_mime_type)
        sleep.assert_awaited_once()

    async def test_invalid_structured_response_is_rejected(self):
        invalid = {
            "summary": "Висновок",
            "top_expense_categories": ["a", "b", "c", "d"],
            "risks": [],
            "advice": [],
        }
        response = SimpleNamespace(
            parsed=None,
            text=json.dumps(invalid),
            usage_metadata=None,
        )
        attempt_count = len(GEMINI_MODELS) * 2
        client, generate_content, _ = fake_gemini_client(
            *(response for _ in range(attempt_count))
        )

        with (
            patch("app.ai_analysis.get_gemini_api_key", return_value="test-key"),
            patch("app.ai_analysis.genai.Client", return_value=client),
            self.assertRaises(GeminiAnalysisError),
        ):
            await generate_transaction_analysis([])

        self.assertEqual(generate_content.await_count, attempt_count)
        with self.assertRaises(ValidationError):
            TransactionAnalysisResponse.model_validate(invalid)


class GeminiChatBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_thread_memory_is_namespaced_by_user(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value={"messages": [HumanMessage("Привіт"), AIMessage("Вітаю!")]}
            )
        )

        with patch("app.ai_chat._get_graph", return_value=graph):
            response_text, pending = await generate_chat_response(
                thread_id="shared-thread",
                user_message="Привіт",
                telegram_id=101,
            )

        self.assertEqual(response_text, "Вітаю!")
        self.assertIsNone(pending)
        self.assertEqual(
            graph.ainvoke.await_args.kwargs["config"]["configurable"]["thread_id"],
            "101:shared-thread",
        )

    async def test_old_pending_action_is_not_repeated_on_a_new_turn(self):
        old_action = create_pending_action(
            action_type=ActionType.DELETE_TRANSACTION,
            telegram_id=101,
            payload={"transaction_id": 1},
        )
        graph = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value={
                    "messages": [
                        HumanMessage("Видали операцію"),
                        ToolMessage(
                            json.dumps({"action_id": old_action.action_id}),
                            tool_call_id="old-call",
                        ),
                        AIMessage("Підтверди дію."),
                        HumanMessage("Дякую"),
                        AIMessage("Будь ласка."),
                    ]
                }
            )
        )

        with patch("app.ai_chat._get_graph", return_value=graph):
            response_text, pending = await generate_chat_response(
                thread_id="thread-1",
                user_message="Дякую",
                telegram_id=101,
            )

        self.assertEqual(response_text, "Будь ласка.")
        self.assertIsNone(pending)

    async def test_current_turn_pending_action_is_returned(self):
        action = create_pending_action(
            action_type=ActionType.CREATE_TRANSACTION,
            telegram_id=101,
            payload={"amount": "10.00", "category": "кава"},
        )
        graph = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value={
                    "messages": [
                        HumanMessage("Додай витрату"),
                        ToolMessage(
                            json.dumps({"action_id": action.action_id}),
                            tool_call_id="new-call",
                        ),
                        AIMessage("Підтверди дію."),
                    ]
                }
            )
        )

        with patch("app.ai_chat._get_graph", return_value=graph):
            _, pending = await generate_chat_response(
                thread_id="thread-2",
                user_message="Додай витрату",
                telegram_id=101,
            )

        self.assertEqual(pending["action_id"], action.action_id)
        self.assertEqual(pending["type"], ActionType.CREATE_TRANSACTION.value)


if __name__ == "__main__":
    unittest.main()
