import io
import json
import sys
import unittest
from urllib import error
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


    @patch.dict(generator.os.environ, {"OPENAI_POLL_TIMEOUT_SECONDS": "1"})
    @patch.object(generator.time, "monotonic", side_effect=[10, 11])
    @patch.object(generator, "get_json")
    @patch.object(generator, "post_json", return_value=(200, {"id": "first", "status": "in_progress"}))
    def test_background_polling_stops_at_deadline(self, post_json, get_json, monotonic) -> None:
        with self.assertRaisesRegex(RuntimeError, "exceeded its polling deadline"):
            generator.call_responses_api(
                "key", instructions="instructions", input_text="input",
                schema=self.schema, model="model", background=True,
            )
        get_json.assert_not_called()

    @patch.object(generator, "SENDGRID_API_KEY", "mock-mail-key")
    @patch.object(generator.request, "urlopen")
    def test_sendgrid_post_is_not_retried_and_response_body_is_omitted(self, urlopen) -> None:
        urlopen.side_effect = error.HTTPError(
            "https://api.sendgrid.com/v3/mail/send", 503, "Unavailable", None,
            io.BytesIO(b"Provider response may contain mock-mail-key"),
        )
        with self.assertRaisesRegex(RuntimeError, r"HTTP 503 from api\.sendgrid\.com\.$") as failure:
            generator.send_email_via_sendgrid("Mock subject", "Mock body")
        self.assertNotIn("mock-mail-key", str(failure.exception))
        urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
