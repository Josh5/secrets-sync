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

### Lookup helper

Values can embed simple Jinja expressions that call `lookup('file', path)`—similar to Ansible—to inline the contents of another file. Paths are resolved relative to the YAML file currently being processed, so you can keep certificate snippets or other large blobs next to the value file:

```yaml
values:
  - name: TLS_CERT_PEM
    value: "{{ lookup('file', './files/{}-cert.pem'.format(ENVIRONMENT_NAME)) }}"
    description: "Populated from vars/files/dev-cert.pem via lookup()."
```

Filters `from_json` and `to_json` are also available so you can parse JSON blobs from disk, manipulate them, and re-emit normalized JSON:

```yaml
values:
  - name: TLS_CERT_METADATA
    value: "{{ lookup('file', './files/{}-metadata.json'.format(ENVIRONMENT_NAME)) | from_json | to_json }}"
```

Lookup templates receive the merged config `vars` and all environment variables, so the example above works without exporting `ENVIRONMENT_NAME` separately. Unsupported lookup plugins raise an error, and missing files stop the sync early so issues are caught before pushing to sinks.

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
