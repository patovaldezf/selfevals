# Troubleshooting

Common errors `bootstrap` may show, what they mean, and how to fix
them. All user-facing errors exit with code **2** (`error: ...` on
stderr, no traceback). An exit code of **1** means something internal
went wrong — file a bug.

---

## 1. Invalid YAML in the experiment spec

**Symptom**

```text
$ bootstrap run evals/experiments/foo.yaml --no-persist
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
  from bootstrap.graders.registry import register_grader
  from my_pkg.graders import MyGrader

  register_grader("my_grader", lambda: MyGrader())
  ```

- If you intend to declare a grader inside the YAML, use the top-level
  `graders:` block (see `docs/spec/grader-spec.md`) — the loader will
  register the named factory before validation runs.

---

## 4. HTTP adapter cannot reach the endpoint

**Symptom (from a trace, after `bootstrap run`)**

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
  `bootstrap run` does.
- If the server is slow, increase `timeout_seconds` on the
  `HttpEndpointAdapter` construction site.
- If you are running behind a proxy, set `HTTPS_PROXY` / `HTTP_PROXY`
  before invoking `bootstrap`.

---

## 5. SQLite database locked or corrupted

**Symptom (locked)**

```text
error: sqlite database /path/to/bootstrap.sqlite is locked
  hint: another bootstrap process is using it; try `--db <new-path>` or wait
```

**Symptom (corrupted)**

```text
error: sqlite database /path/to/bootstrap.sqlite is corrupted or not a valid
  bootstrap db
  hint: back up the file and re-run with `--db <new-path>` to start clean
```

**Cause**

- *Locked*: a different `bootstrap` process holds a write lock — common
  if you have `bootstrap serve` running and then start a second
  `bootstrap run` against the same db.
- *Corrupted*: the file is not a SQLite database (overwritten, truncated,
  copied mid-write) or it was created by a different application.

**Fix**

- For *locked*: stop the other process, or point this run at a separate
  db with `--db /tmp/scratch.sqlite`.
- For *corrupted*: move the file aside (do **not** delete until
  you have inspected it), then re-run with a fresh `--db` path. If
  the file is salvageable, see the SQLite "Database Disk Image Is
  Malformed" recovery doc.

---

## Anything else

If you hit an error that isn't friendly (i.e. exit code **1** or a
visible traceback), that's a bug. Please file an issue including the
traceback and the YAML / commit that triggered it.
