# Family Reunification Legal Authority Packs

This folder contains legal authority packs for the Swiss family reunification blog automation workflow.

The packs are used by `generate_and_publish.py` to ground legal analysis, drafting and validation for blog posts concerning Swiss family reunification, including spouse, child, partner, dependant, sponsor-status, evidence, timing, refusal and procedure issues.

## Folder Structure

```text
family_reunification/
  broad/
  core/
  issue_specific/
  route_specific/
  README.md
```

## Folder Purposes

### `core/`

Use this folder for foundational authority packs that explain the overall Swiss family reunification framework.

Core packs should cover cross-cutting legal architecture, including the relationship between:

* the Foreign Nationals and Integration Act (LEI / AIG);
* the Ordinance on Admission, Stay and Employment (OASA / VZAE);
* the Agreement on the Free Movement of Persons (AFMP / FZA);
* SEM Directives and official procedure guidance;
* ordinary family reunification routes;
* sponsor-status distinctions;
* entitlement, discretion and procedural sequencing.

A core pack should be broad enough to support general family reunification topics across multiple routes.

### `broad/`

Use this folder for broad thematic packs that apply across several family reunification routes.

Examples include:

* financial requirements and sponsor stability;
* housing and household formation;
* relationship and dependency evidence;
* timing, age limits and filing strategy;
* procedure from abroad and D-visa sequencing;
* refusals, appeals and reapplications;
* cantonal practice and procedure;
* comparison by sponsor status and nationality;
* residence consequences after separation, divorce or family breakdown.

Broad packs should help the generator deal with recurring issues that arise across more than one route.

### `route_specific/`

Use this folder for packs focused on a specific family reunification route or applicant category.

Examples include:

* spouse and registered partner reunification;
* child reunification;
* fiancé / intended marriage and post-marriage residence planning;
* unmarried partner cases;
* adult dependent relative cases.

Route-specific packs should identify the legal basis, sponsor category, entitlement or discretion, eligibility conditions, procedure, evidence, risks and drafting cautions for the specific route.

### `issue_specific/`

Use this folder for packs focused on narrower risk issues or complex sub-categories within family reunification.

Examples include:

* same-sex couple cases;
* stepchildren and blended families;
* L-permit sponsors;
* children approaching age limits;
* late child reunification;
* inadequate housing;
* social assistance or supplementary benefits;
* weak civil-status evidence;
* DNA or identity evidence;
* sham marriage or abuse concerns.

Issue-specific packs should be used where a topic needs deeper treatment than the general route pack provides.

## Naming Convention

Use clear, descriptive filenames beginning with:

```text
legal_authority_family_reunification_
```

Recommended examples:

```text
legal_authority_family_reunification_core_framework_pack.md
legal_authority_family_reunification_financial_requirements_and_sponsor_stability_pack.md
legal_authority_family_reunification_spouse_and_registered_partner_pack.md
legal_authority_family_reunification_child_pack.md
legal_authority_family_reunification_unmarried_partner_pack.md
legal_authority_family_reunification_l_permit_sponsor_pack.md
legal_authority_family_reunification_children_approaching_age_limits_pack.md
```

Use lowercase filenames, underscores rather than spaces, and the `.md` extension for Markdown authority packs.

## Source Hierarchy

When drafting or maintaining authority packs, apply the following source hierarchy:

1. Federal legislation, ordinances and treaties;
2. SEM Directives, SEM instructions, official SEM factsheets and official federal guidance;
3. Federal Supreme Court authority and other relevant case law;
4. Official cantonal migration authority guidance;
5. Internal legal analysis and drafting cautions;
6. Existing website editorial content, for positioning only.

Website editorial content must not be treated as legal authority.

## Drafting Standards for Authority Packs

Each authority pack should, where possible, include:

* an executive legal summary;
* a structural overview of the relevant route, issue or framework;
* a table of legal propositions;
* primary legal texts;
* official guidance and administrative materials;
* cantonal practice notes where relevant;
* practical legal analysis;
* drafting cautions;
* source list;
* open questions or limitations.

Legal propositions should distinguish clearly between:

* entitlement;
* eligibility only;
* discretion;
* facilitation;
* guidance;
* procedure;
* evidential practice;
* cantonal practice;
* unresolved or practice-sensitive points.

Do not describe guidance as law. Do not describe discretionary routes as rights. Do not describe cantonal practice as uniform Swiss law.

## Terminology

Use both English and Swiss legal references where useful.

Preferred public-facing terminology includes:

* `LEI / AIG` for the Foreign Nationals and Integration Act;
* `OASA / VZAE` for the Ordinance on Admission, Stay and Employment;
* `AFMP / FZA` for the Agreement on the Free Movement of Persons;
* `SEM Directives` for SEM’s immigration directives.

Avoid using only one language abbreviation where both French and German references are commonly relevant in Swiss immigration practice.

## Important Drafting Cautions

Family reunification should not be treated as a single route.

The analysis usually depends on:

* sponsor status;
* applicant nationality;
* relationship category;
* age;
* dependency;
* residence history;
* whether the case falls under LEI / AIG, AFMP / FZA, provisional admission or asylum law;
* whether the route creates an entitlement or is discretionary;
* filing deadlines;
* cohabitation and household formation;
* housing and financial requirements;
* language or integration requirements;
* civil-status documentation;
* cantonal procedure.

Authority packs should preserve these distinctions so that public blog posts do not overstate rights or flatten route-specific conditions.

## GitHub Folder Tracking

Empty folders are tracked using `.gitkeep` files.

Once a folder contains at least one real authority pack, the `.gitkeep` file in that folder may be removed.

