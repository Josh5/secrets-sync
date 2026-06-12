# AWS Secrets Manager Sink Guide

The `secrets_manager` sink writes secrets to AWS Secrets Manager. It is a good fit for secret values that should live as individually named secret objects rather than Parameter Store parameters.

## Option reference

```yaml
sinks:
  - name: app-secrets
    type: secrets_manager
    options:
      prefix: "env/{{ ENVIRONMENT_NAME }}/secret/"
      kms_key_id: "alias/aws/secretsmanager"
      rate_limit_rps: 5
      concurrency: 5
    sources: ["1password"]
```

- `prefix`: Optional string prefix prepended to each secret name.
- `kms_key_id`: Optional KMS key ID or alias used when creating new secrets.
- `rate_limit_rps`, `concurrency`: Control request pacing and parallelism.

## Name mapping

Each routed secret item becomes one Secrets Manager secret:

- secret name: `DB_PASSWORD`
- `prefix: "env/prod/secret/"`
- final secret name: `env/prod/secret/DB_PASSWORD`

If `prefix` is omitted, the source item name is used directly as the secret name.

## Sync behavior

For each item, the sink does two things:

1. Ensures the secret object exists, creating it if needed.
2. Writes the current value with `PutSecretValue`.

Descriptions from the source item are applied when the secret is first created.

With `--print-sync-details`, the sink reads the current secret value first so it can classify each item as:

- `created`
- `changed`
- `unchanged`

Like the other AWS sinks, request pacing is rate-limited automatically and throttling responses are retried with exponential backoff.

## KMS behavior

If `kms_key_id` is set, it is used only when creating a new secret. Updating an existing secret value does not recreate the secret or change its KMS key.

That means:

- new secrets use the configured KMS key
- existing secrets keep their existing KMS configuration

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

Exported credentials such as `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` also work through boto3's default behavior.

## Example configs

Push 1Password items into Secrets Manager:

```yaml
sources:
  - name: app-secrets
    type: 1password
    options:
      vault: "EnvironmentSecrets"
      tag_filters: ["default", "prod"]

sinks:
  - name: aws-secrets
    type: secrets_manager
    options:
      prefix: "env/prod/secret/"
    sources: ["app-secrets"]
```

Split config and secrets across different AWS sinks:

```yaml
sources:
  - name: yaml-config
    type: yaml
    options:
      files:
        - "./defaults.yaml"
      key: values

  - name: vault-secrets
    type: 1password
    options:
      vault: "EnvironmentSecrets"
      tag_filters: ["default", "prod"]

sinks:
  - name: ssm-config
    type: ssm
    options:
      prefix: "/env/prod/config/"
      type: "String"
    sources: ["yaml-config"]

  - name: aws-secrets
    type: secrets_manager
    options:
      prefix: "env/prod/secret/"
    sources: ["vault-secrets"]
```

## Troubleshooting

- `ResourceNotFoundException` during reads:
  - Expected for new secrets; the sink will create them automatically before writing.
- `AccessDeniedException`:
  - Check IAM permissions for `secretsmanager:DescribeSecret`, `secretsmanager:CreateSecret`, and `secretsmanager:PutSecretValue`, plus relevant KMS permissions when using a customer-managed key.
- Secret exists but uses the wrong KMS key:
  - This sink does not rotate existing secrets onto a new KMS key. Update the secret in AWS separately if that migration is required.
