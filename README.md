# Swiss Blog Automation

This repository contains the Swiss blog automation system used to generate, organise, review, and publish Swiss immigration and nationality law content.

The system is intended to support legally accurate, editorially consistent, and reusable content production by combining structured topic data, legal authority packs, internal knowledge sources, generation scripts, and publication workflows.

## Scope

Use this repository for materials concerning:

- Swiss immigration law blog production
- Swiss nationality law blog production
- Legal authority mapping
- Topic planning
- Automated or semi-automated blog generation
- Editorial review and quality control
- Website-ready content preparation
- Publishing workflows

## Repository structure

- `knowledge/` — structured legal, editorial, and topic-planning materials used by the blog automation system.
- `generated_blog_runs/` — generated outputs from blog production runs, including draft articles and related run materials.
- `.github/` — GitHub configuration, workflows, templates, and repository automation where applicable.
- `generate_and_publish.py` — main script for generating and publishing blog content.
- `topics.json` — structured topic list used for content planning and generation.

## Workflow overview

The usual workflow is:

1. Add or update reusable knowledge sources in `knowledge/`.
2. Add or update topic data in `topics.json`.
3. Map relevant legal authorities using `authority_pack_map.json`.
4. Run the blog generation process.
5. Review generated drafts for legal accuracy, tone, structure, and website suitability.
6. Publish approved content or move it into the relevant output location.

## Running checks and recovering failed topics

From the repository root with Python 3.11, run:

```bash
python -m pip install PyPDF2==3.0.1
PYTHONPATH="$PWD" python -B -m unittest discover -s tests -v
python -B scripts/validate_authority_pack_map.py
PYTHONPATH="$PWD" python -B generate_and_publish.py --dry-run
```

The tests mock OpenAI and SendGrid. The dry run shows the next eligible topic and mapped authorities without using credentials or changing the topic queue.

GitHub Actions uploads a `legal-memo-<run-id>-<attempt>` artifact for 14 days when legal diagnostics exist. It contains the topic index, source paths, complete memoranda and per-attempt validation results. Use the supplied excerpts and mapped packs to verify the law. The automated excerpt check verifies that a quotation exists in the supplied source. It cannot determine whether the source supports the model's legal interpretation or remains current.

- **`needs_legal_review`** — A source mapping or legal validation failure held the topic. Review the artifact and the administrator email, resolve the issue, then use the workflow's `topic_index` input on `main`. Check the current zero-based index before dispatching. Scheduled runs continue with the first unused topic.
- **`delivery_pending`** — The workflow saved a reservation before attempting SendGrid delivery. Check the linked workflow and SendGrid activity before editing the queue. If acceptance is confirmed, record the title and date and mark the topic `used`. If no message was accepted, remove the reservation and restore the topic to its previous state. Leave ambiguous deliveries pending while investigating.
- **`used`** — SendGrid accepted the email and the workflow saved the completed topic state. Acceptance confirms submission to SendGrid. Check SendGrid activity for final delivery status when needed.

The production workflow requires permission to push `topics.json` updates to `main`. If the reservation cannot be saved remotely, the generator stops before sending the draft. Terminal legal failures attempt one administrator notification via the existing SendGrid recipient configuration.

## Knowledge principles

The automation should rely on structured, reusable knowledge wherever possible.

Legal materials should be placed in the most specific applicable folder. Editorial guidance should be kept separate from legal authorities. Generated outputs should not be treated as source knowledge unless they have been reviewed and deliberately promoted into the knowledge base.

## Quality control

Generated content should be reviewed before publication for:

- Legal accuracy
- Correct use of Swiss immigration and nationality terminology
- Appropriate use of legal authorities
- Clear distinction between law, policy, practice, and commentary
- Editorial consistency
- Website tone and formatting
- Avoidance of overstatement
- Suitability for the intended audience

## Inclusion criteria

Add files to this repository only if they support the Swiss blog automation system, legal knowledge base, topic planning, content generation, editorial review, or publishing workflow.

Do not add unrelated client files, one-off research fragments, or unreviewed legal material unless they are clearly marked and stored in an appropriate working area.

## File naming

Use clear, descriptive filenames.

Suggested formats:

`legal_authority_[topic]_[jurisdiction_or_source]_[short_description]`

`internal_note_[topic]_[short_description]`

`editorial_guidance_[purpose_or_page_type]`

`generated_blog_run_[topic_or_batch]_[date]`

Examples:

`legal_authority_c_permit_swiss_settlement_framework_pack`

`internal_note_family_reunification_dependency_evidence`

`editorial_guidance_swiss_immigration_blog_style`

`generated_blog_run_c_permit_absences_2026_04_28`
