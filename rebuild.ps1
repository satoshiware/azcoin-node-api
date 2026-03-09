git status
git add -A
git commit -m "Creating latest Release v0.1.4"
git tag v0.1.4 -m "v0.1.4"
git push origin main
git push origin v0.1.4

$SHA = (git rev-parse --short HEAD).Trim()

# Build once, tag many (v0.1.4 + stable + sha; optionally latest)
$SHA = (git rev-parse --short HEAD).Trim()

docker build `
  -t ghcr.io/satoshiware/azcoin-node-api:v0.1.4 `
  -t ghcr.io/satoshiware/azcoin-node-api:sha-$SHA `
  -t ghcr.io/satoshiware/azcoin-node-api:latest `
  .

docker push ghcr.io/satoshiware/azcoin-node-api:v0.1.4
docker push ghcr.io/satoshiware/azcoin-node-api:sha-$SHA
docker push ghcr.io/satoshiware/azcoin-node-api:latest