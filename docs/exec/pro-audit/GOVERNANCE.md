# Branch protection proposal

Read-only inspection on 2026-09-22 found no main branch protection (HTTP 404,
“Branch not protected”) and an empty repository ruleset list. The existing
successful checks are `check (ubuntu-latest)` and `check (macos-latest)`, emitted
by GitHub Actions app ID 15368. The names remain stable in the new shared runner.

The prepared [ruleset](main-ruleset.json) requires a PR, both fixed platform
checks and resolved review threads, and prevents main deletion and force push.
No bypass actors are preauthorized. Review count is zero so a sole maintainer
can use the PR process; choosing an independent reviewer and enforcing approval
count is an explicit owner decision. Rolling and hardware checks are not
misrepresented as available mandatory checks.

This configuration has **not** been applied. After the release owner reviews it,
they can submit the JSON to `POST /repos/Strangelight-Merser/moon-mqtt-client/rulesets`
and verify the resulting active rules. The field definitions are from the
[GitHub repository rules REST documentation](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset).
Do not infer remote protection from the presence of this file.
