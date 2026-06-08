# Runbook: S3MediaErrors (object storage failures)

**Alert:** `ExternalDependencyErrors` with log patterns `S3|AmazonS3|NoSuchBucket|AccessDenied`.

## Meaning
The `media` module cannot upload or serve objects. Uploads fail; existing URLs may 403/404.

## First checks
1. Logs: `{service="platform-service"} | json | logger_name=~".*media.*" | level="ERROR"`.
2. Verify `AWS_ACCESS_KEY_ID` / bucket policy (PROD) or MinIO health (DEV).
3. Recent deploy changing bucket name or region.

## Common causes
- Expired or rotated IAM credentials.
- Bucket policy missing public-read for media URLs (PROD).
- Throttling or regional outage.

## Blast radius
`media` module; tenant asset uploads and CDN-backed URLs.

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
