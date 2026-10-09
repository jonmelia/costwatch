# Security

## What costwatch accesses

- It only uses read-only AWS APIs (`Describe*`, `List*`, `Get*`, `Lookup*`); `costwatch policy`
  prints the full list. It never creates, changes or deletes resources.
- It runs locally with your credentials and sends nothing anywhere except AWS API calls.
- With `--tfstate`, it reads Terraform state, which can contain secrets. Only identifier
  attributes (IDs and ARNs) are read, in memory; nothing else from the state is stored or
  printed.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's private vulnerability reporting
(**Security → Report a vulnerability** on the repository). You'll get a reply within a few days.
