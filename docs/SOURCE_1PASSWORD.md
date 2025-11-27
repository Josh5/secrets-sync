# 1Password Integration Guide

This guide explains how to set up the 1Password CLI (`op`), obtain a Service Account token (`OP_SERVICE_ACCOUNT_TOKEN`), and organize your secrets in 1Password for use with this tool.

## Prerequisites

- A 1Password account and permission to create a Service Account
- 1Password CLI v2 installed (`op`)
- Access to (or permission to create) a dedicated vault for app/environment secrets

## Install the 1Password CLI

Follow the official 1Password CLI installation instructions for your OS.

- macOS (Homebrew): `brew install 1password-cli`
- Linux: use the package for your distro from 1Password downloads
- Windows: use the installer from 1Password

Verify installation:

```
op --version
```

References: See the “1Password CLI” documentation on 1Password’s website for up-to-date install steps and supported platforms.

## Create a Service Account and get the token

Service Accounts allow non-interactive automation to access selected vaults.

1. In the 1Password web UI, navigate to Integrations/Developer settings and create a new “Service Account”.
2. Restrict its access to only the vault(s) you need (e.g., `EnvironmentSecrets`). Grant Read access.
3. Copy the generated token. This is your `OP_SERVICE_ACCOUNT_TOKEN`.

Security notes:

- Treat the token like any other high-privilege credential. Store it in your CI/CD secret manager.
- Limit vault access to the minimum required.
- Rotate the token periodically and on team changes.

Export the token for local use:

```
export OP_SERVICE_ACCOUNT_TOKEN="<paste token here>"
```

The tool reads this env var by default. You can also provide it under the 1Password source `options.service_account_token`, but environment variables are recommended for CI.

## Recommended vault and tagging layout

Use a dedicated vault for environment/application secrets, e.g., `EnvironmentSecrets`.

- Item Title = the secret key you want to appear in AWS, e.g., `APP_DB_PASSWORD`.
- Item Type = “Password” (recommended) so there is a single concealed value field.
- Use tags to minimize duplication and express overrides:
  - `default` tag for values that apply to all environments by default.
  - Additional tags only where needed for overrides, e.g., `test`, `staging`, `prod`.
  - Create duplicate items (clone) only when an environment needs a different value.

Example items in `EnvironmentSecrets`:

- `APP_DB_PASSWORD` (Password) with tag `default`
- `APP_DB_PASSWORD` (Password) with tag `prod` (only if prod differs)
- `APP_API_KEY` (Password) with tag `default`
- `APP_API_KEY` (Password) with tag `staging` (only if staging differs)

This keeps defaults explicit and reduces duplication.

### Tag filters and overrides

When you configure `tag_filters`, the source keeps items whose tags match **any** of the supplied values, but it also uses the order of that list to break ties between items that share the same title. Later entries have higher priority. For example, with:

```
tag_filters:
  - default
  - staging
  - test-3
```

`test-3` overrides `staging`, which overrides `default`.

> [!NOTE]
> If two items share the same title and the same highest-priority tag (e.g., two `APP_KEY` entries both tagged `test-3`), the last one wins and the CLI logs a warning so you can fix the duplicate in 1Password.

## Configure the 1Password source

In your config, add a source of type `1password`, specifying the vault and the tags you want to include. Typically you include `default` and the environment tag so that environment-specific items override the defaults by title.

```
sources:
  - name: 1password
    type: 1password
    options:
      vault: "EnvironmentSecrets"
      tag_filters: ["default", "prod"]
      include_regex: '^APP_.*'
      # Optional alternative to the environment variable. If you add this, keep this file secure!
      # service_account_token: "${OP_SERVICE_ACCOUNT_TOKEN}"
```

Then route to your sinks (SSM/Secrets Manager), optionally with prefixes:

```
sinks:
  - type: ssm
    options:
      prefix: '/env/{{ ENVIRONMENT_NAME }}/secret/'
    sources: [ '1password' ]
  - type: secrets_manager
    options:
      prefix: '{{ ENVIRONMENT_NAME }}/secret/'
    sources: [ '1password' ]
```

## Sanity-check with op CLI

- List items by tag in a vault:

```
op item list --vault EnvironmentSecrets --tags default --format json
```

- Fetch one item and inspect fields:

```
op item get "APP_DB_PASSWORD" --vault EnvironmentSecrets --format json
```

The tool prefers a `password` field, then any concealed field, then the first field with a value.

## Troubleshooting

- Empty results or permission errors:
  - Ensure the Service Account has read access to the vault.
  - Verify `OP_SERVICE_ACCOUNT_TOKEN` is set for the process running the tool (local shell or CI job).
- Item missing from sync:
  - Check `include_regex` matches the item title.
  - Confirm the environment tag is included in `tag_filters`.
- Duplicate titles:
  - Items are keyed by title. Keep one `default` item plus only the necessary overrides (e.g., `prod`, `staging`).

## Example dry-run

```
export OP_SERVICE_ACCOUNT_TOKEN="…"
secrets-sync \
  --dry-run \
  --print-values \
  --print-format=table \
  -f ./examples/basic/vars/default.yaml \
  -f ./examples/basic/vars/dev.yaml
```

This prints a preview of what would be pushed, grouped by sink, including any configured prefixes.
