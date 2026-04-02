git status
git add -A
git commit -m "fixing issues with 1.6 v0.1.6-r2"
git tag v0.1.6-r2
git push origin main
git push origin v0.1.6-r2

$SHA = (git rev-parse --short HEAD).Trim()

# Build once, tag many (v0.1.4 + stable + sha; optionally latest)
$SHA = (git rev-parse --short HEAD).Trim()

docker build `
  -t ghcr.io/satoshiware/azcoin-node-api:sha-$SHA `
  -t ghcr.io/satoshiware/azcoin-node-api:latest `
  .

docker push ghcr.io/satoshiware/azcoin-node-api:sha-$SHA
docker push ghcr.io/satoshiware/azcoin-node-api:latest