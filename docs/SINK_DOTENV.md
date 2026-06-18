# Dotenv Sink Guide

The `dotenv` sink writes the selected secrets to a local dotenv file so they can be consumed by tools such as Docker Compose, Node.js dotenv loaders, or Python processes that read `.env` files.

## Option reference

```yaml
sinks:
  - name: app-dotenv
    type: dotenv
    options:
      path: "./generated/.env"
      mode: "merge"
      file_mode: "0600"
      key_case: "upper"
      value_format: "parsed"
      strip_prefix: "APP_"
    sources: ["infisical-config"]
```

- `path`: Required output file path. Relative paths are resolved against the config file where they are declared.
- `mode`: Optional `merge` (default) or `replace`.
- `file_mode`: Optional octal file mode applied after each write, such as `"0600"` or `"0640"`.
- `key_case`: Optional `preserve` (default), `upper`, or `lower`.
- `value_format`: Optional `parsed` (default) or `raw`.
- `strip_prefix`: Optional prefix transform removed from the start of each secret name before writing. Accepts a string or list.
- `strip_suffix`: Optional suffix transform removed from the end of each secret name before writing. Accepts a string or list.

In `merge` mode, the sink updates existing matching keys in place, preserves unrelated existing lines, comments, and formatting where practical, and appends missing keys at the end of the file.

In `replace` mode, the sink rewrites the target file from only the items routed to that sink. Keys are sorted before writing so output is deterministic.

Sink-side filtering:

- `include_regex`: Optional regex or list of regexes applied to secret names before dotenv key transforms run.
- `exclude_regex`: Optional regex or list of regexes applied after inclusion.

This is useful when one source contains a mix of environment-style keys and file-shaped keys. For example, you can exclude names ending in `.pem`, `.json`, or `.jwks` from the dotenv sink and route them to `dir_files` instead.

## Name transforms

Transforms are applied in this order:

1. Strip configured prefixes from the start of the secret name.
2. Apply `key_case`.

Example:

- source secret: `APP_DATABASE_URL`
- `strip_prefix: "APP_"`
- `key_case: "lower"`
- emitted dotenv key: `database_url`

If two secrets collapse to the same final key after these transforms, the sync fails with a clear error instead of silently overwriting one of them.

## Dotenv key rules

This sink is intended for environment-style consumers, so emitted keys must match:

```text
[A-Za-z_][A-Za-z0-9_]*
```

If a source secret name contains characters outside that pattern after transforms, the sink fails and points at the offending key.

## Value formatting

In `parsed` mode, the sink writes simple values without quotes when safe, and uses double-quoted escaping when needed for spaces, `#`, quotes, or multiline content.

Examples:

- `API_URL=https://example.com`
- `GREETING="hello world"`
- `PRIVATE_KEY="line1\nline2"`

In `raw` mode, the sink writes values exactly as provided after the `=` with no added escaping.

Examples:

- `API_URL=https://example.com`
- `API_KEY_AND_SECRET_PAIRS={"key": "bcrypt_sha256$$2b$12$..."}`
- `PRIVATE_KEY=-----BEGIN KEY-----`

`raw` is intended for readers that support a literal env-file mode, especially Docker Compose:

```yaml
services:
  app:
    env_file:
      - path: ./.config.env
        format: raw
      - path: ./.secrets.env
        format: raw
```

Use `raw` only when the downstream consumer expects literal values. If a reader expects standard dotenv parsing semantics, keep the default `parsed` mode.

## Example configs

Pull from Infisical and publish an application-friendly `.env` file:

```yaml
sources:
  - name: infisical-config
    type: infisical
    options:
      host: "https://infisical.example.internal"
      project_id: "d9256764-cc78-45a9-af56-79f08a936f33"
      environment_slug: "dev"
      secret_path: "/config"
      auth_method: "token"

sinks:
  - name: app-dotenv
    type: dotenv
    options:
      path: "./generated/.env"
      mode: "merge"
      key_case: "upper"
      value_format: "parsed"
      strip_prefix: "APP_"
    sources: ["infisical-config"]
```

Preserve source key casing and strip multiple possible prefixes:

```yaml
sinks:
  - name: compose-env
    type: dotenv
    options:
      path: "./compose/.env"
      mode: "merge"
      value_format: "parsed"
      strip_prefixes: ["APP_", "DEV_"]
    sources: ["env", "yaml"]
```

Generate an authoritative file from only the selected sink inputs:

```yaml
sinks:
  - name: app-dotenv
    type: dotenv
    options:
      path: "./generated/.env"
      mode: "replace"
      key_case: "lower"
      value_format: "parsed"
    sources: ["infisical-config"]
```

Generate a file intended for Docker Compose `format: raw`:

```yaml
sinks:
  - name: compose-env
    type: dotenv
    options:
      path: "./compose/.config.env"
      mode: "replace"
      value_format: "raw"
    sources: ["infisical-config"]
```

## Troubleshooting

- `transformed secret name ... into an empty key`:
  - A prefix rule removed the entire source key. Adjust `strip_prefix` or the source selection.
- `produced duplicate keys after applying strip_prefix and key_case`:
  - Two source secrets collapse to the same final dotenv key. Narrow the source set or change the transforms.
- `produced a non-portable environment variable name`:
  - The emitted key contains characters that do not map cleanly to typical environment-variable consumers.
