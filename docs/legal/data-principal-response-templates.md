# Data-principal request and grievance letters (templates)

| Field | Value |
|---|---|
| template_version | 0.1 (2026-10-21) |
| Decision | [ADR-0048](../adr/ADR-0048-dpdp-processor-pack-and-data-principal-requests.md) decisions 5-9 |
| Based on | docs/08 §3 (PRV-010, PRV-011), §7 (retention and erasure), §15.3 (financial records); NFR-PRV-004 (7 days) |
| Placeholders | `{{school_short_name}}`, `{{privacy_contact_name}}`, `{{privacy_contact_phone}}`, `{{privacy_contact_email}}`, `{{request_reference}}`, `{{received_on}}`, `{{due_on}}`, `{{subject_name}}`, `{{requester_name}}`, `{{decision_lines}}`, `{{refusal_lines}}`, `{{next_steps}}`, `{{decided_on}}`, `{{nominee_name}}`, `{{nominee_relationship}}`, `{{purpose_label}}` |

> **Not legal advice.** Drafted by SchoolOS (D30). The school decides each request. Letters are filled in SchoolOS from the request record and printed or handed over; SchoolOS does not send them.

## 1. Acknowledgement (every request and grievance)

> Dear {{requester_name}},
>
> We received your request about {{subject_name}} on {{received_on}} (reference {{request_reference}}). We will answer by {{due_on}}. If we need anything more from you to confirm who you are, we will tell you.
>
> {{privacy_contact_name}}, {{school_short_name}} · {{privacy_contact_phone}} · {{privacy_contact_email}}

## 2. Access: your report is ready

> The attached report lists the information {{school_short_name}} holds about {{subject_name}} in SchoolOS, where it came from and how long we keep it. Aadhaar numbers are never stored; only the last 4 digits and the details as printed on the card are kept, and these are shown at the office. Please keep this report safe.

## 3. Correction

> We checked your request against the documents you gave us. {{decision_lines}} Changes to official records such as the admission register need a supporting document and a second staff member's approval; where we could not change something, we have said why. {{next_steps}}

## 4. Erasure

> We have erased: {{decision_lines}}
>
> We could not erase: {{refusal_lines}} (for example, the admission register and issued certificates are permanent school records, and fee records must be kept for 8 years for the school's accounts). We will delete them when the law no longer requires us to keep them.

## 5. Nomination

> We have recorded that {{nominee_name}} ({{nominee_relationship}}) may act for you if you are unable to. You can change or withdraw this at any time by contacting us.

## 6. Consent withdrawn

> We have recorded that you withdrew your consent for {{purpose_label}} on {{decided_on}}. We will not use your child's information for this from now on. Anything already published before today will be taken down where we can; tell us if you see it.

## 7. Grievance response

> Thank you for raising this with us on {{received_on}}. What we found: {{decision_lines}} What we have done: {{next_steps}} If you are not satisfied with this answer, you may complain to the Data Protection Board of India.
