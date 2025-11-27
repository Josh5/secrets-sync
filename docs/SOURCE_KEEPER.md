# Keeper Integration Guide

This guide walks through preparing the Keeper Commander CLI (`keeper`) for unattended use with this tool, limiting access to only the folders you need, and structuring records so the `keeper` source can filter with `tag_filters`.

## Prerequisites

- Keeper Business/Enterprise account with access to the Admin Console
- Permission to create a dedicated automation user (recommended read-only access to secrets and scoped only to the folders you want to sync)

## Create a restricted automation user

1. In the Keeper Admin Console, open the **Admin** tab and click **Add User** to create a user that will only be used by this secrets-sync tool.
2. Still under **Admin**, select the **Roles** tab, click **Add Role**, and create a dedicated role for the automation user.
3. Select the new secrets-sync role, click **Enforcement Policies**, and configure the settings exactly as follows:
   - **Two-Factor Authentication**
     - Require the use of Two-Factor Authentication: **Off**
   - **Creating and Sharing**
     - Creating:
       - Can create records: **Off**
       - Can create folders: **Off**
       - Can create shared folders: **Off**
       - Can create items in identity & payments tab: **Off**
       - Can upload files: **Off**
       - Can create two-factor codes: **Off**
     - Sharing:
       - Can only receive shared items: **Selected**
   - **Import and Export**
     - Can import into vault: **Off**
     - Can export from vault: **Off**
4. Return to the **Users** tab, click the edit icon for the new automation user, and under **Roles** ensure that only the newly created secrets-sync role is applied.
5. Share only the folders that contain the secrets you expect to sync and grant **read-only** access. Note the exact folder name/path; you will reference it via the source `options.folder` value.
6. Record the email + password somewhere secure (password manager or your main Keeper account with MFA).

## Provide Keeper credentials

### Recommended: environment variables or config options

For most automation scenarios, set the Keeper email/username and password via the environment (or the `keeper_user` / `keeper_password` source options).

1. Store the automation user's email and password in your secret manager of choice.
2. Inject them into the job environment as `KEEPER_USER` and `KEEPER_PASSWORD` (optionally `KEEPER_SERVER` if you use a non-default Keeper domain). Example:

   ```
   export KEEPER_USER="$KEEPER_AUTOMATION_EMAIL"
   export KEEPER_PASSWORD="$KEEPER_AUTOMATION_PASSWORD"
   # optional
   export KEEPER_SERVER="keepersecurity.com"
   ```

3. Run `secrets-sync ...` and the Keeper source will authenticate directly with those values. No local config file is required in this mode.

When you prefer config files instead of environment variables, specify the same values under `options.keeper_user` / `options.keeper_password`. If you store credentials in a config file, treat it as sensitive secret material—do not commit it to version control and ensure it is handled like any other secret.

### Alternative: persistent CLI login (config file)

If you still need Keeper's 30-day persistent login (for example, when sharing the CLI session across tools), you can maintain `~/.keeper/config.json`. Run these commands once on a workstation (or ephemeral CI task) to initialize that file:

```
keeper shell
Not logged in> login
# enter the automation user's email + password when prompted
My Vault> this-device register
My Vault> this-device persistent-login on
My Vault> this-device ip-auto-approve on
My Vault> this-device timeout 30d
My Vault> quit
```

After this initialization you should be able to run:

```
keeper list
```

and immediately see record titles without being prompted for credentials. Copy `~/.keeper/config.json`, base64-encode it, and store the encoded blob in your CI/CD secrets manager. In pipelines, decode it back to `~/.keeper/config.json` before running `secrets-sync`. Example:

```
echo "$KEEPER_CONFIG_B64" | base64 -d > ~/.keeper/config.json
```

## Supported record layout and tagging

Keeper does not support native tags, so this source uses a custom field to emulate them:

1. Open a record inside the shared folder and add a **Custom Field**.
2. Set the label to `tags` (all lowercase).
3. Enter a comma-separated list of tags as the value, e.g., `default,prod`.

When `tag_filters` is provided in the secrets-sync config, a record qualifies if **any** of the tags in that custom field match (logical OR). Leave the field empty to include the record in every sync, or set explicit tags to target specific environments.

Record types `Login`, `General`, and `Secure Note` are supported.

> [!NOTE]
> The source prefers the record-level password, then any password/login/note fields, then any other custom fields (excluding the `tags` helper), and finally the record notes body.

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
> If two items share the same title and the same highest-priority tag (e.g., two `APP_KEY` entries both tagged `test-3`), the last one wins and the CLI logs a warning so you can fix the duplicate in Keeper.

## Configuring the Keeper source

```
sources:
  - name: keeper
    type: keeper
    options:
      folder: 'EnvironmentSecrets'              # required shared folder/path
      include_regex: '^APP_.*'                  # optional filter on record title
      tag_filters: ['default','prod']           # matches custom field "tags"
      config_file: '/custom/path/config.json'   # optional; only needed when using Keeper's config file
      keeper_user: '{{ vars.KEEPER_USER }}'     # recommended (or set KEEPER_USER env var)
      keeper_password: '{{ vars.KEEPER_PASSWORD }}' # recommended (or set KEEPER_PASSWORD env var)
      keeper_server: 'keepersecurity.com'       # optional override or set KEEPER_SERVER env var
```

Route the source to your sinks just like any other source. If you rely on Keeper's persistent login file, make sure the automation environment writes `~/.keeper/config.json`; otherwise `keeper list` / `keeper get` will prompt for credentials and the run will hang. When `keeper_user` and `keeper_password` are provided (via options or env vars), the source skips the config file entirely and relies on those credentials instead.

When `keeper_user`, `keeper_password`, or `keeper_server` are omitted in the config, the source automatically falls back to the environment variables `KEEPER_USER`, `KEEPER_PASSWORD`, and `KEEPER_SERVER`. These overrides are applied after loading `~/.keeper/config.json`, allowing you to inject secrets via your CI/CD variables instead of storing them directly in the Keeper CLI config (or to eliminate the config file when explicit credentials are provided).

## Sanity checks

- `keeper list --format json | jq '.[].title'` — verify records and titles are visible.
- `keeper get <record-uid> --format json | jq '.custom_fields'` — confirm the `tags` custom field value looks correct.
- Run `secrets-sync --dry-run --print-values ...` to preview the merged output before pushing to AWS.

If login fails, rerun `keeper shell` locally, `login`, and `this-device persistent-login ON` to refresh the session, then re-upload the updated config file wherever it is needed.
