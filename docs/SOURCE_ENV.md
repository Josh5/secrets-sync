# Environment Source Guide

The `env` source reads secrets directly from the process environment. It is a convenient way to capture values that already exist in your shell, CI job, or container runtime and forward them into sinks such as AWS SSM or Secrets Manager without another intermediate file.

## When to use

- Local development where you export `APP_*` variables before syncing.
- CI pipelines that receive secrets from a platform-specific key vault and expose them as env vars.
- Migrating from a legacy `.env` file while gradually moving items into other sources.

## Configuration options

```yaml
sources:
  - name: env
    type: env
    options:
      include_regex:
        - "^APP_.*"
        - "^SHARED_.*"
      exclude_regex: "^APP_DEBUG$"
      keys:
        - SHARED_TRACING_ENDPOINT
      strip_prefix: ["APP_", "SHARED_"]
      strip_suffix: "_FILE"
```

- `include_regex`: Regex or list of regexes applied to the full environment-variable key. Matching any entry includes the variable.
- `exclude_regex`: Regex or list of regexes that remove matches after inclusion filters run. Helpful to skip secrets such as `*_DEBUG` while still using a broad include.
- `keys`: Explicit list of variables to pull even if they do not match the include filters. Use this for one-off shared values such as `SHARED_TRACING_ENDPOINT`.
- `strip_prefix`: Removes leading text before emitting the final name. Accepts a string or list.
- `strip_suffix`: Removes trailing text before emitting the final name. Accepts a string or list.

All options are optional; with no filters defined, every variable is exported exactly as it appears in the environment.

## Best practices

- Prefer explicit `include_regex`/`keys` filters to avoid accidentally syncing unrelated variables such as AWS credentials.
- Combine with `vars` templating for consistent prefixes:

```yaml
vars:
  ENVIRONMENT_NAME: dev

sinks:
  - type: ssm
    options:
      prefix: "/env/{{ ENVIRONMENT_NAME }}/"
    sources: ["env"]
```
