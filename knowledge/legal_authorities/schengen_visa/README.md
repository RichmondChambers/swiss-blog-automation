# Swiss Schengen Visa Legal Authority Packs

This folder contains legal authority packs for the Swiss Schengen visa blog automation workflow.

The packs are used by `generate_and_publish.py` to ground legal analysis, drafting and validation for blog posts concerning Swiss Schengen visas, including Type C short-stay visas, permitted travel purposes, 90 / 180-day limits, entry conditions, application procedure, financial means, accommodation, invitation evidence, medical insurance, biometrics, refusals, appeals, EES, VIS and comparison with Type D visas and residence permits.

## Folder Structure

```text
schengen_visa/
  broad/
  core/
  specific/
  README.md
```

## Folder Purposes

### `core/`

Use this folder for foundational authority packs that explain the overall Swiss Schengen visa framework.

Core packs should cover cross-cutting legal architecture, including the relationship between:

the Schengen Borders Code;  
the Visa Code;  
Swiss implementation legislation and ordinances;  
SEM guidance and official procedure materials;  
consular practice;  
competent state rules;  
Type C visa requirements;  
short-stay entry conditions;  
90 / 180-day calculation;  
border control discretion;  
refusal, appeal and reapplication procedure.

A core pack should be broad enough to support general Swiss Schengen visa topics across multiple applicant profiles, travel purposes and evidence scenarios.

### `broad/`

Use this folder for broad thematic packs that apply across several Swiss Schengen visa scenarios.

Examples include:

legal framework and architecture;  
permitted purposes and short-stay limits;  
90 / 180-day rule and overstay risk;  
entry conditions and border checks;  
visa requirement exemptions and competent state;  
application procedure, place of filing and jurisdiction;  
documents and evidential credibility;  
accommodation, invitation and host evidence;  
financial means, sponsorship and declaration;  
medical insurance and biometrics;  
business visits, work boundary and professional activity;  
fees, exemptions and facilitation;  
refusals, appeals and reapplications;  
EES, VIS and digital records;  
comparison with Type D visas and residence permits.

Broad packs should help the generator deal with recurring issues that arise across more than one Schengen visa scenario.

### `specific/`

Use this folder for packs focused on narrower risk issues, applicant profiles, nationality groups, travel purposes or complex sub-categories within Swiss Schengen visa applications.

Examples include:

family visitors;  
private visitors staying with Swiss hosts;  
business visitors;  
conference or event attendees;  
medical visitors;  
repeat travellers;  
multiple-entry visa applicants;  
applicants with prior refusals;  
applicants with weak ties to their country of residence;  
applicants relying on host sponsorship;  
applicants with prior overstays;  
border refusal risk;  
unclear purpose of stay;  
short professional activity close to the work boundary.

Specific packs should be used where a topic needs deeper treatment than the core or broad thematic packs provide.

## Naming Convention

Use clear, descriptive filenames beginning with:

`legal_authority_swiss_schengen_visa_`

For core framework packs, use the prefix:

`legal_authority_core_swiss_schengen_visa_`

Recommended examples:

`legal_authority_core_swiss_schengen_visa_type_c_framework.md`  
`legal_authority_swiss_schengen_visa_legal_framework_and_architecture.md`  
`legal_authority_swiss_schengen_visa_permitted_purposes_and_short_stay_limits.md`  
`legal_authority_swiss_schengen_visa_90_180_day_rule_and_overstay_risk.md`  
`legal_authority_swiss_schengen_visa_entry_conditions_and_border_checks.md`  
`legal_authority_swiss_schengen_visa_requirement_exemptions_and_competent_state.md`  
`legal_authority_swiss_schengen_visa_application_procedure_place_of_filing_and_jurisdiction.md`  
`legal_authority_swiss_schengen_visa_documents_and_evidential_credibility.md`  
`legal_authority_swiss_schengen_visa_accommodation_invitation_and_host_evidence.md`  
`legal_authority_swiss_schengen_visa_financial_means_sponsorship_and_declaration.md`  
`legal_authority_swiss_schengen_visa_medical_insurance_and_biometrics.md`  
`legal_authority_swiss_schengen_visa_business_visits_work_boundary_and_professional_activity.md`  
`legal_authority_swiss_schengen_visa_fees_exemptions_and_facilitation.md`  
`legal_authority_swiss_schengen_visa_refusals_appeals_and_reapplications.md`  
`legal_authority_swiss_schengen_visa_ees_vis_and_digital_records.md`  
`legal_authority_swiss_schengen_visa_comparison_with_type_d_and_residence_permits.md`

Use lowercase filenames, underscores rather than spaces, and the `.md` extension for Markdown authority packs.

## Source Hierarchy

When drafting or maintaining authority packs, apply the following source hierarchy:

EU / Schengen legislation and directly relevant Schengen legal instruments;  
Swiss legislation, ordinances and implementation materials;  
SEM Directives, SEM instructions, official SEM factsheets and official federal guidance;  
official consular guidance and application procedure materials;  
Federal Supreme Court authority and other relevant case law;  
official border, VIS, EES or Schengen system guidance where relevant;  
consular and administrative practice, where evidenced;  
internal legal analysis and drafting cautions;  
existing website editorial content, for positioning only.

Website editorial content must not be treated as legal authority.

## Drafting Standards for Authority Packs

Each authority pack should, where possible, include:

an executive legal summary;  
a structural overview of the relevant route, issue or framework;  
a table of legal propositions;  
primary legal texts;  
official guidance and administrative materials;  
key case law, where relevant;  
consular or authority practice notes, where relevant;  
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
consular practice;  
border practice;  
digital record practice;  
unresolved or practice-sensitive points.

Do not describe guidance as law. Do not describe visa eligibility as an entitlement to entry. Do not describe a Type C visa as permission to reside. Do not describe consular practice as uniform Schengen law. Do not describe possession of a visa as guaranteed admission at the border.

## Terminology

Use both English and Swiss / Schengen legal references where useful.

Preferred public-facing terminology includes:

Schengen visa;  
Swiss Schengen visa;  
Type C visa;  
short-stay visa;  
90 / 180-day rule;  
permitted purpose of stay;  
tourism;  
family visit;  
private visit;  
business visit;  
invitation letter;  
host declaration;  
accommodation evidence;  
financial means;  
sponsorship;  
declaration of sponsorship;  
travel medical insurance;  
biometrics;  
VIS;  
EES;  
border checks;  
entry conditions;  
refusal;  
appeal;  
reapplication;  
competent Schengen state;  
main destination;  
first entry;  
Type D visa;  
residence permit;  
SEM;  
consulate;  
Schengen Borders Code;  
Visa Code.

Avoid using informal terminology that suggests a Schengen visa guarantees entry, permits residence, or authorises work in Switzerland.

## Important Drafting Cautions

A Swiss Schengen visa should not be treated as a residence permit or work authorisation.

The analysis usually depends on:

applicant nationality;  
visa requirement or exemption;  
residence country;  
competent Schengen state;  
main destination;  
first entry;  
place of filing;  
consular jurisdiction;  
purpose of travel;  
duration of stay;  
90 / 180-day calculation;  
single-entry or multiple-entry visa;  
prior Schengen travel history;  
previous refusals;  
previous overstays;  
financial means;  
sponsorship;  
accommodation;  
invitation and host evidence;  
employment, study or family ties;  
return evidence;  
travel medical insurance;  
biometrics;  
VIS and EES records;  
document credibility;  
intention to leave;  
public policy or security issues;  
border control discretion.

Authority packs should preserve these distinctions so that public blog posts do not overstate rights, imply that a Type C visa permits residence or work, treat possession of a visa as guaranteed entry, or flatten purpose-specific and consular-practice distinctions into uniform outcomes.

## GitHub Folder Tracking

Empty folders are tracked using `.gitkeep` files.

Once a folder contains at least one real authority pack, the `.gitkeep` file in that folder may be removed.
