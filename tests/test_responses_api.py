import json
import sys
import unittest
from unittest.mock import patch

with patch.object(sys, "argv", ["generate_and_publish.py"]):
    import generate_and_publish as generator


def completed_response(response_id: str, value: dict) -> dict:
    return {
        "id": response_id,
        "status": "completed",
        "output_text": json.dumps(value),
    }


class BackgroundResponsesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = {
            "name": "result",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
        }

    @patch.object(generator.time, "sleep")
    @patch.object(generator, "get_json")
    @patch.object(generator, "post_json")
    def test_retries_a_failed_background_response(self, post_json, get_json, sleep) -> None:
        post_json.side_effect = [
            (200, {"id": "first", "status": "in_progress"}),
            (200, completed_response("second", {"ok": True})),
        ]
        get_json.return_value = (200, {"id": "first", "status": "failed", "error": {"code": "server_error"}})

        result = generator.call_responses_api(
            "key",
            instructions="instructions",
            input_text="input",
            schema=self.schema,
            model="model",
            background=True,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(post_json.call_count, 2)

    @patch.dict(generator.os.environ, {"OPENAI_RESPONSE_MAX_ATTEMPTS": "2"})
    @patch.object(generator.time, "sleep")
    @patch.object(generator, "post_json")
    def test_reports_terminal_status_after_retries(self, post_json, sleep) -> None:
        post_json.side_effect = [
            (200, {"id": "first", "status": "cancelled"}),
            (200, {"id": "second", "status": "incomplete", "incomplete_details": {"reason": "timeout"}}),
        ]

        with self.assertRaisesRegex(RuntimeError, "status=incomplete after 2 attempts"):
            generator.call_responses_api(
                "key",
                instructions="instructions",
                input_text="input",
                schema=self.schema,
                model="model",
                background=True,
            )


if __name__ == "__main__":
    unittest.main()
