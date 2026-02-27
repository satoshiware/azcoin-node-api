git status
git add -A
git commit -m "Creating Stable Release v0.1.3"
git tag v0.1.3 -m "v0.1.3"
git push origin main
git push origin v0.1.3

$SHA = (git rev-parse --short HEAD).Trim()

# Build once, tag many (v0.1.4 + stable + sha; optionally latest)
docker build `
docker build -t ghcr.io/satoshiware/azcoin-node-api:v0.1.3 `
docker build -t ghcr.io/satoshiware/azcoin-node-api:stable `
docker build -t ghcr.io/satoshiware/azcoin-node-api:sha-$SHA `
docker build -t ghcr.io/satoshiware/azcoin-node-api:latest `
  .

docker push ghcr.io/satoshiware/azcoin-node-api:v0.1.3
docker push ghcr.io/satoshiware/azcoin-node-api:stable
docker push ghcr.io/satoshiware/azcoin-node-api:sha-$SHA
docker push ghcr.io/satoshiware/azcoin-node-api:latest