The `dir_files` sink writes selected secret values to individual files in a target directory. It is intended for file-shaped secrets such as JWKS documents, PEM bundles, JSON blobs, or config fragments whose key names should be preserved as file names.

Example:

```yaml
sinks:
  - name: file-secrets
    type: dir_files
    options:
      path: "./run/secrets"
      include_regex: "\\.[A-Za-z0-9]+$"
    sources: ["my-passwords"]
```

Each selected secret becomes one file:

- secret name: `my-keystore.jwks`
- output path: `./run/secrets/my-keystore.jwks`
- file contents: the secret value exactly as stored

Options:

- `path`: Required target directory. Relative paths are resolved relative to the config file where the sink is declared.
- `include_regex`: Optional regex or list of regexes applied to secret names before writing. Non-matching items are skipped.
- `exclude_regex`: Optional regex or list of regexes to skip after inclusion.
- `strip_prefix`: Optional prefix transform removed from the emitted file name. Accepts a string or list.
- `strip_suffix`: Optional suffix transform removed from the emitted file name. Accepts a string or list.

Safety rules:

- File names must resolve to a single relative file name.
- Absolute paths, path separators, `.` and `..` are rejected.
- If two source items collapse to the same output file name after prefix stripping, the sink fails.

This sink writes or replaces only the files selected for the current run. It does not delete unrelated files already present in the target directory.
