# MISSION-CONTROL-CALYX-REPORT-MVP-001

## Objective

Add a usable **Mission Control → Calyx Report** workspace for generating evidence-grounded FCOS newsletter articles.

## Same-day MVP

- Add a visible Calyx Report navigation entry.
- Add publication, topic, audience, target length, citation mode, confidence mode, optional section, and visual selectors.
- Default to the Five Cities Orchid Society Newsletter and the global orchid-conservation project survey preset.
- Provide an evidence preview state before article generation.
- Support explicit full Continuum mode and limited-evidence mode.
- Never fabricate citations, confidence, project counts, or current project status.
- Provide Markdown preview, copy, and download.
- Preserve form state and show loading, unavailable dependency, and error states.
- Do not auto-publish.

## Backend contract

Prefer the backend Journalism Engine endpoints from `jsp1440/orchid-calyx-backend#173`. Until available, use a documented limited-evidence adapter to existing briefing/literature capabilities.

## Acceptance criteria

1. Calyx Report is reachable from Mission Control navigation.
2. The conservation survey preset generates a Markdown article.
3. Citation and confidence limitations are explicit.
4. Markdown can be copied and downloaded.
5. Unsupported claims are not presented as verified.
6. Basic UI and API error handling tests pass.

## Delivery

Keep the pull request in Draft until implementation and validation are complete. Report the head SHA, exact tests, and backend limitations.
