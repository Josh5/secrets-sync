# Secrets Sync

Async CLI to pull secrets from multiple sources (env, YAML, 1Password) and push to AWS SSM Parameter Store and/or AWS Secrets Manager with concurrency, per-sink rate limiting, retry/backoff, multi-file config merging, variable templating, and per-sink routing.

## Install

Requires Python 3.10+.

```
uv venv && uv pip install --editable .

source .venv/bin/activate
```

## Usage

Merge multiple YAML config files (later files override earlier values):

```
secrets-sync -f ./defaults.yml -f ./test-1.yml
```

Flags:

- `--file, -f PATH`: add a config file to merge (may be repeated; later overrides earlier).
- `--print-values`: print a preview of what will be pushed, grouped by sink. Combine with `--dry-run` for preview only.
- `--print-format {list,table,json}`: output format for preview (default `list`).
- `--dry-run`: collect and optionally print, but do not push to AWS.
- `--print-sync-details`: print a line for each item as it's synced (success/failure plus created/unchanged/changed). When combined with `--print-values`, each log also shows value snapshots (`created 'new'`, `unchanged 'old'`, or `changed 'old' -> 'new'`).

## Config

- `vars`: key/value map. Values here override environment variables during template interpolation. Placeholders `{{ VAR_NAME }}` in strings are replaced at load time. Missing variables cause an error.
- `aws`: configure which AWS region/profile the CLI should use for AWS API calls (same as the AWS CLI/boto3 provider chain):
  - `region`: optional explicit region. If omitted, the CLI falls back to `AWS_DEFAULT_REGION` or `AWS_REGION`.
  - `profile`: optional profile name from your `~/.aws/config`/`~/.aws/credentials`. If omitted, `AWS_PROFILE` (when set) is used, otherwise boto3 falls back to its default profile chain.
  - Regardless of profile, boto3 can still authenticate with exported credentials such as `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN`.
- `sources`: list of sources, each with `name`, `type`, and `options` (See the [Sources](#sources) section below for more details).
- `sinks`: list of sinks, each with `type`, `options`, and optional `sources` filter listing source names to route (See the [Sinks](#sinks) section below for more details).

Example: [examples/basic/dev.yaml](examples/basic/dev.yaml).

### Sources

- `env`: Reads directly from the running process environment. Use this source to capture secrets already loaded into the shell or CI job. See [docs/SOURCE_ENV.md](docs/SOURCE_ENV.md) for scenarios and examples.

  - `include_regex`: regex to include (or `include: [patterns]`)
  - `exclude`: list of regexes to exclude
  - `keys`: explicit variable names to include
  - `strip_prefix`: remove leading prefix from names

- `yaml`: Loads values from one or more YAML documents on disk. Files are merged in order so you can provide layered defaults plus environment overrides. Additional details live in [docs/SOURCE_YAML.md](docs/SOURCE_YAML.md).

  - `files`: list of YAML file paths (merged in order; later files override earlier)
  - `key`: dot-path to the subtree to read (e.g., `values` for the example shape)
  - Supported structures: mapping of `name: value`, or `{ values: [ { name, value, description } ] }`, or a list of `{ name, value, description }`.
  - Relative paths are resolved against the config file where they are declared (not the working directory). This also holds when merging multiple config files.

- `1password`: Fetches items from a 1Password vault and maps each item title to a secret. Requires the 1Password `op` CLI to be installed plus either a configured `service_account_token` or the `OP_SERVICE_ACCOUNT_TOKEN` environment variable for authentication. Full walkthrough: [docs/SOURCE_1PASSWORD.md](docs/SOURCE_1PASSWORD.md).

  - `vault`: Vault name (required).
  - `tag_filters`: Only items containing any of these tags are included. The list order also determines override priority when multiple items share the same title.
  - `include_regex`: Optional regex applied to item titles for additional filtering.
  - `service_account_token`: Inline token value; falls back to the `OP_SERVICE_ACCOUNT_TOKEN` environment variable when omitted.
  - `concurrency`: Number of parallel fetches when pulling item details (default `8`).

- `keeper`: Uses the Keeper Commander SDK/CLI session to pull records from Keeper Enterprise. Requires a logged-in Keeper Commander environment with persistent login or inline credentials. Reference guide: [docs/SOURCE_KEEPER.md](docs/SOURCE_KEEPER.md).

  - `folder`: Keeper folder or path to read from (required).
  - `tag_filters`: Only records whose custom `tags` field matches any supplied tag are included. The list order also determines override priority when multiple items share the same title.
  - `include_regex`: Optional regex applied to record titles.
  - `config_file`: Path to the Keeper Commander config (default `~/.keeper/config.json`).
  - `keeper_server`, `keeper_user`, `keeper_password`: Inline overrides (or `KEEPER_SERVER`, `KEEPER_USER`, `KEEPER_PASSWORD` env vars) for CLI login values. Overrides what is read from `config_file`.

### Sinks

- `ssm` options:

  - `prefix`: optional string prefix for parameter names (supports `{{ VAR }}` placeholders)
  - `type`: `SecureString` (default) or `String` (any other value errors at load time)
  - `tier`: `Standard` (default) or `Advanced`. Values over 4 KB (measured after UTF-8 encoding) are automatically promoted to the Advanced tier with a warning so large file-style secrets can be stored without changing the source config. Note: Values over 8 KB will fail with an error.
  - `overwrite`: boolean (default true)
  - `kms_key_id`: optional KMS key id for SecureString
  - `rate_limit_rps`, `concurrency`: control throughput

- `secrets_manager` options:
  - `prefix`: optional string prefix for secret names (supports `{{ VAR }}`)
  - `kms_key_id`, `rate_limit_rps`, `concurrency` similar to SSM

The AWS API usage for both AWS sinks are paced automatically: the sinks meter requests so they stay within the configured `rate_limit_rps`, and they fall back to exponential backoff with jitter whenever AWS responds with throttling errors.

Each sink may specify `sources: [source-name, ...]` to only accept items from those sources. If a sink references a source that does not exist, config loading fails with a clear error.

### Variables and templating

- `vars` provides values for `{{ VAR }}` placeholders anywhere in the config. Values in `vars` override environment variables with the same keys.
- If a placeholder cannot be resolved, config loading fails.

### Preview output

- `--print-values --print-format=list` (default): prints `full_name=value` under each sink header.
- `--print-format=table`: prints two columns (Name, Value) per sink.
- `--print-format=json`: prints a JSON array of sink objects with `name`, `type`, `prefix`, `sources`, and `items[]` (each with `name`, `value`, `description`).

Examples:

```
secrets-sync --dry-run --print-values -f ./defaults.yaml -f ./env.yaml
secrets-sync --dry-run --print-values --print-format=table -f ./examples/basic/dev.yaml
secrets-sync --dry-run --print-values --print-format=json -f ./examples/basic/dev.yaml
```

### Example config

```
vars:
  ENVIRONMENT_NAME: test-1

aws:
  region: ap-southeast-2

sources:
  - name: env
    type: env
    options:
      include_regex: '^APP_.*'
      strip_prefix: 'APP_'
  - name: external-yaml-file
    type: yaml
    options:
      files:
        - configs/default.yaml
        - configs/test-1.yaml
      key: values
  - name: 1password
    type: 1password
    options:
      vault: 'EnvironmentSecrets'
      include_regex: '^APP_.*'
      tag_filters: ['default','prod']

sinks:
  - name: ssm-secrets
    type: ssm
    options:
      prefix: '/env/{{ ENVIRONMENT_NAME }}/secret/'
      overwrite: true
      type: SecureString
      rate_limit_rps: 10
      concurrency: 10
    sources: [ '1password' ]
  - name: ssm-config
    type: ssm
    options:
      prefix: '/env/{{ ENVIRONMENT_NAME }}/config/'
      overwrite: true
      type: String
    sources: [ 'external-yaml-file', 'env' ]
  - name: secrets-manager
    type: secrets_manager
    options:
      prefix: 'env/{{ ENVIRONMENT_NAME }}/secret/'
    sources: [ '1password' ]
```

### Requirements

- Python 3.10+
- AWS credentials/auth per your environment (respects `AWS_PROFILE`, `AWS_DEFAULT_REGION`/`AWS_REGION`).
- 1Password source requires the `op` CLI with a service account token (via `OP_SERVICE_ACCOUNT_TOKEN` or `options.service_account_token`).
- Keeper source requires the Keeper Commander CLI config (`~/.keeper/config.json`) and the `keepercommander` Python package (installed with this tool). The Keeper CLI credentials can be overridden with `options.keeper_*` or `KEEPER_*` environment variables.

### Notes

- Lists of dicts with `name` fields are deep-merged by name across config files (later files override earlier entries). Other lists are replaced.
- YAML source `files` are resolved relative to the config file they are declared in.
- YAML source values can call `{{ lookup('file', 'relative/path') }}` to inline file contents. Lookup templates receive the merged config `vars` plus environment variables, and relative paths are evaluated from the YAML file that declares the secret. You can chain Ansible-style filters such as `| from_json | to_json` to parse and re-emit structured data.
