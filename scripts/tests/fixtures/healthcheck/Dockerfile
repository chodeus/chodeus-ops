# smoke-reusables fixture: the smallest image whose HEALTHCHECK turns healthy.
# Unpinned on purpose (Renovate ignores tests/); the upgrade keeps the Trivy gate green.
FROM alpine:3.24
RUN apk upgrade --no-cache
HEALTHCHECK --interval=3s --timeout=2s --start-period=1s --retries=2 CMD ["/bin/true"]
CMD ["sleep", "300"]
