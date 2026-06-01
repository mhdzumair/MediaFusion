# Choosing a Deployment Method

Not sure how to run MediaFusion? Use this guide to pick the right option.

## Decision guide

```
Do you want to manage a server yourself?
├── No  →  Use the community instance (free) or ElfHosted managed instance
└── Yes →  Do you know Docker?
            ├── No  →  Direct Binary (PostgreSQL + Redis required separately)
            └── Yes →  Do you need production-grade scalability?
                        ├── No  →  Docker Compose  ← most people start here
                        └── Yes →  Kubernetes
```

---

## Option comparison

| Method | Difficulty | Cost | Best for |
|---|---|---|---|
| [Community instance](quick-start.md) | None | Free | Just using the addon |
| [ElfHosted managed](../deployment/elfhosted.md) | Minimal | Paid | Private instance without sysadmin work |
| [Docker Compose](../deployment/docker-compose.md) | Easy | Free (your server) | Self-hosters, home servers, VPS |
| [Direct Binary](../deployment/binary.md) | Medium | Free (your server) | Minimal footprint, no Docker |
| [Kubernetes](../deployment/kubernetes.md) | Hard | Free (your cluster) | Production, auto-scaling |
| [Local Dev](../deployment/local-dev.md) | Medium | Free | Contributing to MediaFusion |

---

## What you need to self-host

All self-hosted methods require:

- **PostgreSQL** — the primary database (version 14+)
- **Redis** — cache and task queue (version 6+)
- A publicly accessible URL (for Stremio to reach your instance)

Docker Compose and Kubernetes handle PostgreSQL and Redis for you. The direct binary method requires you to bring your own.

!!! tip "Cheap VPS options"
    A $5/month VPS (Hetzner, DigitalOcean, etc.) with 2 GB RAM is enough to run a personal MediaFusion instance with Docker Compose.

## Minimum hardware requirements

| Workload | RAM | CPU | Storage |
|---|---|---|---|
| Personal / small | 1 GB | 1 vCPU | 10 GB |
| Shared / medium | 2–4 GB | 2 vCPU | 20 GB |
| Public community instance | 8+ GB | 4+ vCPU | 50+ GB |

---

## Next: pick a guide

- [ElfHosted](../deployment/elfhosted.md) — managed, one-click
- [Docker Compose](../deployment/docker-compose.md) — recommended for self-hosters
- [Direct Binary](../deployment/binary.md) — minimal, no containers
- [Kubernetes](../deployment/kubernetes.md) — production/scalable
