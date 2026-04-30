# Swiss Citizenship Legal Authority Packs

This folder contains legal authority packs for the Swiss citizenship and naturalisation blog automation workflow.

The packs are used by `generate_and_publish.py` to ground legal analysis, drafting and validation for blog posts concerning Swiss citizenship and naturalisation, including ordinary naturalisation, facilitated naturalisation, residence counting, continuity, C permit requirements, integration, language evidence, cantonal and communal procedure, absences, interrupted residence, refusals, postponements and appeals.

## Folder Structure

swiss_citizenship/
  broad/
  core/
  issue_specific/
  route_specific/
  README.md

## Folder Purposes

### core/

Use this folder for foundational authority packs that explain the overall Swiss citizenship and naturalisation framework.

Core packs should cover cross-cutting legal architecture, including the relationship between:

the Swiss Citizenship Act (SCA / BüG / LN);
the Swiss Citizenship Ordinance (SCO / BüV / OLN);
SEM guidance and official federal naturalisation materials;
ordinary naturalisation;
facilitated naturalisation;
federal, cantonal and communal competence;
residence requirements and residence counting;
integration and language requirements;
good character, public-order and compliance considerations;
procedure, discretion and evidential assessment.

A core pack should be broad enough to support general Swiss citizenship and naturalisation topics across multiple routes and applicant categories.

### broad/

Use this folder for broad thematic packs that apply across several Swiss citizenship or naturalisation routes.

Examples include:

ordinary Swiss naturalisation eligibility;
facilitated Swiss naturalisation by marriage;
Swiss naturalisation after absence or interrupted residence;
C permit requirements for naturalisation;
cantonal and communal naturalisation procedure;
integration requirements;
language requirements and evidence;
residence counting and continuity;
refusal, postponement and appeal;
drafting cautions and legal proposition banks.

Broad packs should help the generator deal with recurring issues that arise across more than one naturalisation route or applicant category.

### route_specific/

Use this folder for packs focused on a specific citizenship or naturalisation route, applicant category or procedural pathway.

Examples include:

ordinary naturalisation;
facilitated naturalisation by marriage to a Swiss citizen;
facilitated naturalisation for children of Swiss citizens;
facilitated naturalisation for third-generation foreign nationals;
re-naturalisation;
naturalisation for registered partners or spouses in complex residence histories;
naturalisation following long-term residence and C permit acquisition.

Route-specific packs should identify the legal basis, route, applicant category, eligibility conditions, residence requirements, integration requirements, procedure, evidence, risks and drafting cautions for the specific route.

### issue_specific/

Use this folder for packs focused on narrower risk issues or complex sub-categories within Swiss citizenship and naturalisation work.

Examples include:

interrupted residence;
extended absences from Switzerland;
insufficient residence continuity;
absence of a C permit where one is required;
language evidence problems;
integration concerns;
social assistance or debt enforcement issues;
criminality, public-order or compliance concerns;
cantonal or communal objections;
postponement rather than refusal;
appeal prospects and procedural fairness;
family members applying at different times.

Issue-specific packs should be used where a topic needs deeper treatment than the general route or broad thematic pack provides.

## Naming Convention

Use clear, descriptive filenames beginning with:

legal_authority_swiss_naturalisation_

Recommended examples include:

legal_authority_swiss_naturalisation_core_framework_pack.md
legal_authority_ordinary_swiss_naturalisation_eligibility.md
legal_authority_facilitated_swiss_naturalisation_by_marriage.md
legal_authority_swiss_naturalisation_residence_counting_and_continuity.md
legal_authority_swiss_naturalisation_c_permit_requirement.md
legal_authority_swiss_naturalisation_integration_requirements.md
legal_authority_swiss_naturalisation_language_requirements_and_evidence.md
legal_authority_swiss_naturalisation_cantonal_communal_procedure.md
legal_authority_swiss_naturalisation_after_absence_or_interrupted_residence.md
legal_authority_swiss_naturalisation_refusal_postponement_and_appeal.md
legal_authority_swiss_naturalisation_drafting_cautions_and_proposition_bank.md

Use lowercase filenames, underscores rather than spaces, and the `.md` extension for Markdown authority packs.

## Source Hierarchy

When drafting or maintaining authority packs, apply the following source hierarchy:

Federal legislation, ordinances and treaties;
SEM guidance, SEM instructions, official SEM factsheets and official federal naturalisation materials;
Federal Supreme Court authority and other relevant case law;
Official cantonal and communal naturalisation authority guidance;
Internal legal analysis and drafting cautions;
Existing website editorial content, for positioning only.

Website editorial content must not be treated as legal authority.

## Drafting Standards for Authority Packs

Each authority pack should, where possible, include:

an executive legal summary;
a structural overview of the relevant route, issue or framework;
a table of legal propositions;
primary legal texts;
official guidance and administrative materials;
cantonal and communal practice notes where relevant;
practical legal analysis;
drafting cautions;
source list;
open questions or limitations.

Legal propositions should distinguish clearly between:

entitlement;
eligibility only;
discretion;
facilitation;
guidance;
procedure;
evidential practice;
cantonal practice;
communal practice;
unresolved or practice-sensitive points.

Do not describe guidance as law. Do not describe eligibility as an automatic right to Swiss citizenship. Do not describe discretionary or evaluative naturalisation decisions as entitlements. Do not describe cantonal or communal practice as uniform Swiss law.

## Terminology

Use both English and Swiss legal references where useful.

Preferred public-facing terminology includes:

Swiss citizenship;
Swiss naturalisation;
ordinary naturalisation;
facilitated naturalisation;
SCA / BüG / LN for the Swiss Citizenship Act;
SCO / BüV / OLN for the Swiss Citizenship Ordinance;
SEM guidance for official federal naturalisation guidance;
C permit / settlement permit;
residence counting;
residence continuity;
integration requirements;
language requirements;
cantonal and communal procedure.

Avoid using only one language abbreviation where German, French and Italian references may be relevant in Swiss citizenship practice.

## Important Drafting Cautions

Swiss citizenship and naturalisation should not be treated as a single uniform route.

The analysis usually depends on:

type of naturalisation route;
nationality and residence history;
ordinary or facilitated naturalisation basis;
length and continuity of residence;
whether residence abroad or interrupted residence affects eligibility;
C permit status, where required;
marriage, family relationship or descent from a Swiss citizen, where relevant;
integration record;
language evidence;
participation in economic life or education;
social assistance, debt enforcement or tax compliance issues;
criminality, public-order or security concerns;
federal, cantonal and communal decision-making roles;
local procedure and evidential expectations;
whether the issue concerns refusal, postponement, appeal or reapplication.

Authority packs should preserve these distinctions so that public blog posts do not overstate rights, flatten route-specific conditions or imply that citizenship follows automatically after a fixed number of years.

## GitHub Folder Tracking

Empty folders are tracked using `.gitkeep` files.

Once a folder contains at least one real authority pack, the `.gitkeep` file in that folder may be removed.
