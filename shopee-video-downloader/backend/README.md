---
title: Shopee Video Resolver
emoji: 🎬
colorFrom: orange
colorTo: red
sdk: docker
app_port: 7860
---

# Shopee Video Resolver

FastAPI backend for resolving publicly accessible Shopee video URLs.

## Endpoints

- `GET /health`
- `POST /api/resolve`

The service only accepts Shopee domains and short links. It does not act as an arbitrary URL proxy.
