# Computer app repair acceptance

`invoice-review-v1.html` is deliberately defective synthetic input, not a shipped
product page. Publish it privately through `create_live_app` as an HTML app. Its
row filters work but its summary total ignores those filters. Have Callie open
the actual published URL in the Computer browser, exercise the cases below,
record the failing observations, inspect the stored source, publish a repair
through `update_live_app` with `expected_version`, and repeat every case against
the new immutable version. Do not use a separate hidden capture browser or a
local copy as evidence that the published route works.

| Customer | Status | Rows | Correct total |
| --- | --- | ---: | ---: |
| All customers | All statuses | 5 | $1,000.00 |
| All customers | Paid | 3 | $550.00 |
| North | Paid | 2 | $400.00 |
| South | Pending | 1 | $250.00 |
| Reset filters | | 5 | $1,000.00 |

Keep the app URL, immutable version, observed row count and total, expected
values, and pass/fail results in the Computer job output. The run's ordinary
receipts retain code, intent, timestamps, output and the corresponding 2 FPS
video. Verify independently from the stored version and the final guest output.
Browser sign-in should follow the normal human takeover flow if needed.
