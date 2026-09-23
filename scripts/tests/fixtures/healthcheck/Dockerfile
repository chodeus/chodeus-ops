# Fixture for smoke-reusables: the smallest image whose HEALTHCHECK turns healthy.
# Unpinned on purpose (Renovate ignores tests/), so the upgrade keeps it patched
# and the smoke's Trivy gate is never red over the fixture itself.
FROM alpine:3.24
RUN apk upgrade --no-cache
HEALTHCHECK --interval=3s --timeout=2s --start-period=1s --retries=2 CMD ["/bin/false"]
CMD ["sleep", "300"]
