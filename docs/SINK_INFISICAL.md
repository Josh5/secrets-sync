# Infisical Sink Guide

The `infisical` sink writes secrets into an Infisical project, environment, and folder path. It supports both token auth and universal auth, with credentials supplied only through environment variables.

## Option reference

```yaml
sinks:
  - name: infisical-config
    type: infisical
    options:
      host: "https://infisical.example.internal"
      project_id: "d9256764-cc78-45a9-af56-79f08a936f33"
      environment_slug: "dev"
      secret_path: "/config"
      auth_method: "token"
      name_prefix: "APP_"
      rate_limit_rps: 5
      concurrency: 5
    sources: ["yaml-values"]
```

- `host`: Optional Infisical base URL. Defaults to `INFISICAL_HOST` or `https://app.infisical.com`.
- `project_id` or `project_slug`: Target Infisical project. `project_id` takes precedence if both are set.
- `environment_slug`: Required environment slug such as `dev`, `staging`, or `prod`.
- `secret_path`: Folder path inside the selected project environment. Use `/` for the root folder, `/config` for a folder named `config`, or nested paths like `/service-a/config`. Missing folders are created automatically before the first sync.
- `name_prefix`: Optional string prepended to each secret key before writing.
- `auth_method`: Optional `token` or `universal_auth`. If omitted, token auth is selected when `INFISICAL_TOKEN` is present; otherwise universal auth is used.
- `rate_limit_rps`, `concurrency`: Control request pacing and parallelism.

Secrets are synchronized by secret name within a single project, environment, and path. Existing values are updated when changed, new values are created, and unchanged values are skipped.

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

- `secret_path: "/"`: write to the root folder of the environment
- `secret_path: "/config"`: write to a folder named `config`
- `secret_path: "/service-a/config"`: write to nested folders

In the UI, open the project, switch to the target environment, and look at the current folder in the Secrets view.

If you are unsure, start with:

```yaml
secret_path: "/"
```

and move to a more specific folder path once the structure exists.

## Self-hosted ingress and SSO

For self-hosted Infisical behind an ALB or reverse proxy, API requests must reach Infisical directly. If `/api/*` is intercepted by Google OIDC or another interactive login flow, machine auth will fail.

The simplest rule is:

- bypass interactive auth for `/api/*`
- keep interactive auth on browser UI routes

At minimum, this sink needs these API routes reachable without browser login:

- `GET /api/v3/secrets/raw`
- `POST /api/v3/secrets/raw/{secretName}`
- `PATCH /api/v3/secrets/raw/{secretName}`

If you use universal auth, also allow:

- `POST /api/v1/auth/universal-auth/login`

## Example config

Token auth:

```yaml
sinks:
  - name: infisical-config
    type: infisical
    options:
      host: "https://infisical.example.internal"
      project_id: "d9256764-cc78-45a9-af56-79f08a936f33"
      environment_slug: "dev"
      secret_path: "/config"
      auth_method: "token"
    sources: ["external-yaml-file", "env"]
```

Universal auth:

```yaml
sinks:
  - name: infisical-secrets
    type: infisical
    options:
      host: "https://infisical.example.internal"
      project_slug: "streaming-tech"
      environment_slug: "prod"
      secret_path: "/secrets"
      auth_method: "universal_auth"
    sources: ["1password"]
```

## Troubleshooting

- `project slug was not found`:
  - The display name and slug are different. Query `/api/v1/projects` and use the returned `slug`, or prefer `project_id`.
- `Folder with path '/config' ... was not found`:
  - The sink now attempts to create missing folders automatically. If this still appears, check that the token or machine identity can manage folders in the target project.
- `redirected ... to accounts.google.com`:
  - Your API hostname is behind interactive SSO. Exempt `/api/*` from that auth layer or use a direct backend hostname.
- `401` or `403`:
  - Check the token or machine identity credentials and confirm the identity has access to the target project and path.
