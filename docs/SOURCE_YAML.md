# YAML Source Guide

The `yaml` source lets you load secrets from structured files. It is ideal for layered configuration (defaults + environment overrides) and for storing metadata such as descriptions alongside each secret.

## Option reference

```yaml
sources:
  - name: base-files
    type: yaml
    options:
      files:
        - configs/default.yaml
        - configs/dev.yaml
      key: values
```

- `files` (required): Ordered list of YAML file paths. Later files override earlier entries when the same secret name appears.
- `key`: Dot-path (e.g., `values`, `foo.bar.values`) pointing to the subtree to read. If omitted, the entire document is processed.

The `files` paths are resolved relative to the config file that declares the source, not the current working directory. This makes nested includes predictable no matter where you run the command from.

## Supported data shapes

Any of the following structures produce the same output:

1. Simple mapping
   ```yaml
   DB_HOST: db.internal
   DB_PASSWORD: dev-db-password
   ```
2. Object with `values` array
   ```yaml
   values:
     - name: DB_HOST
       value: db.internal
       description: Primary database endpoint
   ```
3. Raw list of objects
   ```yaml
   - name: DB_HOST
     value: db.internal
     description: Primary database endpoint
   ```

Descriptions are optional but recommended. They flow through to sinks that support metadata.

## Layering example

`configs/default.yaml`

```yaml
values:
  - name: API_URL
    value: https://api.example.com
  - name: FEATURE_FLAG
    value: "false"
```

`configs/dev.yaml`

```yaml
values:
  - name: FEATURE_FLAG
    value: "true"
  - name: DEV_ONLY_TOKEN
    value: dev-only-token
```

`secrets-sync.yaml`

```yaml
sources:
  - name: yaml-values
    type: yaml
    options:
      files:
        - configs/default.yaml
        - configs/dev.yaml
      key: values

sinks:
  - type: ssm
    options:
      prefix: "/env/dev/"
    sources: ["yaml-values"]
```

Running `secrets-sync --dry-run -f secrets-sync.yaml` would emit:

```
API_URL=https://api.example.com
FEATURE_FLAG=true
DEV_ONLY_TOKEN=dev-only-token
```

## Tips and troubleshooting

- **Missing overrides**: Order matters. Ensure the file containing overrides appears later in the `files` array.
- **Dot-path errors**: When `key` is provided, confirm the subtree exists; typos result in `KeyError` during config loading.
- **Relative paths**: When referencing files from nested configs, start relative paths from the file where the source is defined. For absolute stability, use absolute paths or `${PROJECT_ROOT}` env vars.

The YAML source offers the most control over structured data and is the best choice when you need versioned, reviewable secret definitions.
