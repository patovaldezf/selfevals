# Troubleshooting

Common errors `selfevals` may show, what they mean, and how to fix
them. All user-facing errors exit with code **2** (`error: ...` on
stderr, no traceback). An exit code of **1** means something internal
went wrong — file a bug.

---

## 1. Invalid YAML in the experiment spec

**Symptom**

```text
$ selfevals run evals/experiments/foo.yaml
error: could not parse YAML /path/to/foo.yaml: while parsing a flow node
  expected the node content, but found ']'
  in "<unicode string>", line 1, column 18
  hint: open /path/to/foo.yaml and check indentation and unclosed brackets; yaml
  errors usually point at the line just *after* the mistake
```

**Cause**

The YAML parser could not decode the file — typically an unclosed
bracket, a mis-indented block, or a stray tab.

**Fix**

- Open the file at the cited path. The YAML library points one line
  *past* the problem; the actual mistake is usually on the line above.
- Run `yamllint <path>` (or any YAML linter) for a structural view.
- If you mean a YAML mapping but accidentally typed a flow-style list,
  rewrite using block style:

  ```yaml
  # Bad (forgot the closing bracket)
  garbage: [unclosed

  # Good
  garbage:
    - first
    - second
  ```

---

## 2. Dataset path not found

**Symptom**

```text
error: dataset path 'evals/datasets/pingpang.jsonl' not found
  hint: did you mean evals/datasets/pingpong.jsonl?
```

**Cause**

The `dataset.cases_path` value in your YAML points to a file that does
not exist on disk. The path is resolved **relative to the YAML file's
directory**, not the current working directory.

**Fix**

- If the suggestion is right, fix the typo.
- Otherwise check the path is relative to the experiment file. For
  example, an experiment at `evals/experiments/foo.yaml` and a dataset
  at `evals/datasets/cases.jsonl` should use `cases_path:
  ../datasets/cases.jsonl`.

---

## 3. Grader name not registered

**Symptom**

```text
error: grader 'foo_grader' not registered; available: deterministic, llm_judge
```

**Cause**

One of your cases declares `graders: [foo_grader]`, but `foo_grader` is
not in the grader registry.

**Fix**

- Use one of the listed names, **or**
- Register your grader at startup:

  ```python
  from selfevals.graders.registry import register_grader
  from my_pkg.graders import MyGrader

  register_grader("my_grader", lambda: MyGrader())
  ```

- If you intend to declare a grader inside the YAML, use the top-level
  `graders:` block (see `docs/spec/grader-spec.md`) — the loader will
  register the named factory before validation runs.

---

## 4. HTTP adapter cannot reach the endpoint

**Symptom (from a trace, after `selfevals run`)**

```text
adapter_error: HTTP adapter could not reach http://localhost:8080/agent
  ([Errno 61] Connection refused); check the endpoint is running and reachable
  from this host
```

**Cause**

The agent is configured as an HTTP endpoint, but the URL is
unreachable (server not running, firewalled, DNS misconfigured) or it
times out before responding.

**Fix**

- Boot the endpoint and re-run. `curl <url>` should succeed before
  `selfevals run` does.
- If the server is slow, increase `timeout_seconds` on the
  `HttpEndpointAdapter` construction site.
- If you are running behind a proxy, set `HTTPS_PROXY` / `HTTP_PROXY`
  before invoking `selfevals`.

---

## 5. Cannot connect to Postgres

selfevals is Postgres-only. Storage comes from `SELFEVALS_STORAGE_URL` (or the
global `--db <postgres-url>` flag); with neither set, the CLI/API raises rather
than guessing.

**Symptom (no storage configured)**

```text
error: no storage configured: set SELFEVALS_STORAGE_URL to a Postgres URL
```

**Symptom (connection refused / auth failed)**

```text
connection to server at "localhost" (::1), port 5433 failed: Connection refused
# or:
password authentication failed for user "selfevals"
```

**Cause**

- *No storage configured*: neither `SELFEVALS_STORAGE_URL` nor `--db` is set.
  Postgres is required — there is no DB-less run mode. `docker compose up -d`
  starts the local one from `.env.example`.
- *Connection refused*: Postgres isn't running, or the URL points at the wrong
  host/port. The local default in `.env.example` is port `5433`.
- *Auth failed*: wrong user/password/database in the URL.

**Fix**

- Bring up local services: `docker compose up -d postgres redis`, then
  `cp .env.example .env` and `set -a && source .env && set +a` so
  `SELFEVALS_STORAGE_URL` is exported.
- Verify the URL: `selfevals --db postgresql://selfevals:selfevals@localhost:5433/selfevals workspace show <ws>`.
- Migrating off a legacy SQLite file? Import it once with
  `selfevals migrate-sqlite ./old.sqlite --to "$SELFEVALS_STORAGE_URL"` —
  SQLite is not a live backend.

---

## Anything else

If you hit an error that isn't friendly (i.e. exit code **1** or a
visible traceback), that's a bug. Please file an issue including the
traceback and the YAML / commit that triggered it.
