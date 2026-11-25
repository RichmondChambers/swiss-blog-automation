import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

with patch.object(sys, "argv", ["generate_and_publish.py"]):
    import generate_and_publish as generator


PACK_NAME = "knowledge/legal_authorities/test_authority.md"
EXCERPT = "An approval depends on the statutory conditions."
NARROWER_EXCERPT = "Approval is determined by the competent authority in the individual case."
WORKFLOW_URL = "https://github.com/example/blog/actions/runs/123"
DRAFT_TITLE = "Does Approval Follow Automatically?"
DRAFT = {
    "blog_title": DRAFT_TITLE,
    "blog_content": (
        "Approval depends on the conditions and the decision in the individual case.\n\n"
        "The applicable process determines which evidence must be reviewed.\n\n"
        "**What the Decision Depends On**\n\n"
        "The decision depends on how the authority assesses the circumstances of the case.\n\n"
        f"**{generator.CTA_HEADING}**\n\n"
        "Our lawyers can review your evidence and advise on the procedure, the next steps, "
        "and any practical risks that may affect your application.\n\n"
        f"{generator.CTA_STANDARD_CONTACT_SENTENCE}"
    ),
}


def legal_issue(index, *, confidence="high", essential=True):
    return {
        "issue": f"Test legal issue {index}",
        "essential": essential,
        "rule": "Approval is automatic." if confidence == "low" else "Approval depends on the conditions.",
        "legal_basis": "Test statute, Article 1",
        "authority_type": "statute",
        "confidence": confidence,
        "confidence_reason": "The source does not establish automatic approval." if confidence == "low" else "The source sets out the conditions.",
        "support": [{"source_type": "legal_authority", "source_name": PACK_NAME, "excerpt": EXCERPT}],
    }


class LegalMemoRecoveryTests(unittest.TestCase):
    def setUp(self):
        context = self.enterContext(ExitStack())
        root = Path(context.enter_context(tempfile.TemporaryDirectory()))
        self.topics_path = root / "topics.json"
        self.output_dir = root / "generated"
        pack = root / PACK_NAME
        pack.parent.mkdir(parents=True)
        pack.write_text(EXCERPT + "\n" + (" " * generator.SOURCE_EXCERPT_LIMIT) + NARROWER_EXCERPT)
        self.topics = [
            {"topic": "Example topic", "status": "unused", "pillar": "work", "subtopic": "approval"},
            {"topic": "Next topic", "status": "unused", "pillar": "work", "subtopic": "evidence"},
        ]
        self.topics_path.write_text(json.dumps(self.topics))
        context.enter_context(patch.dict(generator.os.environ, {
            "OPENAI_API_KEY": "secret-openai-test-value",
            "SENDGRID_API_KEY": "secret-sendgrid-test-value",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "example/blog",
        }))
        context.enter_context(patch.multiple(
            generator, SCRIPT_DIR=root, TOPICS_PATH=self.topics_path, OUTPUT_DIR=self.output_dir,
        ))
        context.enter_context(patch.multiple(
            generator.args, dry_run=False, topic_index=None, persist_topic_state=False,
            allow_low_confidence_legal_memo=False,
        ))
        context.enter_context(patch.object(generator, "load_authority_pack_map", return_value={}))
        self.packs = context.enter_context(patch.object(generator, "resolve_authority_pack_paths", return_value=[pack]))
        context.enter_context(patch.object(generator, "load_chunks_from_folder", return_value=[]))
        self.api = context.enter_context(patch.object(generator, "call_responses_api", side_effect=self.respond))
        self.email = context.enter_context(patch.object(generator, "send_email_via_sendgrid", return_value=True))
        self.delivery_render = context.enter_context(
            patch.object(generator, "render_success_email", wraps=generator.render_success_email)
        )
        self.initial = {
            "article_positioning": "Test analysis",
            "issues": [legal_issue(i) for i in range(1, 6)] + [legal_issue(6, confidence="low")],
            "open_questions": ["Check whether the provision confers a right."],
        }
        self.memos = iter([self.initial] * 3)

    def respond(self, api_key, *, instructions, input_text, schema, **options):
        if schema == generator.LEGAL_MEMO_SCHEMA:
            result = next(self.memos)
            if isinstance(result, Exception):
                raise result
            return result
        if schema == generator.DRAFT_SCHEMA:
            return DRAFT.copy()
        if schema == generator.READER_JOURNEY_SCHEMA:
            return {"one_sentence_answer": "Approval depends on the conditions."}
        if schema == generator.SEO_SCHEMA:
            return {
                "seo_meta_title": DRAFT_TITLE,
                "seo_meta_description": "Example description.",
                "suggested_seo_keywords": [],
            }
        return {}

    def read_diagnostics(self):
        path, = self.output_dir.glob("123_2_*_legal_memo.json")
        return json.loads(path.read_text())

    def read_topics(self):
        return json.loads(self.topics_path.read_text())

    def test_repair_receives_previous_memo_and_evidence_and_delivers_once(self):
        repaired = deepcopy(self.initial)
        repaired["issues"][-1] = legal_issue(6)
        repaired["issues"][-1]["support"][0]["excerpt"] = NARROWER_EXCERPT
        self.memos = iter([self.initial, repaired])

        def send(*args, **kwargs):
            self.assertEqual(self.read_topics()[0]["status"], "delivery_pending")
            return True

        self.email.side_effect = send
        generator.main()

        repair_request = self.api.call_args_list[2].kwargs
        repair_input = json.loads(repair_request["input_text"])
        self.assertIn("Issue 6 has low confidence.", repair_input["validation_errors"])
        self.assertEqual(repair_input["previous_memo"], self.initial)
        self.assertTrue(repair_input["required_issues"]["Test legal issue 6"])
        self.assertIn(NARROWER_EXCERPT, repair_input["original_request"]["legal_sources"])
        self.assertIn("Never raise a confidence rating", repair_request["instructions"])
        self.assertEqual([item["outcome"] for item in self.read_diagnostics()["attempts"]], ["rejected", "accepted"])
        self.email.assert_called_once()
        self.assertEqual(self.email.call_args.kwargs["subject"], f"Blog draft: {DRAFT_TITLE}")
        self.assertTrue(self.email.call_args.kwargs["is_html"])
        self.assertIn("SEO META TITLE", self.email.call_args.kwargs["body"])
        self.assertIn(generator.CTA_HEADING, self.email.call_args.kwargs["body"])
        self.assertEqual(self.read_topics()[0]["status"], "used")
        self.assertEqual(self.read_topics()[0]["used_title"], DRAFT_TITLE)
        self.assertEqual(generator.pick_topic(self.read_topics(), None)[0], 1)

    def test_unresolved_essential_issue_is_preserved_and_held_for_review(self):
        self.initial["issues"][-1]["confidence_reason"] += " secret-openai-test-value"
        with self.assertRaisesRegex(generator.LegalMemoValidationError, "Issue 6 has low confidence"):
            generator.main()

        self.assertEqual(self.api.call_count, 4)
        self.delivery_render.assert_not_called()
        self.email.assert_called_once()
        subject, body = self.email.call_args.args
        self.assertIn("legal review required", subject)
        for expected in ("Example topic", "Approval is automatic.", "Attempt 3 (repair): rejected", "Check whether", WORKFLOW_URL):
            self.assertIn(expected, body)
        self.assertNotIn("secret-openai-test-value", body)
        report = self.read_diagnostics()
        self.assertEqual(report["outcome"], "rejected")
        self.assertEqual(report["workflow"]["url"], WORKFLOW_URL)
        self.assertEqual(report["selected_legal_authority_packs"], [PACK_NAME])
        self.assertEqual(len(report["attempts"]), 3)
        self.assertEqual(report["attempts"][0]["failed_propositions"]["6"]["support"][0]["excerpt"], EXCERPT)
        self.assertNotIn("secret-openai-test-value", json.dumps(report))
        topics = self.read_topics()
        self.assertEqual(topics[0]["status"], "needs_legal_review")
        self.assertEqual(generator.pick_topic(topics, None)[0], 1)
        self.assertEqual(generator.pick_topic(topics, 0)[0], 0)

    def test_repair_cannot_drop_essential_issue_or_only_relabel_confidence(self):
        omitted = deepcopy(self.initial)
        omitted["issues"].pop()
        relabelled = deepcopy(self.initial)
        relabelled["issues"][-1]["confidence"] = "high"
        relabelled["issues"][-1]["confidence_reason"] = "Changed classification."
        self.memos = iter([self.initial, omitted, relabelled])
        with self.assertRaisesRegex(generator.LegalMemoValidationError, "without changing the rule"):
            generator.main()
        report = self.read_diagnostics()
        self.assertIn("Required issue omitted: Test legal issue 6", report["attempts"][1]["validation_errors"])
        self.delivery_render.assert_not_called()

    def test_repair_can_omit_unsupported_nonessential_issue(self):
        self.initial["issues"][-1]["essential"] = False
        repaired = deepcopy(self.initial)
        repaired["issues"].pop()
        self.memos = iter([self.initial, repaired])
        generator.main()
        self.assertEqual(self.read_topics()[0]["status"], "used")

    def test_unknown_source_and_fabricated_excerpt_are_rejected(self):
        for citation in (
            {"source_type": "website_editorial", "source_name": PACK_NAME, "excerpt": EXCERPT},
            {"source_type": "legal_authority", "source_name": PACK_NAME, "excerpt": "Invented excerpt"},
        ):
            with self.subTest(citation=citation):
                memo = {"issues": [legal_issue(1)]}
                memo["issues"][0]["support"] = [citation]
                errors = generator.validate_legal_memo(memo, {("legal_authority", PACK_NAME): EXCERPT}, {}, {})
                self.assertTrue(any("unverified source or excerpt" in error for error in errors))

    def test_notification_failure_preserves_original_validation_error(self):
        self.email.side_effect = RuntimeError("Mock notification failure")
        with self.assertRaisesRegex(generator.LegalMemoValidationError, "Issue 6 has low confidence"):
            generator.main()
        self.email.assert_called_once()
        self.assertEqual(self.read_diagnostics()["outcome"], "rejected")
        self.assertEqual(self.read_topics()[0]["status"], "needs_legal_review")

    def test_api_error_preserves_prior_memo_without_holding_topic(self):
        self.memos = iter([self.initial, RuntimeError("Mock network failure")])
        with self.assertRaisesRegex(RuntimeError, "Mock network failure"):
            generator.main()
        diagnostics = self.read_diagnostics()
        self.assertEqual(diagnostics["attempts"][0]["memo"], self.initial)
        self.assertEqual(diagnostics["attempts"][1]["outcome"], "request_failed")
        self.assertEqual(self.read_topics()[0]["status"], "unused")
        self.email.assert_not_called()

    def test_missing_pack_is_held_without_calling_openai(self):
        self.packs.return_value = []
        with self.assertRaisesRegex(generator.LegalMemoValidationError, "No legal authority packs"):
            generator.main()
        self.api.assert_not_called()
        self.email.assert_called_once()
        self.assertEqual(self.read_diagnostics()["attempts"], [])
        self.assertEqual(self.read_topics()[0]["status"], "needs_legal_review")

    def test_uncertain_delivery_remains_pending_and_cannot_be_retried_by_index(self):
        self.memos = iter([{"issues": [legal_issue(1)]}])
        self.email.side_effect = RuntimeError("Mock SendGrid timeout")
        with self.assertRaisesRegex(RuntimeError, "Mock SendGrid timeout"):
            generator.main()
        topics = self.read_topics()
        self.assertEqual(topics[0]["status"], "delivery_pending")
        self.assertEqual(generator.pick_topic(topics, None)[0], 1)
        with self.assertRaisesRegex(RuntimeError, "cannot be selected"):
            generator.pick_topic(topics, 0)
        self.email.assert_called_once()

    def test_reservation_failure_blocks_send(self):
        self.memos = iter([{"issues": [legal_issue(1)]}])
        with patch.object(generator, "save_topic_state", side_effect=OSError("Mock write failure")):
            with self.assertRaisesRegex(OSError, "Mock write failure"):
                generator.main()
        self.email.assert_not_called()
        self.assertEqual(self.read_topics()[0]["status"], "unused")

    def test_production_rejects_low_confidence_override(self):
        with patch.multiple(generator.args, persist_topic_state=True, allow_low_confidence_legal_memo=True):
            with self.assertRaisesRegex(RuntimeError, "cannot enable low-confidence"):
                generator.main()
        self.api.assert_not_called()
        self.email.assert_not_called()

    def test_successful_send_with_failed_final_save_leaves_reservation(self):
        self.memos = iter([{"issues": [legal_issue(1)]}])
        original_save = generator.save_topic_state

        def fail_final_save(topics, message):
            if topics[0]["status"] == "used":
                raise OSError("Mock final save failure")
            original_save(topics, message)

        with patch.object(generator, "save_topic_state", side_effect=fail_final_save):
            with self.assertRaisesRegex(RuntimeError, "SendGrid accepted the draft") as failure:
                generator.main()
        self.assertIsInstance(failure.exception.__cause__, OSError)
        self.email.assert_called_once()
        self.assertEqual(self.read_topics()[0]["status"], "delivery_pending")


if __name__ == "__main__":
    unittest.main()
