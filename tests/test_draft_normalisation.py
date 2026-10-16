import sys
import unittest
from unittest.mock import patch

with patch.object(sys, "argv", ["generate_and_publish.py"]):
    import generate_and_publish as generator


class CPermitNormalisationTests(unittest.TestCase):
    def test_hyphenated_c_permit_phrase_is_not_corrupted(self) -> None:
        for dash in ("-", "–", "—"):
            with self.subTest(dash=dash):
                source = f"The ordinary C{dash}Permit Route"
                output = generator.replace_informal_c_permit_terms(source)

                self.assertNotIn("The an", output)
                self.assertNotRegex(
                    output,
                    r"\ban ordinary C[\-–—]permit[\-–—]Permit\b",
                )
                self.assertNotRegex(
                    output,
                    r"\bC[\-–—]permit[\-–—]Permit\b",
                )

    def test_existing_malformed_phrase_is_repaired_idempotently(self) -> None:
        source = "The an ordinary C-permit-Permit Route"
        output = generator.replace_informal_c_permit_terms(source)

        self.assertEqual(output, "The Ordinary C-Permit Route")
        self.assertEqual(generator.replace_informal_c_permit_terms(output), output)


if __name__ == "__main__":
    unittest.main()
