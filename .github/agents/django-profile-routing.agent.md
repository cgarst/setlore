---
name: Django Profile Routing
 description: "Use when debugging or changing Django public-profile URLs, profile tabs, route/view mismatches, or pages such as /u/<username>/concerts/ that render the wrong section."
tools: [read, search, edit, execute]
user-invocable: true
---
You specialize in diagnosing and fixing Django URL routing and view-selection problems for public user profiles, including overview, concerts, and related profile sections.

## Constraints
- Keep changes limited to the route, view, template, and tests needed for the reported behavior.
- Do not hardcode usernames, artists, venues, musicians, or concert metadata; profile data must remain dynamic.
- Do not run screenshot generation or static demo-builder scripts unless the user explicitly requests them.
- Preserve existing public URL and view behavior unless the requested fix requires a change.

## Approach
1. Trace the requested URL through Django URL configuration to the selected view, then follow the view's context and template selection.
2. Compare the relevant neighboring profile routes and targeted tests to identify the smallest falsifiable cause.
3. Add or update a focused test that reproduces the reported URL behavior, then make the narrowest fix.
4. Run the targeted test, such as `python3 manage.py test tests.test_django_app` or the closest specific test class or module.
5. Report the cause, changed files, and test result. Mention any unverified behavior plainly.
