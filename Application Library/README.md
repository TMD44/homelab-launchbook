# Application Library

The Application Library provides ready-to-use deployment files, configuration examples, and reference notes for the services featured throughout the Homelab Launchbook.

## Versions

> [!NOTE]
> This section is generated automatically by GitHub Actions.
> Current versions are read from the Docker Compose and environment files stored in this repository.
> A version is considered verified when a stable GitHub Release has an exact Docker Hub tag that normalizes to the same version using the configured service-specific patterns.
> Do not edit this section manually.

| Category | Service | Current version | GitHub release | Verified Docker Hub tag | Update status | Current version source |
| --- | --- | --- | --- | --- | --- | --- |
| Dashboards | Homepage | `latest` | [`v2.4.0`](https://github.com/gethomepage/homepage/releases/tag/v2.4.0) | [`v2.4.0`](https://hub.docker.com/r/gethomepage/homepage/tags?name=v2.4.0) | ⚠️ Unpinned or unverifiable | [`homepage-compose.yaml`](./Dashboards/Homepage/homepage-compose.yaml) |
| DNS Servers | AdGuard Home | `v0.107.78` | [`v0.107.79`](https://github.com/AdguardTeam/AdGuardHome/releases/tag/v0.107.79) | [`v0.107.79`](https://hub.docker.com/r/adguard/adguardhome/tags?name=v0.107.79) | ⬆️ Update available | [`adguardhome.env`](./DNS%20Servers/AdGuard%20Home/adguardhome.env) |
| Monitoring Tools | Beszel | `latest` | [`v0.20.0`](https://github.com/henrygd/beszel/releases/tag/v0.20.0) | [`0.20.0`](https://hub.docker.com/r/henrygd/beszel/tags?name=0.20.0) | ⚠️ Unpinned or unverifiable | [`beszel-compose.yaml`](./Monitoring%20Tools/Beszel/beszel-compose.yaml) |
| Monitoring Tools | MySpeed | `latest` | [`v1.0.9`](https://github.com/gnmyt/MySpeed/releases/tag/v1.0.9) | [`1.0.9`](https://hub.docker.com/r/germannewsmaker/myspeed/tags?name=1.0.9) | ⚠️ Unpinned or unverifiable | [`myspeed-compose.yaml`](./Monitoring%20Tools/MySpeed/myspeed-compose.yaml) |
| Monitoring Tools | Uptime Kuma | `latest` | [`2.5.5`](https://github.com/louislam/uptime-kuma/releases/tag/2.5.5) | [`2.5.5`](https://hub.docker.com/r/louislam/uptime-kuma/tags?name=2.5.5) | ⚠️ Unpinned or unverifiable | [`uptime-kuma-compose.yaml`](./Monitoring%20Tools/Uptime-kuma/uptime-kuma-compose.yaml) |
| Notifications & Alerting | Gotify | `2` | [`v3.1.1`](https://github.com/gotify/server/releases/tag/v3.1.1) | [`3.1.1`](https://hub.docker.com/r/gotify/server/tags?name=3.1.1) | ⚠️ Unpinned or unverifiable | [`gotify.env`](./Notifications%20%26%20Alerting/Gotify/gotify.env) |
| Reverse Proxies | Traefik | `v3.7.10` | [`v3.7.13`](https://github.com/traefik/traefik/releases/tag/v3.7.13) | [`v3.7.13`](https://hub.docker.com/_/traefik/tags?name=v3.7.13) | ⬆️ Update available | [`traefik.env`](./Reverse%20Proxies/Traefik/traefik.env) |

### Summary

- **Tracked services:** 7
- **Up to date:** 0
- **Updates available:** 2
- **Unpinned or unverifiable:** 5
- **Source mismatches:** 0
