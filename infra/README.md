# `infra` folder

This folder contains **infrastructure configurations** for the monorepo (for example: Dockerfiles, Terraform scripts, deployment manifests, Nginx configs, etc.).

- **Main purpose**: to centralize the definition of the infrastructure required to run the company's applications and services.
- **Recommendation**: document how to provision and deploy the infrastructure.

> _Spanish version: [README.es.md](./README.es.md)._

## Seguridad — endurecimiento del servidor

- [hardening.md](./hardening.md) — SSH sin root, usuario no-root con permisos explícitos y firewall deny-by-default (requisitos 1-3 de la auditoría de seguridad).
