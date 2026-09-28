# Development Langflow connectivity

Langflow listens on host 127.0.0.1:7860. API containers use
`http://host.docker.internal:7861` from the existing `.env`.
`scripts/langflow_dev_bridge.py` forwards Docker gateway 172.17.0.1:7861
to host loopback 7860 without HTTP request logging.

Started and verified from the running API container on 2026-09-28:
GET `/api/v1/version` returned HTTP 200 and version 1.11.6.

The user-level systemd unit is transient (not a persistent production deployment):

```sh
systemd-run --user --unit=temanbule-langflow-bridge --property=Restart=on-failure \
  /home/dani/Projects/teman-bule/.venv/bin/python \
  /home/dani/Projects/teman-bule/scripts/langflow_dev_bridge.py
systemctl --user status temanbule-langflow-bridge
```

Stop: `systemctl --user stop temanbule-langflow-bridge`.
Recreate after reboot if the transient unit is absent. The gateway address is
specific to this development machine; verify it before using on another host.

This bridge only fixes backend-to-Langflow connectivity. CallCraft is used as a
REST request/JSON response service. It does not need inbound connectivity to
Teman Bule; the trusted application caller dispatches domain operations locally.
The previously asserted CallCraft callback/tunnel requirement is superseded.
