# Data model routing

The Data sidebar follows Calliope's selected provider and model. At the start
of each draft revision, `Explorer.client()` reads the live Hermes configuration:
`platforms.api_server.extra.model_name` takes precedence over `model.default`.
That selection is retained for clarification rewriting, bounded discovery,
SQL generation and validation repair within the revision.

Calliope sends text messages to the authenticated internal Hermes endpoint
`POST /v1/data/completion`. Hermes resolves the selected provider through its
shared runtime and completion adapters, covering OAuth subscriptions, API keys
and registered external-process providers. Credentials remain inside Hermes.
The old `rvbbit.backends` transport whitelist and `WAREHOUSE_DATA_*` overrides
are not used for model selection.

The endpoint performs a single completion, without an agent session, native
tools, memory, skills or an injected agent prompt. It accepts only an explicit
provider/model, text messages, output budget and timeout. It does not use
background-task models or switch providers on failure. Provider authentication,
rate limits and model availability still apply. Unsupported or disconnected
routes fail explicitly.

Data continues to own its three-lookup limit, authorized source selection,
read-only SQL guard, validation, separate final execution and saved history.
Completion receipts return the provider's reported usage and model to Data's
existing accounting path; Hermes does not separately record these calls as
auxiliary work. Some provider adapters omit cache detail or response IDs; no
values are invented when absent.

Ship both the Hermes route and Calliope client together. The Hermes image build
runs `docker/calliope/test-hermes-data-completion.py`, including a check against
the pinned upstream adapter interface. The server regression suite is
`test_data_completion.py` and `test_data_explorer.py`; the latter's database
cases require `ACTIVITY_TEST_ADMIN_DSN` pointing to a disposable test cluster.
