# AWS SSM Sink Guide

The `ssm` sink writes secrets to AWS Systems Manager Parameter Store. It is a good fit for application config and secrets that should be addressed by hierarchical parameter names such as `/env/dev/config/DB_URL`.

## Option reference

```yaml
sinks:
  - name: ssm-config
    type: ssm
    options:
      prefix: "/env/{{ ENVIRONMENT_NAME }}/config/"
      type: "String"
      tier: "Standard"
      overwrite: true
      kms_key_id: "alias/aws/ssm"
      rate_limit_rps: 10
      concurrency: 10
    sources: ["yaml-values"]
```

- `prefix`: Optional string prefix prepended to each parameter name. If set, the final parameter name becomes `prefix + item.name`, with exactly one `/` between them.
- `type`: Optional `SecureString` (default) or `String`.
- `tier`: Optional `Standard` (default) or `Advanced`.
- `overwrite`: Optional boolean, default `true`.
- `kms_key_id`: Optional KMS key ID or alias used when `type` is `SecureString`.
- `rate_limit_rps`, `concurrency`: Control request pacing and parallelism.

## Name mapping

Each routed secret item becomes one SSM parameter:

- secret name: `DB_URL`
- `prefix: "/env/dev/config/"`
- final parameter name: `/env/dev/config/DB_URL`

If `prefix` is omitted, the source item name is used directly as the SSM parameter name.

## Value types

- `SecureString`: Recommended for secrets such as passwords, API tokens, and private keys.
- `String`: Appropriate for non-sensitive config values that still benefit from central distribution.

If `type` is anything else, config loading fails immediately.

## Tier behavior and size limits

Parameter Store has size limits, and this sink enforces them:

- Standard tier supports values up to 4 KB.
- Advanced tier supports values up to 8 KB.

When a value is larger than 4 KB, the sink automatically promotes that parameter write to `Advanced` tier, even if the configured default tier is `Standard`, and logs a warning. Values larger than 8 KB fail with an error.

One important AWS nuance is already handled: if a parameter already exists as `Advanced` and the new value is small enough for `Standard`, the sink retries with `Advanced` instead of failing with the AWS downgrade validation error.

## Authentication and region

This sink uses boto3 and the standard AWS credential/provider chain.

You can provide AWS context either through config:

```yaml
aws:
  region: ap-southeast-2
  profile: my-profile
```

or through the environment:

```bash
export AWS_REGION="ap-southeast-2"
export AWS_PROFILE="my-profile"
```

Exported credentials such as `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` are also supported through boto3's normal behavior.

## Sync behavior

For each item, the sink writes the value with `PutParameter`.

- Missing parameters are created.
- Existing parameters are overwritten when `overwrite: true`.
- With `--print-sync-details`, the sink classifies each item as `created`, `changed`, or `unchanged` by reading the current value first.

Request pacing is automatically rate-limited, and AWS throttling responses are retried with exponential backoff.

## Example configs

Push secrets from 1Password as encrypted parameters:

```yaml
sources:
  - name: app-secrets
    type: 1password
    options:
      vault: "EnvironmentSecrets"
      tag_filters: ["default", "prod"]

sinks:
  - name: ssm-secrets
    type: ssm
    options:
      prefix: "/env/prod/secret/"
      type: "SecureString"
      tier: "Standard"
    sources: ["app-secrets"]
```

Push plain config values from YAML:

```yaml
sources:
  - name: app-config
    type: yaml
    options:
      files:
        - "./config/default.yaml"
        - "./config/prod.yaml"
      key: values

sinks:
  - name: ssm-config
    type: ssm
    options:
      prefix: "/env/prod/config/"
      type: "String"
    sources: ["app-config"]
```

## Troubleshooting

- `SSM 'type' must be 'SecureString' or 'String'`:
  - The sink only supports the two SSM parameter types implemented by the tool.
- `SSM 'tier' must be 'Standard' or 'Advanced'`:
  - Use one of those exact values.
- `... exceeds the SSM Advanced tier limit`:
  - The value is larger than 8 KB and cannot be stored in Parameter Store. Route it to a file sink or another backend instead.
- `AccessDeniedException`:
  - Check IAM permissions for `ssm:GetParameter` and `ssm:PutParameter`, plus `kms:Encrypt` / `kms:Decrypt` when using a customer-managed KMS key.
