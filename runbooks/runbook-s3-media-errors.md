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

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:13:15.200Z","level":"ERROR","logger_name":"com.publishi.platform.media.S3MediaStorage","service":"platform-service","trace_id":"3456789abcdef01234567890123456789","message":"S3 putObject failed: AmazonS3Exception: The specified bucket does not exist (Service: Amazon S3; Status Code: 404; Error Code: NoSuchBucket)"}
{"@timestamp":"2026-07-20T20:13:18.640Z","level":"ERROR","logger_name":"com.publishi.platform.media.S3MediaStorage","service":"platform-service","trace_id":"456789abcdef012345678901234567891","message":"S3 getObject failed: AmazonS3Exception: Access Denied (Status Code: 403; Error Code: AccessDenied) — check IAM credentials"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
