# Infisical Source Guide

The `infisical` source reads secrets from a single Infisical project, environment, and folder path. It supports both token auth and universal auth, with credentials supplied only through environment variables.

## Option reference

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
      rate_limit_rps: 5
      concurrency: 5
      recursive: false
      include_imports: false
      expand_secret_references: true
      include_regex: "^APP_.*"
      tag_filters: ["default", "dev"]
      strip_prefix: "APP_"
```

- `host`: Optional Infisical base URL. Defaults to `INFISICAL_HOST` or `https://app.infisical.com`.
- `project_id` or `project_slug`: Source Infisical project. `project_id` takes precedence if both are set.
- `environment_slug`: Required environment slug such as `dev`, `staging`, or `prod`.
- `secret_path`: Folder path inside the selected project environment. Use `/` for the root folder, `/config` for a folder named `config`, or nested paths like `/service-a/config`.
- `auth_method`: Optional `token` or `universal_auth`. If omitted, token auth is selected when `INFISICAL_TOKEN` is present; otherwise universal auth is used.
- `rate_limit_rps`, `concurrency`: Control request pacing and parallelism.
- `recursive`: When `true`, includes nested folders under `secret_path`. Defaults to `false`.
- `include_imports`: When `true`, includes imported secrets in the response. Defaults to `false`.
- `expand_secret_references`: When `true`, resolves Infisical secret references before returning values. Defaults to `true`.
- `include_regex`: Optional regex applied to secret names after Infisical returns them.
- `tag_filters`: Optional list of Infisical tags used by the API to filter which secrets are returned.
- `strip_prefix`: Optional prefix removed from each secret name before the source emits it.

The source emits one `SecretItem` per returned Infisical secret. Secret comments become `description` values when present.

## Authentication

Authentication is environment-only:

- Token Auth: `INFISICAL_TOKEN`
- Universal Auth: `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`

Example:

```bash
export INFISICAL_HOST="https://infisical.example.internal"
export INFISICAL_TOKEN="..."
```

Or:

```bash
export INFISICAL_HOST="https://infisical.example.internal"
export INFISICAL_CLIENT_ID="..."
export INFISICAL_CLIENT_SECRET="..."
```

Do not place Infisical credentials directly in the YAML config.

## Finding the project ID or slug

To verify token-based access and inspect the projects visible to that token:

```bash
curl -sS \
  -H "Authorization: Bearer $INFISICAL_TOKEN" \
  "https://your-infisical-host/api/v1/projects"
```

This is useful for two reasons:

- It confirms that `INFISICAL_TOKEN` is accepted by the API.
- It shows each project's `id`, `name`, and `slug`.

Example response excerpt:

```json
{
  "projects": [
    {
      "id": "d9256764-cc78-45a9-af56-79f08a936f33",
      "name": "myproject",
      "slug": "myproject-i-n-bo",
      "environments": [{ "name": "myenv-1", "slug": "myenv-1" }]
    }
  ]
}
```

The display name is not always the same as the slug, so prefer `project_id` when possible.

## Understanding `secret_path`

`secret_path` is the folder path inside an Infisical project environment. It is not returned by the `/api/v1/projects` response above.

Examples:

- `secret_path: "/"`: read from the root folder of the environment
- `secret_path: "/config"`: read from a folder named `config`
- `secret_path: "/service-a/config"`: read from nested folders

In the UI, open the project, switch to the target environment, and look at the current folder in the Secrets view.

If you are unsure, start with:

```yaml
secret_path: "/"
```

and narrow the path once you know exactly which folder tree should be read.

## Recursive reads and duplicate names

The source flattens Infisical secrets into this tool's global name/value model. That means every emitted secret name must be unique after local filters are applied.

Be careful when using:

- `recursive: true`
- `include_imports: true`
- `strip_prefix`

These can cause different Infisical secrets to collapse to the same final emitted name. For example:

- `/service-a/config/DB_URL`
- `/service-b/config/DB_URL`

or:

- `APP_DB_URL`
- `DB_URL` with `strip_prefix: "APP_"`

If that happens, the source fails instead of silently letting one secret overwrite another. Narrow the `secret_path`, disable `recursive`, or adjust local name filters so each emitted secret name stays unique.

## Self-hosted ingress and SSO

For self-hosted Infisical behind an ALB or reverse proxy, API requests must reach Infisical directly. If `/api/*` is intercepted by Google OIDC or another interactive login flow, machine auth will fail.

The simplest rule is:

- bypass interactive auth for `/api/*`
- keep interactive auth on browser UI routes

At minimum, this source needs these API routes reachable without browser login:

- `GET /api/v3/secrets/raw`

If you use universal auth, also allow:

- `POST /api/v1/auth/universal-auth/login`

## Example config

Token auth:

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
      include_regex: "^APP_.*"
      strip_prefix: "APP_"
```

Universal auth with tag filtering:

```yaml
sources:
  - name: infisical-secrets
    type: infisical
    options:
      host: "https://infisical.example.internal"
      project_slug: "streaming-tech"
      environment_slug: "prod"
      secret_path: "/secrets"
      auth_method: "universal_auth"
      tag_filters: ["shared", "prod"]
      expand_secret_references: true
```

## Troubleshooting

- `project slug was not found`:
  - The display name and slug are different. Query `/api/v1/projects` and use the returned `slug`, or prefer `project_id`.
- `Folder with path '/config' ... was not found`:
  - The path does not exist in the selected environment. Check the configured `secret_path` and confirm the folder exists in the UI.
- `redirected ... to accounts.google.com`:
  - Your API hostname is behind interactive SSO. Exempt `/api/*` from that auth layer or use a direct backend hostname.
- `401` or `403`:
  - Check the token or machine identity credentials and confirm the identity has read access to the target project and path.
- `duplicate secret names after applying local filters`:
  - Narrow the path, disable recursive/imported reads, or adjust `strip_prefix` and `include_regex` so each emitted secret name is unique.
